"""
autogen_engine.py — Moteur AutoGen : création des agents, GroupChat, intercepteur de messages.

Fournit AutogenEngineMixin à injecter dans DnDApp :
  - run_autogen : démarre la boucle AutoGen complète (agents + groupchat + game loop)

Cette méthode est intentionnellement volumineuse — elle contient toutes les closures
critiques (patched_receive, _execute_action_mechanics, etc.) qui partagent des variables
locales (COMBAT_STATE, _CHAR_MECHANICS, _pending_smite…) et ne peuvent pas être
facilement découpées sans refactoring profond de l'architecture de fermetures.

Prérequis sur l'instance hôte :
  self.msg_queue, self.audio_queue, self.groupchat, self.root,
  self._agents, self._base_system_msgs, self._autogen_thread_id,
  self._llm_running, self._waiting_for_mj,
  self._pending_interrupt_input, self._pending_interrupt_display,
  self._pending_combat_trigger,
  self.CHAR_COLORS, self.wait_for_input(), self._set_waiting_for_mj(),
  self._set_llm_running(), self._update_agent_combat_prompts(),
  self._update_contextual_memories(), self._on_pc_turn_ended(),
  self._refresh_char_stats()
"""

import threading
import types

from llm_config    import build_llm_config, _default_model, StopLLMRequested, _SSL_LOCK
from app_config    import (get_agent_config, get_chronicler_config,
                           get_groupchat_config, get_memories_config,
                           APP_CONFIG, save_app_config, reload_app_config)
from state_manager import (
    load_state, save_state, get_npcs,
    use_spell_slot, update_hp, add_temp_hp,
    get_scene_prompt, get_active_quests_prompt,
    get_memories_prompt_compact, get_calendar_prompt,
    get_session_logs_prompt, get_active_characters,
    get_spells_prompt,
    get_inventory_prompt,
    add_item_to_inventory, remove_item_from_inventory, update_currency,
)
from agent_logger  import log_tts_start
from combat_tracker import COMBAT_STATE, _is_fully_silenced
from combat_map_panel import get_map_prompt
from chat_log_writer import ChatLogWriter, strip_mechanical_blocks


