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
_C_GRID = (50, 50, 90, 160)
# Fog — vue MJ (semi-transparent : voir la carte sous le brouillard)
_C_FOG_DM     = (20, 20, 60, 100)    # bleuté translucide
# Fog — vue joueur (opaque)
_C_FOG_PLAYER = (8, 8, 18, 240)
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
    "hero":    {"fill": (26,  58, 106), "outline": (91, 164, 245), "shape": "circle"},
    "monster": {"fill": (90,  10,  10), "outline": (224, 64,  64), "shape": "diamond"},
    "trap":    {"fill": (74,  48,   0), "outline": (240, 176, 48), "shape": "triangle"},
}

def _rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb[:3])


# ─── Classe principale ────────────────────────────────────────────────────────

class CombatMapWindow:

    def __init__(self, parent, win_state=None, save_fn=None, track_fn=None,
                 msg_queue=None, inject_fn=None):
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

        # Fenêtre Vue Joueurs (Toplevel séparé, fog opaque)
        self._player_win: "PlayerMapView | None" = None

        # ── État carte ────────────────────────────────────────────────────────
        self.zoom    = 1.0
        self.cols    = 30
        self.rows    = 20
        self.tokens: list = []

        # Taille de case en px (modifiable au clavier)
        self.cell_px = CELL_PX_DEFAULT

        # Taille fixe de l'image de fond en pixels (indépendante de cell_px).
        # Initialisée à la taille de la grille par défaut ; modifiable via
        # "Redimensionner" ou chargement d'une carte. Shift+↑/↓ ne la modifie PAS.
        self.map_w = self.cols * CELL_PX_DEFAULT
        self.map_h = self.rows * CELL_PX_DEFAULT

        # Décalage pixel de l'IMAGE DE FOND uniquement (flèches clavier)
        # La grille reste fixe à (0,0) sur le canvas.
        self.map_ox = 0
        self.map_oy = 0

        # Vue : True = MJ (fog transparent), False = Joueur (fog opaque)
        self._dm_view = True

        # Fog : matrice bool numpy [rows, cols] — True = couvert
        self._fog: "np.ndarray" = np.ones((self.rows, self.cols), dtype=bool)

        # Buffers PIL (reconstruits au zoom/resize seulement)
        self._bg_pil:  "Image.Image | None" = None
        self._fog_pil: "Image.Image | None" = None
        self._scene_photo = None
        self._img_id      = 0

        # Cache image de fond
        self.map_image_path  = ""
        self._map_pil_cache: "Image.Image | None" = None
        self._map_path_cached = ""

        # ── État des outils ───────────────────────────────────────────────────
        self.tool           = "reveal"
        self.brush_size     = 2
        self.token_type     = "hero"
        self._show_grid     = True
        self._drag_token    = None
        self._drag_offset   = (0.0, 0.0)
        self._last_fog_cell = None
        self._pending_render = None

        # ── Outil redimensionnement carte ─────────────────────────────────────
        # _map_resize_handle : "nw"|"n"|"ne"|"e"|"se"|"s"|"sw"|"w"|"move"|None
        self._map_resize_handle: str | None = None
        self._map_resize_start: dict | None = None   # snapshot au début du drag
        self._map_handle_ids: list = []              # canvas item ids des poignées
        self._lock_ratio: bool = False               # Shift = verrouiller ratio

        # Charger état sauvegardé
        self._load_from_saved(self.win_state.get("combat_map_data", {}))
        self._build_window()

    # ─── Persistance ──────────────────────────────────────────────────────────

    def _load_from_saved(self, data: dict):
        self.cols    = data.get("cols", self.cols)
        self.rows    = data.get("rows", self.rows)
        self.cell_px = data.get("cell_px", self.cell_px)
        self.map_w   = data.get("map_w", self.cols * self.cell_px)
        self.map_h   = data.get("map_h", self.rows * self.cell_px)
        self.map_ox  = data.get("map_ox", 0)
        self.map_oy  = data.get("map_oy", 0)

        self._fog = np.ones((self.rows, self.cols), dtype=bool)
        fog_list  = data.get("fog")
        if fog_list is not None:
            self._fog[:] = False
            for cell in fog_list:
                c, r = int(cell[0]), int(cell[1])
                if 0 <= r < self.rows and 0 <= c < self.cols:
                    self._fog[r, c] = True

        for t in data.get("tokens", []):
            self.tokens.append({k: v for k, v in t.items() if k != "ids"})

        p = data.get("map_image_path", "")
        if p and os.path.exists(p):
            self.map_image_path = p

    def _save_state(self):
        rows_idx, cols_idx = np.where(self._fog)
        self.win_state["combat_map_data"] = {
            "cols":           self.cols,
            "rows":           self.rows,
            "cell_px":        self.cell_px,
            "map_w":          self.map_w,
            "map_h":          self.map_h,
            "map_ox":         self.map_ox,
            "map_oy":         self.map_oy,
            "fog":            [[int(c), int(r)] for r, c in zip(rows_idx, cols_idx)],
            "tokens":         [{k: v for k, v in t.items() if k != "ids"}
                               for t in self.tokens],
            "map_image_path": self.map_image_path,
        }
        self.save_fn()

    # ─── Fenêtre ──────────────────────────────────────────────────────────────

    def _build_window(self):
        self.win = tk.Toplevel(self.parent)
        self.win.title("Carte de Combat")
        self.win.configure(bg=BG_WIN)
        self.win.minsize(600, 450)
        self.track_fn("combat_map", self.win)
        if "combat_map" not in self.win_state:
            self.win.geometry("1020x720")
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_toolbar()
        self._build_canvas_area()
        self._build_statusbar()
        self._set_tool("reveal")
        self.win.after(80, self._full_redraw)

    # ─── Toolbar ──────────────────────────────────────────────────────────────

    def _build_toolbar(self):
        tb = tk.Frame(self.win, bg=BG_TOOL, pady=5, padx=6)
        tb.pack(fill=tk.X, side=tk.TOP)

        tk.Label(tb, text="CARTE DE COMBAT", bg=BG_TOOL, fg="#6666aa",
                 font=("Consolas", 8, "bold")).pack(side=tk.LEFT, padx=(4, 12))

        # ── Outils fog ───────────────────────────────────────────────────────
        self._tool_btns = {}
        for key, label, fg_on, bg_on in [
            ("select",     "↖  Sélect.",    "#aaaaff", "#1e1e44"),
            ("reveal",     "◎  Révéler",    "#81c784", "#0e2c1a"),
            ("hide",       "●  Cacher",     "#e57373", "#2c0e0e"),
            ("add",        "+  Token",      "#64b5f6", "#0e1e2c"),
            ("resize_map", "⤢  Carte",      "#ffb74d", "#2c1a00"),
        ]:
            btn = tk.Button(
                tb, text=label, bg="#252538", fg="#aaaacc",
                font=("Consolas", 9, "bold"), relief="flat",
                padx=10, pady=5, cursor="hand2",
                activebackground=bg_on, activeforeground=fg_on,
                command=lambda k=key: self._set_tool(k))
            btn.pack(side=tk.LEFT, padx=2)
            self._tool_btns[key] = (btn, fg_on, bg_on)

        _sep(tb)

        # ── Pinceau ───────────────────────────────────────────────────────────
        tk.Label(tb, text="Rayon :", bg=BG_TOOL, fg="#9999bb",
                 font=("Consolas", 9)).pack(side=tk.LEFT, padx=(4, 2))
        self._brush_var = tk.IntVar(value=self.brush_size)
        tk.Spinbox(
            tb, from_=1, to=10, textvariable=self._brush_var, width=3,
            bg="#252538", fg="#ccccee", font=("Consolas", 10),
            buttonbackground="#2e2e4a", relief="flat",
            command=lambda: setattr(self, "brush_size", self._brush_var.get()),
        ).pack(side=tk.LEFT, padx=2)

        _sep(tb)

        # ── Type token ────────────────────────────────────────────────────────
        tk.Label(tb, text="Token :", bg=BG_TOOL, fg="#9999bb",
                 font=("Consolas", 9)).pack(side=tk.LEFT, padx=(4, 2))
        self._tok_var = tk.StringVar(value="hero")
        for ttype, col in [("hero", "#5ba4f5"), ("monster", "#e04040"), ("trap", "#f0b030")]:
            tk.Radiobutton(
                tb, text=ttype.capitalize(), variable=self._tok_var, value=ttype,
                bg=BG_TOOL, fg=col, selectcolor="#1a1a2e",
                activebackground=BG_TOOL, font=("Consolas", 9),
                command=lambda t=ttype: setattr(self, "token_type", t),
            ).pack(side=tk.LEFT, padx=2)

        _sep(tb)

        # ── Ratio carte (visible uniquement en mode resize_map) ───────────────
        self._ratio_var = tk.BooleanVar(value=False)
        self._ratio_chk = tk.Checkbutton(
            tb, text="⇔ Ratio", variable=self._ratio_var, bg=BG_TOOL, fg="#ffb74d",
            selectcolor="#2c1a00", activebackground=BG_TOOL, font=("Consolas", 9),
            command=lambda: setattr(self, "_lock_ratio", self._ratio_var.get()))
        # Affiché seulement en mode resize_map (pack/forget dynamique)
        self._ratio_chk_visible = False

        # ── Actions carte ─────────────────────────────────────────────────────
        for text, fg, bg, cmd in [
            ("Charger carte",  "#64b5f6", "#0e1e30", self._load_map_image),
            ("Tout révéler",   "#81c784", "#0e2010", self._reveal_all),
            ("Tout cacher",    "#e57373", "#20100e", self._cover_all),
            ("Redimensionner", "#9b8fc7", "#1a1020", self._resize_grid),
        ]:
            tk.Button(
                tb, text=text, bg="#252538", fg=fg,
                font=("Consolas", 8), relief="flat", padx=7, pady=4,
                activebackground=bg, activeforeground=fg,
                command=cmd,
            ).pack(side=tk.LEFT, padx=2)

        # ── Vue MJ / Joueur ───────────────────────────────────────────────────
        self._view_btn = tk.Button(
            tb, text="Vue MJ", bg="#2a1a3a", fg="#c77dff",
            font=("Consolas", 8, "bold"), relief="sunken", padx=8, pady=4,
            command=self._toggle_dm_view)
        self._view_btn.pack(side=tk.LEFT, padx=2)

        # ── Fenêtre joueurs ────────────────────────────────────────────────────
        tk.Button(
            tb, text="Ecran Joueurs", bg="#1a2a3a", fg="#64b5f6",
            font=("Consolas", 8, "bold"), relief="flat", padx=8, pady=4,
            activebackground="#0e1e2c", activeforeground="#90caf9",
            command=self._open_player_view,
        ).pack(side=tk.LEFT, padx=2)

        # ── Injection agents ──────────────────────────────────────────────────
        tk.Button(
            tb, text="→ Agents", bg="#1a2a1a", fg="#81c784",
            font=("Consolas", 8, "bold"), relief="flat", padx=8, pady=4,
            activebackground="#0e2010", activeforeground="#a5d6a7",
            command=self._send_to_agents,
        ).pack(side=tk.LEFT, padx=2)

        # ── Grille ────────────────────────────────────────────────────────────
        self._grid_btn = tk.Button(
            tb, text="Grille ON", bg="#252538", fg="#9999bb",
            font=("Consolas", 8), relief="flat", padx=7, pady=4,
            command=self._toggle_grid)
        self._grid_btn.pack(side=tk.LEFT, padx=2)

        # ── Taille case ───────────────────────────────────────────────────────
        _sep(tb)
        tk.Label(tb, text="Case :", bg=BG_TOOL, fg="#9999bb",
                 font=("Consolas", 8)).pack(side=tk.LEFT, padx=(4, 2))
        self._cellpx_lbl = tk.Label(tb, text=f"{self.cell_px}px", bg=BG_TOOL,
                                    fg="#ccccee", font=("Consolas", 8, "bold"), width=5)
        self._cellpx_lbl.pack(side=tk.LEFT)

        # ── Zoom ─────────────────────────────────────────────────────────────
        self._zoom_lbl = tk.Label(tb, text="100%", bg=BG_TOOL, fg="#8888bb",
                                  font=("Consolas", 9), width=6)
        self._zoom_lbl.pack(side=tk.RIGHT, padx=(0, 10))
        tk.Label(tb, text="Zoom:", bg=BG_TOOL, fg="#7777aa",
                 font=("Consolas", 8)).pack(side=tk.RIGHT)

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
        v_sb.config(command=self.canvas.yview)
        h_sb.config(command=self.canvas.xview)

        # Souris
        self.canvas.bind("<ButtonPress-1>",    self._mb1_down)
        self.canvas.bind("<B1-Motion>",         self._mb1_move)
        self.canvas.bind("<ButtonRelease-1>",   self._mb1_up)
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

    # ─── Propriétés calculées ─────────────────────────────────────────────────

    @property
    def _cp(self) -> int:
        """Pixels par case au zoom courant."""
        return max(2, int(self.cell_px * self.zoom))

    @property
    def _wh(self) -> tuple:
        cp = self._cp
        return self.cols * cp, self.rows * cp

    @property
    def _fog_color(self) -> tuple:
        """Couleur du fog selon la vue active."""
        return _C_FOG_DM if self._dm_view else _C_FOG_PLAYER

    # ─── Rendu offscreen PIL ──────────────────────────────────────────────────

    def _rebuild_bg(self):
        """Couche fond : damier + image + grille. Mise en cache au zoom/resize."""
        cp   = self._cp
        W, H = self._wh

        # Damier vectorisé numpy
        ri  = np.arange(H) // cp
        ci  = np.arange(W) // cp
        chk = (ri[:, None] + ci[None, :]) % 2
        arr = np.where(chk[:, :, None] == 0,
                       np.array(_C_BG_A, dtype=np.uint8),
                       np.array(_C_BG_B, dtype=np.uint8))
        bg = Image.fromarray(arr.astype(np.uint8), "RGBA")

        # Image de fond (carte) — décalée par map_ox/map_oy
        # La carte est rendue à sa taille fixe (map_w×map_h × zoom),
        # indépendamment de cell_px. Seule la grille suit cell_px.
        if self.map_image_path and os.path.exists(self.map_image_path):
            try:
                if self._map_path_cached != self.map_image_path:
                    self._map_pil_cache   = Image.open(self.map_image_path).convert("RGBA")
                    self._map_path_cached = self.map_image_path
                # Taille de l'image de fond = map_w × map_h (zoom appliqué)
                mw = max(1, int(self.map_w * self.zoom))
                mh = max(1, int(self.map_h * self.zoom))
                map_img = self._map_pil_cache.resize((mw, mh), Image.LANCZOS)
                # Crée un canvas transparent de la taille exacte du buffer,
                # puis colle l'image à l'offset voulu (la grille restera par-dessus)
                map_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                map_layer.paste(map_img, (self.map_ox, self.map_oy))
                bg = Image.alpha_composite(bg, map_layer)
            except Exception as e:
                print(f"[CombatMap] image fond : {e}")

        # Grille
        if self._show_grid and cp >= 4:
            bg_arr = np.array(bg, dtype=np.float32)
            gc     = np.array(_C_GRID[:3], dtype=np.float32)
            ga     = _C_GRID[3] / 255.0
            for c in range(self.cols + 1):
                x = min(c * cp, W - 1)
                bg_arr[:, x, :3] = ga * gc + (1 - ga) * bg_arr[:, x, :3]
            for r in range(self.rows + 1):
                y = min(r * cp, H - 1)
                bg_arr[y, :, :3] = ga * gc + (1 - ga) * bg_arr[y, :, :3]
            bg_arr[:, :, 3] = 255
            bg = Image.fromarray(bg_arr.astype(np.uint8), "RGBA")

        self._bg_pil = bg

    def _rebuild_fog(self):
        """Construit _fog_pil entier depuis self._fog + _fog_color courant."""
        cp   = self._cp
        W, H = self._wh
        fog_px = np.repeat(np.repeat(self._fog, cp, axis=0), cp, axis=1)
        arr    = np.zeros((H, W, 4), dtype=np.uint8)
        arr[fog_px] = self._fog_color
        self._fog_pil = Image.fromarray(arr, "RGBA")

    def _patch_fog_cells(self, cells: list):
        """Dirty-patch : met à jour uniquement les cases listées."""
        if self._fog_pil is None:
            self._rebuild_fog()
            return
        cp         = self._cp
        fog_tile   = Image.new("RGBA", (cp, cp), self._fog_color)
        clear_tile = Image.new("RGBA", (cp, cp), _C_FOG_CLEAR)
        for (c, r) in cells:
            if not (0 <= r < self.rows and 0 <= c < self.cols):
                continue
            tile = fog_tile if self._fog[r, c] else clear_tile
            self._fog_pil.paste(tile, (c * cp, r * cp))

    def _composite(self):
        """alpha_composite(bg, fog) → PhotoImage → 1 canvas item."""
        if self._bg_pil is None:
            self._rebuild_bg()
        if self._fog_pil is None:
            self._rebuild_fog()

        scene = Image.alpha_composite(self._bg_pil, self._fog_pil)
        self._scene_photo = ImageTk.PhotoImage(scene)

        W, H = self._wh
        self.canvas.config(scrollregion=(0, 0, W + 40, H + 40))
        if self._img_id:
            self.canvas.itemconfig(self._img_id, image=self._scene_photo)
            self.canvas.coords(self._img_id, 0, 0)
        else:
            self._img_id = self.canvas.create_image(
                0, 0, anchor="nw", image=self._scene_photo, tags=("scene",))
        self.canvas.tag_raise("token")
        # Mise à jour auto de la vue joueurs si elle est ouverte
        if self._player_win is not None:
            try:
                self._player_win.refresh(self._bg_pil, self._fog, self._cp,
                                         self.cols, self.rows, self.tokens)
            except Exception:
                self._player_win = None

    # ── Entrées publiques rendu ───────────────────────────────────────────────

    def _full_redraw(self):
        """Reconstruction complète (zoom, grille, taille case)."""
        self._bg_pil  = None
        self._fog_pil = None
        self._img_id  = 0
        self.canvas.delete("scene")
        self._rebuild_bg()
        self._rebuild_fog()
        self._composite()
        self._redraw_all_tokens()
        self._zoom_lbl.config(text=f"{int(self.zoom * 100)}%")
        self._cellpx_lbl.config(text=f"{self.cell_px}px")
        self._dim_var.set(f"Grille : {self.cols}×{self.rows} cases  |  "
                          f"↑↓ taille  ←→ offset")
        if self.tool == "resize_map":
            self._draw_map_handles()

    def _fog_dirty_update(self, cells: list):
        """Dirty-patch fog + composite throttlé à ~60 fps."""
        self._patch_fog_cells(cells)
        if self._pending_render is not None:
            self.win.after_cancel(self._pending_render)
        self._pending_render = self.win.after(16, self._flush_render)

    def _flush_render(self):
        self._pending_render = None
        self._composite()

    # ─── Tokens ───────────────────────────────────────────────────────────────

    def _redraw_all_tokens(self):
        self.canvas.delete("token")
        for tok in self.tokens:
            tok.pop("ids", None)
            self._draw_one_token(tok)

    def _draw_one_token(self, tok: dict):
        style = TOKEN_STYLES.get(tok["type"], TOKEN_STYLES["hero"])
        cp    = self._cp
        # Tokens positionnés sur la grille (toujours à 0,0 sur le canvas)
        cx    = (tok["col"] + 0.5) * cp
        cy    = (tok["row"] + 0.5) * cp
        rad   = cp * 0.40
        name  = tok.get("name", "")

        fill_rgb = (HERO_COLORS.get(name, style["fill"])
                    if tok["type"] == "hero" else style["fill"])
        fill    = _rgb_to_hex(fill_rgb)
        outline = _rgb_to_hex(style["outline"])
        tag     = f"tok_{id(tok)}"
        ids     = []

        ids.append(self.canvas.create_oval(
            cx-rad-3, cy-rad-3, cx+rad+3, cy+rad+3,
            outline=outline, width=1, fill="", tags=("token", tag)))

        sh = style.get("shape", "circle")
        if sh == "circle":
            ids.append(self.canvas.create_oval(
                cx-rad, cy-rad, cx+rad, cy+rad,
                fill=fill, outline=outline, width=2, tags=("token", tag)))
        elif sh == "diamond":
            pts = [cx, cy-rad, cx+rad, cy, cx, cy+rad, cx-rad, cy]
            ids.append(self.canvas.create_polygon(
                pts, fill=fill, outline=outline, width=2, tags=("token", tag)))
        else:  # triangle piège
            pts = [cx, cy-rad, cx+rad*0.88, cy+rad*0.75, cx-rad*0.88, cy+rad*0.75]
            ids.append(self.canvas.create_polygon(
                pts, fill=fill, outline=outline, width=2, tags=("token", tag)))

        fs = max(7, int(10 * self.zoom))
        ids.append(self.canvas.create_text(
            cx, cy, text=(name[:3] if name else tok["type"][:1].upper()),
            fill="white", font=("Consolas", fs, "bold"), tags=("token", tag)))

        if self.zoom >= 0.65 and name:
            ids.append(self.canvas.create_text(
                cx, cy + rad + 2, text=name, fill=outline,
                font=("Consolas", max(6, int(7 * self.zoom))),
                anchor="n", tags=("token", tag)))

        tok["ids"] = tuple(ids)
        for iid in ids:
            self.canvas.tag_bind(iid, "<ButtonPress-1>",
                                  lambda e, t=tok: self._tok_press(e, t))
            self.canvas.tag_bind(iid, "<B1-Motion>",
                                  lambda e, t=tok: self._tok_drag(e, t))
            self.canvas.tag_bind(iid, "<ButtonRelease-1>",
                                  lambda e, t=tok: self._tok_release(e, t))

    def _redraw_one_token(self, tok: dict):
        for iid in tok.get("ids", ()):
            self.canvas.delete(iid)
        tok.pop("ids", None)
        self._draw_one_token(tok)

    # ─── Outils ───────────────────────────────────────────────────────────────

    def _set_tool(self, tool: str):
        prev_tool = self.tool
        self.tool = tool
        cursors  = {"select": "arrow", "reveal": "dotbox", "hide": "dot",
                    "add": "plus", "resize_map": "fleur"}
        statuses = {
            "select":     "Sélection — glisser les tokens | clic droit = supprimer",
            "reveal":     "Révéler — cliquer/glisser pour lever le brouillard",
            "hide":       "Cacher   — cliquer/glisser pour poser le brouillard",
            "add":        "Token    — cliquer sur une case pour placer un token",
            "resize_map": "Carte — glisser une poignée pour redimensionner | "
                          "glisser le centre pour déplacer | Shift = ratio fixe",
        }
        self.canvas.config(cursor=cursors.get(tool, "crosshair"))
        self._status_var.set(statuses.get(tool, ""))
        for key, (btn, fg_on, bg_on) in self._tool_btns.items():
            if key == tool:
                btn.config(bg=bg_on, fg=fg_on, relief="sunken")
            else:
                btn.config(bg="#252538", fg="#aaaacc", relief="flat")

        # Affiche/masque le checkbox ratio et les poignées
        if tool == "resize_map":
            if not self._ratio_chk_visible:
                self._ratio_chk.pack(side=tk.LEFT, padx=4)
                self._ratio_chk_visible = True
            self._draw_map_handles()
        else:
            if self._ratio_chk_visible:
                self._ratio_chk.pack_forget()
                self._ratio_chk_visible = False
            if prev_tool == "resize_map":
                self._clear_map_handles()


    def _canvas_xy(self, event):
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    def _canvas_to_cell(self, cx, cy):
        """Convertit des coords canvas en (col, row). La grille est fixe à (0,0)."""
        cp = self._cp
        return int(cx / cp), int(cy / cp)

    def _brush_cells(self, col, row) -> list:
        r = max(0, self._brush_var.get())
        cells = []
        for dc in range(-r, r+1):
            for dr in range(-r, r+1):
                if dc*dc + dr*dr <= r*r:
                    c2, r2 = col+dc, row+dr
                    if 0 <= c2 < self.cols and 0 <= r2 < self.rows:
                        cells.append((c2, r2))
        return cells or [(col, row)]

    def _apply_fog_at(self, cx, cy):
        col, row = self._canvas_to_cell(cx, cy)
        pos = (col, row)
        if pos == self._last_fog_cell:
            return
        self._last_fog_cell = pos
        cells     = self._brush_cells(col, row)
        is_reveal = (self.tool == "reveal")
        for (c, r) in cells:
            self._fog[r, c] = not is_reveal
        self._fog_dirty_update(cells)

    # ─── Événements souris ────────────────────────────────────────────────────

    def _mb1_down(self, event):
        cx, cy = self._canvas_xy(event)
        self._last_fog_cell = None
        if self.tool == "resize_map":
            self._map_resize_begin(cx, cy, event)
        elif self.tool == "add":
            self._add_token(cx, cy)
        elif self.tool in ("reveal", "hide"):
            self._apply_fog_at(cx, cy)

    def _mb1_move(self, event):
        cx, cy = self._canvas_xy(event)
        if self.tool == "resize_map":
            self._map_resize_drag(cx, cy, event)
        elif self.tool in ("reveal", "hide"):
            self._apply_fog_at(cx, cy)

    def _mb1_up(self, event):
        self._last_fog_cell = None
        if self.tool == "resize_map":
            self._map_resize_end()
        elif self.tool in ("reveal", "hide"):
            if self._pending_render is not None:
                self.win.after_cancel(self._pending_render)
                self._pending_render = None
            self._composite()
            self._save_state()

    def _mb3_down(self, event):
        cx, cy = self._canvas_xy(event)
        items = self.canvas.find_overlapping(cx-8, cy-8, cx+8, cy+8)
        for iid in items:
            if "token" in self.canvas.gettags(iid):
                for tok in self.tokens:
                    if iid in tok.get("ids", ()):
                        for tid in tok["ids"]:
                            self.canvas.delete(tid)
                        self.tokens.remove(tok)
                        self._save_state()
                        return

    def _mouse_move(self, event):
        cx, cy = self._canvas_xy(event)
        col, row = self._canvas_to_cell(cx, cy)
        if 0 <= col < self.cols and 0 <= row < self.rows:
            self._pos_var.set(f"Col {col+1} / Lig {row+1}")
        else:
            self._pos_var.set("")
        # Curseur adaptatif en mode resize_map
        if self.tool == "resize_map" and self._map_resize_handle is None:
            handle = self._hit_test_handle(cx, cy)
            cursor_map = {
                "nw": "top_left_corner",  "n": "top_side",
                "ne": "top_right_corner", "e": "right_side",
                "se": "bottom_right_corner", "s": "bottom_side",
                "sw": "bottom_left_corner",  "w": "left_side",
                "move": "fleur",
                None: "fleur",
            }
            self.canvas.config(cursor=cursor_map.get(handle, "fleur"))

    def _pan_start(self, event):
        self.canvas.scan_mark(event.x, event.y)

    def _pan_drag(self, event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _do_zoom(self, event):
        factor = 1.18 if (event.num == 4 or getattr(event, "delta", 0) > 0) else 1/1.18
        new_zoom = max(0.25, min(4.0, self.zoom * factor))
        if abs(new_zoom - self.zoom) < 0.001:
            return
        self.zoom = new_zoom
        self._full_redraw()

    # ─── Outil redimensionnement carte (poignées drag) ───────────────────────

    _HANDLE_SIZE = 8   # demi-côté de la poignée en px canvas

    def _map_rect_canvas(self) -> tuple:
        """Retourne (x0, y0, x1, y1) du rectangle de l'image en coordonnées canvas."""
        z = self.zoom
        x0 = int(self.map_ox * z) if False else self.map_ox   # map_ox est déjà en px canvas
        y0 = self.map_oy
        x1 = x0 + int(self.map_w * self.zoom)
        y1 = y0 + int(self.map_h * self.zoom)
        return x0, y0, x1, y1

    def _draw_map_handles(self):
        """Dessine les 8 poignées + contour autour de l'image de fond."""
        self._clear_map_handles()
        if not self.map_image_path:
            return
        x0, y0, x1, y1 = self._map_rect_canvas()
        H = self._HANDLE_SIZE

        # Contour pointillé
        iid = self.canvas.create_rectangle(
            x0, y0, x1, y1,
            outline="#ffb74d", width=1, dash=(6, 4), tags="map_handle")
        self._map_handle_ids.append(iid)

        # 8 poignées : (handle_key, cx, cy)
        mx, my = (x0 + x1) // 2, (y0 + y1) // 2
        handle_pos = [
            ("nw", x0, y0), ("n",  mx, y0), ("ne", x1, y0),
            ("w",  x0, my),                  ("e",  x1, my),
            ("sw", x0, y1), ("s",  mx, y1), ("se", x1, y1),
        ]
        for hkey, hx, hy in handle_pos:
            fill = "#ffb74d" if hkey in ("nw", "ne", "se", "sw") else "#cc8830"
            iid = self.canvas.create_rectangle(
                hx - H, hy - H, hx + H, hy + H,
                fill=fill, outline="#ffe0a0", width=1, tags=("map_handle", f"mh_{hkey}"))
            self._map_handle_ids.append(iid)

        # Label dimensions
        lbl = f"{self.map_w}×{self.map_h}px"
        iid = self.canvas.create_text(
            x0 + 4, y0 - 10, text=lbl, anchor="sw",
            fill="#ffb74d", font=("Consolas", 8), tags="map_handle")
        self._map_handle_ids.append(iid)

        self.canvas.tag_raise("map_handle")
        self.canvas.tag_raise("token")

    def _clear_map_handles(self):
        for iid in self._map_handle_ids:
            self.canvas.delete(iid)
        self._map_handle_ids.clear()

    def _hit_test_handle(self, cx: float, cy: float) -> str | None:
        """Retourne la clé de la poignée sous le curseur, ou 'move' si dans la carte."""
        if not self.map_image_path:
            return None
        x0, y0, x1, y1 = self._map_rect_canvas()
        H = self._HANDLE_SIZE + 4   # zone de détection un peu plus large

        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        handle_pos = [
            ("nw", x0, y0), ("n",  mx, y0), ("ne", x1, y0),
            ("w",  x0, my),                  ("e",  x1, my),
            ("sw", x0, y1), ("s",  mx, y1), ("se", x1, y1),
        ]
        for hkey, hx, hy in handle_pos:
            if abs(cx - hx) <= H and abs(cy - hy) <= H:
                return hkey

        # Clic dans le corps de l'image → déplacer
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            return "move"
        return None

    def _map_resize_begin(self, cx: float, cy: float, event):
        handle = self._hit_test_handle(cx, cy)
        if handle is None:
            self._map_resize_handle = None
            return
        self._map_resize_handle = handle
        self._lock_ratio = self._ratio_var.get() or bool(event.state & 0x0001)  # Shift
        x0, y0, x1, y1 = self._map_rect_canvas()
        self._map_resize_start = {
            "cx": cx, "cy": cy,
            "map_ox": self.map_ox, "map_oy": self.map_oy,
            "map_w":  self.map_w,  "map_h":  self.map_h,
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
        }

    def _map_resize_drag(self, cx: float, cy: float, event):
        if self._map_resize_handle is None or self._map_resize_start is None:
            return
        s = self._map_resize_start
        dx = cx - s["cx"]
        dy = cy - s["cy"]
        z  = self.zoom
        lock = self._lock_ratio or bool(event.state & 0x0001)

        ox, oy = s["map_ox"], s["map_oy"]
        mw, mh = s["map_w"],  s["map_h"]
        # Taille originale pour ratio
        orig_ratio = mw / mh if mh else 1.0

        handle = self._map_resize_handle

        if handle == "move":
            # Déplacer l'image entière
            self.map_ox = int(ox + dx)
            self.map_oy = int(oy + dy)

        else:
            # Convertir delta canvas → delta espace-image (dé-zoomer)
            ddx = dx / z
            ddy = dy / z

            new_ox, new_oy = ox, oy
            new_w,  new_h  = mw, mh

            if "w" in handle:   # bord gauche : tire l'origine + réduit largeur
                delta_w = -ddx
                new_w  = max(20, mw + delta_w)
                new_ox = ox - int((new_w - mw) * z)
            if "e" in handle:   # bord droit : étire la largeur
                new_w  = max(20, mw + ddx)
            if "n" in handle:   # bord haut
                delta_h = -ddy
                new_h  = max(20, mh + delta_h)
                new_oy = oy - int((new_h - mh) * z)
            if "s" in handle:   # bord bas
                new_h  = max(20, mh + ddy)

            if lock and new_w != mw:
                # Ratio basé sur axe dominant
                if abs(new_w - mw) >= abs(new_h - mh):
                    new_h = new_w / orig_ratio
                    if "n" in handle:
                        new_oy = oy - int((new_h - mh) * z)
                else:
                    new_w = new_h * orig_ratio
                    if "w" in handle:
                        new_ox = ox - int((new_w - mw) * z)
            elif lock and new_h != mh:
                new_w = new_h * orig_ratio
                if "w" in handle:
                    new_ox = ox - int((new_w - mw) * z)

            self.map_w  = max(20, int(new_w))
            self.map_h  = max(20, int(new_h))
            self.map_ox = int(new_ox)
            self.map_oy = int(new_oy)

        # Throttled redraw
        self._bg_pil = None
        if self._pending_render is not None:
            self.win.after_cancel(self._pending_render)
        self._pending_render = self.win.after(20, self._flush_map_resize)

    def _flush_map_resize(self):
        self._pending_render = None
        self._rebuild_bg()
        self._composite()
        self._draw_map_handles()

    def _map_resize_end(self):
        if self._map_resize_handle is not None:
            self._map_resize_handle = None
            self._map_resize_start  = None
            self._rebuild_bg()
            self._composite()
            self._draw_map_handles()
            self._save_state()

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
        """Shift+↑/↓ : change la taille de case de 1 px. Déclenche un full redraw."""
        new_size = max(CELL_PX_MIN, min(CELL_PX_MAX, self.cell_px + delta))
        if new_size == self.cell_px:
            return
        self.cell_px = new_size
        self._bg_pil  = None   # invalide le cache fond
        self._fog_pil = None
        self._full_redraw()

    # ─── Drag tokens ─────────────────────────────────────────────────────────

    def _tok_press(self, event, tok):
        if self.tool != "select":
            return
        self._drag_token = tok
        cx, cy = self._canvas_xy(event)
        cp = self._cp
        self._drag_offset = (cx - (tok["col"]+0.5)*cp,
                             cy - (tok["row"]+0.5)*cp)

    def _tok_drag(self, event, tok):
        if self._drag_token is not tok:
            return
        cx, cy = self._canvas_xy(event)
        cp = self._cp
        tok["col"] = max(0.0, min(self.cols-1.0,
                                   (cx - self._drag_offset[0]) / cp - 0.5))
        tok["row"] = max(0.0, min(self.rows-1.0,
                                   (cy - self._drag_offset[1]) / cp - 0.5))
        self._redraw_one_token(tok)

    def _tok_release(self, event, tok):
        if self._drag_token is not tok:
            return
        tok["col"] = round(max(0, min(self.cols-1, tok["col"])))
        tok["row"] = round(max(0, min(self.rows-1, tok["row"])))
        self._redraw_one_token(tok)
        self._drag_token = None
        self._save_state()

    # ─── Actions toolbar ─────────────────────────────────────────────────────

    def _toggle_dm_view(self):
        """Bascule entre vue MJ (fog transparent) et vue Joueur (fog opaque)."""
        self._dm_view = not self._dm_view
        if self._dm_view:
            self._view_btn.config(text="Vue MJ",     bg="#2a1a3a",
                                   fg="#c77dff", relief="sunken")
        else:
            self._view_btn.config(text="Vue Joueur", bg="#1a1a2a",
                                   fg="#8888aa", relief="flat")
        # Reconstruit le fog avec la nouvelle couleur
        self._rebuild_fog()
        self._composite()

    def _toggle_grid(self):
        self._show_grid = not self._show_grid
        self._grid_btn.config(
            text="Grille ON" if self._show_grid else "Grille OFF",
            fg="#9999bb"     if self._show_grid else "#555577")
        self._full_redraw()

    def _add_token(self, cx, cy):
        col, row = self._canvas_to_cell(cx, cy)
        if not (0 <= col < self.cols and 0 <= row < self.rows):
            return
        ttype    = self.token_type
        existing = [t for t in self.tokens if t["type"] == ttype]
        if ttype == "hero":
            used    = {t["name"] for t in self.tokens if t["type"] == "hero"}
            avail   = [n for n in HERO_NAMES if n not in used]
            default = avail[0] if avail else f"Héros {len(existing)+1}"
        elif ttype == "monster":
            default = f"Monstre {len(existing)+1}"
        else:
            default = f"Piège {len(existing)+1}"
        name = simpledialog.askstring(
            "Nom du token", f"Nom du {ttype} :", initialvalue=default, parent=self.win)
        if name is None:
            return
        tok = {"type": ttype, "name": name, "col": col, "row": row}
        self.tokens.append(tok)
        self._draw_one_token(tok)
        self._save_state()

    def _load_map_image(self):
        path = filedialog.askopenfilename(
            parent=self.win, title="Charger une carte",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("Tous", "*.*")])
        if path:
            self.map_image_path   = path
            self._map_pil_cache   = None
            self._map_path_cached = ""
            # Initialise la taille à celle de l'image native (plafonnée si très grande),
            # pour préserver les proportions originales par défaut.
            try:
                with Image.open(path) as _img:
                    iw, ih = _img.size
                # Mise à l'échelle si l'image est plus grande que la grille × 4
                max_dim = max(self.cols, self.rows) * self.cell_px * 4
                scale   = min(1.0, max_dim / max(iw, ih))
                self.map_w = max(20, int(iw * scale))
                self.map_h = max(20, int(ih * scale))
            except Exception:
                self.map_w = self.cols * self.cell_px
                self.map_h = self.rows * self.cell_px
            self.map_ox = 0
            self.map_oy = 0
            self._full_redraw()
            self._save_state()
            # Bascule automatiquement sur l'outil de redimensionnement
            self._set_tool("resize_map")

    def _reveal_all(self):
        self._fog[:] = False
        self._rebuild_fog()
        self._composite()
        self._save_state()

    def _cover_all(self):
        self._fog[:] = True
        self._rebuild_fog()
        self._composite()
        self._save_state()

    def _resize_grid(self):
        cols = simpledialog.askinteger("Colonnes", "Colonnes (5–60) :",
            initialvalue=self.cols, minvalue=5, maxvalue=60, parent=self.win)
        if cols is None:
            return
        rows = simpledialog.askinteger("Lignes", "Lignes (5–40) :",
            initialvalue=self.rows, minvalue=5, maxvalue=40, parent=self.win)
        if rows is None:
            return
        old_fog = self._fog.copy()
        new_fog = np.ones((rows, cols), dtype=bool)
        rr = min(rows, self.rows)
        cc = min(cols, self.cols)
        new_fog[:rr, :cc] = old_fog[:rr, :cc]
        # Met à jour map_w/map_h proportionnellement au nouveau nombre de cases
        self.map_w = int(self.map_w * cols / self.cols) if self.cols else cols * self.cell_px
        self.map_h = int(self.map_h * rows / self.rows) if self.rows else rows * self.cell_px
        self.cols, self.rows = cols, rows
        self._fog   = new_fog
        self.tokens = [t for t in self.tokens
                       if 0 <= t["col"] < cols and 0 <= t["row"] < rows]
        self._full_redraw()
        self._save_state()

    def _open_player_view(self):
        """Ouvre (ou ramène) la fenêtre Vue Joueurs avec fog opaque."""
        if self._player_win is not None:
            try:
                self._player_win.win.deiconify()
                self._player_win.win.lift()
                # Rafraîchit au cas où le fog a changé depuis
                self._player_win.refresh(self._bg_pil, self._fog, self._cp,
                                         self.cols, self.rows, self.tokens)
                return
            except Exception:
                self._player_win = None

        self._player_win = PlayerMapView(
            parent   = self.win,
            on_close = lambda: setattr(self, "_player_win", None),
        )
        # Rendu initial
        if self._bg_pil is not None:
            self._player_win.refresh(self._bg_pil, self._fog, self._cp,
                                     self.cols, self.rows, self.tokens)

    def _send_to_agents(self):
        """Génère une description textuelle + image de la carte et l'injecte aux agents."""
        if self.inject_fn is None and self.msg_queue is None:
            messagebox.showinfo(
                "Agents non disponibles",
                "La carte de combat n'est pas connectée aux agents.\n"
                "Lancez la partie d'abord.",
                parent=self.win)
            return

        desc = self._build_map_description()

        # Sauvegarde l'image vue-joueurs dans un fichier tmp
        img_path = ""
        try:
            player_img = self._render_player_image()
            fd, img_path = tempfile.mkstemp(suffix=".png", prefix="combat_map_")
            os.close(fd)
            player_img.save(img_path, "PNG")
            desc += f"\n[Image carte sauvegardée : {img_path}]"
        except Exception as e:
            print(f"[CombatMap] export image : {e}")

        # Affichage dans le chat
        if self.msg_queue is not None:
            self.msg_queue.put({
                "sender": "Carte de Combat",
                "text":   desc,
                "color":  "#64b5f6",
            })

        # Injection dans autogen (text uniquement — compatible tous modèles)
        if self.inject_fn is not None:
            self.inject_fn(desc)

    def _build_map_description(self) -> str:
        """Construit une description textuelle de la carte visible par les joueurs."""
        cp = self._cp
        total = self.cols * self.rows
        hidden = int(self._fog.sum())
        visible = total - hidden

        lines = [
            "═══ CARTE DE COMBAT ═══",
            f"Grille : {self.cols}×{self.rows} cases  |  "
            f"{visible}/{total} cases visibles  |  {hidden} sous brouillard",
            "",
        ]

        # Tokens visibles (cases non couvertes)
        visible_tokens = []
        hidden_tokens  = []
        for tok in self.tokens:
            c, r = int(tok["col"]), int(tok["row"])
            label = f"{tok['name']} ({tok['type']}) → Col {c+1}, Lig {r+1}"
            if 0 <= r < self.rows and 0 <= c < self.cols and not self._fog[r, c]:
                visible_tokens.append(label)
            else:
                hidden_tokens.append(label)

        if visible_tokens:
            lines.append("Tokens visibles :")
            for t in visible_tokens:
                lines.append(f"  • {t}")
        else:
            lines.append("Aucun token visible (tout est sous brouillard).")

        if hidden_tokens:
            lines.append("Tokens sous brouillard (positions inconnues des joueurs) :")
            for t in hidden_tokens:
                lines.append(f"  ? {t}")

        lines.append("")
        lines.append("Zones révélées (colonnes × lignes, numérotation 1-based) :")

        # Résumé des blocs révélés par ligne
        revealed_rows = []
        for r in range(self.rows):
            revealed_cols = [c+1 for c in range(self.cols) if not self._fog[r, c]]
            if revealed_cols:
                # Compresse en plages
                ranges = _compress_ranges(revealed_cols)
                revealed_rows.append(f"  Lig {r+1} : colonnes {ranges}")
        if revealed_rows:
            lines.extend(revealed_rows[:20])  # max 20 lignes pour ne pas saturer
            if len(revealed_rows) > 20:
                lines.append(f"  … ({len(revealed_rows) - 20} lignes supplémentaires)")
        else:
            lines.append("  (aucune case révélée)")

        return "\n".join(lines)

    def _render_player_image(self) -> "Image.Image":
        """Rend la carte avec fog opaque (vue joueurs) sans modifier l'état courant."""
        if self._bg_pil is None:
            self._rebuild_bg()

        # Fog opaque temporaire
        cp   = self._cp
        W, H = self._wh
        fog_px = np.repeat(np.repeat(self._fog, cp, axis=0), cp, axis=1)
        arr    = np.zeros((H, W, 4), dtype=np.uint8)
        arr[fog_px] = _C_FOG_PLAYER
        fog_opaque = Image.fromarray(arr, "RGBA")

        return Image.alpha_composite(self._bg_pil, fog_opaque)

    def _on_close(self):
        self._save_state()
        self.win.destroy()


