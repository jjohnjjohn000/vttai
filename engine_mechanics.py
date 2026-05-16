"""
engine_mechanics.py — Façade de compatibilité.
Redirige vers les sous-modules découpés pour ne casser aucun import dans le reste du projet.
"""

from engine_mechanics_data import CHAR_MECHANICS, split_into_subactions
from engine_mechanics_rolls import roll_attack_only, roll_damage_only
from engine_mechanics_core import execute_action_mechanics

__all__ = [
    "CHAR_MECHANICS",
    "split_into_subactions",
    "roll_attack_only",
    "roll_damage_only",
    "execute_action_mechanics"
]