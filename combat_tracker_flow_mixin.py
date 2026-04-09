"""
combat_tracker_flow_mixin.py
────────────────────────────
Fichier 7/10 : Mixin gérant le déroulement du combat (Initiative, Tours, Rounds).
"""

import tkinter as tk
from tkinter import messagebox

# Imports des dépendances partagées
try:
    from combat_tracker_constants import C, TACTICS
    from combat_tracker_state import COMBAT_STATE
except ImportError:
    pass


def _darken(hex_color: str, factor: float) -> str:
    try:
        h = hex_color.lstrip("#")
        if len(h) == 6:
            r = min(255, int(int(h[0:2], 16) * factor))
            g = min(255, int(int(h[2:4], 16) * factor))
            b = min(255, int(int(h[4:6], 16) * factor))
            return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        pass
    return hex_color

def _lighten(hex_color: str, factor: float) -> str:
    try:
        h = hex_color.lstrip("#")
        if len(h) == 6:
            r = int(h[0:2], 16)
            g = int(h[2:4], 16)
            b = int(h[4:6], 16)
            r = min(255, r + int((255 - r) * factor))
            g = min(255, g + int((255 - g) * factor))
            b = min(255, b + int((255 - b) * factor))
            return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        pass
    return hex_color

def _set_row_bg_recursive(widget, old_bg: str, new_bg: str):
    """Recolorie récursivement tous les widgets d'une ligne dont le bg == old_bg."""
    try:
        if widget.cget("bg") == old_bg:
            widget.config(bg=new_bg)
    except Exception:
        pass
    for child in widget.winfo_children():
        _set_row_bg_recursive(child, old_bg, new_bg)


