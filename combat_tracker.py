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
import threading

# ─── Intégration bestiary (optionnelle) ───────────────────────────────────────
try:
    from npc_bestiary_panel import (
        search_monsters  as _bestiary_search,
        get_monster      as _bestiary_get,
        _load_bestiary   as _bestiary_load,
        MonsterSheetWindow,
    )
    _BESTIARY_OK = True
except ImportError:
    _BESTIARY_OK = False

# ─── État de combat partagé avec main.py ──────────────────────────────────────
# Mis à jour à chaque changement de tour ; lu par run_autogen pour contraindre
# les agents hors-tour.
COMBAT_STATE: dict = {
    "active":            False,   # combat en cours ?
    "active_combatant":  None,    # nom du combatant dont c'est le tour (str|None)
    "round_num":         0,
    # Deux ressources hors-tour indépendantes, réinitialisées à chaque round :
    "reactions_used":    set(),   # PJ ayant utilisé leur réaction D&D 5e ce round
    "speech_used":       set(),   # PJ ayant utilisé leur parole hors-tour ce round
}


def _is_fully_silenced(agent_name: str) -> bool:
    """Retourne True si l'agent a épuisé ses DEUX ressources hors-tour ce round."""
    return (agent_name in COMBAT_STATE["reactions_used"]
            and agent_name in COMBAT_STATE["speech_used"])


