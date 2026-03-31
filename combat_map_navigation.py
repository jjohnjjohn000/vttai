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

class NavigationMixin:
    pass
    # ─── Clavier : offset grille + taille de case ─────────────────────────────

    def _map_nudge(self, dx: int, dy: int):
        """Flèches : déplace l'image de fond de 1 px sous la grille fixe."""
        self.map_ox += dx
        self.map_oy += dy
        # Invalide uniquement le buffer bg (pas le fog) puis recomposite throttlé
        self._bg_pil = None
        if self._pending_render is not None:
            self.win.after_cancel(self._pending_render)
        self._pending_render = self.win.after(30, self._flush_map_nudge)

    def _flush_map_nudge(self):
        self._pending_render = None
        self._rebuild_bg()
        self._composite()
        self._save_state()

    def _change_cell_size(self, delta: int):
        """Shift+↑/↓ : change la taille de case de 1 px (rendu déboncé 80 ms)."""
        new_size = max(CELL_PX_MIN, min(CELL_PX_MAX, self.cell_px + delta))
        if new_size == self.cell_px:
            return
        self.cell_px = new_size
        self._cellpx_lbl.config(text=f"{self.cell_px}px")
        if getattr(self, "_cell_resize_pending", None):
            self.win.after_cancel(self._cell_resize_pending)
        def _do_redraw():
            self._cell_resize_pending = None
            self._bg_pil  = None
            self._fog_pil = None
            self._full_redraw()
        self._cell_resize_pending = self.win.after(80, _do_redraw)

