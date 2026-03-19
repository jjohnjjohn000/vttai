"""
inventory_panel.py — Inventaire du groupe de héros.

Fenêtre flottante non-modale permettant de :
  - Consulter / modifier la monnaie (pp / po / pe / pa / pc)
  - Ajouter, éditer, supprimer des objets
  - Filtrer par catégorie et par rareté
  - Chercher par nom

Utilise uniquement get_inventory() / save_inventory() de state_manager.
"""

import tkinter as tk
from tkinter import messagebox, simpledialog

from state_manager import get_inventory, save_inventory


# ─── Constantes visuelles ─────────────────────────────────────────────────────

BG        = "#0d1117"
BG2       = "#161b22"
BG3       = "#21262d"
BORDER    = "#30363d"
FG        = "#e6edf3"
FG_DIM    = "#8b949e"
ACCENT    = "#f0c040"   # or — thématique D&D

RARITY_COLORS = {
    "commun":      "#aaaaaa",
    "peu_commun":  "#2dc653",
    "rare":        "#4da6ff",
    "très_rare":   "#c678dd",
    "légendaire":  "#e5a50a",
    "artéfact":    "#ff6e6e",
}

CATEGORY_ICONS = {
    "arme":          "⚔",
    "armure":        "[Def]",
    "potion":        "[Pot]",
    "objet_magique": "[Mag]",
    "munition":      "[Mun]",
    "outil":         "[Out]",
    "divers":        "[?]",
}

CATEGORIES = ["tous", "arme", "armure", "potion", "objet_magique", "munition", "outil", "divers"]
RARITIES   = ["commun", "peu_commun", "rare", "très_rare", "légendaire", "artéfact"]
COIN_KEYS  = ["platinum", "gold", "electrum", "silver", "copper"]
COIN_LABELS = {"platinum": "Platine (pp)", "gold": "Or (po)", "electrum": "Électrum (pe)",
               "silver": "Argent (pa)", "copper": "Cuivre (pc)"}
COIN_COLORS = {"platinum": "#e0e8ff", "gold": "#f0c040", "electrum": "#a0d8a0",
               "silver": "#c0c8d0", "copper": "#c87941"}


def _btn(parent, text, cmd, bg="#21262d", fg=FG, **kw):
    return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                     font=("Arial", 9, "bold"), relief="flat",
                     activebackground="#444", activeforeground=FG, cursor="hand2", **kw)


