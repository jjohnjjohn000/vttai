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

        # Charger état sauvegardé
        self._load_from_saved(self.win_state.get("combat_map_data", {}))
        self._build_window()

    # ─── Système de calques ───────────────────────────────────────────────────

    def _ensure_default_layer(self):
        if not self.map_layers:
            self.map_layers.append({
                "name": "Calque 1",
                "path": "",
                "w": self.cols * self.cell_px,
                "h": self.rows * self.cell_px,
                "ox": 0, "oy": 0,
                "visible": True,
            })

    @property
    def _active_layer(self) -> dict:
        self._ensure_default_layer()
        idx = max(0, min(self._active_layer_idx, len(self.map_layers) - 1))
        self._active_layer_idx = idx
        return self.map_layers[idx]

    @property
    def map_image_path(self) -> str:
        return self._active_layer.get("path", "")
    @map_image_path.setter
    def map_image_path(self, v: str):
        self._active_layer["path"] = v

    @property
    def map_w(self) -> int:
        return self._active_layer.get("w", self.cols * self.cell_px)
    @map_w.setter
    def map_w(self, v: int):
        self._active_layer["w"] = v

    @property
    def map_h(self) -> int:
        return self._active_layer.get("h", self.rows * self.cell_px)
    @map_h.setter
    def map_h(self, v: int):
        self._active_layer["h"] = v

    @property
    def map_ox(self) -> int:
        return self._active_layer.get("ox", 0)
    @map_ox.setter
    def map_ox(self, v: int):
        self._active_layer["ox"] = v

    @property
    def map_oy(self) -> int:
        return self._active_layer.get("oy", 0)
    @map_oy.setter
    def map_oy(self, v: int):
        self._active_layer["oy"] = v

    # ─── Persistance ──────────────────────────────────────────────────────────

    def _load_from_saved(self, data: dict):
        self.cols    = data.get("cols", self.cols)
        self.rows    = data.get("rows", self.rows)
        self.cell_px = data.get("cell_px", self.cell_px)

        if "map_layers" in data:
            self.map_layers = []
            for l in data["map_layers"]:
                self.map_layers.append({
                    "name":    l.get("name", "Calque"),
                    "path":    l.get("path", ""),
                    "w":       l.get("w", self.cols * self.cell_px),
                    "h":       l.get("h", self.rows * self.cell_px),
                    "ox":      l.get("ox", 0),
                    "oy":      l.get("oy", 0),
                    "visible": l.get("visible", True),
                })
            self._active_layer_idx = data.get("active_layer_idx", 0)
        else:
            # Rétrocompatibilité : ancien format champ unique
            self._ensure_default_layer()
            self.map_layers[0]["w"]  = data.get("map_w", self.cols * self.cell_px)
            self.map_layers[0]["h"]  = data.get("map_h", self.rows * self.cell_px)
            self.map_layers[0]["ox"] = data.get("map_ox", 0)
            self.map_layers[0]["oy"] = data.get("map_oy", 0)
            p = data.get("map_image_path", "")
            if p and os.path.exists(p):
                self.map_layers[0]["path"] = p
            self._active_layer_idx = 0

        # ── Vue (zoom + position de scroll) ──────────────────────────────────
        self.zoom        = float(data.get("zoom",     1.0))
        self._scroll_fx  = float(data.get("scroll_x", 0.0))   # fraction xview à restaurer
        self._scroll_fy  = float(data.get("scroll_y", 0.0))   # fraction yview à restaurer

        # ── Fog mask (résolution pixel = cols*cell_px × rows*cell_px) ─────────
        mw, mh = self.cols * self.cell_px, self.rows * self.cell_px
        fog_b64 = data.get("fog_mask_b64")
        if fog_b64:
            import base64, io as _io
            raw = base64.b64decode(fog_b64)
            img = Image.open(_io.BytesIO(raw)).convert("L")
            if img.size != (mw, mh):
                img = img.resize((mw, mh), Image.NEAREST)
            self._fog_mask = img
        else:
            # Rétro-compatibilité : ancien format liste de cases
            self._fog_mask = Image.new("L", (mw, mh), 255)   # tout couvert
            fog_list = data.get("fog")
            if fog_list is not None:
                from PIL import ImageDraw as _ID
                draw = _ID.Draw(self._fog_mask)
                # Révéler tout, puis recouvrir les cases listées
                draw.rectangle([0, 0, mw - 1, mh - 1], fill=0)
                for cell in fog_list:
                    c, r = int(cell[0]), int(cell[1])
                    if 0 <= r < self.rows and 0 <= c < self.cols:
                        x0 = c * self.cell_px
                        y0 = r * self.cell_px
                        draw.rectangle(
                            [x0, y0, x0 + self.cell_px - 1, y0 + self.cell_px - 1],
                            fill=255)

        for t in data.get("tokens", []):
            self.tokens.append({k: v for k, v in t.items() if k != "ids"})

        for n in data.get("notes", []):
            self._notes.append({
                "px":   float(n.get("px", 0)),
                "py":   float(n.get("py", 0)),
                "text": n.get("text", ""),
                "color": n.get("color", "#ffe082"),
                "canvas_ids": [],
            })

        for d in data.get("doors", []):
            self._doors.append({
                "col":   int(d.get("col", 0)),
                "row":   int(d.get("row", 0)),
                "open":  bool(d.get("open", False)),
                "label": d.get("label", ""),
                "canvas_ids": [],
            })

    def _save_state(self):
        import base64, io as _io
        fog_b64 = ""
        if self._fog_mask is not None:
            buf = _io.BytesIO()
            self._fog_mask.save(buf, "PNG")
            fog_b64 = base64.b64encode(buf.getvalue()).decode()
        # Fractions de scroll courantes (canvas peut ne pas exister encore)
        try:
            scroll_x = self.canvas.xview()[0]
            scroll_y = self.canvas.yview()[0]
        except Exception:
            scroll_x = getattr(self, "_scroll_fx", 0.0)
            scroll_y = getattr(self, "_scroll_fy", 0.0)

        self.win_state["combat_map_data"] = {
            "cols":             self.cols,
            "rows":             self.rows,
            "cell_px":          self.cell_px,
            "zoom":             self.zoom,
            "scroll_x":         scroll_x,
            "scroll_y":         scroll_y,
            "fog_mask_b64":     fog_b64,
            "tokens":           [{k: v for k, v in t.items() if k != "ids"}
                                 for t in self.tokens],
            "map_layers":       [{"name": l["name"], "path": l["path"],
                                  "w": l["w"], "h": l["h"],
                                  "ox": l["ox"], "oy": l["oy"],
                                  "visible": l["visible"]}
                                 for l in self.map_layers],
            "active_layer_idx": self._active_layer_idx,
            "notes":            [{"px": n["px"], "py": n["py"],
                                  "text": n["text"], "color": n["color"]}
                                 for n in self._notes],
            "doors":            [{"col": d["col"], "row": d["row"],
                                  "open": d["open"], "label": d["label"]}
                                 for d in self._doors],
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
        self._build_layer_panel()
        self._build_canvas_area()
        self._build_statusbar()
        self._set_tool("reveal")
        self.win.after(80, self._full_redraw)
        # Restaurer zoom + scroll après que le canvas soit rendu et stable
        self.win.after(160, self._restore_view)

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
            ("note",       "📌 Note",       "#ffe082", "#2a2500"),
            ("door",       "[P] Porte",    "#ff9966", "#2c1200"),
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
            ("+ Calque",       "#64b5f6", "#0e1e30", self._add_map_layer),
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

    # ─── Panneau calques (barre latérale gauche) ──────────────────────────────

    def _build_layer_panel(self):
        panel = tk.Frame(self.win, bg="#0f0f1e", width=170)
        panel.pack(side=tk.LEFT, fill=tk.Y)
        panel.pack_propagate(False)
        self._layer_panel = panel
        tk.Label(panel, text="CALQUES", bg="#0f0f1e", fg="#5555aa",
                 font=("Consolas", 7, "bold")).pack(fill=tk.X, padx=4, pady=(6, 2))
        scroll_frame = tk.Frame(panel, bg="#0f0f1e")
        scroll_frame.pack(fill=tk.BOTH, expand=True)
        self._layer_scroll = scroll_frame
        btn_frame = tk.Frame(panel, bg="#0f0f1e")
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=4)
        tk.Button(btn_frame, text="+ Calque", bg="#0e1e30", fg="#64b5f6",
                  font=("Consolas", 8), relief="flat", padx=6, pady=3,
                  activebackground="#1a2a40", activeforeground="#90caf9",
                  command=self._add_map_layer,
                  ).pack(side=tk.LEFT, padx=3, expand=True, fill=tk.X)
        tk.Button(btn_frame, text="✕", bg="#200a0a", fg="#e57373",
                  font=("Consolas", 8), relief="flat", padx=6, pady=3,
                  activebackground="#3a1010", activeforeground="#ef9a9a",
                  command=self._remove_active_layer,
                  ).pack(side=tk.RIGHT, padx=3)
        self._refresh_layer_panel()

    def _refresh_layer_panel(self):
        for w in self._layer_scroll.winfo_children():
            w.destroy()
        for idx, layer in enumerate(self.map_layers):
            is_active = (idx == self._active_layer_idx)
            row_bg = "#1a1a34" if is_active else "#111120"
            row = tk.Frame(self._layer_scroll, bg=row_bg, pady=2)
            row.pack(fill=tk.X, padx=2, pady=1)
            vis_sym = "👁" if layer.get("visible", True) else "🚫"
            tk.Button(row, text=vis_sym, bg=row_bg, fg="#aaaacc",
                      font=("Consolas", 9), relief="flat", padx=3, pady=0,
                      activebackground=row_bg,
                      command=lambda i=idx: self._toggle_layer_visibility(i),
                      ).pack(side=tk.LEFT, padx=(2, 0))
            name    = layer.get("name", f"Calque {idx+1}")
            has_img = bool(layer.get("path") and os.path.exists(layer.get("path", "")))
            lbl_fg  = "#e0e0ff" if is_active else "#9090bb"
            lbl_sym = "🗺" if has_img else "☐"
            tk.Button(row, text=f"{lbl_sym} {name}", bg=row_bg, fg=lbl_fg,
                      font=("Consolas", 8, "bold" if is_active else "normal"),
                      relief="flat", anchor="w", padx=4, pady=2,
                      activebackground="#252550", activeforeground="#ffffff",
                      command=lambda i=idx: self._activate_layer(i),
                      ).pack(side=tk.LEFT, fill=tk.X, expand=True)
            tk.Button(row, text="📁", bg=row_bg, fg="#64b5f6",
                      font=("Consolas", 9), relief="flat", padx=3, pady=0,
                      activebackground="#0e1e30",
                      command=lambda i=idx: self._load_layer_image(i),
                      ).pack(side=tk.RIGHT, padx=(0, 2))
            tk.Button(row, text="✏", bg=row_bg, fg="#ffb74d",
                      font=("Consolas", 9), relief="flat", padx=3, pady=0,
                      activebackground="#2c1a00",
                      command=lambda i=idx: self._rename_layer(i),
                      ).pack(side=tk.RIGHT)

    # ─── Actions calques ──────────────────────────────────────────────────────

    def _activate_layer(self, idx: int):
        self._active_layer_idx = idx
        self._bg_pil = None
        self._refresh_layer_panel()
        self._full_redraw()

    def _toggle_layer_visibility(self, idx: int):
        self.map_layers[idx]["visible"] = not self.map_layers[idx].get("visible", True)
        self._bg_pil = None
        self._refresh_layer_panel()
        self._full_redraw()
        self._save_state()

    def _add_map_layer(self):
        n = len(self.map_layers) + 1
        self.map_layers.append({
            "name": f"Calque {n}", "path": "",
            "w": self.cols * self.cell_px, "h": self.rows * self.cell_px,
            "ox": 0, "oy": 0, "visible": True,
        })
        self._active_layer_idx = len(self.map_layers) - 1
        self._refresh_layer_panel()
        self._load_layer_image(self._active_layer_idx)

    def _remove_active_layer(self):
        if len(self.map_layers) <= 1:
            messagebox.showinfo("Calques", "Il faut au moins un calque.", parent=self.win)
            return
        name = self.map_layers[self._active_layer_idx].get("name", "?")
        if not messagebox.askyesno("Supprimer calque", f"Supprimer « {name} » ?", parent=self.win):
            return
        self.map_layers.pop(self._active_layer_idx)
        self._active_layer_idx = max(0, self._active_layer_idx - 1)
        self._bg_pil = None
        self._refresh_layer_panel()
        self._full_redraw()
        self._save_state()

    def _rename_layer(self, idx: int):
        current = self.map_layers[idx].get("name", f"Calque {idx+1}")
        new_name = simpledialog.askstring("Renommer calque", "Nom du calque :",
                                          initialvalue=current, parent=self.win)
        if new_name and new_name.strip():
            self.map_layers[idx]["name"] = new_name.strip()
            self._refresh_layer_panel()
            self._save_state()

    def _load_layer_image(self, idx: int):
        path = filedialog.askopenfilename(
            parent=self.win, title=f"Image — {self.map_layers[idx].get('name','Calque')}",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("Tous", "*.*")])
        if not path:
            if not self.map_layers[idx].get("path") and len(self.map_layers) > 1:
                self.map_layers.pop(idx)
                self._active_layer_idx = max(0, idx - 1)
                self._refresh_layer_panel()
            return
        old_path = self.map_layers[idx].get("path", "")
        if old_path and old_path != path and old_path in self._map_pil_cache_dict:
            del self._map_pil_cache_dict[old_path]
        self.map_layers[idx]["path"] = path
        try:
            with Image.open(path) as _img:
                iw, ih = _img.size
            max_dim = max(self.cols, self.rows) * self.cell_px * 4
            scale   = min(1.0, max_dim / max(iw, ih))
            self.map_layers[idx]["w"] = max(20, int(iw * scale))
            self.map_layers[idx]["h"] = max(20, int(ih * scale))
        except Exception:
            self.map_layers[idx]["w"] = self.cols * self.cell_px
            self.map_layers[idx]["h"] = self.rows * self.cell_px
        self.map_layers[idx]["ox"] = 0
        self.map_layers[idx]["oy"] = 0
        self._active_layer_idx = idx
        self._bg_pil = None
        self._refresh_layer_panel()
        self._full_redraw()
        self._save_state()
        self._set_tool("resize_map")

    def _load_map_image(self):
        """Compatibilité — délègue vers le calque actif."""
        self._load_layer_image(self._active_layer_idx)

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
        self.win.bind("<Escape>",      lambda e: self._poly_cancel())

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
        """Taille logique complète de la carte (scrollregion)."""
        cp = self._cp
        return self.cols * cp, self.rows * cp

    @property
    def _fog_color(self) -> tuple:
        """Couleur du fog selon la vue active."""
        return _C_FOG_DM if self._dm_view else _C_FOG_PLAYER

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

        # ── Grille (seulement les lignes qui croisent la tuile) ───────────────
        if self._show_grid and cp >= 4:
            bg_arr = np.array(bg, dtype=np.float32)
            gc = np.array(_C_GRID[:3], dtype=np.float32)
            ga = _C_GRID[3] / 255.0
            # Colonnes
            c0 = tx0 // cp
            c1 = tx1 // cp + 1
            for c in range(c0, c1 + 1):
                x = c * cp - tx0
                if 0 <= x < TW:
                    bg_arr[:, x, :3] = ga * gc + (1 - ga) * bg_arr[:, x, :3]
            # Rangées
            r0 = ty0 // cp
            r1 = ty1 // cp + 1
            for r in range(r0, r1 + 1):
                y = r * cp - ty0
                if 0 <= y < TH:
                    bg_arr[y, :, :3] = ga * gc + (1 - ga) * bg_arr[y, :, :3]
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
        """alpha_composite(tuile_bg, tuile_fog) → PhotoImage placé aux coords canvas."""
        if self._bg_pil is None:
            self._rebuild_bg()
        if self._fog_pil is None:
            self._rebuild_fog()

        scene = Image.alpha_composite(self._bg_pil, self._fog_pil)
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
        # Vue joueurs
        if self._player_win is not None:
            try:
                self._player_win.refresh(self._bg_pil, self._fog_mask, self._cp,
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
        self._bg_pil  = None   # force re-crop de la tuile visible
        self._fog_pil = None
        self._composite()

    def _schedule_tile_refresh(self, delay: int = 16):
        """Planifie un re-rendu de la tuile visible (throttlé)."""
        if self._pending_render is not None:
            self.win.after_cancel(self._pending_render)
        self._pending_render = self.win.after(delay, self._flush_render)

    # ─── Tokens ───────────────────────────────────────────────────────────────

    def _redraw_all_tokens(self):
        self.canvas.delete("token")
        for tok in self.tokens:
            tok.pop("ids", None)
            self._draw_one_token(tok)

    def _draw_one_token(self, tok: dict):
        style = TOKEN_STYLES.get(tok["type"], TOKEN_STYLES["hero"])
        cp    = self._cp
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

        # Anneau de sélection (blanc pointillé si sélectionné)
        sel_col = "#ffffff" if id(tok) in self._selected_tokens else ""
        ids.append(self.canvas.create_oval(
            cx-rad-5, cy-rad-5, cx+rad+5, cy+rad+5,
            outline=sel_col, width=2, fill="", dash=(4, 3),
            tags=("token", "sel_ring", tag)))

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
        else:
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
        # Motion + release gérés au niveau canvas (_mb1_move/_mb1_up)
        for iid in ids:
            self.canvas.tag_bind(iid, "<ButtonPress-1>",
                                  lambda e, t=tok: self._tok_press(e, t))

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
                    "add": "plus", "note": "pencil", "resize_map": "fleur",
                    "door": "hand2"}
        statuses = {
            "select":     "Sélection — glisser les tokens | clic droit = supprimer",
            "reveal":     "Révéler — clic gauche : sommet | clic droit : appliquer | Échap : annuler",
            "hide":       "Cacher   — clic gauche : sommet | clic droit : appliquer | Échap : annuler",
            "add":        "Token    — cliquer sur une case pour placer un token",
            "note":       "Note     — clic gauche : placer un post-it | glisser une note : déplacer | double-clic : éditer | clic droit : supprimer",
            "door":       "Porte    — clic gauche : placer/basculer ouverte|fermée | clic droit : supprimer",
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

        # Annuler polygone en cours si on change d'outil
        if prev_tool in ("reveal", "hide") and tool not in ("reveal", "hide"):
            self._poly_cancel()

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
        """Kept for compat — polygon is the only fog tool."""
        pass

    # ─── Outil polygone (reveal / hide) ──────────────────────────────────────

    def _poly_add_point(self, cx: float, cy: float):
        pts = self._poly_points
        col = "#81c784" if self.tool == "reveal" else "#e57373"
        if pts:
            x0, y0 = pts[-1]
            self._poly_ids.append(self.canvas.create_line(
                x0, y0, cx, cy, fill=col, width=1, tags="poly_preview"))
        r = 3
        self._poly_ids.append(self.canvas.create_rectangle(
            cx-r, cy-r, cx+r, cy+r,
            outline=col, fill="#1a1a1a", width=1, tags="poly_preview"))
        pts.append((cx, cy))
        self._poly_update_preview(cx, cy)

    def _poly_update_preview(self, cx: float, cy: float):
        self.canvas.delete("poly_preview_cursor")
        pts = self._poly_points
        if not pts:
            return
        col = "#81c784" if self.tool == "reveal" else "#e57373"
        x0, y0 = pts[-1]
        self.canvas.create_line(x0, y0, cx, cy,
            fill=col, width=1, dash=(4, 4),
            tags=("poly_preview", "poly_preview_cursor"))
        if len(pts) >= 2:
            x1, y1 = pts[0]
            self.canvas.create_line(cx, cy, x1, y1,
                fill=col, width=1, dash=(2, 6),
                tags=("poly_preview", "poly_preview_cursor"))

    def _poly_cancel(self):
        self.canvas.delete("poly_preview")
        self._poly_points.clear()
        self._poly_ids.clear()

    def _poly_apply(self):
        from PIL import ImageDraw as _ID
        pts = self._poly_points
        if len(pts) < 3:
            self._poly_cancel()
            return
        cp  = self._cp
        mw  = self.cols * self.cell_px
        mh  = self.rows * self.cell_px
        if self._fog_mask is None:
            self._fog_mask = Image.new("L", (mw, mh), 255)
        inv    = self.cell_px / cp
        scaled = [(cx * inv, cy * inv) for cx, cy in pts]
        fill   = 255 if self.tool == "hide" else 0
        _ID.Draw(self._fog_mask).polygon(scaled, fill=fill)
        self._poly_cancel()
        self._fog_pil = None
        self._rebuild_fog()
        self._composite()
        self._save_state()

    # ─── Événements souris ────────────────────────────────────────────────────

    def _mb1_down(self, event):
        cx, cy = self._canvas_xy(event)
        self._last_fog_cell = None
        if self.tool == "resize_map":
            self._map_resize_begin(cx, cy, event)
        elif self.tool == "add":
            self._add_token(cx, cy)
        elif self.tool in ("reveal", "hide"):
            self._poly_add_point(cx, cy)
        elif self.tool == "note":
            # Débuter drag si on clique sur une note existante, sinon créer
            hit = self._note_at(cx, cy)
            if hit is not None:
                self._drag_note = hit
                self._drag_note_off = (cx - hit["px"] * self.zoom,
                                       cy - hit["py"] * self.zoom)
            # sinon : on attend le release (pas de drag) pour créer
        elif self.tool == "door":
            col, row = self._canvas_to_cell(cx, cy)
            self._door_toggle_or_create(col, row)
        elif self.tool == "select":
            if self._drag_token is None:
                self._box_select_begin(cx, cy)

    def _mb1_move(self, event):
        cx, cy = self._canvas_xy(event)
        if self._drag_note is not None:
            # Déplacer la note en temps réel
            n = self._drag_note
            n["px"] = (cx - self._drag_note_off[0]) / self.zoom
            n["py"] = (cy - self._drag_note_off[1]) / self.zoom
            self._redraw_one_note(n)
        elif self._drag_token is not None:
            self._tok_drag(event, self._drag_token)
        elif self._box_select_start is not None:
            self._box_select_update(cx, cy)
        elif self.tool == "resize_map":
            self._map_resize_drag(cx, cy, event)

    def _mb1_up(self, event):
        cx, cy = self._canvas_xy(event)
        self._last_fog_cell = None
        if self._drag_note is not None:
            # Snap léger vers grille si très proche d'un bord de case
            self._save_state()
            self._drag_note = None
            self._drag_note_off = (0.0, 0.0)
        elif self.tool == "note" and self._drag_note is None:
            # Pas de drag → créer une nouvelle note
            if not self._note_at(cx, cy):
                self._create_note(cx, cy)
        elif self._drag_token is not None:
            self._tok_release(event, self._drag_token)
        elif self._box_select_start is not None:
            shift = bool(event.state & 0x0001)
            self._box_select_end(cx, cy, shift)
        elif self.tool == "resize_map":
            self._map_resize_end()

    def _mb3_down(self, event):
        cx, cy = self._canvas_xy(event)
        if self.tool in ("reveal", "hide"):
            self._poly_apply()
            return
        # Clic droit sur une porte → la supprimer
        col, row = self._canvas_to_cell(cx, cy)
        door_hit = self._door_at(col, row)
        if door_hit is not None:
            self._delete_door(door_hit)
            return
        # Clic droit sur une note → la supprimer
        hit = self._note_at(cx, cy)
        if hit is not None:
            self._delete_note(hit)
            return
        # Clic droit sur un token → le supprimer
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
        # Prévisualisation polygone
        if self.tool in ("reveal", "hide") and self._poly_points:
            self._poly_update_preview(cx, cy)
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
        self._schedule_tile_refresh(delay=30)
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _do_zoom(self, event):
        factor = 1.10 if (event.num == 4 or getattr(event, "delta", 0) > 0) else 1/1.10
        new_zoom = max(0.25, min(4.0, self.zoom * factor))
        if abs(new_zoom - self.zoom) < 0.001:
            return

        # Coord canvas du point sous le curseur AVANT zoom
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)

        # Mémoriser l'ancre au début de la séquence (premier tick de molette)
        if self._zoom_rebuild_pending is None:
            self._zoom_anchor_world_x = cx / self.zoom   # coord monde normalisée
            self._zoom_anchor_world_y = cy / self.zoom
            self._zoom_anchor_ex = event.x
            self._zoom_anchor_ey = event.y

        self.zoom = new_zoom
        self._zoom_lbl.config(text=f"{int(self.zoom * 100)}%")

        # Throttle : on annule le rebuild précédent et on en replanifie un
        # dans 16 ms (~60 fps). La molette peut envoyer des événements plus vite
        # que ça — on saute les intermédiaires, on ne garde que le dernier.
        if self._zoom_rebuild_pending is not None:
            self.win.after_cancel(self._zoom_rebuild_pending)
        self._zoom_rebuild_pending = self.win.after(16, self._zoom_rebuild_final)

    def _zoom_rebuild_final(self):
        """Rebuild PIL au zoom courant, ancré sur le point mémorisé sous le curseur."""
        self._zoom_rebuild_pending = None

        # ── 1. Repositionner le scroll D'ABORD ───────────────────────────────
        # _visible_rect() lit xview/yview → doit être correct avant le rebuild.
        W, H = self._wh
        sr_w, sr_h = W + 40, H + 40
        self.canvas.config(scrollregion=(0, 0, sr_w, sr_h))

        new_cx = self._zoom_anchor_world_x * self.zoom
        new_cy = self._zoom_anchor_world_y * self.zoom
        fx = max(0.0, min(1.0, (new_cx - self._zoom_anchor_ex) / sr_w))
        fy = max(0.0, min(1.0, (new_cy - self._zoom_anchor_ey) / sr_h))
        self.canvas.xview_moveto(fx)
        self.canvas.yview_moveto(fy)
        self.canvas.update_idletasks()   # flush → xview() à jour pour _visible_rect

        # ── 2. Rebuild de la tuile visible ────────────────────────────────────
        self._bg_pil  = None
        self._fog_pil = None
        self._img_id  = 0
        self.canvas.delete("scene")
        self._rebuild_bg()
        self._rebuild_fog()
        self._redraw_all_doors()
        self._composite()
        self._redraw_all_tokens()
        self._redraw_all_notes()
        if self.tool == "resize_map":
            self._draw_map_handles()
        # Persister le zoom et la position de scroll après stabilisation
        self.win.after(200, self._save_state)

    # ─── Outil redimensionnement carte (poignées drag) ───────────────────────

    _HANDLE_SIZE = 8   # demi-côté de la poignée en px canvas

    def _map_rect_canvas(self) -> tuple:
        """Retourne (x0, y0, x1, y1) du rectangle du calque actif en coordonnées canvas."""
        scale = self._cp / self.cell_px
        layer = self._active_layer
        x0 = int(layer.get("ox", 0) * scale)
        y0 = int(layer.get("oy", 0) * scale)
        x1 = x0 + int(layer.get("w", self.cols * self.cell_px) * scale)
        y1 = y0 + int(layer.get("h", self.rows * self.cell_px) * scale)
        return x0, y0, x1, y1

    def _draw_map_handles(self):
        """Dessine les 8 poignées + contour autour de l'image de fond."""
        self._clear_map_handles()
        if not self.map_image_path:
            return
        x0, y0, x1, y1 = self._map_rect_canvas()
        H = self._HANDLE_SIZE
        iid = self.canvas.create_rectangle(
            x0, y0, x1, y1,
            outline="#ffb74d", width=1, dash=(6, 4), tags="map_handle")
        self._map_handle_ids.append(iid)
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
        layer = self._active_layer
        lname = layer.get("name", "")
        lbl   = f"{layer.get('w', self.map_w)}×{layer.get('h', self.map_h)}px"
        if lname:
            lbl = f"[{lname}]  {lbl}"
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
        self._lock_ratio = self._ratio_var.get() or bool(event.state & 0x0001)
        x0, y0, x1, y1 = self._map_rect_canvas()
        layer = self._active_layer
        self._map_resize_start = {
            "cx": cx, "cy": cy,
            "map_ox": layer.get("ox", 0),  "map_oy": layer.get("oy", 0),
            "map_w":  layer.get("w",  self.cols * self.cell_px),
            "map_h":  layer.get("h",  self.rows * self.cell_px),
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
        }

    def _map_resize_drag(self, cx: float, cy: float, event):
        if self._map_resize_handle is None or self._map_resize_start is None:
            return
        s = self._map_resize_start
        dx = cx - s["cx"]
        dy = cy - s["cy"]
        cp   = self._cp
        inv  = self.cell_px / cp
        lock = self._lock_ratio or bool(event.state & 0x0001)

        ox, oy = s["map_ox"], s["map_oy"]
        mw, mh = s["map_w"],  s["map_h"]
        # Taille originale pour ratio
        orig_ratio = mw / mh if mh else 1.0

        handle = self._map_resize_handle

        if handle == "move":
            self.map_ox = int(ox + dx * inv)
            self.map_oy = int(oy + dy * inv)
        else:
            ddx = dx * inv
            ddy = dy * inv
            new_ox, new_oy = ox, oy
            new_w,  new_h  = mw, mh
            if "w" in handle:
                delta_w = -ddx
                new_w  = max(20, mw + delta_w)
                new_ox = ox - int(new_w - mw)
            if "e" in handle:
                new_w  = max(20, mw + ddx)
            if "n" in handle:
                delta_h = -ddy
                new_h  = max(20, mh + delta_h)
                new_oy = oy - int(new_h - mh)
            if "s" in handle:
                new_h  = max(20, mh + ddy)
            if lock and new_w != mw:
                if abs(new_w - mw) >= abs(new_h - mh):
                    new_h = new_w / orig_ratio
                    if "n" in handle:
                        new_oy = oy - int(new_h - mh)
                else:
                    new_w = new_h * orig_ratio
                    if "w" in handle:
                        new_ox = ox - int(new_w - mw)
            elif lock and new_h != mh:
                new_w = new_h * orig_ratio
                if "w" in handle:
                    new_ox = ox - int(new_w - mw)

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

    # ─── Drag tokens (multi-sélection) ───────────────────────────────────────

    def _tok_press(self, event, tok):
        if self.tool != "select":
            return
        shift = bool(event.state & 0x0001)
        if shift:
            if id(tok) in self._selected_tokens:
                self._selected_tokens.discard(id(tok))
            else:
                self._selected_tokens.add(id(tok))
            self._redraw_one_token(tok)
            return
        if id(tok) not in self._selected_tokens:
            self._clear_selection()
            self._selected_tokens.add(id(tok))
            self._redraw_one_token(tok)
        cx, cy = self._canvas_xy(event)
        cp = self._cp
        self._drag_token  = tok
        self._drag_offset = (cx - (tok["col"] + 0.5) * cp,
                             cy - (tok["row"] + 0.5) * cp)
        self._drag_origins = {
            id(t): (t["col"], t["row"])
            for t in self.tokens if id(t) in self._selected_tokens
        }

    def _tok_drag(self, event, tok):
        if self._drag_token is None:
            return
        cx, cy = self._canvas_xy(event)
        cp = self._cp
        new_col = (cx - self._drag_offset[0]) / cp - 0.5
        new_row = (cy - self._drag_offset[1]) / cp - 0.5
        dcol = new_col - self._drag_origins[id(tok)][0]
        drow = new_row - self._drag_origins[id(tok)][1]
        for t in self.tokens:
            if id(t) not in self._selected_tokens:
                continue
            oc, or_ = self._drag_origins[id(t)]
            t["col"] = max(0.0, min(self.cols - 1.0, oc + dcol))
            t["row"] = max(0.0, min(self.rows - 1.0, or_ + drow))
            self._redraw_one_token(t)

    def _tok_release(self, event, tok):
        if self._drag_token is None:
            return
        for t in self.tokens:
            if id(t) not in self._selected_tokens:
                continue
            t["col"] = round(max(0, min(self.cols - 1, t["col"])))
            t["row"] = round(max(0, min(self.rows - 1, t["row"])))
            self._redraw_one_token(t)
        self._drag_token   = None
        self._drag_origins = {}
        self._save_state()

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
        """Conservé pour compatibilité — délègue vers le calque actif."""
        self._load_layer_image(self._active_layer_idx)

    def _reveal_all(self):
        mw, mh = self.cols * self.cell_px, self.rows * self.cell_px
        self._fog_mask = Image.new("L", (mw, mh), 0)
        self._fog_pil  = None
        self._rebuild_fog()
        self._composite()
        self._save_state()

    def _cover_all(self):
        mw, mh = self.cols * self.cell_px, self.rows * self.cell_px
        self._fog_mask = Image.new("L", (mw, mh), 255)
        self._fog_pil  = None
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

        # Recadre / étend le fog mask
        old_mw = self.cols * self.cell_px
        old_mh = self.rows * self.cell_px
        new_mw = cols * self.cell_px
        new_mh = rows * self.cell_px
        if self._fog_mask is None:
            self._fog_mask = Image.new("L", (old_mw, old_mh), 255)
        new_mask = Image.new("L", (new_mw, new_mh), 255)
        new_mask.paste(self._fog_mask.crop((0, 0,
                                            min(old_mw, new_mw),
                                            min(old_mh, new_mh))), (0, 0))
        self._fog_mask = new_mask

        # map_w/map_h intentionnellement inchangés : ne touche que la grille.
        self.cols, self.rows = cols, rows
        self.tokens = [t for t in self.tokens
                       if 0 <= t["col"] < cols and 0 <= t["row"] < rows]
        self._fog_pil = None
        self._full_redraw()
        self._save_state()

    def _open_player_view(self):
        """Ouvre (ou ramène) la fenêtre Vue Joueurs avec fog opaque."""
        if self._player_win is not None:
            try:
                self._player_win.win.deiconify()
                self._player_win.win.lift()
                # Rafraîchit au cas où le fog a changé depuis
                self._player_win.refresh(self._bg_pil, self._fog_mask, self._cp,
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
            self._player_win.refresh(self._bg_pil, self._fog_mask, self._cp,
                                     self.cols, self.rows, self.tokens)

    def _viewport_cells_rect(self) -> tuple:
        """
        Retourne (col_min, row_min, col_max, row_max) des cases actuellement
        visibles dans le canvas (scroll + zoom pris en compte).
        Bornes inclusives, clampées à la grille.
        """
        cp = self._cp
        W_full, H_full = self._wh
        sr_w = W_full + 40
        sr_h = H_full + 40
        x0f, x1f = self.canvas.xview()
        y0f, y1f = self.canvas.yview()
        vx0 = int(x0f * sr_w);  vy0 = int(y0f * sr_h)
        vx1 = int(x1f * sr_w);  vy1 = int(y1f * sr_h)
        col_min = max(0,             vx0 // cp)
        row_min = max(0,             vy0 // cp)
        col_max = min(self.cols - 1, vx1 // cp)
        row_max = min(self.rows - 1, vy1 // cp)
        return col_min, row_min, col_max, row_max

    def _send_to_agents(self):
        """Génère une description textuelle + image de la carte et l'injecte aux agents.
        L'image est sauvegardée dans campagne/<nom_campagne>/ avec horodatage."""
        if self.inject_fn is None and self.msg_queue is None:
            messagebox.showinfo(
                "Agents non disponibles",
                "La carte de combat n'est pas connectée aux agents.\n"
                "Lancez la partie d'abord.",
                parent=self.win)
            return

        desc = self._build_map_description()

        # ── Dossier de sauvegarde campagne ────────────────────────────────────
        import datetime as _dt
        try:
            from app_config import get_campaign_name
            camp_name = get_campaign_name()
        except Exception:
            camp_name = "campagne"
        camp_name = "".join(c for c in camp_name if c.isalnum() or c in (" ", "-", "_")).strip() or "campagne"
        camp_dir  = os.path.join("campagne", camp_name)
        os.makedirs(camp_dir, exist_ok=True)

        # ── Rendu et sauvegarde de l'image ────────────────────────────────────
        img_path = ""
        try:
            player_img = self._render_player_image()
            ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"carte_{ts}.png"
            img_path = os.path.join(camp_dir, fname)
            player_img.save(img_path, "PNG")
            desc += f"\n[Image carte sauvegardée : {img_path}]"
            print(f"[CombatMap] Image sauvegardée → {img_path}")
        except Exception as e:
            print(f"[CombatMap] export image : {e}")

        # ── Affichage dans le chat ─────────────────────────────────────────────
        if self.msg_queue is not None:
            self.msg_queue.put({
                "sender": "Carte de Combat",
                "text":   desc + (f"\n📁 {img_path}" if img_path else ""),
                "color":  "#64b5f6",
            })

        # ── Injection dans autogen (texte uniquement — compatible tous modèles) ──
        if self.inject_fn is not None:
            self.inject_fn(desc)

    def _build_map_description(self) -> str:
        """Description textuelle restreinte à la zone visible à l'écran (zoom + scroll)."""
        cp_px  = self.cell_px
        mw, mh = self.cols * cp_px, self.rows * cp_px
        mask   = self._fog_mask if self._fog_mask else Image.new("L", (mw, mh), 255)
        mask_arr = np.array(mask)

        def _cell_covered(c, r):
            px = min(int((c + 0.5) * cp_px), mw - 1)
            py = min(int((r + 0.5) * cp_px), mh - 1)
            return mask_arr[py, px] > 127

        col_min, row_min, col_max, row_max = self._viewport_cells_rect()
        vp_cols  = col_max - col_min + 1
        vp_rows  = row_max - row_min + 1
        vp_total = vp_cols * vp_rows
        hidden  = sum(1 for r in range(row_min, row_max + 1)
                      for c in range(col_min, col_max + 1) if _cell_covered(c, r))
        visible = vp_total - hidden

        lines = [
            "═══ CARTE DE COMBAT — VUE ÉCRAN ═══",
            f"Viewport : colonnes {col_min+1}–{col_max+1}, lignes {row_min+1}–{row_max+1}"
            f"  ({vp_cols}×{vp_rows} cases)  |  zoom {int(self.zoom*100)}%",
            f"{visible}/{vp_total} cases visibles dans ce cadre  |  {hidden} sous brouillard",
            "",
        ]

        visible_tokens, hidden_tokens = [], []
        for tok in self.tokens:
            c, r = int(tok["col"]), int(tok["row"])
            if not (col_min <= c <= col_max and row_min <= r <= row_max):
                continue
            label = f"{tok['name']} ({tok['type']}) → Col {c+1}, Lig {r+1}"
            if not _cell_covered(c, r):
                visible_tokens.append(label)
            else:
                hidden_tokens.append(label)

        if visible_tokens:
            lines.append("Tokens visibles :")
            for t in visible_tokens: lines.append(f"  • {t}")
        else:
            lines.append("Aucun token visible dans ce cadre.")
        if hidden_tokens:
            lines.append("Tokens sous brouillard dans ce cadre :")
            for t in hidden_tokens: lines.append(f"  ? {t}")

        lines.append("")
        lines.append("Zones révélées dans le cadre affiché :")
        revealed_rows = []
        for r in range(row_min, row_max + 1):
            rcols = [c+1 for c in range(col_min, col_max + 1) if not _cell_covered(c, r)]
            if rcols:
                revealed_rows.append(f"  Lig {r+1} : colonnes {_compress_ranges(rcols)}")
        if revealed_rows:
            lines.extend(revealed_rows[:20])
            if len(revealed_rows) > 20:
                lines.append(f"  … ({len(revealed_rows) - 20} lignes supplémentaires)")
        else:
            lines.append("  (aucune case révélée dans ce cadre)")

        notes_txt = self._notes_description()
        if notes_txt:
            lines.append(notes_txt)
        return "\n".join(lines)

    def _render_player_image(self) -> "Image.Image":
        """
        Rend exactement ce qui est affiché à l'écran :
          - même zoom que la vue MJ, même position de scroll
          - tous les calques visibles
          - fog opaque (vue joueurs)
        """
        cp     = self._cp
        W_full, H_full = self._wh
        sr_w   = W_full + 40
        sr_h   = H_full + 40
        x0f, x1f = self.canvas.xview()
        y0f, y1f = self.canvas.yview()
        vx0 = max(0,      int(x0f * sr_w))
        vy0 = max(0,      int(y0f * sr_h))
        vx1 = min(W_full, int(x1f * sr_w))
        vy1 = min(H_full, int(y1f * sr_h))
        VW  = max(1, vx1 - vx0)
        VH  = max(1, vy1 - vy0)

        # ── Damier ────────────────────────────────────────────────────────────
        ri  = (np.arange(VH) + vy0) // cp
        ci  = (np.arange(VW) + vx0) // cp
        chk = (ri[:, None] + ci[None, :]) % 2
        arr = np.where(chk[:, :, None] == 0,
                       np.array(_C_BG_A, dtype=np.uint8),
                       np.array(_C_BG_B, dtype=np.uint8))
        bg = Image.fromarray(arr.astype(np.uint8), "RGBA")

        # ── Calques de carte cropped au viewport ──────────────────────────────
        scale = cp / self.cell_px
        for layer in self.map_layers:
            if not layer.get("visible", True):
                continue
            lpath = layer.get("path", "")
            if not lpath or not os.path.exists(lpath):
                continue
            try:
                src = self._map_pil_cache_dict.get(lpath)
                if src is None:
                    src = Image.open(lpath).convert("RGBA")
                    self._map_pil_cache_dict[lpath] = src
                sw, sh  = src.size
                lw      = layer.get("w", self.cols * self.cell_px)
                lh      = layer.get("h", self.rows * self.cell_px)
                lox     = layer.get("ox", 0)
                loy     = layer.get("oy", 0)
                disp_w  = max(1, int(lw * scale))
                disp_h  = max(1, int(lh * scale))
                img_cx0 = int(lox * scale)
                img_cy0 = int(loy * scale)
                ix0 = max(vx0, img_cx0);  iy0 = max(vy0, img_cy0)
                ix1 = min(vx1, img_cx0 + disp_w);  iy1 = min(vy1, img_cy0 + disp_h)
                if ix1 <= ix0 or iy1 <= iy0:
                    continue
                dest_w = ix1 - ix0;  dest_h = iy1 - iy0
                frac_x0 = (ix0 - img_cx0) / disp_w;  frac_y0 = (iy0 - img_cy0) / disp_h
                frac_x1 = (ix1 - img_cx0) / disp_w;  frac_y1 = (iy1 - img_cy0) / disp_h
                src_crop = src.crop((
                    max(0, int(frac_x0 * sw)), max(0, int(frac_y0 * sh)),
                    min(sw, max(1, int(frac_x1 * sw))), min(sh, max(1, int(frac_y1 * sh))),
                ))
                src_cw, _ = src_crop.size
                filt = Image.BILINEAR if dest_w > src_cw else Image.LANCZOS
                tile_img = src_crop.resize((dest_w, dest_h), filt)
                map_layer = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
                map_layer.paste(tile_img, (ix0 - vx0, iy0 - vy0))
                bg = Image.alpha_composite(bg, map_layer)
            except Exception as e:
                print(f"[CombatMap] render_viewport calque '{layer.get('name','?')}' : {e}")

        # ── Grille ────────────────────────────────────────────────────────────
        if self._show_grid and cp >= 4:
            bg_arr = np.array(bg, dtype=np.float32)
            gc = np.array(_C_GRID[:3], dtype=np.float32)
            ga = _C_GRID[3] / 255.0
            for c in range(vx0 // cp, vx1 // cp + 2):
                x = c * cp - vx0
                if 0 <= x < VW:
                    bg_arr[:, x, :3] = ga * gc + (1 - ga) * bg_arr[:, x, :3]
            for r in range(vy0 // cp, vy1 // cp + 2):
                y = r * cp - vy0
                if 0 <= y < VH:
                    bg_arr[y, :, :3] = ga * gc + (1 - ga) * bg_arr[y, :, :3]
            bg_arr[:, :, 3] = 255
            bg = Image.fromarray(bg_arr.astype(np.uint8), "RGBA")

        # ── Fog opaque cropped au viewport ────────────────────────────────────
        mw_fog = self.cols * self.cell_px
        mh_fog = self.rows * self.cell_px
        mask = self._fog_mask if self._fog_mask else Image.new("L", (mw_fog, mh_fog), 255)
        fx0 = int(vx0 / W_full * mw_fog) if W_full > 0 else 0
        fy0 = int(vy0 / H_full * mh_fog) if H_full > 0 else 0
        fx1 = int(vx1 / W_full * mw_fog) if W_full > 0 else mw_fog
        fy1 = int(vy1 / H_full * mh_fog) if H_full > 0 else mh_fog
        fog_crop   = mask.crop((max(0,fx0), max(0,fy0),
                                min(mw_fog,max(fx0+1,fx1)), min(mh_fog,max(fy0+1,fy1))))
        fog_scaled = fog_crop.resize((VW, VH), Image.NEAREST)
        fog_arr    = np.array(fog_scaled, dtype=np.uint8)
        fog_rgba   = np.zeros((VH, VW, 4), dtype=np.uint8)
        fog_rgba[fog_arr > 0] = _C_FOG_PLAYER
        fog_opaque = Image.fromarray(fog_rgba, "RGBA")
        scene = Image.alpha_composite(bg, fog_opaque)

        # ── Notes dans le viewport ────────────────────────────────────────────
        if self._notes:
            scene = self._composite_notes_pil_viewport(scene, VW, VH, vx0, vy0)
        return scene

    def _composite_notes_pil_viewport(self, base: "Image.Image",
                                      W: int, H: int,
                                      vx0: int, vy0: int) -> "Image.Image":
        """Surimprime les notes en tenant compte du décalage viewport (vx0, vy0)."""
        from PIL import ImageDraw as _ID, ImageFont as _IF
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw    = _ID.Draw(overlay)
        z = self.zoom
        hw, hh = self.NOTE_W / 2, self.NOTE_H / 3
        for n in self._notes:
            cx = int(n["px"] * z) - vx0
            cy = int(n["py"] * z) - vy0
            if cx < -hw or cx > W + hw or cy < -hh or cy > H + hh:
                continue
            draw.rectangle([cx - hw, cy - hh, cx + hw, cy + hh], fill=(0, 0, 0, 128))
            font_size = max(9, int(9 * z))
            try:
                font = _IF.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", font_size)
            except Exception:
                font = _IF.load_default()
            col_hex = n["color"].lstrip("#")
            r, g, b = int(col_hex[0:2], 16), int(col_hex[2:4], 16), int(col_hex[4:6], 16)
            for dx, dy in ((-1,-1),(1,-1),(-1,1),(1,1)):
                draw.text((cx+dx, cy+dy), n["text"],
                          fill=(0,0,0,220), font=font, anchor="mm", align="center")
            draw.text((cx, cy), n["text"],
                      fill=(r,g,b,255), font=font, anchor="mm", align="center")
        return Image.alpha_composite(base, overlay)

    def _composite_notes_pil(self, base: "Image.Image", W: int, H: int,
                              zoom_override: float | None = None) -> "Image.Image":
        """Surimprime les notes (fond noir transparent + texte coloré) sur l'image exportée."""
        from PIL import ImageDraw as _ID, ImageFont as _IF
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw    = _ID.Draw(overlay)
        z       = zoom_override if zoom_override is not None else self.zoom
        hw      = self.NOTE_W / 2
        hh      = self.NOTE_H / 3

        for n in self._notes:
            cx = int(n["px"] * z)
            cy = int(n["py"] * z)

            # Fond noir semi-transparent (alpha 128 ≈ 50%)
            draw.rectangle(
                [cx - hw, cy - hh, cx + hw, cy + hh],
                fill=(0, 0, 0, 128))

            font_size = max(9, int(9 * z))
            try:
                font = _IF.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
                    font_size)
            except Exception:
                font = _IF.load_default()

            col_hex = n["color"].lstrip("#")
            r, g, b = int(col_hex[0:2], 16), int(col_hex[2:4], 16), int(col_hex[4:6], 16)

            # Halo noir pour lisibilité
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                draw.text((cx + dx, cy + dy), n["text"],
                          fill=(0, 0, 0, 220), font=font, anchor="mm", align="center")
            # Texte coloré
            draw.text((cx, cy), n["text"],
                      fill=(r, g, b, 255), font=font, anchor="mm", align="center")

        return Image.alpha_composite(base, overlay)

    # ─── Notes flottantes (post-its déplaçables) ────────────────────────────

    NOTE_COLORS = ["#ffe082", "#80cbc4", "#ef9a9a", "#ce93d8",
                   "#80deea", "#a5d6a7", "#ffcc80", "#f48fb1"]
    # Largeur fixe d'un post-it en px canvas (indépendante du zoom)
    NOTE_W = 120
    NOTE_H = 68

    # ── Helpers hit-test ──────────────────────────────────────────────────────

    def _note_at(self, cx: float, cy: float) -> "dict | None":
        """Retourne la note dont le cadre contient (cx, cy), ou None."""
        z = self.zoom
        hw, hh = self.NOTE_W / 2, self.NOTE_H / 2
        for n in reversed(self._notes):   # reversed = dessus en premier
            nx, ny = n["px"] * z, n["py"] * z
            if (nx - hw <= cx <= nx + hw) and (ny - hh <= cy <= ny + hh):
                return n
        return None

    # ── Création / édition ────────────────────────────────────────────────────

    def _create_note(self, cx: float, cy: float):
        """Ouvre le dialogue de saisie et place une note à (cx, cy) canvas."""
        text = simpledialog.askstring(
            "Nouvelle note",
            "Texte de la note :",
            parent=self.win)
        if not text or not text.strip():
            return
        color = self.NOTE_COLORS[len(self._notes) % len(self.NOTE_COLORS)]
        n = {
            "px":  cx / self.zoom,
            "py":  cy / self.zoom,
            "text": text.strip(),
            "color": color,
            "canvas_ids": [],
        }
        self._notes.append(n)
        self._draw_one_note(n)
        self._save_state()

    def _edit_note(self, n: dict):
        """Dialogue d'édition du texte d'une note existante."""
        text = simpledialog.askstring(
            "Modifier la note",
            "Nouveau texte (vide = supprimer) :",
            initialvalue=n["text"],
            parent=self.win)
        if text is None:
            return   # annulé
        if not text.strip():
            self._delete_note(n)
            return
        n["text"] = text.strip()
        self._redraw_one_note(n)
        self._save_state()

    def _delete_note(self, n: dict):
        """Supprime une note du canvas et de la liste."""
        for cid in n.get("canvas_ids", []):
            self.canvas.delete(cid)
        if n in self._notes:
            self._notes.remove(n)
        self._save_state()

    # ── Rendu ─────────────────────────────────────────────────────────────────

    def _draw_one_note(self, n: dict):
        """Dessine une note minimaliste : fond noir transparent + texte lisible."""
        z   = self.zoom
        cx  = n["px"] * z
        cy  = n["py"] * z
        col = n["color"]
        fs  = max(7, int(9 * z))

        # Fond noir semi-transparent (stipple gray50 ≈ 50%)
        # On dimensionne dynamiquement selon le texte approximatif
        hw = self.NOTE_W / 2
        hh = self.NOTE_H / 3   # plus compact, juste pour le texte

        bg = self.canvas.create_rectangle(
            cx - hw, cy - hh, cx + hw, cy + hh,
            fill="#000000", outline="", stipple="gray50",
            tags=("note",))

        # Halo noir (lisibilité) — décalé 1 px dans toutes directions
        halos = []
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            halos.append(self.canvas.create_text(
                cx + dx, cy + dy,
                text=n["text"],
                fill="#000000",
                font=("Consolas", fs, "bold"),
                width=int(hw * 2) - 8,
                justify=tk.CENTER,
                tags=("note",)))

        # Texte principal (couleur de la note — vive sur fond sombre)
        txt = self.canvas.create_text(
            cx, cy,
            text=n["text"],
            fill=col,
            font=("Consolas", fs, "bold"),
            width=int(hw * 2) - 8,
            justify=tk.CENTER,
            tags=("note",))

        ids = [bg] + halos + [txt]
        n["canvas_ids"] = ids

        for iid in ids:
            self.canvas.tag_bind(iid, "<ButtonPress-1>",
                lambda e, note=n: self._note_press(e, note))
            self.canvas.tag_bind(iid, "<Double-Button-1>",
                lambda e, note=n: self._edit_note(note))
            self.canvas.tag_bind(iid, "<ButtonPress-3>",
                lambda e, note=n: self._delete_note(note))

        self.canvas.tag_raise("note")
        self.canvas.tag_raise("token")

    def _redraw_one_note(self, n: dict):
        """Efface et redessine une note."""
        for cid in n.get("canvas_ids", []):
            self.canvas.delete(cid)
        n["canvas_ids"] = []
        self._draw_one_note(n)

    def _redraw_all_notes(self):
        """Efface et redessine toutes les notes (après zoom/resize)."""
        self.canvas.delete("note")
        for n in self._notes:
            n["canvas_ids"] = []
        for n in self._notes:
            self._draw_one_note(n)

    @staticmethod
    def _darken(hex_color: str, factor: float = 0.65) -> str:
        """Assombrit une couleur hexadécimale."""
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return "#{:02x}{:02x}{:02x}".format(
            int(r * factor), int(g * factor), int(b * factor))

    # ── Drag depuis items de la note ──────────────────────────────────────────

    def _note_press(self, event, note: dict):
        """Initie un drag depuis un item canvas appartenant à une note."""
        if self.tool != "note":
            return
        cx, cy = self._canvas_xy(event)
        self._drag_note = note
        self._drag_note_off = (cx - note["px"] * self.zoom,
                               cy - note["py"] * self.zoom)

    # ── Double-clic canvas (hors items bindés) ────────────────────────────────

    def _mb1_double(self, event):
        cx, cy = self._canvas_xy(event)
        hit = self._note_at(cx, cy)
        if hit is not None:
            self._edit_note(hit)

    # ── Build map description (inclure les notes) ─────────────────────────────


    # ─── Outil Porte ─────────────────────────────────────────────────────────

    def _door_at(self, col: int, row: int) -> "dict | None":
        """Retourne la porte à (col, row) ou None."""
        for d in self._doors:
            if d["col"] == col and d["row"] == row:
                return d
        return None

    def _door_toggle_or_create(self, col: int, row: int):
        """Clic gauche : si une porte existe, bascule ouvert/fermé. Sinon ouvre
        une mini-fenêtre pour saisir un label et crée la porte (fermée)."""
        existing = self._door_at(col, row)
        if existing is not None:
            existing["open"] = not existing["open"]
            self._redraw_one_door(existing)
            self._save_state()
            state_txt = "ouverte" if existing["open"] else "fermée"
            label_txt = f" ({existing['label']})" if existing["label"] else ""
            if hasattr(self, "_status_var"):
                self._status_var.set(
                    f"Porte{label_txt} — maintenant {state_txt} "
                    f"(Col {col+1}, Lig {row+1})"
                )
            return

        # Nouvelle porte : demander un label optionnel
        dw = tk.Toplevel(self.win)
        dw.title("Nouvelle porte")
        dw.geometry("280x110")
        dw.configure(bg="#0d1018")
        dw.resizable(False, False)
        dw.wait_visibility()
        dw.grab_set()

        tk.Label(dw, text=f"Porte — Col {col+1}, Lig {row+1}",
                 bg="#0d1018", fg="#ff9966",
                 font=("Consolas", 10, "bold")).pack(pady=(10, 2))
        tk.Label(dw, text="Label (optionnel) :",
                 bg="#0d1018", fg="#aaaacc",
                 font=("Consolas", 8)).pack()
        entry = tk.Entry(dw, bg="#252538", fg="#eeeeee",
                         font=("Consolas", 10), insertbackground="#ff9966",
                         relief="flat", width=24)
        entry.pack(padx=14, ipady=3)
        entry.focus_set()

        def _confirm(event=None):
            label = entry.get().strip()
            dw.destroy()
            door = {"col": col, "row": row, "open": False,
                    "label": label, "canvas_ids": []}
            self._doors.append(door)
            self._redraw_one_door(door)  # utilise _cp courant
            self._save_state()

        entry.bind("<Return>", _confirm)
        tk.Button(dw, text="Créer (fermée)", bg="#2c1200", fg="#ff9966",
                  font=("Consolas", 9, "bold"), relief="flat",
                  command=_confirm).pack(pady=6)

    def _delete_door(self, door: dict):
        for cid in door.get("canvas_ids", []):
            self.canvas.delete(cid)
        if door in self._doors:
            self._doors.remove(door)
        self._save_state()

    def _draw_one_door(self, door: dict):
        """Overlay d'état de porte sur l'image — couvre visuellement la porte dessinée.

        Porte FERMÉE : fond opaque rouge foncé + barres croisées + texte "FERMÉE"
                       → écrase une porte ouverte dans l'image.
        Porte OUVERTE : fond opaque vert foncé + arc ouvert + texte "OUVERTE"
                       → écrase une porte fermée dans l'image.
        """
        cp   = self._cp
        col, row = door["col"], door["row"]
        x0   = col * cp
        y0   = row * cp
        x1   = x0 + cp
        y1   = y0 + cp
        cx_  = x0 + cp * 0.5
        cy_  = y0 + cp * 0.5
        ids  = []
        pad  = max(2, int(cp * 0.06))   # marge intérieure

        if door["open"]:
            # ── OUVERTE : fond vert opaque + arc D&D style + "OUVERT" ───────
            # Fond semi-opaque couvrant la case entière
            ids.append(self.canvas.create_rectangle(
                x0 + pad, y0 + pad, x1 - pad, y1 - pad,
                fill="#0a2a0a", outline="#44cc66", width=2, tags="door"))
            # Arc ouvert (porte pivotée) — style plan architectural
            r = cp * 0.36
            ids.append(self.canvas.create_arc(
                cx_ - r, cy_ - r, cx_ + r, cy_ + r,
                start=0, extent=90, style="arc",
                outline="#44ee66", width=max(2, int(cp * 0.07)), tags="door"))
            # Ligne du battant
            ids.append(self.canvas.create_line(
                cx_, cy_, cx_ + r, cy_,
                fill="#44ee66", width=max(2, int(cp * 0.07)), tags="door"))
            # Label état
            font_sz = max(6, int(cp * 0.20))
            label_txt = door["label"] if door.get("label") else "OUVERT"
            ids.append(self.canvas.create_text(
                cx_, y1 - max(5, int(cp * 0.16)),
                text=label_txt, fill="#88ffaa",
                font=("Consolas", font_sz, "bold"), tags="door"))
        else:
            # ── FERMÉE : fond rouge opaque + barres + cadenas + "FERMÉ" ────
            ids.append(self.canvas.create_rectangle(
                x0 + pad, y0 + pad, x1 - pad, y1 - pad,
                fill="#1e0000", outline="#cc3300", width=2, tags="door"))
            # Deux barres croisées (verrou visuel)
            m = int(cp * 0.22)
            ids.append(self.canvas.create_line(
                cx_ - m, cy_, cx_ + m, cy_,
                fill="#cc3300", width=max(3, int(cp * 0.09)), tags="door"))
            ids.append(self.canvas.create_line(
                cx_, cy_ - m, cx_, cy_ + m,
                fill="#cc3300", width=max(3, int(cp * 0.09)), tags="door"))
            # Petit carré central (cadenas)
            hs = max(3, int(cp * 0.10))
            ids.append(self.canvas.create_rectangle(
                cx_ - hs, cy_ - hs, cx_ + hs, cy_ + hs,
                outline="#ff6633", fill="#3a0000",
                width=1, tags="door"))
            # Label état
            font_sz = max(6, int(cp * 0.20))
            label_txt = door["label"] if door.get("label") else "FERMÉ"
            ids.append(self.canvas.create_text(
                cx_, y1 - max(5, int(cp * 0.16)),
                text=label_txt, fill="#ff8866",
                font=("Consolas", font_sz, "bold"), tags="door"))

        door["canvas_ids"] = ids

    def _redraw_one_door(self, door: dict):
        for cid in door.get("canvas_ids", []):
            self.canvas.delete(cid)
        door["canvas_ids"] = []
        self._draw_one_door(door)

    def _redraw_all_doors(self):
        self.canvas.delete("door")
        for d in self._doors:
            d["canvas_ids"] = []
            self._draw_one_door(d)

    def _doors_description(self) -> str:
        """Description textuelle des portes pour les agents."""
        if not self._doors:
            return ""
        lines = ["\n🚪 PORTES :"]
        for d in self._doors:
            state  = "ouverte" if d["open"] else "FERMÉE"
            label  = f" — {d['label']}" if d.get("label") else ""
            lines.append(
                f"  • Col {d['col']+1}, Lig {d['row']+1}{label} : {state}")
        return "\n".join(lines)

    def _notes_description(self) -> str:
        if not self._notes:
            return ""
        lines = ["\nNotes MJ sur la carte :"]
        for n in self._notes:
            # px/py sont en espace-map (indépendant du zoom)
            col = int(n["px"] / self.cell_px)
            row = int(n["py"] / self.cell_px)
            lines.append(f"  📌 Col {col+1}, Lig {row+1} : {n['text']}")
        return "\n".join(lines)

    def move_token(self, name: str, new_col: int, new_row: int) -> str:
        """
        Déplace le token du personnage 'name' vers (new_col, new_row).
        Appelé depuis autogen_engine quand un agent déclare un mouvement confirmé.
        Thread-safe uniquement si appelé via root.after() depuis le thread Tk.
        Retourne un message descriptif du déplacement pour le chat.
        """
        for tok in self.tokens:
            if tok.get("name") == name:
                old_col = int(round(tok["col"]))
                old_row = int(round(tok["row"]))
                tok["col"] = max(0, min(self.cols - 1, new_col))
                tok["row"] = max(0, min(self.rows - 1, new_row))
                actual_col = int(tok["col"])
                actual_row = int(tok["row"])
                self._redraw_one_token(tok)
                self._save_state()
                # Mise à jour vue joueurs si ouverte
                if self._player_win is not None:
                    try:
                        self._player_win.refresh(
                            self._bg_pil, self._fog_mask, self._cp,
                            self.cols, self.rows, self.tokens)
                    except Exception:
                        self._player_win = None
                dcol = actual_col - old_col
                drow = actual_row - old_row
                dist_m = max(abs(dcol), abs(drow)) * 1.5
                return (
                    f"[Carte] {name} déplacé : "
                    f"Col {old_col+1},Lig {old_row+1} → "
                    f"Col {actual_col+1},Lig {actual_row+1} "
                    f"({dist_m:.1f} m)"
                )
        return f"[Carte] Token '{name}' introuvable — vérifiez qu'il est placé sur la carte."

    def _restore_view(self):
        """Restaure le zoom et la position de scroll sauvegardés (appelé après le premier rendu)."""
        # Rien à restaurer si vue par défaut
        if abs(self.zoom - 1.0) < 0.01 and self._scroll_fx < 0.001 and self._scroll_fy < 0.001:
            return
        try:
            W, H   = self._wh
            sr_w, sr_h = W + 40, H + 40
            self.canvas.config(scrollregion=(0, 0, sr_w, sr_h))
            self.canvas.xview_moveto(max(0.0, min(1.0, self._scroll_fx)))
            self.canvas.yview_moveto(max(0.0, min(1.0, self._scroll_fy)))
            self.canvas.update_idletasks()
            # Rebuild du rendu au zoom restauré
            self._bg_pil  = None
            self._fog_pil = None
            self._img_id  = 0
            self.canvas.delete("scene")
            self._rebuild_bg()
            self._rebuild_fog()
            self._redraw_all_doors()
            self._composite()
            self._redraw_all_tokens()
            self._redraw_all_notes()
            self._zoom_lbl.config(text=f"{int(self.zoom * 100)}%")
        except Exception as e:
            print(f"[CombatMap] _restore_view : {e}")

    def _on_close(self):
        self._save_state()
        self.win.destroy()


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

    def refresh(self, bg_pil, fog_mask, cp: int,
                cols: int, rows: int, tokens: list,
                ox: int = 0, oy: int = 0):
        """Reçoit les données du MJ et re-rend la vue joueurs (fog opaque)."""
        if bg_pil is None:
            return

        W, H = cols * cp, rows * cp
        # Redimensionner le fog mask à la résolution courante
        if fog_mask is not None:
            scaled = fog_mask.resize((W, H), Image.NEAREST)
            arr    = np.array(scaled, dtype=np.uint8)
        else:
            arr = np.full((H, W), 255, dtype=np.uint8)
        rgba = np.zeros((H, W, 4), dtype=np.uint8)
        rgba[arr > 0] = _C_FOG_PLAYER
        fog_opaque = Image.fromarray(rgba, "RGBA")

        # Ensure bg_pil matches the computed (W, H) before compositing.
        # A mismatch occurs when the grid dimensions (cols/rows/cp) differ
        # from the size at which bg_pil was last rendered (e.g. after a
        # resize that regenerated the fog mask but not the tile cache).
        if bg_pil.size != (W, H):
            bg_pil = bg_pil.resize((W, H), Image.LANCZOS)

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

        # Pré-calculer le fog array une seule fois pour la visibilité des tokens
        fog_arr = np.array(fog.resize((W, H), Image.NEAREST)) if fog is not None else None

        for tok in tokens:
            c, r = int(tok["col"]), int(tok["row"])
            if 0 <= r < rows and 0 <= c < cols:
                px = min(int((c + 0.5) * cp), W - 1)
                py = min(int((r + 0.5) * cp), H - 1)
                if fog_arr is None or fog_arr[py, px] <= 127:
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


# ─── Export textuel de la carte pour les agents LLM ──────────────────────────

def get_map_prompt(win_state: dict) -> str:
    """
    Génère une description textuelle de la carte de combat (positions des tokens,
    distances clés) à partir de win_state["combat_map_data"].

    Retourne "" si aucune carte n'est chargée ou si elle est vide de tokens.
    1 case = 1,5 m (équivalent D&D 5ft square).
    Les distances sont calculées en distance de Chebyshev (mouvement diagonale libre 5e).
    """
    data = win_state.get("combat_map_data", {})
    tokens = data.get("tokens", [])
    if not tokens:
        return ""

    cols = data.get("cols", 30)
    rows = data.get("rows", 20)

    heroes    = [t for t in tokens if t.get("type") == "hero"]
    monsters  = [t for t in tokens if t.get("type") == "monster"]
    traps     = [t for t in tokens if t.get("type") == "trap"]
    notes     = data.get("notes", [])

    def _coord(t):
        return int(round(t.get("col", 0))), int(round(t.get("row", 0)))

    def _label(t):
        return t.get("name") or t.get("type", "?")

    def _dist_m(t1, t2):
        """Distance Chebyshev en mètres (1 case = 1,5 m)."""
        c1, r1 = _coord(t1)
        c2, r2 = _coord(t2)
        return max(abs(c1 - c2), abs(r1 - r2)) * 1.5

    lines = [
        f"\n\n🗺️ ═══ CARTE DE COMBAT ({cols}×{rows} cases — 1 case = 1,5 m) ═══"
    ]

    # ── Positions des héros ────────────────────────────────────────────────────
    if heroes:
        lines.append("\n🔵 HÉROS — positions :")
        for h in heroes:
            c, r = _coord(h)
            lines.append(f"  • {_label(h)} → colonne {c}, rangée {r}")

    # ── Positions des monstres ─────────────────────────────────────────────────
    if monsters:
        lines.append("\n🔴 ENNEMIS — positions :")
        for m in monsters:
            c, r = _coord(m)
            lines.append(f"  • {_label(m)} → colonne {c}, rangée {r}")

    # ── Pièges / éléments spéciaux ────────────────────────────────────────────
    if traps:
        lines.append("\n⚠️ PIÈGES / ZONES :")
        for tr in traps:
            c, r = _coord(tr)
            lines.append(f"  • {_label(tr)} → colonne {c}, rangée {r}")

    # ── Distances héros ↔ ennemis les plus proches ────────────────────────────
    if heroes and monsters:
        lines.append("\n📏 DISTANCES (portée de mêlée ≤ 1,5 m = case adjacente) :")
        for h in heroes:
            nearest = min(monsters, key=lambda m: _dist_m(h, m))
            d = _dist_m(h, nearest)
            reach = "portée mêlée ✅" if d <= 1.5 else f"{d:.1f} m"
            lines.append(f"  • {_label(h)} → {_label(nearest)} : {reach}")

    # ── Distances héros ↔ héros ────────────────────────────────────────────────
    if len(heroes) >= 2:
        lines.append("\n🤝 DISTANCES ENTRE ALLIÉS :")
        for i, h1 in enumerate(heroes):
            for h2 in heroes[i + 1:]:
                d = _dist_m(h1, h2)
                adj = " (adjacents)" if d <= 1.5 else ""
                lines.append(
                    f"  • {_label(h1)} ↔ {_label(h2)} : {d:.1f} m{adj}"
                )

    # ── Notes de carte visibles ────────────────────────────────────────────────
    if notes:
        note_texts = [n.get("text", "").strip() for n in notes if n.get("text", "").strip()]
        if note_texts:
            lines.append("\n📌 NOTES SUR LA CARTE :")
            for nt in note_texts[:6]:   # max 6 pour ne pas surcharger
                lines.append(f"  • {nt}")

    # ── Portes (état réel — priorité sur l'image) ─────────────────────────────
    doors = data.get("doors", [])
    if doors:
        lines.append("\n🚪 PORTES — état réel (priorité absolue sur l'image de fond) :")
        lines.append("  ⚠ L'image peut montrer un état différent — ces données font foi.")
        for d in doors:
            state = "OUVERTE" if d.get("open") else "FERMÉE"
            label = f" ({d['label']})" if d.get("label") else ""
            override = ("l'image montre une porte fermée — elle est en réalité OUVERTE"
                        if d.get("open")
                        else "l'image montre une porte ouverte — elle est en réalité FERMÉE")
            lines.append(
                f"  • Col {d['col']+1}, Lig {d['row']+1}{label} : {state} — {override}")

    lines.append(
        "\nUtilise ces positions pour décider de ton mouvement et de ta portée d'attaque."
    )

    return "\n".join(lines)