"""
llm_control_mixin.py — Contrôle LLM, envoi de messages et commandes MJ.

Fournit LLMControlMixin à injecter dans DnDApp :
  - _inject_stop            : interrompt le thread autogen via ctypes
  - _set_llm_running        : met à jour l'indicateur LLM actif
  - _set_waiting_for_mj     : met à jour l'indicateur "tour du MJ"
  - stop_llms               : bouton ⏹ — arrêt immédiat
  - send_text               : traite l'entrée texte du MJ (+ commandes /vote, /msg)
  - _send_private_message   : message secret MJ → un seul agent (bypass groupchat)
  - _run_vote               : vote simultané sur tous les agents joueurs
  - _execute_skill_check    : jet de compétence direct (séparé de la narration)

Prérequis sur l'instance hôte :
  self.msg_queue, self.audio_queue, self.entry, self._agents, self.groupchat,
  self._llm_running, self._waiting_for_mj, self._autogen_thread_id,
  self._pending_interrupt_input, self._pending_interrupt_display,
  self.user_input, self.input_event, self.active_npc,
  self._SKILL_MODIFIERS (dict)
"""

import ctypes
import threading
import concurrent.futures as _cf

import tkinter as tk

from llm_config   import StopLLMRequested, build_llm_config, _SSL_LOCK
from state_manager import roll_dice, load_state
from agent_logger  import log_llm_start, log_llm_end, log_tts_start
from state_manager import get_active_characters
from chat_log_writer import strip_mechanical_blocks

# ─── Verrou global SSL/httpx ──────────────────────────────────────────────────
# _SSL_LOCK est importé depuis llm_config.py — objet UNIQUE partagé avec
# autogen_engine.py pour sérialiser tous les appels httpx/OpenSSL.
_DIRECT_LLM_LOCK = _SSL_LOCK   # alias pour compatibilité avec le code existant