class InventoryPanel:
    """Fenêtre d'inventaire du groupe, non-modale."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self._selected_item_id = None

        self.win = tk.Toplevel(root)
        self.win.title("Inventaire du Groupe")
        self.win.geometry("900x640")
        self.win.configure(bg=BG)
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)

        self._build_ui()
        self._refresh()

    # ─── Construction UI ──────────────────────────────────────────────────────

    def _build_ui(self):
        # ── En-tête ───────────────────────────────────────────────────────────
        header = tk.Frame(self.win, bg=BG2)
        header.pack(fill=tk.X)

        tk.Label(header, text="[Sac]  Inventaire du Groupe", bg=BG2, fg=ACCENT,
                 font=("Arial", 14, "bold")).pack(side=tk.LEFT, padx=16, pady=10)

        _btn(header, "+ Ajouter", self._add_item, bg="#1a3a1a", fg="#81c784"
             ).pack(side=tk.RIGHT, padx=8, pady=8)

        # ── Monnaie ───────────────────────────────────────────────────────────
        self._cur_frame = tk.Frame(self.win, bg=BG3)
        self._cur_frame.pack(fill=tk.X, padx=12, pady=(8, 0))
        self._coin_vars = {}
        self._build_currency()

        # ── Filtres ───────────────────────────────────────────────────────────
        filter_frame = tk.Frame(self.win, bg=BG)
        filter_frame.pack(fill=tk.X, padx=12, pady=(8, 0))

        self._filter_cat = tk.StringVar(value="tous")
        for cat in CATEGORIES:
            icon = CATEGORY_ICONS.get(cat, "")
            label = (icon + " " + cat.replace("_", " ").capitalize()) if cat != "tous" else "Tous"
            tk.Button(filter_frame, text=label, bg=BG2, fg=FG_DIM,
                      font=("Arial", 9), relief="flat", padx=6,
                      command=lambda c=cat: self._set_filter(c)
                      ).pack(side=tk.LEFT, padx=2, pady=4)

        # Recherche
        tk.Label(filter_frame, text="  Recherche:", bg=BG, fg=FG_DIM,
                 font=("Arial", 9)).pack(side=tk.LEFT, padx=(12, 4))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._refresh_list())
        tk.Entry(filter_frame, textvariable=self._search_var, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", width=18,
                 font=("Arial", 10)).pack(side=tk.LEFT)

        # ── Séparateur ────────────────────────────────────────────────────────
        tk.Frame(self.win, bg=BORDER, height=1).pack(fill=tk.X, padx=12, pady=6)

        # ── Zone principale : liste + détail ──────────────────────────────────
        pane = tk.Frame(self.win, bg=BG)
        pane.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        # Colonne liste
        list_col = tk.Frame(pane, bg=BG, width=340)
        list_col.pack(side=tk.LEFT, fill=tk.Y)
        list_col.pack_propagate(False)

        self._list_canvas = tk.Canvas(list_col, bg=BG, highlightthickness=0, width=330)
        list_scroll = tk.Scrollbar(list_col, orient="vertical",
                                   command=self._list_canvas.yview)
        self._list_inner = tk.Frame(self._list_canvas, bg=BG)
        self._list_inner.bind(
            "<Configure>",
            lambda e: self._list_canvas.configure(scrollregion=self._list_canvas.bbox("all"))
        )
        self._list_canvas.create_window((0, 0), window=self._list_inner, anchor="nw")
        self._list_canvas.configure(yscrollcommand=list_scroll.set)
        self._list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Bind mousewheel
        def _scroll(event):
            self._list_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self._list_canvas.bind("<MouseWheel>", _scroll)
        self._list_inner.bind("<MouseWheel>", _scroll)

        # Séparateur vertical
        tk.Frame(pane, bg=BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        # Colonne détail
        detail_col = tk.Frame(pane, bg=BG)
        detail_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._build_detail_panel(detail_col)

        # Stats en bas
        self._stats_var = tk.StringVar()
        tk.Label(self.win, textvariable=self._stats_var, bg=BG, fg=FG_DIM,
                 font=("Arial", 9), anchor="w").pack(fill=tk.X, padx=16, pady=(0, 4))

    def _build_currency(self):
        for w in self._cur_frame.winfo_children():
            w.destroy()

        tk.Label(self._cur_frame, text="Monnaie :", bg=BG3, fg=FG_DIM,
                 font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=(10, 6), pady=6)

        inv = get_inventory()
        cur = inv.get("currency", {})

        self._coin_vars = {}
        for coin in COIN_KEYS:
            val = cur.get(coin, 0)
            color = COIN_COLORS.get(coin, FG)
            short = {"platinum": "PP", "gold": "PO", "electrum": "PE",
                     "silver": "PA", "copper": "PC"}[coin]
            f = tk.Frame(self._cur_frame, bg=BG3)
            f.pack(side=tk.LEFT, padx=6)
            tk.Label(f, text=short, bg=BG3, fg=color,
                     font=("Arial", 9, "bold")).pack(side=tk.TOP)
            v = tk.IntVar(value=val)
            self._coin_vars[coin] = v
            sp = tk.Spinbox(f, textvariable=v, from_=0, to=999999,
                            width=6, bg=BG2, fg=color, relief="flat",
                            buttonbackground=BG3, insertbackground=color,
                            font=("Arial", 10, "bold"))
            sp.pack(side=tk.TOP)
            sp.bind("<FocusOut>", lambda e: self._save_currency())
            sp.bind("<Return>",   lambda e: self._save_currency())

        _btn(self._cur_frame, "Sauvegarder", self._save_currency,
             bg="#1a3a5c", fg="#64b5f6").pack(side=tk.RIGHT, padx=10, pady=6)

    def _build_detail_panel(self, parent):
        """Panneau de détail d'un objet sélectionné."""
        self._detail_widgets = {}

        tk.Label(parent, text="Détail", bg=BG, fg=FG_DIM,
                 font=("Arial", 11, "bold")).pack(anchor="w", padx=6, pady=(6, 2))
        tk.Frame(parent, bg=BORDER, height=1).pack(fill=tk.X, padx=6, pady=(0, 8))

        def _row(label, key, widget_type="entry", options=None, span=False):
            row = tk.Frame(parent, bg=BG)
            row.pack(fill=tk.X, padx=8, pady=3)
            tk.Label(row, text=label, bg=BG, fg=FG_DIM, font=("Arial", 9),
                     width=14, anchor="w").pack(side=tk.LEFT)
            if widget_type == "entry":
                v = tk.StringVar()
                w = tk.Entry(row, textvariable=v, bg=BG3, fg=FG,
                             insertbackground=FG, relief="flat",
                             font=("Arial", 10))
                w.pack(side=tk.LEFT, fill=tk.X, expand=True)
                self._detail_widgets[key] = ("entry", v, w)
            elif widget_type == "spinbox":
                v = tk.IntVar()
                w = tk.Spinbox(row, textvariable=v, from_=1, to=9999,
                               bg=BG3, fg=FG, relief="flat", width=8,
                               buttonbackground=BG3, insertbackground=FG,
                               font=("Arial", 10))
                w.pack(side=tk.LEFT)
                self._detail_widgets[key] = ("spinbox", v, w)
            elif widget_type == "combo":
                v = tk.StringVar()
                om = tk.OptionMenu(row, v, *options)
                om.config(bg=BG3, fg=FG, relief="flat", font=("Arial", 9),
                          activebackground=BG3, highlightthickness=0)
                om["menu"].config(bg=BG3, fg=FG, font=("Arial", 9))
                om.pack(side=tk.LEFT)
                self._detail_widgets[key] = ("combo", v, om)
            elif widget_type == "check":
                v = tk.BooleanVar()
                w = tk.Checkbutton(row, variable=v, bg=BG, fg=FG,
                                   selectcolor=BG3, activebackground=BG,
                                   font=("Arial", 9))
                w.pack(side=tk.LEFT)
                self._detail_widgets[key] = ("check", v, w)
            elif widget_type == "text":
                v = None
                w = tk.Text(row, bg=BG3, fg=FG, insertbackground=FG,
                            relief="flat", font=("Arial", 9), height=3, wrap=tk.WORD)
                w.pack(side=tk.LEFT, fill=tk.X, expand=True)
                self._detail_widgets[key] = ("text", v, w)
            return v

        _row("Nom",            "name")
        _row("Quantité",       "quantity",       "spinbox")
        _row("Catégorie",      "category",       "combo",   CATEGORIES[1:])
        _row("Rareté",         "rarity",         "combo",   RARITIES)
        _row("Poids (lbs)",    "weight",         "entry")
        _row("Harmonisé",      "attuned",        "check")
        _row("Harmonisé par",  "attunement_by")
        _row("Description",    "description",    "text")
        _row("Notes",          "notes",          "text")

        # Boutons d'action
        btn_row = tk.Frame(parent, bg=BG)
        btn_row.pack(fill=tk.X, padx=8, pady=(10, 0))
        self._btn_save   = _btn(btn_row, "Enregistrer", self._save_item,
                                bg="#1a3a1a", fg="#81c784")
        self._btn_save.pack(side=tk.LEFT, padx=4)
        self._btn_delete = _btn(btn_row, "Supprimer", self._delete_item,
                                bg="#3a1010", fg="#e57373")
        self._btn_delete.pack(side=tk.LEFT, padx=4)

        self._detail_state("disabled")

    def _detail_state(self, state):
        for key, (wtype, v, w) in self._detail_widgets.items():
            try:
                w.config(state=state)
            except Exception:
                pass
        try:
            self._btn_save.config(state=state)
            self._btn_delete.config(state=state)
        except Exception:
            pass

    # ─── Données → UI ────────────────────────────────────────────────────────

    def _refresh(self):
        self._build_currency()
        self._refresh_list()

    def _refresh_list(self):
        for w in self._list_inner.winfo_children():
            w.destroy()

        inv     = get_inventory()
        items   = inv.get("items", [])
        cat_f   = self._filter_cat.get() if hasattr(self, "_filter_cat") else "tous"
        search  = self._search_var.get().lower().strip() if hasattr(self, "_search_var") else ""

        filtered = [
            it for it in items
            if (cat_f == "tous" or it.get("category") == cat_f)
            and (not search or search in it.get("name", "").lower()
                 or search in it.get("description", "").lower())
        ]

        # Tri : catégorie → rareté (décroissant) → nom
        def _sort_key(it):
            rar_idx = RARITIES.index(it.get("rarity", "commun")) if it.get("rarity") in RARITIES else 0
            return (it.get("category", ""), -rar_idx, it.get("name", "").lower())

        filtered.sort(key=_sort_key)

        if not filtered:
            tk.Label(self._list_inner, text="Aucun objet",
                     bg=BG, fg=FG_DIM, font=("Arial", 10, "italic")).pack(pady=20)
        else:
            for item in filtered:
                self._build_item_row(item)

        # Statistiques
        total_items = sum(it.get("quantity", 1) for it in items)
        total_weight = sum(it.get("quantity", 1) * float(it.get("weight") or 0) for it in items)
        self._stats_var.set(
            f"{len(items)} type(s) d'objets  |  {total_items} total  |  "
            f"Poids : {total_weight:.1f} lbs  |  "
            f"Affiché : {len(filtered)}"
        )

    def _build_item_row(self, item):
        item_id = item.get("id", "")
        name    = item.get("name", "?")
        qty     = item.get("quantity", 1)
        cat     = item.get("category", "divers")
        rar     = item.get("rarity", "commun")
        attuned = item.get("attuned", False)

        rar_color = RARITY_COLORS.get(rar, FG_DIM)
        icon      = CATEGORY_ICONS.get(cat, "[?]")
        is_sel    = (item_id == self._selected_item_id)
        bg_row    = "#1c2a1c" if is_sel else BG

        row = tk.Frame(self._list_inner, bg=bg_row, cursor="hand2")
        row.pack(fill=tk.X, padx=2, pady=1)

        tk.Label(row, text=icon, bg=bg_row, fg=rar_color,
                 font=("Arial", 10), width=3).pack(side=tk.LEFT, padx=(4, 0))
        tk.Label(row, text=f"{name}" + (" [att.]" if attuned else ""),
                 bg=bg_row, fg=rar_color, font=("Arial", 10, "bold"),
                 anchor="w").pack(side=tk.LEFT, padx=4, pady=5)
        tk.Label(row, text=f"×{qty}", bg=bg_row, fg=FG_DIM,
                 font=("Arial", 9)).pack(side=tk.RIGHT, padx=8)

        def _select(e=None, iid=item_id, it=item):
            self._selected_item_id = iid
            self._load_item_to_detail(it)
            self._refresh_list()

        row.bind("<Button-1>", _select)
        for child in row.winfo_children():
            child.bind("<Button-1>", _select)

    def _load_item_to_detail(self, item):
        self._detail_state("normal")
        for key, (wtype, v, w) in self._detail_widgets.items():
            val = item.get(key, "")
            if wtype == "entry":
                v.set(str(val) if val is not None else "")
            elif wtype == "spinbox":
                try: v.set(int(val))
                except: v.set(1)
            elif wtype == "combo":
                v.set(str(val) if val else "")
            elif wtype == "check":
                v.set(bool(val))
            elif wtype == "text":
                w.config(state="normal")
                w.delete("1.0", tk.END)
                w.insert("1.0", str(val) if val else "")

    # ─── Actions ─────────────────────────────────────────────────────────────

    def _set_filter(self, cat):
        self._filter_cat.set(cat)
        self._refresh_list()

    def _save_currency(self):
        inv = get_inventory()
        for coin, v in self._coin_vars.items():
            try:
                inv["currency"][coin] = max(0, int(v.get()))
            except Exception:
                pass
        save_inventory(inv)

    def _add_item(self):
        name = simpledialog.askstring("Nouvel objet", "Nom de l'objet :",
                                       parent=self.win)
        if not name or not name.strip():
            return
        inv = get_inventory()
        new_item = {
            "id":            __import__("uuid").uuid4().hex[:8],
            "name":          name.strip(),
            "quantity":      1,
            "category":      "divers",
            "rarity":        "commun",
            "weight":        0.0,
            "description":   "",
            "attuned":       False,
            "attunement_by": "",
            "notes":         "",
        }
        inv["items"].append(new_item)
        save_inventory(inv)
        self._selected_item_id = new_item["id"]
        self._refresh()
        self._load_item_to_detail(new_item)

    def _save_item(self):
        if not self._selected_item_id:
            return
        inv = get_inventory()
        for item in inv["items"]:
            if item.get("id") == self._selected_item_id:
                for key, (wtype, v, w) in self._detail_widgets.items():
                    if wtype == "entry":
                        raw = v.get().strip()
                        if key == "weight":
                            try: item[key] = float(raw)
                            except: item[key] = 0.0
                        else:
                            item[key] = raw
                    elif wtype == "spinbox":
                        try: item[key] = max(1, int(v.get()))
                        except: item[key] = 1
                    elif wtype == "combo":
                        item[key] = v.get()
                    elif wtype == "check":
                        item[key] = v.get()
                    elif wtype == "text":
                        item[key] = w.get("1.0", tk.END).strip()
                break
        save_inventory(inv)
        self._refresh_list()

    def _delete_item(self):
        if not self._selected_item_id:
            return
        inv   = get_inventory()
        item  = next((it for it in inv["items"] if it.get("id") == self._selected_item_id), None)
        if not item:
            return
        if not messagebox.askyesno("Supprimer",
                                   f"Supprimer « {item.get('name', '?')} » de l'inventaire ?",
                                   parent=self.win):
            return
        inv["items"] = [it for it in inv["items"] if it.get("id") != self._selected_item_id]
        save_inventory(inv)
        self._selected_item_id = None
        self._detail_state("disabled")
        self._refresh()
