"""
combat_simulator.py
───────────────────
Simulateur de combat rapide (sans RP) pour le Moteur de l'Aube Brisée.
Ouvrir depuis main.py avec : CombatSimulator(root, load_state, msg_queue)

Fonctionnalités :
  • Import automatique des PJ depuis campaign_state.json
  • Ajout de groupes d'ennemis à la volée (nom, PV, CA, bonus attaque, dés dégâts)
  • Simulation complète en 1 clic (ou pas à pas)
  • Log tour par tour exportable
  • Tableau de statistiques : Dégâts infligés/reçus, coups portés, kills, rounds survécus
  • Résultat injecté dans le chat principal (optionnel)
"""

import tkinter as tk
from tkinter import scrolledtext
import random
import re
import threading

# ─── Intégration bestiary (optionnelle) ───────────────────────────────────────
try:
    from npc_bestiary_panel import (
        search_monsters  as _bestiary_search,
        get_monster      as _bestiary_get,
        _load_bestiary   as _bestiary_load,
        _LEGENDARY_DATA  as _bestiary_legendary,
    )
    _BESTIARY_OK = True
except ImportError:
    _BESTIARY_OK = False
    _bestiary_legendary = {}


def _bestiary_get_legendary_group(monster_name: str, source: str = "") -> dict | None:
    """
    Retourne le groupe légendaire (lair actions, regional effects) pour un monstre,
    en cherchant par nom du monstre dans _LEGENDARY_DATA.
    """
    if not _BESTIARY_OK or not _bestiary_legendary:
        return None
    key = monster_name.lower()
    # Cherche correspondance directe
    if key in _bestiary_legendary:
        return _bestiary_legendary[key]
    # Cherche partielle (ex. "Night Hag (Coven)" → "Night Hag")
    base = re.sub(r'\s*\([^)]*\)\s*$', '', monster_name).strip().lower()
    if base in _bestiary_legendary:
        return _bestiary_legendary[base]
    return None


def _sim_stats_from_monster(name: str) -> dict | None:
    """
    Extrait depuis la fiche bestiary les stats utiles pour la simulation :
    hp, ac, atk_bonus, dmg_expr, n_attacks, cr.
    Retourne None si introuvable.
    """
    if not _BESTIARY_OK:
        return None
    _bestiary_load()
    m = _bestiary_get(name)
    if not m:
        return None

    # CA
    ac_raw = m.get("ac", [])
    if ac_raw:
        first = ac_raw[0]
        ac = first if isinstance(first, int) else (first.get("ac", 10) if isinstance(first, dict) else 10)
    else:
        ac = 10

    # PV
    hp_raw = m.get("hp", {})
    hp = hp_raw.get("average", 10) if isinstance(hp_raw, dict) else int(hp_raw or 10)

    # Meilleure attaque
    best_atk = None
    best_avg = -1
    for a in m.get("action", []):
        if "multiattack" in a.get("name", "").lower():
            continue
        for entry in a.get("entries", []):
            if not isinstance(entry, str):
                continue
            hit_m = re.search(r'\{@hit\s+(-?\d+)\}', entry)
            dmg_m = re.search(r'\{@damage\s+([^}]+)\}', entry)
            if hit_m and dmg_m:
                hit  = int(hit_m.group(1))
                expr = dmg_m.group(1).strip()
                avg  = 0.0
                for d in re.finditer(r'(\d*)d(\d+)', expr):
                    n = int(d.group(1)) if d.group(1) else 1
                    avg += n * (int(d.group(2)) + 1) / 2
                bm = re.search(r'([+-]\d+)$', expr)
                if bm:
                    avg += int(bm.group(1))
                if avg > best_avg:
                    best_avg = avg
                    best_atk = {"atk": hit, "dmg": expr}

    # Nombre d'attaques (Multiattack)
    n_attacks = 1
    for a in m.get("action", []):
        if "multiattack" in a.get("name", "").lower():
            text = " ".join(str(e) for e in a.get("entries", []) if isinstance(e, str)).lower()
            mo = re.search(
                r'makes\s+(one|two|three|four|five|\d+)\s+(?:melee|ranged|attack)', text
            )
            if mo:
                nums = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
                val = mo.group(1)
                n_attacks = nums.get(val, int(val) if val.isdigit() else 1)
            break

    # CR
    cr_raw = m.get("cr", "?")
    cr = cr_raw.get("cr", "?") if isinstance(cr_raw, dict) else str(cr_raw)

    return {
        "hp":        hp,
        "ac":        ac,
        "atk_bonus": best_atk["atk"] if best_atk else 4,
        "dmg_expr":  best_atk["dmg"] if best_atk else "1d6",
        "n_attacks": n_attacks,
        "cr":        cr,
    }

# ─── Palette (cohérente avec combat_tracker.py) ───────────────────────────────
C = {
    "bg":         "#0b0d12",
    "panel":      "#111520",
    "border":     "#2a3040",
    "gold":       "#c8a820",
    "red":        "#c0392b",
    "red_bright": "#e74c3c",
    "green":      "#27ae60",
    "green_b":    "#2ecc71",
    "blue":       "#2980b9",
    "blue_b":     "#3498db",
    "purple":     "#7c3aad",
    "fg":         "#dde0e8",
    "fg_dim":     "#8899aa",
    "fg_gold":    "#f0d060",
    "entry":      "#1a1f2e",
    "pc":         "#0d1a2a",
    "enemy":      "#1a100d",
}

# Stats de base niveau 15 pour les PJ (bonus att, dés dégâts, nb attaques)
PC_DEFAULTS = {
    "Kaelen": {"atk": 11, "dmg": "2d6+8",  "n_attacks": 3, "side": "Héros", "color": "#a0c4ff", "ac": 20, "max_hp": 140},
    "Elara":  {"atk": 11, "dmg": "8d6",    "n_attacks": 1, "side": "Héros", "color": "#c8b8ff", "ac": 15, "max_hp": 95},
    "Thorne": {"atk": 11, "dmg": "8d6+5",  "n_attacks": 2, "side": "Héros", "color": "#ff9999", "ac": 18, "max_hp": 105},
    "Lyra":   {"atk": 10, "dmg": "4d8+5",  "n_attacks": 2, "side": "Héros", "color": "#a8f0a8", "ac": 16, "max_hp": 109},
}

NPC_COLORS = ["#ff9966","#ffcc66","#99ddff","#cc99ff","#99ffcc","#ff99bb","#ddbbff","#aaffaa"]


def _parse_dice(expr: str) -> int:
    """Évalue une expression de dés type '2d6+8', '8d6', 'd20+3'. Retourne le total."""
    expr = expr.strip().lower().replace(" ", "")
    total = 0
    bonus = 0

    # Extraire le bonus/malus flat
    m_bonus = re.search(r'([+-]\d+)$', expr)
    if m_bonus:
        bonus = int(m_bonus.group(1))
        expr = expr[:m_bonus.start()]

    # Trouver tous les groupes XdY
    for m in re.finditer(r'(\d*)d(\d+)', expr):
        n = int(m.group(1)) if m.group(1) else 1
        sides = int(m.group(2))
        total += sum(random.randint(1, sides) for _ in range(n))

    return total + bonus


def _roll_dice_only(expr: str) -> int:
    """Roule uniquement les dés d'une expression (sans le bonus flat).
    Utilisé pour les coups critiques : les dés sont doublés deux fois (×4),
    mais le bonus fixe (+8, +5…) n'est jamais multiplié."""
    expr = expr.strip().lower().replace(" ", "")
    m_bonus = re.search(r'([+-]\d+)$', expr)
    if m_bonus:
        expr = expr[:m_bonus.start()]
    total = 0
    for m in re.finditer(r'(\d*)d(\d+)', expr):
        n = int(m.group(1)) if m.group(1) else 1
        sides = int(m.group(2))
        total += sum(random.randint(1, sides) for _ in range(n))
    return total


def _dice_avg(expr: str) -> float:
    """Retourne la moyenne théorique d'une expression de dés."""
    expr = expr.strip().lower().replace(" ", "")
    total = 0.0
    bonus = 0

    m_bonus = re.search(r'([+-]\d+)$', expr)
    if m_bonus:
        bonus = int(m_bonus.group(1))
        expr = expr[:m_bonus.start()]

    for m in re.finditer(r'(\d*)d(\d+)', expr):
        n = int(m.group(1)) if m.group(1) else 1
        sides = int(m.group(2))
        total += n * (sides + 1) / 2

    return total + bonus


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


# ─── Entité de simulation ─────────────────────────────────────────────────────
class SimCombatant:
    def __init__(self, name: str, side: str, max_hp: int, ac: int,
                 atk_bonus: int, dmg_expr: str, n_attacks: int = 1,
                 color: str = "#e0e0e0", bestiary_name: str = ""):
        self.name         = name
        self.side         = side
        self.max_hp       = max_hp
        self.hp           = max_hp
        self.ac           = ac
        self.atk_bonus    = atk_bonus
        self.dmg_expr     = dmg_expr
        self.n_attacks    = n_attacks
        self.color        = color
        self.bestiary_name = bestiary_name  # nom exact dans le bestiary
        self.use_lair     = False           # activer les lair actions pour la sim LLM
        self.extra_spells: list = []        # sorts ajoutés manuellement via le picker

        # Statistiques
        self.stat_dmg_dealt    = 0
        self.stat_dmg_taken    = 0
        self.stat_hits         = 0
        self.stat_misses       = 0
        self.stat_kills        = 0
        self.stat_round_down   = None   # round auquel il est tombé (None = survécu)

    @property
    def alive(self) -> bool:
        return self.hp > 0

    def take_damage(self, dmg: int):
        self.hp = max(0, self.hp - dmg)
        self.stat_dmg_taken += dmg

    def reset(self):
        self.hp = self.max_hp
        self.stat_dmg_dealt = 0
        self.stat_dmg_taken = 0
        self.stat_hits      = 0
        self.stat_misses    = 0
        self.stat_kills     = 0
        self.stat_round_down = None


