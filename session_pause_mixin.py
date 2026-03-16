"""
session_pause_mixin.py — Pause / Reprise globale de la session D&D.

Fournit SessionPauseMixin à injecter dans DnDApp :
  - toggle_session_pause : bascule pause ↔ reprise (lié au bouton ⏸/▶)
  - _do_pause            : stoppe audio + LLM en cours + combat tracker
  - _do_resume           : relance audio + signale la reprise aux agents
  - _update_pause_button : met à jour le label/couleur du bouton

Ce mixin expose self._session_paused (bool) que les autres composants
peuvent lire pour savoir si la session est suspendue.

Interactions avec les autres composants :
  - voice_interface.pause_audio() / resume_audio() → gère ffplay + chunks TTS
  - self.audio_queue → vidé à la pause pour ne pas rejouer les voix en attente
  - self._inject_stop() (LLMControlMixin) → interrompt le thread autogen si actif
  - self._pending_combat_trigger → préservé, sera consommé à la reprise
  - CombatTrackerMixin._on_pc_combat_turn → vérifie self._session_paused

Prérequis sur l'instance hôte :
  self.msg_queue, self.audio_queue, self.root,
  self._llm_running, self._waiting_for_mj,
  self._autogen_thread_id,
  self.btn_pause (créé dans ui_setup_mixin.py)
  self._inject_stop() (fourni par LLMControlMixin)
"""

import ctypes
import threading

from llm_config import StopLLMRequested


class SessionPauseMixin:
    """Mixin pour DnDApp — pause/reprise globale de la session."""

    # ─── API publique ─────────────────────────────────────────────────────────

    def toggle_session_pause(self):
        """Bascule entre pause et reprise. Appelé par le bouton ⏸/▶."""
        if not getattr(self, "_session_paused", False):
            self._do_pause()
        else:
            self._do_resume()

    # ─── Pause ────────────────────────────────────────────────────────────────

    def _do_pause(self):
        """
        Met la session en pause :
          1. Stoppe et vide toute l'audio (ffplay tué, queue drainée)
          2. Interrompt le LLM en cours (si actif) — le thread autogen
             retombe dans wait_for_input() et attendra la reprise
          3. Marque le flag _session_paused
          4. Met à jour l'UI
        """
        self._session_paused = True

        # ── 1. Arrêt immédiat de l'audio ─────────────────────────────────────
        try:
            from voice_interface import pause_audio
            pause_audio()          # tue ffplay en cours + bloque les prochains chunks
        except Exception as e:
            print(f"[Pause] Erreur arrêt audio : {e}")

        # Vider la file d'attente audio (voix en attente de lecture)
        _drained = 0
        while True:
            try:
                self.audio_queue.get_nowait()
                _drained += 1
            except Exception:
                break
        if _drained:
            print(f"[Pause] {_drained} entrée(s) audio drainée(s)")

        # ── 2. Interrompre le LLM si actif (pas en attente MJ) ───────────────
        self._was_llm_running_at_pause = self._llm_running
        if self._llm_running and not self._waiting_for_mj:
            # Injecte StopLLMRequested dans le thread autogen via ctypes
            # Le thread retombera dans wait_for_input() — pas de perte d'historique
            self._inject_stop_for_pause()

        # ── 3. Feedback UI ────────────────────────────────────────────────────
        self.msg_queue.put({
            "sender": "⏸ Session",
            "text": (
                "Session mise en pause.\n"
                "• Audio stoppé — les voix en attente ont été annulées.\n"
                "• LLMs suspendus — l'historique est conservé.\n"
                "• Combat tracker en attente.\n"
                "Appuyez sur ▶ Reprendre pour continuer."
            ),
            "color": "#e67e22",
        })
        self.root.after(0, self._update_pause_button)

    # ─── Reprise ──────────────────────────────────────────────────────────────

    def _do_resume(self):
        """
        Reprend la session :
          1. Débloque l'audio
          2. Retire le flag de pause
          3. Injecte un message de reprise si le LLM avait été interrompu
             (l'agent reprend le contexte au dernier point connu)
          4. Met à jour l'UI
        """
        self._session_paused = False

        # ── 1. Débloquer l'audio ──────────────────────────────────────────────
        try:
            from voice_interface import resume_audio
            resume_audio()
        except Exception as e:
            print(f"[Reprise] Erreur reprise audio : {e}")

        # ── 2. Feedback UI ────────────────────────────────────────────────────
        self.msg_queue.put({
            "sender": "▶ Session",
            "text":   "Session reprise. Tout reprend normalement.",
            "color":  "#27ae60",
        })
        self.root.after(0, self._update_pause_button)

        # ── 3. Si le LLM avait été interrompu → on attend que le MJ envoie ──
        # un message pour reprendre (autogen est en wait_for_input()).
        # Si le LLM était déjà en attente MJ → rien à faire, déjà bloqué.
        if getattr(self, "_was_llm_running_at_pause", False):
            self.msg_queue.put({
                "sender": "⚙️ Système",
                "text": (
                    "Le moteur IA était actif à la pause. "
                    "Envoyez un message pour que les agents reprennent le contexte. "
                    "L'historique complet est conservé."
                ),
                "color": "#888888",
            })

    # ─── Injection StopLLMRequested pour la pause ─────────────────────────────

    def _inject_stop_for_pause(self):
        """
        Injecte StopLLMRequested dans le thread autogen.
        Identique à _inject_stop() (LLMControlMixin) mais sans
        _pending_interrupt_input — le thread retombera dans wait_for_input()
        et attendra un nouveau message à la reprise.
        """
        # Annuler les confirmations MJ en attente (autoriser/refuser action)
        try:
            self._cancel_pending_approvals()
        except Exception:
            pass

        # _stop_event : arrêt immédiat via le sondage dans _make_thinking_wrapper
        try:
            self._stop_event.set()
        except Exception:
            pass

        tid = getattr(self, "_autogen_thread_id", None)
        if tid is None:
            return
        try:
            res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(tid),
                ctypes.py_object(StopLLMRequested),
            )
            if res == 0:
                print("[Pause] Thread autogen introuvable (déjà terminé ?)")
            elif res > 1:
                # Rollback si plusieurs threads touchés (ne devrait pas arriver)
                ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_ulong(tid), None
                )
                print("[Pause] Injection annulée — trop de threads touchés")
        except Exception as e:
            print(f"[Pause] Erreur injection StopLLMRequested : {e}")

    # ─── Mise à jour du bouton ────────────────────────────────────────────────

    def _update_pause_button(self):
        """Met à jour l'apparence du bouton ⏸/▶ selon l'état actuel.
        Doit être appelé depuis le thread Tk (via root.after)."""
        btn = getattr(self, "btn_pause", None)
        if btn is None:
            return
        try:
            if getattr(self, "_session_paused", False):
                btn.config(
                    text="▶ Reprendre",
                    bg="#27ae60",          # vert
                    fg="white",
                )
            else:
                btn.config(
                    text="⏸ Pause",
                    bg="#e67e22",          # orange
                    fg="white",
                )
        except Exception as e:
            print(f"[Pause] Erreur update bouton : {e}")