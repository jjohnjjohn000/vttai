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

class SelectionMixin:
    pass
    # ─── Sélection rectangulaire ──────────────────────────────────────────────

    def _clear_selection(self):
        prev = set(self._selected_tokens)
        self._selected_tokens.clear()
        for t in self.tokens:
            if id(t) in prev:
                self._redraw_one_token(t)

    def _box_select_begin(self, cx: float, cy: float):
        self._box_select_start = (cx, cy)
        if self._box_rect_id:
            self.canvas.delete(self._box_rect_id)
        self._box_rect_id = self.canvas.create_rectangle(
            cx, cy, cx, cy,
            outline="#ffffff", width=1, dash=(4, 3), tags="box_select")

    def _box_select_update(self, cx: float, cy: float):
        if not self._box_select_start:
            return
        x0, y0 = self._box_select_start
        self.canvas.coords(self._box_rect_id, x0, y0, cx, cy)

    def _box_select_end(self, cx: float, cy: float, shift: bool):
        if not self._box_select_start:
            return
        x0, y0 = self._box_select_start
        self._box_select_start = None
        if self._box_rect_id:
            self.canvas.delete(self._box_rect_id)
            self._box_rect_id = 0
        rx0, rx1 = min(x0, cx), max(x0, cx)
        ry0, ry1 = min(y0, cy), max(y0, cy)
        if rx1 - rx0 < 4 and ry1 - ry0 < 4:
            if not shift:
                self._clear_selection()
            return
        if not shift:
            self._clear_selection()
        cp = self._cp
        for t in self.tokens:
            tcx = (t["col"] + 0.5) * cp
            tcy = (t["row"] + 0.5) * cp
            if rx0 <= tcx <= rx1 and ry0 <= tcy <= ry1:
                if shift and id(t) in self._selected_tokens:
                    self._selected_tokens.discard(id(t))
                else:
                    self._selected_tokens.add(id(t))
                self._redraw_one_token(t)

