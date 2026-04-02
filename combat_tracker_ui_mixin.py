"""
combat_tracker_ui_mixin.py
──────────────────────────
Fichier 4/10 : Mixin de construction de l'interface graphique principale.
"""

import tkinter as tk
from tkinter import messagebox

# Imports des dépendances partagées (constantes et bestiaire)
try:
    from combat_tracker_constants import C, _BESTIARY_OK
    if _BESTIARY_OK:
        from npc_bestiary_panel import (
            search_monsters  as _bestiary_search,
            get_monster      as _bestiary_get,
            _load_bestiary   as _bestiary_load
        )
except ImportError:
    pass

# On suppose que _darken sera importé globalement ou hérité
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


class CombatTrackerUIMixin:
    """Mixin regroupant la construction de l'interface du tracker de combat."""

    def _build_window(self):
        self.win = tk.Toplevel(self.root)
        self.win.title("⚔️  Suivi de Combat — D&D 5e")
        self.win.geometry("980x700")
        self.win.configure(bg=C["bg"])
        self.win.minsize(820, 540)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_topbar()
        self._build_columns_header()
        self._build_list_area()
        self._build_bottom_panel()

        # Raccourci clavier global : F3 → Tour suivant (actif même sans focus sur cette fenêtre)
        self.root.bind_all("<F3>", lambda e: self._next_turn())

    def _build_topbar(self):
        bar = tk.Frame(self.win, bg="#080a10", height=54)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        # Titre
        tk.Label(bar, text="⚔  COMBAT TRACKER", bg="#080a10",
                 fg=C["gold"], font=("Consolas", 14, "bold")).pack(side=tk.LEFT, padx=16, pady=10)

        # Round counter
        self._round_var = tk.StringVar(value="Round  —")
        self._round_lbl = tk.Label(bar, textvariable=self._round_var,
                                   bg="#080a10", fg=C["fg_gold"],
                                   font=("Consolas", 16, "bold"))
        self._round_lbl.pack(side=tk.LEFT, padx=24)

        # Boutons combat
        right = tk.Frame(bar, bg="#080a10")
        right.pack(side=tk.RIGHT, padx=12)

        self._btn_start = self._tb_btn(right, "▶ LANCER LE COMBAT", C["green"], self._start_combat)
        self._btn_start.pack(side=tk.LEFT, padx=4)

        self._btn_next = self._tb_btn(right, "▶▶ TOUR SUIVANT", C["gold"], self._next_turn)
        self._btn_next.pack(side=tk.LEFT, padx=4)
        self._btn_next.config(state=tk.DISABLED)

        self._btn_end = self._tb_btn(right, "✕ FIN DU COMBAT", C["red"], self._end_combat)
        self._btn_end.pack(side=tk.LEFT, padx=4)
        self._btn_end.config(state=tk.DISABLED)

        self._btn_roll_all = self._tb_btn(right, "🎲 Roll Initiative", C["blue"], self._roll_all_initiative)
        self._btn_roll_all.pack(side=tk.LEFT, padx=(16, 4))

    def _tb_btn(self, parent, text, color, cmd):
        return tk.Button(parent, text=text, bg=_darken(color, 0.5),
                         fg=color, font=("Consolas", 9, "bold"),
                         activebackground=_darken(color, 0.7),
                         activeforeground="white",
                         relief="flat", padx=10, pady=4, cursor="hand2",
                         command=cmd)

    def _build_columns_header(self):
        hdr = tk.Frame(self.win, bg="#0d1018", height=24)
        hdr.pack(fill=tk.X, padx=8)
        hdr.pack_propagate(False)

        # Largeurs en pixels — doivent correspondre aux frames de _build_row
        COL_WIDTHS =[
            ("Init",       56),
            ("Nom",       158),
            ("PV",        162),
            ("CA",         52),
            ("Conditions", 220),
            ("Actions",   162),
            ("Conc.",      58),
            ("Notes",       0),   # 0 = remplit le reste
        ]
        for label, w in COL_WIDTHS:
            f = tk.Frame(hdr, bg="#0d1018",
                         width=w if w else 1,
                         height=24)
            f.pack(side=tk.LEFT, padx=2)
            f.pack_propagate(False)
            tk.Label(f, text=label, bg="#0d1018", fg="#c8cfd8",
                     font=("Consolas", 8, "bold"), anchor="w"
                     ).pack(fill=tk.X, padx=3)
            if w == 0:
                f.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        tk.Frame(self.win, bg=C["border"], height=1).pack(fill=tk.X, padx=6)

    def _build_list_area(self):
        cont = tk.Frame(self.win, bg=C["bg"])
        cont.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        self._canvas = tk.Canvas(cont, bg=C["bg"], highlightthickness=0)
        self._scroll = tk.Scrollbar(cont, orient="vertical",
                                    command=self._canvas.yview)
        self._inner = tk.Frame(self._canvas, bg=C["bg"])

        # FIX SEGFAULT : PAS de <Configure> sur self._inner — polling à la place.
        def _poll_ct_scroll():
            try:
                if not self._inner.winfo_exists(): return
                self._canvas.configure(scrollregion=self._canvas.bbox("all"))
                self._inner.after(2000, _poll_ct_scroll)
            except Exception:
                pass
        self._inner.after(500, _poll_ct_scroll)

        self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._canvas.configure(yscrollcommand=self._scroll.set)

        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Scroll molette
        self._canvas.bind_all("<MouseWheel>",
            lambda e: self._canvas.yview_scroll(-1*(e.delta//120), "units"))

    def _build_bottom_panel(self):
        sep = tk.Frame(self.win, bg=C["border"], height=1)
        sep.pack(fill=tk.X, padx=6, pady=(4, 0))

        # Pas de height fixe ni pack_propagate(False) — le panel s'adapte
        bot = tk.Frame(self.win, bg="#0d1018")
        bot.pack(fill=tk.X)

        # ── Ajouter un PNJ ─────────────────────────────────────────────────
        add_frame = tk.Frame(bot, bg="#0d1018")
        add_frame.pack(side=tk.LEFT, padx=16, pady=10)

        tk.Label(add_frame, text="AJOUTER UN COMBATANT",
                 bg="#0d1018", fg=C["fg_dim"],
                 font=("Consolas", 8, "bold")).grid(row=0, columnspan=8, sticky="w", pady=(0, 2))

        def lbl(text):
            return tk.Label(add_frame, text=text, bg="#0d1018",
                            fg=C["fg_dim"], font=("Consolas", 8))

        def ent(w, default=""):
            e = tk.Entry(add_frame, bg=C["entry_bg"], fg=C["fg"],
                         font=("Consolas", 10), insertbackground=C["fg"],
                         relief="flat", width=w)
            e.insert(0, default)
            return e

        # ── Ligne recherche bestiary ───────────────────────────────────────
        self._current_bestiary_name = ""
        if _BESTIARY_OK:
            search_frame = tk.Frame(add_frame, bg="#0d1018")
            search_frame.grid(row=1, column=0, columnspan=8, sticky="w", pady=(0, 4))

            tk.Label(search_frame, text="Fiche:", bg="#0d1018", fg=C["gold"],
                     font=("Consolas", 8, "bold")).pack(side=tk.LEFT)

            self._ct_search_var = tk.StringVar()
            self._ct_search_entry = tk.Entry(
                search_frame, textvariable=self._ct_search_var,
                bg=C["entry_bg"], fg=C["fg"], font=("Consolas", 9),
                insertbackground=C["fg"], relief="flat", width=22)
            self._ct_search_entry.pack(side=tk.LEFT, padx=(4, 6), ipady=2)

            self._ct_status = tk.Label(search_frame, text="", bg="#0d1018",
                                       fg=C["fg_dim"], font=("Consolas", 8))
            self._ct_status.pack(side=tk.LEFT)

            self._ct_suggest_frame  = tk.Frame(add_frame, bg="#0d1018", bd=1, relief="solid")
            self._ct_suggest_labels: list[tk.Label] =[]
            self._ct_suggest_visible = False
            self._ct_suggest_idx    = -1

            def _on_search(*_):
                query = self._ct_search_var.get().strip()
                self._ct_hide_suggest()
                if len(query) < 1:
                    return
                results = _bestiary_search(query, max_results=8)
                if not results:
                    return
                for w in self._ct_suggest_frame.winfo_children():
                    w.destroy()
                self._ct_suggest_labels.clear()
                self._ct_suggest_idx = -1
                for res_name in results:
                    lw = tk.Label(self._ct_suggest_frame, text=res_name,
                                  bg="#0d1018", fg=C["fg"],
                                  font=("Consolas", 9), anchor="w",
                                  padx=8, pady=2, cursor="hand2")
                    lw.pack(fill=tk.X)
                    lw.bind("<Enter>",    lambda e, l=lw: l.config(bg=C["border"]))
                    lw.bind("<Leave>",    lambda e, l=lw: l.config(bg="#0d1018"))
                    lw.bind("<Button-1>", lambda e, n=res_name: self._ct_pick(n))
                    self._ct_suggest_labels.append(lw)
                self._ct_suggest_frame.place(
                    in_=search_frame,
                    x=self._ct_search_entry.winfo_x(),
                    y=search_frame.winfo_height() + 2,
                    width=240)
                self._ct_suggest_visible = True

            self._ct_search_var.trace_add("write", _on_search)
            self._ct_search_entry.bind("<Escape>",   lambda e: self._ct_hide_suggest())
            self._ct_search_entry.bind("<FocusOut>",
                lambda e: self.win.after(150, self._ct_hide_suggest))

            def _ct_nav(event):
                if not self._ct_suggest_visible or not self._ct_suggest_labels:
                    return
                if event.keysym == "Down":
                    self._ct_suggest_idx = min(
                        self._ct_suggest_idx + 1, len(self._ct_suggest_labels) - 1)
                elif event.keysym == "Up":
                    self._ct_suggest_idx = max(self._ct_suggest_idx - 1, 0)
                elif event.keysym == "Return":
                    if 0 <= self._ct_suggest_idx < len(self._ct_suggest_labels):
                        self._ct_pick(
                            self._ct_suggest_labels[self._ct_suggest_idx].cget("text"))
                    return
                for i, l in enumerate(self._ct_suggest_labels):
                    l.config(bg=C["border"] if i == self._ct_suggest_idx else "#0d1018")

            self._ct_search_entry.bind("<Down>",   _ct_nav)
            self._ct_search_entry.bind("<Up>",     _ct_nav)
            self._ct_search_entry.bind("<Return>", _ct_nav)

            field_label_row = 2
        else:
            field_label_row = 1

        # ── Labels + champs ────────────────────────────────────────────────
        for col, text in enumerate(["Nom", "PV max", "CA", "Init+", "Init=", "Qte", "Align."]):
            lbl(text).grid(row=field_label_row, column=col, padx=(0,2), sticky="w")

        fr = field_label_row + 1
        self._npc_name       = ent(12, "Gobelin");   self._npc_name.grid(row=fr, column=0, padx=(0,4), ipady=3)
        self._npc_hp         = ent(5,  "15");         self._npc_hp.grid(row=fr,  column=1, padx=(0,4), ipady=3)
        self._npc_ac         = ent(4,  "13");         self._npc_ac.grid(row=fr,  column=2, padx=(0,4), ipady=3)
        self._npc_dex        = ent(4,  "1");          self._npc_dex.grid(row=fr, column=3, padx=(0,4), ipady=3)
        self._npc_init_fixed = ent(4,  "");           self._npc_init_fixed.grid(row=fr, column=4, padx=(0,4), ipady=3)
        self._npc_qty        = ent(3,  "1");          self._npc_qty.grid(row=fr, column=5, padx=(0,4), ipady=3)

        # ── Sélecteur d'alignement (Hostile / Neutral / Allié) ────────────────
        self._npc_alignment = tk.StringVar(value="hostile")
        align_frame = tk.Frame(add_frame, bg="#0d1018")
        align_frame.grid(row=fr, column=6, padx=(2, 6))

        _ALIGN_CFG = [
            ("H", "hostile", "#e53935", "#3a0a0a"),
            ("N", "neutral", "#fdd835", "#3a3200"),
            ("A", "ally",    "#43a047", "#0a2a0a"),
        ]
        self._align_btns = {}
        def _make_align_btn(label, value, fg_on, bg_on):
            def _select(v=value):
                self._npc_alignment.set(v)
                for val, btn in self._align_btns.items():
                    cfg = next(c for c in _ALIGN_CFG if c[1] == val)
                    if val == v:
                        btn.config(bg=cfg[3], fg=cfg[2], relief="sunken")
                    else:
                        btn.config(bg="#1a1a2a", fg="#555577", relief="flat")
            btn = tk.Button(align_frame, text=label,
                            bg="#1a1a2a", fg="#555577",
                            font=("Consolas", 8, "bold"), relief="flat",
                            padx=5, pady=2, cursor="hand2",
                            command=_select)
            btn.pack(side=tk.LEFT, padx=1)
            self._align_btns[value] = btn
            return btn

        for _al, _av, _fg, _bg in _ALIGN_CFG:
            _make_align_btn(_al, _av, _fg, _bg)
        # Active "hostile" par défaut
        self._align_btns["hostile"].config(
            bg="#3a0a0a", fg="#e53935", relief="sunken")

        tk.Button(add_frame, text="+ Ajouter",
                  bg=_darken(C["blue"], 0.4), fg=C["blue_bright"],
                  font=("Consolas", 9, "bold"), relief="flat",
                  padx=8, pady=3, cursor="hand2",
                  command=self._add_npc).grid(row=fr, column=7, padx=(4, 0))

        tk.Button(add_frame, text="+ Héros",
                  bg=_darken(C["green"], 0.35), fg=C["green_bright"],
                  font=("Consolas", 9, "bold"), relief="flat",
                  padx=8, pady=3, cursor="hand2",
                  command=self._add_missing_pc).grid(row=fr, column=8, padx=(4, 0))

        # ── Infos combat ───────────────────────────────────────────────────
        info_frame = tk.Frame(bot, bg="#0d1018")
        info_frame.pack(side=tk.RIGHT, padx=20, pady=10)

        self._info_var = tk.StringVar(value="Aucun combat en cours.")
        tk.Label(info_frame, textvariable=self._info_var,
                 bg="#0d1018", fg=C["fg_dim"],
                 font=("Consolas", 9), justify=tk.LEFT).pack(anchor="e")

        tk.Button(info_frame, text="Retrier par initiative",
                  bg=_darken(C["purple"], 0.4), fg="#c070e0",
                  font=("Consolas", 8), relief="flat", padx=6,
                  command=self._sort_and_refresh).pack(anchor="e", pady=(4, 0))

        # ── Kill Pool ──────────────────────────────────────────────────
        kp_frame = tk.Frame(bot, bg="#0d1018")
        kp_frame.pack(side=tk.RIGHT, padx=16, pady=10)
        tk.Label(kp_frame, text="KILL POOL",
                 bg="#0d1018", fg="#9b59b6",
                 font=("Consolas", 8, "bold")).pack(anchor="w")
        self._kill_pool_inner = tk.Frame(kp_frame, bg="#0d1018")
        self._kill_pool_inner.pack(fill=tk.X)
        tk.Label(self._kill_pool_inner, text="— vide —",
                 bg="#0d1018", fg=C["fg_dim"],
                 font=("Consolas", 8)).pack(anchor="w")

    def _ct_hide_suggest(self):
        if hasattr(self, "_ct_suggest_frame"):
            self._ct_suggest_frame.place_forget()
            self._ct_suggest_visible = False

    def _ct_pick(self, bestiary_name: str):
        """Remplit le formulaire avec HP/CA/Init du monstre sélectionné."""
        self._ct_hide_suggest()
        if not _BESTIARY_OK:
            return
        _bestiary_load()
        m = _bestiary_get(bestiary_name)
        if not m:
            self._ct_status.config(text="Introuvable", fg=C["red_bright"])
            return

        ac_raw = m.get("ac",[])
        if ac_raw:
            first = ac_raw[0]
            ac = first if isinstance(first, int) else (first.get("ac", 10) if isinstance(first, dict) else 10)
        else:
            ac = 10

        hp_raw = m.get("hp", {})
        hp = hp_raw.get("average", 10) if isinstance(hp_raw, dict) else int(hp_raw or 10)

        dex_mod = (int(m.get("dex", 10)) - 10) // 2

        cr_raw = m.get("cr", "?")
        cr = cr_raw.get("cr", "?") if isinstance(cr_raw, dict) else str(cr_raw)

        def _set(e, v):
            e.delete(0, tk.END)
            e.insert(0, str(v))

        _set(self._npc_name, bestiary_name[:14])
        _set(self._npc_hp,   hp)
        _set(self._npc_ac,   ac)
        _set(self._npc_dex,  dex_mod)
        self._npc_init_fixed.delete(0, tk.END)

        self._current_bestiary_name = bestiary_name
        self._ct_search_var.set("")
        self._ct_status.config(text=f"CR {cr}  PV:{hp}  CA:{ac}", fg=C["green_bright"])