def get_combat_prompt(agent_name: str) -> str:
    """
    Retourne le bloc de règles de combat à injecter dans le system_message
    de l'agent selon l'état courant du combat.
    Appelé depuis main.py à chaque changement de tour.

    Deux ressources hors-tour INDÉPENDANTES par round :
      • Réaction   — déclenchée mécaniquement (Attaque d'opportunité, Bouclier…)
      • Parole     — une phrase courte si l'information est VRAIMENT importante
    Chacune ne vaut que si elle apporte une information cruciale ou répond
    à une question directe. Le bavardage tactique est interdit.
    """
    cs = COMBAT_STATE
    if not cs["active"]:
        return ""

    active   = cs["active_combatant"] or "?"
    rnd      = cs["round_num"]
    reacted  = agent_name in cs["reactions_used"]
    spoken   = agent_name in cs["speech_used"]

    # ── Tour actif ───────────────────────────────────────────────────────────
    if agent_name == active:
        # Rappels spécifiques par personnage
        _char_hints = {
            "Kaelen": (
                "  🗡 EXTRA ATTACK : ton Action = 2 attaques — déclare-les toutes les deux.\n"
                "  ✦ DIVINE SMITE : décision APRÈS le jet d'attaque — le système te propose de l'appliquer.\n"
                "    ⚠ Format OBLIGATOIRE : inclure 'Divine Smite niv.X si touche' dans la Règle 5e.\n"
                "    ⚠ JAMAIS de [ACTION] séparé pour le smite — toujours dans le bloc de l'attaque.\n"
                "  ◈ ACTION BONUS : sort smite PRÉ-CAST (Wrathful Smite…) si non lancé avant.\n"
            ),
            "Elara": (
                "  🔮 ACTION : choisis le sort le plus efficace pour la situation.\n"
                "  ◈ CONCENTRATION : vérifie si un sort actif tourne déjà avant d'en lancer un nouveau.\n"
                "  ◈ ACTION BONUS : sort bonus action si disponible (ex. Misty Step pour te repositionner).\n"
            ),
            "Thorne": (
                "  🗡 ACTION : 1 attaque + SNEAK ATTACK (8d6) si avantage ou allié adjacent.\n"
                "  ◈ CUNNING ACTION obligatoire chaque tour (Dash / Disengage / Hide) — choisis selon la tactique.\n"
                "  ⚡ Priorité : Hide → avantage assuré sur la prochaine attaque + Sneak Attack garanti.\n"
            ),
            "Lyra": (
                "  ✦ ACTION : sort de soin/attaque ou Esquive si en danger.\n"
                "  ◈ ARME SPIRITUELLE : si invoquée, attaque bonus gratuite chaque tour (ne pas oublier !).\n"
                "  ◈ CHANNEL DIVINITY disponible si non utilisé ce repos court.\n"
            ),
        }
        hint = _char_hints.get(agent_name, "")
        return (
            f"\n\n⚔️ ═══ COMBAT — ROUND {rnd} — C'EST TON TOUR ═══\n"
            "Utilise TON ÉCONOMIE D'ACTION COMPLÈTE de façon AUTONOME :\n\n"
            f"{hint}"
            "  ↺ RÉACTION : disponible si déclencheur hors-tour (Bouclier, AttOpp…).\n"
            "  🏃 MOUVEMENT : repositionne-toi si c'est tactiquement utile.\n\n"
            "⚠ Ne laisse JAMAIS ton Action inutilisée — au minimum : Esquive (Dodge) ou Aide (Help).\n"
            "⚠ N'attends PAS que le MJ te liste tes options — c'est TON tour, décide.\n\n"
            "FORMAT — termine ton message par un bloc [ACTION] pour chaque action mécanique :\n\n"
            "  [ACTION]\n"
            "  Type      : <Action / Action Bonus / Réaction>\n"
            "  Intention : <ce que ton personnage fait, en une phrase claire>\n"
            "  Règle 5e  : <mécanique exacte : attaque + bonus + dégâts, sort + niveau, etc.>\n"
            "  Cible     : <sur qui ou quoi>\n\n"
            "Si tu as Attaque Supplémentaire, déclare toutes les frappes dans le même bloc :\n"
            "  Type      : Action — Attaque × 2 (Extra Attack)\n"
            "  Règle 5e  : Attaque 1 : corps-à-corps +11, 2d6+8 radiants\n"
            "              Attaque 2 : corps-à-corps +11, 2d6+8 radiants\n\n"
            "Joue avec intensité et concision."
        )

    # ── Hors-tour : les deux ressources épuisées → silence total ────────────
    if reacted and spoken:
        return (
            f"\n\n⚔️ ═══ COMBAT — ROUND {rnd} — HORS-TOUR — TOUTES RESSOURCES ÉPUISÉES ═══\n"
            f"C'est le tour de {active}. Tu as déjà utilisé ta réaction ET ta parole ce round.\n"
            "🚫 TU NE PEUX PLUS RIEN FAIRE jusqu'à ton prochain tour.\n"
            "🚫 Interdit : attaquer, lancer un sort, te déplacer, parler, commenter.\n"
            "✅ Exception : si le MJ te demande explicitement un jet (dégâts, attaque, sauvegarde…),\n"
            "   exécute roll_dice immédiatement — cela ne coûte aucune ressource.\n"
            "✅ Sinon, seule réponse autorisée : le mot-clé exact [SILENCE] — rien d'autre."
        )

    # ── Hors-tour : réaction utilisée, parole encore disponible ─────────────
    if reacted and not spoken:
        return (
            f"\n\n⚔️ ═══ COMBAT — ROUND {rnd} — HORS-TOUR — RÉACTION UTILISÉE ═══\n"
            f"C'est le tour de {active}. Tu as déjà utilisé ta réaction ce round.\n"
            "\n"
            "✅ Il te reste UNE parole possible — seulement si :\n"
            "  • Tu révèles une information tactique CRITIQUE (danger immédiat, piège)\n"
            "  • Tu réponds à une question directe d'un allié\n"
            "  Sinon → [SILENCE]\n"
            "✅ Si le MJ te demande un jet (dégâts, attaque, sauvegarde…) : exécute roll_dice\n"
            "   immédiatement — cela ne coûte aucune ressource.\n"
            "\n"
            "🚫 INTERDIT : toute action physique, mouvement, sort, commentaire.\n"
            "Si tu parles, une seule phrase (max 10 mots). Après : [SILENCE]."
        )

    # ── Hors-tour : parole utilisée, réaction encore disponible ─────────────
    if spoken and not reacted:
        return (
            f"\n\n⚔️ ═══ COMBAT — ROUND {rnd} — HORS-TOUR — PAROLE UTILISÉE ═══\n"
            f"C'est le tour de {active}. Tu as déjà parlé ce round.\n"
            "\n"
            "✅ Il te reste UNE réaction D&D 5e — seulement si un déclencheur mécanique précis se produit :\n"
            "  • Attaque d'opportunité (ennemi quitte ta portée)\n"
            "  • Sort Bouclier (tu es attaqué)\n"
            "  • Riposte ou Pas de côté (si tu possèdes cette capacité)\n"
            "  Sans déclencheur réel → [SILENCE]\n"
            "✅ Si le MJ te demande un jet (dégâts, attaque, sauvegarde…) : exécute roll_dice\n"
            "   immédiatement — cela ne coûte aucune ressource.\n"
            "\n"
            "🚫 INTERDIT : toute action normale, mouvement, parole supplémentaire.\n"
            "Après la réaction (ou si pas de déclencheur) : [SILENCE]."
        )

    # ── Hors-tour : les deux ressources disponibles ──────────────────────────
    return (
        f"\n\n⚔️ ═══ COMBAT — ROUND {rnd} — HORS-TOUR ═══\n"
        f"C'est le tour de {active}. Ce n'est PAS ton tour.\n"
        "\n"
        "Tu disposes de DEUX ressources limitées et indépendantes ce round :\n"
        "\n"
        "🔵 RÉACTION (1 par round) — uniquement si un déclencheur mécanique précis se produit :\n"
        "  • Attaque d'opportunité, sort Bouclier, Riposte, Pas de côté…\n"
        "  Sans déclencheur réel → pas de réaction.\n"
        "\n"
        "🟡 PAROLE (1 par round) — uniquement si l'une de ces conditions est remplie :\n"
        "  • Tu révèles une information CRUCIALE que les alliés ne peuvent pas deviner\n"
        "  • Tu réponds à une question directe d'un allié\n"
        "  Une seule phrase, max 10 mots. Le bavardage tactique est interdit.\n"
        "\n"
        "✅ JETS DEMANDÉS PAR LE MJ — toujours autorisés, quelle que soit ta situation :\n"
        "  Si le MJ te demande un jet (dégâts, attaque, sauvegarde, initiative…),\n"
        "  exécute roll_dice immédiatement. Cela ne consomme ni réaction ni parole.\n"
        "\n"
        "🚫 INTERDIT hors-tour, sans exception :\n"
        "  • Se déplacer, attaquer hors réaction, lancer un sort hors réaction\n"
        "  • Action bonus, objet, compétence\n"
        "  • Commenter l'action, donner des conseils, décrire une posture\n"
        "\n"
        "Si aucune condition ne justifie d'agir → réponds [SILENCE].\n"
        "Après chaque ressource utilisée, réponds [SILENCE] pour les tours suivants."
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
        self.temp_hp    = 0   # PV temporaires (absorbent les dégâts en premier)
        self.ac         = ac
        self.initiative = initiative
        self.dex_bonus  = dex_bonus
        self.color      = color
        self.concentration = concentration
        self.bestiary_name = ""   # nom exact dans le bestiary (pour la fiche)

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

    def temp_hp_pct(self) -> float:
        """Fraction de la barre occupée par les PV temporaires (peut dépasser 1.0)."""
        if self.max_hp <= 0:
            return 0.0
        return min(1.0, self.temp_hp / self.max_hp)

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
            "name":               self.name,
            "is_pc":              self.is_pc,
            "max_hp":             self.max_hp,
            "hp":                 self.hp,
            "temp_hp":            self.temp_hp,
            "ac":                 self.ac,
            "initiative":         self.initiative,
            "dex_bonus":          self.dex_bonus,
            "color":              self.color,
            "concentration":      self.concentration,
            "bestiary_name":      self.bestiary_name,
            "conditions":         list(self.conditions.keys()),
            "notes":              self.notes,
            "death_saves_success": self.death_saves_success,
            "death_saves_fail":   self.death_saves_fail,
            "action_used":        self.action_used,
            "bonus_used":         self.bonus_used,
            "reaction_used":      self.reaction_used,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Combatant":
        """Reconstruit un Combatant depuis un dict sérialisé."""
        c = cls(
            name          = d["name"],
            is_pc         = d["is_pc"],
            max_hp        = d.get("max_hp", 20),
            current_hp    = d.get("hp"),
            ac            = d.get("ac", 10),
            initiative    = d.get("initiative", 0),
            dex_bonus     = d.get("dex_bonus", 0),
            color         = d.get("color", "#e0e0e0"),
            concentration = d.get("concentration", False),
        )
        c.bestiary_name       = d.get("bestiary_name", "")
        c.notes               = d.get("notes", "")
        c.temp_hp             = d.get("temp_hp", 0)
        c.death_saves_success = d.get("death_saves_success", 0)
        c.death_saves_fail    = d.get("death_saves_fail", 0)
        c.action_used         = d.get("action_used", False)
        c.bonus_used          = d.get("bonus_used", False)
        c.reaction_used       = d.get("reaction_used", False)
        for cond in d.get("conditions", []):
            c.conditions[cond] = True
        return c


# ─── Fenêtre principale du tracker ───────────────────────────────────────────
class CombatTracker:
    """Fenêtre Toplevel de gestion de combat D&D 5e."""

    def __init__(self, root: tk.Tk, state_loader,
                 chat_queue=None, pc_turn_callback=None,
                 advance_turn_callback=None):
        """
        root              : tk.Tk principal
        state_loader      : callable → dict (load_state de state_manager)
        chat_queue        : queue.Queue pour injecter des messages dans le chat
        pc_turn_callback  : callable(char_name: str) → déclenché automatiquement
                            quand c'est le tour d'un PJ, pour injecter le trigger
                            autogen sans attendre la saisie du MJ.
        """
        self.root              = root
        self._load_state       = state_loader
        self.chat_queue        = chat_queue
        self.pc_turn_callback  = pc_turn_callback
        self.advance_turn_callback = advance_turn_callback   # ← nouveau
        self.combatants: list[Combatant] = []
        self.current_idx = -1
        self.round_num   = 0
        self.combat_active = False
        self._rows: dict = {}          # uid → frame widgets
        self._row_widgets: dict = {}   # uid → {hp_lbl, bar_canvas, draw_hp_bar} — mises à jour in-place
        self._save_timer = None        # timer de sauvegarde différée (debounce)
        self.kill_pool: list  = []     # combatants retirés via Kill Pool

        self._build_window()
        self._restore_combat_state()
        # Préchauffage du bestiary en arrière-plan pour éviter le freeze
        # au premier _ct_pick() (chargement du JSON ~2–5 MB)
        if _BESTIARY_OK:
            threading.Thread(target=_bestiary_load, daemon=True,
                             name="bestiary-preload").start()

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
            self._ct_suggest_labels: list[tk.Label] = []
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
        for col, text in enumerate(["Nom", "PV max", "CA", "Init+", "Init=", "Qte"]):
            lbl(text).grid(row=field_label_row, column=col, padx=(0,2), sticky="w")

        fr = field_label_row + 1
        self._npc_name       = ent(12, "Gobelin");   self._npc_name.grid(row=fr, column=0, padx=(0,4), ipady=3)
        self._npc_hp         = ent(5,  "15");         self._npc_hp.grid(row=fr,  column=1, padx=(0,4), ipady=3)
        self._npc_ac         = ent(4,  "13");         self._npc_ac.grid(row=fr,  column=2, padx=(0,4), ipady=3)
        self._npc_dex        = ent(4,  "1");          self._npc_dex.grid(row=fr, column=3, padx=(0,4), ipady=3)
        self._npc_init_fixed = ent(4,  "");           self._npc_init_fixed.grid(row=fr, column=4, padx=(0,4), ipady=3)
        self._npc_qty        = ent(3,  "1");          self._npc_qty.grid(row=fr, column=5, padx=(0,4), ipady=3)

        tk.Button(add_frame, text="+ Ajouter",
                  bg=_darken(C["blue"], 0.4), fg=C["blue_bright"],
                  font=("Consolas", 9, "bold"), relief="flat",
                  padx=8, pady=3, cursor="hand2",
                  command=self._add_npc).grid(row=fr, column=6, padx=(4, 0))

        tk.Button(add_frame, text="+ Héros",
                  bg=_darken(C["green"], 0.35), fg=C["green_bright"],
                  font=("Consolas", 9, "bold"), relief="flat",
                  padx=8, pady=3, cursor="hand2",
                  command=self._add_missing_pc).grid(row=fr, column=7, padx=(4, 0))

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

        ac_raw = m.get("ac", [])
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

    # ── Import PJ depuis state_manager ────────────────────────────────────────
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
                COMBAT_STATE["reactions_used"] = set(saved.get("reactions_used", []))
                COMBAT_STATE["speech_used"]    = set(saved.get("speech_used",    []))

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

    # ── Refresh complet de la liste ───────────────────────────────────────────
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
                            anchor="w")
        name_lbl.pack(anchor="w")

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
            # Exécuté dans un thread daemon pour ne pas bloquer le thread Tk.
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
            # Sauvegarde différée — pas de I/O disque synchrone à chaque clic
            self._schedule_save()
            # Rebuild complet seulement si un PJ tombe à 0 (affiche les jets de mort)
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

        # ── Col 5 : Conditions ────────────────────────────────────────────
        cond_f = _col(220)
        self._build_conditions_widget(cond_f, c, row_bg)

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
                self._schedule_save()

            btn.config(command=_toggle)

    def _build_action_economy(self, parent, c: Combatant,
                               row_bg: str, active: bool):
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
        COMBAT_STATE["reactions_used"]   = set()
        COMBAT_STATE["speech_used"]      = set()
        active_c = self.combatants[0] if self.combatants else None
        COMBAT_STATE["active_combatant"] = active_c.name if active_c else None

        self._btn_start.config(state=tk.DISABLED)
        self._btn_next.config( state=tk.NORMAL)
        self._btn_end.config(  state=tk.NORMAL)

        self._update_round_label()
        self._refresh_list()
        self._log_turn()
        self._save_combat_state()
        # ── Déclenche automatiquement le tour si c'est un PJ ──
        self._trigger_pc_turn_if_needed()

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

        # Mise à jour visuelle chirurgicale — PAS de rebuild complet
        self._update_active_rows(old_idx, self.current_idx)
        self._log_turn()
        self._save_combat_state()

        # ── Déclenche automatiquement le tour si c'est un PJ ──
        self._trigger_pc_turn_if_needed()

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

        bname = getattr(self, "_current_bestiary_name", "")

        for i in range(qty):
            n    = f"{name} {i+1}" if qty > 1 else name
            init = int(fixed) if fixed.lstrip("-").isdigit() else 0
            col  = NPC_COLORS[(len(self.combatants)) % len(NPC_COLORS)]
            c    = Combatant(name=n, is_pc=False,
                             max_hp=max_hp, ac=ac,
                             initiative=init, dex_bonus=dex_b,
                             color=col)
            c.bestiary_name = bname
            if not fixed.lstrip("-").isdigit():
                c.roll_initiative()
            self.combatants.append(c)

        # Reset bestiary state
        self._current_bestiary_name = ""
        if _BESTIARY_OK and hasattr(self, "_ct_status"):
            self._ct_status.config(text="")

        self._sort_and_refresh()
        self._log(f"+ {qty}x {name} ajoute(s) au combat.")

    def _remove_combatant(self, c: Combatant):
        if c in self.combatants:
            idx = self.combatants.index(c)
            self.combatants.remove(c)
            if self.combat_active and self.current_idx >= idx:
                self.current_idx = max(0, self.current_idx - 1)
            self._refresh_list()

    def _add_to_kill_pool(self, c: "Combatant"):
        """Retire le combatant de l'initiative et l'ajoute au kill pool."""
        if c not in self.combatants:
            return
        idx = self.combatants.index(c)
        self.combatants.remove(c)
        if self.combat_active and self.current_idx >= idx:
            self.current_idx = max(0, self.current_idx - 1)
        self.kill_pool.append(c)
        self._refresh_list()
        self._refresh_kill_pool()
        self._log(f"[Kill Pool] {c.name} ({c.max_hp} PV max) retiré du combat.")
        if self.chat_queue:
            self.chat_queue.put({
                "sender": "⚔️ Combat",
                "text":   f"☠️ {c.name} est hors combat (Kill Pool).",
                "color":  "#9b59b6",
            })

    def _refresh_kill_pool(self):
        """Met à jour l'affichage du kill pool dans le bottom panel."""
        if not hasattr(self, "_kill_pool_inner"):
            return
        for w in self._kill_pool_inner.winfo_children():
            w.destroy()
        if not self.kill_pool:
            tk.Label(self._kill_pool_inner, text="— vide —",
                     bg="#0d1018", fg=C["fg_dim"],
                     font=("Consolas", 8)).pack(anchor="w")
            return
        for c in self.kill_pool:
            row = tk.Frame(self._kill_pool_inner, bg="#0d1018")
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=f"☠ {c.name}",
                     bg="#0d1018", fg="#cc66cc",
                     font=("Consolas", 8, "bold"), anchor="w"
                     ).pack(side=tk.LEFT, padx=(0, 6))
            tk.Label(row, text=f"{c.max_hp} PV",
                     bg="#0d1018", fg=C["fg_dim"],
                     font=("Consolas", 8)).pack(side=tk.LEFT)
            # Bouton Annuler (remettre dans l'initiative)
            def _restore(cb=c):
                if cb in self.kill_pool:
                    self.kill_pool.remove(cb)
                    cb.hp = max(1, cb.max_hp // 4)  # remet à 25% PV
                    self.combatants.append(cb)
                    self._sort_and_refresh()
                    self._refresh_kill_pool()
            tk.Button(row, text="Annuler",
                      bg=_darken(C["gold"], 0.55), fg=C["gold"],
                      font=("Consolas", 7), bd=0, relief="flat",
                      cursor="hand2", padx=3,
                      command=_restore).pack(side=tk.RIGHT)

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
                                        "Un combat est en cours. Fermer quand même ?\n"
                                        "(Le combat sera sauvegardé et reprendra à la prochaine ouverture.)"):  
                return
        self._save_combat_state()
        self.win.destroy()

    # ── Déclencheur automatique tour PJ ──────────────────────────────────────

    def _trigger_pc_turn_if_needed(self):
        """Si le combatant actif est un PJ vivant, appelle pc_turn_callback
        pour déclencher son tour automatiquement dans autogen.
        Appelé après chaque _next_turn() et _start_combat().
        """
        if not self.combat_active or not self.pc_turn_callback:
            return
        if not (0 <= self.current_idx < len(self.combatants)):
            return
        c = self.combatants[self.current_idx]
        if c.is_pc and not c.is_down:
            self.pc_turn_callback(c.name)


# ─── Helper recoloriage récursif ─────────────────────────────────────────────

def _set_row_bg_recursive(widget, old_bg: str, new_bg: str):
    """Recolorie récursivement tous les widgets d'une ligne dont le bg == old_bg.
    Laisse intacts les widgets avec un bg différent (Entry, Canvas, badges…)."""
    try:
        if widget.cget("bg") == old_bg:
            widget.config(bg=new_bg)
    except Exception:
        pass
    for child in widget.winfo_children():
        _set_row_bg_recursive(child, old_bg, new_bg)


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