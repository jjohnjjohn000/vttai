"""
combat_tracker.py
─────────────────
Fenêtre de gestion de combat D&D 5e pour le Moteur de l'Aube Brisée.
Ouverte depuis le bouton ⚔️ Combat dans main.py.

Fonctionnalités :
  • Ordre d'initiative (d20 + bonus, tri auto)
  • PJ importés automatiquement depuis state_manager
  • PNJ ajoutables à la volée
  • Suivi PV / PV max avec barre de vie colorée
  • Classe d'armure
  • Économie d'actions (Action · Bonus · Réaction · Mouvement)
  • Concentration
  • 15 Conditions D&D 5e avec tooltips
  • Throws de mort (D&D 5e) pour PJ à 0 PV
  • Compteur de round
  • Injection automatique du résumé de combat dans la queue de chat
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import random
import json

# ─── État de combat partagé avec main.py ──────────────────────────────────────
# Mis à jour à chaque changement de tour ; lu par run_autogen pour contraindre
# les agents hors-tour.
COMBAT_STATE: dict = {
    "active":            False,   # combat en cours ?
    "active_combatant":  None,    # nom du combatant dont c'est le tour (str|None)
    "round_num":         0,
    "spoken_off_turn":   set(),   # noms des agents PJ qui ont déjà réagi ce round hors-tour
}


def get_combat_prompt(agent_name: str) -> str:
    """
    Retourne le bloc de règles de combat à injecter dans le system_message
    de l'agent selon l'état courant du combat.
    Appelé depuis main.py à chaque changement de tour.
    """
    cs = COMBAT_STATE
    if not cs["active"]:
        return ""

    active = cs["active_combatant"] or "?"
    rnd    = cs["round_num"]

    if agent_name == active:
        return (
            f"\n\n⚔️ ═══ COMBAT — ROUND {rnd} — C'EST TON TOUR ═══\n"
            "Tu peux agir PLEINEMENT ce tour :\n"
            "✅ Décrire ton attaque ou sort (puis attendre les jets du MJ)\n"
            "✅ Te déplacer, te repositionner\n"
            "✅ Interagir avec l'environnement\n"
            "✅ Parler, crier, donner des ordres\n"
            "Joue avec intensité et concision."
        )
    elif agent_name in cs["spoken_off_turn"]:
        return (
            f"\n\n⚔️ ═══ COMBAT — ROUND {rnd} — HORS-TOUR — INTERVENTION UTILISÉE ═══\n"
            f"C'est le tour de {active}. Tu as déjà parlé ou réagi ce round.\n"
            "🚫 TU NE PEUX PLUS RIEN FAIRE jusqu'à ton prochain tour.\n"
            "🚫 Interdit : attaquer, lancer un sort, te déplacer, parler, commenter, décrire une action.\n"
            "✅ Seule réponse autorisée : le mot-clé exact [SILENCE] — rien d'autre."
        )
    else:
        return (
            f"\n\n⚔️ ═══ COMBAT — ROUND {rnd} — HORS-TOUR ═══\n"
            f"C'est le tour de {active}. Ce n'est PAS ton tour.\n"
            "\n"
            "✅ AUTORISÉ une seule fois ce round (au choix) :\n"
            "  • Une réaction D&D 5e déclenchée par un événement précis\n"
            "    (attaque d'opportunité, sort Bouclier, Riposte, Pas de côté…)\n"
            "  • OU une seule phrase courte parlée à un allié\n"
            "    (avertissement, encouragement, coordination tactique — max 10 mots)\n"
            "\n"
            "🚫 INTERDIT hors-tour, sans exception :\n"
            "  • Se déplacer ou se repositionner\n"
            "  • Attaquer (sauf réaction explicite)\n"
            "  • Lancer un sort (sauf sort de réaction comme Bouclier)\n"
            "  • Utiliser un objet, une compétence, une action bonus\n"
            "  • Décrire une action physique quelconque\n"
            "  • Commenter l'action en cours ou donner des conseils stratégiques\n"
            "\n"
            "Après cette unique intervention, réponds [SILENCE] jusqu'à ton prochain tour."
        )

# ─── Palette ──────────────────────────────────────────────────────────────────
C = {
    "bg":          "#0b0d12",
    "panel":       "#111520",
    "row_pc":      "#0d1a2a",
    "row_npc":     "#1a100d",
    "row_active":  "#1a2200",
    "entry_bg":    "#222535",   # fond des champs de saisie (contraste visible)
    "border":      "#2a3040",
    "border_hot":  "#c8a820",
    "gold":        "#c8a820",
    "red":         "#c0392b",
    "red_bright":  "#e74c3c",
    "green":       "#27ae60",
    "green_bright":"#2ecc71",
    "blue":        "#2980b9",
    "blue_bright": "#3498db",
    "purple":      "#8e44ad",
    "orange":      "#e67e22",
    "fg":          "#dde0e8",
    "fg_dim":      "#b0bfcc",
    "fg_gold":     "#f0d060",
    "skull":       "#e74c3c",
    "conc":        "#9b59b6",
    "hp_high":     "#27ae60",
    "hp_mid":      "#e67e22",
    "hp_low":      "#e74c3c",
}

# ─── Conditions D&D 5e ────────────────────────────────────────────────────────
CONDITIONS = {
    "Aveuglé":      {"abbr": "AV", "color": "#607080", "tip": "Échoue auto. tests Perception visuelle. Attaques en désavantage. Adversaires en avantage."},
    "Charmé":       {"abbr": "CH", "color": "#d070d0", "tip": "Ne peut pas attaquer ou affecter négativement la source du charme. Avantage aux tests de charisme de la source."},
    "Sourd":        {"abbr": "SO", "color": "#808070", "tip": "Échoue auto. tout test nécessitant l'ouïe."},
    "Épuisé":       {"abbr": "EP", "color": "#a07030", "tip": "Malus cumulatifs de niveau 1–6 (voir table D&D 5e)."},
    "Effrayé":      {"abbr": "EF", "color": "#8050a0", "tip": "Désavantage aux jets d'attaque et tests si source visible. Ne peut s'approcher volontairement."},
    "Agrippé":      {"abbr": "AG", "color": "#806040", "tip": "Vitesse = 0. Fin si la cible s'éloigne de la portée ou est déplacée hors de portée."},
    "Incapacité":   {"abbr": "IN", "color": "#505080", "tip": "Ne peut effectuer aucune action ni réaction."},
    "Invisible":    {"abbr": "IV", "color": "#40d0d0", "tip": "Quasi impossible à localiser. Attaques en avantage. Adversaires en désavantage."},
    "Paralysé":     {"abbr": "PA", "color": "#c0b000", "tip": "Incapacité. Échoue STR et DEX. Jets d'attaque auto-critique à ≤5 ft."},
    "Pétrifié":     {"abbr": "PF", "color": "#909090", "tip": "Transformé en statue. Incapacité, résistance tous dégâts, immunité poison/maladie."},
    "Empoisonné":   {"abbr": "EM", "color": "#60a830", "tip": "Désavantage aux jets d'attaque et tests de caractéristiques."},
    "À terre":      {"abbr": "AT", "color": "#806030", "tip": "Mouvement uniquement en rampant. Attaques en désavantage. Adj. en avantage. Non-adj. en désavantage."},
    "Entravé":      {"abbr": "EN", "color": "#b06020", "tip": "Vitesse = 0. Jets d'attaque en désavantage. Adversaires en avantage."},
    "Étourdi":      {"abbr": "ÉT", "color": "#c08000", "tip": "Incapacité. Échoue STR et DEX. Adversaires en avantage."},
    "Inconscient":  {"abbr": "IC", "color": "#e04030", "tip": "Incapacité, tombe à terre. Échoue STR et DEX. Adj. en avantage (critique auto.)."},
}

# ─── Données personnages joueurs (depuis state_manager) ───────────────────────
PC_COLORS = {
    "Kaelen": "#a0c4ff",
    "Elara":  "#c8b8ff",
    "Thorne": "#ff9999",
    "Lyra":   "#a8f0a8",
}

PC_DEX_BONUS = {   # bonus d'initiative par défaut (modif DEX estimé)
    "Kaelen": 2,
    "Elara":  3,
    "Thorne": 6,   # voleur
    "Lyra":   1,
}


# ─── Combatant ────────────────────────────────────────────────────────────────
class Combatant:
    """Représentation d'un participant au combat."""

    _id_counter = 0

    def __init__(self, name: str, is_pc: bool,
                 max_hp: int = 20, current_hp: int = None,
                 ac: int = 10, initiative: int = 0,
                 dex_bonus: int = 0, color: str = "#e0e0e0",
                 concentration: bool = False):
        Combatant._id_counter += 1
        self.uid        = Combatant._id_counter
        self.name       = name
        self.is_pc      = is_pc
        self.max_hp     = max_hp
        self.hp         = current_hp if current_hp is not None else max_hp
        self.ac         = ac
        self.initiative = initiative
        self.dex_bonus  = dex_bonus
        self.color      = color
        self.concentration = concentration

        # Économie d'action
        self.action_used  = False
        self.bonus_used   = False
        self.reaction_used= False
        self.move_used    = 0        # pieds dépensés

        # Conditions actives {nom: True}
        self.conditions: dict = {}

        # Jets de mort (D&D 5e)
        self.death_saves_success = 0
        self.death_saves_fail    = 0

        # Notes libres
        self.notes = ""

    @property
    def is_down(self) -> bool:
        return self.hp <= 0

    @property
    def is_dead(self) -> bool:
        return self.death_saves_fail >= 3

    @property
    def is_stabilized(self) -> bool:
        return self.death_saves_success >= 3

    def hp_pct(self) -> float:
        if self.max_hp <= 0:
            return 0.0
        return max(0.0, min(1.0, self.hp / self.max_hp))

    def hp_color(self) -> str:
        p = self.hp_pct()
        if p > 0.50:
            return C["hp_high"]
        elif p > 0.25:
            return C["hp_mid"]
        else:
            return C["hp_low"]

    def reset_turn_resources(self):
        self.action_used   = False
        self.bonus_used    = False
        self.reaction_used = False
        self.move_used     = 0

    def roll_initiative(self):
        roll = random.randint(1, 20)
        self.initiative = roll + self.dex_bonus
        return roll

    def to_dict(self) -> dict:
        return {
            "name": self.name, "is_pc": self.is_pc,
            "max_hp": self.max_hp, "hp": self.hp,
            "ac": self.ac, "initiative": self.initiative,
            "conditions": list(self.conditions.keys()),
            "notes": self.notes,
        }


