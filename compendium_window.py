import tkinter as tk
from tkinter import ttk
import re

import spell_data
import feat_data
import race_data
import background_data
import class_data

CLASSES_5E = ["Artificer", "Barbarian", "Bard", "Cleric", "Druid", "Fighter", "Monk", "Paladin", "Ranger", "Rogue", "Sorcerer", "Warlock", "Wizard"]

class CompendiumWindow(tk.Toplevel):
    """
    Searchable window providing fast access across Spells, Feats, Classes, Races, and Backgrounds.
    """
    
    BG = "#0b0d12"
    PANEL = "#090c15"
    BORDER = "#2a3040"
    GOLD = "#c8a820"
    FG = "#dde0e8"
    FG_DIM = "#8899aa"
    ENTRY = "#1a1f2e"

    def __init__(self, parent):
        super().__init__(parent)
        self.title("📖 Compendium D&D 5e")
        self.geometry("1000x750")
        self.minsize(800, 600)
        self.configure(bg=self.BG)
        
        self.raw_items = {
            "Sorts": [],
            "Dons": [],
            "Classes": sorted(CLASSES_5E),
            "Races": [],
            "Historiques": []
        }
        
        self._load_data()
        self._build_search_bar()
        self._build_tabs()
        self._on_search()

    def _load_data(self):
        try:
            spell_data.load_spells()
            self.raw_items["Sorts"] = list(spell_data._SPELL_NAMES)
        except Exception: pass
            
        try:
            feat_data.load_feats()
            self.raw_items["Dons"] = list(feat_data._FEAT_NAMES)
        except Exception: pass
            
        try:
            self.raw_items["Races"] = list(race_data.get_available_races())
        except Exception: pass
            
        try:
            self.raw_items["Historiques"] = list(background_data.get_available_backgrounds())
        except Exception: pass

    def _build_search_bar(self):
        top = tk.Frame(self, bg="#080a10", pady=10)
        top.pack(fill=tk.X)

        tk.Label(top, text="🔍 OMNIRECHERCHE", bg="#080a10", fg=self.GOLD, font=("Arial", 11, "bold")).pack(side=tk.LEFT, padx=(16, 10))
        
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(
            top, textvariable=self.search_var,
            bg=self.ENTRY, fg="#ffffff", font=("Consolas", 12),
            insertbackground="#ffffff", relief="flat"
        )
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 16), ipady=5)
        
        self.after_id = None
        self.search_var.trace_add("write", self._schedule_search)

    def _build_tabs(self):
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Comp.TNotebook", background=self.BG, borderwidth=0, tabmargins=[0, 0, 0, 0])
        style.configure("Comp.TNotebook.Tab", background=self.PANEL, foreground=self.FG_DIM, font=("Arial", 10, "bold"), padding=[20, 10])
        style.map("Comp.TNotebook.Tab", background=[("selected", self.ENTRY)], foreground=[("selected", self.GOLD)])

        style.configure("Comp.Treeview", background="#1a1f2e", foreground="#dde0e8", fieldbackground="#1a1f2e", borderwidth=0, font=("Consolas", 10), rowheight=24)
        style.map("Comp.Treeview", background=[("selected", "#2e3b5e")])
        style.configure("Comp.Treeview.Heading", background="#090c15", foreground="#c8a820", font=("Consolas", 10, "bold"), borderwidth=1, relief="flat")


        self.notebook = ttk.Notebook(self, style="Comp.TNotebook")
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.tabs_info = {}
        for cat in ["Sorts", "Dons", "Classes", "Races", "Historiques"]:
            self._build_single_tab(cat)

    def _build_single_tab(self, category):
        tab = tk.Frame(self.notebook, bg=self.ENTRY)
        self.notebook.add(tab, text=f" {category} ")
        
        left = tk.Frame(tab, bg=self.PANEL, width=240)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(4, 0), pady=4)
        left.pack_propagate(False)
        
        sort_var = None
        if category == "Sorts":
            sort_frame = tk.Frame(left, bg=self.PANEL)
            sort_frame.pack(fill=tk.X, pady=(0, 4))
            tk.Label(sort_frame, text="Trier:", bg=self.PANEL, fg=self.FG_DIM, font=("Arial", 9)).pack(side=tk.LEFT, padx=4)
            sort_var = tk.StringVar(value="Nom")
            cb = ttk.Combobox(sort_frame, textvariable=sort_var, values=["Nom", "Niveau", "École"], state="readonly", width=10)
            cb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
            cb.bind("<<ComboboxSelected>>", lambda e: self._on_search())

        list_var = tk.StringVar()
        lb = tk.Listbox(
            left, listvariable=list_var, bg=self.PANEL, fg=self.FG,
            font=("Consolas", 11), selectbackground="#1e2a4a",
            selectforeground=self.GOLD, relief="flat", highlightthickness=0,
            activestyle="none"
        )
        
        sc_lb = tk.Scrollbar(left, command=lb.yview, bg=self.PANEL)
        lb.config(yscrollcommand=sc_lb.set)
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)
        sc_lb.pack(side=tk.RIGHT, fill=tk.Y, pady=4)
        
        right = tk.Frame(tab, bg="#111520")
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)
        
        txt = tk.Text(
            right, bg="#111520", fg=self.FG, font=("Consolas", 11),
            wrap=tk.WORD, relief="flat", highlightthickness=0,
            selectbackground="#2e3b5e", padx=16, pady=16
        )
        sc_txt = tk.Scrollbar(right, command=txt.yview, bg="#111520")
        txt.config(yscrollcommand=sc_txt.set)
        
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc_txt.pack(side=tk.RIGHT, fill=tk.Y)
        txt.config(state=tk.DISABLED)
        
        def _resize_table(event, cat=category):
            f = self.tabs_info.get(cat, {}).get("table_frame")
            if f and f.winfo_exists():
                w = event.width - 40
                if w > 100:
                    if hasattr(f, "dummy_frame"):
                        f.dummy_frame.config(width=w)
                    else:
                        f.config(width=w)
        txt.bind("<Configure>", _resize_table)
        
        # Tags Esthétiques
        txt.tag_config("title", foreground=self.GOLD, font=("Consolas", 16, "bold"), spacing3=8)
        txt.tag_config("subtitle", foreground="#8899aa", font=("Consolas", 10, "italic"), spacing3=15)
        txt.tag_config("section", foreground="#c77dff", font=("Arial", 12, "bold", "underline"), spacing1=15, spacing3=5)
        txt.tag_config("feature", foreground="#64b5f6", font=("Consolas", 11, "bold"), spacing1=10, spacing3=2)
        txt.tag_config("bold", foreground="#ffffff", font=("Consolas", 11, "bold"))
        txt.tag_config("quote", foreground="#a5d6a7", font=("Consolas", 10, "italic"), lmargin1=15, lmargin2=15, spacing1=5, spacing3=5)
        txt.tag_config("bullet", foreground="#ffb74d")
        
        self.tabs_info[category] = {"listbox": lb, "text": txt, "var": list_var, "sort_var": sort_var}
        lb.bind("<<ListboxSelect>>", lambda e, c=category: self._on_item_selected(e, c))

    def _schedule_search(self, *args):
        if self.after_id: self.after_cancel(self.after_id)
        self.after_id = self.after(150, self._on_search)

    def _on_search(self):
        query = self.search_var.get().strip().lower()
        for category, items in self.raw_items.items():
            filtered = [i for i in items if query in i.lower()] if query else list(items)
            
            if category == "Sorts" and self.tabs_info.get("Sorts", {}).get("sort_var"):
                sort_m = self.tabs_info["Sorts"]["sort_var"].get()
                if sort_m == "Niveau":
                    filtered.sort(key=lambda s: (spell_data.get_spell(s).get("level", 0) if spell_data.get_spell(s) else 0, s))
                elif sort_m == "École":
                    filtered.sort(key=lambda s: (spell_data.get_spell(s).get("school", "") if spell_data.get_spell(s) else "", s))
                else:
                    filtered.sort()
                    
            self.tabs_info[category]["var"].set(filtered)

    def _on_item_selected(self, event, category):
        lb = self.tabs_info[category]["listbox"]
        sel = lb.curselection()
        if not sel: return
        self._display_item(category, lb.get(sel[0]))

    def _toggle_subclass(self, class_name, subclass_short, category, item_name):
        """Toggle a subclass on/off and re-render the class display."""
        if not hasattr(self, "_active_subclasses"):
            self._active_subclasses = {}
        if class_name not in self._active_subclasses:
            self._active_subclasses[class_name] = set()
        
        if subclass_short in self._active_subclasses[class_name]:
            self._active_subclasses[class_name].discard(subclass_short)
        else:
            self._active_subclasses[class_name].add(subclass_short)
        txt = self.tabs_info[category]["text"]
        txt.update_idletasks()
        top_idx = txt.index("@0,0")
        
        self._display_item(category, item_name)
        txt.update_idletasks()
        txt.yview(top_idx)

    def _go_to_feat(self, feat_name):
        for i, cat in enumerate(["Sorts", "Dons", "Classes", "Races", "Historiques"]):
            if cat == "Dons":
                self.notebook.select(i)
                break
        self.search_var.set("")
        self._on_search()
        lb = self.tabs_info["Dons"]["listbox"]
        try:
            idx = lb.get(0, tk.END).index(feat_name)
            lb.selection_clear(0, tk.END)
            lb.selection_set(idx)
            lb.see(idx)
        except ValueError:
            pass
        self._display_item("Dons", feat_name)

    def _display_item(self, category, item_name):
        txt = self.tabs_info[category]["text"]
        txt.config(state=tk.NORMAL)
        txt.delete("1.0", tk.END)
        
        def _ins(text, tag=None):
            if not hasattr(self, "_feat_set"):
                feats = self.raw_items.get("Dons", [])
                if feats:
                    self._feat_set = {f.lower(): f for f in feats}
                    self._feat_sorted = sorted(self._feat_set.keys(), key=len, reverse=True)
                else:
                    self._feat_set = {}
                    self._feat_sorted = []
            
            if not self._feat_set or tag in ("title", "section", "subtitle"):
                txt.insert(tk.END, text, tag)
                return

            text_lower = text.lower()
            segments = []  # [(start, end, feat_key), ...]
            
            for feat_key in self._feat_sorted:
                search_start = 0
                while True:
                    idx = text_lower.find(feat_key, search_start)
                    if idx == -1:
                        break
                    end_idx = idx + len(feat_key)
                    # Check word boundaries
                    before_ok = (idx == 0 or not text[idx - 1].isalnum())
                    after_ok = (end_idx >= len(text) or not text[end_idx].isalnum())
                    if before_ok and after_ok:
                        # Check no overlap with existing segments
                        overlaps = False
                        for s, e, _ in segments:
                            if idx < e and end_idx > s:
                                overlaps = True
                                break
                        if not overlaps:
                            segments.append((idx, end_idx, feat_key))
                    search_start = idx + 1
            
            if not segments:
                txt.insert(tk.END, text, tag)
                return
            
            segments.sort(key=lambda x: x[0])
            last_end = 0
            for start, end, feat_key in segments:
                if start > last_end:
                    txt.insert(tk.END, text[last_end:start], tag)
                
                matched_str = text[start:end]
                actual_feat = self._feat_set[feat_key]
                
                link_tag = f"link_feat_{id(actual_feat)}_{start}"
                full_tags = (tag, link_tag) if tag else (link_tag,)
                txt.insert(tk.END, matched_str, full_tags)
                txt.tag_config(link_tag, foreground="#33bbff", underline=True)
                txt.tag_bind(link_tag, "<Button-1>", lambda e, f=actual_feat: self._go_to_feat(f))
                txt.tag_bind(link_tag, "<Enter>", lambda e, t=txt: t.config(cursor="hand2"))
                txt.tag_bind(link_tag, "<Leave>", lambda e, t=txt: t.config(cursor=""))
                    
                last_end = end
                
            if last_end < len(text):
                txt.insert(tk.END, text[last_end:], tag)

        try:
            if category == "Sorts":
                sp = spell_data.get_spell(item_name)
                if sp:
                    lvl_str = "Tour de magie" if sp["level"] == 0 else f"Niveau {sp['level']}"
                    conc_str = " [Concentration]" if sp["concentration"] else ""
                    rit_str = " [Rituel]" if sp["ritual"] else ""
                    
                    _ins(f"✨ {sp['name']}\n", "title")
                    _ins(f"{lvl_str} — {sp['school']}{conc_str}{rit_str} | Source: {sp['source']}\n", "subtitle")
                    _ins("Incantation:", "bold"); _ins(f" {sp['cast_time']}  |  ")
                    _ins("Portée:", "bold"); _ins(f" {sp['range']}\n")
                    _ins("Composantes:", "bold"); _ins(f" {sp['components']}  |  ")
                    _ins("Durée:", "bold"); _ins(f" {sp['duration']}\n\n")
                    
                    for line in sp["description"].split('\n'):
                        if line.strip().startswith("▸"):
                            _ins(line + "\n", "feature")
                        else:
                            _ins(line + "\n")

            elif category == "Dons":
                ft = feat_data.get_feat(item_name)
                if ft:
                    _ins(f"📖 {ft['name']}\n", "title")
                    subts = f"Source: {ft['source']}"
                    if ft.get("prerequisite"):
                        subts += f" | Prérequis: {ft['prerequisite']}"
                    _ins(subts + "\n", "subtitle")
                    
                    for line in ft["description"].split('\n'):
                        if line.strip().startswith("•") or line.strip().startswith("▸"):
                            _ins(line + "\n", "bold")
                        else:
                            _ins(line + "\n")

            elif category == "Classes":
                c_name = item_name.lower()
                _ins(f"🛡 {item_name.upper()}\n", "title")
                
                try:
                    hd = class_data.get_hit_die(c_name)
                    _ins(f"Dé de vie: d{hd}\n\n", "feature")
                except Exception: pass

                # ── Maîtrises ──
                try:
                    profs = class_data.get_proficiencies(c_name)
                    _ins("Maîtrises de base\n", "section")
                    _ins("Armures: ", "bold"); _ins(f"{', '.join(profs.get('armor', [])) or 'Aucune'}\n")
                    _ins("Armes: ", "bold"); _ins(f"{', '.join(profs.get('weapons', [])) or 'Aucune'}\n")
                    _ins("Jet de Sauv.: ", "bold"); _ins(f"{', '.join(profs.get('saves', [])).upper()}\n")
                    
                    choices = class_data.get_class_proficiency_choices(c_name)
                    if choices:
                        _ins("Choix:\n", "bold")
                        for ch in choices:
                            _ins(f"  • {ch['label']} parmi {', '.join(ch['options'][:7])}{'...' if len(ch['options'])>7 else ''}\n", "bullet")
                    _ins("\n")
                except Exception: pass

                # ── Equipement ──
                try:
                    eq = class_data.get_starting_equipment(c_name)
                    if eq:
                        _ins("Equipement de départ\n", "section")
                        for line in eq:
                            _ins(f"• {line}\n")
                        _ins("\n")
                except Exception: pass

                # ── Sorts & Multiclassage ──
                try:
                    mc = class_data.get_multiclassing_info(c_name)
                    prog = class_data.get_caster_progression(c_name)
                    if mc or prog:
                        _ins("Information de Classe\n", "section")
                        if prog:
                            _ins("Progression Spellcaster: ", "bold"); _ins(f"{prog}\n")
                        if mc:
                            reqs = mc.get("requirements", {})
                            if reqs:
                                req_str = ", ".join(f"{k.upper()} {v}" for k,v in reqs.items())
                                _ins("Prérequis Multiclassage: ", "bold"); _ins(f"{req_str}\n")
                            pg = mc.get("proficienciesGained", {})
                            if pg:
                                _ins("Gains Multiclassage: ", "bold")
                                parts = []
                                for k, v in pg.items():
                                    if isinstance(v, list) and v:
                                        parts.append(f"{k} [{', '.join(v)}]")
                                _ins(f"{' / '.join(parts)}\n")
                        _ins("\n")
                except Exception: pass

                # ── Table de Progression ──
                try:
                    tb_groups = class_data.get_class_table_groups(c_name)
                    col_labels = []
                    col_vals = {lvl: [] for lvl in range(1, 21)}
                    
                    for g in tb_groups:
                        labels = g.get("colLabels", [])
                        col_labels.extend([class_data._clean_5etools_text(L) for L in labels])
                        rows = g.get("rows", []) or g.get("rowsSpellProgression", [])
                        for i, r in enumerate(rows):
                            if i < 20: col_vals[i+1].extend(r)
                            
                    feats_by_lvl = {lvl: [] for lvl in range(1, 21)}
                    all_feats = class_data.get_all_feature_details(c_name, subclass_short="", level=20)
                    for f in all_feats:
                        l = f.get("level", 0)
                        if 1 <= l <= 20: feats_by_lvl[l].append(f.get("name", ""))
                        
                    headers = ["Niv", "PB", "Capacités"] + [c.capitalize() for c in col_labels]
                    
                    _ins("\nTable de Progression\n", "section")
                    
                    txt_w = txt.winfo_width()
                    if txt_w < 100: txt_w = 700
                    frame_w = txt_w - 40
                    
                    table_container = tk.Frame(txt, bg="#080a10")
                    self.tabs_info[category]["table_frame"] = table_container
                    
                    dummy = tk.Frame(table_container, width=frame_w, height=0, bg="#080a10")
                    dummy.grid(row=22, column=0, columnspan=len(headers))
                    table_container.dummy_frame = dummy
                    
                    cap_labels = []
                    cap_header_lbl = None
                    
                    for c_idx, h in enumerate(headers):
                        lbl = tk.Label(table_container, text=h, bg="#0b0d12", fg=self.GOLD, font=("Consolas", 10, "bold"), padx=4, pady=4)
                        lbl.grid(row=0, column=c_idx, sticky="nsew", padx=1, pady=1)
                        if h == "Capacités":
                            cap_header_lbl = lbl
                            table_container.grid_columnconfigure(c_idx, weight=1)
                        else:
                            table_container.grid_columnconfigure(c_idx, weight=0)

                    for r_idx, lvl in enumerate(range(1, 21), 1):
                        pb = f"+{(lvl - 1) // 4 + 2}"
                        fs = ", ".join(feats_by_lvl[lvl]) if feats_by_lvl[lvl] else "—"
                        custom = [str(x).replace("{@dice ", "").replace("}", "") for x in col_vals[lvl]]
                        while len(custom) < len(col_labels): custom.append("—")
                        
                        row_data = [str(lvl), pb, fs] + custom
                        bg_c = "#111520" if r_idx % 2 == 0 else "#1a1f2e"
                        
                        for c_idx, val in enumerate(row_data):
                            is_cap = (c_idx == 2)
                            lbl = tk.Label(table_container, text=val, bg=bg_c, fg=self.FG, font=("Consolas", 10), anchor="w" if is_cap else "center", justify="left")
                            lbl.grid(row=r_idx, column=c_idx, sticky="nsew", padx=1, pady=1)
                            if is_cap:
                                cap_labels.append(lbl)

                    if cap_header_lbl:
                        cap_header_lbl.bind("<Configure>", lambda e, labels=cap_labels: [l.config(wraplength=max(50, e.width - 12)) for l in labels])
                    
                    # Forward mousewheel from all grid children to parent text
                    def _fwd_scroll(event, _txt=txt):
                        if event.num == 4: _txt.yview_scroll(-3, "units")
                        elif event.num == 5: _txt.yview_scroll(3, "units")
                        else: _txt.yview_scroll(int(-1*(event.delta/120)), "units")
                        return "break"
                    for child in table_container.winfo_children():
                        child.bind("<MouseWheel>", _fwd_scroll)
                        child.bind("<Button-4>", _fwd_scroll)
                        child.bind("<Button-5>", _fwd_scroll)
                    table_container.bind("<MouseWheel>", _fwd_scroll)
                    table_container.bind("<Button-4>", _fwd_scroll)
                    table_container.bind("<Button-5>", _fwd_scroll)
                        
                    txt.window_create(tk.END, window=table_container)
                    _ins("\n\n")
                except Exception as e:
                    _ins(f"(Erreur lors de la table de progression: {e})\n\n", "subtitle")

                # ── Subclass Toggle Bar ──
                try:
                    subclasses = class_data.get_available_subclasses_short(c_name)
                    if subclasses:
                        _ins("\nSous-classes\n", "section")
                        
                        if not hasattr(self, "_active_subclasses"):
                            self._active_subclasses = {}
                        if c_name not in self._active_subclasses:
                            self._active_subclasses[c_name] = set()
                        
                        btn_frame = tk.Frame(txt, bg="#080a10")
                        for short, full in subclasses:
                            is_on = short in self._active_subclasses[c_name]
                            bg = "#2a3040" if is_on else "#111520"
                            fg = self.GOLD if is_on else self.FG_DIM
                            btn = tk.Label(btn_frame, text=f" {short} ", bg=bg, fg=fg,
                                           font=("Consolas", 10, "bold"), padx=8, pady=4, cursor="hand2",
                                           relief="raised" if is_on else "flat", bd=1)
                            btn.pack(side=tk.LEFT, padx=2, pady=2)
                            btn.bind("<Button-1>", lambda e, s=short, cn=c_name, iname=item_name, cat=category: self._toggle_subclass(cn, s, cat, iname))
                        
                        txt.window_create(tk.END, window=btn_frame)
                        _ins("\n\n")
                except Exception: pass

                # ── Traits (Class + active subclasses) ──
                active_subs = self._active_subclasses.get(c_name, set()) if hasattr(self, "_active_subclasses") else set()
                
                _ins("Traits de Classe (Niv 1 - 20)\n", "section")
                feats = class_data.get_all_feature_details(c_name, subclass_short="", level=20)
                
                all_combined_feats = list(feats)
                for sub_short in sorted(active_subs):
                    sub_feats = class_data.get_all_feature_details(c_name, subclass_short=sub_short, level=20)
                    for f in sub_feats:
                        if f.get("type") == "subclass":
                            f_copy = dict(f)
                            f_copy["name"] = f"{f_copy.get('name', '')} [{sub_short}]"
                            all_combined_feats.append(f_copy)
                            
                all_combined_feats.sort(key=lambda x: (x.get('level', 0), x.get('type', 'class'), x.get('name', '')))
                
                for f in all_combined_feats:
                    is_sub = (f.get("type") == "subclass")
                    prefix = "🗡" if is_sub else "▸"
                    _ins(f"{prefix} [Niveau {f.get('level', '?')}] {f.get('name', '')}\n", "feature")
                    for line in f.get('text', '').split('\n'):
                        if line.strip().startswith("•"):
                            _ins(line + "\n", "bullet")
                        else:
                            _ins(line + "\n")
                    _ins("\n")

            elif category == "Races":
                r_name, r_src = race_data.split_race_source(item_name)
                _ins(f"🧬 {r_name.upper()}\n", "title")
                _ins(f"Source: {r_src}\n", "subtitle")

                fluff = race_data.get_race_fluff(r_name, source=r_src)
                if fluff:
                    _ins(f"{fluff}\n\n", "quote")
                
                # ── Stats bloc ──
                _ins("Caractéristiques\n", "section")
                try:
                    speed = race_data.get_race_speed(r_name)
                    spd_parts = []
                    for k, v in speed.items():
                        if k == "walk": spd_parts.append(f"{v} pi.")
                        else: spd_parts.append(f"{k.title()} {v} pi.")
                    _ins("Vitesse: ", "bold"); _ins(f"{', '.join(spd_parts)}\n")
                except Exception: pass

                try:
                    sizes = race_data.get_race_size(r_name)
                    _ins("Taille: ", "bold"); _ins(f"{', '.join(sizes)}\n")
                except Exception: pass

                try:
                    dv = race_data.get_race_darkvision(r_name)
                    if dv:
                        _ins("Vision dans le noir: ", "bold"); _ins(f"{dv} pi.\n")
                except Exception: pass

                try:
                    bonuses = race_data.format_ability_bonuses(r_name, source=r_src)
                    _ins("Bonus: ", "bold"); _ins(f"{', '.join(bonuses)}\n")
                except Exception: pass

                try:
                    langs = race_data.get_race_languages(r_name, source=r_src)
                    _ins("Langues: ", "bold"); _ins(f"{', '.join(langs)}\n")
                except Exception: pass

                try:
                    res = race_data.get_race_resistance(r_name, source=r_src)
                    if res:
                        _ins("Résistances: ", "bold"); _ins(f"{', '.join(res)}\n")
                    imm = race_data.get_race_immunity(r_name, source=r_src)
                    if imm:
                        _ins("Immunités: ", "bold"); _ins(f"{', '.join(imm)}\n")
                except Exception: pass

                _ins("\n")

                # ── Full trait descriptions ──
                try:
                    traits = race_data.get_race_traits(r_name, source=r_src)
                    if traits:
                        _ins("Traits Raciaux\n", "section")
                        for t in traits:
                            _ins(f"▸ {t['name']}\n", "feature")
                            for line in t.get("text", "").split('\n'):
                                if line.strip().startswith("•"):
                                    _ins(f"  {line}\n", "bullet")
                                elif line.strip().startswith("▸"):
                                    _ins(f"{line}\n", "bold")
                                else:
                                    _ins(f"{line}\n")
                            _ins("\n")
                except Exception: pass

            elif category == "Historiques":
                bge = background_data.get_background_entry(item_name)
                if bge:
                    _ins(f"📜 {item_name.upper()}\n", "title")
                    
                    _ins("Maîtrises\n", "section")
                    pf = background_data.get_background_fixed_proficiencies(item_name)
                    _ins("Compétences: ", "bold"); _ins(f"{', '.join(pf['skills']) or 'Aucune'}\n")
                    _ins("Outils: ", "bold"); _ins(f"{', '.join(pf['tools']) or 'Aucune'}\n")
                    _ins("Langues: ", "bold"); _ins(f"{', '.join(pf['languages']) or 'Aucune'}\n\n")
                    
                    feats = background_data.get_background_features(item_name)
                    if feats:
                        _ins("Capacités & Traits\n", "section")
                        for f in feats:
                            _ins(f"♦ {f['name']}\n", "feature")
                            _ins(f"{f.get('text', '')}\n\n")
                            
        except Exception as e:
            _ins(f"Erreur de rendu pour {item_name}: {e}", "subtitle")
            import traceback; traceback.print_exc()
            
        txt.config(state=tk.DISABLED)

def open_compendium(parent):
    CompendiumWindow(parent)
