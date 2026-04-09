"""
combat_map_panel.py — Carte de combat avec brouillard de guerre.

Architecture de rendu (GPU-like, offscreen) :
  ┌──────────────────────────────────────────────────────────┐
  │  _bg_pil    PIL RGBA  — fond (damier/image) + grille     │
  │     ↓  cached, reconstruit uniquement au zoom/resize     │
  │  _fog_pil   PIL RGBA  — brouillard (transparent=révélé)  │
  │     ↓  dirty-patch sur chaque cellule peinte             │
  │  alpha_composite(bg, fog)  →  _scene_photo               │
  │     ↓  1 seul canvas.create_image() — jamais d'items Tk  │
  │  tokens     canvas items  (5-15 seulement, drag fluide)  │
  └──────────────────────────────────────────────────────────┘

Fonctionnalités :
  • Vue MJ  : fog semi-transparent (MJ voit la carte en dessous)
  • Vue Joueur : fog opaque (vision joueur)
  • Flèches : déplace la grille au pixel près (sans recomposite)
  • Shift+↑/↓ : augmente / diminue la taille des cases (1 px à la fois)

Dépendances :  pip install Pillow numpy
"""

import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox
import os
import base64
import tempfile

try:
    import numpy as np
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True 
except ImportError:
    PIL_AVAILABLE = False

# ─── Constantes ────────────────────────────────────────────────────────────────

CELL_PX_DEFAULT = 44   # taille de case par défaut (pixels, zoom 1.0)
CELL_PX_MIN     = 8
CELL_PX_MAX     = 120

# Fond damier
_C_BG_A = (19, 19, 32, 255)
_C_BG_B = (16, 16, 24, 255)
# Grille
_C_GRID = (255, 255, 255, 35)
# Fog — vue MJ (semi-transparent : voir la carte sous le brouillard)
_C_FOG_DM     = (20, 20, 60, 100)    # bleuté translucide
# Fog — vue joueur (opaque)
_C_FOG_PLAYER = (8, 8, 18, 255)
_C_FOG_CLEAR  = (0, 0, 0, 0)

BG_WIN  = "#0d0d1a"
BG_TOOL = "#181828"
BG_CNV  = "#0c0c18"

HERO_NAMES  = ["Kaelen", "Elara", "Thorne", "Lyra"]
HERO_COLORS = {
    "Kaelen": (229, 115, 115),
    "Elara":  (100, 181, 246),
    "Thorne": (206, 147, 216),
    "Lyra":   (129, 199, 132),
}
TOKEN_STYLES = {
    "hero":     {"fill": (26,  58, 106), "outline": (91, 164, 245), "shape": "circle"},
    "monster":  {"fill": (90,  10,  10), "outline": (224, 64,  64), "shape": "diamond"},
    "trap":     {"fill": (74,  48,   0), "outline": (240, 176, 48), "shape": "triangle"},
    "spectral": {"fill": (156, 39, 176), "outline": (234, 128, 252), "shape": "diamond"},
}

# Tailles de token : nombre de cases occupées (côté du carré)
TOKEN_SIZES = {"Tiny": 0.5, "Small": 1, "Medium": 1, "Large": 2, "Huge": 3, "Gargantuan": 4}

# Conditions D&D 5e avec couleur de badge
DND_CONDITIONS = {
    "Aveuglé":        "#888888",
    "Charmé":         "#ff80ab",
    "Épuisé":         "#bf9169",
    "Effrayé":        "#b39ddb",
    "Agrippé":        "#a5d6a7",
    "Étourdi":        "#ffe082",
    "Inconscient":    "#ef9a9a",
    "Invisible":      "#e0e0e0",
    "Paralysé":       "#fff9c4",
    "Pétrifié":       "#bcaaa4",
    "Empoisonné":     "#69f0ae",
    "À terre":        "#ff8a65",
    "Entravé":        "#ce93d8",
    "Sourd":          "#90a4ae",
    "Concentré":      "#40c4ff",
}

# Statuts tactiques D&D 5e avec couleur de badge (anneau interne)
DND_TACTICS = {
    "Esquive":        "#00bcd4",
    "Caché":          "#455a64",
    "Préparé":        "#ffb300",
    "Désengagé":      "#8d6e63",
}

def _rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb[:3])


# ─── Classe principale ────────────────────────────────────────────────────────

# ─── Utilitaires ─────────────────────────────────────────────────────────────

def _sep(parent):
    tk.Frame(parent, bg="#3a3a55", width=1, height=26).pack(
        side=tk.LEFT, padx=6, pady=2)

def _darken_rgb(r: int, g: int, b: int, factor: float = 0.65):
    """Retourne un tuple RGB assombri (utilisé pour les outlines PIL)."""
    return (int(r * factor), int(g * factor), int(b * factor))

def _darken_rgb_tuple(r: int, g: int, b: int, factor: float = 0.65):
    return (int(r * factor), int(g * factor), int(b * factor))

def _compress_ranges(cols: list) -> str:
    """Convertit [1,2,3,5,6,9] → '1-3, 5-6, 9'."""
    if not cols:
        return ""
    ranges, start, end = [], cols[0], cols[0]
    for c in cols[1:]:
        if c == end + 1:
            end = c
        else:
            ranges.append(f"{start}-{end}" if start != end else str(start))
            start = end = c
    ranges.append(f"{start}-{end}" if start != end else str(start))
    return ", ".join(ranges)
