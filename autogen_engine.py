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

from llm_config    import build_llm_config, _default_model, StopLLMRequested
from app_config    import (get_agent_config, get_chronicler_config,
                           get_groupchat_config, get_memories_config)
from state_manager import (
    load_state, save_state, get_npcs,
    use_spell_slot, update_hp,
    get_scene_prompt, get_active_quests_prompt,
    get_memories_prompt_compact, get_calendar_prompt,
    get_session_logs_prompt, get_active_characters,
)
from agent_logger  import log_tts_start
from combat_tracker import COMBAT_STATE, _is_fully_silenced
from combat_map_panel import get_map_prompt


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

        # ── Chargement des configs LLM par personnage ─────────────────────────
        _char_state = load_state().get("characters", {})
        def _cfg(char_name: str) -> dict:
            # Priorité : app_config > campaign_state > défaut env
            ac = get_agent_config(char_name)
            model = ac.get("model") or _char_state.get(char_name, {}).get("llm", _default_model)
            temp  = ac.get("temperature", 0.7)
            return build_llm_config(model, temperature=temp)

        def _provider_label(char_name: str) -> str:
            ac = get_agent_config(char_name)
            model = ac.get("model") or _char_state.get(char_name, {}).get("llm", _default_model)
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
            "\n  3. Ton rôle : narrer l'effet en 1-2 phrases de roleplay fidèles au résultat."
            "\n  4. NE JAMAIS appeler roll_dice, use_spell_slot, update_hp toi-même."
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
            "\n\n▶ MONDE & UNICITÉ"
            "\nN'invente aucun élément qu'Alexis n'a pas établi. Ne répète jamais une question "
            "ou idée déjà exprimée — apporte un angle nouveau ou reste silencieux."
            "\n\n▶ ÉLOCUTION (SYNTHÈSE VOCALE)"
            "\nRépliques : 1-2 phrases MAX, courtes et percutantes. Ponctuation forte (?, !). "
            "Zéro tirade. Parle comme en pleine action."
            "\n═══════════════════════════════════════════\n"
        )

        kaelen_agent = autogen.AssistantAgent(
            name="Kaelen",
            system_message=(
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
                + _regle_outils
            ),
            llm_config=_cfg("Kaelen"),
        )

        elara_agent = autogen.AssistantAgent(
            name="Elara",
            system_message=(
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
                + _regle_outils
            ),
            llm_config=_cfg("Elara"),
        )

        thorne_agent = autogen.AssistantAgent(
            name="Thorne",
            system_message=(
                "Tu es Thorne, un Voleur (Assassin) Tieffelin de niveau 15, cynique et pragmatique.\n"
                "PERSONNALITÉ : Tu vois le monde en termes de risques, de profits et de qui manipule qui. "
                "Tes questions portent sur les motivations cachées, les pièges potentiels, ce qu'on ne te dit pas, "
                "et ce que rapporte concrètement la mission. Tu es sarcastique et tu n'accordes ta confiance à personne. "
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
                + _regle_outils
            ),
            llm_config=_cfg("Thorne"),
        )

        lyra_agent = autogen.AssistantAgent(
            name="Lyra",
            system_message=(
                "Tu es Lyra, une Clerc (Domaine de la Vie) Demi-Elfe de niveau 15, bienveillante et implacable.\n"
                "PERSONNALITÉ : Tu penses d'abord aux innocents qui souffrent, à la dimension spirituelle et divine "
                "des événements, et à ce que les dieux pourraient vouloir ici. Tu poses des questions sur les victimes, "
                "la souffrance des gens ordinaires, les signes divins, et ce que signifie moralement la situation. "
                "Tu parles avec un accent québécois."
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
                + _regle_outils
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
        # Kaelen et Thorne : combat (dés + sorts uniquement, pas de soins)
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
        autogen.agentchat.register_function(
            update_hp, caller=lyra_agent, executor=mj_agent,
            name="update_hp",
            description="Modifier les PV d'un personnage (- pour dégâts, + pour soin). À appeler UNIQUEMENT si le MJ valide le soin."
        )

        # --- SÉLECTEUR DE SPEAKER COMBAT-AWARE ---
        # PLAYER_NAMES est recalculé dynamiquement à chaque appel pour tenir compte
        # des personnages activés/désactivés en cours de session.
        _ALL_PLAYER_NAMES = ["Kaelen", "Elara", "Thorne", "Lyra"]
        _app_ref = self   # référence pour les closures

        def combat_speaker_selector(last_speaker, groupchat):
            """
            Hors combat : sélection auto normale.
            En combat : les agents hors-tour qui ont déjà réagi sont exclus.
            Les agents inactifs (hors scène) sont toujours exclus.
            """
            # Recalcul dynamique des joueurs actifs (peut changer entre sessions)
            PLAYER_NAMES = get_active_characters()

            # Recalcul des actifs en temps réel (peut changer pendant la session)
            _currently_active = get_active_characters()

            if not COMBAT_STATE["active"]:
                # Hors combat : exclure les agents désactivés en cours de session
                _eligible_hc = [
                    a for a in groupchat.agents
                    if a.name not in PLAYER_NAMES or a.name in _currently_active
                ]
                if len(_eligible_hc) != len(groupchat.agents):
                    original_agents = groupchat.agents
                    groupchat.agents = _eligible_hc
                    groupchat.agents = original_agents
                    return _eligible_hc  # AutoGen utilisera cette liste restreinte
                return "auto"

            eligible = [
                a for a in groupchat.agents
                if (not _is_fully_silenced(a.name) or a.name not in PLAYER_NAMES)
                and (a.name not in PLAYER_NAMES or a.name in _currently_active)
            ]
            # Garde au minimum le MJ + l'agent actif
            if not eligible:
                eligible = [a for a in groupchat.agents
                            if a.name == "Alexis_Le_MJ"]
            # Retire temporairement les agents silenciés de groupchat.agents
            # pour forcer "auto" à les ignorer
            original_agents = groupchat.agents
            groupchat.agents = eligible
            result = "auto"   # signale à AutoGen d'utiliser sa sélection LLM parmi eligible
            groupchat.agents = original_agents
            return result

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
            r'tu\s+(?:te\s+)?(?:prend[s]?|subis|re[çc]ois|perds?)\s+(?P<dmg_d>\d+)\s*(?:d[eé]g[aâ]ts?|points?\s*de\s*d[eé]g[aâ]ts?|PV|pv|hp)'
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

        def patched_receive(self_mgr, message, sender, request_reply=None, silent=False):
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

            # ── FILTRE INACTIF : agent désactivé en cours de session ───────
            # Si le personnage n'est plus dans la scène, on ignore son message
            # sans l'injecter dans le contexte autogen.
            if name in PLAYER_NAMES and name not in get_active_characters():
                return  # silence total — pas de feedback, pas d'injection

            # ── RÉPONSE À JET DEMANDÉ PAR LE MJ : exemptée de toutes les restrictions ──
            # Quand le MJ demande un jet (dégâts, attaque, sauvegarde, soin…), l'agent
            # doit pouvoir exécuter l'appel d'outil sans que ça lui coûte une ressource
            # hors-tour, même s'il est silencieux ou que ce n'est pas son tour.
            _FREE_TOOLS = frozenset({"roll_dice", "update_hp", "use_spell_slot"})
            is_mj_roll_response = False
            if tool_calls and isinstance(tool_calls, list):
                for _tc in tool_calls:
                    _fn_name = (
                        _tc.get("function", {}).get("name")
                        if isinstance(_tc, dict)
                        else getattr(getattr(_tc, "function", None), "name", None)
                    )
                    if _fn_name in _FREE_TOOLS:
                        is_mj_roll_response = True
                        break

            # ── FILTRE COMBAT : PJ hors-tour tente une action ──────────────
            # Bloque tout [ACTION] ou tentative d'action physique si ce n'est
            # pas le tour du PJ. Autorise uniquement : réaction D&D, parole brève.
            _is_offturn_action = (
                not is_system
                and not is_mj_roll_response
                and COMBAT_STATE["active"]
                and name in PLAYER_NAMES
                and name != COMBAT_STATE.get("active_combatant")
                and content
                and _action_pattern.search(str(content))
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
                    _res["confirmed"]    = confirmed
                    _res["actual_level"] = actual_level
                    _ev.set()

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
                    _app.msg_queue.put({"sender": name, "text": clean_content,
                                        "color": _app.CHAR_COLORS.get(name, "#e0e0e0")})
                    log_tts_start(name, clean_content)
                    _app.audio_queue.put((clean_content, name))

                # Bloque jusqu'à la décision du MJ (max 5 min)
                _spell_confirm_event.wait(timeout=300)

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
                    _app.msg_queue.put({
                        "sender": name,
                        "text":   clean_content,
                        "color":  _app.CHAR_COLORS.get(name, "#e0e0e0"),
                    })
                    log_tts_start(name, clean_content)
                    _app.audio_queue.put((clean_content, name))

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
                        _res["confirmed"] = confirmed
                        _res["mj_note"]   = mj_note
                        _ev.set()

                    _pre_is_spell = any(
                        k in _sub["regle"].lower() or k in _sub["intention"].lower()
                        for k in ("sort","magie","incant","boule","projectile",
                                  "éclair","feu","soin","soigne","heal","cure",
                                  "guéri","restaure","parole","dard","rayon",
                                  "projectile magique","missile")
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
                                _res["hit"]  = hit
                                _res["note"] = mj_note_hit
                                _ev.set()

                            _app.msg_queue.put({
                                "action":          "result_confirm",
                                "char_name":       name,
                                "type_label":      _sub["type_label"],
                                "results_text":    _atk_data["atk_text"],
                                "mode":            "attack",
                                "resume_callback": _hit_cb,
                            })
                            _hit_ev.wait(timeout=600)

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
                                _app.msg_queue.put({
                                    "action":          "result_confirm",
                                    "char_name":       name,
                                    "type_label":      _sm_candidate["label"],
                                    "results_text":    _smite_txt,
                                    "mode":            "smite",
                                    "resume_callback": _smite_cb,
                                })
                                _smite_ev.wait(timeout=600)

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
                                _res["note"] = mj_note_dmg
                                _ev.set()

                            _dmg_part = (
                                _dmg_feedback
                                .split("\n\n[INSTRUCTION NARRATIVE]")[0]
                                .replace("[RÉSULTAT SYSTÈME — DÉGÂTS CONFIRMÉS PAR MJ]\n","")
                                .strip()
                            )
                            _app.msg_queue.put({
                                "action":          "result_confirm",
                                "char_name":       name,
                                "type_label":      _sub["type_label"],
                                "results_text":    _dmg_part,
                                "mode":            "damage",
                                "resume_callback": _dmg_cb,
                            })
                            _dmg_ev.wait(timeout=600)

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
                                    _res["hit"]  = hit
                                    _res["note"] = mj_note_res
                                    _ev.set()
                            else:
                                def _result_cb(mj_note_res="",
                                               _ev=_result_ev, _res=_result_note):
                                    _res["note"] = mj_note_res
                                    _ev.set()

                            # Attaque de sort → carte Touché/Raté (mode="attack")
                            # Autre action → carte Continuer (mode="damage")
                            _result_mode = "attack" if _is_spell_attack else "damage"

                            _app.msg_queue.put({
                                "action":          "result_confirm",
                                "char_name":       name,
                                "type_label":      _sub["type_label"],
                                "results_text":    _results_part,
                                "mode":            _result_mode,
                                "resume_callback": _result_cb,
                            })
                            _result_ev.wait(timeout=600)

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

            # ── DÉGÂTS MJ → héros : mise à jour HP automatique ───────────────
            # Quand le MJ annonce qu'un héros prend des dégâts, on appelle
            # update_hp() immédiatement et on injecte un [RÉSULTAT SYSTÈME]
            # pour que le personnage concerné (et les autres) le voient.
            if (not is_system
                    and name == "Alexis_Le_MJ"
                    and content
                    and _damage_pattern.search(str(content))):
                from state_manager import update_hp as _update_hp
                _dmg_hits = []
                _content_str_dmg = str(content)
                _ctx_names = list(dict.fromkeys(
                    m.group(1).capitalize() for m in _PC_NAME_RE.finditer(_content_str_dmg)
                ))
                _ctx_target_d = _ctx_names[0] if len(_ctx_names) == 1 else None
                for _m in _damage_pattern.finditer(_content_str_dmg):
                    if _m.group("tgt_a") and _m.group("dmg_a"):
                        _dmg_hits.append((_m.group("tgt_a"), int(_m.group("dmg_a"))))
                    elif _m.group("tgt_b") and _m.group("dmg_b"):
                        _dmg_hits.append((_m.group("tgt_b"), int(_m.group("dmg_b"))))
                    elif _m.group("tgt_c") and _m.group("dmg_c"):
                        _dmg_hits.append((_m.group("tgt_c"), int(_m.group("dmg_c"))))
                    elif _m.group("dmg_d") and _ctx_target_d:
                        _dmg_hits.append((_ctx_target_d, int(_m.group("dmg_d"))))
                for _tgt, _dmg in _dmg_hits:
                    _hp_result = _update_hp(_tgt, -_dmg)
                    _feedback = (
                        f"[RÉSULTAT SYSTÈME — DÉGÂTS]\n"
                        f"{_hp_result}\n\n"
                        f"[INSTRUCTION NARRATIVE]\n"
                        f"{_tgt}, narre en 1-2 phrases comment tu encaisses ou réagis "
                        f"à ces {_dmg} dégâts. Pas de chiffres — décris la douleur, "
                        f"le choc, ta posture de combat."
                    )
                    _app.msg_queue.put({
                        "sender": "⚙️ Système",
                        "text":   _feedback,
                        "color":  "#ef9a9a",
                    })
                    # Rafraîchit l'UI des stats de personnage si disponible
                    try:
                        _app.root.after(0, _app._refresh_char_stats)
                    except Exception:
                        pass
                    # Sync tracker de combat si ouvert
                    try:
                        if _app._combat_tracker is not None:
                            _app.root.after(0, _app._combat_tracker.sync_pc_hp_from_state)
                    except Exception:
                        pass
                    # Injecte dans le contexte autogen pour que les agents voient la MAJ
                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": _feedback, "name": "Alexis_Le_MJ"},
                        sender,
                        request_reply=False,
                        silent=True,
                    )

            # ── DEMANDE DE JET DU MJ → héros : exécution automatique ────────────
            # Quand le MJ demande explicitement un jet à un personnage joueur
            # ("Kaelen, lance tes dés de dégâts", "Thorne, attaque !", etc.),
            # on l'exécute immédiatement en Python et on injecte le résultat.
            # Patterns : "Lance X", "Roule X", "Jet de X", "Roll X", nom + verbe de jet
            _MJ_DICE_REQUEST = _re.compile(
                r"(?P<char>Kaelen|Elara|Thorne|Lyra)"
                r"[^.!?\n]{0,40}"
                r"(?:lance|lancer|roule|rouler|fais?|effectue?|tire|roll|jet\s+de?|dégâts?|damage|attaque\s+(?:de\s+)?(?:dégâts?)?)"
                r"[^.!?\n]{0,60}"
                r"(?P<dice>\d+d\d+(?:\s*[+\-]\s*\d+)?)"
                r"|"
                r"(?P<char2>Kaelen|Elara|Thorne|Lyra)"
                r"[^.!?\n]{0,40}"
                r"(?:lance|roule|fais?|effectue?|tire|roll)\s+"
                r"(?:tes?|les?|des?)?\s*"
                r"(?:dés?|dégâts?|damage|attaque|jet|roll)"
                r"|"
                r"(?:lance|roule|roll|jet\s+de?)\s+"
                r"(?:tes?|les?|des?|ses?)?\s*"
                r"(?:dés?\s+de\s+)?(?:dégâts?|damage)"
                r"[^.!?\n]{0,20}"
                r"(?P<char3>Kaelen|Elara|Thorne|Lyra)"
                ,
                _re.IGNORECASE
            )
            if (not is_system
                    and name == "Alexis_Le_MJ"
                    and content
                    and _MJ_DICE_REQUEST.search(str(content))):
                from state_manager import roll_dice as _roll_dice_auto
                _mj_content = str(content)
                for _m in _MJ_DICE_REQUEST.finditer(_mj_content):
                    _char = (_m.group("char") or _m.group("char2") or _m.group("char3") or "").strip()
                    if not _char:
                        continue
                    _dice_str = (_m.group("dice") if "dice" in _m.groupdict() and _m.group("dice") else None)

                    if _dice_str:
                        # Dice formula explicite dans la demande → on l'utilise
                        _d_parts = _re.match(r"(\d+)d(\d+)(?:\s*([+\-]\s*\d+))?", _dice_str)
                        if _d_parts:
                            _dn, _df = int(_d_parts.group(1)), int(_d_parts.group(2))
                            _db = int((_d_parts.group(3) or "0").replace(" ", "")) if _d_parts.group(3) else 0
                            _dice_formula = f"{_dn}d{_df}"
                            _result = _roll_dice_auto(_char, _dice_formula, _db)
                        else:
                            continue
                    else:
                        # Pas de formule explicite → dés de dégâts par défaut du personnage
                        _cm = _CHAR_MECHANICS.get(_char, {})
                        _dn, _df, _db = _cm.get("dmg_melee", (1, 8, 0))
                        _dice_formula = f"{_dn}d{_df}"
                        _result = _roll_dice_auto(_char, _dice_formula, _db)

                    _auto_feedback = (
                        f"[RÉSULTAT SYSTÈME — JET AUTOMATIQUE]\n"
                        f"{_char} → {_dice_formula} : {_result}"
                    )
                    _app.msg_queue.put({
                        "sender": f"🎲 Dés ({_char})",
                        "text":   _result,
                        "color":  "#4fc3f7",
                    })
                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": _auto_feedback, "name": "Alexis_Le_MJ"},
                        sender,
                        request_reply=False,
                        silent=True,
                    )
                    break  # un seul jet auto par message MJ

            # Appel normal
            _original_receive(self_mgr, message, sender, request_reply, silent)

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
            if (not is_system
                    and not is_mj_roll_response
                    and COMBAT_STATE["active"]
                    and name in PLAYER_NAMES
                    and name != COMBAT_STATE.get("active_combatant")
                    and content
                    and str(content).strip() != "[SILENCE]"):

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
                    _app.msg_queue.put({"sender": display_name, "text": content, "color": color})
                    if not is_system and display_name in PLAYER_NAMES:
                        log_tts_start(display_name, str(content))
                        _app.audio_queue.put((content, display_name))

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

                # ── Détection quota gemini-2.5-pro → bascule auto vers flash ──────
                if is_quota_error and "gemini-2.5-pro" in err_msg:
                    try:
                        state = load_state()
                        switched = []
                        for char_name, char_data in state.get("characters", {}).items():
                            if char_data.get("llm", "") == "gemini-2.5-pro":
                                state["characters"][char_name]["llm"] = "gemini-2.5-flash"
                                switched.append(char_name)
                        if switched:
                            save_state(state)
                            self.msg_queue.put({
                                "sender": "⚠️ Système (Auto-Fallback)",
                                "text": (
                                    f"⚡ Quota Gemini Pro épuisé pour aujourd'hui.\n"
                                    f"✅ Basculement automatique vers gemini-2.5-flash pour : {', '.join(switched)}.\n"
                                    f"Les modèles ont été mis à jour dans campaign_state.json.\n"
                                    f"Tapez un nouveau message pour reprendre (l'historique est conservé)."
                                ),
                                "color": "#FF9800"
                            })
                    except Exception as switch_err:
                        print(f"[Auto-Fallback] Erreur lors du basculement : {switch_err}")
                        self.msg_queue.put({
                            "sender": "⚠️ Système (Crash IA)",
                            "text": (
                                "❌ Quota Gemini Pro épuisé ET échec du basculement automatique.\n"
                                f"Détail : {err_msg}\n\n"
                                "💡 Changez manuellement 'gemini-2.5-pro' → 'gemini-2.5-flash' dans campaign_state.json\n"
                                "puis relancez l'application."
                            ),
                            "color": "#F44336"
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