# ─── Utilitaires ─────────────────────────────────────────────────────────────

def _sep(parent):
    tk.Frame(parent, bg="#3a3a55", width=1, height=26).pack(
        side=tk.LEFT, padx=6, pady=2)

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


# ─── Fenêtre Vue Joueurs ──────────────────────────────────────────────────────

class PlayerMapView:
    """Fenêtre secondaire lecture-seule : carte avec fog opaque pour les joueurs."""

    def __init__(self, parent, on_close=None):
        self._on_close_cb = on_close
        self._photo       = None

        self.win = tk.Toplevel(parent)
        self.win.title("Vue Joueurs — Carte de Combat")
        self.win.configure(bg="#0a0a14")
        self.win.geometry("900x640")
        self.win.protocol("WM_DELETE_WINDOW", self._close)

        # En-tête
        hdr = tk.Frame(self.win, bg="#0a0a14", pady=6)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="VUE JOUEURS", bg="#0a0a14", fg="#e57373",
                 font=("Consolas", 9, "bold")).pack(side=tk.LEFT, padx=12)
        tk.Label(hdr, text="lecture seule  —  fog opaque",
                 bg="#0a0a14", fg="#333355", font=("Consolas", 8)).pack(side=tk.LEFT)

        # Canvas avec scrollbars
        frame = tk.Frame(self.win, bg="#0a0a14")
        frame.pack(fill=tk.BOTH, expand=True)
        v_sb = tk.Scrollbar(frame, orient=tk.VERTICAL,   bg="#0f0f1a", troughcolor="#0a0a14")
        h_sb = tk.Scrollbar(frame, orient=tk.HORIZONTAL, bg="#0f0f1a", troughcolor="#0a0a14")
        v_sb.pack(side=tk.RIGHT, fill=tk.Y)
        h_sb.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas = tk.Canvas(frame, bg="#0a0a14", highlightthickness=0,
                                yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        v_sb.config(command=self.canvas.yview)
        h_sb.config(command=self.canvas.xview)

        self._img_id = 0
        self._tok_drawn = []

    def refresh(self, bg_pil, fog: "np.ndarray", cp: int,
                cols: int, rows: int, tokens: list,
                ox: int = 0, oy: int = 0):
        """Reçoit les données du MJ et re-rend la vue joueurs (fog opaque)."""
        if bg_pil is None:
            return

        # Fog opaque
        W, H   = cols * cp, rows * cp
        fog_px = np.repeat(np.repeat(fog, cp, axis=0), cp, axis=1)
        arr    = np.zeros((H, W, 4), dtype=np.uint8)
        arr[fog_px] = _C_FOG_PLAYER
        fog_opaque = Image.fromarray(arr, "RGBA")

        scene = Image.alpha_composite(bg_pil, fog_opaque)
        self._photo = ImageTk.PhotoImage(scene)

        self.canvas.config(scrollregion=(
            min(0, ox), min(0, oy),
            W + max(0, ox) + 40, H + max(0, oy) + 40))

        if self._img_id:
            self.canvas.itemconfig(self._img_id, image=self._photo)
            self.canvas.coords(self._img_id, ox, oy)
        else:
            self._img_id = self.canvas.create_image(
                ox, oy, anchor="nw", image=self._photo, tags=("scene",))

        # Tokens — seulement ceux sur cases révélées
        for iid in self._tok_drawn:
            self.canvas.delete(iid)
        self._tok_drawn.clear()

        for tok in tokens:
            c, r = int(tok["col"]), int(tok["row"])
            if 0 <= r < rows and 0 <= c < cols and not fog[r, c]:
                self._draw_token(tok, cp, ox, oy)

        self.canvas.tag_raise("ptok")

    def _draw_token(self, tok: dict, cp: int, ox: int, oy: int):
        style = TOKEN_STYLES.get(tok["type"], TOKEN_STYLES["hero"])
        cx    = (tok["col"] + 0.5) * cp + ox
        cy    = (tok["row"] + 0.5) * cp + oy
        rad   = cp * 0.40
        name  = tok.get("name", "")

        fill_rgb = (HERO_COLORS.get(name, style["fill"])
                    if tok["type"] == "hero" else style["fill"])
        fill    = _rgb_to_hex(fill_rgb)
        outline = _rgb_to_hex(style["outline"])

        sh = style.get("shape", "circle")
        if sh == "circle":
            iid = self.canvas.create_oval(
                cx-rad, cy-rad, cx+rad, cy+rad,
                fill=fill, outline=outline, width=2, tags="ptok")
        elif sh == "diamond":
            pts = [cx, cy-rad, cx+rad, cy, cx, cy+rad, cx-rad, cy]
            iid = self.canvas.create_polygon(
                pts, fill=fill, outline=outline, width=2, tags="ptok")
        else:
            pts = [cx, cy-rad, cx+rad*0.88, cy+rad*0.75, cx-rad*0.88, cy+rad*0.75]
            iid = self.canvas.create_polygon(
                pts, fill=fill, outline=outline, width=2, tags="ptok")

        self._tok_drawn.append(iid)
        tid = self.canvas.create_text(
            cx, cy, text=(name[:3] if name else tok["type"][:1].upper()),
            fill="white", font=("Consolas", max(7, int(10 * cp / 44)), "bold"),
            tags="ptok")
        self._tok_drawn.append(tid)

        if cp >= 30 and name:
            nlbl = self.canvas.create_text(
                cx, cy + rad + 2, text=name, fill=outline,
                font=("Consolas", max(6, int(7 * cp / 44))),
                anchor="n", tags="ptok")
            self._tok_drawn.append(nlbl)

    def _close(self):
        if self._on_close_cb:
            self._on_close_cb()
        self.win.destroy()


# ─── Point d'entrée ───────────────────────────────────────────────────────────

def open_combat_map(parent, win_state: dict, save_fn, track_fn,
                    msg_queue=None, inject_fn=None) -> CombatMapWindow:
    return CombatMapWindow(parent, win_state=win_state,
                           save_fn=save_fn, track_fn=track_fn,
                           msg_queue=msg_queue, inject_fn=inject_fn)