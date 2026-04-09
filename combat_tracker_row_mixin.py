"""
combat_tracker_row_mixin.py
───────────────────────────
Fichier 5/10 : Mixin gérant l'affichage et la mise à jour des lignes de combatants.
"""

import tkinter as tk
from tkinter import messagebox
import threading

# Imports des dépendances partagées
try:
    from combat_tracker_constants import C, CONDITIONS, TACTICS, _BESTIARY_OK
    if _BESTIARY_OK:
        from npc_bestiary_panel import MonsterSheetWindow
except ImportError:
    pass

# Helpers de couleur utilisés pour les lignes
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


class CombatTrackerRowMixin:
    """Mixin regroupant la construction et le refresh des lignes de combatants."""

    def _refresh_list(self):
        # 1. Établir les uids toujours présents
        current_uids = {c.uid for c in self.combatants}

        # 2. Détruire les lignes des combatants retirés
        for uid, rw in list(self._row_widgets.items()):
            if uid not in current_uids:
                rw["row_frame"].destroy()
                del self._row_widgets[uid]

        # 3. Détacher visuellement toutes les lignes pour les réordonner
        for rw in self._row_widgets.values():
            rw["row_frame"].pack_forget()

        self._rows.clear()

        # 4. Construire ou réutiliser et empiler les lignes dans le nouvel ordre
        for idx, c in enumerate(self.combatants):
            is_active = (self.combat_active and idx == self.current_idx)

            if c.uid in self._row_widgets:
                rw = self._row_widgets[c.uid]
                rf = rw["row_frame"]
                rf.pack(fill=tk.X, padx=4, pady=2)
                
                # Mise à jour des données potentiellement modifiées par script/trie
                if "init_var" in rw: rw["init_var"].set(str(c.initiative))
                if "ac_var" in rw:   rw["ac_var"].set(str(c.ac))
                if "conc_var" in rw: rw["conc_var"].set(c.concentration)
                if "hp_lbl" in rw:
                    temp_suffix = f"  +{c.temp_hp}✦" if c.temp_hp > 0 else ""
                    rw["hp_lbl"].config(
                        text=f"{max(0, c.hp)} / {c.max_hp}{temp_suffix}",
                        fg=c.hp_color()
                    )
                    rw["draw_hp_bar"](rw["bar_canvas"], c)

                # Variables d'actions
                acts = rw.get("action_vars", {})
                if "action" in acts: acts["action"].set(c.action_used)
                if "bonus" in acts:  acts["bonus"].set(c.bonus_used)
                if "react" in acts:  acts["react"].set(c.reaction_used)
                if "move" in acts:   acts["move"].set(str(c.move_used))

                # Mise à jour des badges conditions et tactiques
                if "cond_btns" in rw:
                    for cn, b in rw["cond_btns"].items():
                        cdata = CONDITIONS.get(cn)
                        if cdata:
                            active = cn in c.conditions
                            b.config(bg=cdata["color"] if active else _darken(cdata["color"], 0.25),
                                     fg="#ffffff" if active else "#666677")
                
                if "tac_btns" in rw:
                    for tn, b in rw["tac_btns"].items():
                        tdata = TACTICS.get(tn)
                        if tdata:
                            active = tn in c.tactics
                            b.config(bg=tdata["color"] if active else _darken(tdata["color"], 0.25),
                                     fg="#ffffff" if active else "#666677")

                # Mise à jour visuelle _active_ / _inactive_ (incluant création/suppression bouton réinit)
                self._update_row_visuals(rw, c, is_active)
            else:
                self._build_row(c, idx, is_active)

        self._canvas.update_idletasks()
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _update_row_visuals(self, rw, cb, is_active):
        """Met à jour l'apparence de la ligne (couleurs, étoiles) sans tout reconstruire"""
        rf = rw["row_frame"]
        new_bg = C["row_active"] if cb.is_pc else _lighten(C["row_active"], 0.15)
        if not is_active:
            new_bg = C["row_pc"] if cb.is_pc else C["row_npc"]
            
        rf.config(highlightbackground=C["border_hot"] if is_active else C["border"],
                  highlightthickness=2 if is_active else 1)
        _set_row_bg_recursive(rf, rf.cget("bg"), new_bg)

        skull = " [X]" if cb.is_dead else (" [~]" if cb.is_down else "")
        star = " *" if is_active else ""
        rw["name_lbl"].config(text=cb.name + skull + star,
                              fg=C["fg_gold"] if is_active else cb.color)

        btn = rw.get("reset_btn")
        if is_active and not btn:
            btn = tk.Button(rw["act_inner"], text="↺ Réinit. actions",
                            bg=_darken(C["gold"], 0.3), fg=C["gold"],
                            font=("Consolas", 7, "bold"), relief="flat",
                            padx=4, cursor="hand2",
                            command=lambda c=cb: (c.reset_turn_resources(),
                                                  self._refresh_list()))
            btn.pack(anchor="w", pady=(2, 0))
            rw["reset_btn"] = btn
        elif not is_active and btn:
            try: btn.destroy()
            except Exception: pass
            rw["reset_btn"] = None

    def _build_row(self, c, idx: int, active: bool):
        if c.is_pc:
            row_bg = C["row_active"] if active else C["row_pc"]
        else:
            row_bg = _lighten(C["row_active"], 0.15) if active else C["row_npc"]

        border_color = C["border_hot"] if active else C["border"]

        row = tk.Frame(self._inner, bg=row_bg,
                       highlightbackground=border_color,
                       highlightthickness=2 if active else 1)
        row.pack(fill=tk.X, padx=4, pady=2)

        # Helper : fixe la largeur minimale via un spacer invisible (height=0)
        # sans pack_propagate(False) qui tronque le contenu verticalement.
        def _col(w, padx=4, pady=4):
            f = tk.Frame(row, bg=row_bg)
            f.pack(side=tk.LEFT, padx=padx, pady=pady)
            tk.Frame(f, bg=row_bg, width=w, height=0).pack()
            return f

        # ── Col 1 : Initiative ─────────────────────────────────────────────
        init_f = _col(56, padx=(6, 2))

        init_var = tk.StringVar(value=str(c.initiative))
        init_entry = tk.Entry(init_f, textvariable=init_var, width=4,
                              bg=C["entry_bg"], fg=C["fg_gold"] if active else C["gold"],
                              font=("Consolas", 13, "bold"),
                              insertbackground=C["gold"], relief="flat",
                              justify="center")
        init_entry.pack(fill=tk.X, ipady=2)

        def _set_init(event, cb=c, var=init_var):
            try:
                cb.initiative = int(var.get())
            except ValueError:
                var.set(str(cb.initiative))

        init_entry.bind("<FocusOut>", _set_init)
        init_entry.bind("<Return>",   _set_init)

        tk.Button(init_f, text="[D]", bg=row_bg, fg="#c8a820",
                  font=("Consolas", 8, "bold"), bd=0, relief="flat", cursor="hand2",
                  command=lambda cb=c: self._roll_one_initiative(cb)
                  ).pack()

        # ── Col 2 : Nom + badge + boutons ─────────────────────────────────
        name_f = _col(158)

        badge    = "PJ" if c.is_pc else "PNJ"
        badge_bg = _darken(c.color, 0.45) if c.is_pc else "#5a2a10"
        tk.Label(name_f, text=badge, bg=badge_bg, fg="white",
                 font=("Consolas", 7, "bold"), padx=4, pady=1
                 ).pack(anchor="w")

        skull = " [X]" if c.is_dead else (" [~]" if c.is_down else "")
        star  = " *"   if active else ""
        name_lbl = tk.Label(name_f, text=c.name + skull + star, bg=row_bg,
                            fg=C["fg_gold"] if active else c.color,
                            font=("Consolas", 11, "bold") if c.is_pc else ("Consolas", 10, "bold"),
                            anchor="w", width=16)
        name_lbl.pack(anchor="w")

        name_lbl.bind("<Enter>", lambda e, cb=c: self._row_enter(e, cb))
        name_lbl.bind("<Leave>", lambda e, cb=c: self._row_leave(e, cb))

        # Boutons sous le nom
        btn_row = tk.Frame(name_f, bg=row_bg)
        btn_row.pack(anchor="w")

        # Bouton retirer — confirmation pour les PJ
        def _confirm_remove(cb=c):
            if cb.is_pc:
                if not messagebox.askyesno(
                    "Retirer du combat",
                    f"Retirer {cb.name} du combat ?\n(PV et stats non modifies)",
                    parent=self.win):
                    return
            self._remove_combatant(cb)

        tk.Button(btn_row,
                  text="Retirer" if c.is_pc else "X",
                  bg=_darken("#e05050", 0.55), fg="#e07070",
                  font=("Consolas", 7, "bold"), bd=0, relief="flat",
                  cursor="hand2", padx=3,
                  command=_confirm_remove).pack(side=tk.LEFT)

        # Bouton Fiche pour les PNJ ayant un bestiary_name
        if not c.is_pc and _BESTIARY_OK and c.bestiary_name:
            def _open_fiche(cb=c):
                MonsterSheetWindow(self.root, cb.name,
                                   bestiary_name=cb.bestiary_name,
                                   chat_queue=self.chat_queue)
            tk.Button(btn_row, text="Fiche",
                      bg=_darken(C["gold"], 0.55), fg=C["gold"],
                      font=("Consolas", 7, "bold"), bd=0, relief="flat",
                      cursor="hand2", padx=3,
                      command=_open_fiche).pack(side=tk.LEFT, padx=(3, 0))

        # Bouton Kill Pool (PNJ uniquement)
        if not c.is_pc:
            tk.Button(btn_row, text="[Mort]",
                      bg=_darken("#800080", 0.45), fg="#cc66cc",
                      font=("Consolas", 7, "bold"), bd=0, relief="flat",
                      cursor="hand2", padx=3,
                      command=lambda cb=c: self._add_to_kill_pool(cb)
                      ).pack(side=tk.LEFT, padx=(3, 0))

        if not c.is_pc:
            def _spawn_token(cb=c):
                if getattr(self.app, "_combat_map_win", None):
                    # Trouver la taille du monstre depuis le bestiaire
                    tok_size = 1.0
                    bname = getattr(cb, "bestiary_name", "")
                    if bname:
                        try:
                            from npc_bestiary_panel import get_monster
                            m = get_monster(bname)
                            if m and "size" in m:
                                size_map = {"T": 1.0, "S": 1.0, "M": 1.0, "L": 2.0, "H": 3.0, "G": 4.0}
                                sz = m["size"][0] if m["size"] else "M"
                                tok_size = size_map.get(sz.upper(), 1.0)
                        except Exception as e:
                            print(f"[CombatTracker] Erreur taille PNJ : {e}")

                    self.app._combat_map_win.place_new_token(
                        cb.name, "monster",
                        size=tok_size,
                        hp=cb.hp, max_hp=cb.max_hp, ac=cb.ac,
                        conditions=list(cb.conditions.keys()),
                        tactics=list(cb.tactics.keys()),
                        alignment=getattr(cb, "alignment", "hostile"),
                        portrait=getattr(cb, "portrait", ""),
                        source_name=bname)
                else:
                    if self.chat_queue:
                        self.chat_queue.put({
                            "sender": "⚙️ Système",
                            "text": "La carte doit être ouverte pour placer un token.",
                            "color": "#888888"
                        })

            tk.Button(btn_row, text="[📍]",
                      bg=_darken(C["green"], 0.45), fg=C["green_bright"],
                      font=("Consolas", 7, "bold"), bd=0, relief="flat",
                      cursor="hand2", padx=3,
                      command=_spawn_token).pack(side=tk.LEFT, padx=(3, 0))

        # ── Col 3 : PV ────────────────────────────────────────────────────
        hp_f = _col(162)

        hp_font = ("Consolas", 13, "bold") if c.is_pc else ("Consolas", 10, "bold")
        temp_suffix = f"  +{c.temp_hp}✦" if c.temp_hp > 0 else ""
        hp_lbl  = tk.Label(hp_f,
                           text=f"{max(0,c.hp)} / {c.max_hp}{temp_suffix}",
                           bg=row_bg, fg=c.hp_color(), font=hp_font)
        hp_lbl.pack(anchor="w")

        bar_canvas = tk.Canvas(hp_f, height=6, bg="#1a1a1a", highlightthickness=0)
        bar_canvas.pack(fill=tk.X, pady=(1, 3))

        def draw_hp_bar(canvas=bar_canvas, cb=c):
            w = canvas.winfo_width()
            if w < 4:
                w = 140
            canvas.delete("all")
            # Fond
            canvas.create_rectangle(0, 0, w, 6, fill="#1a1a1a", outline="")
            # PV réels
            filled = int(w * cb.hp_pct())
            if filled > 0:
                canvas.create_rectangle(0, 0, filled, 6, fill=cb.hp_color(), outline="")
            # PV temporaires — segment jaune superposé à droite des PV réels
            if cb.temp_hp > 0:
                temp_w = max(3, int(w * min(1.0, cb.temp_hp / max(cb.max_hp, 1))))
                x0 = min(filled, w - temp_w)
                canvas.create_rectangle(x0, 0, x0 + temp_w, 6, fill="#f1c40f", outline="")

        bar_canvas.bind("<Configure>",
                        lambda e, cb=c, canvas=bar_canvas: (
                            draw_hp_bar(canvas, cb) if canvas.winfo_exists() else None
                        ))

        hp_btn_f = tk.Frame(hp_f, bg=row_bg)
        hp_btn_f.pack(anchor="w")

        dmg_var = tk.StringVar(value="")
        hp_entry = tk.Entry(hp_btn_f, textvariable=dmg_var, width=5,
                            bg=C["entry_bg"], fg=C["fg"], font=("Consolas", 9),
                            insertbackground=C["fg"], relief="flat", justify="center")
        hp_entry.pack(side=tk.LEFT, ipady=2, padx=(0, 2))

        def apply_dmg(sign, cb=c, var=dmg_var, lbl=hp_lbl, canvas=bar_canvas):
            try:
                val = int(var.get()) if var.get().strip() else 0
            except ValueError:
                val = 0
            var._last_val = val
            was_up = cb.hp > 0

            if sign < 0 and cb.temp_hp > 0:
                # Dégâts : les PV temp absorbent en premier
                absorbed = min(cb.temp_hp, val)
                cb.temp_hp -= absorbed
                val -= absorbed

            cb.hp = max(0, min(cb.max_hp, cb.hp + sign * val))

            temp_suffix = f"  +{cb.temp_hp}✦" if cb.temp_hp > 0 else ""
            lbl.config(text=f"{max(0,cb.hp)} / {cb.max_hp}{temp_suffix}",
                       fg=cb.hp_color(),
                       font=("Consolas", 13, "bold") if cb.is_pc else ("Consolas", 10, "bold"))
            draw_hp_bar(canvas, cb)
            var.set("")
            # ── Sync bidirectionnel : tracker → campaign_state["characters"] ──
            if cb.is_pc:
                _name, _hp = cb.name, cb.hp
                def _sync_hp(name=_name, hp=_hp):
                    try:
                        from state_manager import load_state as _ls, save_state as _ss
                        _st = _ls()
                        if name in _st.get("characters", {}):
                            _st["characters"][name]["hp"] = hp
                            _ss(_st)
                    except Exception as _e:
                        print(f"[CombatTracker] Sync HP -> state_manager : {_e}")
                threading.Thread(target=_sync_hp, daemon=True, name="ct-hp-sync").start()
                
            # ── Synchro avec la carte pour tokens correspondants ──
            if getattr(self, "app", None) is not None:
                map_win = getattr(self.app, "_combat_map_win", None)
                if map_win is not None and hasattr(map_win, "tokens"):
                    for tok in map_win.tokens:
                        if tok.get("name") == cb.name:
                            tok["hp"] = cb.hp
                            if hasattr(map_win, "_redraw_one_token"):
                                map_win._redraw_one_token(tok)
                            if getattr(var, "_last_val", 0) > 0 and hasattr(map_win, "spawn_floating_text"):
                                prefix = "−" if sign < 0 else "+"
                                color = "#ef5350" if sign < 0 else "#4caf50"
                                map_win.spawn_floating_text(tok, f"{prefix}{getattr(var, '_last_val')}", color)
                    if hasattr(map_win, "_save_state"):
                        map_win._save_state()

            self._schedule_save()
            if cb.is_pc and cb.hp == 0 and was_up:
                self._refresh_list()
                self._open_death_saves(cb)

        tk.Button(hp_btn_f, text="+ Soin",
                  bg=_darken(C["green"], 0.35), fg=C["green_bright"],
                  font=("Consolas", 7, "bold"), relief="flat", padx=3, cursor="hand2",
                  command=lambda cb=c, v=dmg_var, l=hp_lbl,
                  canvas=bar_canvas: apply_dmg(+1, cb, v, l, canvas)
                  ).pack(side=tk.LEFT)
        tk.Button(hp_btn_f, text="- Degat",
                  bg=_darken(C["red"], 0.35), fg=C["red_bright"],
                  font=("Consolas", 7, "bold"), relief="flat", padx=3, cursor="hand2",
                  command=lambda cb=c, v=dmg_var, l=hp_lbl,
                  canvas=bar_canvas: apply_dmg(-1, cb, v, l, canvas)
                  ).pack(side=tk.LEFT, padx=(2, 0))

        def apply_temp(cb=c, var=dmg_var, lbl=hp_lbl, canvas=bar_canvas):
            try:
                val = int(var.get()) if var.get().strip() else 0
            except ValueError:
                val = 0
            if val <= 0:
                return
            cb.temp_hp = max(cb.temp_hp, val)   # règle 5e : on prend le meilleur
            temp_suffix = f"  +{cb.temp_hp}✦" if cb.temp_hp > 0 else ""
            lbl.config(text=f"{max(0,cb.hp)} / {cb.max_hp}{temp_suffix}", fg=cb.hp_color(),
                       font=("Consolas", 13, "bold") if cb.is_pc else ("Consolas", 10, "bold"))
            draw_hp_bar(canvas, cb)
            var.set("")
            # Sync state_manager si PJ
            if cb.is_pc:
                _name, _tmp = cb.name, cb.temp_hp
                def _sync_tmp(name=_name, tmp=_tmp):
                    try:
                        from state_manager import load_state as _ls, save_state as _ss
                        _st = _ls()
                        if name in _st.get("characters", {}):
                            _st["characters"][name]["temp_hp"] = tmp
                            _ss(_st)
                    except Exception as _e:
                        print(f"[CombatTracker] Sync temp_hp : {_e}")
                threading.Thread(target=_sync_tmp, daemon=True, name="ct-tmp-sync").start()
            self._schedule_save()

        tk.Button(hp_btn_f, text="+Tmp",
                  bg=_darken("#f1c40f", 0.35), fg="#f1c40f",
                  font=("Consolas", 7, "bold"), relief="flat", padx=3, cursor="hand2",
                  command=apply_temp
                  ).pack(side=tk.LEFT, padx=(2, 0))

        if c.is_pc and c.is_down:
            self._mini_death_saves(hp_f, c)

        # ── Col 4 : CA ────────────────────────────────────────────────────
        ac_f = _col(52)

        ac_var = tk.StringVar(value=str(c.ac))
        ac_entry = tk.Entry(ac_f, textvariable=ac_var, width=4,
                            bg=C["entry_bg"], fg=C["blue_bright"],
                            font=("Consolas", 11, "bold"),
                            insertbackground=C["blue_bright"],
                            relief="flat", justify="center")
        ac_entry.pack(fill=tk.X, ipady=2)

        def _set_ac(event, cb=c, var=ac_var):
            try:
                cb.ac = int(var.get())
            except ValueError:
                var.set(str(cb.ac))

        ac_entry.bind("<FocusOut>", _set_ac)
        ac_entry.bind("<Return>",   _set_ac)
        tk.Label(ac_f, text="CA", bg=row_bg, fg=C["fg_dim"],
                 font=("Consolas", 7)).pack()

        # ── Col 5 : Conditions et Tactiques ──────────────────────────────
        cond_f = _col(220)
        cond_btns = self._build_conditions_widget(cond_f, c, row_bg)
        tac_btns = self._build_tactics_widget(cond_f, c, row_bg)

        # ── Col 6 : Actions ───────────────────────────────────────────────
        act_f = _col(162)
        act_inner, action_vars = self._build_action_economy(act_f, c, row_bg, active)

        # Bouton réinit — uniquement sur la ligne active ; géré par _update_active_rows
        if active:
            reset_btn = tk.Button(act_inner, text="↺ Réinit. actions",
                                  bg=_darken(C["gold"], 0.3), fg=C["gold"],
                                  font=("Consolas", 7, "bold"), relief="flat",
                                  padx=4, cursor="hand2",
                                  command=lambda cb=c: (cb.reset_turn_resources(),
                                                        self._refresh_list()))
            reset_btn.pack(anchor="w", pady=(2, 0))
        else:
            reset_btn = None

        # Stocker toutes les refs — act_inner et reset_btn sont maintenant définis
        self._row_widgets[c.uid] = {
            "hp_lbl":      hp_lbl,
            "bar_canvas":  bar_canvas,
            "draw_hp_bar": draw_hp_bar,
            "row_frame":   row,
            "name_lbl":    name_lbl,
            "act_inner":   act_inner,
            "reset_btn":   reset_btn,
            "is_pc":       c.is_pc,
            "combatant":   c,
            "init_var":    init_var,
            "ac_var":      ac_var,
            "action_vars": action_vars,  # dict of action vars
            "cond_btns":   cond_btns,
            "tac_btns":    tac_btns,
        }

        # ── Col 7 : Concentration ─────────────────────────────────────────
        conc_f = _col(58)

        conc_var = tk.BooleanVar(value=c.concentration)
        self._row_widgets[c.uid]["conc_var"] = conc_var  # inject it

        conc_cb  = tk.Checkbutton(conc_f, variable=conc_var,
                                  text="Conc", bg=row_bg,
                                  fg=C["conc"] if c.concentration else C["fg_dim"],
                                  activebackground=row_bg,
                                  selectcolor=_darken(C["conc"], 0.3),
                                  font=("Consolas", 8, "bold"), bd=0)
        conc_cb.pack(anchor="w")

        def _toggle_conc(cb=c, var=conc_var, btn=conc_cb):
            cb.concentration = var.get()
            btn.config(fg=C["conc"] if cb.concentration else C["fg_dim"])

        conc_var.trace_add("write", lambda *a: _toggle_conc())

        # ── Col 8 : Notes ─────────────────────────────────────────────────
        note_f = tk.Frame(row, bg=row_bg)
        note_f.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 8), pady=4)

        note_entry = tk.Entry(note_f, bg=C["entry_bg"], fg=C["fg"],
                              font=("Consolas", 8),
                              insertbackground=C["fg"], relief="flat")
        note_entry.pack(fill=tk.X, ipady=2)
        note_entry.insert(0, c.notes)

        def _save_note(event, cb=c, entry=note_entry):
            cb.notes = entry.get()

        note_entry.bind("<FocusOut>", _save_note)

        if not c.is_pc and c.is_down:
            tk.Label(row, text="KO", bg=row_bg, fg=C["skull"],
                     font=("Consolas", 9, "bold")).pack(side=tk.RIGHT, padx=6)

    def _build_conditions_widget(self, parent, c, row_bg: str):
        """Grille compacte de badges de conditions cliquables."""
        outer = tk.Frame(parent, bg=row_bg)
        outer.pack(fill=tk.BOTH, expand=True)

        # 2 lignes de badges
        row1 = tk.Frame(outer, bg=row_bg)
        row1.pack(fill=tk.X)
        row2 = tk.Frame(outer, bg=row_bg)
        row2.pack(fill=tk.X)

        cond_names = list(CONDITIONS.keys())
        cond_btns = {}

        for i, cname in enumerate(cond_names):
            cdata  = CONDITIONS[cname]
            active = cname in c.conditions
            frame  = row1 if i < 8 else row2

            # Inactif : très sombre. Actif : couleur pure et police blanche.
            btn_bg  = cdata["color"]  if active else _darken(cdata["color"], 0.25)
            btn_fg  = "#ffffff"       if active else "#666677"

            btn = tk.Button(frame, text=cdata["abbr"],
                            bg=btn_bg, fg=btn_fg,
                            font=("Consolas", 7, "bold"),
                            relief="flat", padx=3, pady=1,
                            cursor="hand2")
            btn.pack(side=tk.LEFT, padx=1, pady=1)
            cond_btns[cname] = btn

            # Tooltip
            self._tooltip(btn, f"{cname}\n{cdata['tip']}")

            def _toggle(cb=c, cn=cname, b=btn, cd=cdata):
                enabled = False
                if cn in cb.conditions:
                    del cb.conditions[cn]
                    b.config(bg=_darken(cd["color"], 0.25), fg="#666677")
                else:
                    cb.conditions[cn] = True
                    b.config(bg=cd["color"], fg="#ffffff")
                    enabled = True
                    
                # ── Synchro avec la carte ──
                if getattr(self.app, "_combat_map_win", None):
                    for t in self.app._combat_map_win.tokens:
                        if t.get("name") == cb.name:
                            t.setdefault("conditions", [])
                            if enabled and cn not in t["conditions"]:
                                t["conditions"].append(cn)
                            elif not enabled and cn in t["conditions"]:
                                t["conditions"].remove(cn)
                            self.app._combat_map_win._redraw_one_token(t)
                            
                self._schedule_save()

            btn.config(command=_toggle)
        
        return cond_btns

    def _build_tactics_widget(self, parent, c, row_bg: str):
        """Grille compacte de badges tactiques (Esquive, Caché...) - 1 ligne."""
        outer = tk.Frame(parent, bg=row_bg)
        outer.pack(fill=tk.X, expand=True)

        tac_names = list(TACTICS.keys())
        tac_btns = {}
        for i, tname in enumerate(tac_names):
            tdata  = TACTICS[tname]
            active = tname in c.tactics

            btn_bg  = tdata["color"]  if active else _darken(tdata["color"], 0.25)
            btn_fg  = "#ffffff"       if active else "#666677"

            btn = tk.Button(outer, text=tdata["abbr"],
                            bg=btn_bg, fg=btn_fg,
                            font=("Consolas", 7, "bold"),
                            relief="flat", padx=3, pady=1,
                            cursor="hand2")
            btn.pack(side=tk.LEFT, padx=1, pady=1)
            tac_btns[tname] = btn

            self._tooltip(btn, f"{tname}\n{tdata['tip']}")

            def _toggle(cb=c, tn=tname, b=btn, td=tdata):
                enabled = False
                if tn in cb.tactics:
                    del cb.tactics[tn]
                    b.config(bg=_darken(td["color"], 0.25), fg="#666677")
                else:
                    cb.tactics[tn] = True
                    b.config(bg=td["color"], fg="#ffffff")
                    enabled = True
                    
                # ── Synchro avec la carte ──
                if getattr(self.app, "_combat_map_win", None):
                    for t in self.app._combat_map_win.tokens:
                        if t.get("name") == cb.name:
                            t.setdefault("tactics", [])
                            if enabled and tn not in t["tactics"]:
                                t["tactics"].append(tn)
                            elif not enabled and tn in t["tactics"]:
                                t["tactics"].remove(tn)
                            self.app._combat_map_win._redraw_one_token(t)
                            
                self._schedule_save()

            btn.config(command=_toggle)
            
        return tac_btns

    def _build_action_economy(self, parent, c, row_bg: str, active: bool):
        """Cases à cocher pour Action / Bonus / Réaction + mouvement.
        Retourne le frame inner pour permettre l'ajout externe du bouton réinit."""
        inner = tk.Frame(parent, bg=row_bg)
        inner.pack(fill=tk.BOTH, expand=True)

        def check_row(row_parent, label, color, used_attr):
            var = tk.BooleanVar(value=getattr(c, used_attr))
            fg  = C["red_bright"] if getattr(c, used_attr) else color

            cb = tk.Checkbutton(row_parent, text=label, variable=var,
                                bg=row_bg, fg=fg,
                                activebackground=row_bg, activeforeground=color,
                                selectcolor="#222233",
                                font=("Consolas", 8), padx=0)
            cb.pack(side=tk.LEFT)

            def _upd(attr=used_attr, v=var, btn=cb, c_=color):
                setattr(c, attr, v.get())
                btn.config(fg=C["red_bright"] if v.get() else c_)

            var.trace_add("write", lambda *a: _upd())
            return var

        r1 = tk.Frame(inner, bg=row_bg)
        r1.pack(fill=tk.X)
        v_act = check_row(r1, "✦ Action",       C["gold"],         "action_used")
        v_bon = check_row(r1, "◈ Bonus",        "#d06800",         "bonus_used")

        r2 = tk.Frame(inner, bg=row_bg)
        r2.pack(fill=tk.X)
        v_rea = check_row(r2, "↺ Réaction",     C["blue_bright"],  "reaction_used")

        # Mouvement
        r3 = tk.Frame(inner, bg=row_bg)
        r3.pack(fill=tk.X)
        tk.Label(r3, text="Mvt:", bg=row_bg, fg=C["fg_dim"],
                 font=("Consolas", 7)).pack(side=tk.LEFT)
        mv_var = tk.StringVar(value=str(c.move_used))
        mv_e   = tk.Entry(r3, textvariable=mv_var, width=4,
                          bg=C["entry_bg"], fg=C["fg"],
                          font=("Consolas", 8),
                          insertbackground=C["fg"], relief="flat",
                          justify="center")
        mv_e.pack(side=tk.LEFT, ipady=1, padx=(2, 1))
        tk.Label(r3, text="ft", bg=row_bg, fg=C["fg_dim"],
                 font=("Consolas", 7)).pack(side=tk.LEFT)

        def _set_mv(event, cb=c, var=mv_var):
            try:
                cb.move_used = int(var.get())
            except ValueError:
                var.set("0")

        mv_e.bind("<FocusOut>", _set_mv)
        mv_e.bind("<Return>",   _set_mv)

        # Le bouton "↺ Réinit. actions" est ajouté par _build_row (actif)
        # ou par _update_active_rows (changement de tour) — pas ici.
        return inner, {"action": v_act, "bonus": v_bon, "react": v_rea, "move": mv_var}

    def _mini_death_saves(self, parent, c):
        """Affiche les jets de mort compacts sous la barre de vie."""
        f = tk.Frame(parent, bg=parent.cget("bg"))
        f.pack(anchor="w", pady=(2, 0))

        tk.Label(f, text="Sauv. mort →", bg=f.cget("bg"),
                 fg=C["skull"], font=("Consolas", 7)).pack(side=tk.LEFT)

        def _suc():
            if c.death_saves_success < 3:
                c.death_saves_success += 1
            self._refresh_list()
            if c.death_saves_success >= 3:
                self._log(f"✅ {c.name} est stabilisé(e) !")

        def _fail():
            if c.death_saves_fail < 3:
                c.death_saves_fail += 1
            self._refresh_list()
            if c.death_saves_fail >= 3:
                self._log(f"💀 {c.name} est mort(e) !")

        tk.Button(f, text=f"✓×{c.death_saves_success}",
                  bg=_darken(C["green"], 0.3), fg=C["green"],
                  font=("Consolas", 7), relief="flat", padx=3,
                  command=_suc).pack(side=tk.LEFT, padx=1)
        tk.Button(f, text=f"✗×{c.death_saves_fail}",
                  bg=_darken(C["red"], 0.3), fg=C["red_bright"],
                  font=("Consolas", 7), relief="flat", padx=3,
                  command=_fail).pack(side=tk.LEFT, padx=1)

    # ─── Image Tooltip (Survol) ──────────────────────────────────────────────

    def _row_enter(self, event, cb):
        if getattr(self, "_leave_timer", None):
            self.win.after_cancel(self._leave_timer)
            self._leave_timer = None
            
        if getattr(self, "_hovered_cb", None) == cb:
            self._hover_x = event.x_root
            self._hover_y = event.y_root
            return
            
        self._row_leave_now()
        self._hovered_cb = cb
        self._hover_x = event.x_root
        self._hover_y = event.y_root
        self._hover_timer = self.win.after(500, self._show_row_image_tooltip)

    def _row_leave(self, event, cb):
        if getattr(self, "_leave_timer", None):
            self.win.after_cancel(self._leave_timer)
        self._leave_timer = self.win.after(50, self._row_leave_now)

    def _row_leave_now(self):
        if getattr(self, "_hover_timer", None):
            self.win.after_cancel(self._hover_timer)
            self._hover_timer = None
        if getattr(self, "_img_tooltip_win", None):
            self._img_tooltip_win.destroy()
            self._img_tooltip_win = None
        self._hovered_cb = None

    def _show_row_image_tooltip(self):
        cb = getattr(self, "_hovered_cb", None)
        if not cb: return

        import os
        print(f"[Debug-Tooltip-Tracker] Survol de : '{cb.name}'")
        img_path = None
        source = ""

        # Helper : vérifie qu'un chemin est bien sous images/portraits/
        def _in_portraits(p: str) -> bool:
            try:
                from portrait_resolver import _PORTRAITS_ROOT
                return os.path.abspath(p).startswith(os.path.abspath(_PORTRAITS_ROOT))
            except Exception:
                return False

        # ── 0. Portrait pré-résolu depuis images/portraits/ ──────────────────
        # Accepté uniquement s'il provient de images/portraits/.
        pre = getattr(cb, "portrait", "")
        if pre and os.path.exists(pre) and _in_portraits(pre):
            img_path = pre
            source = "Portrait pré-résolu"

        # ── 1. Chercher dans les personnages (Héros) ─────────────────────────
        # Accepté uniquement si le chemin est dans images/portraits/.
        if not img_path:
            try:
                from state_manager import load_state
                st = load_state()
                chars = st.get("characters", {})
                cdata = chars.get(cb.name)
                if not cdata:
                    for k, v in chars.items():
                        if k.lower() == cb.name.lower():
                            cdata = v
                            break
                if cdata:
                    p = cdata.get("image") or cdata.get("portrait")
                    if p and os.path.exists(p) and _in_portraits(p):
                        img_path = p
                        source = "Héros (characters)"
            except Exception as e:
                print(f"[Debug-Tooltip-Tracker] Erreur Héros : {e}")

        # ── 2. Tenter une résolution live via portrait_resolver ───────────────
        # (couvre les combatants chargés depuis une ancienne sauvegarde sans portrait)
        if not img_path:
            try:
                from portrait_resolver import resolve_portrait
                import re
                lookup = getattr(cb, "bestiary_name", "") or re.sub(r'\s+\d+$', '', cb.name).strip()
                p = resolve_portrait(lookup)
                if p and os.path.exists(p):
                    img_path = p
                    source = "Portrait resolver"
                    cb.portrait = p  # mise en cache sur l'objet
            except Exception as e:
                print(f"[Debug-Tooltip-Tracker] Erreur portrait_resolver : {e}")

        print(f"[Debug-Tooltip-Tracker] Chemin d'image trouvé : {img_path} (Source: {source})")

        # ── 3. File-dialog en dernier recours (uniquement si aucun portrait trouvé)
        # Ne s'affiche pas si portrait_resolver a déjà trouvé une image.
        if not img_path:
            asked_attr = f"_asked_image_{id(cb)}"
            if not getattr(self, asked_attr, False):
                setattr(self, asked_attr, True)
                print(f"[Debug-Tooltip-Tracker] Aucun portrait automatique — demande fichier…")
                import tkinter.filedialog as fd
                new_path = fd.askopenfilename(
                    title=f"Portrait manquant pour {cb.name} — localiser le fichier :",
                    filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.webp")],
                    parent=self.win
                )
                if new_path and os.path.exists(new_path) and _in_portraits(new_path):
                    print(f"[Debug-Tooltip-Tracker] Fichier sélectionné : {new_path}")
                    cb.portrait = new_path
                    img_path = new_path
                    if cb.is_pc:
                        try:
                            from state_manager import load_state, save_state
                            st = load_state()
                            if cb.name in st.get("characters", {}):
                                st["characters"][cb.name]["image"] = new_path
                                save_state(st)
                        except Exception:
                            pass
                elif new_path and not _in_portraits(new_path):
                    print(f"[Debug-Tooltip-Tracker] Fichier hors images/portraits/ — ignoré : {new_path}")
                else:
                    print("[Debug-Tooltip-Tracker] Demande annulée ou fichier invalide.")
            else:
                print("[Debug-Tooltip-Tracker] Déjà demandé auparavant, on ignore.")

        # ── 4. Afficher l'image dans une fenêtre flottante ───────────────────
        if img_path and os.path.exists(img_path):
            print("[Debug-Tooltip-Tracker] Affichage de la fenêtre...")
            try:
                from PIL import Image, ImageTk
                import tkinter as tk
                tw = tk.Toplevel(self.win)
                tw.wm_overrideredirect(True)
                x = self._hover_x + 15
                y = self._hover_y + 15
                tw.geometry(f"+{x}+{y}")
                tw.configure(bg="#000000", highlightbackground="#ffffff", highlightthickness=1)
                img = Image.open(img_path)
                img.thumbnail((250, 250))
                photo = ImageTk.PhotoImage(img)
                lbl = tk.Label(tw, image=photo, bg="#000000")
                lbl.image = photo
                lbl.pack(padx=2, pady=2)
                self._img_tooltip_win = tw
            except ImportError:
                pass
            except Exception as e:
                try: tw.destroy()
                except: pass
                print(f"[CombatTracker] Erreur tooltip image : {e}")