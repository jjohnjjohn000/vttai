"""
combat_tracker_state_mixin.py
─────────────────────────────
Fichier 6/10 : Mixin gérant la sauvegarde, la restauration et la synchro d'état.
"""

import tkinter as tk
from tkinter import messagebox
import threading
import random

# Imports des dépendances partagées
try:
    from combat_tracker_constants import C, PC_COLORS, PC_DEX_BONUS
    from combat_tracker_state import COMBAT_STATE
    from combat_tracker_combatant import Combatant
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


class CombatTrackerStateMixin:
    """Mixin pour la gestion de la persistance, de l'état et de l'import des PJ."""

    # ── Sauvegarde différée (debounce) ───────────────────────────────────────

    def _schedule_save(self, delay_ms: int = 800):
        """Sauvegarde différée : annule le timer précédent et repart de zéro.
        Évite les I/O disque répétées lors de rafales de clics (HP, conditions…).
        Les événements critiques (changement de tour, fin de combat) appellent
        _save_combat_state() directement sans passer par ce debounce."""
        if self._save_timer is not None:
            try:
                self.win.after_cancel(self._save_timer)
            except Exception:
                pass
        try:
            self._save_timer = self.win.after(delay_ms, self._do_scheduled_save)
        except Exception:
            pass  # fenêtre détruite entre temps

    def _do_scheduled_save(self):
        self._save_timer = None
        self._save_combat_state()

    def apply_damage_to_npc(self, target_name: str, damage: int):
        """Recherche un PNJ par nom et applique les dégâts (temp hp inclus)."""
        target_lower = target_name.lower()
        hit = False
        for c in self.combatants:
            if not c.is_pc:
                if c.name.lower() in target_lower or target_lower in c.name.lower():
                    actual_dmg = damage
                    if c.temp_hp > 0:
                        absorbed = min(c.temp_hp, actual_dmg)
                        c.temp_hp -= absorbed
                        actual_dmg -= absorbed
                    
                    c.hp = max(0, c.hp - actual_dmg)
                    hit = True
        if hit:
            try:
                self.win.after(0, self._refresh_list)
                self._schedule_save()
            except Exception:
                pass

    # ── Persistance du combat ─────────────────────────────────────────────────

    def _save_combat_state(self):
        """Sérialise l'état complet du tracker dans campaign_state.json.
        L'écriture disque est effectuée dans un thread daemon pour ne pas
        bloquer le thread Tk (le snapshot est pris immédiatement, thread-safe)."""
        try:
            from state_manager import load_state as _ls, save_state as _ss
            # Snapshot immédiat dans le thread Tk — pas de race condition
            snapshot = {
                "active":         self.combat_active,
                "round_num":      self.round_num,
                "current_idx":    self.current_idx,
                "reactions_used": list(COMBAT_STATE.get("reactions_used", set())),
                "speech_used":    list(COMBAT_STATE.get("speech_used",    set())),
                "combatants":     [c.to_dict() for c in self.combatants],
            }

            def _write():
                try:
                    state = _ls()
                    state["combat_tracker"] = snapshot
                    _ss(state)
                except Exception as e:
                    print(f"[CombatTracker] Erreur sauvegarde (thread) : {e}")

            threading.Thread(target=_write, daemon=True, name="ct-save").start()
        except Exception as e:
            print(f"[CombatTracker] Erreur sauvegarde : {e}")

    def sync_pc_hp_from_state(self):
        """
        Synchronise les PV des PJ dans le tracker depuis campaign_state["characters"].
        Appelé depuis autogen_engine chaque fois que update_hp() modifie les PV.
        Mise à jour in-place via _row_widgets — pas de rebuild complet de la liste.
        Thread-safe : doit être appelé via root.after() depuis le thread Tk.
        """
        try:
            from state_manager import load_state as _ls
            _st = _ls()
            chars = _st.get("characters", {})
            needs_full_refresh = False
            for cb in self.combatants:
                if cb.is_pc and cb.name in chars:
                    new_hp   = chars[cb.name].get("hp", cb.hp)
                    new_temp = chars[cb.name].get("temp_hp", 0)
                    changed  = (cb.hp != new_hp) or (cb.temp_hp != new_temp)
                    if changed:
                        was_up   = cb.hp > 0
                        cb.hp    = new_hp
                        cb.temp_hp = new_temp
                        rw = self._row_widgets.get(cb.uid)
                        if rw:
                            try:
                                temp_suffix = f"  +{cb.temp_hp}✦" if cb.temp_hp > 0 else ""
                                rw["hp_lbl"].config(
                                    text=f"{max(0, cb.hp)} / {cb.max_hp}{temp_suffix}",
                                    fg=cb.hp_color(),
                                    font=("Consolas", 13, "bold"),
                                )
                                rw["draw_hp_bar"](rw["bar_canvas"], cb)
                            except Exception:
                                needs_full_refresh = True
                        else:
                            needs_full_refresh = True
                        # Afficher les jets de mort si le PJ vient de tomber à 0
                        if cb.is_pc and cb.hp == 0 and was_up:
                            needs_full_refresh = True
            if needs_full_refresh:
                self._refresh_list()
        except Exception as e:
            print(f"[CombatTracker] sync_pc_hp_from_state : {e}")

    def _restore_combat_state(self):
        """
        Recharge l'état du tracker depuis campaign_state.json.
        Si un combat était en cours, le reprend exactement là où il en était.
        Sinon, importe seulement les PJ depuis l'état de la campagne.
        """
        try:
            from state_manager import load_state as _ls
            state = _ls()
            saved = state.get("combat_tracker", {})

            if saved.get("combatants"):
                # Reconstruction complète depuis la sauvegarde
                self.combatants = [Combatant.from_dict(d) for d in saved["combatants"]]

                # ── Réconciliation HP : la source de vérité pour les PJ est
                # campaign_state["characters"], pas le snapshot du tracker.
                # (update_hp écrit dans characters ; on s'assure qu'ils sont alignés.)
                _canonical_chars = state.get("characters", {})
                for cb in self.combatants:
                    if cb.is_pc and cb.name in _canonical_chars:
                        cb.hp = _canonical_chars[cb.name].get("hp", cb.hp)

                self.combat_active = saved.get("active", False)
                self.round_num     = saved.get("round_num", 0)
                self.current_idx   = saved.get("current_idx", -1)

                # Restaure les ressources hors-tour du round courant
                COMBAT_STATE["reactions_used"] = set(saved.get("reactions_used",[]))
                COMBAT_STATE["speech_used"]    = set(saved.get("speech_used",[]))

                if self.combat_active:
                    # Remet COMBAT_STATE en ordre
                    COMBAT_STATE["active"]     = True
                    COMBAT_STATE["round_num"]  = self.round_num
                    active_c = (self.combatants[self.current_idx]
                                if 0 <= self.current_idx < len(self.combatants) else None)
                    COMBAT_STATE["active_combatant"] = active_c.name if active_c else None

                    # Met à jour les boutons
                    self._btn_start.config(state=tk.DISABLED)
                    self._btn_next.config( state=tk.NORMAL)
                    self._btn_end.config(  state=tk.NORMAL)
                    self._update_round_label()
                else:
                    COMBAT_STATE["active"] = False
                    COMBAT_STATE["active_combatant"] = None

                self._refresh_list()
                if self.combat_active:
                    self._log(f"⟳ Combat restauré — Round {self.round_num}")
            else:
                # Pas de sauvegarde : import classique des PJ
                self._import_pcs()
        except Exception as e:
            print(f"[CombatTracker] Erreur restauration : {e}")
            self._import_pcs()

    def _import_pcs(self):
        try:
            state = self._load_state()
            for name, data in state.get("characters", {}).items():
                c = Combatant(
                    name=name, is_pc=True,
                    max_hp=data["max_hp"],
                    current_hp=data["hp"],
                    ac=16,   # valeur par défaut raisonnable
                    initiative=0,
                    dex_bonus=PC_DEX_BONUS.get(name, 2),
                    color=PC_COLORS.get(name, "#a0c0ff"),
                )
                self.combatants.append(c)
        except Exception as e:
            print(f"[CombatTracker] Erreur import PJ : {e}")
        self._refresh_list()

    def _add_missing_pc(self):
        """
        Ouvre une petite fenêtre listant les PJ présents dans campaign_state
        mais absents du tracker. Permet de les ajouter un par un avec
        leur initiative saisie manuellement.
        """
        try:
            state = self._load_state()
            all_chars = state.get("characters", {})
        except Exception as e:
            print(f"[CombatTracker] Erreur chargement state : {e}")
            return

        # PJ déjà dans le tracker
        present = {c.name for c in self.combatants if c.is_pc}
        missing = {name: data for name, data in all_chars.items()
                   if name not in present}

        if not missing:
            messagebox.showinfo("Héros",
                                "Tous les héros sont déjà dans le tracker.",
                                parent=self.win)
            return

        # Fenêtre de sélection
        dlg = tk.Toplevel(self.win)
        dlg.title("Ajouter un héros")
        dlg.configure(bg=C["bg"])
        dlg.grab_set()
        dlg.resizable(False, False)

        tk.Label(dlg, text="Héros absents du combat",
                 bg=C["bg"], fg=C["gold"],
                 font=("Consolas", 11, "bold")).pack(pady=(12, 4), padx=16)
        tk.Label(dlg, text="Saisissez l'initiative puis cliquez sur Ajouter.",
                 bg=C["bg"], fg=C["fg_dim"],
                 font=("Consolas", 8)).pack(padx=16)

        tk.Frame(dlg, bg=C["border"], height=1).pack(fill=tk.X, padx=8, pady=8)

        for name, data in missing.items():
            row = tk.Frame(dlg, bg=C["bg"])
            row.pack(fill=tk.X, padx=16, pady=4)

            color = PC_COLORS.get(name, "#a0c0ff")
            tk.Label(row, text=name, bg=C["bg"], fg=color,
                     font=("Consolas", 11, "bold"), width=10,
                     anchor="w").pack(side=tk.LEFT)

            tk.Label(row, text=f"PV:{data.get('hp', '?')}/{data.get('max_hp', '?')}",
                     bg=C["bg"], fg=C["fg_dim"],
                     font=("Consolas", 9), width=12).pack(side=tk.LEFT, padx=(4, 8))

            tk.Label(row, text="Init:", bg=C["bg"], fg=C["fg_dim"],
                     font=("Consolas", 9)).pack(side=tk.LEFT)

            init_var = tk.StringVar(value="")
            init_entry = tk.Entry(row, textvariable=init_var,
                                  bg=C["entry_bg"], fg=C["fg_gold"],
                                  font=("Consolas", 10), width=5,
                                  insertbackground=C["gold"], relief="flat",
                                  justify="center")
            init_entry.pack(side=tk.LEFT, padx=(2, 8), ipady=2)

            def _do_add(n=name, d=data, iv=init_var, dlg_ref=dlg):
                try:
                    init_val = int(iv.get())
                except ValueError:
                    # Pas d'initiative saisie → lancer le dé
                    dex = PC_DEX_BONUS.get(n, 2)
                    init_val = random.randint(1, 20) + dex

                c = Combatant(
                    name=n, is_pc=True,
                    max_hp=d.get("max_hp", 20),
                    current_hp=d.get("hp"),
                    ac=16,
                    initiative=init_val,
                    dex_bonus=PC_DEX_BONUS.get(n, 2),
                    color=PC_COLORS.get(n, "#a0c0ff"),
                )
                self.combatants.append(c)
                self._sort_and_refresh()
                self._save_combat_state()
                if self.chat_queue:
                    self.chat_queue.put({
                        "sender": "⚔️ Combat",
                        "text":   f"➕ {n} rejoint le combat (Init: {init_val}).",
                        "color":  "#c8a820",
                    })
                # Retirer la ligne du dialogue
                for w in row.winfo_children():
                    w.destroy()
                tk.Label(row, text=f"✅ {n} ajouté(e)",
                         bg=C["bg"], fg=C["green_bright"],
                         font=("Consolas", 9)).pack(side=tk.LEFT)

            tk.Button(row, text="Ajouter",
                      bg=_darken(C["green"], 0.35), fg=C["green_bright"],
                      font=("Consolas", 9, "bold"), relief="flat",
                      padx=6, pady=2, cursor="hand2",
                      command=_do_add).pack(side=tk.LEFT)

        tk.Frame(dlg, bg=C["border"], height=1).pack(fill=tk.X, padx=8, pady=8)
        tk.Button(dlg, text="Fermer",
                  bg=_darken(C["red"], 0.4), fg="#e07070",
                  font=("Consolas", 9), relief="flat", padx=10, pady=4,
                  command=dlg.destroy).pack(pady=(0, 12))