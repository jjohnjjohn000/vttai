"""
session_mixin.py — Gestion du cycle de vie des sessions D&D.

Fournit SessionMixin à injecter dans DnDApp :
  - trigger_save            : sauvegarde rapide (bouton 💾)
  - trigger_end_session     : fin de session propre
  - _generate_and_save_summary : génération du résumé via le Chroniqueur IA
  - _reset_for_new_session  : réinitialisation du chat + état pour repartir

Toutes ces méthodes supposent que l'instance hôte expose :
  self.msg_queue, self.groupchat, self.chat_display, self.messages_index,
  self.msg_counter, self._agents, self._base_system_msgs,
  self._active_memory_ids, self._contextual_mem_block,
  self._autogen_thread_id, self._llm_running, self._waiting_for_mj,
  self._pending_interrupt_input, self._pending_interrupt_display,
  self.input_event, self.root
"""

import threading

from llm_config   import build_llm_config, _default_model
from app_config   import get_chronicler_config
from state_manager import (
    load_state, get_scene_prompt, get_active_quests_prompt,
    get_memories_prompt_compact, save_session_log, update_summary,
)


class SessionMixin:
    """Mixin pour DnDApp — gestion du cycle de vie des sessions."""

    # ─── Déclencheurs publics (boutons UI) ───────────────────────────────────

    def trigger_save(self):
        self.msg_queue.put({"sender": "Système", "text": "💾 Sauvegarde en cours... Le Chroniqueur IA rédige le résumé...", "color": "#FF9800"})
        threading.Thread(target=self._generate_and_save_summary, args=(False,), daemon=True).start()

    def trigger_end_session(self):
        """Termine la session en cours : résumé → journal → nouvelle session.
        Ne ferme PAS l'application."""
        if not self.groupchat:
            self.msg_queue.put({
                "sender": "Système",
                "text":   "❌ La session n'a pas encore commencé.",
                "color":  "#F44336",
            })
            return
        self.msg_queue.put({
            "sender": "Système",
            "text":   "📖 Fin de session — génération du résumé en cours…",
            "color":  "#FF9800",
        })
        # Interrompre les LLMs s'ils tournent encore
        if self._llm_running and not self._waiting_for_mj:
            self._inject_stop()
        threading.Thread(
            target=self._generate_and_save_summary,
            daemon=True,
        ).start()

    # ─── Génération du résumé ────────────────────────────────────────────────

    def _generate_and_save_summary(self, _legacy_end=False):
        """Génère le résumé de session, le sauvegarde dans session_logs,
        puis réinitialise le chat pour démarrer une nouvelle session.
        Le paramètre _legacy_end est ignoré — conservé pour compatibilité."""
        import autogen  # lazy

        # ── 1. Extraire l'historique du groupchat ────────────────────────────
        chat_history = ""
        if self.groupchat:
            for msg in self.groupchat.messages:
                name    = msg.get("name", "Inconnu")
                content = msg.get("content", "")
                if content and not str(content).startswith("[RÉSULTAT SYSTÈME]"):
                    chat_history += f"{name}: {content}\n"

        if not chat_history.strip():
            self.msg_queue.put({
                "sender": "Système",
                "text":   "⚠ Historique vide — aucun résumé généré. Nouvelle session prête.",
                "color":  "#FF9800",
            })
            self.root.after(0, self._reset_for_new_session)
            return

        # ── 2. Générer le résumé via le Chroniqueur ──────────────────────────
        try:
            _chron     = get_chronicler_config()
            _chron_llm = build_llm_config(
                _chron.get("model", _default_model),
                temperature=_chron.get("temperature", 0.3),
            )
            client = autogen.OpenAIWrapper(config_list=_chron_llm["config_list"])

            state        = load_state()
            old_summary  = state.get("session_summary", "Aucun résumé précédent.")
            scene_txt    = get_scene_prompt()
            quests_txt   = get_active_quests_prompt()
            memories_txt = get_memories_prompt_compact(
                importance_min=_chron.get("memories_importance", 1)
            )

            system_prompt = _chron.get("system_prompt", (
                "Tu es le Chroniqueur IA d'une campagne D&D. "
                "Génère un résumé complet et immersif de la session qui vient de se terminer. "
                "Ce résumé sera archivé dans le journal de campagne et relu au début de la "
                "prochaine session. Inclus : les événements clés, les décisions importantes, "
                "les PNJs rencontrés, les objets trouvés, et les progressions de quête. "
                "Sois précis, vivant, et capture l'esprit de la session. "
                "Écris à la 3e personne comme un chroniqueur historique."
            ))

            user_prompt = (
                f"--- CONTEXTE CAMPAGNE ---\n{old_summary}\n\n"
                f"--- SCÈNE FINALE ---\n{scene_txt}\n\n"
                f"--- QUÊTES ACTIVES ---\n{quests_txt}\n\n"
                f"--- MÉMOIRES CLÉS ---\n{memories_txt}\n\n"
                f"--- TRANSCRIPTION DE LA SESSION ---\n{chat_history}\n\n"
                f"Rédige maintenant le résumé de cette session."
            )

            response = client.create(messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ])
            session_resume = response.choices[0].message.content

        except Exception as e:
            session_resume = (
                f"[Résumé automatique indisponible — erreur Chroniqueur : {e}]\n\n"
                f"Transcription brute :\n{chat_history[:2000]}"
            )
            self.msg_queue.put({
                "sender": "Système",
                "text":   f"⚠ Erreur Chroniqueur : {e} — résumé partiel sauvegardé.",
                "color":  "#FF9800",
            })

        # ── 3. Sauvegarder dans session_logs (section dédiée) ────────────────
        try:
            session_num = save_session_log(session_resume)
            update_summary(session_resume)   # met aussi à jour le résumé global
            self.msg_queue.put({
                "sender": "📖 Chroniqueur",
                "text": (
                    f"✅ Session {session_num} archivée.\n\n"
                    f"─── Résumé ───\n{session_resume}"
                ),
                "color": "#c8b8ff",
            })
        except Exception as e:
            self.msg_queue.put({
                "sender": "Système",
                "text":   f"❌ Erreur sauvegarde journal : {e}",
                "color":  "#F44336",
            })

        # ── 4. Réinitialiser pour la prochaine session ───────────────────────
        self.root.after(1500, self._reset_for_new_session)

    # ─── Réinitialisation ────────────────────────────────────────────────────

    def _reset_for_new_session(self):
        """Réinitialise le chat et l'état de session pour repartir à zéro
        sans fermer l'application. Appelé depuis le thread Tk (root.after)."""

        # ── Vider le chat ──────────────────────────────────────────────────
        self.chat_display.config(state="normal")
        self.chat_display.delete("1.0", "end")
        self.chat_display.config(state="disabled")
        self.messages_index.clear()
        self.msg_counter = 0

        # ── Réinitialiser l'état de session ───────────────────────────────
        self.groupchat             = None
        self._agents               = {}
        self._base_system_msgs     = {}
        self._active_memory_ids    = set()
        self._contextual_mem_block = ""
        self._autogen_thread_id    = None
        self._llm_running          = False
        self._waiting_for_mj       = False
        self._pending_interrupt_input   = None
        self._pending_interrupt_display = None
        self.input_event.clear()

        # ── Relancer autogen dans un nouveau thread ────────────────────────
        self.msg_queue.put({
            "sender": "Système",
            "text":   "🌅 Nouvelle session prête. À vous de lancer la partie !",
            "color":  "#4CAF50",
        })
        self.root.after(500, lambda: threading.Thread(
            target=self.run_autogen, daemon=True, name="autogen-worker"
        ).start())
