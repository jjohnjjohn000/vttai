"""
combat_tracker_utils_mixin.py
─────────────────────────────
Fichier 9/10 : Mixin utilitaire (Jets de mort, Tooltips, Logs et Fermeture).
"""

import tkinter as tk
from tkinter import messagebox
import random

try:
    from combat_tracker_constants import C
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


class CombatTrackerUtilsMixin:
    """Mixin regroupant les fonctions utilitaires, modales et événements."""

    # ── Jets de mort (fenêtre dédiée) ─────────────────────────────────────────
    def _open_death_saves(self, c: Combatant):
        """Mini-fenêtre de jets de mort pour un PJ tombé à 0 PV."""
        if c.is_dead or c.is_stabilized:
            return
        dw = tk.Toplevel(self.win)
        dw.title(f"💀 Jets de mort — {c.name}")
        dw.geometry("340x240")
        dw.configure(bg=C["bg"])
        dw.grab_set()

        tk.Label(dw, text=f"💀  {c.name} est à 0 PV !",
                 bg=C["bg"], fg=C["skull"],
                 font=("Consolas", 13, "bold")).pack(pady=(14, 4))
        tk.Label(dw, text="Jets de mort D&D 5e : 3 succès = stabilisé  |  3 échecs = mort",
                 bg=C["bg"], fg=C["fg_dim"], font=("Consolas", 8)).pack()

        status_var = tk.StringVar(value="")
        status_lbl = tk.Label(dw, textvariable=status_var,
                              bg=C["bg"], fg=C["fg_gold"],
                              font=("Consolas", 10, "bold"))
        status_lbl.pack(pady=4)

        def update_status():
            s = (f"✓ Succès : {'🟢' * c.death_saves_success}{'⚫' * (3 - c.death_saves_success)}"
                 f"   ✗ Échecs : {'🔴' * c.death_saves_fail}{'⚫' * (3 - c.death_saves_fail)}")
            status_var.set(s)
            if c.is_stabilized:
                status_var.set("✅ Stabilisé(e) !")
                dw.after(1500, dw.destroy)
            elif c.is_dead:
                status_var.set("💀 Mort(e).")
                dw.after(1500, dw.destroy)

        update_status()

        btn_f = tk.Frame(dw, bg=C["bg"])
        btn_f.pack(pady=12)

        def roll_save():
            roll = random.randint(1, 20)
            result_txt = f"Lancé : {roll}"
            if roll == 1:       # échec critique
                c.death_saves_fail = min(3, c.death_saves_fail + 2)
                result_txt += " — ÉCHEC CRITIQUE (×2) !"
            elif roll == 20:    # succès critique : reprend 1 PV
                c.hp = 1
                c.death_saves_success = 3
                result_txt += " — SUCCÈS CRITIQUE ! Reprend 1 PV."
            elif roll >= 10:
                c.death_saves_success = min(3, c.death_saves_success + 1)
                result_txt += " — Succès."
            else:
                c.death_saves_fail = min(3, c.death_saves_fail + 1)
                result_txt += " — Échec."
            roll_lbl.config(text=result_txt)
            update_status()
            self._refresh_list()

        roll_lbl = tk.Label(dw, text="", bg=C["bg"], fg=C["fg"],
                            font=("Consolas", 10))
        roll_lbl.pack()

        tk.Button(btn_f, text="🎲 Lancer le jet de mort",
                  bg=_darken(C["skull"], 0.4), fg=C["skull"],
                  font=("Consolas", 10, "bold"), relief="flat",
                  padx=12, pady=6, cursor="hand2",
                  command=roll_save).pack(side=tk.LEFT, padx=6)

        tk.Button(btn_f, text="💊 Stabilisé manuellement",
                  bg=_darken(C["green"], 0.3), fg=C["green_bright"],
                  font=("Consolas", 9), relief="flat",
                  padx=6, pady=6, cursor="hand2",
                  command=lambda:[
                      setattr(c, "death_saves_success", 3),
                      update_status(), self._refresh_list()
                  ]).pack(side=tk.LEFT, padx=6)

    # ── Tooltip ───────────────────────────────────────────────────────────────
    def _tooltip(self, widget, text: str):
        tip = None
        def show(e):
            nonlocal tip
            tip = tk.Toplevel(self.win)
            tip.overrideredirect(True)
            tip.attributes("-topmost", True)
            tk.Label(tip, text=text, bg="#1a2030", fg=C["fg"],
                     font=("Consolas", 8), justify=tk.LEFT,
                     padx=8, pady=6, wraplength=280,
                     relief="solid", bd=1).pack()
            tip.geometry(f"+{e.x_root+12}+{e.y_root+12}")
        def hide(e):
            nonlocal tip
            if tip:
                tip.destroy()
                tip = None
        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    # ── Log interne ───────────────────────────────────────────────────────────
    def _log(self, text: str):
        print(f"[Combat] {text}")

    # ── Fermeture ─────────────────────────────────────────────────────────────
    def _on_close(self):
        if self.combat_active:
            if not messagebox.askyesno("Combat actif",
                                        "Un combat est en cours. Fermer quand même ?\n"
                                        "(Le combat sera sauvegardé et reprendra à la prochaine ouverture.)"):  
                return
        self._save_combat_state()
        self.win.destroy()