class AutogenEngineMixin:
    """Mixin pour DnDApp — moteur AutoGen complet."""

    def run_autogen(self):
        import autogen  # lazy — gRPC démarre ici, bien après Tk.mainloop()
        # === NOUVEAU : Chargement et affichage du résumé au démarrage ===
        # Charge les voix PNJ dans le mapping TTS au démarrage
        try:
            from voice_interface import VOICE_MAPPING, SPEED_MAPPING
            for npc in get_npcs():
                key = f"__npc__{npc['name']}"
                VOICE_MAPPING[key] = npc.get("voice", "fr-FR-HenriNeural")
                SPEED_MAPPING[key] = npc.get("speed", "+0%")
        except Exception as e:
            print(f"[NPC] Erreur chargement voix PNJ : {e}")

        try:
            state = load_state()
            summary = state.get("session_summary", "Aucun résumé pour le moment.")
            
            # On ne l'affiche que s'il y a un vrai résumé
            if summary and summary != "Aucun résumé pour le moment.":
                self.msg_queue.put({
                    "sender": "Chroniqueur IA", 
                    "text": f"📜 Précédemment dans votre campagne :\n{summary}", 
                    "color": "#FF9800"
                })
        except Exception as e:
            print(f"Erreur lors du chargement du résumé : {e}")
        # ================================================================

        self.msg_queue.put({"sender": "Système", "text": "⚔️ MOTEUR INITIALISÉ. Connexion aux LLMs en cours...", "color": "#ffcc00"})

        # ── Journal narratif de session ───────────────────────────────────────
        _chat_log = ChatLogWriter()
        self.msg_queue.put({
            "sender": "📋 Système",
            "text":   f"Journal de session ouvert → {_chat_log.path}",
            "color":  "#607d8b",
        })

        # ── Chargement des configs LLM par personnage ─────────────────────────
        _char_state = load_state().get("characters", {})
        def _cfg(char_name: str) -> dict:
            # Priorité : campaign_state > app_config > défaut env
            model = (_char_state.get(char_name, {}).get("llm", "")
                     or get_agent_config(char_name).get("model", "")
                     or _default_model)
            temp  = get_agent_config(char_name).get("temperature", 0.7)
            return build_llm_config(model, temperature=temp)

        def _provider_label(char_name: str) -> str:
            model = (_char_state.get(char_name, {}).get("llm", "")
                     or get_agent_config(char_name).get("model", "")
                     or _default_model)
            if model.startswith("groq/"):        return f"Groq ({model[5:]})"
            if model.startswith("openrouter/"): return f"OpenRouter ({model[11:]})"
            return f"Gemini ({model})"

        providers_info = " | ".join(
            f"{n}: {_provider_label(n)}" for n in ["Kaelen","Elara","Thorne","Lyra"]
        )
        self.msg_queue.put({"sender": "Système", "text": f"🧠 Modèles chargés : {providers_info}", "color": "#aaaaff"})

        mj_agent = autogen.UserProxyAgent(
            name="Alexis_Le_MJ",
            system_message="Tu es Alexis, le Maître du Jeu suprême. Tu as l'autorité absolue sur le monde et les règles de D&D 5e.",
            human_input_mode="ALWAYS", 
            code_execution_config=False,
        )

        def gui_get_human_input(self_agent, prompt: str, **kwargs) -> str:
            # Si un trigger de tour combat est en attente (stocké par _on_pc_turn
            # avant que get_human_input soit appelé), on le consomme directement
            # sans afficher "En attente de votre action".
            if self._pending_combat_trigger is not None:
                trigger = self._pending_combat_trigger
                self._pending_combat_trigger = None
                return trigger
            self.msg_queue.put({"sender": "Système", "text": "En attente de votre action (Texte ou 🎤)...", "color": "#888888"})
            self._set_waiting_for_mj(True)
            result = self.wait_for_input()
            self._set_waiting_for_mj(False)
            return result
        
        mj_agent.get_human_input = types.MethodType(gui_get_human_input, mj_agent)

        # --- RÈGLES ANTI-HALLUCINATION (communes à tous les joueurs) ---
        _regle_outils = (
            "\n\n═══════════════════════════════════════════"
            "\nRÈGLES ABSOLUES — LIRE ET APPLIQUER À CHAQUE MESSAGE"
            "\n═══════════════════════════════════════════"
            "\n\n▶ HORS COMBAT"
            "\nTu joues ton rôle : roleplay, dialogue, exploration, réflexion."
            "\nNe déclare PAS d'action d'attaque, ne lance PAS de dés, ne prends PAS d'initiative de combat"
            "\nsauf si le MJ l'indique explicitement ou si COMBAT EN COURS apparaît dans tes instructions."
            "\nException : le MOUVEMENT est toujours autorisé hors combat via un bloc [ACTION] Type: Mouvement.\n"
            "\n▶ ACTIONS MÉCANIQUES (uniquement sur demande du MJ ou si le combat est actif)"
            "\nSi le MJ te demande une action mécanique, termine ton message par un bloc [ACTION] :\n\n"
            "  [ACTION]\n"
            "  Type      : <Action / Action Bonus / Réaction>\n"
            "  Intention : <ce que ton personnage fait, en une phrase claire>\n"
            "  Règle 5e  : <mécanique exacte : sort + niveau, attaque + bonus + dégâts, etc.>\n"
            "  Cible     : <sur qui ou quoi>\n\n"
            "▶ CONTRAT SYSTÈME :"
            "\n  1. Le SYSTÈME exécute les dés — tu ne lances rien toi-même."
            "\n  2. Tu reçois un [RÉSULTAT SYSTÈME] avec les valeurs exactes."
            "\n  3. Après un [RÉSULTAT SYSTÈME], tu narres UNIQUEMENT l'EFFORT physique ou mental"
            "\n     de ton personnage (la tension de ses muscles, sa concentration, sa sensation)."
            "\n     TU NE DÉCRIS JAMAIS CE QUE TU TROUVES, DÉCOUVRES OU PERÇOIS DANS L'ENVIRONNEMENT."
            "\n     C'est le MJ seul qui décrit ce qui existe dans le monde."
            "\n     Exemple INTERDIT : 'Je trouve une brique sur pivot dissimulée par la suie.'"
            "\n     Exemple CORRECT  : 'Mes doigts s'arrêtent. Quelque chose cloche ici.'"
            "\n  4. NE JAMAIS appeler roll_dice, use_spell_slot, update_hp, add_temp_hp de ta propre initiative."
            "\n     EXCEPTION : si tu reçois une [DIRECTIVE SYSTÈME — JET] ou [DIRECTIVE SYSTÈME — DÉGÂTS]"
            "\n     avec ton nom, tu DOIS appeler l'outil indiqué IMMÉDIATEMENT, AVANT tout texte."
            "\n  5. NE JAMAIS inventer un résultat différent de celui donné par le système.\n"
            "\n▶ MOUVEMENT SUR LA CARTE (combat ET exploration)"
            "\nDès que ton personnage se déplace narrativement, utilise un bloc [ACTION] Type: Mouvement."
            "\nCela fonctionne EN PERMANENCE — en combat et hors combat."
            "\nLe système déplacera automatiquement ton token sur la carte.\n"
            "  [ACTION]\n"
            "  Type      : Mouvement\n"
            "  Intention : <description narrative du déplacement>\n"
            "  Règle 5e  : <N cases (M m)> vers <nord/sud/est/ouest/nord-est…>\n"
            "              OU vers Col X, Lig Y  OU vers <nom d un allié/ennemi>\n"
            "  Cible     : <destination>\n"
            "\n▶ DÉGÂTS REÇUS"
            "\nQuand le MJ annonce que tu prends des dégâts, le SYSTÈME met tes PV à jour."
            "\nTon seul rôle : narrer en 1-2 phrases comment ton personnage encaisse le coup."
            "\nPas de chiffres — décris la douleur, le choc, ta posture. Reste dans l'action.\n"
            "\n▶ PNJ"
            "\nTu n'inventes JAMAIS les paroles d'un PNJ. Si tu t'adresses à un PNJ, ARRÊTE "
            "immédiatement après. Le MJ est la seule voix des PNJ."
            "\n\n▶ MONDE & UNICITÉ — RÈGLE ABSOLUE"
            "\nTu n'existes QUE dans ta tête et ton corps. Le monde extérieur appartient au MJ."
            "\nN'invente JAMAIS : un objet, une texture, une odeur, un mécanisme, un passage,"
            "\nune inscription, une créature, une réaction de PNJ — rien de ce qui existe hors"
            "\nde toi. Si ton jet de dés réussit, dis ce que TON CORPS ressent (une anomalie,"
            "\nun doute, une intuition) — PAS ce que tu trouves. Attends qu'Alexis décrive."
            "\nNe répète jamais une question ou idée déjà exprimée — apporte un angle nouveau"
            "\nou reste silencieux."
            "\n\n▶ INTERDICTION DE COPIE — RÈGLE ABSOLUE"
            "\nNe reproduis JAMAIS, même partiellement, le contenu du message précédent."
            "\nSi un autre personnage vient de dire ou faire quelque chose, tu ne le répètes pas,"
            "\nne le paraphrases pas, ne le reformules pas. Chaque personnage a sa propre voix,"
            "\nses propres actes. Si tu n'as rien d'original à apporter : dis [SILENCE]."
            "\n\n▶ ÉLOCUTION (SYNTHÈSE VOCALE)"
            "\nRépliques : 1-2 phrases MAX, courtes et percutantes. Ponctuation forte (?, !). "
            "Zéro tirade. Parle comme en pleine action."
            "\n═══════════════════════════════════════════\n"
        )

        kaelen_agent = autogen.AssistantAgent(
            name="Kaelen",
            system_message=(
                _regle_outils + 
                "Tu es Kaelen, un Paladin Humain de niveau 15, hanté par un serment passé.\n"
                "PERSONNALITÉ : Tu es économe en mots, fier et grave. Tes préoccupations sont toujours liées "
                "à l'honneur, aux serments, à qui mérite protection et à ce qui constitue une cause juste. "
                "Quand tu interviens, c'est pour évaluer la valeur morale de la mission ou jurer ta protection. "
                "Tu n'es pas curieux des mécaniques — tu veux savoir SI ça vaut le coup de mourir pour ça.\n"
                "CAPACITÉS DE COMBAT (à utiliser de façon autonome lors de ton tour) :\n"
                "  • ATTAQUE SUPPLÉMENTAIRE : ton Action t'accorde 2 attaques. Déclare-les toutes les deux.\n"
                "  • ACTION BONUS : sort de smite (Courroux Divin, Frappe Tonnerre…) AVANT l'attaque "
                "pour le déclencher sur un coup ; ou Aura de Protection passive (pas de coût).\n"
                "  • DIVINE SMITE : après un toucher, peut dépenser un emplacement de sort (1d8/niveau radiants, "
                "+1d8 contre morts-vivants/démons) — décision après le jet d'attaque.\n"
                "  • AURA DE PROTECTION (passif) : alliés à 30 ft ajoutent +5 aux jets de sauvegarde.\n"
                "  • Si aucune cible valide : Esquive (Dodge) ou Aide (Help un allié).\n"
                "FORMAT SMITE OBLIGATOIRE — n'utilise JAMAIS un bloc [ACTION] séparé pour le smite :\n"
                "  [ACTION]\n"
                "  Type      : Action — Attaque × 2 (Extra Attack)\n"
                "  Intention : Frapper deux fois ; Divine Smite sur attaque 1 si touche\n"
                "  Règle 5e  : Attaque 1 : corps-à-corps +11, 2d6+8 | Divine Smite niv.2 si touche\n"
                "              Attaque 2 : corps-à-corps +11, 2d6+8\n"
                "  Cible     : [la cible]\n"
                "Ne déclare PAS le smite comme Action Bonus séparé — il doit toujours être dans le même bloc que l'attaque.\n"
                "RÈGLES : 1. Alexis est MJ. "
                "2. Déclare toutes tes actions de façon autonome — n'attends pas qu'on te les liste. "
                "3. Ne décide pas si tu touches ou tues. N'invente pas d'environnement. "
                "4. Tu ne connais pas la vallée de Barovie, tout est nouveau ici pour toi."
                + get_scene_prompt()
                + get_active_quests_prompt()
                + get_memories_prompt_compact(importance_min=get_memories_config().get("compact_importance_min", 2))
                + get_calendar_prompt()
                + get_session_logs_prompt(max_sessions=3)
                + get_spells_prompt("Kaelen")
                + get_inventory_prompt()
            ),
            llm_config=_cfg("Kaelen"),
        )

        elara_agent = autogen.AssistantAgent(
            name="Elara",
            system_message=(
                _regle_outils + 
                "Tu es Elara, une Magicienne de niveau 15, froide et méthodique.\n"
                "PERSONNALITÉ : Tu analyses, tu quantifies, tu cherches les failles logiques. Tes questions portent "
                "toujours sur la mécanique précise des choses : comment fonctionne la magie du phare, quelle est "
                "la source du pouvoir, y a-t-il des données concrètes, des artefacts, des textes. "
                "Tu t'ennuies des généralités et tu coupes court aux discours flous. "
                "Tu ne poses JAMAIS une question qu'Elara a déjà posée, ni une que quelqu'un d'autre vient de poser.\n"
                "CAPACITÉS DE COMBAT (à utiliser de façon autonome lors de ton tour) :\n"
                "  • ACTION : lancer un sort (Boule de Feu, Rayon de Givre, Projectile Magique…), "
                "Attaque à l'arme si aucun sort pertinent, ou Esquive (Dodge) / Chercher (Search).\n"
                "  • ACTION BONUS : sort à temps d'incantation Bonus (ex. Feu Follet si actif, Objet Magique…).\n"
                "  • CONCENTRATION : vérifie si un sort actif est déjà en cours — ne lance pas deux sorts "
                "à concentration simultanément.\n"
                "  • ARCANE RECOVERY (1/repos long) : récupère des emplacements de sort après un repos court.\n"
                "  • Si aucun sort utile : Attaque magique de contact ou Esquive.\n"
                "RÈGLES : 1. Alexis est MJ. "
                "2. Déclare toutes tes actions de façon autonome — n'attends pas qu'on te les liste. "
                "3. Ne décide pas du résultat. N'invente pas d'environnement. "
                "4. Tu ne connais pas la vallée de Barovie, tout est nouveau ici pour toi."
                + get_scene_prompt()
                + get_active_quests_prompt()
                + get_memories_prompt_compact(importance_min=get_memories_config().get("compact_importance_min", 2))
                + get_calendar_prompt()
                + get_session_logs_prompt(max_sessions=3)
                + get_spells_prompt("Elara")
                + get_inventory_prompt()
            ),
            llm_config=_cfg("Elara"),
        )

        thorne_agent = autogen.AssistantAgent(
            name="Thorne",
            system_message=(
                _regle_outils + 
                "Tu es Thorne, un Voleur (Assassin) Tieffelin de niveau 15, cynique et pragmatique.\n"
                "PERSONNALITÉ : Tu vois le monde en termes de risques, de profits et de qui manipule qui. "
                "Tes questions portent sur les motivations cachées, les pièges potentiels, ce qu'on ne te dit pas, "
                "et ce que rapporte concrètement la mission. Tu es sarcastique et tu n'accordes ta confiance à personne. "
                "Tu parles avec un accent québécois."
                "Tu ne poses JAMAIS une question qu'un autre personnage vient de poser — tu trouves ça embarrassant.\n"
                "CAPACITÉS DE COMBAT (à utiliser de façon autonome lors de ton tour) :\n"
                "  • ACTION : 1 attaque (Rogues n'ont pas Extra Attack sauf Arcane Trickster/Eldritch Knight). "
                "Si avantage ou allié adjacent à la cible → ajoute SNEAK ATTACK (8d6) à l'attaque.\n"
                "  • ACTION BONUS — CUNNING ACTION (toujours disponible, au choix) :\n"
                "    - Dash : double ton mouvement ce tour\n"
                "    - Disengage : ton mouvement ne provoque pas d'attaque d'opportunité\n"
                "    - Hide : jet de Discrétion pour te cacher (avantage sur la prochaine attaque)\n"
                "  • ASSASSINATE (vs. surprise) : avantage sur les attaques, coups automatiquement critiques.\n"
                "  • UNCANNY DODGE (réaction) : réduit de moitié les dégâts d'une attaque qui te touche.\n"
                "  • EVASION (passif) : DD sauvegarde DEX : succès = 0 dégâts, échec = moitié.\n"
                "  • Priorité tactique : te positionner pour le Sneak Attack, puis Hide ou Disengage en Bonus.\n"
                "RÈGLES : 1. Alexis est ton MJ. "
                "2. Déclare toutes tes actions de façon autonome — n'attends pas qu'on te les liste. "
                "3. Ne décide jamais si tu réussis. N'invente pas d'environnement. "
                "4. Tu connais la légende de la vallée de Barovie, les grands mots, mais tu n'y crois pas."
                + get_scene_prompt()
                + get_active_quests_prompt()
                + get_memories_prompt_compact(importance_min=get_memories_config().get("compact_importance_min", 2))
                + get_calendar_prompt()
                + get_session_logs_prompt(max_sessions=3)
                + get_inventory_prompt()
            ),
            llm_config=_cfg("Thorne"),
        )

        lyra_agent = autogen.AssistantAgent(
            name="Lyra",
            system_message=(
                _regle_outils + 
                "Tu es Lyra, une Clerc (Domaine de la Vie) Demi-Elfe de niveau 15, bienveillante et implacable.\n"
                "PERSONNALITÉ : Tu penses d'abord aux innocents qui souffrent, à la dimension spirituelle et divine "
                "des événements, et à ce que les dieux pourraient vouloir ici. Tu poses des questions sur les victimes, "
                "la souffrance des gens ordinaires, les signes divins, et ce que signifie moralement la situation. "
                "Tu ne poses JAMAIS une question qu'un autre personnage vient de poser — chaque voix doit être unique.\n"
                "CAPACITÉS DE COMBAT (à utiliser de façon autonome lors de ton tour) :\n"
                "  • ACTION : lancer un sort (Mot de Mort, Gardiens Spirituels, Soins, Flamme Sacrée…), "
                "Attaque à l'arme (masse d'armes), ou Esquive / Aide.\n"
                "  • ACTION BONUS — ARME SPIRITUELLE (si déjà invoquée) : attaque bonus gratuite avec l'arme (1d8+5).\n"
                "  • CHANNEL DIVINITY (2/repos court) :\n"
                "    - Renvoi des morts-vivants (Turn Undead) : action, DD Sagesse, morts-vivants fuient.\n"
                "    - Préservation de la Vie : action, soigne 5×niveau Clerc PV répartis librement.\n"
                "  • DISCIPLE OF LIFE (passif) : sorts de soin récupèrent 2 + niveau du sort PV supplémentaires.\n"
                "  • Priorité : maintenir la concentration sur un sort actif ; soigner si un allié est < 25% PV.\n"
                "  • Si aucune action prioritaire : Mot de Mort sur l'ennemi le plus faible ou Esquive.\n"
                "RÈGLES : 1. Alexis est ton MJ. "
                "2. Déclare toutes tes actions de façon autonome — n'attends pas qu'on te les liste. "
                "3. Ne décide pas du résultat. N'invente pas d'environnement. "
                "4. Tu ne connais pas la vallée de Barovie, tout est nouveau ici pour toi."
                + get_scene_prompt()
                + get_active_quests_prompt()
                + get_memories_prompt_compact(importance_min=get_memories_config().get("compact_importance_min", 2))
                + get_calendar_prompt()
                + get_session_logs_prompt(max_sessions=3)
                + get_spells_prompt("Lyra")
                + get_inventory_prompt()
            ),
            llm_config=_cfg("Lyra"),
        )

        # --- STOCKAGE DES AGENTS pour MAJ dynamique des prompts combat ---
        self._agents = {
            "Kaelen": kaelen_agent,
            "Elara":  elara_agent,
            "Thorne": thorne_agent,
            "Lyra":   lyra_agent,
        }
        self._base_system_msgs = {
            name: agent.system_message
            for name, agent in self._agents.items()
        }

        # ── Bulle de pensée : wrapper generate_reply ──────────────────────────
        # Chaque fois qu'AutoGen demande à un agent joueur de générer une réponse
        # (= appel LLM en cours), on active l'animation de réflexion sur son avatar.
        # set_thinking est thread-safe (simple bool lu par la boucle Tk à 30 fps).
        def _make_thinking_wrapper(agent, name, app_ref):
            """
            Deux responsabilités :
              1. Bulle de pensée : set_thinking(True/False) autour de generate_reply.
              2. Interruption fiable : l'appel LLM réel tourne dans un sous-thread
                 daemon. Le thread autogen sonde _stop_event toutes les 50 ms.
                 Dès que _stop_event est levé, StopLLMRequested est lancé dans le
                 thread autogen IMMÉDIATEMENT — même si le sous-thread est encore
                 bloqué dans un appel C (HTTP/gRPC). Ce sous-thread finit sa
                 requête en tâche de fond (daemon → pas de fuite à l'arrêt de l'app).
            """
            import threading as _th_wrap
            _orig_gr = agent.generate_reply.__func__

            def _wrapped(self_agent, messages=None, sender=None, **kwargs):
                face = app_ref.face_windows.get(name)
                if face:
                    try:
                        face.set_thinking(True)
                    except Exception:
                        pass

                # Nettoyer un stop_event résiduel avant de commencer
                app_ref._stop_event.clear()

                result    = [None]
                exc_box   = [None]
                done_evt  = _th_wrap.Event()

                def _llm_call():
                    try:
                        # _SSL_LOCK : sérialise TOUS les appels httpx/OpenSSL pour éviter
                        # le segfault dans ssl.py quand deux threads partagent le pool SSL.
                        # Nécessaire car les daemon threads interrompus restent actifs.

                        # ── Snapshot avant l'appel (usage cumulatif) ─────────────────
                        # actual_usage_summary est cumulatif sur la session entière.
                        # On prend un snapshot AVANT puis on diff APRÈS pour isoler
                        # le modèle qui a répondu à CE call spécifique.
                        _usage_before = dict(
                            getattr(self_agent.client, "actual_usage_summary", None) or {}
                        )

                        # ── Reset du sticky-fallback d'AutoGen ────────────────────────
                        # OpenAIWrapper mémorise le dernier index de config_list ayant
                        # réussi (_last_config_idx). Sans reset, une erreur transitoire
                        # suffit à faire basculer TOUS les appels suivants vers le
                        # fallback — même quand le modèle primaire est de nouveau dispo.
                        try:
                            self_agent.client._last_config_idx = 0
                        except Exception:
                            pass

                        with _SSL_LOCK:
                            result[0] = _orig_gr(
                                self_agent, messages=messages, sender=sender, **kwargs
                            )

                        # ── Log du modèle ayant effectivement répondu ────────────────
                        try:
                            from agent_logger import log_llm_model_used
                            from state_manager import load_state as _ls_log
                            _usage_after = getattr(self_agent.client, "actual_usage_summary", None) or {}
                            # Modèles nouveaux ou dont le compteur a augmenté depuis le snapshot
                            _new = [
                                m for m in _usage_after
                                if m != "total_cost"
                                and _usage_after[m] != _usage_before.get(m)
                            ]
                            actual = _new[0] if _new else None
                            if actual:
                                # Source de vérité : campaign_state d'abord
                                _cs = _ls_log().get("characters", {}).get(name, {})
                                configured = (_cs.get("llm", "")
                                              or get_agent_config(name).get("model", "")
                                              or "")
                                log_llm_model_used(name, actual, configured)
                        except Exception:
                            pass
                    except StopLLMRequested:
                        # Thread interrompu via ctypes : on sort proprement
                        # Le lock with-block est déjà libéré par l'exception
                        exc_box[0] = StopLLMRequested()
                    except BaseException as _e:
                        exc_box[0] = _e
                    finally:
                        done_evt.set()

                llm_thread = _th_wrap.Thread(target=_llm_call, daemon=True,
                                             name=f"llm-call-{name}")
                llm_thread.start()

                # Sondage : vérifie stop_event toutes les 50 ms
                while not done_evt.wait(timeout=0.05):
                    if app_ref._stop_event.is_set():
                        app_ref._stop_event.clear()
                        if face:
                            try:
                                face.set_thinking(False)
                            except Exception:
                                pass
                        # Le sous-thread LLM continue en daemon — pas de fuite
                        raise StopLLMRequested()

                if face:
                    try:
                        face.set_thinking(False)
                    except Exception:
                        pass

                if exc_box[0] is not None:
                    raise exc_box[0]
                return result[0]

            import types as _types
            return _types.MethodType(_wrapped, agent)

        for _think_name, _think_agent in self._agents.items():
            _think_agent.generate_reply = _make_thinking_wrapper(
                _think_agent, _think_name, self
            )

        # ── Wrapper tolérant pour roll_dice ──────────────────────────────────
        # Les LLMs envoient parfois dice_notation="5d4+5" au lieu des deux
        # champs séparés dice_type="5d4" et bonus=5. Ce wrapper accepte les deux
        # formes et évite les erreurs Pydantic validation.
        import re as _re_dice
        from state_manager import roll_dice as _roll_dice_orig
        def roll_dice_safe(character_name: str,
                           dice_type: str = "",
                           bonus: int = 0,
                           dice_notation: str = "") -> str:
            """
            Lance des dés pour character_name.
            Paramètres :
              character_name : nom du personnage (ex: "Kaelen")
              dice_type      : formule sans bonus (ex: "2d6")
              bonus          : modificateur entier (ex: 5)
            Le paramètre dice_notation="2d6+5" est aussi accepté en fallback.
            """
            # Si le LLM a utilisé dice_notation, on le parse
            if dice_notation and not dice_type:
                _m = _re_dice.match(r"(\d+d\d+)\s*([+-]\s*\d+)?", dice_notation.strip())
                if _m:
                    dice_type = _m.group(1)
                    bonus     = int((_m.group(2) or "0").replace(" ", "")) if _m.group(2) else 0
            # Si dice_type contient un bonus intégré (ex: "2d6+5"), on l'extrait
            if dice_type and ('+' in dice_type or (dice_type.count('-') > 0 and 'd' in dice_type)):
                _m2 = _re_dice.match(r"(\d+d\d+)\s*([+-]\s*\d+)?", dice_type.strip())
                if _m2:
                    bonus    = int((_m2.group(2) or "0").replace(" ", "")) if _m2.group(2) else bonus
                    dice_type = _m2.group(1)
            if not dice_type:
                return "Erreur : dice_type manquant. Exemple : dice_type='2d6', bonus=5"
            return _roll_dice_orig(character_name, dice_type, int(bonus))

        # --- ENREGISTREMENT DES OUTILS PAR RÔLE ---
        # update_hp : enregistré sur TOUS les agents joueurs.
        # Chaque PJ doit pouvoir appliquer lui-même les dégâts reçus ou
        # les soins obtenus quand le MJ le lui indique.
        _update_hp_desc = (
            "Mettre à jour les PV d'un personnage. "
            "Utilise un entier NÉGATIF pour des dégâts (ex: -7), POSITIF pour un soin (ex: +12). "
            "Paramètres : character_name (str, ex: 'Thorne'), amount (int). "
            "À appeler dès que le MJ annonce que tu prends des dégâts ou reçois un soin."
        )
        for _upd_agent in [kaelen_agent, elara_agent, thorne_agent, lyra_agent]:
            autogen.agentchat.register_function(
                update_hp, caller=_upd_agent, executor=mj_agent,
                name="update_hp",
                description=_update_hp_desc,
            )

        # add_temp_hp : enregistré sur TOUS les agents joueurs.
        _add_temp_hp_desc = (
            "Ajouter des PV temporaires à un personnage (sorts, capacités raciales, etc.). "
            "Règle D&D 5e : les PV temporaires ne se cumulent pas — seul le plus grand total est conservé. "
            "Ils absorbent les dégâts AVANT les PV réels. Les soins ne les restaurent pas. "
            "Paramètres : character_name (str, ex: 'Lyra'), amount (int positif, ex: 8). "
            "À appeler dès que le MJ confirme que tu gagnes des PV temporaires."
        )
        for _upd_agent in [kaelen_agent, elara_agent, thorne_agent, lyra_agent]:
            autogen.agentchat.register_function(
                add_temp_hp, caller=_upd_agent, executor=mj_agent,
                name="add_temp_hp",
                description=_add_temp_hp_desc,
            )

        # ── Inventaire du groupe : enregistré sur TOUS les agents joueurs ────
        # Les agents peuvent ajouter/retirer des objets et mettre à jour la
        # monnaie quand le MJ annonce un gain ou une dépense.
        _add_item_desc = (
            "Ajouter un objet à l'inventaire du groupe (ou incrémenter sa quantité). "
            "Paramètres : name (str), quantity (int, défaut 1), "
            "category (str : arme/armure/potion/objet_magique/munition/outil/divers), "
            "rarity (str : commun/peu_commun/rare/très_rare/légendaire/artéfact), "
            "description (str), notes (str). "
            "À appeler quand le MJ confirme que le groupe trouve ou reçoit un objet."
        )
        _remove_item_desc = (
            "Retirer une quantité d'un objet de l'inventaire du groupe. "
            "Paramètres : name (str), quantity (int, défaut 1). "
            "À appeler quand le groupe utilise, perd ou vend un objet."
        )
        _currency_desc = (
            "Mettre à jour la monnaie du groupe (positif = gain, négatif = dépense). "
            "Paramètres : gold (int), silver (int), copper (int), platinum (int), electrum (int). "
            "Exemple gain : gold=50, silver=10. Exemple dépense : gold=-30. "
            "À appeler quand le MJ annonce un gain ou une dépense de monnaie."
        )
        for _inv_agent in [kaelen_agent, elara_agent, thorne_agent, lyra_agent]:
            autogen.agentchat.register_function(
                add_item_to_inventory, caller=_inv_agent, executor=mj_agent,
                name="add_item_to_inventory", description=_add_item_desc,
            )
            autogen.agentchat.register_function(
                remove_item_from_inventory, caller=_inv_agent, executor=mj_agent,
                name="remove_item_from_inventory", description=_remove_item_desc,
            )
            autogen.agentchat.register_function(
                update_currency, caller=_inv_agent, executor=mj_agent,
                name="update_currency", description=_currency_desc,
            )

        # Kaelen et Thorne : combat (dés + sorts)
        for agent in [kaelen_agent, thorne_agent]:
            autogen.agentchat.register_function(
                roll_dice_safe, caller=agent, executor=mj_agent,
                name="roll_dice",
                description=(
                    "Lancer des dés pour un personnage. "
                    "Paramètres OBLIGATOIRES séparés : "
                    "character_name (str, ex: 'Kaelen'), "
                    "dice_type (str, formule SANS bonus, ex: '2d6' ou '1d20'), "
                    "bonus (int, modificateur, ex: 5 ou -1). "
                    "Exemple correct : character_name='Kaelen', dice_type='2d6', bonus=8. "
                    "NE PAS utiliser dice_notation ni combiner le bonus dans dice_type."
                )
            )
            autogen.agentchat.register_function(
                use_spell_slot, caller=agent, executor=mj_agent,
                name="use_spell_slot",
                description="Consommer un slot de sort (1-9). À appeler UNIQUEMENT si le MJ te le demande explicitement."
            )

        # Elara : sorts + dés (jets d'attaque de sort, dégâts)
        autogen.agentchat.register_function(
            roll_dice_safe, caller=elara_agent, executor=mj_agent,
            name="roll_dice",
            description=(
                "Lancer des dés pour un personnage. "
                "Paramètres OBLIGATOIRES séparés : "
                "character_name (str, ex: 'Elara'), "
                "dice_type (str, formule SANS bonus, ex: '8d6' ou '1d20'), "
                "bonus (int, modificateur, ex: 10 ou 0). "
                "Exemple correct : character_name='Elara', dice_type='8d6', bonus=0."
            )
        )
        autogen.agentchat.register_function(
            use_spell_slot, caller=elara_agent, executor=mj_agent,
            name="use_spell_slot",
            description="Consommer un slot de sort (1-9). Paramètres : character_name (str), level (str, ex: '3')."
        )

        # Lyra : sorts + soins + dés (Arme Spirituelle, jets d'attaque de sort)
        autogen.agentchat.register_function(
            roll_dice_safe, caller=lyra_agent, executor=mj_agent,
            name="roll_dice",
            description=(
                "Lancer des dés pour un personnage. "
                "Paramètres OBLIGATOIRES séparés : "
                "character_name (str, ex: 'Lyra'), "
                "dice_type (str, formule SANS bonus, ex: '1d8'), "
                "bonus (int, modificateur, ex: 7). "
                "Exemple correct : character_name='Lyra', dice_type='1d8', bonus=7."
            )
        )
        autogen.agentchat.register_function(
            use_spell_slot, caller=lyra_agent, executor=mj_agent,
            name="use_spell_slot",
            description="Consommer un slot de sort (1-9). Paramètres : character_name (str), level (str, ex: '3')."
        )
        # --- SÉLECTEUR DE SPEAKER COMBAT-AWARE ---
        # PLAYER_NAMES est recalculé dynamiquement à chaque appel pour tenir compte
        # des personnages activés/désactivés en cours de session.
        _ALL_PLAYER_NAMES = ["Kaelen", "Elara", "Thorne", "Lyra"]
        _app_ref = self   # référence pour les closures

        def combat_speaker_selector(last_speaker, groupchat):
            """
            Sélecteur de speaker entièrement déterministe — ne retourne JAMAIS "auto".

            Retourner "auto" déclenche _auto_select_speaker() dans autogen, qui
            appelle _create_internal_agents() → instancie des agents LLM temporaires
            → références circulaires → GC pendant inspect.getfullargspec() → SEGFAULT.

            Stratégie basée sur l'intention du MJ (par ordre de priorité) :

              1. Noms explicites : le MJ mentionne un ou plusieurs PJ par nom
                 ("Elara, qu'en penses-tu ?" / "Kaelen et Thorne, agissez.")
                 → seuls ces PJ répondent, dans l'ordre d'apparition dans le message.

              2. Question de groupe : pas de nom mentionné mais présence d'un '?'
                 ou d'un marqueur de groupe ("tout le monde", "vous tous", "le groupe")
                 → tous les PJ actifs répondent, chacun une seule fois.

              3. Narration / pas de question : pas de nom, pas de '?'
                 → un seul PJ réagit (rotation simple, comme avant).

              4. Un PJ vient de parler → retour au MJ.

            SOURCE DE VÉRITÉ UNIQUE : groupchat.agents (maintenu par
            _sync_groupchat_agents). On N'appelle PLUS get_active_characters()
            ici — cela créait une désynchronisation quand le flag JSON et la liste
            d'agents divergeaient entre deux toggles.
            """
            _ALL_PLAYERS = ["Kaelen", "Elara", "Thorne", "Lyra"]
            _GROUP_MARKERS = ("tout le monde", "vous tous", "le groupe", "chacun",
                              "l'équipe", "vous avez", "que faites-vous",
                              "vos réactions", "qu'en pensez-vous")

            # Joueurs actuellement dans le groupchat (= présents dans la scène)
            _players_in_gc = [a for a in groupchat.agents if a.name in _ALL_PLAYERS]
            _player_names_in_gc = {a.name for a in _players_in_gc}

            def _eligible_agents():
                """Agents pouvant prendre la parole ce tour."""
                if not COMBAT_STATE["active"]:
                    return list(groupchat.agents)
                else:
                    candidates = [
                        a for a in groupchat.agents
                        if not _is_fully_silenced(a.name) or a.name not in _ALL_PLAYERS
                    ]
                    if not candidates:
                        candidates = [a for a in groupchat.agents if a.name == "Alexis_Le_MJ"]
                    return candidates

            eligible = _eligible_agents()
            if not eligible:
                mj = next((a for a in groupchat.agents if a.name == "Alexis_Le_MJ"), None)
                return mj or groupchat.agents[0]

            eligible_names  = {a.name for a in eligible}
            last_name       = last_speaker.name if last_speaker else ""
            mj_agent_ref    = next((a for a in eligible if a.name == "Alexis_Le_MJ"), None)

            # ─── Helpers ──────────────────────────────────────────────────────

            def _find_last_mj_msg():
                """Retourne (index, content) du dernier message MJ, ou (None, '')."""
                for i in range(len(groupchat.messages) - 1, -1, -1):
                    if groupchat.messages[i].get("name") == "Alexis_Le_MJ":
                        return i, str(groupchat.messages[i].get("content", ""))
                return None, ""

            def _responded_since(mj_idx):
                """Ensemble des PJ ayant répondu après groupchat.messages[mj_idx]."""
                responded = set()
                for msg in groupchat.messages[mj_idx + 1:]:
                    if msg.get("name") in _ALL_PLAYERS:
                        responded.add(msg.get("name"))
                return responded

            def _next_pending(target_list, responded):
                """Premier PJ de target_list non encore répondu et éligible."""
                for name in target_list:
                    if name not in responded and name in eligible_names:
                        return next((a for a in eligible if a.name == name), None)
                return None

            # ─── Analyse du dernier message MJ ────────────────────────────────
            # Exécutée que le dernier locuteur soit le MJ OU un PJ (pour enchaîner
            # les réponses quand plusieurs PJ sont ciblés).

            last_mj_idx, last_mj_content = _find_last_mj_msg()

            if last_mj_idx is not None:
                # ── Garde approbation / message vide ──────────────────────────
                # [APPROBATION] ou vide → MJ reprend la main (pas de rotation PJ).
                # [PAROLE_SPONTANEE] → sauter l'analyse et aller direct à la rotation.
                _stripped = last_mj_content.strip()
                if _stripped == "[PAROLE_SPONTANEE]":
                    # Un seul PJ parle puis retour au MJ — pas de round-robin.
                    # Trouver si un PJ a déjà répondu APRÈS ce message [PAROLE_SPONTANEE].
                    _ps_responded = _responded_since(last_mj_idx)
                    if _ps_responded:
                        # Un PJ a parlé → MJ reprend la main
                        return mj_agent_ref or eligible[0]
                    # Aucun PJ n'a encore parlé → choisir aléatoirement parmi les PJ
                    players_eligible =[a for a in eligible if a.name in _ALL_PLAYERS]
                    if players_eligible:
                        import random
                        return random.choice(players_eligible)

                # ── Résultat d'outil : MJ a auto-répondu après exécution ──
                # Ce n'est pas un vrai message narratif — ne pas déclencher
                # de rotation de PJ. Retourner au MJ pour attendre le vrai input.
                _is_tool_result = (
                    _stripped.startswith("[RÉSULTAT SYSTÈME")
                    or _stripped.startswith("Error: Function")
                    or "Function" in _stripped and "not found" in _stripped
                )
                if _is_tool_result:
                    if mj_agent_ref:
                        return mj_agent_ref
                    return eligible[0]

                content_low = last_mj_content.lower()

                # Cas 1 — noms explicites dans le message du MJ
                mentioned = [
                    name for name in _ALL_PLAYERS
                    if name.lower() in content_low
                    and name in _player_names_in_gc
                ]

                # Cas 2 — question de groupe (pas de nom + '?' ou marqueur de groupe)
                if not mentioned:
                    is_group_question = (
                        "?" in last_mj_content
                        or any(m in content_low for m in _GROUP_MARKERS)
                    )
                    if is_group_question:
                        mentioned = [n for n in _ALL_PLAYERS if n in _player_names_in_gc]

                if mentioned:
                    responded = _responded_since(last_mj_idx)
                    pending   = _next_pending(mentioned, responded)
                    if pending:
                        return pending
                    # Tous les PJ ciblés ont répondu → retour au MJ
                    if mj_agent_ref:
                        return mj_agent_ref

            # ─── Un PJ vient de parler (hors ciblage multi-joueurs) → MJ ──────
            if last_name in _ALL_PLAYERS:
                if mj_agent_ref:
                    return mj_agent_ref

            # ─── MJ vient de parler sans cibler → attendre l'input ─────────
            # Pas de rotation automatique. Le MJ attend que l'utilisateur
            # tape quelque chose ou appuie sur Enter ([PAROLE_SPONTANEE]).
            if last_name == "Alexis_Le_MJ":
                return mj_agent_ref or eligible[0]

            # ─── Fallback ultime : choix aléatoire parmi les PJ éligibles ───────────────
            players_eligible = [a for a in eligible if a.name in _ALL_PLAYERS]
            if players_eligible:
                import random
                # Tente d'éviter de faire parler le même PJ deux fois de suite si d'autres sont dispos
                candidates = [a for a in players_eligible if a.name != last_name]
                return random.choice(candidates if candidates else players_eligible)

            return eligible[0]

        # Sauvegarde de l'objet groupchat sur l'instance (self) pour pouvoir faire le résumé plus tard
        _gc_cfg   = get_groupchat_config()
        _chron_cfg = get_chronicler_config()
        _manager_llm = build_llm_config(
            _chron_cfg.get("model", _default_model),
            temperature=_chron_cfg.get("temperature", 0.3),
        )

        # ── Filtrage des agents inactifs (hors scène) ─────────────────────────
        _all_player_agents = {
            "Kaelen": kaelen_agent,
            "Elara":  elara_agent,
            "Thorne": thorne_agent,
            "Lyra":   lyra_agent,
        }
        _active_names = get_active_characters()
        _active_agents = [mj_agent] + [
            agent for name, agent in _all_player_agents.items()
            if name in _active_names
        ]
        _inactive_names = [n for n in _all_player_agents if n not in _active_names]
        if _inactive_names:
            self.msg_queue.put({
                "sender": "⚙ Scène",
                "text":   f"Agents absents de la scène (silenciés) : {', '.join(_inactive_names)}",
                "color":  "#666677",
            })

        self.groupchat = autogen.GroupChat(
            agents=_active_agents,
            messages=[],
            max_round=_gc_cfg.get("max_round", 100),
            speaker_selection_method=combat_speaker_selector,
            allow_repeat_speaker=_gc_cfg.get("allow_repeat_speaker", False),
        )
        manager = autogen.GroupChatManager(groupchat=self.groupchat, llm_config=_manager_llm)

        # FIX SEGFAULT : on capture la méthode originale au niveau CLASSE (unbound),
        # pas au niveau instance. Puis on remplace __class__ par une sous-classe anonyme.
        # Raison : types.MethodType() sur une instance crée un objet Python temporaire
        # que les threads C natifs de gRPC peuvent accéder sans tenir le GIL → SEGFAULT.
        # Remplacer __class__ fait résoudre receive() via la MRO de façon atomique.
        # Noms des PNJ connus — à compléter au fil de la campagne
        PNJ_NAMES = ["Ismark", "Strahd", "Ireena", "Madam Eva", "Rahadin", "Viktor", "Morgantha"]
        PLAYER_NAMES = ["Kaelen", "Elara", "Thorne", "Lyra"]
        SPELL_CASTERS = ["Kaelen", "Elara", "Lyra"]  # Thorne n'a pas de sorts
        import re as _re
        _pnj_pattern = _re.compile(
            r'(?:^|\n)\s*(?:' + '|'.join(_re.escape(n) for n in PNJ_NAMES) + r')\s*:',
            _re.IGNORECASE | _re.MULTILINE
        )
        # Détecte [SORT: NomDuSort | Niveau: X | Cible: Y]
        _sort_pattern = _re.compile(
            r'\[SORT\s*:\s*(?P<nom>[^|\]]+?)\s*\|\s*Niveau\s*:\s*(?P<niveau>\d)\s*(?:\|\s*Cible\s*:\s*(?P<cible>[^\]]*?))?\s*\]',
            _re.IGNORECASE
        )
        # Détecte un ou plusieurs blocs [ACTION] (multiligne).
        # Capture optionnelle du champ Type et règle multiligne (Extra Attack).
        _action_pattern = _re.compile(
            r'\[ACTION\][ \t]*\n'
            r'(?:[ \t]*Type[ \t]*:[ \t]*(?P<type>[^\n]+)\n)?'
            r'[ \t]*Intention[ \t]*:[ \t]*(?P<intention>[^\n]+)\n'
            r'[ \t]*R[eè]gle 5e[ \t]*:[ \t]*(?P<regle>.+?)\n'
            r'[ \t]*Cible[ \t]*:[ \t]*(?P<cible>[^\n]+)',
            _re.IGNORECASE | _re.DOTALL
        )

        def _split_into_subactions(type_label: str, intention: str,
                                   regle: str, cible: str) -> list[dict]:
            """
            Décompose un bloc [ACTION] en sous-actions individuelles.

            • Extra Attack (Attaque × N) → une carte de confirmation par attaque.
            • Bloc attaque + smite combiné → single_attack=True (flow Phase 1/2/3).
            • Tout autre bloc → une seule carte.

            Retourne une liste de dict {type_label, intention, regle, cible}.
            """
            type_low  = (type_label or "").lower()
            intent_low = intention.lower()
            regle_low  = regle.lower()
            combined   = type_low + " " + intent_low + " " + regle_low

            # ── Détection Extra Attack (format structuré ou langage naturel) ──
            is_extra = (
                "extra attack" in combined
                or bool(_re.search(r'attaque[s]?\s*[×x]\s*\d+', combined))
                or bool(_re.search(r'\d+\s*attaques?', combined))
                or "deux fois" in intent_low
                or "deux attaques" in intent_low
                or "two attacks" in combined
            )

            if is_extra:
                # Cas 1 : lignes "Attaque N : détail" dans le champ règle
                lines = _re.findall(
                    r'attaque\s*(\d+)\s*:\s*([^\n]+)',
                    regle, _re.IGNORECASE
                )
                if lines:
                    total = len(lines)
                    return [
                        {
                            "type_label":    f"Action — Attaque {i+1}/{total} (Extra Attack)",
                            "intention":     intention,
                            "regle":         detail.strip(),
                            "cible":         cible,
                            "single_attack": True,
                        }
                        for i, (_, detail) in enumerate(lines)
                    ]

                # Cas 2 : pas de lignes structurées → déduire N depuis le texte
                _n_m = (
                    _re.search(r'attaque[s]?\s*[×x]\s*(\d+)', combined)
                    or _re.search(r'(\d+)\s*(?:fois|attaques?)', intent_low)
                )
                n_attacks = int(_n_m.group(1)) if _n_m else (
                    2 if ("deux" in combined or "2" in type_low) else
                    int(_re.search(r'(\d)', type_low).group(1))
                    if _re.search(r'\d', type_low) else 2
                )
                regle_clean = regle.strip()
                return [
                    {
                        "type_label":    f"Action — Attaque {i+1}/{n_attacks} (Extra Attack)",
                        "intention":     intention,
                        "regle":         regle_clean,
                        "cible":         cible,
                        "single_attack": True,
                    }
                    for i in range(n_attacks)
                ]

            # ── Bloc attaque + smite combiné dans un seul [ACTION] ──────────
            # Ex: "Je frappe. Si ça touche, j'utilise Châtiment Divin."
            # → Force le flow single_attack (Phase 1 jet → Phase 2 smite → Phase 3 dégâts)
            _SMITE_DETECT = ("smite", "châtiment", "chatiment", "courroux divin",
                             "frappe tonnerre", "frappe lumière", "branding smite",
                             "divine smite", "smite divin")
            _ATK_DETECT   = ("attaque", "frappe", "coup", "tir", "corps-à-corps",
                             "poignarde", "tranche", "assaut")
            _has_smite = any(k in combined for k in _SMITE_DETECT)
            _has_atk   = any(k in combined for k in _ATK_DETECT)
            if _has_smite and _has_atk:
                return [{
                    "type_label":    type_label or "Action",
                    "intention":     intention,
                    "regle":         regle.strip(),
                    "cible":         cible,
                    "single_attack": True,   # → Phase 1 jet d'attaque, Phase 2 smite
                }]

            return [{
                "type_label": type_label or "Action",
                "intention":  intention,
                "regle":      regle.strip(),
                "cible":      cible,
            }]
        # Détecte les annonces de dégâts par le MJ sur un héros joueur.
        # Exemples couverts :
        #   "Kaelen prend 14 dégâts"  "Thorne subit 7 points de dégâts"
        #   "inflige 22 dégâts à Elara"  "Lyra reçoit 9 dégâts de feu"
        #   "Kaelen perd 5 PV"  "- 18 PV pour Thorne"
        #   "lui fait 7 dégâts"  "lui inflige 12 dégâts de force"  (Forme E, cible = contexte)
        #   "7 dégâts à Thorne"  "9 dégâts pour Elara"             (Forme F)
        _damage_pattern = _re.compile(
            r'(?:'
            # Forme A : "<Nom> prend/subit/reçoit/perd N dégâts/PV"
            r'(?P<tgt_a>Kaelen|Elara|Thorne|Lyra)\s+(?:prend|subit|re[çc]oit|perd)\s+(?P<dmg_a>\d+)\s*(?:d[eé]g[aâ]ts?|points?\s*de\s*d[eé]g[aâ]ts?|PV|pv|hp)'
            r'|'
            # Forme B : "inflige/cause/fait N dégâts à <Nom>"
            r'(?:inflige|cause|fait|d[eé]al)\s+(?P<dmg_b>\d+)\s*(?:d[eé]g[aâ]ts?|points?\s*de\s*d[eé]g[aâ]ts?|PV|pv|hp)\s+[àa]\s+(?P<tgt_b>Kaelen|Elara|Thorne|Lyra)'
            r'|'
            # Forme C : "- N PV pour/à <Nom>" ou "-N PV Kaelen"
            r'-\s*(?P<dmg_c>\d+)\s*(?:PV|pv|hp|d[eé]g[aâ]ts?)\s*(?:pour|[àa])?\s*(?P<tgt_c>Kaelen|Elara|Thorne|Lyra)'
            r'|'
            # Forme D : "tu prends/subis/reçois N dégâts" (MJ s'adresse directement, cible = contexte)
            r'tu\s+(?:te\s+)?(?:prend[s]?|subis|re[çc]ois|perds?)\s+(?P<dmg_d>\d+)\s*(?:d[eé]g[aâ]ts?|points?\s*de\s*d[eé]g[aâ]ts?|PV|pv|hp)'
            r'|'
            # Forme E : "lui/leur inflige/cause/fait N dégâts" (pronom indirect, cible = contexte)
            r'(?:lui|leur|vous)\s+(?:inflige|cause|fait|deal)\s+(?P<dmg_e>\d+)\s*(?:d[eé]g[aâ]ts?|points?\s*de\s*d[eé]g[aâ]ts?|PV|pv|hp)'
            r'|'
            # Forme F : "N dégâts à/pour <Nom>" (résumé court sans verbe)
            r'(?P<dmg_f>\d+)\s*(?:d[eé]g[aâ]ts?|points?\s*de\s*d[eé]g[aâ]ts?|PV|pv|hp)\s+[àa]\s+(?P<tgt_f>Kaelen|Elara|Thorne|Lyra)'
            r'|'
            # Forme G : "N dégâts pour <Nom>"
            r'(?P<dmg_g>\d+)\s*(?:d[eé]g[aâ]ts?|points?\s*de\s*d[eé]g[aâ]ts?|PV|pv|hp)\s+pour\s+(?P<tgt_g>Kaelen|Elara|Thorne|Lyra)'
            r')',
            _re.IGNORECASE,
        )
        _PC_NAME_RE = _re.compile(r'\b(Kaelen|Elara|Thorne|Lyra)\b', _re.IGNORECASE)

        # Event pour bloquer l'agent pendant la confirmation du MJ (sort)
        import threading as _threading
        _spell_confirm_event = _threading.Event()
        _spell_confirm_result = {}   # {"confirmed": bool, "level": int}
        _original_receive = manager.__class__.receive
        _app = self  # référence explicite pour les closures

        # ── Stats mécaniques D&D 5e 2014, niveau 15 ──────────────────────────
        _CHAR_MECHANICS = {
            "Kaelen": {  # Paladin 15 — STR20 DEX14 CON16 INT10 WIS14 CHA18 — Prof+5
                "atk_melee": +11, "atk_ranged": +7, "atk_spell": +9,
                "dmg_melee": (2, 6, +8), "n_attacks": 3, "save_dc": 18,
                "skills": {"athlétisme":+10,"religion":+5,"persuasion":+9,
                           "perspicacité":+7,"intimidation":+9,"perception":+7},
                "saves":  {"force":+10,"dextérité":+7,"constitution":+8,
                           "intelligence":+5,"sagesse":+7,"charisme":+9},
            },
            "Elara": {   # Mage 15 — STR8 DEX16 CON14 INT20 WIS14 CHA10 — Prof+5
                "atk_melee": +3, "atk_ranged": +8, "atk_spell": +10,
                "dmg_melee": (1, 4, -1), "n_attacks": 1, "save_dc": 18,
                "skills": {"arcanes":+15,"histoire":+10,"investigation":+10,
                           "nature":+10,"religion":+10,"perception":+7,"perspicacité":+7},
                "saves":  {"force":-1,"dextérité":+8,"constitution":+7,
                           "intelligence":+10,"sagesse":+7,"charisme":+5},
            },
            "Thorne": {  # Voleur Assassin 15 — STR12 DEX20 CON14 INT16 WIS12 CHA14 — Prof+5
                "atk_melee": +11, "atk_ranged": +11, "atk_spell": None,
                "dmg_melee": (1, 6, +5), "dmg_sneak": (8, 6, 0),
                "n_attacks": 2, "save_dc": None,
                "skills": {"discrétion":+15,"escamotage":+15,"tromperie":+12,
                           "perception":+11,"perspicacité":+6,"acrobaties":+10,
                           "investigation":+8,"athlétisme":+6,"intimidation":+7},
                "saves":  {"force":+6,"dextérité":+10,"constitution":+7,
                           "intelligence":+8,"sagesse":+6,"charisme":+7},
            },
            "Lyra": {    # Clerc Vie 15 — STR14 DEX12 CON14 INT12 WIS20 CHA16 — Prof+5
                "atk_melee": +7, "atk_ranged": +6, "atk_spell": +10,
                "dmg_melee": (1, 8, +2), "n_attacks": 2, "save_dc": 18,
                "skills": {"médecine":+15,"perspicacité":+10,"religion":+6,
                           "persuasion":+8,"perception":+10,"histoire":+6},
                "saves":  {"force":+7,"dextérité":+6,"constitution":+7,
                           "intelligence":+6,"sagesse":+10,"charisme":+8},
            },
        }

        # Dict {char_name: {"dice": "1d6", "type": "psychique", "label": "Wrathful Smite"}}
        # Stocke les smites en attente d'être appliqués sur la prochaine attaque.
        _pending_smite: dict = {}

        # Set des PJ qui doivent narrer des dégâts reçus (annoncés par le MJ).
        # Leur prochaine réponse est une narration de douleur — elle ne coûte
        # AUCUNE ressource hors-tour (ni parole, ni réaction).
        _pending_damage_narrators: set = set()

        # ── Parseur LLM de directives MJ ─────────────────────────────────────
        # Pré-filtre léger : on n'appelle le LLM que si le message du MJ contient
        # des indicateurs de directive mécanique (chiffres, mots-clés).
        _DIRECTIVE_PREFILTER = _re.compile(
            r'\d'                                          # un chiffre
            r'|(?:d[eé]g[aâ]t|pv\b|hp\b|soin|jet|roll'
            r'|sauvegarde|save\b|attaque|touche|rate)',
            _re.IGNORECASE,
        )

        _PARSER_SYSTEM = (
            "Tu es un parseur JSON pour D&D 5e. "
            "Analyse le message du MJ et extrais UNIQUEMENT les directives mécaniques "
            "destinées aux personnages joueurs (Kaelen, Elara, Thorne, Lyra).\n"
            "Réponds UNIQUEMENT avec un tableau JSON valide — rien d'autre, "
            "aucun texte avant ni après, aucun markdown.\n\n"
            "Format de chaque directive :\n"
            '{"action":"degats"|"soin"|"jet_sauvegarde"|"jet_competence"|"jet_attaque","cible":"Kaelen"|"Elara"|"Thorne"|"Lyra"|"tous","montant":<int>,"type_degat":<str>,"de":<str>,"bonus":<int>,"dc":<int>,"caracteristique":<str>}\n\n'
            "Champs obligatoires selon l'action :\n"
            "  degats  → cible, montant  (type_degat optionnel)\n"
            "  soin    → cible, montant\n"
            "  jet_sauvegarde → cible, caracteristique, dc\n"
            "  jet_competence → cible, caracteristique  (dc optionnel)\n"
            "  jet_attaque    → cible, de, bonus\n\n"
            "Règles d'inférence de la cible :\n"
            "  - Si un seul PJ est mentionné dans le message (ou via pronom lui/toi), c'est la cible.\n"
            "  - Si le MJ dit 'vous' / 'tout le monde', cible = 'tous'.\n"
            "  - Si aucun PJ identifiable, omets la directive.\n\n"
            "Exemples :\n"
            '  "Thorne prend 7 dégâts de force." → [{"action":"degats","cible":"Thorne","montant":7,"type_degat":"force"}]\n'
            '  "Le fantôme attaque Thorne et lui fait 7 dégâts de force." → [{"action":"degats","cible":"Thorne","montant":7,"type_degat":"force"}]\n'
            '  "Thorne enlève-toi 3 PV." → [{"action":"degats","cible":"Thorne","montant":3}]\n'
            '  "Lyra soigne Kaelen de 14 PV." → [{"action":"soin","cible":"Kaelen","montant":14}]\n'
            '  "Tout le monde fait un jet de Sagesse DC 13." → [{"action":"jet_sauvegarde","cible":"tous","caracteristique":"sagesse","dc":13}]\n'
            '  "Le dragon rugit." → []\n'
        )

        def _parse_mj_directives(mj_text: str) -> list:
            """
            Extrait les directives mécaniques d'un message MJ.
            Stratégie en deux passes :
              1. Regex rapide : couvre les cas simples sans appel LLM.
              2. LLM (OpenAI SDK) pour les cas ambigus/complexes.
            Retourne une liste de dicts (vide si aucune directive).
            """
            import json as _json

            # Pré-filtre : évite tout traitement pour les messages purement narratifs
            if not _DIRECTIVE_PREFILTER.search(mj_text):
                return []

            # ── Passe 1 : regex sans LLM ─────────────────────────────────────
            _PLAYER_SET = {"kaelen", "elara", "thorne", "lyra"}
            _NAME_CANON = {"kaelen":"Kaelen","elara":"Elara","thorne":"Thorne","lyra":"Lyra"}
            _CARAC_MAP  = {
                "force":"force","str":"force",
                "dextérité":"dextérité","dex":"dextérité",
                "constitution":"constitution","con":"constitution",
                "intelligence":"intelligence","int":"intelligence",
                "sagesse":"sagesse","wis":"sagesse","sag":"sagesse",
                "charisme":"charisme","cha":"charisme",
            }
            _SKILL_MAP = {
                "athlétisme":"force","acrobaties":"dextérité",
                "discrétion":"dextérité","escamotage":"dextérité",
                "arcanes":"intelligence","histoire":"intelligence",
                "investigation":"intelligence","nature":"intelligence","religion":"intelligence",
                "dressage":"sagesse","médecine":"sagesse","perception":"sagesse",
                "perspicacité":"sagesse","survie":"sagesse",
                "tromperie":"charisme","intimidation":"charisme",
                "persuasion":"charisme","représentation":"charisme",
            }
            _txt_low = mj_text.lower()
            _results_regex = []

            # Détection de la cible nommée dans le texte
            def _find_target(text):
                for pname in _PLAYER_SET:
                    if pname in text.lower():
                        return _NAME_CANON[pname]
                if any(w in text.lower() for w in ("vous","tout le monde","chacun","groupe")):
                    return "tous"
                return None

            # Jet de sauvegarde / jet de constitution / etc.
            _jet_re = _re.search(
                r'jet\s+(?:de\s+)?(constitution|force|dextérité|sagesse|intelligence|charisme|'
                r'con\b|str\b|dex\b|wis\b|int\b|cha\b|sag\b)'
                r'(?:[^D]*(?:DC|cd|dd)\s*(\d+))?',
                _txt_low, _re.IGNORECASE)
            if _jet_re:
                raw_carac = _jet_re.group(1).lower().strip()
                carac = _CARAC_MAP.get(raw_carac, raw_carac)
                dc_raw = _jet_re.group(2)
                dc = int(dc_raw) if dc_raw else None
                tgt = _find_target(mj_text)
                if tgt:
                    d = {"action": "jet_sauvegarde", "cible": tgt,
                         "caracteristique": carac, "de": "1d20", "bonus": 0}
                    if dc:
                        d["dc"] = dc
                    _results_regex.append(d)

            # Jet de compétence (ex: "jet d'Arcanes", "jet de Perception")
            if not _results_regex:
                _skill_pattern = (
                    r'jet\s+(?:de\s+|d["\'])?('
                    + '|'.join(_SKILL_MAP.keys()) + r')'
                    r'(?:[^D]*(?:DC|cd)\s*(\d+))?'
                )
                _skill_re = _re.search(_skill_pattern, _txt_low, _re.IGNORECASE)
                if _skill_re:
                    skill_name = _skill_re.group(1).lower().strip()
                    carac = _SKILL_MAP.get(skill_name, skill_name)
                    dc_raw = _skill_re.group(2)
                    dc = int(dc_raw) if dc_raw else None
                    tgt = _find_target(mj_text)
                    if tgt:
                        d = {"action": "jet_competence", "cible": tgt,
                             "caracteristique": carac, "de": "1d20", "bonus": 0}
                        if dc:
                            d["dc"] = dc
                        _results_regex.append(d)

            # Dégâts numériques explicites
            _dmg_re = _re.search(
                r'(\d+)\s*(?:d[eé]g[aâ]ts?|pv\b|points?\s*de\s*vie|hp\b)'
                r'(?:\s+(?:de\s+)?(\w+))?',
                _txt_low)
            if _dmg_re and not _results_regex:
                montant = int(_dmg_re.group(1))
                type_d  = _dmg_re.group(2) or ""
                tgt = _find_target(mj_text)
                if tgt:
                    _results_regex.append({"action": "degats", "cible": tgt,
                                           "montant": montant, "type_degat": type_d})

            # Soin explicite
            _soin_re = _re.search(
                r'(?:soign|récup[eè]r|regagn|rend)[^\d]*(\d+)\s*(?:pv\b|points?|hp\b)?',
                _txt_low)
            if _soin_re and not _results_regex:
                montant = int(_soin_re.group(1))
                tgt = _find_target(mj_text)
                if tgt:
                    _results_regex.append({"action": "soin", "cible": tgt,
                                           "montant": montant})

            if _results_regex:
                return _results_regex

            # ── Passe 2 : LLM (OpenAI SDK — pas de litellm) ──────────────────
            try:
                import httpx as _httpx
                import openai as _openai
                _ac  = get_agent_config("Thorne")   # modèle le plus léger
                _cfg0 = build_llm_config(
                    _ac.get("model") or _default_model, temperature=0
                )["config_list"][0]
                _http = _httpx.Client()
                _oa   = _openai.OpenAI(
                    api_key    = _cfg0["api_key"],
                    base_url   = str(_cfg0.get("base_url", "https://api.openai.com/v1")),
                    http_client= _http,
                )
                from llm_config import _SSL_LOCK as _psl
                with _psl:
                    _resp = _oa.chat.completions.create(
                        model    = _cfg0["model"],
                        messages = [
                            {"role": "system", "content": _PARSER_SYSTEM},
                            {"role": "user",   "content": mj_text},
                        ],
                        temperature = 0,
                        max_tokens  = 400,
                    )
                _http.close()
                _raw = _resp.choices[0].message.content.strip()
                _raw = _re.sub(r"^```(?:json)?\s*|\s*```$", "", _raw).strip()
                _parsed = _json.loads(_raw)
                if isinstance(_parsed, list):
                    return _parsed
            except Exception as _pe:
                print(f"[MJParser] Erreur LLM : {_pe}")
            return []

        def _roll_attack_only(char_name: str, regle: str, intention: str, cible: str,
                              mj_note: str) -> dict:
            """
            Phase 1 d'une attaque individuelle : lance UNIQUEMENT le 1d20.
            Retourne {atk_text, nat, total, is_crit, is_fumble, dn, df, db, atk_bonus}.
            """
            from state_manager import roll_dice
            stats    = _CHAR_MECHANICS.get(char_name, {})
            r_low    = regle.lower()
            i_low    = intention.lower()

            ranged = any(k in r_low or k in i_low
                         for k in ("distance","arc","arbalète","javelot","projectile"))
            _m_atk = _re.search(
                r'(?:corps[- ]à[- ]corps|mêlée|melee|distance|ranged|attaque)[^,]*?([+-]\d+)',
                r_low
            )
            if _m_atk:
                atk_bonus = int(_m_atk.group(1))
            else:
                m_bon = _re.search(r"bonus\s*([+-]\d+)", r_low)
                atk_bonus = (int(m_bon.group(1)) if m_bon
                             else stats.get("atk_ranged" if ranged else "atk_melee", +5))

            # Dés de dégâts (extraits de la règle pour usage ultérieur)
            def _all_dice_local(text):
                return [(int(m.group(1)), int(m.group(2)),
                         int(m.group(3).replace(" ","")) if m.group(3) else 0)
                        for m in _re.finditer(r"(\d+)d(\d+)(?:\s*([+-]\s*\d+))?",
                                              text, _re.IGNORECASE)]
            all_d = _all_dice_local(regle)
            dmg_d = all_d[0] if all_d else None
            if dmg_d is None:
                dn, df, db = stats.get("dmg_melee", (1, 8, 0))
            else:
                dn, df, db = dmg_d

            atk_res  = roll_dice(char_name, "1d20", atk_bonus)
            lines    = [f"⚔️ {char_name} attaque {cible}"]
            if mj_note:
                lines.append(f"Note MJ : {mj_note}")
            lines.append(f"  [jet d'attaque] {atk_res}")

            nat      = None
            total    = None
            is_crit  = False
            is_fumble= False

            m_nat = _re.search(r"Dés:\s*\[(\d+)", atk_res)
            m_tot = _re.search(r"Total\s*=\s*(\d+)", atk_res)
            if m_nat: nat   = int(m_nat.group(1))
            if m_tot: total = int(m_tot.group(1))

            if nat == 20:
                is_crit = True
                lines.append("  🎯 COUP CRITIQUE — les dégâts seront doublés !")
            elif nat == 1:
                is_fumble = True
                lines.append("  💀 ÉCHEC CRITIQUE (nat.1) — attaque automatiquement ratée.")
            elif total is not None:
                lines.append(f"  → Total {total} — MJ compare à la CA de {cible}")

            return {
                "atk_text":  "\n".join(lines),
                "nat":       nat,
                "total":     total,
                "is_crit":   is_crit,
                "is_fumble": is_fumble,
                "dn": dn, "df": df, "db": db,
            }

        def _roll_damage_only(char_name: str, cible: str, dn: int, df: int, db: int,
                              is_crit: bool, smite: dict | None, mj_note: str) -> str:
            """
            Phase 2 d'une attaque : lance les dés de dégâts (+ smite si présent).
            Retourne le feedback complet prêt à être injecté dans autogen.
            """
            from state_manager import roll_dice
            lines = [f"[RÉSULTAT SYSTÈME — DÉGÂTS CONFIRMÉS PAR MJ]",
                     f"⚔️ {char_name} → {cible}"]
            if mj_note:
                lines.append(f"Note MJ : {mj_note}")

            if is_crit:
                dmg_res = roll_dice(char_name, f"{dn*2}d{df}", db)
                lines.append(f"  [dégâts CRITIQUE] {dmg_res}")
            else:
                dmg_res = roll_dice(char_name, f"{dn}d{df}", db)
                lines.append(f"  [dégâts] {dmg_res}")

            if smite:
                sm_res = roll_dice(char_name, smite["dice"], 0)
                lines.append(
                    f"  [✨ {smite['label']}] {sm_res}  "
                    f"(dégâts {smite['type']} supplémentaires)"
                )

            if char_name == "Thorne":
                sn, sf, sb = _CHAR_MECHANICS.get("Thorne", {}).get("dmg_sneak", (8,6,0))
                snk_res = roll_dice("Thorne", f"{sn}d{sf}", sb)
                lines.append(f"  [sournoise] {snk_res}  ← si avantage/allié adjacent")

            lines.append("")
            lines.append("[INSTRUCTION NARRATIVE]")
            lines.append(
                f"Le système vient d exécuter les dégâts. "
                f"Narre en 1-2 phrases vivantes l impact sur {cible}. "
                f"Ne mentionne PAS les chiffres."
            )
            return "\n".join(lines)

        def _get_prepared_spell_names(char_name: str) -> list[str]:
            """Retourne la liste des noms de sorts préparés pour l'affichage
            dans les messages d'erreur SORT IMPOSSIBLE.
            Lit spells_prepared depuis campaign_state — aucun sort hardcodé."""
            try:
                state = load_state()
                return list(state.get("characters", {}).get(char_name, {})
                            .get("spells_prepared", []))
            except Exception:
                return []

        def _extract_spell_name_llm(intention: str, char_name: str) -> str:
            """
            Utilise un LLM léger pour identifier le nom canonique du sort lancé.

            Stratégie LLM + vérification codée :
              - Le LLM reçoit le texte brut de l'intention ET la liste des sorts préparés.
              - Il retourne UNIQUEMENT le nom exact du sort tel qu'il apparaît dans la liste,
                ou "AUCUN" s'il ne reconnaît aucun sort.
              - La vérification reste entièrement codée dans _is_spell_prepared.

            Avantages vs regex :
              - Gère naturellement les rituels ("en tant que rituel"), abréviations,
                variantes de langue (FR/EN), fautes de frappe légères.
              - Zéro maintenance : pas de table de traduction à mettre à jour.
            """
            import re as _re
            prepared = _get_prepared_spell_names(char_name)
            if not prepared:
                return intention.strip()[:50]

            spell_list = ", ".join(prepared)
            prompt = (
                f"Tu es un assistant de règles D&D 5e. "
                f"Voici la liste des sorts préparés de {char_name} : {spell_list}.\n\n"
                f"Dans ce texte d'action : \"{intention}\"\n\n"
                f"Quel sort de la liste est lancé ? "
                f"Réponds UNIQUEMENT avec le nom exact du sort tel qu'il apparaît dans la liste. "
                f"Si aucun sort de la liste n'est mentionné, réponds : AUCUN. "
                f"Aucune explication, aucune ponctuation supplémentaire."
            )
            try:
                import autogen as _ag
                from llm_config import build_llm_config, _default_model
                from app_config import get_chronicler_config
                _chron = get_chronicler_config()
                _model = _chron.get("model", _default_model)
                _cfg   = build_llm_config(_model, temperature=0.0)
                client = _ag.OpenAIWrapper(config_list=_cfg["config_list"])
                response = client.create(messages=[{"role": "user", "content": prompt}])
                raw = (response.choices[0].message.content or "").strip()
                # Nettoyer fences markdown éventuelles
                raw = _re.sub(r"^```[a-z]*\s*", "", raw)
                raw = _re.sub(r"\s*```$", "", raw.strip()).strip()
                if raw.upper() == "AUCUN" or not raw:
                    return ""
                return raw
            except Exception as e:
                print(f"[SpellExtract] Erreur LLM : {e}")
                # Fallback : retourner l'intention brute tronquée
                return intention.strip()[:50]

        def _is_spell_prepared(char_name: str, spell_name: str) -> bool:
            """Retourne True si spell_name correspond à un sort préparé du personnage.

            Stratégie de correspondance (par ordre de priorité) :
              1. Égalité exacte après normalisation Unicode + lowercase.
              2. Le nom du JSON est contenu dans la saisie (substring).
              3. La saisie est contenue dans le nom du JSON.

            Note : la traduction FR→EN est désormais gérée en amont par
            _extract_spell_name_llm, qui retourne directement le nom canonique
            de la liste. Cette fonction reste le gardien déterministe final.

            Les cantrips (level=0) sont TOUJOURS autorisés.
            Si le personnage n'a pas de liste de sorts définie → non restrictif (True).
            """
            import unicodedata as _ud

            def _norm(s: str) -> str:
                """Lowercase + supprime les diacritiques + réduit les espaces."""
                nfkd = _ud.normalize("NFKD", s)
                ascii_str = "".join(c for c in nfkd if not _ud.combining(c))
                return " ".join(ascii_str.lower().split())

            try:
                state = load_state()
                spell_names = (
                    state.get("characters", {})
                    .get(char_name, {})
                    .get("spells_prepared", None)
                )
                if spell_names is None:
                    return True   # champ absent → pas de restriction

                needle = _norm(spell_name.strip())
                if not needle:
                    return True

                # Essayer de récupérer le niveau via spell_data (cantrips toujours OK)
                try:
                    from spell_data import get_spell as _gs, load_spells as _ls
                    _ls()
                except Exception:
                    _gs = lambda n: None

                for name in spell_names:
                    sp_name_n = _norm(name)
                    if not sp_name_n:
                        continue
                    match = (
                        sp_name_n == needle
                        or (len(sp_name_n) >= 5 and sp_name_n in needle)
                        or (len(needle) >= 5 and needle in sp_name_n)
                    )
                    if match:
                        sp_data = _gs(name)
                        if sp_data and int(sp_data.get("level", 1)) == 0:
                            return True
                        return True

                return False
            except Exception:
                return True   # en cas d'erreur, ne pas bloquer

        def _execute_action_mechanics(char_name, intention, regle, cible, mj_note, single_attack=False, type_label=""):
            """
            Exécute directement les mécaniques D&D 5e en Python et retourne
            un résumé [RÉSULTAT SYSTÈME] à injecter dans le contexte de l agent.
            """
            from state_manager import roll_dice, use_spell_slot, update_hp

            stats  = _CHAR_MECHANICS.get(char_name, {})
            r_low  = regle.lower()
            i_low  = intention.lower()
            t_low  = (type_label or "").lower()
            results = []
            narrative_hint = ""

            # Court-circuit : Type: Mouvement déclaré explicitement
            # → ignorer ATK_KW/SPELL_KW qui pourraient matcher le texte libre
            # On conserve r_low_orig pour la détection de direction (ex: "6 cases vers le sud")
            r_low_orig = r_low
            if "mouvement" in t_low:
                r_low = "mouvement "  # neutralise is_atk/is_spell, pas la direction

            if mj_note:
                results.append(f"Note MJ : {mj_note}")

            # Helpers
            def _all_dice(text):
                return [(int(m.group(1)), int(m.group(2)),
                         int(m.group(3).replace(" ","")) if m.group(3) else 0)
                        for m in _re.finditer(r"(\d+)d(\d+)(?:\s*([+-]\s*\d+))?",
                                              text, _re.IGNORECASE)]

            def _extract_dc(text):
                m = _re.search(r"\bDC\s*(\d+)", text, _re.IGNORECASE)
                return int(m.group(1)) if m else None

            def _extract_level(text):
                for pat in (r"niv(?:eau)?\.?\s*(\d+)", r"niveau\s*(\d+)", r"\bniv(\d+)"):
                    m = _re.search(pat, text, _re.IGNORECASE)
                    if m: return int(m.group(1))
                return None

            def _skill_bonus(text):
                t = text.lower()
                for table in (stats.get("skills",{}), stats.get("saves",{})):
                    for k, v in table.items():
                        if k in t:
                            return v
                return None

            def _total(res_str):
                m = _re.search(r"Total\s*=\s*(\d+)", res_str)
                return int(m.group(1)) if m else None

            def _first_roll(res_str):
                """Retourne le premier dé individuel (nat 20 critique)."""
                m = _re.search(r"Dés:\s*\[(\d+)", res_str)
                return int(m.group(1)) if m else None

            # Détection du type
            SPELL_KW = ("sort","magie","incant","boule","projectile","éclair","feu",
                        "soin","soigne","heal","cure","guéri","restaure","parole",
                        "contresort","dissipation","bannissement","désintégration",
                        "lumière","ténèbres","sacré","nécro","évocation","abjuration")
            ATK_KW   = ("attaque","frappe","coup","tir","tire","charge","poignarde",
                        "tranche","abat","corps-à-corps","distance","assaut","offensive")
            SKILL_KW = ("jet","check","compétence","sauvegarde","save","arcanes",
                        "perception","investigation","discrétion","athlétisme",
                        "acrobaties","médecine","histoire","nature","religion",
                        "perspicacité","intimidation","tromperie","persuasion",
                        "dressage","survie","escamotage","force","dextérité",
                        "constitution","intelligence","sagesse","charisme")

            # Mots-clés smite — ces "sorts" augmentent une attaque, PAS des sorts indépendants.
            # Si des mots-clés smite + mots-clés attaque coexistent → traiter comme is_atk.
            _SMITE_BOOST_KW = ("divine smite", "smite divin", "châtiment divin", "chatiment divin",
                               "wrathful smite", "courroux divin", "thunderous smite",
                               "frappe tonnerre", "branding smite", "frappe lumière")
            _is_smite_boost = any(k in r_low or k in i_low for k in _SMITE_BOOST_KW)

            is_spell = any(k in r_low or k in i_low for k in SPELL_KW) and not _is_smite_boost
            is_atk   = (any(k in r_low or k in i_low for k in ATK_KW) or _is_smite_boost) and not is_spell
            is_skill = any(k in r_low or k in i_low for k in SKILL_KW) and not is_atk and not is_spell

            # ── ATTAQUE ──────────────────────────────────────────────────────
            if is_atk:
                ranged = any(k in r_low or k in i_low
                             for k in ("distance","arc","arbalète","javelot","projectile"))
                m_bon = _re.search(r"bonus\s*([+-]\d+)", r_low)
                atk_bonus = (int(m_bon.group(1)) if m_bon
                             else stats.get("atk_ranged" if ranged else "atk_melee", +5))

                all_d  = _all_dice(regle)
                if single_attack:
                    # Sous-action déjà splitée : pas de 1d20 dans la règle,
                    # all_d[0] est directement les dés de dégâts.
                    dmg_d = all_d[0] if all_d else None
                    # Extraire le bonus d'attaque depuis la règle
                    # ex. "corps-à-corps +11, 2d6+8" → atk_bonus = 11
                    _m_atk = _re.search(
                        r'(?:corps[- ]à[- ]corps|mêlée|melee|distance|ranged|attaque)[^,]*?([+-]\d+)',
                        r_low
                    )
                    if _m_atk:
                        atk_bonus = int(_m_atk.group(1))
                else:
                    # Format classique : l'agent peut écrire "1d20+11, 2d6+8"
                    # all_d[0] serait le 1d20, all_d[1] les dégâts.
                    dmg_d = all_d[1] if len(all_d) >= 2 else None
                if dmg_d is None:
                    dn, df, db = stats.get("dmg_melee", (1, 8, 0))
                else:
                    dn, df, db = dmg_d

                # single_attack=True : sous-action déjà splitée → exactement 1 attaque
                n_atk  = 1 if single_attack else stats.get("n_attacks", 1)
                dc_val = _extract_dc(regle)

                results.append(f"⚔️ {char_name} — {intention} → {cible}")
                for i in range(1, n_atk + 1):
                    atk_res = roll_dice(char_name, "1d20", atk_bonus)
                    lbl = f"attaque {i}/{n_atk}" if n_atk > 1 else "attaque"
                    results.append(f"  [{lbl}] {atk_res}")

                    nat = _first_roll(atk_res)
                    tot = _total(atk_res)

                    if nat == 20:
                        crit_res = roll_dice(char_name, f"{dn*2}d{df}", db)
                        results.append(f"  🎯 CRITIQUE ! {crit_res}")
                        continue
                    if nat == 1:
                        results.append(f"  💀 ÉCHEC CRITIQUE (nat.1) — attaque ratée.")
                        continue

                    if dc_val and tot is not None:
                        hit = tot >= dc_val
                        results.append(f"  → {'TOUCHÉ ✅' if hit else 'RATÉ ❌'} (CA {dc_val})")
                    elif tot is not None:
                        results.append(f"  → Total {tot} — MJ compare à la CA de {cible}")

                    dmg_res = roll_dice(char_name, f"{dn}d{df}", db)
                    results.append(f"  [dégâts] {dmg_res}")

                # ── Smite en attente → appliqué sur la première attaque confirmée ──────
                if single_attack and char_name in _pending_smite:
                    _sm = _pending_smite.pop(char_name)
                    sm_res = roll_dice(char_name, _sm["dice"], 0)
                    results.append(
                        f"  [✨ {_sm['label']}] {sm_res}  "
                        f"(dégâts {_sm['type']} supplémentaires)"
                    )

                # Attaque sournoise Thorne
                if char_name == "Thorne":
                    sn, sf, sb = stats.get("dmg_sneak", (8, 6, 0))
                    snk_res = roll_dice("Thorne", f"{sn}d{sf}", sb)
                    results.append(f"  [sournoise] {snk_res}  ← si avantage/allié adjacent")

                narrative_hint = (
                    f"Le système vient d exécuter les jets d attaque. "
                    f"Narre en 1-2 phrases vivantes comment {char_name} attaque {cible}. "
                    f"Ne mentionne PAS les chiffres — décris l action, la violence, le mouvement."
                )

            # ── COMPÉTENCE / SAUVEGARDE ──────────────────────────────────────
            elif is_skill:
                bonus  = _skill_bonus(regle + " " + intention) or 0
                m_bon  = _re.search(r"([+-]\d+)", regle)
                if bonus == 0 and m_bon:
                    bonus = int(m_bon.group(1))
                dc_val = _extract_dc(regle)

                res = roll_dice(char_name, "1d20", bonus)
                results.append(f"🎲 {char_name} — {regle}")
                results.append(f"  {res}")
                tot = _total(res)
                if dc_val and tot is not None:
                    outcome = "RÉUSSITE ✅" if tot >= dc_val else "ÉCHEC ❌"
                    results.append(f"  → DC {dc_val} : {outcome}")
                else:
                    results.append(f"  → MJ annoncera la DC et l effet.")

                narrative_hint = (
                    f"Le système a lancé le jet. "
                    f"Narre en 1 phrase la tentative de {char_name} : {intention}. "
                    f"Ne répète pas les chiffres. Attends que le MJ décrive l effet si DC inconnue."
                )

            # ── SORT ─────────────────────────────────────────────────────────
            elif is_spell:
                lvl       = _extract_level(regle) or _extract_level(intention)
                is_cantrip = lvl is None or lvl == 0
                is_heal   = any(k in r_low or k in i_low
                                for k in ("soin","soigne","heal","cure","guéri",
                                          "restaure","parole curative"))
                is_atk_roll = any(k in r_low for k in ("jet d attaque de sort",
                                                         "attaque de sort","rayon"))
                dc_val    = _extract_dc(regle)

                # ── Vérification liste de sorts préparés ─────────────────────
                # Le LLM identifie le nom canonique du sort depuis l'intention brute,
                # gérant naturellement les rituels, variantes FR/EN, abréviations, etc.
                if not is_cantrip:
                    _spell_name_candidate = _extract_spell_name_llm(intention, char_name)

                    if _spell_name_candidate and not _is_spell_prepared(char_name, _spell_name_candidate):
                        _avail = _get_prepared_spell_names(char_name)
                        _avail_str = ", ".join(_avail) if _avail else "aucun sort préparé trouvé"
                        _no_prep_msg = (
                            f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE]\n"
                            f"« {_spell_name_candidate} » n'est pas dans la liste de sorts "
                            f"préparés de {char_name}. Ce sort ne peut pas être lancé aujourd'hui.\n\n"
                            f"[SORTS AUTORISÉS POUR {char_name.upper()}]\n"
                            f"{_avail_str}\n\n"
                            f"[INSTRUCTION]\n"
                            f"Choisis UNIQUEMENT parmi les sorts listés ci-dessus. "
                            f"Déclare une nouvelle action avec [ACTION]."
                        )
                        return (
                            "[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE]\n"
                            + _no_prep_msg
                        )

                results.append(f"✨ {char_name} — {regle} → {cible}")

                # ── Smite spells → détection EN PREMIER, AVANT toute consommation de slot ──
                # CRITIQUE : le slot ne doit PAS être consommé ici.
                # Il sera consommé uniquement quand l'attaque touche (Phase 2/3).
                # Détecter AVANT use_spell_slot() pour éviter le bug "slot mangé sans attaque".
                _SMITE_TABLE = {
                    "wrathful smite":   ("1d6",  "psychique",  "Wrathful Smite"),
                    "courroux divin":   ("1d6",  "psychique",  "Wrathful Smite"),
                    "thunderous smite": ("2d6",  "tonnerre",   "Thunderous Smite"),
                    "frappe tonnerre":  ("2d6",  "tonnerre",   "Thunderous Smite"),
                    "branding smite":   ("2d6",  "radiant",    "Branding Smite"),
                    "frappe lumière":   ("2d6",  "radiant",    "Branding Smite"),
                    "divine smite":     (None,   "radiant",    "Divine Smite"),
                    "smite divin":      (None,   "radiant",    "Divine Smite"),
                    "châtiment divin":  (None,   "radiant",    "Divine Smite"),
                    "chatiment divin":  (None,   "radiant",    "Divine Smite"),
                }
                _smite_match = next(
                    ((dice, typ, lbl)
                     for kw, (dice, typ, lbl) in _SMITE_TABLE.items()
                     if kw in r_low or kw in i_low),
                    None
                )
                if _smite_match:
                    _sm_dice, _sm_type, _sm_label = _smite_match
                    _sm_lvl = lvl or 1
                    if _sm_dice is None:
                        _sm_dice = f"{_sm_lvl + 1}d8"
                    # Stocker le niveau du slot pour le consommer uniquement sur toucher
                    _pending_smite[char_name] = {
                        "dice":       _sm_dice,
                        "type":       _sm_type,
                        "label":      _sm_label,
                        "slot_level": _sm_lvl,   # consommé en Phase 2 si attaque touche
                    }
                    results.append(
                        f"  [✨ {_sm_label}] En attente — {_sm_dice} dégâts {_sm_type} "
                        f"s'ajouteront sur la prochaine attaque de {char_name} SI elle touche. "
                        f"(slot niv.{_sm_lvl} sera consommé uniquement sur toucher)"
                    )
                    narrative_hint = (
                        f"Le sort {_sm_label} est prêt. "
                        f"Narre en 1 phrase : la lueur sacrée qui enveloppe l'arme de {char_name}, "
                        f"prête à se décharger sur le prochain coup."
                    )
                    return (
                        "[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ]\n"
                        + "\n".join(results)
                        + "\n\n[INSTRUCTION NARRATIVE]\n"
                        + narrative_hint
                    )

                # Slot (uniquement pour les sorts NON-smite)
                if not is_cantrip and lvl:
                    slot_res = use_spell_slot(char_name, str(lvl))
                    results.append(f"  [slot niv.{lvl}] {slot_res}")
                    if "ÉCHEC" in slot_res:
                        narrative_hint = (
                            f"{char_name} n a plus de slot de niveau {lvl}. "
                            f"Narre en 1 phrase qu il réalise qu il est à court d énergie magique."
                        )
                        return ("[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ]\n"
                                + "\n".join(results)
                                + "\n\n[INSTRUCTION NARRATIVE]\n" + narrative_hint)

                # Jet d'attaque de sort → pré-roller les dégâts et retourner
                # avec un header distinct pour que le calling code utilise mode="attack"
                if is_atk_roll:
                    atk_spell = stats.get("atk_spell", +5)
                    atk_res = roll_dice(char_name, "1d20", atk_spell)
                    results.append(f"  [attaque sort] {atk_res}")

                    # Table de dégâts de cantrips par défaut (si l_agent n'a pas précisé)
                    _CANTRIP_DMG = {
                        "rayon de givre": ("1d8",  0, "froid"),
                        "ray of frost":   ("1d8",  0, "froid"),
                        "flamme sacrée":  ("2d8",  0, "radiant"),
                        "sacred flame":   ("2d8",  0, "radiant"),
                        "bourrasque":     ("1d8",  0, "tonnerre"),
                        "dard du feu":    ("1d10", 0, "feu"),
                        "fire bolt":      ("1d10", 0, "feu"),
                        "contact glacial":("1d8",  0, "nécrotique"),
                        "chill touch":    ("1d8",  0, "nécrotique"),
                        "éclair de sorcière": ("1d10", 0, "foudre"),
                        "eldritch blast": ("1d10", 0, "force"),
                        "trait de feu":   ("1d10", 0, "feu"),
                        "rayon empoisonné": ("1d4", 0, "poison"),
                        "poison spray":   ("1d12", 0, "poison"),
                    }
                    all_d = _all_dice(regle)
                    if all_d:
                        _dn, _df, _db = all_d[0]
                        _dmg_type = "magique"
                    else:
                        # Chercher dans la table de cantrips
                        _cantrip_key = next(
                            (k for k in _CANTRIP_DMG
                             if k in r_low or k in i_low),
                            None
                        )
                        if _cantrip_key:
                            _dice_str, _db, _dmg_type = _CANTRIP_DMG[_cantrip_key]
                            _dm = _re.match(r"(\d+)d(\d+)", _dice_str)
                            _dn, _df = (int(_dm.group(1)), int(_dm.group(2))) if _dm else (1, 8)
                        else:
                            _dn, _df, _db, _dmg_type = 1, 8, 0, "magique"

                    dmg_res = roll_dice(char_name, f"{_dn}d{_df}", _db)
                    results.append(f"  [dégâts si touche] {dmg_res}  ({_dmg_type})")
                    results.append(f"  → MJ : confirmer Touché ou Raté")

                    narrative_hint = (
                        f"Le système a résolu l'attaque de sort. "
                        f"Si touché : narre en 1-2 phrases l'impact du sort sur {cible}. "
                        f"Si raté : narre l'esquive ou la résistance. Ne mentionne pas les chiffres."
                    )
                    return (
                        "[RÉSULTAT SYSTÈME — ATTAQUE DE SORT]\n"
                        + "\n".join(results)
                        + "\n\n[INSTRUCTION NARRATIVE]\n"
                        + narrative_hint
                    )

                # ── Projectile Magique (Magic Missile) — touche automatiquement ──
                _MM_KW = ("projectile magique", "magic missile", "projectiles magiques")
                _is_magic_missile = any(k in r_low or k in i_low for k in _MM_KW)
                if _is_magic_missile:
                    _mm_lvl    = lvl if lvl and lvl >= 1 else 1
                    _mm_darts  = 3 + max(0, _mm_lvl - 1)   # 3 au niv.1, +1/niveau sup.
                    results.append(
                        f"  [Projectile Magique — niv.{_mm_lvl}] "
                        f"{_mm_darts} projectile(s) — touche(nt) automatiquement"
                    )
                    _mm_totals = []
                    for _i in range(1, _mm_darts + 1):
                        _dart_res = roll_dice(char_name, "1d4", 1)   # 1d4+1 force
                        _dart_m = _re.search(r"Total\s*=\s*(\d+)", _dart_res)
                        _dart_tot = int(_dart_m.group(1)) if _dart_m else "?"
                        results.append(f"  [projectile {_i}] {_dart_res}  (dégâts de force)")
                        _mm_totals.append(_dart_tot)
                    _mm_grand_total = sum(t for t in _mm_totals if isinstance(t, int))
                    _cible_note = (
                        "répartis librement entre les cibles"
                        if ("," in cible or " et " in cible)
                        else cible
                    )
                    results.append(
                        f"  → Total dégâts de force : {_mm_grand_total} ({_cible_note})"
                    )
                    narrative_hint = (
                        f"Le système a lancé les {_mm_darts} projectile(s) de force. "
                        f"Narre en 1-2 phrases comment les flèches de lumière dorée fusent "
                        f"inévitablement vers {cible} et l'impact (total {_mm_grand_total} dégâts de force). "
                        f"Ne mentionne pas les chiffres individuels des dés."
                    )
                    return (
                        "[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ]\n"
                        + "\n".join(results)
                        + "\n\n[INSTRUCTION NARRATIVE]\n"
                        + narrative_hint
                    )

                # Dés de dégâts / soin
                all_d = _all_dice(regle)
                if all_d:
                    dn, df, db = all_d[0]
                    verb = "soin" if is_heal else "dégâts"
                    res  = roll_dice(char_name, f"{dn}d{df}", db)
                    results.append(f"  [{verb}] {res}")
                    if is_heal:
                        tot = _total(res)
                        heal_amt = tot or 0
                        targets = [n for n in PLAYER_NAMES if n.lower() in cible.lower()]
                        if not targets:
                            targets = [char_name]
                        for tgt in targets:
                            hp_res = update_hp(tgt, heal_amt)
                            results.append(f"  [PV] {hp_res}")
                        # Sync tracker après soins
                        try:
                            if _app._combat_tracker is not None:
                                _app.root.after(0, _app._combat_tracker.sync_pc_hp_from_state)
                        except Exception:
                            pass

                if dc_val and not is_atk_roll:
                    results.append(
                        f"  → Cibles : jet de sauvegarde DC {dc_val}. "
                        f"Le MJ gère la réussite/échec."
                    )

                narrative_hint = (
                    f"Le système a exécuté la mécanique du sort. "
                    f"Narre en 1-2 phrases comment {char_name} incante et l effet visible sur {cible}. "
                    f"Ne mentionne pas les chiffres bruts."
                )

            # ── MOUVEMENT ────────────────────────────────────────────────────
            else:
                MOVE_KW = ("mouvement", "déplace", "deplace", "repositionne",
                           "avance", "recule", "cours", "marche", "approche",
                           "éloigne", "eloigne", "dash", "sprint", "charge",
                           "vers le nord", "vers le sud", "vers l est", "vers l ouest",
                           "vers le", "cases vers", "metres vers", "mètres vers",
                           "se deplace", "se déplace")
                is_move = any(k in r_low or k in i_low for k in MOVE_KW)

                if is_move:
                    # ── Récupérer la position courante du token ───────────────
                    _cur_col, _cur_row = 0, 0
                    try:
                        _map_data = _app._win_state.get("combat_map_data", {})
                        for _tok in _map_data.get("tokens", []):
                            if _tok.get("name") == char_name:
                                _cur_col = int(round(_tok.get("col", 0)))
                                _cur_row = int(round(_tok.get("row", 0)))
                                break
                    except Exception:
                        pass

                    _combined_mv = r_low + " " + i_low + " " + cible.lower()

                    # ── 1. Coordonnées absolues : "col X, lig Y" ──────────────
                    _new_col, _new_row = _cur_col, _cur_row
                    _m_abs = _re.search(
                        r'col(?:onne)?\s*(\d+)[,\s]+(?:lig(?:ne)?|rang(?:ée?)?)\s*(\d+)',
                        _combined_mv, _re.IGNORECASE
                    )
                    if _m_abs:
                        _new_col = int(_m_abs.group(1)) - 1  # 1-based → 0-based
                        _new_row = int(_m_abs.group(2)) - 1
                    else:
                        # ── 2. Distance + direction ───────────────────────────
                        _m_cases = _re.search(r'(\d+)\s*cases?', _combined_mv)
                        _m_met   = _re.search(r'(\d+(?:[.,]\d+)?)\s*m(?:ètres?|etres?|\.|\b)', _combined_mv)
                        if _m_cases:
                            _dist = int(_m_cases.group(1))
                        elif _m_met:
                            _dist = max(1, round(float(_m_met.group(1).replace(",", ".")) / 1.5))
                        else:
                            _dist = 6  # 30 ft par défaut

                        # Directions cardinales en deux groupes :
                        #   - Composées longues (nord-est...) : correspondance exacte avec tiret
                        #   - Cardinals simples (nord/sud/est/ouest) : word boundary pour éviter
                        #     les faux positifs sur "se déplacer", "ne pas", "notre", etc.
                        #   - Abréviations NE/NO/SE/SO : retirées — trop ambiguës en français
                        _DIR_EXACT = [
                            ("nord-est",   ( 1, -1)), ("nord-ouest", (-1, -1)),
                            ("sud-est",    ( 1,  1)), ("sud-ouest",  (-1,  1)),
                            ("north-east", ( 1, -1)), ("north-west", (-1, -1)),
                            ("south-east", ( 1,  1)), ("south-west", (-1,  1)),
                        ]
                        _DIR_WORD = [
                            ("nord",  ( 0, -1)), ("north", ( 0, -1)),
                            ("sud",   ( 0,  1)), ("south", ( 0,  1)),
                            ("est",   ( 1,  0)), ("east",  ( 1,  0)),
                            ("ouest", (-1,  0)), ("west",  (-1,  0)),
                        ]
                        _dcol, _drow = 0, 0
                        # Chercher la direction UNIQUEMENT dans l'intention et la cible
                        # r_low_orig préserve le champ Règle 5e original
                        # (ex: "6 cases vers le sud") avant le court-circuit mouvement
                        _dir_search = i_low + " " + cible.lower() + " " + r_low_orig
                        # 1. D'abord les composées exactes (tiret obligatoire)
                        for _kd, (_dc, _dr) in _DIR_EXACT:
                            if _kd in _dir_search:
                                _dcol, _drow = _dc, _dr
                                break
                        # 2. Ensuite les cardinaux simples avec word boundary
                        if _dcol == 0 and _drow == 0:
                            for _kd, (_dc, _dr) in _DIR_WORD:
                                if _re.search(r'\b' + _kd + r'\b', _dir_search):
                                    _dcol, _drow = _dc, _dr
                                    break

                        # ── 3. Vers un autre token ────────────────────────────
                        if _dcol == 0 and _drow == 0:
                            try:
                                _map_tokens = _app._win_state.get("combat_map_data", {}).get("tokens", [])
                                for _other in _map_tokens:
                                    _oname = _other.get("name", "").lower()
                                    if _oname and _oname in _combined_mv and _other.get("name") != char_name:
                                        _oc = int(round(_other.get("col", 0)))
                                        _or = int(round(_other.get("row", 0)))
                                        _raw_dc = _oc - _cur_col
                                        _raw_dr = _or - _cur_row
                                        _mag = max(abs(_raw_dc), abs(_raw_dr)) or 1
                                        _dcol = round(_raw_dc / _mag)
                                        _drow = round(_raw_dr / _mag)
                                        break
                            except Exception:
                                pass

                        # ── 4. Destination non résoluble → refus propre ───────
                        # Si ni coordonnées, ni direction, ni token trouvé :
                        # ne pas deviner, demander au MJ de préciser.
                        if _dcol == 0 and _drow == 0:
                            narrative_hint = (
                                f"Destination non déterminée automatiquement pour {char_name}. "
                                f"MJ : précise la destination avec 'Col X, Lig Y' ou une direction cardinale "
                                f"(nord/sud/est/ouest) pour déplacer le token manuellement."
                            )
                            results.append(f"🏃 {char_name} — {intention}")
                            results.append(f"  Position actuelle : Col {_cur_col+1}, Lig {_cur_row+1}")
                            results.append(f"  ⚠ Destination '{cible}' non résoluble — token non déplacé.")
                            results.append(f"  → Précise : Col X, Lig Y  OU  direction (nord/sud/est/ouest)")
                            return (
                                "[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ]\n"
                                + "\n".join(results)
                                + "\n\n[INSTRUCTION NARRATIVE]\n"
                                + narrative_hint
                            )

                        _new_col = _cur_col + _dcol * _dist
                        _new_row = _cur_row + _drow * _dist

                    # Clamp à la grille
                    try:
                        _grid_cols = _app._win_state.get("combat_map_data", {}).get("cols", 30)
                        _grid_rows = _app._win_state.get("combat_map_data", {}).get("rows", 20)
                    except Exception:
                        _grid_cols, _grid_rows = 30, 20
                    _new_col = max(0, min(_grid_cols - 1, _new_col))
                    _new_row = max(0, min(_grid_rows - 1, _new_row))

                    _dist_actual = max(abs(_new_col - _cur_col), abs(_new_row - _cur_row))
                    _dist_m = _dist_actual * 1.5
                    results.append(f"🏃 {char_name} — {intention}")
                    results.append(f"  Position actuelle : Col {_cur_col+1}, Lig {_cur_row+1}")
                    results.append(f"  Destination       : Col {_new_col+1}, Lig {_new_row+1}")
                    results.append(f"  Distance          : {_dist_actual} cases ({_dist_m:.1f} m)")
                    results.append(f"[MOVE_TOKEN:{char_name}:{_new_col}:{_new_row}]")
                    narrative_hint = (
                        f"Le système a calculé le déplacement. "
                        f"Narre en 1 phrase le mouvement de {char_name} : {intention}. "
                        f"Décris la façon dont il se déplace, son attitude, pas les coordonnées."
                    )
                else:
                    # ── Autre action non couverte ─────────────────────────────
                    results.append(f"⚙️ {char_name} — {intention}")
                    results.append(f"  Mécanique : {regle} | Cible : {cible}")
                    narrative_hint = (
                        f"Narre en 1-2 phrases l action de {char_name} : {intention}. "
                        f"Si des dés sont encore nécessaires, pose un nouveau [ACTION]."
                    )

            return (
                "[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ]\n"
                + "\n".join(results)
                + "\n\n[INSTRUCTION NARRATIVE]\n"
                + narrative_hint
            )

        # Dict partagé par patched_receive pour la détection de copie inter-agents
        _last_player_messages: dict[str, str] = {}

        def patched_receive(self_mgr, message, sender, request_reply=None, silent=False):

            def _strip_stars(text: str) -> str:
                """Supprime tous les astérisques des messages des agents joueurs."""
                return text.replace("*", "") if text else text

            def _tts_clean(text: str) -> str:
                """
                Prépare le texte pour la lecture TTS.
                Les moteurs TTS (Piper, edge-tts) s'arrêtent au premier \n —
                on remplace chaque retour à la ligne par une virgule+espace
                afin que la lecture soit continue et naturelle.
                Les doubles retours à la ligne (paragraphes) deviennent une pause
                plus marquée (point + espace).
                """
                if not text:
                    return text
                import re as _re_tts
                text = text.replace("\n\n", ". ")
                text = text.replace("\n", ", ")
                text = _re_tts.sub(r',\s*\.', '.', text)
                text = _re_tts.sub(r'\.\s*,', '.', text)
                text = _re_tts.sub(r',\s*,', ',', text)
                text = _re_tts.sub(r'\s{2,}', ' ', text)
                return text.strip()

            def _split_sentences(text: str) -> list:
                """
                Découpe un texte en phrases courtes pour le TTS.
                Chaque phrase est jouée dès qu'elle est synthétisée → latence
                perçue réduite sur les longues répliques.

                Règles :
                  - Coupe après . ! ? ; suivi d'une espace + majuscule/guillemet
                  - Ignore les abréviations courantes (M. Dr. etc.)
                  - Fusionne les fragments < 18 chars avec le suivant
                  - Retourne au moins un élément (le texte entier si non découpable)
                """
                import re as _re_s
                if not text or len(text) < 40:
                    return [text] if text else []

                # Protéger les abréviations connues pour ne pas les couper
                _ABBREVS = r"(?:M|Mme|Dr|Prof|St|Ste|Mr|Jr|Sr|vol|p|pp|art|no|No|fig|cf|vs|env|hab|av|apr|J\.-C|etc)\."
                # Marqueur temporaire
                protected = _re_s.sub(_ABBREVS, lambda m: m.group().replace(".", "\x00"), text)

                # Couper sur . ! ? ; suivi d'espace + majuscule ou guillemet ouvrant
                parts = _re_s.split(r'(?<=[.!?;])\s+(?=[A-ZÀÂÄÉÈÊËÎÏÔÙÛÜÇ"«\u2019])', protected)

                # Restaurer les points protégés
                parts = [p.replace("\x00", ".").strip() for p in parts if p.strip()]

                # Fusionner les fragments trop courts avec le suivant
                merged = []
                buf = ""
                for p in parts:
                    buf = (buf + " " + p).strip() if buf else p
                    if len(buf) >= 18:
                        merged.append(buf)
                        buf = ""
                if buf:
                    if merged:
                        merged[-1] = (merged[-1] + " " + buf).strip()
                    else:
                        merged.append(buf)

                return merged if merged else [text]

            def _enqueue_tts(text: str, char_name: str):
                """Découpe text en phrases et enfile chaque phrase séparément
                dans audio_queue. Cela permet à Piper/edge-tts de commencer
                la synthèse de la phrase 1 pendant que la phrase 2 est en attente,
                réduisant la latence perçue sur les longues répliques."""
                cleaned = _tts_clean(strip_mechanical_blocks(text))
                for sentence in _split_sentences(cleaned):
                    _app.audio_queue.put((sentence, char_name))
            if isinstance(message, dict):
                content    = message.get("content", "")
                name       = message.get("name", sender.name)
                tool_calls = message.get("tool_calls", None)
            else:
                content    = message
                name       = sender.name
                tool_calls = None

            is_system = False
            if isinstance(message, dict) and message.get("role") == "tool":
                is_system = True
            if content and str(content).startswith("[RÉSULTAT SYSTÈME]"):
                is_system = True

            # ── GARDE-FOU ANTI-COPIE : détecte si un agent répète le message précédent ──
            # Cible principalement les petits modèles (Groq/llama) qui ont tendance à
            # copier le dernier message du contexte quand ils n'ont rien à ajouter.
            # Seuil : >60% de mots communs entre le message de l'agent et le dernier
            # message d'un autre joueur → on rejette et on injecte [SILENCE].
            if (not is_system
                    and name in PLAYER_NAMES
                    and content
                    and str(content).strip() not in ("[SILENCE]", "")):
                _prev_msg = _last_player_messages.get("_last_other_" + name, "")
                if _prev_msg:
                    import re as _re_copy
                    def _word_set(t):
                        return set(_re_copy.findall(r"[a-zA-ZÀ-ÿ]{4,}", t.lower()))
                    _cur_words  = _word_set(str(content))
                    _prev_words = _word_set(_prev_msg)
                    if _cur_words and _prev_words:
                        _common = len(_cur_words & _prev_words)
                        _ratio  = _common / max(len(_prev_words), 1)
                        if _ratio > 0.60:
                            _app.msg_queue.put({
                                "sender": "⚠️ Règle",
                                "text": (
                                    f"[COPIE DÉTECTÉE] {name} a reproduit ~{int(_ratio*100)}% "
                                    f"du message précédent. Message ignoré → [SILENCE] injecté."
                                ),
                                "color": "#e67e22",
                            })
                            # On injecte un SILENCE à la place dans le contexte autogen
                            _original_receive(
                                self_mgr,
                                {"role": "assistant", "content": "[SILENCE]", "name": name},
                                sender, request_reply=False, silent=True,
                            )
                            return

                # Mémorise ce message pour la vérification du prochain agent
                _last_player_messages["_last_other_" + name] = str(content)
                # Màj aussi pour tous les autres joueurs (ils voient le msg de `name`)
                for _pn in PLAYER_NAMES:
                    if _pn != name:
                        _last_player_messages["_last_other_" + _pn] = str(content)

            # ── FILTRE INACTIF : agent désactivé en cours de session ───────
            # Si le personnage n'est plus dans la scène, on ignore son message
            # sans l'injecter dans le contexte autogen.
            if name in PLAYER_NAMES and name not in get_active_characters():
                return  # silence total — pas de feedback, pas d'injection

            # ── RÉPONSE À JET DEMANDÉ PAR LE MJ : exemptée de toutes les restrictions ──
            # Quand le MJ demande un jet (dégâts, attaque, sauvegarde, soin…), l'agent
            # doit pouvoir exécuter l'appel d'outil sans que ça lui coûte une ressource
            # hors-tour, même s'il est silencieux ou que ce n'est pas son tour.
            #
            # GARDE-FOU ANTI-PARASITE :
            # Un appel d'outil n'est légitime que si une [DIRECTIVE SYSTÈME] récente
            # existe dans l'historique pour cet agent. Sans directive → l'appel est
            # bloqué et un message d'erreur est injecté dans le contexte autogen.
            # Cela évite que les modèles faibles (arcee, llama free tier) appellent
            # update_hp / roll_dice spontanément sans narration préalable.
            _FREE_TOOLS = frozenset({"roll_dice", "update_hp", "use_spell_slot", "add_temp_hp",
                              "add_item_to_inventory", "remove_item_from_inventory", "update_currency"})

            def _has_recent_directive(agent_name: str) -> bool:
                """Retourne True si une [DIRECTIVE SYSTÈME] récente dans l'historique
                cible cet agent. Fenêtre : 10 derniers messages."""
                try:
                    for _msg in reversed((self.groupchat.messages if self.groupchat else [])[-10:]):
                        _mc = str(_msg.get("content", ""))
                        if "[DIRECTIVE SYSTÈME" in _mc and agent_name in _mc:
                            return True
                except Exception:
                    pass
                return False

            def _extract_tool_args(tc) -> dict:
                """Extrait les arguments d'un tool_call (dict ou objet)."""
                try:
                    import json as _j
                    raw = (tc.get("function", {}).get("arguments", "{}")
                           if isinstance(tc, dict)
                           else getattr(getattr(tc, "function", None), "arguments", "{}"))
                    return _j.loads(raw) if isinstance(raw, str) else (raw or {})
                except Exception:
                    return {}

            is_mj_roll_response = False
            if tool_calls and isinstance(tool_calls, list):
                for _tc in tool_calls:
                    _fn_name = (
                        _tc.get("function", {}).get("name")
                        if isinstance(_tc, dict)
                        else getattr(getattr(_tc, "function", None), "name", None)
                    )
                    if _fn_name not in _FREE_TOOLS:
                        continue

                    # ── Guard 1 : update_hp(amount=0) est toujours un appel parasite ──
                    if _fn_name == "update_hp":
                        _args = _extract_tool_args(_tc)
                        if int(_args.get("amount", 1)) == 0:
                            _parasite_msg = (
                                f"[SYSTÈME — APPEL INVALIDE]\n"
                                f"{name} a appelé update_hp(amount=0) — appel ignoré.\n"
                                f"update_hp ne doit être appelé qu'avec un montant non nul "
                                f"(négatif pour dégâts, positif pour soin) ET uniquement "
                                f"sur instruction [DIRECTIVE SYSTÈME — DÉGÂTS/SOIN] du MJ.\n"
                                f"Ne modifie JAMAIS tes PV de ta propre initiative."
                            )
                            _app.msg_queue.put({
                                "sender": "⚠️ Système",
                                "text": _parasite_msg,
                                "color": "#cc4422",
                            })
                            _original_receive(
                                self_mgr,
                                {"role": "user", "content": _parasite_msg, "name": "Alexis_Le_MJ"},
                                sender, request_reply=False, silent=True,
                            )
                            return   # bloquer le message entier

                    # ── Guard 2 : appel sans directive MJ préalable → parasite ──────
                    if name in PLAYER_NAMES and not _has_recent_directive(name):
                        _parasite_msg = (
                            f"[SYSTÈME — APPEL OUTIL REFUSÉ]\n"
                            f"{name} a appelé {_fn_name} sans [DIRECTIVE SYSTÈME] du MJ.\n\n"
                            f"RÈGLE ABSOLUE (point 4) :\n"
                            f"  Tu ne peux PAS appeler {_fn_name} de ta propre initiative.\n"
                            f"  Cet outil n'est autorisé QUE sur instruction explicite du MJ\n"
                            f"  via [DIRECTIVE SYSTÈME — DÉGÂTS], [DIRECTIVE SYSTÈME — JET], etc.\n\n"
                            f"Action requise : rédige une réponse narrative (roleplay) ou\n"
                            f"déclare une action via un bloc [ACTION] si c'est ton tour."
                        )
                        _app.msg_queue.put({
                            "sender": "⚠️ Système",
                            "text": _parasite_msg,
                            "color": "#cc4422",
                        })
                        _original_receive(
                            self_mgr,
                            {"role": "user", "content": _parasite_msg, "name": "Alexis_Le_MJ"},
                            sender, request_reply=False, silent=True,
                        )
                        return   # bloquer le message entier

                    # ── Appel légitime ────────────────────────────────────────────────
                    is_mj_roll_response = True
                    # Sync tracker après add_temp_hp
                    if _fn_name == "add_temp_hp":
                        try:
                            if _app._combat_tracker is not None:
                                _app.root.after(300, _app._combat_tracker.sync_pc_hp_from_state)
                        except Exception:
                            pass
                    break

            # ── FILTRE COMBAT : PJ hors-tour tente une action ──────────────
            # Bloque tout [ACTION] ou tentative d'action physique si ce n'est
            # pas le tour du PJ. Autorise uniquement : réaction D&D, parole brève.
            # Exception : un [ACTION] Type: Réaction est toujours autorisé hors-tour
            # (Uncanny Dodge, Bouclier, Attaque d'opportunité, Contresort…).
            _content_str_offturn = str(content) if content else ""
            _action_match_offturn = _action_pattern.search(_content_str_offturn)
            _is_reaction_block = (
                _action_match_offturn is not None
                and "réaction" in (_action_match_offturn.group("type") or "").lower()
            )
            _is_offturn_action = (
                not is_system
                and not is_mj_roll_response
                and COMBAT_STATE["active"]
                and name in PLAYER_NAMES
                and name != COMBAT_STATE.get("active_combatant")
                and content
                and _action_match_offturn is not None
                and not _is_reaction_block   # les réactions hors-tour sont légitimes
            )
            if _is_offturn_action:
                _block_msg = (
                    f"[SYSTÈME — HORS TOUR]\n"
                    f"Ce n'est pas le tour de {name}. "
                    f"C'est actuellement le tour de {COMBAT_STATE.get('active_combatant', '?')}. "
                    f"Tu ne peux PAS déclarer d'[ACTION] hors de ton tour.\n"
                    f"Options autorisées : réaction D&D 5e (Attaque d'opportunité, Shield, Contresort…) "
                    f"ou une phrase de roleplay sans mécanique. "
                    f"Attends ton tour avant d'agir."
                )
                _app.msg_queue.put({
                    "sender": "⚔️ Combat",
                    "text":   _block_msg,
                    "color":  "#cc4422"
                })
                _original_receive(
                    self_mgr,
                    {"role": "user", "content": _block_msg, "name": "Alexis_Le_MJ"},
                    sender, request_reply=False, silent=True,
                )
                _original_receive(self_mgr, message, sender, request_reply, silent)
                return

            # ── FILTRE COMBAT : agent hors-tour ayant épuisé réaction ET parole ──
            if (not is_system
                    and not is_mj_roll_response
                    and COMBAT_STATE["active"]
                    and name in PLAYER_NAMES
                    and name != COMBAT_STATE.get("active_combatant")
                    and _is_fully_silenced(name)):
                _app.msg_queue.put({
                    "sender": "⚔️ Combat",
                    "text":   f"🤫 {name} — silencieux (réaction ET parole déjà utilisées ce round).",
                    "color":  "#444455"
                })
                _original_receive(self_mgr, message, sender, request_reply, silent)
                return

            # ── FILTRE COMBAT : violation hors-tour (action/mouvement/sort interdit) ──
            # Détecte les tentatives d'action physique non autorisées hors réaction/parole.
            _ILLEGAL_OFFTURN = _re.compile(
                r"\b(je me d[eé]place|je cours|je bouge|je marche|je recule|je charge"
                r"|j'attaque(?! d'opportunit)|j'effectue une attaque"
                r"|je lance (?!un regard|un cri|un mot|un avertissement)"
                r"|je d[eé]coche|je frappe|je plonge|je saute|je roule"
                r"|action bonus|j'utilise mon action(?! de r[eé]action)"
                r"|je m'interpose|je me pr[eé]cipite"
                r"|\[ACTION\]|⚔️\s*ACTION"
                r"|s'abat|\bfrappe\b|enchaîn|troisième frappe|seconde frappe|deuxième frappe"
                r"|mon épée.*s'abat|ma lame.*frappe|mon arme.*touche)\b",
                _re.IGNORECASE
            )
            _is_offturn_violation = (
                not is_system
                and not is_mj_roll_response
                and COMBAT_STATE["active"]
                and name in PLAYER_NAMES
                and name != COMBAT_STATE.get("active_combatant")
                and content
                and str(content).strip() != "[SILENCE]"
                and _ILLEGAL_OFFTURN.search(str(content))
            )
            if _is_offturn_violation:
                _app.msg_queue.put({
                    "sender": "⚠️ Combat",
                    "text": (
                        f"[VIOLATION] {name} a tenté une action interdite hors-tour "
                        f"(mouvement, attaque ou sort hors réaction). "
                        f"Ce n'est pas son tour — seule une réaction D&D 5e ou une phrase brève est permise."
                    ),
                    "color": "#cc4422"
                })
                _original_receive(self_mgr, message, sender, request_reply, silent)
                return

            # ── INTERCEPTION SORT : [SORT: Nom | Niveau: X | Cible: Y] ──────────
            if (not is_system
                    and name in SPELL_CASTERS
                    and content
                    and _sort_pattern.search(str(content))):
                m = _sort_pattern.search(str(content))
                spell_name  = m.group("nom").strip()
                spell_level = int(m.group("niveau"))
                target      = (m.group("cible") or "").strip()
                # Retire la balise du contenu affiché — ne montrer que le roleplay
                clean_content = _sort_pattern.sub("", str(content)).strip()

                # ── Vérification liste de sorts préparés ───────────────────
                if not _is_spell_prepared(name, spell_name):
                    _avail3 = _get_prepared_spell_names(name)
                    _avail3_str = ", ".join(_avail3) if _avail3 else "aucun sort préparé trouvé"
                    _not_prepared_msg = (
                        f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE]\n"
                        f"{spell_name} n'est pas dans la liste de sorts préparés de {name}. "
                        f"Ce sort ne peut pas être lancé aujourd'hui.\n\n"
                        f"[SORTS AUTORISÉS POUR {name.upper()}]\n"
                        f"{_avail3_str}\n\n"
                        f"[INSTRUCTION]\n"
                        f"Choisis UNIQUEMENT parmi les sorts listés ci-dessus. "
                        f"Ne tente PAS de lancer {spell_name} — déclare une nouvelle action avec [ACTION]."
                    )
                    _app.msg_queue.put({"sender": "⚙️ Système",
                                        "text": _not_prepared_msg, "color": "#cc4444"})
                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": _not_prepared_msg, "name": "Alexis_Le_MJ"},
                        sender, request_reply=False, silent=True,
                    )
                    _original_receive(self_mgr, message, sender, request_reply, silent)
                    return

                # ── Vérification slots AVANT widget MJ ─────────────────
                if spell_level and spell_level > 0:
                    _state_check = load_state()
                    _slots_avail = (
                        _state_check.get("characters", {})
                        .get(name, {})
                        .get("spell_slots", {})
                        .get(str(spell_level), 0)
                    )
                    if _slots_avail <= 0:
                        _no_slot_msg = (
                            f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE]\n"
                            f"{name} n'a plus d'emplacement de sort de niveau {spell_level}. "
                            f"Le sort {spell_name} ne peut pas être lancé.\n\n"
                            f"[INSTRUCTION]\n"
                            f"Choisis une autre action (sort de niveau inférieur avec slots disponibles, "
                            f"attaque physique, ou sort sans slot). "
                            f"Ne tente PAS de lancer ce sort — déclare une nouvelle action avec [ACTION]."
                        )
                        _app.msg_queue.put({"sender": "⚙️ Système",
                                            "text": _no_slot_msg, "color": "#cc4444"})
                        _original_receive(
                            self_mgr,
                            {"role": "user", "content": _no_slot_msg, "name": "Alexis_Le_MJ"},
                            sender, request_reply=False, silent=True,
                        )
                        _original_receive(self_mgr, message, sender, request_reply, silent)
                        return

                # Bloque l'agent pendant que le MJ décide
                _spell_confirm_event.clear()
                _spell_confirm_result.clear()

                def _resume_cb(confirmed, actual_level,
                               _ev=_spell_confirm_event, _res=_spell_confirm_result):
                    _app._unregister_approval_event(_ev)
                    _res["confirmed"]    = confirmed
                    _res["actual_level"] = actual_level
                    _ev.set()

                _app._register_approval_event(_spell_confirm_event)
                _app.msg_queue.put({
                    "action":          "spell_confirm",
                    "char_name":       name,
                    "spell_name":      spell_name,
                    "spell_level":     spell_level,
                    "target":          target,
                    "resume_callback": _resume_cb,
                })

                # Affiche la partie roleplay sans la balise
                if clean_content and clean_content != "[SILENCE]":
                    clean_content = _strip_stars(clean_content)
                    _app.msg_queue.put({"sender": name, "text": clean_content,
                                        "color": _app.CHAR_COLORS.get(name, "#e0e0e0")})
                    log_tts_start(name, clean_content)
                    _enqueue_tts(clean_content, name)

                # Bloque jusqu'à la décision du MJ (max 5 min)
                _spell_confirm_event.wait(timeout=300)
                _app._unregister_approval_event(_spell_confirm_event)  # nettoyage si timeout

                if not _spell_confirm_result.get("confirmed", False):
                    # Sort refusé : on laisse passer le message original mais sans effet
                    pass

                # Dans tous les cas on continue (l'agent reprendra naturellement)
                _original_receive(self_mgr, message, sender, request_reply, silent)
                return

            # ── INTERCEPTION ACTION : [ACTION] Intention / Règle 5e / Cible ──────
            # Présent quand un joueur déclare une intention mécanique explicite.
            # ── INTERCEPTION ACTION(S) : [ACTION] Intention / Règle 5e / Cible ──────
            # Un message peut contenir plusieurs blocs [ACTION] (un par type :
            # Action, Action Bonus, Mouvement, Réaction, Gratuite…).
            # Pour les Extra Attack (Attaque × N), chaque attaque individuelle
            # est également confirmée séparément.
            # Chaque sous-action reçoit sa propre carte de confirmation MJ.
            if (not is_system
                    and name in PLAYER_NAMES
                    and content
                    and _action_pattern.search(str(content))):

                # Affiche le roleplay (tout ce qui précède les blocs [ACTION])
                clean_content = _action_pattern.sub("", str(content)).strip()
                if clean_content and clean_content != "[SILENCE]":
                    clean_content = _strip_stars(clean_content)
                    _app.msg_queue.put({
                        "sender": name,
                        "text":   clean_content,
                        "color":  _app.CHAR_COLORS.get(name, "#e0e0e0"),
                    })
                    log_tts_start(name, clean_content)
                    _enqueue_tts(clean_content, name)

                # Collecte toutes les sous-actions de tous les blocs [ACTION]
                _all_subactions: list[dict] = []
                for _m_a in _action_pattern.finditer(str(content)):
                    _type_lbl = (_m_a.group("type") or "").strip() or "Action"
                    _intention = _m_a.group("intention").strip()
                    _regle     = _m_a.group("regle").strip()
                    _cible     = _m_a.group("cible").strip()
                    _all_subactions.extend(
                        _split_into_subactions(_type_lbl, _intention, _regle, _cible)
                    )

                _sub_total = len(_all_subactions)

                # Confirme et exécute chaque sous-action séquentiellement
                for _sub_idx, _sub in enumerate(_all_subactions, start=1):
                    _sub_ev  = _threading.Event()
                    _sub_res: dict = {}

                    def _sub_cb(confirmed, mj_note="",
                                _ev=_sub_ev, _res=_sub_res):
                        _app._unregister_approval_event(_ev)
                        _res["confirmed"] = confirmed
                        _res["mj_note"]   = mj_note
                        _ev.set()

                    _pre_is_spell = any(
                        k in _sub["regle"].lower() or k in _sub["intention"].lower()
                        for k in (
                            # Termes généraux
                            "sort", "magie", "incant",
                            # Verbes d'invocation (manquaient — source du bug Lyra)
                            "invoque", "appelle", "convoque", "projette", "déclenche",
                            # Sorts offensifs
                            "boule", "projectile", "éclair", "feu", "dard", "rayon",
                            "missile", "flamme", "froid", "nécro", "acide", "tonnerre",
                            # Sorts de soin / support
                            "soin", "soigne", "heal", "cure", "guéri", "restaure",
                            "parole", "bénédic", "sanctif", "gardien", "arme spirit",
                            # Sorts de contrôle / défense
                            "bannit", "contresort", "dissip", "mur de", "bouclier",
                            "protection", "résistance", "balise",
                        )
                    )
                    _pre_lvl = None
                    if _pre_is_spell:
                        for _pat in (r"niv(?:eau)?\.?\s*(\d+)",
                                     r"niveau\s*(\d+)",
                                     r"\bniv(\d+)",
                                     r"slot\s+(?:de\s+)?(?:niveau\s+)?(\d)",
                                     r"emplacement\s+(?:de\s+)?(?:niveau\s+)?(\d)"):
                            _pm = _re.search(
                                _pat,
                                _sub["regle"] + " " + _sub["intention"],
                                _re.IGNORECASE
                            )
                            if _pm:
                                _pre_lvl = int(_pm.group(1))
                                break
                    if _pre_is_spell and _pre_lvl and _pre_lvl > 0:
                        try:
                            _pre_state = load_state()
                            _pre_slots = (
                                _pre_state.get("characters", {})
                                .get(name, {})
                                .get("spell_slots", {})
                                .get(str(_pre_lvl), 0)
                            )
                        except Exception:
                            _pre_slots = 1
                        if _pre_slots <= 0:
                            _no_slot_fb = (
                                f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE]\n"
                                f"{name} n'a plus d'emplacement de sort de niveau {_pre_lvl}. "
                                f"Ce sort ne peut pas être lancé.\n\n"
                                f"[INSTRUCTION]\n"
                                f"Choisis une autre action : sort de niveau inférieur, "
                                f"tour de magie, ou attaque physique."
                            )
                            _app.msg_queue.put({"sender": "⚙️ Système",
                                                "text": _no_slot_fb, "color": "#cc4444"})
                            _original_receive(
                                self_mgr,
                                {"role": "user", "content": _no_slot_fb, "name": "Alexis_Le_MJ"},
                                sender, request_reply=False, silent=True,
                            )
                            _sub_ev.set()
                            continue

                    # ── Vérification sorts préparés (pré-check [ACTION]) ─────
                    # Même logique que dans _execute_action_mechanics mais ici
                    # on intercepte AVANT d'envoyer la carte de confirmation au MJ.
                    if _pre_is_spell and _pre_lvl and _pre_lvl > 0:
                        _pre_spell_candidate = _extract_spell_name_llm(
                            _sub["intention"], name
                        )

                        if _pre_spell_candidate and not _is_spell_prepared(name, _pre_spell_candidate):
                            _avail2 = _get_prepared_spell_names(name)
                            _avail2_str = ", ".join(_avail2) if _avail2 else "aucun sort préparé trouvé"
                            _no_prep_fb = (
                                f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE]\n"
                                f"« {_pre_spell_candidate} » n'est pas dans la liste de sorts "
                                f"préparés de {name}. Ce sort ne peut pas être lancé aujourd'hui.\n\n"
                                f"[SORTS AUTORISÉS POUR {name.upper()}]\n"
                                f"{_avail2_str}\n\n"
                                f"[INSTRUCTION]\n"
                                f"Choisis UNIQUEMENT parmi les sorts listés ci-dessus. "
                                f"Déclare une nouvelle action avec [ACTION]."
                            )
                            _app.msg_queue.put({"sender": "⚙️ Système",
                                                "text": _no_prep_fb, "color": "#cc4444"})
                            _original_receive(
                                self_mgr,
                                {"role": "user", "content": _no_prep_fb, "name": "Alexis_Le_MJ"},
                                sender, request_reply=False, silent=True,
                            )
                            _sub_ev.set()
                            continue

                    _app._register_approval_event(_sub_ev)
                    _app.msg_queue.put({
                        "action":          "action_confirm",
                        "char_name":       name,
                        "type_label":      _sub["type_label"],
                        "intention":       _sub["intention"],
                        "regle":           _sub["regle"],
                        "cible":           _sub["cible"],
                        "sub_index":       _sub_idx,
                        "sub_total":       _sub_total,
                        "resume_callback": _sub_cb,
                    })

                    # Bloque jusqu'à la décision du MJ (max 10 min par sous-action)
                    _sub_ev.wait(timeout=600)
                    _app._unregister_approval_event(_sub_ev)  # nettoyage si timeout/annulation

                    _confirmed = _sub_res.get("confirmed", False)
                    _mj_note   = _sub_res.get("mj_note", "")

                    if _confirmed:
                        _is_single_atk = _sub.get("single_attack", False)

                        if _is_single_atk:
                            # ══════════════════════════════════════════════════════
                            # FLOW ATTAQUE INDIVIDUELLE
                            #  Phase 1 — jet d'attaque → MJ confirme touché/raté
                            #  Phase 2 — (si touché) propose Divine Smite si actif
                            #  Phase 3 — dégâts → MJ voit les dés et continue
                            # ══════════════════════════════════════════════════════

                            # ── Phase 1 : jet d'attaque ──────────────────────────
                            _atk_data = _roll_attack_only(
                                name, _sub["regle"], _sub["intention"],
                                _sub["cible"], _mj_note
                            )

                            if _atk_data["is_fumble"]:
                                # Fumble : on informe l'agent directement, pas de dégâts
                                feedback = (
                                    "[RÉSULTAT SYSTÈME — ATTAQUE]\n"
                                    + _atk_data["atk_text"]
                                    + "\n\n[INSTRUCTION NARRATIVE]\n"
                                    + f"Nat.1 — attaque automatiquement ratée. "
                                    + f"Narre en 1 phrase la maladresse de {name}."
                                )
                                _app.msg_queue.put({
                                    "sender": "⚙️ Système",
                                    "text": feedback, "color": "#4fc3f7"
                                })
                                _original_receive(
                                    self_mgr,
                                    {"role":"user","content":feedback,"name":"Alexis_Le_MJ"},
                                    sender, request_reply=False, silent=True,
                                )
                                continue  # sous-action suivante

                            # Carte hit/miss — le MJ décide si ça touche
                            _hit_ev  = _threading.Event()
                            _hit_res: dict = {}

                            def _hit_cb(hit, mj_note_hit="",
                                        _ev=_hit_ev, _res=_hit_res):
                                _app._unregister_approval_event(_ev)
                                _res["hit"]  = hit
                                _res["note"] = mj_note_hit
                                _ev.set()

                            _app._register_approval_event(_hit_ev)
                            _app.msg_queue.put({
                                "action":          "result_confirm",
                                "char_name":       name,
                                "type_label":      _sub["type_label"],
                                "results_text":    _atk_data["atk_text"],
                                "mode":            "attack",
                                "resume_callback": _hit_cb,
                            })
                            _hit_ev.wait(timeout=600)
                            _app._unregister_approval_event(_hit_ev)

                            _hit       = _hit_res.get("hit", False)
                            _hit_note  = _hit_res.get("note", "")

                            if not _hit:
                                # Raté
                                feedback = (
                                    "[RÉSULTAT SYSTÈME — ATTAQUE RATÉE]\n"
                                    + _atk_data["atk_text"]
                                    + "\n  → RATÉ ❌ (MJ)"
                                    + (f"\n  Note : {_hit_note}" if _hit_note else "")
                                    + "\n\n[INSTRUCTION NARRATIVE]\n"
                                    + f"Attaque ratée. Narre en 1 phrase l'esquive ou la parade de {_sub['cible']}."
                                )
                                _app.msg_queue.put({
                                    "sender": "⚙️ Système",
                                    "text": feedback, "color": "#4fc3f7"
                                })
                                _original_receive(
                                    self_mgr,
                                    {"role":"user","content":feedback,"name":"Alexis_Le_MJ"},
                                    sender, request_reply=False, silent=True,
                                )
                                continue  # sous-action suivante

                            # ── Phase 2 : proposer Divine Smite si en attente ──
                            # Deux sources possibles :
                            #  A) _pending_smite déjà rempli par une Action Bonus smite
                            #     déclarée dans un bloc [ACTION] séparé traité avant l'attaque.
                            #  B) Le smite est mentionné DANS le même bloc que l'attaque
                            #     (intention ou regle) → on le détecte ici et on enregistre
                            #     à la volée sans attendre qu'un bloc Action Bonus soit passé.
                            _smite_used = None

                            # Source B — détection inline si pas déjà dans _pending_smite
                            if name not in _pending_smite:
                                _sub_i_low = _sub["intention"].lower()
                                _sub_r_low = _sub["regle"].lower()
                                # Cherche aussi dans le message complet (le joueur peut avoir
                                # déclaré le smite de façon conditionnelle dans son roleplay,
                                # ex : "si ça touche j'utilise mon Châtiment Divin !"
                                # avant le bloc [ACTION] d'attaque).
                                _full_msg_low = str(content).lower()
                                _inline_smite_table = {
                                    "divine smite":     (None,  "radiant",   "Divine Smite"),
                                    "smite divin":      (None,  "radiant",   "Divine Smite"),
                                    "châtiment divin":  (None,  "radiant",   "Divine Smite"),
                                    "chatiment divin":  (None,  "radiant",   "Divine Smite"),
                                    "wrathful smite":   ("1d6", "psychique", "Wrathful Smite"),
                                    "courroux divin":   ("1d6", "psychique", "Wrathful Smite"),
                                    "thunderous smite": ("2d6", "tonnerre",  "Thunderous Smite"),
                                    "frappe tonnerre":  ("2d6", "tonnerre",  "Thunderous Smite"),
                                    "branding smite":   ("2d6", "radiant",   "Branding Smite"),
                                    "frappe lumière":   ("2d6", "radiant",   "Branding Smite"),
                                }
                                for _kw, (_dice, _typ, _lbl) in _inline_smite_table.items():
                                    if (_kw in _sub_i_low or _kw in _sub_r_low
                                            or _kw in _full_msg_low):
                                        # Extraire le niveau du slot mentionné
                                        _sm_lvl = None
                                        for _pat in (r"niv(?:eau)?\.?\s*(\d+)", r"\bniv(\d+)",
                                                     r"slot\s+(?:de\s+)?(?:niveau\s+)?(\d)",
                                                     r"emplacement\s+(?:de\s+)?(?:niveau\s+)?(\d)"):
                                            _pm = _re.search(_pat, _sub_i_low + " " + _sub_r_low,
                                                             _re.IGNORECASE)
                                            if _pm:
                                                _sm_lvl = int(_pm.group(1))
                                                break
                                        if _sm_lvl is None:
                                            _sm_lvl = 1   # fallback : slot niv.1
                                        if _dice is None:
                                            # Divine Smite : (1 + niveau_slot)d8 radiants
                                            _dice = f"{_sm_lvl + 1}d8"
                                        _pending_smite[name] = {
                                            "dice":  _dice,
                                            "type":  _typ,
                                            "label": _lbl,
                                        }
                                        break

                            if name in _pending_smite:
                                _sm_candidate = _pending_smite[name]
                                _smite_ev  = _threading.Event()
                                _smite_res: dict = {}

                                def _smite_cb(apply_it, mj_note_sm="",
                                              _ev=_smite_ev, _res=_smite_res):
                                    _app._unregister_approval_event(_ev)
                                    _res["apply"] = apply_it
                                    _res["note"]  = mj_note_sm
                                    _ev.set()

                                # Lire les slots disponibles pour les afficher dans la carte
                                try:
                                    from state_manager import load_state as _ls_smcard
                                    _smcard_slots = (
                                        _ls_smcard().get("characters", {})
                                        .get(name, {}).get("spell_slots", {})
                                    )
                                    _slots_avail_str = ", ".join(
                                        f"niv.{k}×{v}"
                                        for k, v in sorted(_smcard_slots.items(), key=lambda x: int(x[0]))
                                        if v > 0
                                    ) or "⚠ Aucun slot disponible !"
                                except Exception:
                                    _slots_avail_str = "(inconnu)"

                                _smite_txt = (
                                    f"{name} a {_sm_candidate['label']} actif.\n"
                                    f"Dés : {_sm_candidate['dice']} dégâts {_sm_candidate['type']}\n"
                                    f"Slots disponibles : {_slots_avail_str}\n"
                                    f"L'attaque a touché — appliquer le smite ?"
                                )
                                _app._register_approval_event(_smite_ev)
                                _app.msg_queue.put({
                                    "action":          "result_confirm",
                                    "char_name":       name,
                                    "type_label":      _sm_candidate["label"],
                                    "results_text":    _smite_txt,
                                    "mode":            "smite",
                                    "resume_callback": _smite_cb,
                                })
                                _smite_ev.wait(timeout=600)
                                _app._unregister_approval_event(_smite_ev)

                                if _smite_res.get("apply", False):
                                    _smite_used = _pending_smite.pop(name)
                                    # ── Consommer le slot : choisir le niveau disponible ──
                                    # Priorité : slot_level demandé → sinon le plus bas disponible.
                                    _sm_slot_lvl = _smite_used.get("slot_level", 1)
                                    try:
                                        from state_manager import use_spell_slot as _uss, load_state as _ls_sm
                                        _sm_state = _ls_sm()
                                        _sm_slots = (_sm_state.get("characters", {})
                                                     .get(name, {}).get("spell_slots", {}))
                                        # Si le niveau demandé est épuisé, prendre le plus bas dispo
                                        if _sm_slots.get(str(_sm_slot_lvl), 0) <= 0:
                                            _avail = sorted(
                                                (int(k) for k, v in _sm_slots.items() if v > 0),
                                                key=lambda x: x
                                            )
                                            if _avail:
                                                _sm_slot_lvl = _avail[0]
                                                # Recalcul des dés si Divine Smite
                                                if _smite_used["label"] == "Divine Smite":
                                                    _smite_used["dice"] = f"{_sm_slot_lvl + 1}d8"
                                            else:
                                                # Plus aucun slot — annuler le smite
                                                _app.msg_queue.put({
                                                    "sender": "⚙️ Système",
                                                    "text": (
                                                        f"[Divine Smite annulé] {name} n'a plus "
                                                        f"aucun emplacement de sort disponible."
                                                    ),
                                                    "color": "#cc4444",
                                                })
                                                _smite_used = None
                                        if _smite_used:
                                            _slot_result = _uss(name, str(_sm_slot_lvl))
                                            _app.msg_queue.put({
                                                "sender": "⚙️ Système",
                                                "text": f"[Slot niv.{_sm_slot_lvl}] {_slot_result}",
                                                "color": "#8888cc",
                                            })
                                    except Exception as _sse:
                                        print(f"[Smite slot] Erreur : {_sse}")
                                else:
                                    # MJ dit non → on conserve le smite pour la prochaine attaque
                                    pass

                            # ── Phase 3 : dégâts ─────────────────────────────
                            _dmg_feedback = _roll_damage_only(
                                name,
                                _sub["cible"],
                                _atk_data["dn"], _atk_data["df"], _atk_data["db"],
                                _atk_data["is_crit"],
                                _smite_used,
                                _hit_note,
                            )

                            # Carte de résultats dégâts → MJ valide
                            _dmg_ev  = _threading.Event()
                            _dmg_note: dict = {}

                            def _dmg_cb(mj_note_dmg="",
                                        _ev=_dmg_ev, _res=_dmg_note):
                                _app._unregister_approval_event(_ev)
                                _res["note"] = mj_note_dmg
                                _ev.set()

                            _dmg_part = (
                                _dmg_feedback
                                .split("\n\n[INSTRUCTION NARRATIVE]")[0]
                                .replace("[RÉSULTAT SYSTÈME — DÉGÂTS CONFIRMÉS PAR MJ]\n","")
                                .strip()
                            )
                            _app._register_approval_event(_dmg_ev)
                            _app.msg_queue.put({
                                "action":          "result_confirm",
                                "char_name":       name,
                                "type_label":      _sub["type_label"],
                                "results_text":    _dmg_part,
                                "mode":            "damage",
                                "resume_callback": _dmg_cb,
                            })
                            _dmg_ev.wait(timeout=600)
                            _app._unregister_approval_event(_dmg_ev)

                            _dmg_mj_note = _dmg_note.get("note", "")
                            if _dmg_mj_note:
                                _dmg_feedback += f"\n[Modification MJ] {_dmg_mj_note}"

                            # Injecter le feedback complet (hit + dégâts + smite) dans autogen
                            feedback = (
                                "[RÉSULTAT SYSTÈME — ATTAQUE RÉSOLUE]\n"
                                + _atk_data["atk_text"]
                                + "\n  → TOUCHÉ ✅ (MJ)"
                                + (f"\n  Note : {_hit_note}" if _hit_note else "")
                                + "\n\n"
                                + _dmg_feedback
                            )
                            _app.msg_queue.put({
                                "sender": "⚙️ Système",
                                "text":   feedback, "color": "#4fc3f7"
                            })
                            _original_receive(
                                self_mgr,
                                {"role":"user","content":feedback,"name":"Alexis_Le_MJ"},
                                sender, request_reply=False, silent=True,
                            )

                        else:
                            # ══════════════════════════════════════════════════════
                            # FLOW NON-ATTAQUE (sort, action bonus, mouvement, etc.)
                            # Un seul feedback → résultat → MJ continue
                            # ══════════════════════════════════════════════════════
                            try:
                                feedback = _execute_action_mechanics(
                                    name,
                                    _sub["intention"],
                                    _sub["regle"],
                                    _sub["cible"],
                                    _mj_note,
                                    single_attack=False,
                                    type_label=_sub.get("type_label", ""),
                                )
                            except Exception as _exec_err:
                                feedback = (
                                    f"[MJ → {name}] ✅ [{_sub['type_label']}] autorisé. "
                                    f"(Erreur : {_exec_err}) "
                                    f"Narre : {_sub['intention']} — {_sub['regle']} → {_sub['cible']}"
                                )

                            _split_marker = "\n\n[INSTRUCTION NARRATIVE]"
                            _results_part = (
                                feedback.split(_split_marker)[0]
                                .replace("[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ]\n","")
                                .replace("[RÉSULTAT SYSTÈME — ATTAQUE DE SORT]\n","")
                                .strip()
                            )

                            _is_spell_attack = feedback.startswith("[RÉSULTAT SYSTÈME — ATTAQUE DE SORT]")

                            _result_ev  = _threading.Event()
                            _result_note: dict = {}

                            if _is_spell_attack:
                                def _result_cb(hit, mj_note_res="",
                                               _ev=_result_ev, _res=_result_note):
                                    _app._unregister_approval_event(_ev)
                                    _res["hit"]  = hit
                                    _res["note"] = mj_note_res
                                    _ev.set()
                            else:
                                def _result_cb(mj_note_res="",
                                               _ev=_result_ev, _res=_result_note):
                                    _app._unregister_approval_event(_ev)
                                    _res["note"] = mj_note_res
                                    _ev.set()

                            # Attaque de sort → carte Touché/Raté (mode="attack")
                            # Autre action → carte Continuer (mode="damage")
                            _result_mode = "attack" if _is_spell_attack else "damage"

                            _app._register_approval_event(_result_ev)
                            _app.msg_queue.put({
                                "action":          "result_confirm",
                                "char_name":       name,
                                "type_label":      _sub["type_label"],
                                "results_text":    _results_part,
                                "mode":            _result_mode,
                                "resume_callback": _result_cb,
                            })
                            _result_ev.wait(timeout=600)
                            _app._unregister_approval_event(_result_ev)

                            _res_mj_note = _result_note.get("note", "")

                            if _is_spell_attack:
                                # _result_note["note"] contient "hit" ou "" selon le callback
                                # Pour mode="attack", le callback reçoit (hit: bool, note: str)
                                # On réutilise la même convention : note="raté" si raté
                                _spell_hit = _result_note.get("hit", True)
                                if not _spell_hit:
                                    feedback = (
                                        "[RÉSULTAT SYSTÈME — ATTAQUE DE SORT RATÉE]\n"
                                        + _results_part
                                        + "\n  → RATÉ ❌ (MJ)"
                                        + (f"\n  Note : {_res_mj_note}" if _res_mj_note else "")
                                        + "\n\n[INSTRUCTION NARRATIVE]\n"
                                        + f"Attaque ratée. Narre en 1 phrase comment {cible} esquive ou résiste."
                                    )
                                else:
                                    feedback = (
                                        "[RÉSULTAT SYSTÈME — ATTAQUE DE SORT RÉSOLUE]\n"
                                        + _results_part
                                        + "\n  → TOUCHÉ ✅ (MJ)"
                                        + (f"\n  Note : {_res_mj_note}" if _res_mj_note else "")
                                        + "\n\n[INSTRUCTION NARRATIVE]\n"
                                        + f"Attaque de sort réussie. Narre en 1-2 phrases l'impact sur {cible}. Ne mentionne pas les chiffres."
                                    )
                            else:
                                if _res_mj_note:
                                    feedback += f"\n[Modification MJ] {_res_mj_note}"

                            # ── Mouvement : déplacer le token sur la carte ────
                            _move_match = _re.search(
                                r'\[MOVE_TOKEN:([^:]+):(\d+):(\d+)\]', feedback
                            )
                            if _move_match:
                                _mv_name = _move_match.group(1)
                                _mv_col  = int(_move_match.group(2))
                                _mv_row  = int(_move_match.group(3))
                                # Supprimer le tag du feedback injecté dans autogen
                                feedback = _re.sub(r'\[MOVE_TOKEN:[^\]]+\]', '', feedback).strip()
                                try:
                                    _cmap = getattr(_app, "_combat_map_win", None)
                                    if _cmap is not None:
                                        def _do_move(cmap=_cmap, n=_mv_name,
                                                     c=_mv_col, r=_mv_row):
                                            msg = cmap.move_token(n, c, r)
                                            _app.msg_queue.put({
                                                "sender": "🗺️ Carte",
                                                "text": msg,
                                                "color": "#64b5f6",
                                            })
                                            # Rebuild prompts pour que les agents voient
                                            # les nouvelles positions
                                            try:
                                                _app._rebuild_agent_prompts()
                                            except Exception:
                                                pass
                                        _app.root.after(0, _do_move)
                                    else:
                                        _app.msg_queue.put({
                                            "sender": "🗺️ Carte",
                                            "text": (
                                                f"[Mouvement {_mv_name}] Carte non ouverte — "
                                                f"token non déplacé. Ouvrez la carte pour voir les positions."
                                            ),
                                            "color": "#888888",
                                        })
                                except Exception as _mv_err:
                                    print(f"[MoveToken] {_mv_err}")

                            _app.msg_queue.put({
                                "sender": "⚙️ Système",
                                "text":   feedback, "color": "#4fc3f7"
                            })
                            _original_receive(
                                self_mgr,
                                {"role":"user","content":feedback,"name":"Alexis_Le_MJ"},
                                sender, request_reply=False, silent=True,
                            )
                    else:
                        _note_txt = f" {_mj_note}" if _mj_note else ""
                        feedback = (
                            f"[MJ → {name}] ❌ [{_sub['type_label']}] refusé.{_note_txt}"
                        )
                        _app.msg_queue.put({
                            "sender": "❌ MJ",
                            "text":   feedback,
                            "color":  "#ef9a9a",
                        })
                        _original_receive(
                            self_mgr,
                            {"role": "user", "content": feedback, "name": "Alexis_Le_MJ"},
                            sender,
                            request_reply=False,
                            silent=True,
                        )

                # ── [FIN_DE_TOUR] : avance le combat tracker ────────────
                if (COMBAT_STATE["active"]
                        and name == COMBAT_STATE.get("active_combatant")
                        and "[FIN_DE_TOUR]" in str(content)):
                    _app.root.after(0, lambda n=name: _app._on_pc_turn_ended(n))

                _original_receive(self_mgr, message, sender, request_reply, silent)
                return

            # ── [FIN_DE_TOUR] sans bloc [ACTION] (message roleplay final) ──
            if (not is_system
                    and COMBAT_STATE["active"]
                    and name in PLAYER_NAMES
                    and name == COMBAT_STATE.get("active_combatant")
                    and content
                    and "[FIN_DE_TOUR]" in str(content)
                    and not _action_pattern.search(str(content))):
                _app.root.after(0, lambda n=name: _app._on_pc_turn_ended(n))

            # ── DIRECTIVES MJ → héros : parseur LLM ──────────────────────────
            # Remplace les anciens blocs regex (dégâts + demande de jet).
            # Un LLM rapide (flash/groq, température 0) parse le message du MJ
            # et retourne un JSON structuré. On injecte une [DIRECTIVE SYSTÈME]
            # que l'agent cible exécutera via ses outils (update_hp, roll_dice…).
            if (not is_system
                    and name == "Alexis_Le_MJ"
                    and content):
                _directives = _parse_mj_directives(str(content))
                for _d in _directives:
                    _d_action = _d.get("action", "")
                    _d_cible  = _d.get("cible", "")
                    # Expand "tous" en liste de PJ actifs
                    _d_targets = (
                        [n for n in PLAYER_NAMES if n in [a.name for a in groupchat.agents]]
                        if _d_cible == "tous"
                        else [_d_cible] if _d_cible in PLAYER_NAMES
                        else []
                    )
                    if not _d_targets:
                        continue

                    for _tgt in _d_targets:
                        import json as _json_d

                        if _d_action == "degats":
                            _montant = int(_d.get("montant", 0))
                            _type_d  = _d.get("type_degat", "")
                            _type_str = f" de {_type_d}" if _type_d else ""
                            _instr = (
                                f"[DIRECTIVE SYSTÈME — DÉGÂTS] ⚠️ APPEL D'OUTIL OBLIGATOIRE\n"
                                f"Destinataire : {_tgt} UNIQUEMENT\n"
                                f"{_montant} dégâts{_type_str}\n\n"
                                f"▶ {_tgt} : tu DOIS appeler update_hp maintenant (règle 4 exception).\n"
                                f"   Appel exact : update_hp("
                                f"character_name=\"{_tgt}\", amount=-{_montant})\n\n"
                                f"Après le retour de l'outil, narre en 1-2 phrases comment tu "
                                f"encaisses le coup. Pas de chiffres.\n"
                                f"⚠️ Cette réponse NE COÛTE AUCUNE ressource hors-tour."
                            )
                            _pending_damage_narrators.add(_tgt)
                            # Rafraîchit UI après que l'agent aura appelé update_hp
                            try:
                                _app.root.after(500, _app._refresh_char_stats)
                            except Exception:
                                pass
                            try:
                                if _app._combat_tracker is not None:
                                    _app.root.after(600, _app._combat_tracker.sync_pc_hp_from_state)
                            except Exception:
                                pass

                        elif _d_action == "soin":
                            _montant = int(_d.get("montant", 0))
                            _directive_json = _json_d.dumps(
                                {"action":"soin","cible":_tgt,"montant":_montant},
                                ensure_ascii=False
                            )
                            _instr = (
                                f"[DIRECTIVE SYSTÈME — SOIN]\n"
                                f"{_directive_json}\n\n"
                                f"{_tgt} : appelle update_hp(character_name=\"{_tgt}\", amount=+{_montant}) "
                                f"IMMÉDIATEMENT — AVANT tout texte.\n"
                                f"Ensuite, narre en 1 phrase ta réaction au soin."
                            )

                        elif _d_action in ("jet_sauvegarde", "jet_competence", "jet_attaque"):
                            _carac  = _d.get("caracteristique", "")
                            _dc     = _d.get("dc")
                            _de     = _d.get("de", "1d20")
                            _bonus  = int(_d.get("bonus", 0))
                            _dc_str = f" contre DC {_dc}" if _dc else ""
                            _action_label = {
                                "jet_sauvegarde": "Jet de sauvegarde",
                                "jet_competence": "Jet de compétence",
                                "jet_attaque":    "Jet d'attaque",
                            }.get(_d_action, "Jet")
                            # Lookup du bonus réel depuis les stats si bonus=0 non spécifié
                            _real_bonus = _bonus
                            if _real_bonus == 0 and _tgt in _CHAR_MECHANICS:
                                _stats = _CHAR_MECHANICS[_tgt]
                                _carac_low = _carac.lower()
                                _real_bonus = (
                                    _stats.get("saves", {}).get(_carac_low)
                                    or _stats.get("skills", {}).get(_carac_low)
                                    or 0
                                )
                            _directive_json = _json_d.dumps(_d, ensure_ascii=False)
                            _instr = (
                                f"[DIRECTIVE SYSTÈME — JET] ⚠️ APPEL D'OUTIL OBLIGATOIRE\n"
                                f"Destinataire : {_tgt} UNIQUEMENT\n"
                                f"{_action_label} de {_carac}{_dc_str}\n\n"
                                f"▶ {_tgt} : tu DOIS appeler roll_dice maintenant (règle 4 exception).\n"
                                f"   Appel exact : roll_dice("
                                f"character_name=\"{_tgt}\", "
                                f"dice_type=\"{_de}\", "
                                f"bonus={_real_bonus})\n\n"
                                f"Après le résultat du dé, narre en 1 phrase comment "
                                f"{_tgt} vit physiquement ce moment. "
                                f"Ne mentionne pas le chiffre du résultat."
                            )

                        else:
                            continue

                        _original_receive(
                            self_mgr,
                            {"role": "user", "content": _instr, "name": "Alexis_Le_MJ"},
                            sender, request_reply=False, silent=True,
                        )
            # Appel normal
            _original_receive(self_mgr, message, sender, request_reply, silent)

            # ── JOURNAL NARRATIF ──────────────────────────────────────────────
            if not is_system and content and str(content).strip() not in ("[SILENCE]", ""):
                _chat_log.log_message(name, str(content))

            # ── MÉMOIRES CONTEXTUELLES : détection dynamique sur chaque message ──
            # Scan sur le contenu de TOUS les messages non-système (joueurs et MJ)
            # pour activer les mémoires mentionnées et enrichir les prompts en temps réel.
            if not is_system and content and str(content).strip() not in ("[SILENCE]", ""):
                _app._update_contextual_memories(str(content))

            # ── FILTRE PNJ : détection de paroles inventées ──────────────────────
            if not is_system and name in PLAYER_NAMES and content and _pnj_pattern.search(str(content)):
                _app.msg_queue.put({
                    "sender": "⚠️ Règle",
                    "text": (
                        f"[VIOLATION] {name} a tenté d'incarner un PNJ. "
                        f"Ce passage a été masqué. Alexis, c'est à vous de donner la réplique du PNJ."
                    ),
                    "color": "#F44336"
                })
                return

            # ── SUIVI COMBAT : marque la ressource hors-tour consommée ──────────
            # Classifie le contenu pour consommer réaction ou parole (ou les deux).
            # Les appels d'outils demandés par le MJ (roll_dice, etc.) sont exemptés.
            # Les narrations de dégâts reçus (_pending_damage_narrators) sont aussi
            # exemptées : ce n'est pas une action du personnage, juste un état.
            if (not is_system
                    and not is_mj_roll_response
                    and COMBAT_STATE["active"]
                    and name in PLAYER_NAMES
                    and name != COMBAT_STATE.get("active_combatant")
                    and content
                    and str(content).strip() != "[SILENCE]"):

                # Narration de dégâts reçus → aucune ressource consommée
                if name in _pending_damage_narrators:
                    _pending_damage_narrators.discard(name)
                    # Pas de tracking de parole/réaction pour cette réponse
                else:
                    _content_str = str(content)

                    # Déclencheurs mécaniques D&D → consomme la RÉACTION
                    _REACTION_TRIGGER = _re.compile(
                        r"\b(r[eé]action|attaque d.opportunit[eé]|bouclier|riposte"
                        r"|pas de c[oô]t[eé]|sort de r[eé]action|contre-attaque"
                        r"|j.utilise (ma|mon) action de r[eé]action"
                        r"|j.interpose|frappe en r[eé]action)\b",
                        _re.IGNORECASE
                    )
                    # Parole explicite → consomme la PAROLE
                    _SPEECH_TRIGGER = _re.compile(
                        r'[«»\"\u201c\u201d]'                        # guillemets
                        r'|\bje (crie|hurle|chuchote|dis|murmure|siffle|avertis|lance un cri|lance un mot)\b'
                        r'|\b(attention|garde[sz]?-vous|derrière|à droite|à gauche|recule[sz]?|fuyez)\b',
                        _re.IGNORECASE
                    )

                    is_reaction = bool(_REACTION_TRIGGER.search(_content_str))
                    is_speech   = bool(_SPEECH_TRIGGER.search(_content_str))

                    # Par défaut (contenu non vide et non classifié → parole prudente)
                    if not is_reaction and not is_speech:
                        is_speech = True

                    if is_reaction and name not in COMBAT_STATE["reactions_used"]:
                        COMBAT_STATE["reactions_used"].add(name)
                        _app._update_agent_combat_prompts()
                        _app.msg_queue.put({
                            "sender": "⚔️ Combat",
                            "text":   f"↺ {name} — réaction hors-tour consommée pour ce round.",
                            "color":  "#5588cc"
                        })

                    if is_speech and name not in COMBAT_STATE["speech_used"]:
                        COMBAT_STATE["speech_used"].add(name)
                        _app._update_agent_combat_prompts()
                        _app.msg_queue.put({
                            "sender": "⚔️ Combat",
                            "text":   f"💬 {name} — parole hors-tour consommée pour ce round.",
                            "color":  "#8855aa"
                        })

            if name != "Alexis_Le_MJ" or is_system:
                if isinstance(message, dict) and message.get("role") == "tool":
                    nom_outil      = message.get("name", "Outil")
                    resultat_outil = message.get("content", "")
                    _app.msg_queue.put({
                        "sender": f"🎲 Résultat ({nom_outil})",
                        "text":   resultat_outil,
                        "color":  "#4CAF50"
                    })
                elif content and str(content).strip() != "[SILENCE]":
                    display_name = "Système" if is_system else name
                    color        = "#ffcc00" if is_system else "#e0e0e0"
                    display_text = (_strip_stars(str(content))
                                    if not is_system and display_name in PLAYER_NAMES
                                    else content)
                    _app.msg_queue.put({"sender": display_name, "text": display_text, "color": color})
                    if not is_system and display_name in PLAYER_NAMES:
                        log_tts_start(display_name, str(display_text))
                        _enqueue_tts(display_text, display_name)

                if tool_calls:
                    _app.msg_queue.put({"sender": name, "text": "✨[Est en train de préparer une action/un sort...]", "color": "#aaaaaa"})

        # Substitution de classe (atomique, safe avec gRPC) au lieu de types.MethodType sur l'instance
        manager.__class__ = type(
            "PatchedGroupChatManager",
            (manager.__class__,),
            {"receive": patched_receive}
        )

        self.msg_queue.put({"sender": "Système", "text": "⚔️ Tous les joueurs sont à la table. À vous de lancer la partie (Texte ou 🎤)...", "color": "#888888"})

        # Enregistre l'ID du thread pour pouvoir l'interrompre via ctypes
        self._autogen_thread_id = threading.current_thread().ident

        self._set_waiting_for_mj(True)
        premier_message = self.wait_for_input()
        self._set_waiting_for_mj(False)
        clear_hist = True
        
        while True:
            try:
                self._stop_event.clear()   # évite qu'un event résiduel stoppe la reprise
                self._set_llm_running(True)
                mj_agent.initiate_chat(
                    manager,
                    message=premier_message,
                    clear_history=clear_hist
                )
                self._set_llm_running(False)
                break  # La session s'est terminée normalement
            except StopLLMRequested:
                self._set_llm_running(False)
                self._set_waiting_for_mj(False)
                if self._pending_interrupt_input is not None:
                    premier_message = self._pending_interrupt_input
                    self._pending_interrupt_input = None
                    # ← Affiche le message utilisateur APRÈS l'arrêt effectif
                    if self._pending_interrupt_display is not None:
                        self.msg_queue.put(self._pending_interrupt_display)
                        self._pending_interrupt_display = None
                    self.msg_queue.put({"sender": "Système", "text": "▶️ Reprise avec le nouveau message.", "color": "#aaaaaa"})
                else:
                    self._pending_interrupt_display = None
                    self.msg_queue.put({"sender": "Système", "text": "⏹️ LLMs arrêtés. Tapez un message pour reprendre.", "color": "#FF9800"})
                    self._set_waiting_for_mj(True)
                    premier_message = self.wait_for_input()
                    self._set_waiting_for_mj(False)
                clear_hist = False
            except Exception as e:
                self._set_llm_running(False)
                import traceback
                traceback.print_exc()

                err_msg = str(e)
                is_quota_error = "RESOURCE_EXHAUSTED" in err_msg or "429" in err_msg or "quota" in err_msg.lower()

                # ── Fallback générique : détecte quel modèle a épuisé son quota
                # et bascule TOUS les agents qui l'utilisent vers le suivant dans
                # la chaîne de priorité définie dans llm_config.py.
                # Source de vérité : app_config.json (lu via get_agent_config).
                # campaign_state["characters"][x]["llm"] est aussi mis à jour
                # pour que la fiche personnage reste cohérente.
                # ─────────────────────────────────────────────────────────────────
                # Chaîne de fallback : doit rester synchronisée avec llm_config.py.
                _FALLBACK_CHAIN = [
                    "gemini-3.1-pro-preview",
                    "gemini-3.1-flash-lite-preview",
                    "gemini-2.5-pro",
                    "groq/meta-llama/llama-4-scout-17b-16e-instruct",
                    "gemini-2.5-flash",
                    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
                    "openrouter/mistralai/mistral-small-3.1-24b-instruct:free",
                    "openrouter/arcee-ai/trinity-large-preview:free",
                ]

                if is_quota_error:
                    try:
                        # Déterminer quel modèle a déclenché le 429
                        exhausted_model = None
                        for candidate in _FALLBACK_CHAIN:
                            bare = candidate.split("/")[-1]
                            if candidate in err_msg or bare in err_msg:
                                exhausted_model = candidate
                                break

                        if exhausted_model is None:
                            for _cn in ["Kaelen", "Elara", "Thorne", "Lyra"]:
                                _m = get_agent_config(_cn).get("model", "")
                                if _m and any(kw in err_msg for kw in ["gemini", "groq", "llama", "arcee"]):
                                    exhausted_model = _m
                                    break

                        next_model = None
                        if exhausted_model and exhausted_model in _FALLBACK_CHAIN:
                            idx = _FALLBACK_CHAIN.index(exhausted_model)
                            if idx + 1 < len(_FALLBACK_CHAIN):
                                next_model = _FALLBACK_CHAIN[idx + 1]

                        if next_model:
                            switched = []
                            # Source de vérité : campaign_state → on y écrit en premier
                            try:
                                state = load_state()
                                for _cn in ["Kaelen", "Elara", "Thorne", "Lyra"]:
                                    current = (_char_state.get(_cn, {}).get("llm", "")
                                               or get_agent_config(_cn).get("model", ""))
                                    if current == exhausted_model:
                                        state.setdefault("characters", {}).setdefault(_cn, {})["llm"] = next_model
                                        switched.append(_cn)
                                if switched:
                                    save_state(state)
                            except Exception as _se:
                                print(f"[Auto-Fallback] Erreur écriture campaign_state : {_se}")

                            # Synchronisation app_config (secondaire)
                            if switched:
                                try:
                                    cfg = APP_CONFIG
                                    for _cn in switched:
                                        cfg.setdefault("agents", {}).setdefault(_cn, {})["model"] = next_model
                                    save_app_config(cfg)
                                    reload_app_config()
                                except Exception as _ae:
                                    print(f"[Auto-Fallback] Erreur écriture app_config : {_ae}")
                                print(f"[Auto-Fallback] {exhausted_model} → {next_model} pour : {switched}")
                                self.msg_queue.put({
                                    "sender": "⚠️ Système (Auto-Fallback)",
                                    "text": (
                                        f"⚡ Quota épuisé : {exhausted_model}\n"
                                        f"✅ Basculement automatique → {next_model}\n"
                                        f"Agents concernés : {', '.join(switched)}\n"
                                        f"app_config.json et campaign_state.json mis à jour.\n"
                                        f"Tapez un message pour reprendre (historique conservé)."
                                    ),
                                    "color": "#FF9800",
                                })
                            else:
                                self.msg_queue.put({
                                    "sender": "⚠️ Système (Quota)",
                                    "text": (
                                        f"⚡ Quota épuisé ({exhausted_model or 'modèle inconnu'}) "
                                        f"mais aucun agent ne l'utilisait directement.\n"
                                        f"Le fallback automatique d'AutoGen a dû prendre le relais.\n"
                                        f"Tapez un message pour reprendre."
                                    ),
                                    "color": "#FF9800",
                                })
                        else:
                            self.msg_queue.put({
                                "sender": "⚠️ Système (Quota total)",
                                "text": (
                                    f"❌ Tous les modèles de la chaîne de fallback sont épuisés.\n"
                                    f"Dernier modèle tenté : {exhausted_model or 'inconnu'}\n"
                                    f"💡 Attendez la réinitialisation des quotas ou ajoutez "
                                    f"une clé API supplémentaire dans .env."
                                ),
                                "color": "#F44336",
                            })

                    except Exception as switch_err:
                        print(f"[Auto-Fallback] Erreur basculement : {switch_err}")
                        self.msg_queue.put({
                            "sender": "⚠️ Système (Auto-Fallback)",
                            "text": (
                                f"❌ Quota épuisé ET échec du basculement automatique.\n"
                                f"Détail : {err_msg}\n\n"
                                f"Erreur interne : {switch_err}\n"
                                f"💡 Modifiez manuellement le modèle dans app_config.json."
                            ),
                            "color": "#F44336",
                        })
                else:
                    # Autre type d'erreur — message générique
                    self.msg_queue.put({
                        "sender": "⚠️ Système (Crash IA)",
                        "text": (
                            "❌ L'IA a rencontré une erreur fatale et tous les modèles de secours ont échoué.\n"
                            f"Détail : {err_msg}\n\n"
                            "💡 CONSEIL : Si c'est un problème de Quota (ex: 429), attendez quelques minutes ou changez les modèles/clés API dans le fichier .env.\n"
                            "L'application est toujours active. Tapez un nouveau message pour relancer la partie (l'historique est conservé)."
                        ),
                        "color": "#F44336"
                    })

                # On attend une nouvelle entrée du MJ pour retenter
                self._set_waiting_for_mj(True)
                premier_message = self.wait_for_input()
                self._set_waiting_for_mj(False)
                clear_hist = False  # On ne vide pas l'historique pour reprendre là où ça a crashé