class LLMControlMixin:
    """Mixin pour DnDApp — contrôle des LLMs et interactions MJ."""

    # ─── Contrôle du thread autogen ──────────────────────────────────────────

    def _inject_stop(self):
        import time as _time

        # ── 0. Annuler toutes les confirmations MJ en attente ─────────────────
        # Les threading.Event de autoriser/refuser sont bloqués dans .wait(600).
        # On les débloque avant l'injection ctypes pour que le thread autogen
        # sorte immédiatement du .wait() et reçoive l'exception async.
        self._cancel_pending_approvals()

        # ── Mécanisme principal : _stop_event sondé dans _make_thinking_wrapper ──
        try:
            self._stop_event.set()
        except Exception:
            pass

        # ── Fallback : ctypes async exception ─────────────────────────────────
        tid = self._autogen_thread_id
        if tid:
            res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(tid), ctypes.py_object(StopLLMRequested))
            if res > 1:
                ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid), None)

        # ── Filet de sécurité : débloque l'UI si le thread ne répond pas ──────
        def _force_unblock():
            _time.sleep(5.0)
            if self._llm_running and not self._waiting_for_mj:
                self._set_llm_running(False)
                self.msg_queue.put({
                    "sender": "⚠️ Système",
                    "text": "Thread LLM non réactif après 5 s — débloqué de force. Tapez un message pour reprendre.",
                    "color": "#FF9800",
                })
                self._set_waiting_for_mj(True)
        threading.Thread(target=_force_unblock, daemon=True).start()

    # ─── Gestion des events d'approbation MJ ─────────────────────────────────

    def _register_approval_event(self, ev):
        """Enregistre un threading.Event de confirmation MJ (autoriser/refuser)."""
        with self._approval_events_lock:
            self._pending_approval_events.append(ev)

    def _unregister_approval_event(self, ev):
        """Retire un event de la liste après résolution (callback appelé)."""
        with self._approval_events_lock:
            try:
                self._pending_approval_events.remove(ev)
            except ValueError:
                pass

    def _cancel_pending_approvals(self):
        """Débloque tous les .wait() de confirmation MJ en cours.
        Appelé par _inject_stop et _inject_stop_for_pause."""
        with self._approval_events_lock:
            if self._pending_approval_events:
                count = len(self._pending_approval_events)
                print(f"[Stop] Annulation de {count} confirmation(s) MJ en attente.")
                for ev in self._pending_approval_events:
                    ev.set()
                self._pending_approval_events.clear()
                self.msg_queue.put({
                    "sender": "⏹ Système",
                    "text": f"{count} action(s) en attente d'approbation annulée(s) — interruption reçue.",
                    "color": "#FF9800",
                })

    def _set_llm_running(self, running: bool):
        self._llm_running = running
        # root.after() n'est pas thread-safe sur Linux — on passe par msg_queue
        self.msg_queue.put({"action": "set_llm_running", "value": running})

    def _set_waiting_for_mj(self, waiting: bool):
        """Active/désactive l'indicateur 'tour du MJ' et met à jour le bouton Stop."""
        self._waiting_for_mj = waiting
        # root.after() n'est pas thread-safe sur Linux — on passe par msg_queue
        self.msg_queue.put({"action": "set_waiting_for_mj", "value": waiting})

    def stop_llms(self):
        if not self._llm_running or self._waiting_for_mj:
            return
        self.msg_queue.put({"sender": "⏹ Système", "text": "Interruption demandée — LLMs arrêtés. Tapez un message pour reprendre.", "color": "#FF9800"})
        self._inject_stop()

    # ─── Envoi de texte (entrée MJ) ──────────────────────────────────────────

    def send_text(self):
        # ── Bloqué pendant la pause — aucun message ne doit atteindre les agents ──
        if getattr(self, '_session_paused', False):
            self.msg_queue.put({
                "sender": "⏸ Session",
                "text": "Session en pause — message non transmis aux agents. Appuyez sur ▶ Reprendre.",
                "color": "#e67e22",
            })
            return
        text = self.entry.get().strip()
        self.entry.delete(0, tk.END)
        if self._llm_running and not self._waiting_for_mj:
            if not text:
                self.stop_llms()
                return
            npc = self.active_npc
            if npc:
                formatted = f"[{npc['name']}] : {text}"
                display = {"sender": f"🎭 {npc['name']}", "text": text, "color": npc.get("color", "#c77dff")}
            else:
                formatted = text
                display = {"sender": "Alexis_Le_MJ", "text": text, "color": "#4CAF50"}
            # Stocke le message à afficher APRÈS l'arrêt — le except StopLLMRequested le postera
            # Si une interruption est déjà en cours, juste remplacer le message en attente
            if self._pending_interrupt_input is not None:
                self._pending_interrupt_input = formatted
                self._pending_interrupt_display = display
                return
            self._pending_interrupt_input = formatted
            self._pending_interrupt_display = display
            self.msg_queue.put({"sender": "⏹ Système", "text": "LLMs interrompus — reprise avec votre nouveau message.", "color": "#FF9800"})
            self._inject_stop()   # pas de with_input ici — géré par _pending_interrupt_display
            return
        if self.input_event.is_set():
            return
        # ── Détection commande /vote choix1 choix2 ... ───────────────────────
        import re as _re_msg
        _pv = _re_msg.match(r'^/vote\s+(.+)$', text, _re_msg.IGNORECASE)
        if _pv:
            raw_choices = _pv.group(1).strip()
            choices = [c.strip() for c in _re_msg.split(r'\s+', raw_choices) if c.strip()]
            if len(choices) < 2:
                self.msg_queue.put({"sender": "⚠️ Système",
                                    "text": "Usage : /vote choix_1 choix_2 [choix_3 ...]",
                                    "color": "#FF9800"})
                return
            if not self._agents:
                self.msg_queue.put({"sender": "⚠️ Système",
                                    "text": "Agents non initialisés — lancez la partie d'abord.",
                                    "color": "#FF9800"})
                return
            threading.Thread(target=self._run_vote, args=(choices,), daemon=True).start()
            return

        # ── Détection commande /msg NomPersonnage texte... ────────────────────
        _pm = _re_msg.match(r'^/msg\s+(\S+)\s+(.+)$', text, _re_msg.IGNORECASE)
        if _pm:
            target_raw = _pm.group(1)
            private_text = _pm.group(2).strip()
            if not self._agents:
                self.msg_queue.put({"sender": "⚠️ Système", "text": "Agents non initialisés — lancez la partie d'abord.", "color": "#FF9800"})
                return
            real_name = next((n for n in self._agents if n.lower().startswith(target_raw.lower())), None)
            if real_name is None:
                self.msg_queue.put({
                    "sender": "⚠️ Système",
                    "text": f"Personnage '{target_raw}' introuvable. Valides : {', '.join(self._agents.keys())}",
                    "color": "#FF9800"
                })
                return
            threading.Thread(target=self._send_private_message, args=(real_name, private_text), daemon=True).start()
            return
        # ── Enter vide → parole spontanée ────────────────────────────────
        # [PAROLE_SPONTANEE] est un marqueur reconnu par le sélecteur de
        # speaker dans autogen_engine : il déclenche directement la rotation
        # d'un PJ sans passer par l'analyse du contenu du message MJ.
        if not text:
            self.user_input = "[PAROLE_SPONTANEE]"
            self.input_event.set()
            return

        self.user_input = text
        npc = self.active_npc
        if npc:
            display_name = f"🎭 {npc['name']}"
            color = npc.get("color", "#c77dff")
            self.msg_queue.put({"sender": display_name, "text": text, "color": color})
            self.user_input = f"[{npc['name']}] : {text}"
        else:
            self.msg_queue.put({"sender": "Alexis_Le_MJ", "text": text, "color": "#4CAF50"})
        self.input_event.set()

    # ─── Message privé MJ → agent ────────────────────────────────────────────

    def _send_private_message(self, char_name: str, message: str, inject_groupchat: bool = True):
        """Envoie un message secret directement à un agent (bypass groupchat). Affiché en chat côté MJ."""
        import autogen  # lazy
        # ── Bloqué pendant la pause ────────────────────────────────────────────
        if getattr(self, '_session_paused', False):
            self.msg_queue.put({
                "sender": "⏸ Session",
                "text": f"Session en pause — message privé vers {char_name} annulé.",
                "color": "#e67e22",
            })
            return
        agent = self._agents.get(char_name)
        if agent is None:
            self.msg_queue.put({"sender": "Système", "text": f"❌ Agent {char_name} introuvable.", "color": "#F44336"})
            return

        # ── Affichage côté MJ (message envoyé + indicateur secret) ──────────
        CHAR_COLORS = {"Kaelen": "#e57373", "Elara": "#64b5f6", "Thorne": "#ce93d8", "Lyra": "#81c784"}
        char_color = CHAR_COLORS.get(char_name, "#aaaaaa")
        self.msg_queue.put({
            "sender": f"🔒 MJ → {char_name}",
            "text": message,
            "color": "#888844"
        })

        # ── Prompt : l'agent choisit de répondre au groupe ou en secret ─────
        system_msg = agent.system_message or ""
        prompt = (
            f"[MESSAGE PRIVÉ DU MJ — POUR {char_name.upper()} UNIQUEMENT — LES AUTRES JOUEURS NE VOIENT PAS CECI]\n"
            f"{message}\n\n"
            f"Tu dois choisir comment réagir à cette information. Deux options EXCLUSIVES :\n\n"
            f"Option A — Répondre DIRECTEMENT AU GROUPE (les autres joueurs entendent) :\n"
            f"  Commence ta réponse par : [GROUPE]\n"
            f"  IMPORTANT : C'est le choix par défaut ! Parle à voix haute à tes alliés pour partager tes déductions, tes découvertes ou l'information que tu viens de recevoir. Le jeu de rôle coopératif exige de communiquer.\n\n"
            f"Option B — Répondre SECRÈTEMENT au MJ seulement :\n"
            f"  Commence ta réponse par : [SECRET]\n"
            f"  Utilise ça UNIQUEMENT si l'information concerne un secret lourd, une trahison, ou tes pensées purement internes que tu refuses absolument de dévoiler aux autres.\n\n"
            f"Reste dans le personnage de {char_name}. Réponse courte, en roleplay pur. "
            f"Ne mentionne jamais les balises [GROUPE] ou [SECRET] dans le corps de ta réponse."
        )

        # FIX SEGFAULT SSL : on sérialise tous les appels directs via _DIRECT_LLM_LOCK
        # ET on force la création d'un client httpx isolé (pas de pool partagé)
        # pour éviter la collision OpenSSL entre threads sur Linux/Python 3.10.
        # Itère sur toute la config_list pour reproduire le comportement de fallback
        # d'AutoGen (si le modèle principal retourne 404/429, on tente le suivant).
        import httpx as _httpx
        import openai as _openai

        config_list = agent.llm_config.get("config_list", [])
        temperature = agent.llm_config.get("temperature", 0.7)
        text_content = ""
        last_error = None

        log_llm_start(char_name, prompt, context="msg_privé")
        for cfg in config_list:
            http_client = _httpx.Client()
            try:
                oa_client = _openai.OpenAI(
                    api_key     = cfg["api_key"],
                    base_url    = str(cfg.get("base_url", "https://api.openai.com/v1")),
                    http_client = http_client,
                    default_headers = cfg.get("default_headers", {}),
                )
                with _DIRECT_LLM_LOCK:
                    resp = oa_client.chat.completions.create(
                        model    = cfg["model"],
                        messages = [
                            {"role": "system", "content": system_msg},
                            {"role": "user",   "content": prompt},
                        ],
                        max_tokens  = 800,
                        temperature = temperature,
                    )
                choice = resp.choices[0]
                text_content = (choice.message.content or "").strip()
                finish = getattr(choice, "finish_reason", "?")
                if finish == "length":
                    print(f"[msg_privé] ⚠ {char_name} — finish_reason=length, réponse tronquée par max_tokens")
                log_llm_end(char_name, response_preview=text_content)
                last_error = None
                break  # succès → on sort de la boucle
            except Exception as e:
                last_error = e
                print(f"[msg_privé] {char_name} — modèle {cfg.get('model','?')} échoué : {e}, essai suivant…")
            finally:
                http_client.close()

        if last_error is not None:
            log_llm_end(char_name, error=str(last_error))
            self.msg_queue.put({"sender": "❌ Erreur", "text": f"Échec msg privé pour {char_name} : {last_error}", "color": "#F44336"})
            return

        if not text_content or text_content == "[SILENCE]":
            return

        # ── Analyse du choix de l'agent ──────────────────────────────────────
        if text_content.startswith("[GROUPE]"):
            # L'agent veut parler au groupe directement — on injecte sans demander
            clean_text = text_content[len("[GROUPE]"):].strip()
            self.msg_queue.put({"sender": char_name, "text": clean_text, "color": char_color})
            _tts_groupe = strip_mechanical_blocks(clean_text)
            if _tts_groupe:
                log_tts_start(char_name, _tts_groupe)
                self.audio_queue.put((_tts_groupe, char_name))
            # Injecter dans le groupchat seulement si demandé
            # (inject_groupchat=False depuis le bouton Parler pour éviter le double message)
            if inject_groupchat:
                relayed = f"[{char_name}, s'adressant au groupe] {clean_text}"
                if self._llm_running and not self._waiting_for_mj:
                    self._pending_interrupt_input = relayed
                    self._pending_interrupt_display = None
                    self._inject_stop()
                else:
                    self.user_input = relayed
                    self.input_event.set()

        else:
            # L'agent répond en secret ([SECRET] ou pas de balise reconnue)
            clean_text = text_content[len("[SECRET]"):].strip() if text_content.startswith("[SECRET]") else text_content
            self.msg_queue.put({"sender": f"🔒 {char_name} (privé)", "text": clean_text, "color": char_color})
            # Aucune lecture audio TTS pour les messages privés.
            # Garder le bouton relay : le MJ peut décider de partager au groupe
            self.msg_queue.put({"action": "relay_button", "char_name": char_name, "reply_text": clean_text})

    # ─── Vote de groupe ───────────────────────────────────────────────────────

    def _run_vote(self, choices: list[str]):
        """
        Lance un vote simultané sur tous les agents joueurs.
        Chaque agent choisit parmi les options et justifie brièvement en roleplay.
        Les résultats s'affichent dans le chat avec un récapitulatif.
        """
        import autogen  # lazy
        # ── Bloqué pendant la pause ────────────────────────────────────────────
        if getattr(self, '_session_paused', False):
            self.msg_queue.put({
                "sender": "⏸ Session",
                "text": "Session en pause — vote annulé. Appuyez sur ▶ Reprendre.",
                "color": "#e67e22",
            })
            return
        import re as _re_v

        PLAYER_NAMES = get_active_characters()   # uniquement les héros présents dans la scène
        CHAR_COLORS  = {"Kaelen": "#e57373", "Elara": "#64b5f6",
                        "Thorne": "#ce93d8", "Lyra":  "#81c784"}

        choices_str  = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(choices))
        choices_list = ", ".join(f'"{c}"' for c in choices)

        self.msg_queue.put({
            "sender": "🗳️ Vote",
            "text":   f"Le MJ demande une décision au groupe :\n{choices_str}",
            "color":  "#ffcc00"
        })

        # Interroge chaque agent en parallèle
        def _ask_agent(name):
            agent = self._agents.get(name)
            if not agent:
                return name, None, ""
            system_msg = agent.system_message or ""
            prompt = (
                f"[DÉCISION DU GROUPE — VOTE DU MJ]\n"
                f"Le groupe doit choisir immédiatement sa prochaine action parmi ces options :\n"
                f"{choices_str}\n\n"
                f"Tu dois :\n"
                f"1. Choisir UNE option parmi : {choices_list}\n"
                f"2. Répondre UNIQUEMENT avec ce format exact sur deux lignes :\n"
                f"   VOTE: <option choisie exactement comme écrite>\n"
                f"   RAISON: <une phrase courte en roleplay expliquant ton choix>\n\n"
                f"Ne dévie pas du format. Choisis selon la personnalité de {name}."
            )
            try:
                import httpx as _httpx
                import openai as _openai
                cfg0 = agent.llm_config["config_list"][0]
                http_client = _httpx.Client()
                oa_client = _openai.OpenAI(
                    api_key     = cfg0["api_key"],
                    base_url    = str(cfg0.get("base_url", "https://api.openai.com/v1")),
                    http_client = http_client,
                )
                log_llm_start(name, prompt, context="vote")
                with _DIRECT_LLM_LOCK:
                    resp = oa_client.chat.completions.create(
                        model    = cfg0["model"],
                        messages = [
                            {"role": "system", "content": system_msg},
                            {"role": "user",   "content": prompt},
                        ],
                        max_tokens  = 150,
                        temperature = agent.llm_config.get("temperature", 0.7),
                    )
                raw = (resp.choices[0].message.content or "").strip()
                log_llm_end(name, response_preview=raw)
                http_client.close()
                # Parse VOTE: et RAISON:
                vote_m   = _re_v.search(r'VOTE\s*:\s*(.+)', raw, _re_v.IGNORECASE)
                raison_m = _re_v.search(r'RAISON\s*:\s*(.+)', raw, _re_v.IGNORECASE)
                vote_txt   = vote_m.group(1).strip()   if vote_m   else raw.splitlines()[0]
                raison_txt = raison_m.group(1).strip() if raison_m else ""
                # Normalise le vote vers le choix le plus proche
                best = min(choices, key=lambda c: (
                    0 if c.lower() == vote_txt.lower()
                    else (1 if c.lower() in vote_txt.lower() or vote_txt.lower() in c.lower()
                          else 2)
                ))
                return name, best, raison_txt
            except Exception as e:
                log_llm_end(name, error=str(e))
                return name, None, f"(erreur: {e})"

        with _cf.ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(_ask_agent, n): n for n in PLAYER_NAMES}
            results = {}   # name -> (choice, raison)
            for f in _cf.as_completed(futures):
                name, choice, raison = f.result()
                results[name] = (choice, raison)
                if choice:
                    color = CHAR_COLORS.get(name, "#aaaaaa")
                    self.msg_queue.put({
                        "sender": f"🗳️ {name}",
                        "text":   f"→ **{choice}**" + (f"  —  {raison}" if raison else ""),
                        "color":  color
                    })
                    tts_text = strip_mechanical_blocks(raison or choice)
                    if tts_text:
                        log_tts_start(name, tts_text)
                        self.audio_queue.put((tts_text, name))

        # Décompte
        tally: dict[str, list[str]] = {c: [] for c in choices}
        for name, (choice, _) in results.items():
            if choice and choice in tally:
                tally[choice].append(name)

        # Résumé visuel
        max_votes  = max((len(v) for v in tally.values()), default=0)
        winners    = [c for c, v in tally.items() if len(v) == max_votes and v]
        tally_lines = []
        for c in choices:
            voters = tally[c]
            bar    = "█" * len(voters) + "░" * (len(PLAYER_NAMES) - len(voters))
            marker = " ◀ MAJORITÉ" if c in winners else ""
            tally_lines.append(f"  {bar} {c} ({len(voters)}/{len(PLAYER_NAMES)}){marker}")

        summary = "─── Résultats ───\n" + "\n".join(tally_lines)
        if len(winners) == 1:
            summary += f"\n\n✅ Décision : {winners[0]}"
        else:
            summary += f"\n\n⚖️ Égalité entre : {' / '.join(winners)} — au MJ de trancher."

        self.msg_queue.put({"sender": "🗳️ Vote terminé", "text": summary, "color": "#ffcc00"})

        # Injecte la décision dans le groupchat pour que les agents en soient informés
        if len(winners) == 1:
            inject = f"[RÉSULTAT DU VOTE] Le groupe a décidé : {winners[0]}."
        else:
            inject = f"[RÉSULTAT DU VOTE] Égalité entre {' et '.join(winners)} — le MJ tranchera."
        self.user_input = inject
        self.input_event.set()

    # ─── Jet de compétence ───────────────────────────────────────────────────

    def _execute_skill_check(self, char_name: str, skill: str, ability: str, dc: int | None, reason: str | None = None):
        """Appelle directement l'agent concerné pour un jet de compétence (bypass groupchat).

        FIX : Le system prompt des agents interdit d'appeler roll_dice soi-même (règle 5).
        On sépare donc la narration (agent) et le lancer de dés (Python direct).
        """
        # ── Bloqué pendant la pause ────────────────────────────────────────────
        if getattr(self, '_session_paused', False):
            self.msg_queue.put({
                "sender": "⏸ Session",
                "text": f"Session en pause — jet de compétence de {char_name} annulé.",
                "color": "#e67e22",
            })
            return
        agent = self._agents.get(char_name)
        if agent is None:
            self.msg_queue.put({"sender": "Système", "text": f"❌ Agent {char_name} introuvable.", "color": "#F44336"})
            return

        # ── Récupération du bonus de compétence ──────────────────────────────
        char_mods = self._SKILL_MODIFIERS.get(char_name, {})
        skill_low = skill.lower()
        bonus = (
            char_mods.get("skills", {}).get(skill_low)
            or char_mods.get("saves", {}).get(skill_low)
            or char_mods.get("default_ability", {}).get(ability)
            or 0
        )

        # ── Annonce publique dans le chat ────────────────────────────────────
        dc_txt    = f"  |  DC {dc}" if dc is not None else "  |  DC secret"
        reason_txt = f"  |  {reason}" if reason else ""
        bonus_txt  = f"  |  Bonus {bonus:+d}" if bonus else ""
        announce = f"🎲 Jet de compétence → [{char_name}] : {skill} ({ability}){dc_txt}{bonus_txt}{reason_txt}"
        self.msg_queue.put({"sender": "🎲 MJ", "text": announce, "color": "#ffcc00"})

        # ── Lancer de dés IMMÉDIAT (Python — ne dépend pas de l'agent) ───────
        dice_result = roll_dice(
            character_name=char_name,
            dice_type="1d20",
            bonus=bonus,
        )
        self.msg_queue.put({"sender": f"🎲 Résultat ({char_name})", "text": dice_result, "color": "#4CAF50"})

        # ── Évaluation DC ─────────────────────────────────────────────────────
        if dc is not None:
            try:
                import re as _re
                m = _re.search(r"Total\s*=\s*(\d+)", dice_result)
                if m:
                    total = int(m.group(1))
                    outcome = "✅ Réussite" if total >= dc else "❌ Échec"
                    self.msg_queue.put({
                        "sender": "🎲 MJ (DC secret)",
                        "text": f"{outcome} — Total {total} vs DC {dc}",
                        "color": "#4CAF50" if total >= dc else "#e57373"
                    })
            except Exception:
                pass

        # ── Prompt narration UNIQUEMENT (pas de demande d'appel d'outil) ─────
        reason_instruction = f"\nContexte : {reason}" if reason else ""
        prompt = (
            f"[INSTRUCTION NARRATIVE]\n"
            f"Le système a exécuté la mécanique du sort. "
            f"Narre en 1-2 phrases comment {char_name} incante et l'effet visible sur {reason or 'la cible'}."
            f"{reason_instruction}\n"
            f"Ne mentionne pas les chiffres bruts."
        )

        try:
            reply = agent.generate_reply(
                messages=[{"role": "user", "content": prompt}]
            )
        except Exception as e:
            self.msg_queue.put({"sender": "❌ Erreur", "text": f"Narration impossible pour {char_name} : {e}", "color": "#F44336"})
            return

        # ── Affichage de la narration ─────────────────────────────────────────
        text_content = ""
        if isinstance(reply, str):
            text_content = reply.strip()
        elif isinstance(reply, dict):
            raw = reply.get("content") or ""
            text_content = raw.strip() if isinstance(raw, str) else ""

        if text_content:
            self.msg_queue.put({"sender": char_name, "text": text_content, "color": "#e0e0e0"})
            _tts_skill = strip_mechanical_blocks(text_content)
            if _tts_skill:
                log_tts_start(char_name, _tts_skill)
                self.audio_queue.put((_tts_skill, char_name))