class CombatTrackerFlowMixin:
    """Mixin pour le contrôle du flux de combat (initiative, tours)."""

    # ── Initiative ────────────────────────────────────────────────────────────
    def _roll_all_initiative(self):
        results =[]
        for c in self.combatants:
            roll = c.roll_initiative()
            results.append(f"  {c.name}: {roll} + {c.dex_bonus} = {c.initiative}")
        self._sort_and_refresh()
        self._log("🎲 JETS D'INITIATIVE :\n" + "\n".join(results))

    def _roll_one_initiative(self, c):
        roll = c.roll_initiative()
        self._log(f"🎲 Initiative {c.name}: {roll} + {c.dex_bonus} = {c.initiative}")
        self._sort_and_refresh()

    def _sort_and_refresh(self):
        self.combatants.sort(key=lambda c: -c.initiative)
        self._refresh_list()

    # ── Combat flow ───────────────────────────────────────────────────────────
    def _start_combat(self):
        if not self.combatants:
            messagebox.showwarning("Combat", "Ajoutez des combatants d'abord !")
            return

        # Auto-roll si initiative = 0
        unrolled = [c for c in self.combatants if c.initiative == 0]
        if unrolled:
            for c in unrolled:
                c.roll_initiative()

        self.combatants.sort(key=lambda c: -c.initiative)
        self.round_num    = 1
        self.current_idx  = 0
        self.combat_active= True

        # ── Mise à jour état partagé ──
        COMBAT_STATE["active"]           = True
        COMBAT_STATE["round_num"]        = 1
        COMBAT_STATE["reactions_used"]   = set()
        COMBAT_STATE["speech_used"]      = set()
        active_c = self.combatants[0] if self.combatants else None
        COMBAT_STATE["active_combatant"] = active_c.name if active_c else None
        COMBAT_STATE["turn_res"]         = {}

        self._btn_start.config(state=tk.DISABLED)
        self._btn_next.config( state=tk.NORMAL)
        self._btn_end.config(  state=tk.NORMAL)

        self._update_round_label()
        self._refresh_list()
        self._log_turn()
        self._save_combat_state()

        # ── Marque la position dans le chat au début du combat ──
        # Utilisée pour nettoyer le chat si le MJ refuse la sauvegarde en fin de combat.
        try:
            cd = getattr(self.app, "chat_display", None)
            if cd:
                cd.mark_set("combat_start", tk.END)
                cd.mark_gravity("combat_start", tk.LEFT)
        except Exception:
            pass

        # ── Basculer les agents PJ vers le modèle combat (léger/rapide) ──
        try:
            if self.app is not None and hasattr(self.app, "_set_combat_llm"):
                self.app._set_combat_llm(True)
        except Exception as e:
            print(f"[CombatTracker] Erreur switch LLM combat : {e}")

        # ── Déclenche automatiquement le tour si c'est un PJ ──
        self._trigger_pc_turn_if_needed()
        # ── Déclenche l'affichage PNJ ──
        self._trigger_npc_turn_if_needed()

    def _update_active_rows(self, old_idx: int, new_idx: int):
        """Mise à jour visuelle chirurgicale des deux lignes affectées par le
        changement de tour. Ne rebuild PAS la liste entière.

        Modifie uniquement :
          - bordure et bg du frame de ligne
          - label de nom (ajout/retrait de " *", couleur)
          - bouton "↺ Réinit. actions" (créé sur la nouvelle ligne active,
            détruit sur l'ancienne)
        """
        def _deactivate(idx):
            if not (0 <= idx < len(self.combatants)):
                return
            cb   = self.combatants[idx]
            rw   = self._row_widgets.get(cb.uid)
            if not rw:
                return
            rf   = rw["row_frame"]
            # bg avant → bg après
            old_bg = C["row_active"] if cb.is_pc else _lighten(C["row_active"], 0.15)
            new_bg = C["row_pc"]     if cb.is_pc else C["row_npc"]
            _set_row_bg_recursive(rf, old_bg, new_bg)
            rf.config(highlightbackground=C["border"], highlightthickness=1)
            # Nom : retirer l'étoile
            skull = " [X]" if cb.is_dead else (" [~]" if cb.is_down else "")
            rw["name_lbl"].config(text=cb.name + skull, fg=cb.color)
            # Supprimer le bouton réinit
            btn = rw.get("reset_btn")
            if btn:
                try:
                    btn.destroy()
                except Exception:
                    pass
                rw["reset_btn"] = None

        def _activate(idx):
            if not (0 <= idx < len(self.combatants)):
                return
            cb   = self.combatants[idx]
            rw   = self._row_widgets.get(cb.uid)
            if not rw:
                return
            rf   = rw["row_frame"]
            old_bg = C["row_pc"]     if cb.is_pc else C["row_npc"]
            new_bg = C["row_active"] if cb.is_pc else _lighten(C["row_active"], 0.15)
            _set_row_bg_recursive(rf, old_bg, new_bg)
            rf.config(highlightbackground=C["border_hot"], highlightthickness=2)
            # Nom : ajouter l'étoile
            skull = " [X]" if cb.is_dead else (" [~]" if cb.is_down else "")
            rw["name_lbl"].config(text=cb.name + skull + " *", fg=C["fg_gold"])
            # Ajouter le bouton réinit si absent
            if rw.get("reset_btn") is None:
                btn = tk.Button(rw["act_inner"], text="↺ Réinit. actions",
                                bg=_darken(C["gold"], 0.3), fg=C["gold"],
                                font=("Consolas", 7, "bold"), relief="flat",
                                padx=4, cursor="hand2",
                                command=lambda c=cb: (c.reset_turn_resources(),
                                                      self._refresh_list()))
                btn.pack(anchor="w", pady=(2, 0))
                rw["reset_btn"] = btn

        _deactivate(old_idx)
        _activate(new_idx)
        self._update_round_label()

    def _next_turn(self):
        if not self.combat_active:
            return

        old_idx = self.current_idx

        # Réinitialise les actions du combatant actif
        if 0 <= old_idx < len(self.combatants):
            self.combatants[old_idx].reset_turn_resources()

        # Avance
        self.current_idx += 1
        if self.current_idx >= len(self.combatants):
            self.current_idx = 0
            self.round_num  += 1
            self._log(f"\n══ Round {self.round_num} ══")
            COMBAT_STATE["reactions_used"] = set()
            COMBAT_STATE["speech_used"]    = set()

        # ── Mise à jour état partagé ──
        COMBAT_STATE["round_num"] = self.round_num
        active_c = self.combatants[self.current_idx] if self.combatants else None
        COMBAT_STATE["active_combatant"] = active_c.name if active_c else None
        COMBAT_STATE["turn_spells"] =[]
        if active_c and "turn_res" in COMBAT_STATE:
            COMBAT_STATE["turn_res"].pop(active_c.name, None)

        # ── Retrait des tactiques qui expirent au DEBUT du tour (Esquive) ──
        if active_c and "Esquive" in active_c.tactics:
            del active_c.tactics["Esquive"]
            # Mise à jour du bouton ui
            rw = self._row_widgets.get(active_c.uid)
            if rw and "tac_btns" in rw:
                b = rw["tac_btns"].get("Esquive")
                if b and "Esquive" in TACTICS:
                    b.config(bg=_darken(TACTICS["Esquive"]["color"], 0.25), fg="#666677")
            # Synchro de la carte (efface le badge)
            if getattr(self, "app", None) and getattr(self.app, "_combat_map_win", None):
                for t in self.app._combat_map_win.tokens:
                    if t.get("name") == active_c.name and "tactics" in t:
                        if "Esquive" in t["tactics"]:
                            t["tactics"].remove("Esquive")
                            self.app._combat_map_win._redraw_one_token(t)

        # Mise à jour visuelle chirurgicale — PAS de rebuild complet
        self._update_active_rows(old_idx, self.current_idx)
        self._log_turn()
        self._save_combat_state()

        # ── Déclenche automatiquement le tour si c'est un PJ ──
        self._trigger_pc_turn_if_needed()
        # ── Déclenche l'affichage PNJ ──
        self._trigger_npc_turn_if_needed()

        # Auto-scroll vers le combatant actif
        try:
            self._canvas.yview_moveto(
                max(0, self.current_idx / max(1, len(self.combatants)) - 0.1)
            )
        except Exception:
            pass

    def advance_turn(self):
        """Appelé par le moteur IA quand un PJ déclare [FIN_DE_TOUR]."""
        if self.combat_active:
            self.root.after(0, self._next_turn)

    def _end_combat(self):

        if not messagebox.askyesno("Fin du combat",
                                    "Terminer le combat et réinitialiser ?"):
            return

        summary = self._build_summary()
        self.combat_active = False
        self.current_idx   = -1
        self.round_num     = 0

        # ── Réinitialise l'état partagé ──
        COMBAT_STATE["active"]           = False
        COMBAT_STATE["active_combatant"] = None
        COMBAT_STATE["round_num"]        = 0
        COMBAT_STATE["reactions_used"]   = set()
        COMBAT_STATE["speech_used"]      = set()
        COMBAT_STATE["turn_res"]         = {}

        for c in self.combatants:
            c.reset_turn_resources()
            c.conditions.clear()
            if c.is_pc:
                c.death_saves_success = 0
                c.death_saves_fail    = 0

        self._btn_start.config(state=tk.NORMAL)
        self._btn_next.config( state=tk.DISABLED)
        self._btn_end.config(  state=tk.DISABLED)
        self._round_var.set("Round  —")
        self._refresh_list()

        self._log("🏁 COMBAT TERMINÉ\n" + summary)
        self._save_combat_state()

        # ── Restaurer les configs LLM d'origine des agents PJ ───────────────
        try:
            if self.app is not None and hasattr(self.app, "_set_combat_llm"):
                self.app._set_combat_llm(False)
        except Exception as e:
            print(f"[CombatTracker] Erreur restauration LLM : {e}")

        # ── Confirmation de sauvegarde du journal ────────────────────────────
        if messagebox.askyesno(
            "Sauvegarder le journal ?",
            "Voulez-vous sauvegarder le journal de cette session ?\n\n"
            "Non → le chat du combat sera effacé pour tous les agents.",
            icon="question",
            parent=self.win,
        ):
            try:
                if self.app is not None and hasattr(self.app, "trigger_save"):
                    self.app.trigger_save()
            except Exception as e:
                print(f"[CombatTracker] Erreur sauvegarde chat : {e}")
        else:
            # Nettoyage du chat depuis le début du combat
            try:
                cd = getattr(self.app, "chat_display", None)
                if cd and "combat_start" in cd.mark_names():
                    cd.config(state=tk.NORMAL)
                    cd.delete("combat_start", tk.END)
                    cd.config(state=tk.DISABLED)
            except Exception as e:
                print(f"[CombatTracker] Erreur nettoyage chat : {e}")
            # Purge de l'historique de combat partagé
            COMBAT_STATE.pop("combat_history", None)

        if self.chat_queue:
            self.chat_queue.put({
                "sender": "⚔️ Combat",
                "text":   "🏁 **Combat terminé** — " + summary,
                "color":  "#e67e22"
            })

    def _update_round_label(self):
        self._round_var.set(f"Round  {self.round_num}")
        active_name = (self.combatants[self.current_idx].name
                       if 0 <= self.current_idx < len(self.combatants)
                       else "—")
        alive = sum(1 for c in self.combatants if not c.is_down)
        self._info_var.set(
            f"Tour de : {active_name}\n"
            f"Combatants debout : {alive}/{len(self.combatants)}"
        )

    def _log_turn(self):
        if not (0 <= self.current_idx < len(self.combatants)):
            return
        c = self.combatants[self.current_idx]
        conds = ", ".join(c.conditions.keys()) or "Aucune"
        
        # 1. Log interne de la fenêtre du tracker
        log_msg = (f"⚡ Tour de {c.name}  "
                   f"(Init {c.initiative} | PV {c.hp}/{c.max_hp} | CA {c.ac})\n"
                   f"   Conditions : {conds}")
        self._log(log_msg)
        
        # 2. Envoyer le beau bandeau dans le chat principal (pour PJ et PNJ)
        if self.chat_queue:
            hp_info = f"PV {c.hp}/{c.max_hp} | CA {c.ac}"
            round_n = getattr(self, "round_num", "?")
            chat_msg = (
                f"{'─' * 38}\n"
                f"⚡ Tour de {c.name}  —  Round {round_n}\n"
                f"   {hp_info}\n"
                f"{'─' * 38}"
            )
            if c.conditions:
                chat_msg += f"\n   Conditions : {conds}"
                
            color = "#e67e22" if c.is_pc else "#c0392b"  # Orange (Héros) ou Rouge (Ennemis)
            
            self.chat_queue.put({
                "sender": "⚔️ Combat",
                "text":   chat_msg,
                "color":  color
            })

    def _build_summary(self) -> str:
        lines =[f"Durée : {self.round_num} round(s)"]
        down  =[c for c in self.combatants if c.is_down]
        dead  =[c for c in self.combatants if c.is_dead]
        if dead:
            lines.append("Morts : " + ", ".join(c.name for c in dead))
        if down:
            lines.append("KO    : " + ", ".join(c.name for c in down))
        return "  |  ".join(lines)

    def _trigger_pc_turn_if_needed(self):
        """Si le combatant actif est un PJ vivant, appelle pc_turn_callback
        pour déclencher son tour automatiquement dans autogen.
        Appelé après chaque _next_turn() et _start_combat().
        """
        if not self.combat_active or not self.pc_turn_callback:
            return
        # ── Bloqué pendant la pause — aucune réaction LLM ne doit partir ───────
        if getattr(getattr(self, "app", None), "_session_paused", False):
            return
        if not (0 <= self.current_idx < len(self.combatants)):
            return
        c = self.combatants[self.current_idx]
        if c.is_pc and not c.is_down:
            self.pc_turn_callback(c.name)

    def _trigger_npc_turn_if_needed(self):
        """Déclenche l'affichage des outils MJ si le combattant actif est un PNJ."""
        if not self.combat_active:
            return
        if not (0 <= self.current_idx < len(self.combatants)):
            return
        c = self.combatants[self.current_idx]
        if not c.is_pc and not c.is_down:
            if hasattr(self, "_show_npc_turn_tools"):
                self._show_npc_turn_tools(c)