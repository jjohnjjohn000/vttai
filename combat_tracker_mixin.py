"""
combat_tracker_mixin.py — Intégration du tracker de combat D&D 5e dans DnDApp.

Fournit CombatTrackerMixin à injecter dans DnDApp :
  - open_combat_tracker     : ouvre (ou ramène) la fenêtre CombatTracker
  - _on_pc_combat_turn      : callback déclenché quand c'est au tour d'un PJ
  - _on_pc_turn_ended       : callback déclenché quand [FIN_DE_TOUR] est détecté

Prérequis sur l'instance hôte :
  self.root, self.msg_queue, self._combat_tracker,
  self._agents, self._waiting_for_mj, self._pending_combat_trigger,
  self.user_input, self.input_event
  self._rebuild_agent_prompts(), self._track_window()
"""

from state_manager  import load_state
from combat_tracker import CombatTracker


class CombatTrackerMixin:
    """Mixin pour DnDApp — suivi du tracker de combat."""

    # ─── Ouverture du tracker ────────────────────────────────────────────────

    def open_combat_tracker(self):
        """Ouvre (ou ramène au premier plan) la fenêtre de combat D&D 5e."""
        if self._combat_tracker is not None:
            try:
                self._combat_tracker.win.deiconify()
                self._combat_tracker.win.lift()
                return
            except Exception:
                self._combat_tracker = None
        self._combat_tracker = CombatTracker(
            root=self.root,
            state_loader=load_state,
            chat_queue=self.msg_queue,
            pc_turn_callback=self._on_pc_combat_turn,
            advance_turn_callback=self._on_pc_turn_ended,
            app=self,
        )
        try:
            self._track_window("combat_tracker", self._combat_tracker.win)
        except Exception:
            pass

    # ─── Callbacks de tour ───────────────────────────────────────────────────

    def _on_pc_combat_turn(self, char_name: str):
        """Appelé par CombatTracker quand c'est au tour d'un PJ.

        1. Reconstruit les prompts de tous les agents (COMBAT_STATE déjà mis à jour).
        2. Affiche un indicateur dans le chat.
        3. Stocke le trigger dans _pending_combat_trigger — gui_get_human_input le
           consommera au prochain appel, qu'il arrive avant ou après ce callback.
           (root.after(0,...) peut s'exécuter avant que _waiting_for_mj soit True,
            donc on ne peut pas se fier à ce flag ici.)
        4. Si le moteur attend déjà la saisie du MJ, on peut injecter directement.
        """
        # Ne pas déclencher de tour si la session est en pause.
        # Le tracker repassera par là quand la session reprend et qu'un tour est avancé.
        if getattr(self, "_session_paused", False):
            self.msg_queue.put({
                "sender": "⏸ Combat",
                "text":   f"Tour de {char_name} en attente — session en pause.",
                "color":  "#888888",
            })
            return
        # Mise à jour des prompts (COMBAT_STATE déjà à jour dans _next_turn)
        if self._agents:
            self._rebuild_agent_prompts()

        # Indicateur visuel dans le chat
        self.msg_queue.put({
            "sender": "⚔️ Combat",
            "text":   f"🗡️ Tour de {char_name} — en attente de ses actions...",
            "color":  "#c8a820",
        })

        # ── Résumé des slots : calculé AVANT la rédaction du trigger ──────────
        _slots_header = ""
        _slots_detail = ""
        try:
            _s = load_state()
            _slots = _s.get("characters", {}).get(char_name, {}).get("spell_slots", {})
            if _slots:
                _avail = [
                    (int(k), v)
                    for k, v in sorted(_slots.items(), key=lambda x: int(x[0]))
                    if v > 0
                ]
                _empty = [
                    int(k)
                    for k, v in sorted(_slots.items(), key=lambda x: int(x[0]))
                    if v == 0
                ]
                if _avail:
                    _avail_str = ", ".join(f"niv.{k}×{v}" for k, v in _avail)
                    _slots_detail += f"  ✅ DISPONIBLES : {_avail_str}\n"
                if _empty:
                    _empty_str = ", ".join(f"niv.{k}" for k in _empty)
                    _slots_detail += f"  ❌ ÉPUISÉS    : {_empty_str}\n"
                if _slots_detail:
                    _slots_header = (
                        f"\n📋 SLOTS DE SORT DE {char_name.upper()} — VÉRIFIER AVANT DE DÉCLARER :\n"
                        + _slots_detail
                        + f"  → Tu DOIS choisir un niveau de slot présent dans la liste ✅.\n"
                        + f"  → Si le sort que tu veux est à un niveau ❌ épuisé, déclare-le\n"
                        + f"    avec un slot supérieur disponible (upcast) ou choisis un autre sort.\n"
                    )
        except Exception:
            pass

        trigger = (
            f"⚔️ [SYSTÈME DE COMBAT — TOUR DE {char_name.upper()}]\n"
            f"C'est le tour de {char_name}. {char_name}, déclare ton économie d'action "
            f"complète (Action, Action Bonus, Mouvement, Réaction si applicable).\n"
            f"{_slots_header}\n"
            f"RÈGLE ABSOLUE — NARRATION D'ABORD :\n"
            f"1. Narre d'abord ce que ton personnage fait, dit ou ressent (roleplay).\n"
            f"2. Puis déclare chaque action mécanique dans un bloc [ACTION].\n"
            f"3. N'appelle AUCUN outil directement (update_hp, roll_dice, etc.) — "
            f"le MJ les exécute lui-même après validation dans le chat.\n"
            f"4. Ne modifie jamais tes PV, slots ou état toi-même.\n"
            f"5. Pour les sorts : vérifie la liste 📋 ci-dessus et indique un niveau ✅ disponible.\n\n"
            f"Format attendu :\n"
            f"  <roleplay narratif>\n"
            f"  [ACTION]\n"
            f"  Type: Attaque / Sort / Mouvement / …\n"
            f"  Cible: <cible>\n"
            f"  Règle: <règle D&D 5e applicable>\n\n"
            f"Quand tu as tout déclaré, termine par [FIN_DE_TOUR]."
        )

        # Toujours stocker dans le buffer — gui_get_human_input le consommera
        self._pending_combat_trigger = trigger

        # Si on attend déjà l'input MJ : injecter immédiatement aussi
        if self._waiting_for_mj:
            self.user_input = trigger
            self._pending_combat_trigger = None
            self.input_event.set()

    def _on_pc_turn_ended(self, char_name: str):
        """Appelé quand le moteur détecte [FIN_DE_TOUR] dans le message d'un PJ.
        Avance le combat tracker au tour suivant (thread-safe via root.after).
        Ne fait rien si la session est en pause.
        """
        if getattr(self, "_session_paused", False):
            return   # l'avance de tour sera relancée manuellement à la reprise
        self.msg_queue.put({
            "sender": "⚔️ Combat",
            "text":   f"✅ {char_name} a terminé son tour — passage au combatant suivant.",
            "color":  "#c8a820",
        })
        if self._combat_tracker is not None:
            try:
                self._combat_tracker.advance_turn()
            except Exception as _e:
                print(f"[advance_turn] Erreur : {_e}")