# ─── Fenêtre principale ───────────────────────────────────────────────────────
class CombatSimulator:
    def __init__(self, root: tk.Tk, state_loader=None, chat_queue=None, llm_config: dict | None = None,
                 inject_to_agents_fn=None):
        self.root               = root
        self._load_state        = state_loader
        self.chat_queue         = chat_queue
        self._llm_config        = llm_config          # AutoGen config_list dict (optionnel)
        self._inject_to_agents  = inject_to_agents_fn # callable(text) → injecte dans le groupchat autogen

        self.combatants: list[SimCombatant] = []
        self._sim_log: list[str] = []
        self._sim_done = False
        self._last_winner = "Indéterminé"
        self._step_mode = False
        self._order: list[SimCombatant] = []
        self._round = 0
        self._turn_idx = 0
        self._spells_used: dict = {}   # {char_name: {str(level): count}} — LLM sim uniquement
        self._state_applied = False    # True une fois que _apply_to_state a été appelé

        self._build_window()
        self._import_pcs()

    # ── Construction UI ───────────────────────────────────────────────────────
    def _build_window(self):
        self.win = tk.Toplevel(self.root)
        self.win.title("⚡ Simulateur de Combat Rapide")
        self.win.geometry("1200x800")
        self.win.configure(bg=C["bg"])
        self.win.minsize(900, 600)
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)

        self._build_topbar()

        # Zone principale : gauche (combatants) | droite (log + stats)
        main = tk.Frame(self.win, bg=C["bg"])
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 8))

        # ── Colonne gauche : liste des combatants ─────────────────────────────
        left = tk.Frame(main, bg=C["panel"], width=460)
        left.pack(side=tk.LEFT, fill=tk.BOTH, padx=(0, 6))
        left.pack_propagate(False)

        tk.Label(left, text="COMBATANTS", bg=C["panel"], fg=C["gold"],
                 font=("Consolas", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 2))

        # En-tête colonnes
        hdr = tk.Frame(left, bg="#0d1018")
        hdr.pack(fill=tk.X, padx=6)
        for txt, w in [("Nom", 120), ("Camp", 60), ("PV", 50), ("CA", 36),
                       ("Att", 36), ("Dégâts", 80), ("Att/t", 40)]:
            tk.Label(hdr, text=txt, bg="#0d1018", fg=C["fg_dim"],
                     font=("Consolas", 8, "bold"), width=w//8, anchor="w"
                     ).pack(side=tk.LEFT, padx=2, pady=2)

        # Canvas scrollable
        c_frame = tk.Frame(left, bg=C["bg"])
        c_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=2)

        self._list_canvas = tk.Canvas(c_frame, bg=C["bg"], highlightthickness=0)
        sb = tk.Scrollbar(c_frame, orient="vertical", command=self._list_canvas.yview)
        self._list_inner = tk.Frame(self._list_canvas, bg=C["bg"])
        # FIX SEGFAULT : polling au lieu de <Configure>
        def _poll_list_scroll():
            try:
                if not self._list_inner.winfo_exists(): return
                self._list_canvas.configure(scrollregion=self._list_canvas.bbox("all"))
                self._list_inner.after(400, _poll_list_scroll)
            except Exception:
                pass
        self._list_inner.after(200, _poll_list_scroll)
        self._list_canvas.create_window((0, 0), window=self._list_inner, anchor="nw")
        self._list_canvas.configure(yscrollcommand=sb.set)
        self._list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        # ── Formulaire ajout ennemi ───────────────────────────────────────────
        add_frame = tk.LabelFrame(left, text="  ➕ Ajouter des ennemis  ",
                                  bg=C["panel"], fg=C["fg_dim"],
                                  font=("Consolas", 8), bd=1, relief="groove")
        add_frame.pack(fill=tk.X, padx=6, pady=6)

        # ── Recherche bestiary ────────────────────────────────────────────────
        if _BESTIARY_OK:
            search_row = tk.Frame(add_frame, bg=C["panel"])
            search_row.pack(fill=tk.X, padx=6, pady=(6, 2))

            tk.Label(search_row, text="🔍 Fiche :", bg=C["panel"], fg=C["gold"],
                     font=("Consolas", 8, "bold")).pack(side=tk.LEFT)

            self._e_search_var = tk.StringVar()
            self._e_search = tk.Entry(search_row, textvariable=self._e_search_var,
                                      bg=C["entry"], fg=C["fg"],
                                      font=("Consolas", 9), insertbackground=C["fg"],
                                      relief="flat", width=22)
            self._e_search.pack(side=tk.LEFT, padx=(4, 6), ipady=2)

            self._bestiary_status = tk.Label(search_row, text="", bg=C["panel"],
                                             fg=C["fg_dim"], font=("Consolas", 8))
            self._bestiary_status.pack(side=tk.LEFT)

            # Dropdown suggestions
            self._suggest_frame = tk.Frame(add_frame, bg="#0d1018", bd=1, relief="solid")
            self._suggest_labels: list[tk.Label] = []
            self._suggest_visible = False
            self._suggest_idx = -1

            def _on_search_change(*_):
                query = self._e_search_var.get().strip()
                self._hide_suggestions()
                if len(query) < 1:
                    return
                results = _bestiary_search(query, max_results=8)
                if not results:
                    return
                for w in self._suggest_frame.winfo_children():
                    w.destroy()
                self._suggest_labels.clear()
                for res_name in results:
                    lbl_w = tk.Label(self._suggest_frame, text=res_name,
                                     bg="#0d1018", fg=C["fg"],
                                     font=("Consolas", 9), anchor="w",
                                     padx=8, pady=2, cursor="hand2")
                    lbl_w.pack(fill=tk.X)
                    lbl_w.bind("<Enter>",    lambda e, l=lbl_w: l.config(bg=C["border"]))
                    lbl_w.bind("<Leave>",    lambda e, l=lbl_w: l.config(bg="#0d1018"))
                    lbl_w.bind("<Button-1>", lambda e, n=res_name: self._pick_bestiary(n))
                    self._suggest_labels.append(lbl_w)
                self._suggest_frame.place(
                    in_=search_row, x=self._e_search.winfo_x(),
                    y=search_row.winfo_height() + 2,
                    width=220
                )
                self._suggest_visible = True

            self._e_search_var.trace_add("write", _on_search_change)
            self._e_search.bind("<Escape>",   lambda e: self._hide_suggestions())
            self._e_search.bind("<FocusOut>", lambda e: self.win.after(150, self._hide_suggestions))

            def _nav_suggest(event):
                if not self._suggest_visible or not self._suggest_labels:
                    return
                if event.keysym == "Down":
                    self._suggest_idx = min(self._suggest_idx + 1, len(self._suggest_labels) - 1)
                elif event.keysym == "Up":
                    self._suggest_idx = max(self._suggest_idx - 1, 0)
                elif event.keysym == "Return":
                    if 0 <= self._suggest_idx < len(self._suggest_labels):
                        name = self._suggest_labels[self._suggest_idx].cget("text")
                        self._pick_bestiary(name)
                    return
                for i, l in enumerate(self._suggest_labels):
                    l.config(bg=C["border"] if i == self._suggest_idx else "#0d1018")

            self._e_search.bind("<Down>",   _nav_suggest)
            self._e_search.bind("<Up>",     _nav_suggest)
            self._e_search.bind("<Return>", _nav_suggest)

            tk.Frame(add_frame, bg=C["border"], height=1).pack(fill=tk.X, padx=6, pady=2)

        row1 = tk.Frame(add_frame, bg=C["panel"])
        row1.pack(fill=tk.X, padx=6, pady=(4, 2))
        row2 = tk.Frame(add_frame, bg=C["panel"])
        row2.pack(fill=tk.X, padx=6, pady=(0, 6))

        def lbl(parent, t):
            return tk.Label(parent, text=t, bg=C["panel"], fg=C["fg_dim"],
                            font=("Consolas", 8))
        def ent(parent, w, default=""):
            e = tk.Entry(parent, bg=C["entry"], fg=C["fg"],
                         font=("Consolas", 9), insertbackground=C["fg"],
                         relief="flat", width=w)
            e.insert(0, default)
            return e

        lbl(row1, "Nom").pack(side=tk.LEFT)
        self._e_name = ent(row1, 12, "Gobelin")
        self._e_name.pack(side=tk.LEFT, padx=(2, 8), ipady=2)

        lbl(row1, "Camp").pack(side=tk.LEFT)
        self._e_side_var = tk.StringVar(value="Ennemis")
        tk.OptionMenu(row1, self._e_side_var, "Ennemis", "Héros", "Neutre"
                     ).pack(side=tk.LEFT, padx=(2, 8))

        lbl(row1, "Qté").pack(side=tk.LEFT)
        self._e_qty = ent(row1, 3, "3")
        self._e_qty.pack(side=tk.LEFT, padx=2, ipady=2)

        lbl(row2, "PV").pack(side=tk.LEFT)
        self._e_hp = ent(row2, 5, "30")
        self._e_hp.pack(side=tk.LEFT, padx=(2, 8), ipady=2)

        lbl(row2, "CA").pack(side=tk.LEFT)
        self._e_ac = ent(row2, 4, "13")
        self._e_ac.pack(side=tk.LEFT, padx=(2, 8), ipady=2)

        lbl(row2, "Att+").pack(side=tk.LEFT)
        self._e_atk = ent(row2, 4, "5")
        self._e_atk.pack(side=tk.LEFT, padx=(2, 8), ipady=2)

        lbl(row2, "Dégâts").pack(side=tk.LEFT)
        self._e_dmg = ent(row2, 8, "2d6+3")
        self._e_dmg.pack(side=tk.LEFT, padx=(2, 8), ipady=2)

        lbl(row2, "Att/tour").pack(side=tk.LEFT)
        self._e_natk = ent(row2, 3, "1")
        self._e_natk.pack(side=tk.LEFT, padx=(2, 4), ipady=2)

        tk.Button(add_frame, text="➕ Ajouter",
                  bg=_darken(C["blue"], 0.5), fg=C["blue_b"],
                  font=("Consolas", 9, "bold"), relief="flat",
                  padx=8, pady=3, cursor="hand2",
                  command=self._add_enemy).pack(side=tk.LEFT, pady=(0, 6), padx=6)

        # Checkbox Lair actions
        self._lair_var = tk.BooleanVar(value=False)
        tk.Checkbutton(add_frame, text="🏰 Lair actions",
                       variable=self._lair_var,
                       bg=C["panel"], fg="#c8a820",
                       selectcolor=C["entry"],
                       activebackground=C["panel"],
                       font=("Consolas", 8),
                       relief="flat").pack(side=tk.LEFT, pady=(0, 6))

        # ── Panneau sorts (ajout manuel via picker) ───────────────────────────
        tk.Frame(add_frame, bg=C["border"], height=1).pack(fill=tk.X, padx=6, pady=(2, 4))

        spell_hdr = tk.Frame(add_frame, bg=C["panel"])
        spell_hdr.pack(fill=tk.X, padx=6, pady=(0, 2))
        tk.Label(spell_hdr, text="✨ Sorts manuels :", bg=C["panel"], fg=C["gold"],
                 font=("Consolas", 8, "bold")).pack(side=tk.LEFT)
        tk.Button(spell_hdr, text="＋ Sort",
                  bg=_darken(C["purple"], 0.5), fg="#a855f7",
                  font=("Consolas", 8, "bold"), relief="flat",
                  padx=6, pady=2, cursor="hand2",
                  command=self._open_spell_picker_for_enemy).pack(side=tk.LEFT, padx=6)
        tk.Button(spell_hdr, text="✕ Vider",
                  bg="#1a0808", fg="#885555",
                  font=("Consolas", 7), relief="flat",
                  padx=4, pady=2, cursor="hand2",
                  command=self._clear_enemy_spells).pack(side=tk.LEFT, padx=2)

        self._enemy_spells_label = tk.Label(
            add_frame, text="(aucun sort sélectionné)",
            bg=C["panel"], fg=C["fg_dim"],
            font=("Consolas", 7, "italic"), anchor="w",
            wraplength=430, justify=tk.LEFT
        )
        self._enemy_spells_label.pack(fill=tk.X, padx=10, pady=(0, 6))
        self._current_enemy_spells: list = []   # sorts en attente pour le prochain ajout

        # ── Colonne droite : log + stats ──────────────────────────────────────
        right = tk.Frame(main, bg=C["bg"])
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Onglets manuels (pas de ttk pour éviter le segfault Linux)
        tab_bar = tk.Frame(right, bg="#080a10", height=32)
        tab_bar.pack(fill=tk.X)
        tab_bar.pack_propagate(False)

        self._tab_content = tk.Frame(right, bg=C["bg"])
        self._tab_content.pack(fill=tk.BOTH, expand=True)

        # Contenus des onglets (frames superposées)
        log_tab   = tk.Frame(self._tab_content, bg=C["bg"])
        stats_tab = tk.Frame(self._tab_content, bg=C["bg"])
        self._tabs = [log_tab, stats_tab]

        def show_tab(idx):
            for f in self._tabs:
                f.place_forget()
            self._tabs[idx].place(relx=0, rely=0, relwidth=1, relheight=1)
            for i, b in enumerate(self._tab_btns):
                b.config(fg=C["gold"] if i == idx else C["fg_dim"],
                         bg=C["bg"] if i == idx else "#080a10")
            self._active_tab = idx

        self._active_tab = 0
        self._tab_btns = []
        for i, label in enumerate(["📜  Journal de combat", "📊  Statistiques"]):
            b = tk.Button(tab_bar, text=label,
                          bg="#080a10", fg=C["fg_dim"],
                          font=("Consolas", 9, "bold"),
                          relief="flat", padx=14, pady=4,
                          cursor="hand2",
                          command=lambda i=i: show_tab(i))
            b.pack(side=tk.LEFT)
            self._tab_btns.append(b)

        self._show_tab = show_tab
        show_tab(0)

        # Tab 1 : Log
        self._log_box = scrolledtext.ScrolledText(
            log_tab, bg="#0a0c10", fg=C["fg"],
            font=("Consolas", 9), state=tk.DISABLED,
            wrap=tk.WORD, relief="flat"
        )
        self._log_box.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        for tag, color in [("round",  C["gold"]),
                            ("hit",    C["green_b"]),
                            ("miss",   C["fg_dim"]),
                            ("kill",   C["red_bright"]),
                            ("crit",   "#ff9900"),
                            ("system", C["blue_b"]),
                            ("result", C["fg_gold"])]:
            self._log_box.tag_config(tag, foreground=color)

        # Tab 2 : Statistiques (canvas scrollable)
        stats_canvas = tk.Canvas(stats_tab, bg=C["bg"], highlightthickness=0)
        stats_sb = tk.Scrollbar(stats_tab, orient="vertical", command=stats_canvas.yview)
        self._stats_frame = tk.Frame(stats_canvas, bg=C["bg"])
        # FIX SEGFAULT : polling au lieu de <Configure>
        def _poll_stats_scroll():
            try:
                if not self._stats_frame.winfo_exists(): return
                stats_canvas.configure(scrollregion=stats_canvas.bbox("all"))
                self._stats_frame.after(400, _poll_stats_scroll)
            except Exception:
                pass
        self._stats_frame.after(200, _poll_stats_scroll)
        stats_canvas.create_window((0, 0), window=self._stats_frame, anchor="nw")
        stats_canvas.configure(yscrollcommand=stats_sb.set)
        stats_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        stats_sb.pack(side=tk.RIGHT, fill=tk.Y)

        self._placeholder_lbl = tk.Label(
            self._stats_frame,
            text="Les statistiques apparaîtront ici après la simulation.",
            bg=C["bg"], fg=C["fg_dim"], font=("Consolas", 10)
        )
        self._placeholder_lbl.pack(expand=True, pady=40)

    def _build_topbar(self):
        bar = tk.Frame(self.win, bg="#080a10", height=50)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        tk.Label(bar, text="⚡  SIMULATEUR DE COMBAT RAPIDE",
                 bg="#080a10", fg=C["gold"],
                 font=("Consolas", 13, "bold")).pack(side=tk.LEFT, padx=16, pady=8)

        right = tk.Frame(bar, bg="#080a10")
        right.pack(side=tk.RIGHT, padx=12)

        def btn(text, color, cmd):
            return tk.Button(right, text=text,
                             bg=_darken(color, 0.4), fg=color,
                             font=("Consolas", 9, "bold"),
                             activebackground=_darken(color, 0.6),
                             activeforeground="white",
                             relief="flat", padx=10, pady=4,
                             cursor="hand2", command=cmd)

        btn("🔄 Réinitialiser",  C["fg_dim"],    self._reset).pack(side=tk.LEFT, padx=3)
        btn("👣 Pas à pas",      C["blue"],       self._toggle_step).pack(side=tk.LEFT, padx=3)
        self._btn_step_next = btn("▶ Tour suivant", C["gold"], self._sim_step)
        self._btn_step_next.pack(side=tk.LEFT, padx=3)
        self._btn_step_next.config(state=tk.DISABLED)
        btn("⚡ Simuler (dés)",  C["green"],      self._simulate_all).pack(side=tk.LEFT, padx=3)
        self._btn_llm_sim = btn("🧠 Simuler (LLM)", "#a855f7", self._simulate_llm)
        self._btn_llm_sim.pack(side=tk.LEFT, padx=3)
        btn("📋 Copier log",     C["purple"],     self._copy_log).pack(side=tk.LEFT, padx=3)

        self._btn_apply_state = btn("⚙️ Appliquer aux héros", "#c07000", self._apply_to_state_ui)
        self._btn_apply_state.pack(side=tk.LEFT, padx=3)
        self._btn_apply_state.config(state=tk.DISABLED)

        self._btn_send_agents = btn("📨 Envoyer aux agents", "#1a6a3a", self._send_to_agents)
        self._btn_send_agents.pack(side=tk.LEFT, padx=3)
        self._btn_send_agents.config(state=tk.DISABLED)

        self._step_lbl = tk.Label(bar, text="", bg="#080a10", fg=C["blue_b"],
                                   font=("Consolas", 9))
        self._step_lbl.pack(side=tk.LEFT, padx=8)

        self._btn_step_mode = right.winfo_children()[1]  # ref au bouton pas-à-pas

    # ── Import PJ depuis state ────────────────────────────────────────────────
    def _import_pcs(self):
        if self._load_state:
            try:
                state = self._load_state()
                for name, data in state.get("characters", {}).items():
                    defaults = PC_DEFAULTS.get(name, {})
                    hp = data.get("hp", defaults.get("max_hp", 100))
                    c = SimCombatant(
                        name      = name,
                        side      = "Héros",
                        max_hp    = hp,
                        ac        = defaults.get("ac", 16),
                        atk_bonus = defaults.get("atk", 10),
                        dmg_expr  = defaults.get("dmg", "2d6+5"),
                        n_attacks = defaults.get("n_attacks", 2),
                        color     = defaults.get("color", "#a0c4ff"),
                    )
                    self.combatants.append(c)
            except Exception as e:
                print(f"[Sim] Erreur import PJ : {e}")
        else:
            # Fallback sans state_loader
            for name, d in PC_DEFAULTS.items():
                self.combatants.append(SimCombatant(
                    name=name, side="Héros",
                    max_hp=d["max_hp"], ac=d["ac"],
                    atk_bonus=d["atk"], dmg_expr=d["dmg"],
                    n_attacks=d["n_attacks"], color=d["color"]
                ))
        self._refresh_list()

    # ── Sorts manuels ennemis ─────────────────────────────────────────────────

    def _open_spell_picker_for_enemy(self):
        """Ouvre le SpellPickerDialog pour ajouter un sort à l'ennemi en cours de saisie."""
        try:
            from spell_data import SpellPickerDialog
        except ImportError:
            self._log_write("⚠️ Module spell_data introuvable.\n", "system")
            return

        def _on_pick(sp: dict):
            if not hasattr(self, "_current_enemy_spells"):
                self._current_enemy_spells = []
            # Évite les doublons
            if not any(s["name"] == sp["name"] for s in self._current_enemy_spells):
                self._current_enemy_spells.append(sp)
            # Met à jour le label
            names = ", ".join(
                f"{s['name']} (Niv {s['level'] if s['level'] > 0 else 'TM'})"
                for s in self._current_enemy_spells
            )
            self._enemy_spells_label.config(
                text=names or "(aucun sort sélectionné)",
                fg="#a855f7" if names else C["fg_dim"]
            )

        SpellPickerDialog(self.win, _on_pick, title="✨ Ajouter un sort à l'ennemi")

    def _clear_enemy_spells(self):
        if hasattr(self, "_current_enemy_spells"):
            self._current_enemy_spells.clear()
        if hasattr(self, "_enemy_spells_label"):
            self._enemy_spells_label.config(
                text="(aucun sort sélectionné)", fg=C["fg_dim"])

    # ── Bestiary : autocomplétion et extraction de stats ─────────────────────
    def _hide_suggestions(self):
        if hasattr(self, "_suggest_frame"):
            self._suggest_frame.place_forget()
            self._suggest_visible = False
            self._suggest_idx = -1

    def _pick_bestiary(self, bestiary_name: str):
        """Remplit le formulaire avec les stats du monstre sélectionné."""
        self._hide_suggestions()
        stats = _sim_stats_from_monster(bestiary_name)
        if not stats:
            self._bestiary_status.config(text="⚠️ Introuvable", fg=C["red_bright"])
            return
        self._e_name.delete(0, tk.END)
        self._e_name.insert(0, bestiary_name[:14])
        self._set_entry(self._e_hp,   str(stats["hp"]))
        self._set_entry(self._e_ac,   str(stats["ac"]))
        self._set_entry(self._e_atk,  str(stats["atk_bonus"]))
        self._set_entry(self._e_dmg,  stats["dmg_expr"])
        self._set_entry(self._e_natk, str(stats["n_attacks"]))
        # Mémorise le nom bestiary exact pour le formulaire
        self._current_bestiary_name = bestiary_name
        cr_txt = f"CR {stats['cr']}" if stats["cr"] != "?" else ""
        # Vérifie si lair actions disponibles
        lg = _bestiary_get_legendary_group(bestiary_name)
        lair_txt = "  🏰 Lair dispo" if (lg and lg.get("lairActions")) else ""
        self._bestiary_status.config(
            text=f"✅ {cr_txt}  HP:{stats['hp']} CA:{stats['ac']}{lair_txt}",
            fg=C["green_b"]
        )
        self._e_search_var.set("")

    def _set_entry(self, entry: tk.Entry, value: str):
        entry.delete(0, tk.END)
        entry.insert(0, value)

    # ── Ajout d'ennemis ───────────────────────────────────────────────────────
    def _add_enemy(self):
        try:
            name   = self._e_name.get().strip() or "Ennemi"
            side   = self._e_side_var.get()
            qty    = max(1, int(self._e_qty.get() or 1))
            hp     = max(1, int(self._e_hp.get() or 10))
            ac     = max(1, int(self._e_ac.get() or 12))
            atk    = int(self._e_atk.get() or 4)
            dmg    = self._e_dmg.get().strip() or "1d6"
            natk   = max(1, int(self._e_natk.get() or 1))
        except ValueError:
            self._log_write("⚠️ Vérifiez les valeurs numériques.\n", "system")
            return

        # Récupère le nom bestiary exact s'il a été sélectionné via la recherche
        bname = getattr(self, "_current_bestiary_name", name)
        use_lair = self._lair_var.get() if hasattr(self, "_lair_var") else False

        color_pool = NPC_COLORS
        for i in range(qty):
            n = f"{name} {i+1}" if qty > 1 else name
            col = color_pool[len(self.combatants) % len(color_pool)]
            c = SimCombatant(
                name=n, side=side, max_hp=hp, ac=ac,
                atk_bonus=atk, dmg_expr=dmg, n_attacks=natk,
                color=col, bestiary_name=bname
            )
            c.use_lair = use_lair
            c.extra_spells = list(getattr(self, "_current_enemy_spells", []))
            self.combatants.append(c)
        # Reset
        self._current_bestiary_name = ""
        if hasattr(self, "_current_enemy_spells"):
            self._current_enemy_spells.clear()
            self._enemy_spells_label.config(text="(aucun sort sélectionné)")
        self._refresh_list()

    # ── Liste des combatants (affichage) ─────────────────────────────────────
    def _refresh_list(self):
        for w in self._list_inner.winfo_children():
            w.destroy()

        for c in self.combatants:
            bg = C["pc"] if c.side == "Héros" else C["enemy"]
            if not c.alive:
                bg = "#111116"

            row = tk.Frame(self._list_inner, bg=bg, pady=2)
            row.pack(fill=tk.X, padx=2, pady=1)

            def lbl(text, w, color=C["fg"], bold=False):
                f = ("Consolas", 9, "bold") if bold else ("Consolas", 9)
                return tk.Label(row, text=text, bg=bg, fg=color, font=f,
                                width=w, anchor="w")

            hp_color = C["green_b"] if c.hp > c.max_hp * 0.5 else (
                       C["gold"] if c.hp > c.max_hp * 0.25 else C["red_bright"])
            dead_mark = " 💀" if not c.alive else ""

            lbl(c.name[:14] + dead_mark, 14, c.color, bold=True).pack(side=tk.LEFT, padx=4)
            lbl(c.side[:8],              8,  C["fg_dim"]).pack(side=tk.LEFT, padx=2)
            lbl(f"{c.hp}/{c.max_hp}",   8,  hp_color).pack(side=tk.LEFT, padx=2)
            lbl(str(c.ac),              4,  C["fg_dim"]).pack(side=tk.LEFT, padx=2)
            lbl(f"+{c.atk_bonus}",      5,  C["fg_dim"]).pack(side=tk.LEFT, padx=2)
            lbl(c.dmg_expr,             9,  C["fg_dim"]).pack(side=tk.LEFT, padx=2)
            lbl(f"×{c.n_attacks}",      4,  C["fg_dim"]).pack(side=tk.LEFT, padx=2)
            # Icônes bestiary / lair
            if c.bestiary_name:
                lbl("📋", 2, "#6688aa").pack(side=tk.LEFT, padx=1)
            if c.use_lair:
                lbl("🏰", 2, C["gold"]).pack(side=tk.LEFT, padx=1)
            if c.extra_spells:
                sp_tip = ", ".join(s["name"] for s in c.extra_spells[:3])
                sp_lbl = tk.Label(row, text=f"✨{len(c.extra_spells)}", bg=bg,
                                  fg="#a855f7", font=("Consolas", 7),
                                  cursor="hand2")
                sp_lbl.pack(side=tk.LEFT, padx=1)

            # Bouton supprimer
            tk.Button(row, text="✕", bg=bg, fg="#553333",
                      font=("Consolas", 8), relief="flat", padx=2,
                      cursor="hand2",
                      command=lambda cc=c: self._remove(cc)).pack(side=tk.RIGHT, padx=4)

    def _remove(self, c: SimCombatant):
        self.combatants = [x for x in self.combatants if x is not c]
        self._refresh_list()

    # ── Log ───────────────────────────────────────────────────────────────────
    def _log_write(self, text: str, tag: str = ""):
        self._log_box.config(state=tk.NORMAL)
        if tag:
            self._log_box.insert(tk.END, text, tag)
        else:
            self._log_box.insert(tk.END, text)
        self._log_box.see(tk.END)
        self._log_box.config(state=tk.DISABLED)
        self._sim_log.append(text)

    def _log_clear(self):
        self._log_box.config(state=tk.NORMAL)
        self._log_box.delete("1.0", tk.END)
        self._log_box.config(state=tk.DISABLED)
        self._sim_log.clear()

    # ── Réinitialisation ─────────────────────────────────────────────────────
    def _reset(self):
        for c in self.combatants:
            c.reset()
        self._log_clear()
        self._sim_done = False
        self._order = []
        self._round = 0
        self._turn_idx = 0
        self._spells_used = {}
        self._state_applied = False
        self._btn_step_next.config(state=tk.DISABLED)
        self._btn_send_agents.config(state=tk.DISABLED)
        self._btn_apply_state.config(state=tk.DISABLED)
        self._refresh_list()
        for w in self._stats_frame.winfo_children():
            w.destroy()
        self._placeholder_lbl = tk.Label(
            self._stats_frame,
            text="Les statistiques apparaîtront ici après la simulation.",
            bg=C["bg"], fg=C["fg_dim"], font=("Consolas", 10)
        )
        self._placeholder_lbl.pack(expand=True)
        self._step_lbl.config(text="")

    # ── Pas-à-pas ─────────────────────────────────────────────────────────────
    def _toggle_step(self):
        self._step_mode = not self._step_mode
        if self._step_mode:
            self._btn_step_next.config(state=tk.NORMAL)
            self._step_lbl.config(text="MODE PAS-À-PAS activé")
        else:
            self._btn_step_next.config(state=tk.DISABLED)
            self._step_lbl.config(text="")

    # ── Initialisation du combat ──────────────────────────────────────────────
    def _init_simulation(self) -> bool:
        """Prépare et trie l'ordre d'initiative. Retourne False si impossible."""
        sides = set(c.side for c in self.combatants)
        if len(sides) < 2:
            self._log_write("⚠️ Il faut au moins 2 camps différents pour simuler.\n", "system")
            return False

        # Reset stats
        for c in self.combatants:
            c.reset()

        # Roll initiative
        self._log_clear()
        self._log_write("═" * 60 + "\n", "round")
        self._log_write("   ⚡ DÉBUT DE LA SIMULATION\n", "round")
        self._log_write("═" * 60 + "\n\n", "round")
        self._log_write("🎲 Jet d'initiative :\n", "system")

        order = []
        for c in self.combatants:
            roll = random.randint(1, 20)
            init = roll + (2 if c.side == "Héros" else 1)  # Dex bonus estimé
            c._initiative = init
            order.append(c)
            self._log_write(f"  {c.name:<18} d20({roll:2d}) = {init:2d}\n", "")

        self._order = sorted(order, key=lambda x: x._initiative, reverse=True)
        self._log_write("\n📋 Ordre d'initiative : " +
                        " → ".join(c.name for c in self._order) + "\n\n", "system")

        self._round    = 0
        self._turn_idx = len(self._order)  # Force new round au 1er appel
        self._sim_done = False
        return True

    # ── Un tour de simulation ─────────────────────────────────────────────────
    def _do_one_turn(self) -> bool:
        """
        Joue le prochain tour dans l'ordre d'initiative.
        Retourne True si le combat continue, False si terminé.
        """
        # Passe au round suivant si tous ont joué
        if self._turn_idx >= len(self._order):
            self._round += 1
            self._turn_idx = 0
            self._log_write("\n" + "─" * 60 + "\n", "round")
            self._log_write(f"   ⚔️  ROUND {self._round}\n", "round")
            self._log_write("─" * 60 + "\n", "round")

        # Récupère le combatant actif
        attacker = self._order[self._turn_idx]
        self._turn_idx += 1

        if not attacker.alive:
            return self._combat_still_going()

        # Cible : un adversaire vivant aléatoire
        enemies = [c for c in self.combatants
                   if c.side != attacker.side and c.alive]
        if not enemies:
            return False  # Combat terminé

        target = random.choice(enemies)

        # Attaques
        for atk_num in range(attacker.n_attacks):
            if not target.alive:
                # Changer de cible si la première est morte
                enemies = [c for c in self.combatants
                           if c.side != attacker.side and c.alive]
                if not enemies:
                    break
                target = random.choice(enemies)

            roll = random.randint(1, 20)
            is_crit = (roll == 20)
            total_roll = roll + attacker.atk_bonus

            atk_label = f" (att. {atk_num+1})" if attacker.n_attacks > 1 else ""

            if is_crit or total_roll >= target.ac:
                # Touché
                dmg = _parse_dice(attacker.dmg_expr)
                if is_crit:
                    # Doubler les dés deux fois (×4 dés totaux) — bonus flat inchangé.
                    # Tous les dés de l'attaque sont concernés (Divine Smite inclus).
                    for _ in range(3):
                        dmg += _roll_dice_only(attacker.dmg_expr)
                target.take_damage(dmg)
                attacker.stat_dmg_dealt += dmg
                attacker.stat_hits += 1

                tag = "crit" if is_crit else "hit"
                crit_str = " 💥 CRITIQUE !" if is_crit else ""
                dead_str = ""

                if not target.alive:
                    attacker.stat_kills += 1
                    target.stat_round_down = self._round
                    dead_str = " — 💀 MORT !"

                self._log_write(
                    f"  {attacker.name:<18}{atk_label} → {target.name:<18} "
                    f"d20={roll:2d}+{attacker.atk_bonus} ({total_roll:2d} vs CA {target.ac:2d}) "
                    f"TOUCHÉ{crit_str} : {dmg:3d} dégâts  "
                    f"[PV: {max(0,target.hp):3d}/{target.max_hp}]{dead_str}\n",
                    tag
                )
                if dead_str:
                    self._log_write(f"  ☠️  {target.name} est éliminé !\n", "kill")
            else:
                attacker.stat_misses += 1
                self._log_write(
                    f"  {attacker.name:<18}{atk_label} → {target.name:<18} "
                    f"d20={roll:2d}+{attacker.atk_bonus} ({total_roll:2d} vs CA {target.ac:2d}) "
                    f"RATÉ\n",
                    "miss"
                )

        self._refresh_list()
        return self._combat_still_going()

    def _combat_still_going(self) -> bool:
        sides_alive = set(c.side for c in self.combatants if c.alive)
        return len(sides_alive) > 1

    # ── Simulation complète ───────────────────────────────────────────────────
    def _simulate_all(self):
        if not self._init_simulation():
            return
        self._step_mode = False
        self._btn_step_next.config(state=tk.DISABLED)
        self._step_lbl.config(text="Simulation en cours...")
        self.win.update()

        MAX_ROUNDS = 200
        while self._round <= MAX_ROUNDS:
            going = self._do_one_turn()
            if not going:
                break
        else:
            self._log_write(f"\n⚠️ Simulation arrêtée après {MAX_ROUNDS} rounds (boucle infinie?).\n", "system")

        self._finish_simulation()

    # ── Pas à pas ─────────────────────────────────────────────────────────────
    def _sim_step(self):
        if not self._order:
            if not self._init_simulation():
                return
        if self._sim_done:
            return

        going = self._do_one_turn()
        self._step_lbl.config(text=f"Round {self._round} | Tour {self._turn_idx}/{len(self._order)}")

        if not going:
            self._finish_simulation()

    # ── Simulation LLM ────────────────────────────────────────────────────────

    @staticmethod
    def _clean(text: str) -> str:
        """Nettoie les tags 5etools {@tag content} → content."""
        return re.sub(r'\{@\w+\s*([^|}]*)[^}]*\}', r'\1', str(text)).strip()

    @staticmethod
    def _fmt_entries_flat(entries: list) -> str:
        """Aplatit une liste d'entries 5etools en texte lisible."""
        parts = []
        for e in entries:
            if isinstance(e, str):
                parts.append(CombatSimulator._clean(e))
            elif isinstance(e, dict):
                etype = e.get("type", "")
                if etype == "list":
                    for item in e.get("items", []):
                        if isinstance(item, str):
                            parts.append("• " + CombatSimulator._clean(item))
                        elif isinstance(item, dict):
                            parts.append("• " + CombatSimulator._clean(
                                " ".join(str(x) for x in item.get("entries", []) if isinstance(x, str))
                            ))
                else:
                    sub = " ".join(str(x) for x in e.get("entries", []) if isinstance(x, str))
                    if sub:
                        parts.append(CombatSimulator._clean(sub))
        return " ".join(parts)

    @staticmethod
    def _fmt_spellcasting(sc_list: list) -> list[str]:
        """
        Formate les blocs spellcasting (innate + préparé + coven) en lignes texte.
        Inclut save DC, spell lists, slots, et fréquences (at will / daily).
        """
        lines = []
        for sc in sc_list:
            sc_name = sc.get("name", "Spellcasting")
            headers = sc.get("headerEntries", [])
            header_txt = CombatSimulator._clean(" ".join(headers))

            # Fréquences innate
            will = [CombatSimulator._clean(s) for s in sc.get("will", [])]
            daily = sc.get("daily", {})
            daily_lines = []
            for freq, spells in daily.items():
                freq_label = freq.replace("e", "×/jour")
                daily_lines.append(f"{freq_label}: " + ", ".join(CombatSimulator._clean(s) for s in spells))

            # Slots préparés / coven
            slots = sc.get("spells", {})
            slot_lines = []
            for lvl in sorted(slots.keys(), key=lambda x: int(x)):
                lvl_data = slots[lvl]
                n_slots = lvl_data.get("slots", "∞")
                spell_names = [CombatSimulator._clean(s) for s in lvl_data.get("spells", [])]
                slot_lines.append(f"Niv{lvl} ({n_slots} slots): " + ", ".join(spell_names))

            footer = [CombatSimulator._clean(f) for f in sc.get("footerEntries", [])]

            lines.append(f"[{sc_name}] {header_txt}")
            if will:
                lines.append("  À volonté: " + ", ".join(will))
            lines.extend("  " + d for d in daily_lines)
            lines.extend("  " + s for s in slot_lines)
            lines.extend("  " + f for f in footer)

        return lines

    @staticmethod
    def _fmt_resist(lst: list, key: str) -> str:
        parts = []
        for x in lst:
            if isinstance(x, str):
                parts.append(x)
            elif isinstance(x, list):
                parts.append(CombatSimulator._fmt_resist(x, key))
            elif isinstance(x, dict):
                sp = x.get("special", "")
                if sp:
                    parts.append(sp)
                else:
                    sub = x.get(key, [])
                    chunk = ", ".join(sub) if isinstance(sub, list) else str(sub)
                    note = x.get("note", "")
                    pre  = x.get("preNote", "")
                    if pre:   chunk = f"{pre} {chunk}".strip()
                    if note:  chunk = f"{chunk} ({note})"
                    parts.append(chunk)
        return ", ".join(p for p in parts if p)

    def _build_monster_sheet_text(self, c: SimCombatant) -> str:
        """
        Construit une fiche complète pour le LLM :
        stats défensives, caractéristiques, spellcasting complet (innate + coven),
        traits, toutes les actions, actions légendaires, lair actions.
        """
        cl = self._clean
        lines = [f"{'='*55}", f"### {c.name}  |  Camp: {c.side}"]
        lines.append(f"PV: {c.hp}/{c.max_hp}  CA: {c.ac}  "
                     f"Attaque de base: +{c.atk_bonus} / {c.dmg_expr} × {c.n_attacks}/tour")

        if not _BESTIARY_OK:
            return "\n".join(lines)

        # Cherche par bestiary_name si disponible, sinon par nom du combatant
        lookup = c.bestiary_name if c.bestiary_name else c.name
        m = _bestiary_get(lookup)
        if not m:
            base = re.sub(r'\s+\d+$', '', lookup)
            m = _bestiary_get(base)
        if not m:
            return "\n".join(lines)

        # CR / type
        cr_raw = m.get("cr", "?")
        cr = cr_raw.get("cr", "?") if isinstance(cr_raw, dict) else str(cr_raw)
        coven_cr = cr_raw.get("coven") if isinstance(cr_raw, dict) else None
        t = m.get("type", "")
        t = t.get("type", "") if isinstance(t, dict) else str(t)
        cr_str = f"CR {cr}" + (f" (coven: {coven_cr})" if coven_cr else "")
        lines.append(f"{cr_str}  Type: {t}")

        # Caractéristiques avec modificateurs
        stats = {k: m.get(k, 10) for k in ("str","dex","con","int","wis","cha")}
        mods  = {k: (v-10)//2 for k,v in stats.items()}
        lines.append("  ".join(f"{k.upper()} {v}({mods[k]:+d})" for k,v in stats.items()))

        # Saves / compétences
        saves = m.get("save", {})
        if saves:
            lines.append("Saves: " + "  ".join(f"{k.upper()} {v}" for k,v in saves.items()))
        skills = m.get("skill", {})
        if skills:
            lines.append("Skills: " + "  ".join(f"{k} {v}" for k,v in skills.items()))

        # Résistances / immunités (COMPLÈTES avec conditions)
        dr = self._fmt_resist(m.get("resist", []), "resist")
        di = self._fmt_resist(m.get("immune", []), "immune")
        ci = ", ".join(
            x if isinstance(x, str) else x.get("condition", str(x))
            for x in m.get("conditionImmune", [])
        )
        if dr: lines.append(f"⚡ RÉSISTANCES: {dr}")
        if di: lines.append(f"🛡 IMMUNITÉS dégâts: {di}")
        if ci: lines.append(f"🛡 IMMUNITÉS états: {ci}")

        # Sens / languages
        senses = m.get("senses", [])
        if senses: lines.append("Sens: " + ", ".join(senses))

        # ── SPELLCASTING (COMPLET) ────────────────────────────────────────────
        sc_list = m.get("spellcasting", [])
        if sc_list:
            lines.append("--- SORTS ---")
            lines.extend(self._fmt_spellcasting(sc_list))

        # ── TRAITS ────────────────────────────────────────────────────────────
        traits = m.get("trait", [])
        if traits:
            lines.append("--- TRAITS ---")
            for t in traits:
                tname = t.get("name", "")
                tdesc = self._fmt_entries_flat(t.get("entries", []))
                lines.append(f"▸ {tname}: {tdesc[:350]}")

        # ── ACTIONS ───────────────────────────────────────────────────────────
        actions = m.get("action", [])
        if actions:
            lines.append("--- ACTIONS ---")
            for a in actions:
                aname = a.get("name", "")
                adesc = self._fmt_entries_flat(a.get("entries", []))
                lines.append(f"▸ {aname}: {adesc[:350]}")

        # ── ACTIONS BONUS ─────────────────────────────────────────────────────
        bonus_actions = m.get("bonus", [])
        if bonus_actions:
            lines.append("--- ACTIONS BONUS ---")
            for a in bonus_actions:
                lines.append(f"▸ {a.get('name','')}: {self._fmt_entries_flat(a.get('entries',[]))[:250]}")

        # ── RÉACTIONS ─────────────────────────────────────────────────────────
        reactions = m.get("reaction", [])
        if reactions:
            lines.append("--- RÉACTIONS ---")
            for r in reactions:
                lines.append(f"▸ {r.get('name','')}: {self._fmt_entries_flat(r.get('entries',[]))[:250]}")

        # ── ACTIONS LÉGENDAIRES ───────────────────────────────────────────────
        legendary = m.get("legendary", [])
        leg_header = m.get("legendaryHeader", [])
        if legendary:
            lines.append("--- ACTIONS LÉGENDAIRES (3/round) ---")
            if leg_header:
                lines.append(self._fmt_entries_flat(leg_header))
            for la in legendary:
                lname = la.get("name", "")
                lcost = la.get("cost", 1)
                ldesc = self._fmt_entries_flat(la.get("entries", []))
                cost_str = f" (coût: {lcost})" if lcost != 1 else ""
                lines.append(f"▸ {lname}{cost_str}: {ldesc[:300]}")

        # ── LAIR ACTIONS (depuis legendarygroups.json) ────────────────────────
        lg = _bestiary_get_legendary_group(m.get("name", ""), m.get("source", ""))
        if lg and c.use_lair:
            lair = lg.get("lairActions", [])
            if lair:
                lines.append("--- LAIR ACTIONS (initiative 20, actives) ---")
                lines.append(self._fmt_entries_flat(lair[:2]))
                for item in lair[2:]:
                    if isinstance(item, dict) and item.get("type") == "list":
                        for la_item in item.get("items", []):
                            lines.append(f"▸ {cl(la_item)[:300]}")

        return "\n".join(lines)

    def _build_llm_prompt(self) -> tuple[str, str]:
        """Construit le system prompt et le user prompt pour la simulation LLM."""

        system = """Tu es un moteur de simulation de combat D&D 5e expert et rigoureux.

RÈGLES ABSOLUES :
1. SORTS : Si un monstre a des sorts (Innate Spellcasting, Shared Spellcasting, etc.), il DOIT les utiliser tactiquement. Les lanceurs de sorts privilégient les sorts offensifs (lightning bolt, ray of enfeeblement, sleep, hold person, eyebite...) avant les attaques physiques. Utilise les slots disponibles — ne les ignore JAMAIS.
2. RÉSISTANCES : Si un monstre a une résistance ou immunité aux dégâts non-magiques (bludgeoning/piercing/slashing), les attaques physiques normales des PJ font MOITIÉ dégâts (résistance) ou 0 (immunité). Indique-le dans le champ "note" et applique la réduction dans "damage". Les sorts et armes magiques ignorent cette résistance.
3. LAIR ACTIONS : À l'initiative 20 de chaque round, le lair peut agir (si présent dans la fiche). Inclus-les comme une action séparée avec attacker="Lair".
4. MULTIATTAQUE : Respecte le nombre d'attaques par tour indiqué.
5. CRITIQUES : d20=20 → les dés de dégâts sont doublés DEUX FOIS (×4 dés totaux). Le bonus fixe (+X) n'est PAS multiplié. Tous les dés de l'attaque sont concernés : dés d'arme ET dés de Divine Smite ou autre effet additionnel. Ex : 2d6+8 crit → 8d6+8.
6. TACTIQUE : Les ennemis intelligents (INT ≥ 10) agissent tactiquement — ils concentrent leurs attaques, utilisent leurs capacités spéciales au bon moment, fuient si à moins de 25% PV.
7. COHÉRENCE : Les PV après chaque action doivent être cohérents et décroissants correctement.

FORMAT DE RÉPONSE : JSON pur, sans markdown, sans texte avant ou après.
{
  "rounds": [
    {
      "round": 1,
      "narrative": "Récit immersif en français (3-5 phrases, mentionne les sorts, effets spéciaux)",
      "actions": [
        {
          "attacker": "Nom exact",
          "target": "Nom exact",
          "action_type": "attack|spell|lair|special",
          "spell_used": "Nom du sort si applicable",
          "roll": 14,
          "bonus": 6,
          "total": 20,
          "target_ac": 17,
          "hit": true,
          "crit": false,
          "damage": 18,
          "damage_type": "fire|cold|necrotic|etc",
          "resistance_applied": false,
          "target_hp_after": 94,
          "note": "Description courte de l'effet (résistance, condition, etc.)"
        }
      ],
      "deaths": [],
      "conditions_applied": ["target: condition"]
    }
  ],
  "winner": "Nom du camp vainqueur",
  "total_rounds": 5,
  "summary": "Résumé narratif immersif de 4-6 phrases en français"
}"""

        # Fiches complètes
        sheets = "\n\n".join(self._build_monster_sheet_text(c) for c in self.combatants)
        sides  = sorted(set(c.side for c in self.combatants))

        # Contexte coven si plusieurs hags
        hag_names = [c.name for c in self.combatants
                     if any(h in c.name.lower() for h in ("hag", "sorcière", "morgantha"))]
        coven_note = ""
        if len(hag_names) >= 2:
            coven_note = (
                f"\n\nNOTE COVEN : {', '.join(hag_names)} forment un COUVENT. "
                "Elles partagent les slots de sorts du bloc 'Shared Spellcasting (Coven Only)'. "
                "Elles peuvent utiliser ces slots ensemble (pool commun). "
                "Elles ont le CR de coven indiqué, pas leur CR individuel."
            )

        user = (
            f"Simule ce combat D&D 5e en respectant TOUTES les règles ci-dessus.\n\n"
            f"CAMPS : {' vs '.join(sides)}\n\n"
            f"FICHES COMPLÈTES :\n{sheets}"
            f"{coven_note}\n\n"
            f"CONSIGNES :\n"
            f"- Ordre d'initiative : tire des d20 + modificateur DEX pour chaque combatant au round 1.\n"
            f"- Les sorts de zone (lightning bolt, sleep...) peuvent toucher PLUSIEURS ennemis.\n"
            f"- Arrête dès qu'un camp n'a plus de survivants.\n"
            f"- Maximum 20 rounds. Si dépassé : winner = 'Indéterminé'.\n"
            f"- JSON complet et valide uniquement."
        )

        return system, user

    def _simulate_llm(self):
        """Lance la simulation via LLM dans un thread séparé."""
        if self._sim_done:
            return
        sides = set(c.side for c in self.combatants)
        if len(sides) < 2:
            self._log_write("⚠️ Il faut au moins 2 camps différents pour simuler.\n", "system")
            return
        if not self._llm_config:
            self._log_write("⚠️ Aucun llm_config fourni — simulation LLM indisponible.\n", "system")
            return

        # Reset
        for c in self.combatants:
            c.reset()
        self._log_clear()
        self._sim_done = False

        self._log_write("═" * 60 + "\n", "round")
        self._log_write("   🧠 SIMULATION LLM EN COURS...\n", "round")
        self._log_write("═" * 60 + "\n\n", "round")
        self._step_lbl.config(text="🧠 Simulation LLM en cours...")
        self._btn_llm_sim.config(state=tk.DISABLED)

        system_prompt, user_prompt = self._build_llm_prompt()

        def _run():
            try:
                import autogen as _ag
                client = _ag.OpenAIWrapper(
                    config_list=self._llm_config["config_list"]
                )
                response = client.create(messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ])
                raw = response.choices[0].message.content.strip()
                # Nettoyer éventuel markdown
                raw = re.sub(r'^```json\s*', '', raw)
                raw = re.sub(r'\s*```$',    '', raw)
                self.win.after(0, lambda: self._apply_llm_result(raw))
            except Exception as e:
                err = str(e)
                self.win.after(0, lambda: self._llm_error(err))

        threading.Thread(target=_run, daemon=True).start()

    def _apply_llm_result(self, raw_json: str):
        """Applique le résultat JSON du LLM : met à jour les stats et le log."""
        import json as _json
        try:
            data = _json.loads(raw_json)
        except Exception as e:
            self._llm_error(f"JSON invalide : {e}\n\nRéponse brute :\n{raw_json[:500]}")
            return

        # Index des combatants par nom (insensible à la casse)
        by_name = {c.name.lower(): c for c in self.combatants}

        rounds = data.get("rounds", [])
        self._round = data.get("total_rounds", len(rounds))

        for rnd in rounds:
            rnum = rnd.get("round", "?")
            self._log_write("\n" + "─" * 60 + "\n", "round")
            self._log_write(f"   ⚔️  ROUND {rnum}\n", "round")
            self._log_write("─" * 60 + "\n", "round")

            # Récit narratif
            narrative = rnd.get("narrative", "")
            if narrative:
                self._log_write(f"\n📖 {narrative}\n\n", "system")

            # Actions
            for act in rnd.get("actions", []):
                attacker_name = act.get("attacker") or "?"
                target_name   = act.get("target")   or "?"
                roll          = act.get("roll",    0)
                bonus         = act.get("bonus",   0)
                total         = act.get("total",   roll + bonus)
                target_ac     = act.get("target_ac", 0)
                hit           = act.get("hit",    False)
                crit          = act.get("crit",   False)
                dmg           = act.get("damage", 0)
                hp_after      = act.get("target_hp_after", None)
                note          = act.get("note",   "")

                # Mettre à jour les stats du combatant attaquant
                attacker = by_name.get(attacker_name.lower())
                target   = by_name.get(target_name.lower())

                if attacker:
                    if hit:
                        attacker.stat_hits += 1
                        attacker.stat_dmg_dealt += dmg
                    else:
                        attacker.stat_misses += 1

                # ── Suivi sorts utilisés par les héros (pour apply_to_state) ──
                _HERO_NAMES = {"Kaelen", "Elara", "Thorne", "Lyra"}
                if (act.get("action_type") == "spell"
                        and attacker_name in _HERO_NAMES):
                    spell_name = act.get("spell_used", "").strip()
                    if spell_name:
                        lvl = self._get_spell_level(attacker_name, spell_name)
                        if lvl and lvl > 0:
                            self._spells_used.setdefault(attacker_name, {})
                            key = str(lvl)
                            self._spells_used[attacker_name][key] = \
                                self._spells_used[attacker_name].get(key, 0) + 1

                if target and hp_after is not None:
                    # Calculer les dégâts reçus réels
                    dmg_taken = max(0, target.hp - hp_after)
                    target.hp = max(0, hp_after)
                    target.stat_dmg_taken += dmg_taken

                # Log de l'action
                if hit:
                    crit_str = " 💥 CRITIQUE !" if crit else ""
                    note_str = f"  [{note}]" if note else ""
                    dead_str = ""

                    if target and not target.alive and target.stat_round_down is None:
                        attacker and setattr(attacker, 'stat_kills', attacker.stat_kills + 1)
                        target.stat_round_down = rnum
                        dead_str = " — 💀 MORT !"

                    hp_str = f"[PV: {max(0,hp_after):3d}/{target.max_hp}]" if target and hp_after is not None else ""
                    self._log_write(
                        f"  {attacker_name:<18} → {target_name:<18} "
                        f"d20={roll:2d}+{bonus} ({total:2d} vs CA {target_ac:2d}) "
                        f"TOUCHÉ{crit_str} : {dmg:3d} dégâts  {hp_str}{dead_str}{note_str}\n",
                        "crit" if crit else "hit"
                    )
                    if dead_str:
                        self._log_write(f"  ☠️  {target_name} est éliminé !\n", "kill")
                else:
                    self._log_write(
                        f"  {attacker_name:<18} → {target_name:<18} "
                        f"d20={roll:2d}+{bonus} ({total:2d} vs CA {target_ac:2d}) RATÉ\n",
                        "miss"
                    )

            # Morts ce round (vérification)
            for dead_name in rnd.get("deaths", []):
                dead = by_name.get(dead_name.lower())
                if dead and dead.hp > 0:
                    dead.hp = 0

        # Récupérer le vainqueur
        winner = data.get("winner", "Indéterminé")

        # Reconstruire les kills depuis les stats finales si absent
        for c in self.combatants:
            if c.stat_round_down is None and not c.alive:
                c.stat_round_down = self._round

        summary_llm = data.get("summary", "")

        self._log_write("\n" + "═" * 60 + "\n", "round")
        self._log_write(f"   🏆 COMBAT TERMINÉ — Vainqueur : {winner.upper()}\n", "result")
        self._log_write(f"   Durée : {self._round} round(s)\n", "result")
        if summary_llm:
            self._log_write(f"\n📜 {summary_llm}\n", "result")
        self._log_write("═" * 60 + "\n", "round")

        self._sim_done = True
        self._last_winner = winner
        self._btn_llm_sim.config(state=tk.NORMAL)
        self._btn_send_agents.config(state=tk.NORMAL)
        self._btn_apply_state.config(state=tk.NORMAL)
        self._state_applied = False
        self._step_lbl.config(text=f"✅ Terminé en {self._round} round(s) — Vainqueur : {winner}  |  📨 Prêt à envoyer")
        self._refresh_list()
        self._build_stats_table(winner)
        self._show_tab(1)

        # Pas d'envoi automatique — le MJ utilise le bouton 📨 Envoyer aux agents

    def _llm_error(self, msg: str):
        self._log_write(f"\n❌ Erreur simulation LLM :\n{msg}\n", "system")
        self._btn_llm_sim.config(state=tk.NORMAL)
        self._step_lbl.config(text="❌ Erreur LLM")

    # ── Fin de combat ─────────────────────────────────────────────────────────
    def _finish_simulation(self):
        self._sim_done = True
        self._btn_step_next.config(state=tk.DISABLED)
        self._btn_send_agents.config(state=tk.NORMAL)

        # Identifier le vainqueur
        sides_alive = {}
        for c in self.combatants:
            if c.alive:
                sides_alive[c.side] = sides_alive.get(c.side, 0) + 1

        winner = list(sides_alive.keys())[0] if len(sides_alive) == 1 else "Indéterminé"
        self._last_winner = winner

        self._log_write("\n" + "═" * 60 + "\n", "round")
        self._log_write(f"   🏆 COMBAT TERMINÉ — Vainqueur : {winner.upper()}\n", "result")
        self._log_write(f"   Durée : {self._round} round(s)\n", "result")
        self._log_write("═" * 60 + "\n", "round")

        self._step_lbl.config(text=f"✅ Terminé — Vainqueur : {winner}  |  📨 Prêt à envoyer aux agents")
        self._btn_apply_state.config(state=tk.NORMAL)
        self._state_applied = False
        self._build_stats_table(winner)
        self._show_tab(1)  # Aller à l'onglet Statistiques
        # Pas d'envoi automatique — le MJ utilise le bouton 📨 Envoyer aux agents

    # ── Tableau de statistiques ───────────────────────────────────────────────
    def _build_stats_table(self, winner: str):
        for w in self._stats_frame.winfo_children():
            w.destroy()

        tk.Label(self._stats_frame,
                 text=f"🏆 Vainqueur : {winner}   |   Durée : {self._round} round(s)",
                 bg=C["bg"], fg=C["fg_gold"],
                 font=("Consolas", 12, "bold")).pack(pady=(8, 12))

        # Tableau
        tbl = tk.Frame(self._stats_frame, bg=C["bg"])
        tbl.pack(fill=tk.BOTH, expand=True)

        COLS = [
            ("Nom",              180, "w"),
            ("Camp",              70, "center"),
            ("PV final",          80, "center"),
            ("Dégâts infligés",  130, "center"),
            ("Dégâts reçus",     120, "center"),
            ("Touches",           70, "center"),
            ("Ratés",             60, "center"),
            ("% précision",      100, "center"),
            ("Kills",             50, "center"),
            ("Statut",           100, "center"),
        ]

        # En-tête
        hdr = tk.Frame(tbl, bg="#0d1018")
        hdr.pack(fill=tk.X)
        for label, w, anchor in COLS:
            tk.Label(hdr, text=label, bg="#0d1018", fg=C["gold"],
                     font=("Consolas", 8, "bold"),
                     width=w//7, anchor=anchor
                     ).pack(side=tk.LEFT, padx=3, pady=4)

        tk.Frame(tbl, bg=C["border"], height=1).pack(fill=tk.X)

        # Lignes — triées par camp puis dégâts infligés
        sorted_c = sorted(self.combatants,
                           key=lambda x: (x.side != "Héros", -x.stat_dmg_dealt))

        for i, c in enumerate(sorted_c):
            bg = C["pc"] if c.side == "Héros" else C["enemy"]
            if not c.alive:
                bg = "#0e0e12"

            row = tk.Frame(tbl, bg=bg, pady=3)
            row.pack(fill=tk.X, padx=2, pady=1)

            total_atk = c.stat_hits + c.stat_misses
            accuracy  = f"{100 * c.stat_hits / total_atk:.0f}%" if total_atk > 0 else "—"
            status    = "✅ Vivant" if c.alive else f"💀 Round {c.stat_round_down or '?'}"
            status_color = C["green_b"] if c.alive else C["red_bright"]
            hp_str    = f"{c.hp}/{c.max_hp}"
            hp_color  = C["green_b"] if c.alive else C["red_bright"]

            data = [
                (c.name[:22],              c.color,    "w"),
                (c.side[:10],              C["fg_dim"], "center"),
                (hp_str,                   hp_color,    "center"),
                (str(c.stat_dmg_dealt),    C["fg_gold"],"center"),
                (str(c.stat_dmg_taken),    C["fg_dim"], "center"),
                (str(c.stat_hits),         C["green_b"],"center"),
                (str(c.stat_misses),       C["fg_dim"], "center"),
                (accuracy,                 C["fg"],     "center"),
                (str(c.stat_kills),        C["red_bright"] if c.stat_kills else C["fg_dim"], "center"),
                (status,                   status_color,"center"),
            ]

            for (text, color, anchor), (_, w, _) in zip(data, COLS):
                tk.Label(row, text=text, bg=bg, fg=color,
                         font=("Consolas", 9), width=w//7, anchor=anchor
                         ).pack(side=tk.LEFT, padx=3)

            # Barre de PV
            if c.max_hp > 0:
                bar_frame = tk.Frame(tbl, bg=C["border"], height=3)
                bar_frame.pack(fill=tk.X, padx=2)
                bar_w = max(1, int(c.hp / c.max_hp * 100))
                bar_color = (C["green"] if c.hp > c.max_hp * 0.5
                             else C["gold"] if c.hp > c.max_hp * 0.25 else C["red"])
                tk.Frame(bar_frame, bg=bar_color, height=3,
                         width=bar_w).pack(side=tk.LEFT)

        # Résumé par camp
        tk.Frame(self._stats_frame, bg=C["border"], height=1).pack(fill=tk.X, pady=8)

        summary_frame = tk.Frame(self._stats_frame, bg=C["bg"])
        summary_frame.pack(fill=tk.X)

        sides = sorted(set(c.side for c in self.combatants))
        for side in sides:
            group = [c for c in self.combatants if c.side == side]
            alive = sum(1 for c in group if c.alive)
            total_dealt = sum(c.stat_dmg_dealt for c in group)
            total_kills = sum(c.stat_kills for c in group)
            color = C["blue_b"] if side == "Héros" else C["red_bright"]

            tk.Label(summary_frame,
                     text=f"  {side} : {alive}/{len(group)} survivants | "
                          f"{total_dealt} dégâts totaux | {total_kills} kills",
                     bg=C["bg"], fg=color,
                     font=("Consolas", 10, "bold")).pack(anchor="w", padx=8)

    # ── Résumé texte pour le chat ─────────────────────────────────────────────
    def _build_chat_summary(self, winner: str) -> str:
        lines = [
            f"⚡ Simulation terminée en {self._round} round(s). Vainqueur : **{winner}**\n",
            f"{'Nom':<18} {'Camp':<10} {'PV final':<12} {'Dmg infligés':<14} {'Touches':<10} {'Kills':<6} {'Statut'}",
            "─" * 80,
        ]
        for c in sorted(self.combatants, key=lambda x: (x.side != "Héros", -x.stat_dmg_dealt)):
            status = "Vivant" if c.alive else f"KO R.{c.stat_round_down}"
            total  = c.stat_hits + c.stat_misses
            acc    = f"{100*c.stat_hits//total}%" if total else "—"
            lines.append(
                f"{c.name:<18} {c.side:<10} {c.hp}/{c.max_hp:<8} "
                f"{c.stat_dmg_dealt:<14} {c.stat_hits}/{total} ({acc:<5}) "
                f"{c.stat_kills:<6} {status}"
            )
        # ── SORTS MANUELS (ajoutés via picker) ───────────────────────────────────
        if c.extra_spells:
            lines.append("--- SORTS AJOUTÉS MANUELLEMENT ---")
            for sp in c.extra_spells:
                lvl_str = "Tour de magie" if sp.get("level", 0) == 0 else f"Niv {sp['level']}"
                conc = " [Conc.]" if sp.get("concentration") else ""
                lines.append(
                    f"  ✨ {sp['name']} ({lvl_str} — {sp.get('school','?')}){conc}"
                    f"  {sp.get('cast_time','?')} | {sp.get('range','?')} | {sp.get('duration','?')}"
                )
                desc = sp.get("description", "")
                if desc:
                    lines.append(f"     {desc[:350]}")

        return "\n".join(lines)

    # ── Lookup niveau de sort depuis campaign_state ────────────────────────────
    def _get_spell_level(self, char_name: str, spell_name: str) -> int | None:
        """Retourne le niveau du sort depuis la liste de sorts du personnage, ou None si introuvable/cantrip."""
        if not self._load_state:
            return None
        try:
            state = self._load_state()
            spells = state.get("characters", {}).get(char_name, {}).get("spells", [])
            spell_lower = spell_name.lower().strip()
            for sp in spells:
                if sp.get("name", "").lower().strip() == spell_lower:
                    lvl = sp.get("level", 0)
                    return lvl if lvl > 0 else None  # 0 = tour de magie, pas de slot
            # Correspondance partielle (le LLM peut utiliser le nom en anglais ou abrégé)
            for sp in spells:
                if spell_lower in sp.get("name", "").lower() or sp.get("name", "").lower() in spell_lower:
                    lvl = sp.get("level", 0)
                    return lvl if lvl > 0 else None
        except Exception as e:
            print(f"[Sim._get_spell_level] Erreur : {e}")
        return None

    # ── Application des effets à campaign_state.json ──────────────────────────
    def _apply_to_state(self) -> list[str]:
        """
        Applique les résultats de la simulation à campaign_state.json :
          - Dégâts aux héros via update_hp()
          - Slots de sorts consommés via use_spell_slot() (sim LLM uniquement)
        Retourne la liste des changements appliqués (strings).
        """
        if self._state_applied:
            return ["⚠️ Déjà appliqué — réinitialisez pour recommencer."]
        if not self._load_state:
            return ["⚠️ Aucun state_loader fourni — PV et sorts non appliqués."]

        from state_manager import update_hp, use_spell_slot

        _HERO_NAMES = {"Kaelen", "Elara", "Thorne", "Lyra"}
        applied = []

        # ── 1. PV : dégâts reçus par chaque héros ────────────────────────────
        for c in self.combatants:
            if c.side != "Héros" or c.name not in _HERO_NAMES:
                continue
            dmg = c.stat_dmg_taken
            if dmg > 0:
                update_hp(c.name, -dmg)
                try:
                    state_after = self._load_state()
                    hp_now  = state_after["characters"][c.name]["hp"]
                    max_hp  = state_after["characters"][c.name]["max_hp"]
                    applied.append(f"💥 {c.name:<10} -{dmg:>4} PV  →  {hp_now}/{max_hp}")
                except Exception:
                    applied.append(f"💥 {c.name} : -{dmg} PV appliqués")
            else:
                applied.append(f"✅ {c.name:<10} aucun dégât")

        # ── 2. Slots de sorts (sim LLM uniquement) ───────────────────────────
        if self._spells_used:
            applied.append("─── Sorts consommés ───")
            for char_name, slots in sorted(self._spells_used.items()):
                for level_str, count in sorted(slots.items(), key=lambda x: int(x[0])):
                    for _ in range(count):
                        result = use_spell_slot(char_name, level_str)
                        ok = "Succès" in result or "utilisé" in result
                        icon = "✨" if ok else "⚠️"
                        suffix = "" if ok else " (slot déjà vide)"
                        applied.append(f"  {icon} {char_name} : slot niv.{level_str}{suffix}")
        else:
            applied.append("ℹ️ Aucun sort tracé (sim dés = pas de suivi automatique)")

        self._state_applied = True
        return applied

    # ── Bouton standalone "Appliquer aux héros" ────────────────────────────────
    def _apply_to_state_ui(self):
        """Applique les effets à la campagne et affiche le résultat dans le log."""
        changes = self._apply_to_state()
        header = "═" * 55 + "\n   ⚙️ APPLIQUÉ À LA CAMPAGNE\n" + "═" * 55 + "\n"
        self._log_write("\n" + header, "round")
        for line in changes:
            self._log_write(f"  {line}\n", "system")
        self._log_write("\n", "")
        self._btn_apply_state.config(state=tk.DISABLED)
        self._step_lbl.config(text="✅ PV et sorts appliqués à campaign_state.json")
        # Notifier dans le chat principal si disponible
        if self.chat_queue:
            self.chat_queue.put({
                "sender": "⚙️ Simulation",
                "text": "Effets du combat simulé appliqués à la campagne :\n" + "\n".join(changes),
                "color": "#c07000",
            })

    # ── Envoi vers les agents (avec application automatique si pas encore fait) ─
    def _send_to_agents(self):
        """Injecte le résumé de la simulation dans le groupchat autogen."""
        if not self._inject_to_agents:
            self._step_lbl.config(text="⚠️ Partie non démarrée — agents non disponibles.")
            return

        # Applique automatiquement si pas encore fait
        if not self._state_applied:
            changes = self._apply_to_state()
            self._btn_apply_state.config(state=tk.DISABLED)
            changes_block = "\n⚙️ Effets appliqués à la campagne :\n" + "\n".join(f"  {l}" for l in changes)
        else:
            changes_block = "\n(PV et sorts déjà appliqués via bouton ⚙️)"

        summary = self._build_chat_summary(self._last_winner)
        self._inject_to_agents(f"[RÉSULTAT SIMULATION COMBAT]\n{summary}{changes_block}")
        self._btn_send_agents.config(state=tk.DISABLED)
        self._step_lbl.config(text="✅ Résultat envoyé aux agents + effets appliqués à la campagne.")

        if self.chat_queue and not self._state_applied:
            # _state_applied est mis à True dans _apply_to_state — on notifie dans le chat
            self.chat_queue.put({
                "sender": "⚙️ Simulation",
                "text": "Effets du combat simulé appliqués automatiquement à la campagne.\n" + changes_block,
                "color": "#c07000",
            })

    # ── Copier le log ─────────────────────────────────────────────────────────
    def _copy_log(self):
        text = "".join(self._sim_log)
        self.win.clipboard_clear()
        self.win.clipboard_append(text)
        self._step_lbl.config(text="📋 Log copié dans le presse-papier !")
        self.win.after(2000, lambda: self._step_lbl.config(text=""))


# ─── Point d'entrée standalone (test) ────────────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    sim = CombatSimulator(root)
    root.mainloop()