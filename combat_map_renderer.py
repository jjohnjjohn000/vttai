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

class RendererMixin:
    pass
    # ─── Canvas ───────────────────────────────────────────────────────────────

    def _build_canvas_area(self):
        frame = tk.Frame(self.win, bg=BG_CNV)
        frame.pack(fill=tk.BOTH, expand=True)
        v_sb = tk.Scrollbar(frame, orient=tk.VERTICAL,   bg="#15151f", troughcolor=BG_CNV)
        h_sb = tk.Scrollbar(frame, orient=tk.HORIZONTAL, bg="#15151f", troughcolor=BG_CNV)
        v_sb.pack(side=tk.RIGHT, fill=tk.Y)
        h_sb.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas = tk.Canvas(frame, bg=BG_CNV, highlightthickness=0,
                                yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        def _scroll_x(*args):
            self.canvas.xview(*args)
            self._schedule_tile_refresh()

        def _scroll_y(*args):
            self.canvas.yview(*args)
            self._schedule_tile_refresh()

        v_sb.config(command=_scroll_y)
        h_sb.config(command=_scroll_x)

        # Souris
        self.canvas.bind("<ButtonPress-1>",    self._mb1_down)
        self.canvas.bind("<B1-Motion>",         self._mb1_move)
        self.canvas.bind("<ButtonRelease-1>",   self._mb1_up)
        self.canvas.bind("<Double-Button-1>",   self._mb1_double)
        self.canvas.bind("<ButtonPress-2>",     self._pan_start)
        self.canvas.bind("<B2-Motion>",         self._pan_drag)
        self.canvas.bind("<ButtonPress-3>",     self._mb3_down)
        self.canvas.bind("<Alt-ButtonPress-1>", self._pan_start)
        self.canvas.bind("<Alt-B1-Motion>",     self._pan_drag)
        self.canvas.bind("<MouseWheel>",        self._do_zoom)
        self.canvas.bind("<Button-4>",          self._do_zoom)
        self.canvas.bind("<Button-5>",          self._do_zoom)
        self.canvas.bind("<Motion>",            self._mouse_move)

        # Clavier (focus sur la fenêtre toplevel, pas le canvas)
        self.win.bind("<Left>",        lambda e: self._map_nudge(-1,  0))
        self.win.bind("<Right>",       lambda e: self._map_nudge( 1,  0))
        self.win.bind("<Up>",          lambda e: self._map_nudge( 0, -1))
        self.win.bind("<Down>",        lambda e: self._map_nudge( 0,  1))
        self.win.bind("<Shift-Up>",    lambda e: self._change_cell_size( 1))
        self.win.bind("<Shift-Down>",  lambda e: self._change_cell_size(-1))
        self.win.bind("<Escape>",      lambda e: self._escape_to_select())
        self.win.bind("<Control-z>",   lambda e: self._undo_fog())
        self.win.bind("<Control-Z>",   lambda e: self._undo_fog())

    def _build_statusbar(self):
        sb = tk.Frame(self.win, bg="#070710", pady=3)
        sb.pack(fill=tk.X, side=tk.BOTTOM)
        self._status_var = tk.StringVar()
        self._pos_var    = tk.StringVar()
        self._dim_var    = tk.StringVar(value=f"Grille : {self.cols}×{self.rows}")
        tk.Label(sb, textvariable=self._status_var, bg="#070710", fg="#8888aa",
                 font=("Consolas", 8), anchor="w").pack(side=tk.LEFT, padx=8)
        tk.Label(sb, textvariable=self._dim_var, bg="#070710", fg="#6666aa",
                 font=("Consolas", 8), anchor="e").pack(side=tk.RIGHT, padx=8)
        tk.Label(sb, textvariable=self._pos_var, bg="#070710", fg="#7777aa",
                 font=("Consolas", 8), anchor="e").pack(side=tk.RIGHT, padx=8)

    # ─── Rendu offscreen PIL ──────────────────────────────────────────────────

    def _rebuild_bg(self):
        """
        Couche fond : damier + image + grille.

        Stratégie de rendu selon le zoom :
        - Zoom-in (tuile < carte) : crop natif → resize uniquement la portion visible
          → qualité pixel-perfect, coût O(viewport) indépendant du zoom
        - Zoom-out (carte entière visible) : resize source → taille d'affichage réelle
          → compression proportionnelle, pas de pixels gaspillés

        L'image PIL rendue a exactement la taille de la zone visible (tuile).
        Elle est placée à (tile_x0, tile_y0) dans le canvas.
        """
        cp = self._cp
        W_full, H_full = self._wh

        # ── Zone visible dans l'espace canvas (coordonnées logiques) ──────────
        x0f, x1f = self.canvas.xview()
        y0f, y1f = self.canvas.yview()
        sr_w = W_full + 40
        sr_h = H_full + 40
        margin = cp  # 1 case de marge pour éviter les bords blancs au pan

        tx0 = max(0,      int(x0f * sr_w - margin))
        ty0 = max(0,      int(y0f * sr_h - margin))
        tx1 = min(W_full, int(x1f * sr_w + margin))
        ty1 = min(H_full, int(y1f * sr_h + margin))

        TW = max(1, tx1 - tx0)   # taille de la tuile à rendre (pixels)
        TH = max(1, ty1 - ty0)
        self._tile_rect = (tx0, ty0, tx1, ty1)

        # ── Damier ────────────────────────────────────────────────────────────
        # La phase du damier dépend de tx0/ty0 pour que les cases restent alignées
        ri  = (np.arange(TH) + ty0) // cp
        ci  = (np.arange(TW) + tx0) // cp
        chk = (ri[:, None] + ci[None, :]) % 2
        arr = np.where(chk[:, :, None] == 0,
                       np.array(_C_BG_A, dtype=np.uint8),
                       np.array(_C_BG_B, dtype=np.uint8))
        bg = Image.fromarray(arr.astype(np.uint8), "RGBA")

        # ── Calques de carte (du plus bas au plus haut) ───────────────────────
        for layer in self.map_layers:
            if not layer.get("visible", True):
                continue
            lpath = layer.get("path", "")
            if not lpath or not os.path.exists(lpath):
                continue
            try:
                if lpath not in self._map_pil_cache_dict:
                    self._map_pil_cache_dict[lpath] = Image.open(lpath).convert("RGBA")
                src = self._map_pil_cache_dict[lpath]
                sw, sh = src.size
                scale   = self._cp / self.cell_px
                lw      = layer.get("w", self.cols * self.cell_px)
                lh      = layer.get("h", self.rows * self.cell_px)
                lox     = layer.get("ox", 0)
                loy     = layer.get("oy", 0)
                disp_w  = max(1, int(lw * scale))
                disp_h  = max(1, int(lh * scale))
                img_cx0 = int(lox * scale)
                img_cy0 = int(loy * scale)
                ix0 = max(tx0, img_cx0);  iy0 = max(ty0, img_cy0)
                ix1 = min(tx1, img_cx0 + disp_w);  iy1 = min(ty1, img_cy0 + disp_h)
                if ix1 > ix0 and iy1 > iy0:
                    dest_w = ix1 - ix0;  dest_h = iy1 - iy0
                    frac_x0 = (ix0 - img_cx0) / disp_w;  frac_y0 = (iy0 - img_cy0) / disp_h
                    frac_x1 = (ix1 - img_cx0) / disp_w;  frac_y1 = (iy1 - img_cy0) / disp_h
                    src_crop = src.crop((
                        max(0, int(frac_x0 * sw)), max(0, int(frac_y0 * sh)),
                        min(sw, max(1, int(frac_x1 * sw))), min(sh, max(1, int(frac_y1 * sh))),
                    ))
                    src_cw, src_ch = src_crop.size
                    filt = Image.BILINEAR if dest_w > src_cw else Image.LANCZOS
                    tile_img = src_crop.resize((dest_w, dest_h), filt)
                    map_layer = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
                    map_layer.paste(tile_img, (ix0 - tx0, iy0 - ty0))
                    bg = Image.alpha_composite(bg, map_layer)
            except Exception as e:
                print(f"[CombatMap] calque '{layer.get('name','?')}' : {e}")

        # ── Grille (vectorisée — une seule opération numpy) ─────────────────
        if self._show_grid and cp >= 4:
            bg_arr = np.array(bg, dtype=np.float32)
            gc = np.array(_C_GRID[:3], dtype=np.float32)
            ga = _C_GRID[3] / 255.0
            inv_ga = 1.0 - ga
            # Colonnes : positions x de toutes les lignes verticales dans la tuile
            col_xs = np.arange(tx0 // cp, tx1 // cp + 2) * cp - tx0
            col_xs = col_xs[(col_xs >= 0) & (col_xs < TW)]
            if col_xs.size:
                bg_arr[:, col_xs, :3] = ga * gc + inv_ga * bg_arr[:, col_xs, :3]
            # Rangées : positions y de toutes les lignes horizontales
            row_ys = np.arange(ty0 // cp, ty1 // cp + 2) * cp - ty0
            row_ys = row_ys[(row_ys >= 0) & (row_ys < TH)]
            if row_ys.size:
                bg_arr[row_ys, :, :3] = ga * gc + inv_ga * bg_arr[row_ys, :, :3]
            bg_arr[:, :, 3] = 255
            bg = Image.fromarray(bg_arr.astype(np.uint8), "RGBA")

        self._bg_pil = bg

    def _rebuild_fog(self):
        """Fog sur la tuile visible uniquement, résolution native."""
        tx0, ty0, tx1, ty1 = getattr(self, "_tile_rect", (0, 0) + self._wh)
        TW = max(1, tx1 - tx0)
        TH = max(1, ty1 - ty0)
        W_full, H_full = self._wh

        if self._fog_mask is None:
            self._fog_mask = Image.new("L", (self.cols * self.cell_px,
                                             self.rows * self.cell_px), 255)
        mW, mH = self._fog_mask.size

        # Crop du fog mask proportionnel à la tuile canvas
        fx0 = int(tx0 / W_full * mW) if W_full > 0 else 0
        fy0 = int(ty0 / H_full * mH) if H_full > 0 else 0
        fx1 = int(tx1 / W_full * mW) if W_full > 0 else mW
        fy1 = int(ty1 / H_full * mH) if H_full > 0 else mH
        fog_crop = self._fog_mask.crop((
            max(0, fx0), max(0, fy0),
            min(mW, max(fx0 + 1, fx1)),
            min(mH, max(fy0 + 1, fy1))))
        scaled = fog_crop.resize((TW, TH), Image.NEAREST)

        arr  = np.array(scaled, dtype=np.uint8)
        rgba = np.zeros((TH, TW, 4), dtype=np.uint8)
        fc   = np.array(self._fog_color, dtype=np.uint8)
        covered = arr > 0
        rgba[covered] = fc
        if self._dm_view:
            rgba[covered, 3] = (arr[covered].astype(np.uint16) * fc[3] // 255).astype(np.uint8)
        self._fog_pil = Image.fromarray(rgba, "RGBA")

    def _patch_fog_cells(self, cells: list):
        """Non utilisé avec le fog mask — garde uniquement pour compat d'appel."""
        self._rebuild_fog()

    def _composite(self):
        """alpha_composite(bg, obstacles, fog) → PhotoImage placé aux coords canvas."""
        if self._bg_pil is None:
            self._rebuild_bg()
        if self._fog_pil is None:
            self._rebuild_fog()

        W, H = self._bg_pil.size

        # Composition : bg → obstacles (si présents) → fog
        has_obs = bool(self._obstacles)
        if has_obs:
            if self._obs_pil is None or self._obs_pil.size != (W, H):
                self._obs_pil = self._build_obstacle_pil(W, H)
            bg_with_obs = Image.alpha_composite(self._bg_pil, self._obs_pil)
        else:
            self._obs_pil = None
            bg_with_obs = self._bg_pil

        scene = Image.alpha_composite(bg_with_obs, self._fog_pil)
        self._scene_photo = ImageTk.PhotoImage(scene)

        W_full, H_full = self._wh
        self.canvas.config(scrollregion=(0, 0, W_full + 40, H_full + 40))

        x0, y0 = getattr(self, "_tile_rect", (0, 0))[:2]
        if self._img_id:
            self.canvas.itemconfig(self._img_id, image=self._scene_photo)
            self.canvas.coords(self._img_id, x0, y0)
        else:
            self._img_id = self.canvas.create_image(
                x0, y0, anchor="nw", image=self._scene_photo, tags=("scene",))
        self.canvas.tag_raise("token")
        self.canvas.tag_raise("note")
        self.canvas.tag_raise("door")

    # ── Entrées publiques rendu ───────────────────────────────────────────────

    def _full_redraw(self):
        """Reconstruction complète (zoom, grille, taille case)."""
        self._bg_pil  = None
        self._fog_pil = None
        self._obs_pil = None   # invalide le cache obstacles (zoom changé)
        self._img_id  = 0
        self.canvas.delete("scene")
        self._rebuild_bg()
        self._rebuild_fog()
        self._redraw_all_doors()
        self._composite()
        self._redraw_all_tokens()
        self._redraw_all_notes()
        self._zoom_lbl.config(text=f"{int(self.zoom * 100)}%")
        self._cellpx_lbl.config(text=f"{self.cell_px}px")
        self._dim_var.set(f"Grille : {self.cols}×{self.rows} cases  |  "
                          f"↑↓ taille  ←→ offset")
        if self.tool == "resize_map":
            self._draw_map_handles()

    def _fog_dirty_update(self, cells: list):
        """Dirty-patch fog + composite throttlé à ~60 fps."""
        self._patch_fog_cells(cells)
        self._schedule_tile_refresh()

    def _flush_render(self):
        self._pending_render = None
        self._bg_pil  = None
        self._fog_pil = None
        self._obs_pil = None
        self._composite()
        # En mode vue joueurs (fenêtre principale), recalculer la visibilité
        # des tokens ennemis après chaque changement de fog.
        if not getattr(self, "_dm_view", True):
            self._redraw_all_tokens()

    def _schedule_tile_refresh(self, delay: int = 16):
        """Planifie un re-rendu de la tuile visible (throttlé)."""
        if self._pending_render is not None:
            self.win.after_cancel(self._pending_render)
        self._pending_render = self.win.after(delay, self._flush_render)