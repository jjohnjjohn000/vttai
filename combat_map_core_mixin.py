import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox
import os
import tempfile
import base64
try:
    import numpy as np
    from PIL import Image, ImageTk, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from combat_map_constants import *
from combat_map_constants import _sep, _darken_rgb, _darken_rgb_tuple, _compress_ranges, _C_BG_A, _C_BG_B, _C_FOG_CLEAR, _C_FOG_DM, _C_FOG_PLAYER, _C_GRID, _rgb_to_hex

class CoreMixin:
    pass
    def __init__(self, parent, win_state=None, save_fn=None, track_fn=None,
                 msg_queue=None, inject_fn=None, update_sys_prompt_fn=None, app=None):
        if not PIL_AVAILABLE:
            messagebox.showerror(
                "Dépendances manquantes",
                "La carte de combat nécessite Pillow et numpy :\n\n"
                "  pip install Pillow numpy",
                parent=parent)
            return

        self.parent    = parent
        self.win_state = win_state or {}
        self.save_fn   = save_fn   or (lambda: None)
        self.track_fn  = track_fn  or (lambda k, w: w)
        self.msg_queue = msg_queue          # pour notifier le chat
        self.inject_fn = inject_fn          # callable(text) → injecte dans autogen
        self.update_sys_prompt_fn = update_sys_prompt_fn # callback silencieux LLM
        self.app = app

        # ── État carte ────────────────────────────────────────────────────────
        self.zoom    = 1.0
        self.cols    = 30
        self.rows    = 20
        self.tokens: list = []

        # Taille de case en px (modifiable au clavier)
        self.cell_px = CELL_PX_DEFAULT

        # ── Calques de carte (support multi-étages) ───────────────────────────
        # Chaque calque : {"name", "path", "w", "h", "ox", "oy", "visible"}
        self.map_layers: list = []
        self._active_layer_idx: int = 0
        self._ensure_default_layer()

        # Fractions de scroll à restaurer après le premier rendu
        self._scroll_fx: float = 0.0
        self._scroll_fy: float = 0.0

        # Vue : True = MJ (fog transparent), False = Joueur (fog opaque)
        self._dm_view = True

        # Fog : image PIL "L" (cols*cell_px × rows*cell_px) — 255=couvert 0=révélé
        # Résolution fixe zoom-indépendante ; scalée à cp au rendu.
        self._fog_mask: "Image.Image | None" = None   # initialisée dans _load_from_saved
        self._fog_pil:  "Image.Image | None" = None

        # Buffer fond (reconstruits au zoom/resize seulement)
        self._bg_pil:  "Image.Image | None" = None
        self._scene_photo = None
        self._img_id      = 0

        # Cache image par chemin (partagé entre tous les calques)
        self._map_pil_cache_dict: dict = {}   # {path: PIL Image RGBA}
        self._tile_rect: tuple = (0, 0, 0, 0)  # (x0,y0,x1,y1) tuile courante

        # ── État des outils ───────────────────────────────────────────────────
        self.tool           = "reveal"
        self.brush_size     = 2
        self.token_type     = "hero"
        self._show_grid     = True
        self._drag_token    = None
        self._drag_offset   = (0.0, 0.0)
        self._last_fog_cell = None
        self._pending_render = None

        # ── Undo fog (Ctrl+Z) — 15 états max ─────────────────────────────────
        self._fog_undo_stack: list = []   # copies PIL Image "L" du fog_mask

        # ── Outil règle ───────────────────────────────────────────────────────
        self._ruler_start_pt: "tuple | None" = None
        self._ruler_ids: list = []

        # ── Zoom fluide ───────────────────────────────────────────────────────
        # Durant le scroll : rebuild PIL throttlé à 16 ms (60 fps max).
        # Après 120 ms d'inactivité : rebuild PIL complet (image nette).
        self._zoom_rebuild_pending = None   # after-id du rebuild PIL différé
        self._zoom_anchor_world_x: float = 0.0  # coord monde sous curseur (début séquence)
        self._zoom_anchor_world_y: float = 0.0
        self._zoom_anchor_ex: int   = 0    # coord écran du curseur
        self._zoom_anchor_ey: int   = 0

        # ── Notes flottantes (post-its déplaçables) ───────────────────────────
        # Chaque note : {px, py, text, color, canvas_ids: [], pinned: bool}
        # px/py = coordonnées en espace carte (indépendantes du zoom)
        # → converties à l'affichage en canvas_x = px * zoom_factor
        self._notes: list = []
        self._doors: list = []  # {col, row, open, label, canvas_ids}
        self._drag_note: "dict | None" = None       # note en cours de déplacement
        self._drag_note_off: tuple = (0.0, 0.0)    # offset souris→origine note

        # ── Dessin polygonal ──────────────────────────────────────────────────
        self._poly_points: list = []
        self._poly_ids:    list = []

        # ── Obstacles (formes opaques permanentes) ────────────────────────────
        # Chaque obstacle : {pts:[(x,y)…], color:str, label:str, type:"poly"|"free"}
        # pts en coordonnées monde (indépendantes du zoom)
        self._obstacles:     list          = []
        self._obs_poly_pts:  list          = []   # sommets en cours (outil poly)
        self._obs_poly_ids:  list          = []   # canvas ids de preview
        self._obs_free_pts:  list          = []   # points main levée en cours
        self._obs_free_id:   int           = 0    # canvas id ligne preview
        self._obs_color:     str           = "#cc4400"  # couleur courante
        self._obs_pil:       "Image.Image | None" = None  # calque PIL composité

        # ── Sélection multiple ────────────────────────────────────────────────
        self._selected_tokens:   set          = set()
        self._drag_origins:      dict         = {}
        self._box_select_start: "tuple|None"  = None
        self._box_rect_id:       int          = 0

        # ── Outil redimensionnement carte ─────────────────────────────────────
        # _map_resize_handle : "nw"|"n"|"ne"|"e"|"se"|"s"|"sw"|"w"|"move"|None
        self._map_resize_handle: str | None = None
        self._map_resize_start: dict | None = None   # snapshot au début du drag
        self._map_handle_ids: list = []              # canvas item ids des poignées
        self._lock_ratio: bool = False               # Shift = verrouiller ratio

        # ── Système multi-cartes ──────────────────────────────────────────────
        # Chaque carte est sauvegardée dans campagne/<nom>/maps/<nom_carte>.json
        # win_state contient seulement active_map_name (persistance de la sélection)
        self._active_map_name: str = ""   # nom de la carte courante
        self._map_selector_var: "tk.StringVar | None" = None  # dropdown Tk

        # Charger état sauvegardé
        self._init_maps_system()
        self._build_window()

    # ─── Propriétés calculées ─────────────────────────────────────────────────

    @property
    def _cp(self) -> int:
        """Pixels par case au zoom courant."""
        return max(2, int(self.cell_px * self.zoom))

    @property
    def _wh(self) -> tuple:
        """Taille logique complète de la carte (scrollregion)."""
        cp = self._cp
        return self.cols * cp, self.rows * cp

    @property
    def _fog_color(self) -> tuple:
        """Couleur du fog selon la vue active."""
        return _C_FOG_DM if self._dm_view else _C_FOG_PLAYER

    # ─── Fenêtre ──────────────────────────────────────────────────────────────

    def _build_window(self):
        self.win = tk.Toplevel(self.parent)
        self.win.title(f"Carte de Combat — {self._active_map_name}")
        self.win.configure(bg=BG_WIN)
        self.win.minsize(600, 450)
        self.track_fn("combat_map", self.win)
        if "combat_map" not in self.win_state:
            self.win.geometry("1020x720")
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_toolbar()
        self._build_map_selector_bar()
        self._build_layer_panel()
        self._build_canvas_area()
        self._build_statusbar()
        self._set_tool("reveal")
        self.win.after(80, self._full_redraw)
        # Restaurer zoom + scroll après que le canvas soit rendu et stable
        self.win.after(160, self._restore_view)