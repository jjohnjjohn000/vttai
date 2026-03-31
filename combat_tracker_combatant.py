"""
combat_tracker_combatant.py
───────────────────────────
Fichier 3/10 : Classe Combatant (représentation de la donnée d'un participant).
"""

import random

# Note: ce module présume que le dictionnaire C (palette) est accessible ou importé.
try:
    from combat_tracker_constants import C
except ImportError:
    pass

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

        # ── Portrait pré-résolu depuis images/portraits/ ──────────────────────
        # Chemin absolu vers le fichier image, ou "" si aucun portrait trouvé.
        # Rempli par portrait_resolver.resolve_portrait() au moment de l'ajout
        # du combatant (dans CombatTrackerNPCMixin._add_npc ou _add_missing_pc).
        # Une fois rempli, les tooltips du tracker ET de la carte combat
        # l'utilisent directement, sans jamais afficher de file-dialog.
        self.portrait: str = ""

        # Alignement envers les héros : "hostile" | "neutral" | "ally"
        # Par défaut : PJ → allié, PNJ → hostile
        self.alignment: str = "ally" if is_pc else "hostile"

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
            "portrait":           self.portrait,
            "alignment":          self.alignment,
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
        c.portrait            = d.get("portrait", "")
        c.alignment           = d.get("alignment", "ally" if d["is_pc"] else "hostile")
        c.notes               = d.get("notes", "")
        c.temp_hp             = d.get("temp_hp", 0)
        c.death_saves_success = d.get("death_saves_success", 0)
        c.death_saves_fail    = d.get("death_saves_fail", 0)
        c.action_used         = d.get("action_used", False)
        c.bonus_used          = d.get("bonus_used", False)
        c.reaction_used       = d.get("reaction_used", False)
        for cond in d.get("conditions",[]):
            c.conditions[cond] = True

        # Si le portrait sérialisé n'existe plus sur le disque, re-résoudre.
        if c.portrait and not __import__("os").path.exists(c.portrait):
            c.portrait = ""
        if not c.portrait:
            try:
                from portrait_resolver import resolve_portrait
                lookup = c.bestiary_name or c.name
                c.portrait = resolve_portrait(lookup)
            except Exception:
                pass

        return c