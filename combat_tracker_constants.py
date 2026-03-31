"""
combat_tracker_constants.py
───────────────────────────
Fichier 2/10 : Imports globaux, constantes, palette et configuration des conditions.
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