# ─── Fenêtre principale du tracker ───────────────────────────────────────────
class CombatTracker:
    """Fenêtre Toplevel de gestion de combat D&D 5e."""

    def __init__(self, root: tk.Tk, state_loader,
                 chat_queue=None):
        """
        root         : tk.Tk principal
        state_loader : callable → dict (load_state de state_manager)
        chat_queue   : queue.Queue pour injecter des messages dans le chat
        """
        self.root        = root
        self._load_state = state_loader
        self.chat_queue  = chat_queue
        self.combatants: list[Combatant] = []
        self.current_idx = -1
        self.round_num   = 0
        self.combat_active = False
        self._rows: dict = {}   # uid → frame widgets

        self._build_window()
        self._import_pcs()

    # ── Construction de la fenêtre ────────────────────────────────────────────
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
        COL_WIDTHS = [
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
                self._inner.after(400, _poll_ct_scroll)
            except Exception:
                pass
        self._inner.after(200, _poll_ct_scroll)

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

        bot = tk.Frame(self.win, bg="#0d1018", height=88)
        bot.pack(fill=tk.X)
        bot.pack_propagate(False)

        # ── Ajouter un PNJ ─────────────────────────────────────────────────
        add_frame = tk.Frame(bot, bg="#0d1018")
        add_frame.pack(side=tk.LEFT, padx=16, pady=10)

        tk.Label(add_frame, text="AJOUTER UN COMBATANT",
                 bg="#0d1018", fg=C["fg_dim"],
                 font=("Consolas", 8, "bold")).grid(row=0, columnspan=7, sticky="w", pady=(0, 4))

        def lbl(text):
            return tk.Label(add_frame, text=text, bg="#0d1018",
                            fg=C["fg_dim"], font=("Consolas", 8))

        def ent(w, default=""):
            e = tk.Entry(add_frame, bg=C["entry_bg"], fg=C["fg"],
                         font=("Consolas", 10), insertbackground=C["fg"],
                         relief="flat", width=w)
            e.insert(0, default)
            return e

        lbl("Nom").grid(row=1, column=0, padx=(0,2), sticky="w")
        self._npc_name = ent(12, "Gobelin")
        self._npc_name.grid(row=2, column=0, padx=(0,4), ipady=3)

        lbl("PV max").grid(row=1, column=1, padx=(0,2), sticky="w")
        self._npc_hp = ent(5, "15")
        self._npc_hp.grid(row=2, column=1, padx=(0,4), ipady=3)

        lbl("CA").grid(row=1, column=2, padx=(0,2), sticky="w")
        self._npc_ac = ent(4, "13")
        self._npc_ac.grid(row=2, column=2, padx=(0,4), ipady=3)

        lbl("Init. bonus").grid(row=1, column=3, padx=(0,2), sticky="w")
        self._npc_dex = ent(4, "1")
        self._npc_dex.grid(row=2, column=3, padx=(0,4), ipady=3)

        lbl("Init. fixe").grid(row=1, column=4, padx=(0,2), sticky="w")
        self._npc_init_fixed = ent(4, "")
        self._npc_init_fixed.grid(row=2, column=4, padx=(0,4), ipady=3)

        lbl("Quantité").grid(row=1, column=5, padx=(0,2), sticky="w")
        self._npc_qty = ent(3, "1")
        self._npc_qty.grid(row=2, column=5, padx=(0,4), ipady=3)

        tk.Button(add_frame, text="＋ Ajouter",
                  bg=_darken(C["blue"], 0.4), fg=C["blue_bright"],
                  font=("Consolas", 9, "bold"), relief="flat",
                  padx=8, pady=3, cursor="hand2",
                  command=self._add_npc).grid(row=2, column=6, padx=(4, 0))

        # ── Infos combat ───────────────────────────────────────────────────
        info_frame = tk.Frame(bot, bg="#0d1018")
        info_frame.pack(side=tk.RIGHT, padx=20, pady=10)

        self._info_var = tk.StringVar(value="Aucun combat en cours.")
        tk.Label(info_frame, textvariable=self._info_var,
                 bg="#0d1018", fg=C["fg_dim"],
                 font=("Consolas", 9), justify=tk.LEFT).pack(anchor="e")

        # Bouton tri manuel
        tk.Button(info_frame, text="⇅ Retrier par initiative",
                  bg=_darken(C["purple"], 0.4), fg="#c070e0",
                  font=("Consolas", 8), relief="flat", padx=6,
                  command=self._sort_and_refresh).pack(anchor="e", pady=(4, 0))

    # ── Import PJ depuis state_manager ────────────────────────────────────────
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

    # ── Refresh complet de la liste ───────────────────────────────────────────
    def _refresh_list(self):
        for w in self._inner.winfo_children():
            w.destroy()
        self._rows.clear()

        for idx, c in enumerate(self.combatants):
            is_active = (self.combat_active and idx == self.current_idx)
            self._build_row(c, idx, is_active)

        self._canvas.update_idletasks()
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _build_row(self, c: Combatant, idx: int, active: bool):
        if c.is_pc:
            row_bg = C["row_active"] if active else C["row_pc"]
        else:
            row_bg = _lighten(C["row_active"], 0.15) if active else C["row_npc"]

        border_color = C["border_hot"] if active else C["border"]

        row = tk.Frame(self._inner, bg=row_bg,
                       highlightbackground=border_color,
                       highlightthickness=2 if active else 1)
        row.pack(fill=tk.X, padx=4, pady=2)

        # ── Col 1 : Initiative ─────────────────────────────────────────────
        init_f = tk.Frame(row, bg=row_bg, width=56)
        init_f.pack(side=tk.LEFT, padx=(6, 2), pady=4)
        init_f.pack_propagate(False)

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

        tk.Button(init_f, text="🎲", bg=row_bg, fg="#c8a820",
                  font=("Arial", 8), bd=0, relief="flat", cursor="hand2",
                  command=lambda cb=c: self._roll_one_initiative(cb)
                  ).pack()

        # ── Col 2 : Nom + badge ────────────────────────────────────────────
        name_f = tk.Frame(row, bg=row_bg, width=158)
        name_f.pack(side=tk.LEFT, padx=4, pady=4)
        name_f.pack_propagate(False)

        badge = "PJ" if c.is_pc else "PNJ"
        badge_bg = _darken(c.color, 0.50) if c.is_pc else "#5a2a10"
        badge_fg = "white"

        tk.Label(name_f, text=badge, bg=badge_bg, fg=badge_fg,
                 font=("Consolas", 7, "bold"), padx=4, pady=1
                 ).pack(anchor="w")

        skull = " 💀" if c.is_dead else (" 🩸" if c.is_down else "")
        star  = " ⭐" if active else ""
        tk.Label(name_f, text=c.name + skull + star, bg=row_bg,
                 fg=C["fg_gold"] if active else c.color,
                 font=("Consolas", 10, "bold"), anchor="w"
                 ).pack(anchor="w")

        # Bouton supprimer
        tk.Button(name_f, text="✕", bg=row_bg, fg="#cc5555",
                  font=("Arial", 7), bd=0, relief="flat",
                  cursor="hand2",
                  command=lambda cb=c: self._remove_combatant(cb)
                  ).pack(anchor="w")

        # ── Col 3 : PV ────────────────────────────────────────────────────
        hp_f = tk.Frame(row, bg=row_bg, width=162)
        hp_f.pack(side=tk.LEFT, padx=4, pady=4)
        hp_f.pack_propagate(False)

        hp_lbl = tk.Label(hp_f,
                          text=f"{max(0,c.hp)} / {c.max_hp}",
                          bg=row_bg, fg=c.hp_color(),
                          font=("Consolas", 10, "bold"))
        hp_lbl.pack(anchor="w")

        # Barre de vie
        bar_canvas = tk.Canvas(hp_f, height=6, bg="#1a1a1a",
                               highlightthickness=0)
        bar_canvas.pack(fill=tk.X, pady=(1, 3))

        def draw_hp_bar(canvas=bar_canvas, cb=c):
            canvas.update_idletasks()
            w = canvas.winfo_width()
            if w < 4:
                w = 140
            canvas.delete("all")
            filled = int(w * cb.hp_pct())
            canvas.create_rectangle(0, 0, w, 6, fill="#1a1a1a", outline="")
            if filled > 0:
                canvas.create_rectangle(0, 0, filled, 6,
                                        fill=cb.hp_color(), outline="")

        # Ce <Configure> est sur un Canvas directement (pas sur une Frame enfant de Canvas)
        # → moins risqué, mais on protège quand même avec un try/except.
        bar_canvas.bind("<Configure>",
                        lambda e, cb=c, canvas=bar_canvas: (
                            draw_hp_bar(canvas, cb) if canvas.winfo_exists() else None
                        ))

        # Boutons +/-/saisie
        hp_btn_f = tk.Frame(hp_f, bg=row_bg)
        hp_btn_f.pack(anchor="w")

        dmg_var = tk.StringVar(value="")
        hp_entry = tk.Entry(hp_btn_f, textvariable=dmg_var, width=5,
                            bg=C["entry_bg"], fg=C["fg"], font=("Consolas", 9),
                            insertbackground=C["fg"], relief="flat",
                            justify="center")
        hp_entry.pack(side=tk.LEFT, ipady=2, padx=(0, 2))

        def apply_dmg(sign, cb=c, var=dmg_var,
                      lbl=hp_lbl, canvas=bar_canvas):
            try:
                val = int(var.get()) if var.get().strip() else 0
            except ValueError:
                val = 0
            cb.hp = max(0, min(cb.max_hp, cb.hp + sign * val))
            lbl.config(text=f"{max(0,cb.hp)} / {cb.max_hp}",
                       fg=cb.hp_color())
            draw_hp_bar(canvas, cb)
            var.set("")
            # Re-render le nom (skull update)
            self._refresh_list()
            # Death saves si PJ à 0
            if cb.is_pc and cb.hp == 0:
                self._open_death_saves(cb)

        tk.Button(hp_btn_f, text="❤ Soin",
                  bg=_darken(C["green"], 0.35), fg=C["green_bright"],
                  font=("Consolas", 7, "bold"), relief="flat", padx=3,
                  cursor="hand2",
                  command=lambda cb=c, v=dmg_var,
                  l=hp_lbl, canvas=bar_canvas: apply_dmg(+1, cb, v, l, canvas)
                  ).pack(side=tk.LEFT)
        tk.Button(hp_btn_f, text="💥 Dégât",
                  bg=_darken(C["red"], 0.35), fg=C["red_bright"],
                  font=("Consolas", 7, "bold"), relief="flat", padx=3,
                  cursor="hand2",
                  command=lambda cb=c, v=dmg_var,
                  l=hp_lbl, canvas=bar_canvas: apply_dmg(-1, cb, v, l, canvas)
                  ).pack(side=tk.LEFT, padx=(2, 0))

        # Death saves si down
        if c.is_pc and c.is_down:
            self._mini_death_saves(hp_f, c)

        # ── Col 4 : CA ────────────────────────────────────────────────────
        ac_f = tk.Frame(row, bg=row_bg, width=52)
        ac_f.pack(side=tk.LEFT, padx=4, pady=4)
        ac_f.pack_propagate(False)

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

        # ── Col 5 : Conditions ────────────────────────────────────────────
        cond_f = tk.Frame(row, bg=row_bg, width=220)
        cond_f.pack(side=tk.LEFT, padx=4, pady=4)
        cond_f.pack_propagate(False)

        self._build_conditions_widget(cond_f, c, row_bg)

        # ── Col 6 : Actions ───────────────────────────────────────────────
        act_f = tk.Frame(row, bg=row_bg, width=162)
        act_f.pack(side=tk.LEFT, padx=4, pady=4)
        act_f.pack_propagate(False)

        self._build_action_economy(act_f, c, row_bg, active)

        # ── Col 7 : Concentration ─────────────────────────────────────────
        conc_f = tk.Frame(row, bg=row_bg, width=58)
        conc_f.pack(side=tk.LEFT, padx=4, pady=4)
        conc_f.pack_propagate(False)

        conc_var = tk.BooleanVar(value=c.concentration)
        conc_cb  = tk.Checkbutton(conc_f, variable=conc_var,
                                  text="♦", bg=row_bg,
                                  fg=C["conc"] if c.concentration else C["fg_dim"],
                                  activebackground=row_bg,
                                  selectcolor=_darken(C["conc"], 0.3),
                                  font=("Arial", 12), bd=0)
        conc_cb.pack()
        tk.Label(conc_f, text="Conc.", bg=row_bg, fg=C["fg_dim"],
                 font=("Consolas", 7)).pack()

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

        # Bouton jet de mort rapide pour les PNJ tombés
        if not c.is_pc and c.is_down:
            tk.Label(row, text="💀 KO", bg=row_bg, fg=C["skull"],
                     font=("Consolas", 9, "bold")).pack(side=tk.RIGHT, padx=6)

    def _build_conditions_widget(self, parent, c: Combatant, row_bg: str):
        """Grille compacte de badges de conditions cliquables."""
        outer = tk.Frame(parent, bg=row_bg)
        outer.pack(fill=tk.BOTH, expand=True)

        # 2 lignes de badges
        row1 = tk.Frame(outer, bg=row_bg)
        row1.pack(fill=tk.X)
        row2 = tk.Frame(outer, bg=row_bg)
        row2.pack(fill=tk.X)

        cond_names = list(CONDITIONS.keys())

        for i, cname in enumerate(cond_names):
            cdata  = CONDITIONS[cname]
            active = cname in c.conditions
            frame  = row1 if i < 8 else row2

            # Inactif : fond légèrement teinté + texte lisible
            btn_bg  = cdata["color"]  if active else _darken(cdata["color"], 0.55)
            btn_fg  = "white"         if active else "#cccccc"

            btn = tk.Button(frame, text=cdata["abbr"],
                            bg=btn_bg, fg=btn_fg,
                            font=("Consolas", 7, "bold"),
                            relief="flat", padx=3, pady=1,
                            cursor="hand2")
            btn.pack(side=tk.LEFT, padx=1, pady=1)

            # Tooltip
            self._tooltip(btn, f"{cname}\n{cdata['tip']}")

            def _toggle(cb=c, cn=cname, b=btn, cd=cdata):
                if cn in cb.conditions:
                    del cb.conditions[cn]
                    b.config(bg=_darken(cd["color"], 0.55), fg="#cccccc")
                else:
                    cb.conditions[cn] = True
                    b.config(bg=cd["color"], fg="white")

            btn.config(command=_toggle)

    def _build_action_economy(self, parent, c: Combatant,
                               row_bg: str, active: bool):
        """Cases à cocher pour Action / Bonus / Réaction + mouvement."""
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
        check_row(r1, "✦ Action",       C["gold"],         "action_used")
        check_row(r1, "◈ Bonus",        "#d06800",         "bonus_used")

        r2 = tk.Frame(inner, bg=row_bg)
        r2.pack(fill=tk.X)
        check_row(r2, "↺ Réaction",     C["blue_bright"],  "reaction_used")

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

        # Bouton réinitialiser tour
        if active:
            tk.Button(inner, text="↺ Réinit. actions",
                      bg=_darken(C["gold"], 0.3), fg=C["gold"],
                      font=("Consolas", 7, "bold"), relief="flat",
                      padx=4, cursor="hand2",
                      command=lambda cb=c: (cb.reset_turn_resources(),
                                            self._refresh_list())
                      ).pack(anchor="w", pady=(2, 0))

    def _mini_death_saves(self, parent, c: Combatant):
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

    # ── Initiative ────────────────────────────────────────────────────────────
    def _roll_all_initiative(self):
        results = []
        for c in self.combatants:
            roll = c.roll_initiative()
            results.append(f"  {c.name}: {roll} + {c.dex_bonus} = {c.initiative}")
        self._sort_and_refresh()
        self._log("🎲 JETS D'INITIATIVE :\n" + "\n".join(results))

    def _roll_one_initiative(self, c: Combatant):
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
        COMBAT_STATE["spoken_off_turn"]  = set()
        active_c = self.combatants[0] if self.combatants else None
        COMBAT_STATE["active_combatant"] = active_c.name if active_c else None

        self._btn_start.config(state=tk.DISABLED)
        self._btn_next.config( state=tk.NORMAL)
        self._btn_end.config(  state=tk.NORMAL)

        self._update_round_label()
        self._refresh_list()
        self._log_turn()

    def _next_turn(self):
        if not self.combat_active:
            return

        # Réinitialise les actions du combatant actif
        if 0 <= self.current_idx < len(self.combatants):
            self.combatants[self.current_idx].reset_turn_resources()

        # Avance
        self.current_idx += 1
        if self.current_idx >= len(self.combatants):
            self.current_idx = 0
            self.round_num  += 1
            self._log(f"\n══ Round {self.round_num} ══")
            COMBAT_STATE["spoken_off_turn"] = set()   # reset réactions au nouveau round

        # ── Mise à jour état partagé ──
        COMBAT_STATE["round_num"] = self.round_num
        active_c = self.combatants[self.current_idx] if self.combatants else None
        COMBAT_STATE["active_combatant"] = active_c.name if active_c else None

        self._update_round_label()
        self._refresh_list()
        self._log_turn()

        # Auto-scroll vers le combatant actif
        try:
            self._canvas.yview_moveto(
                max(0, self.current_idx / max(1, len(self.combatants)) - 0.1)
            )
        except Exception:
            pass

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
        COMBAT_STATE["spoken_off_turn"]  = set()

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
        msg = (f"⚡ Tour de {c.name}  "
               f"(Init {c.initiative} | PV {c.hp}/{c.max_hp} | CA {c.ac})\n"
               f"   Conditions : {conds}")
        self._log(msg)
        if self.chat_queue:
            self.chat_queue.put({
                "sender": "⚔️ Combat",
                "text":   msg,
                "color":  "#e67e22"
            })

    def _build_summary(self) -> str:
        lines = [f"Durée : {self.round_num} round(s)"]
        down  = [c for c in self.combatants if c.is_down]
        dead  = [c for c in self.combatants if c.is_dead]
        if dead:
            lines.append("Morts : " + ", ".join(c.name for c in dead))
        if down:
            lines.append("KO    : " + ", ".join(c.name for c in down))
        return "  |  ".join(lines)

    # ── PNJ ───────────────────────────────────────────────────────────────────
    def _add_npc(self):
        try:
            name    = self._npc_name.get().strip() or "Ennemi"
            max_hp  = int(self._npc_hp.get()  or 15)
            ac      = int(self._npc_ac.get()   or 13)
            dex_b   = int(self._npc_dex.get()  or 1)
            qty     = max(1, int(self._npc_qty.get() or 1))
            fixed   = self._npc_init_fixed.get().strip()
        except ValueError:
            messagebox.showwarning("Ajout PNJ", "Vérifiez les valeurs numériques.")
            return

        NPC_COLORS = ["#ff9966","#ffcc66","#99ddff","#cc99ff",
                      "#99ffcc","#ff99bb","#ddbbff","#aaffaa"]

        for i in range(qty):
            n    = f"{name} {i+1}" if qty > 1 else name
            init = int(fixed) if fixed.lstrip("-").isdigit() else 0
            col  = NPC_COLORS[(len(self.combatants)) % len(NPC_COLORS)]
            c    = Combatant(name=n, is_pc=False,
                             max_hp=max_hp, ac=ac,
                             initiative=init, dex_bonus=dex_b,
                             color=col)
            if not fixed.lstrip("-").isdigit():
                c.roll_initiative()
            self.combatants.append(c)

        self._sort_and_refresh()
        self._log(f"➕ {qty}× {name} ajouté(s) au combat.")

    def _remove_combatant(self, c: Combatant):
        if c in self.combatants:
            idx = self.combatants.index(c)
            self.combatants.remove(c)
            if self.combat_active and self.current_idx >= idx:
                self.current_idx = max(0, self.current_idx - 1)
            self._refresh_list()

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
                  command=lambda: [
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
                                        "Un combat est en cours. Fermer quand même ?"):
                return
        self.win.destroy()


# ─── Helpers couleur ──────────────────────────────────────────────────────────
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