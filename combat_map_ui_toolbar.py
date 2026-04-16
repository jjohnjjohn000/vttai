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

class UIToolbarMixin:
    pass
    # ─── Toolbar ──────────────────────────────────────────────────────────────

    def _build_toolbar(self):
        # Conteneur global 2 lignes
        tb_outer = tk.Frame(self.win, bg=BG_TOOL)
        tb_outer.pack(fill=tk.X, side=tk.TOP)

        # ── Ligne 1 : Outils de peinture ─────────────────────────────────────
        row1 = tk.Frame(tb_outer, bg=BG_TOOL, pady=4, padx=6)
        row1.pack(fill=tk.X)

        tk.Label(row1, text="CARTE", bg=BG_TOOL, fg="#6666aa",
                 font=("Consolas", 8, "bold")).pack(side=tk.LEFT, padx=(4, 10))

        self._tool_btns = {}
        for key, label, fg_on, bg_on in [
            ("select",        "↖ Sélect.",     "#aaaaff", "#1e1e44"),
            ("pointer",       "📍 Pointer",     "#ff8a80", "#2c0808"),
            ("reveal",        "⬡ Révéler",     "#81c784", "#0e2c1a"),
            ("hide",          "⬡ Cacher",      "#e57373", "#2c0e0e"),
            ("brush_reveal",  "◉ Pinceau+",    "#b2dfdb", "#0a2020"),
            ("brush_hide",    "◉ Pinceau-",    "#ffcdd2", "#2c1010"),
            ("ruler",         "📐 Règle",       "#fff176", "#2a2600"),
            ("add",           "+ Token",       "#64b5f6", "#0e1e2c"),
            ("note",          "Note",          "#ffe082", "#2a2500"),
            ("door",          "Porte",         "#ff9966", "#2c1200"),
            ("obstacle_poly", "⬡ Obstacle",    "#ff6633", "#2c1000"),
            ("obstacle_free", "✏ Main levée",  "#ff9955", "#2c1800"),
            ("erase_obs",     "⌫ Efface",       "#ff6b6b", "#2c0808"),
            ("resize_map",    "⤢ Carte",       "#ffb74d", "#2c1a00"),
        ]:
            btn = tk.Button(
                row1, text=label, bg="#252538", fg="#aaaacc",
                font=("Consolas", 9, "bold"), relief="flat",
                padx=9, pady=4, cursor="hand2",
                activebackground=bg_on, activeforeground=fg_on,
                command=lambda k=key: self._set_tool(k))
            btn.pack(side=tk.LEFT, padx=2)
            self._tool_btns[key] = (btn, fg_on, bg_on)

        _sep(row1)

        self._obs_color_btn = tk.Button(
            row1, text="Couleur", bg=self._obs_color, fg="white",
            font=("Consolas", 8, "bold"), relief="flat", padx=7, pady=3,
            cursor="hand2", command=self._pick_obstacle_color)
        self._obs_color_btn.pack(side=tk.LEFT, padx=2)

        _sep(row1)
        tk.Label(row1, text="Rayon :", bg=BG_TOOL, fg="#9999bb",
                 font=("Consolas", 8)).pack(side=tk.LEFT, padx=(4, 2))
        self._brush_var = tk.IntVar(value=self.brush_size)
        tk.Spinbox(
            row1, from_=1, to=10, textvariable=self._brush_var, width=3,
            bg="#252538", fg="#ccccee", font=("Consolas", 9),
            buttonbackground="#2e2e4a", relief="flat",
            command=lambda: setattr(self, "brush_size", self._brush_var.get()),
        ).pack(side=tk.LEFT, padx=2)

        # Ratio — affiché dynamiquement en ligne 1 (mode resize_map)
        self._ratio_var = tk.BooleanVar(value=False)
        self._ratio_chk = tk.Checkbutton(
            row1, text="⇔ Ratio", variable=self._ratio_var,
            bg=BG_TOOL, fg="#ffb74d", selectcolor="#2c1a00",
            activebackground=BG_TOOL, font=("Consolas", 8),
            command=lambda: setattr(self, "_lock_ratio", self._ratio_var.get()))
        self._ratio_chk_visible = False

        # Zoom (droite ligne 1)
        self._zoom_lbl = tk.Label(row1, text="100%", bg=BG_TOOL, fg="#8888bb",
                                  font=("Consolas", 9), width=6)
        self._zoom_lbl.pack(side=tk.RIGHT, padx=(0, 10))
        tk.Label(row1, text="Zoom :", bg=BG_TOOL, fg="#7777aa",
                 font=("Consolas", 8)).pack(side=tk.RIGHT)

        # Séparateur visuel entre les deux lignes
        tk.Frame(tb_outer, bg="#252545", height=1).pack(fill=tk.X)

        # ── Ligne 2 : Type token + actions + vue ─────────────────────────────
        row2 = tk.Frame(tb_outer, bg="#13131f", pady=4, padx=6)
        row2.pack(fill=tk.X)

        tk.Label(row2, text="Token :", bg="#13131f", fg="#9999bb",
                 font=("Consolas", 8)).pack(side=tk.LEFT, padx=(4, 2))
        self._tok_var = tk.StringVar(value="hero")
        for ttype, col in [("hero", "#5ba4f5"), ("monster", "#e04040"), ("trap", "#f0b030")]:
            tk.Radiobutton(
                row2, text=ttype.capitalize(), variable=self._tok_var, value=ttype,
                bg="#13131f", fg=col, selectcolor="#1a1a2e",
                activebackground="#13131f", font=("Consolas", 8),
                command=lambda t=ttype: setattr(self, "token_type", t),
            ).pack(side=tk.LEFT, padx=2)

        _sep(row2)
        tk.Label(row2, text="Taille :", bg="#13131f", fg="#9999bb",
                 font=("Consolas", 8)).pack(side=tk.LEFT, padx=(2, 1))
        self._tok_size_var = tk.StringVar(value="Medium")
        size_names = list(TOKEN_SIZES.keys())
        size_menu = tk.OptionMenu(row2, self._tok_size_var, *size_names)
        size_menu.config(bg="#1e1e30", fg="#ccccee", font=("Consolas", 8),
                         relief="flat", padx=4, pady=2,
                         highlightthickness=0, activebackground="#2a2a44")
        size_menu["menu"].config(bg="#1a1a2e", fg="#dde0e8", font=("Consolas", 8))
        size_menu.pack(side=tk.LEFT, padx=2)

        _sep(row2)

        for text, fg, bg_act, cmd in [
            ("+ Calque",         "#64b5f6", "#0e1e30", self._add_map_layer),
            ("Tout révéler",     "#81c784", "#0e2010", self._reveal_all),
            ("Tout cacher",      "#e57373", "#20100e", self._cover_all),
            ("Redimensionner",   "#9b8fc7", "#1a1020", self._resize_grid),
            ("Eff. Tokens",      "#ff8a65", "#2c1500", self._clear_all_tokens),
            ("Eff. Obstacles",   "#ff8a65", "#2c1500", self._clear_all_obstacles),
        ]:
            tk.Button(
                row2, text=text, bg="#1e1e30", fg=fg,
                font=("Consolas", 8), relief="flat", padx=7, pady=3,
                activebackground=bg_act, activeforeground=fg,
                command=cmd,
            ).pack(side=tk.LEFT, padx=2)

        _sep(row2)

        self._view_btn = tk.Button(
            row2, text="Vue MJ", bg="#2a1a3a", fg="#c77dff",
            font=("Consolas", 8, "bold"), relief="sunken", padx=8, pady=3,
            command=self._toggle_dm_view)
        self._view_btn.pack(side=tk.LEFT, padx=2)

        tk.Button(
            row2, text="→ Agents", bg="#1a2a1a", fg="#81c784",
            font=("Consolas", 8, "bold"), relief="flat", padx=8, pady=3,
            activebackground="#0e2010", activeforeground="#a5d6a7",
            command=self._send_to_agents,
        ).pack(side=tk.LEFT, padx=2)

        _sep(row2)

        self._grid_btn = tk.Button(
            row2, text="Grille ON", bg="#1e1e30", fg="#9999bb",
            font=("Consolas", 8), relief="flat", padx=7, pady=3,
            command=self._toggle_grid)
        self._grid_btn.pack(side=tk.LEFT, padx=2)

        # ── Sélecteur couleur de grille (cycle + menu déroulant) ─────────────
        self._build_grid_color_selector(row2)

        # Taille case + zoom (droite ligne 2)
        self._cellpx_lbl = tk.Label(row2, text=f"{self.cell_px}px",
                                    bg="#13131f", fg="#ccccee",
                                    font=("Consolas", 8, "bold"), width=5)
        self._cellpx_lbl.pack(side=tk.RIGHT, padx=(0, 8))
        tk.Label(row2, text="Case :", bg="#13131f", fg="#9999bb",
                 font=("Consolas", 8)).pack(side=tk.RIGHT, padx=(8, 2))

    # ─── Actions toolbar ─────────────────────────────────────────────────────

    def _toggle_dm_view(self):
        """Bascule entre vue MJ (fog transparent) et vue Joueur (fog opaque)."""
        # set_view_mode change _dm_view, invalide les fingerprints et
        # appelle _redraw_all_tokens() immédiatement — les tokens ennemis
        # dans le fog disparaissent sans attendre un zoom ou un scroll.
        self.set_view_mode(not self._dm_view)
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

    def place_new_token(self, name: str, ttype: str = "monster", size: float = 1.0,
                        hp: int = -1, max_hp: int = -1, ac: int = -1, conditions: list = None,
                        tactics: list = None, alignment: str = "",
                        portrait: str = "", source_name: str = ""):
        """Place un nouveau token depuis le tracker au centre du viewport (recherche libre en spirale).

        source_name : nom canonique du fichier image (ex. bestiary_name = "Rictavio")
                      utilisé pour résoudre token_art et portrait quand le nom
                      affiché diffère du nom de fichier (alias, déguisement, etc.).
        """
        W, H = self._wh
        sr_w, sr_h = W + 40, H + 40
        x0f, x1f = self.canvas.xview()
        y0f, y1f = self.canvas.yview()
        vx0 = max(0, int(x0f * sr_w))
        vy0 = max(0, int(y0f * sr_h))
        vx1 = min(W, int(x1f * sr_w))
        vy1 = min(H, int(y1f * sr_h))
        cx = (vx0 + vx1) / 2
        cy = (vy0 + vy1) / 2
        
        c_col = int(cx / self._cp)
        c_row = int(cy / self._cp)
        
        dirs = [(1, 0), (0, 1), (-1, 0), (0, -1)]
        r = 1
        d_idx = 0
        cur_col, cur_row = c_col, c_row
        
        def is_free(c, ro):
            for t in self.tokens:
                if int(round(t.get("col", 0))) == c and int(round(t.get("row", 0))) == ro:
                    return False
            return True
            
        found = False
        steps = 0
        
        if is_free(cur_col, cur_row):
            found = True
        else:
            while steps < 400:
                for _ in range(2):
                    for _ in range(r):
                        cur_col += dirs[d_idx][0]
                        cur_row += dirs[d_idx][1]
                        if 0 <= cur_col < self.cols and 0 <= cur_row < self.rows:
                            if is_free(cur_col, cur_row):
                                found = True
                                break
                    if found: break
                    d_idx = (d_idx + 1) % 4
                if found: break
                r += 1
                steps += 1
                
        if not found:
            cur_col, cur_row = c_col, c_row
            
        cur_col = max(0, min(cur_col, self.cols - 1))
        cur_row = max(0, min(cur_row, self.rows - 1))
        
        # Alignement : si non fourni, déduit du type (hero → ally, sinon hostile)
        resolved_alignment = alignment or ("ally" if ttype == "hero" else "hostile")
        # Résoudre token_art (images/tokens/) depuis source_name ou name
        _resolved_token_art = ""
        try:
            import re as _re
            from portrait_resolver import resolve_token_art
            _lookup = source_name.strip() if source_name.strip() else                       _re.sub(r"\s+\d+\s*$", "", name).strip()
            _resolved_token_art = resolve_token_art(_lookup)
        except Exception as _e:
            print(f"[CombatMap] resolve_token_art (place_new_token) : {_e}")

        tok = {
            "type":        ttype,
            "name":        name,
            "source_name": source_name,  # nom canonique pour résolution images
            "col":         cur_col,
            "row":         cur_row,
            "hp":          hp,
            "max_hp":      max_hp,
            "ac":          ac,
            "size":        size,
            "conditions":  conditions or [],
            "tactics":     tactics or [],
            "alignment":   resolved_alignment,
            "portrait":    portrait,          # portrait brut (tooltip)
            "token_art":   _resolved_token_art,  # art de token (canvas)
        }
        self.tokens.append(tok)
        self._save_state()
        self._redraw_all_tokens()
        self._notify_token_moved(name, ttype, cur_col, cur_row, cur_col, cur_row,
                                 source="mj", alignment=resolved_alignment)

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

        # ── Boîte de dialogue étendue ─────────────────────────────────────────
        tok_data = self._show_add_token_dialog(ttype, default)
        if tok_data is None:
            return

        size_key = self._tok_size_var.get()
        size     = TOKEN_SIZES.get(size_key, 1)

        # ── Résolution des images ─────────────────────────────────────────────
        # token_art (canvas) → images/tokens/  |  portrait (tooltip) → images/portraits/
        portrait_path  = ""
        token_art_path = ""
        try:
            import re as _re
            tok_name = tok_data["name"]
            base = _re.sub(r"\s+\d+\s*$", "", tok_name).strip()
            if ttype == "hero":
                from state_manager import load_state as _ls
                _st = _ls()
                cdata = _st.get("characters", {}).get(tok_name) or {}
                portrait_path = cdata.get("image") or cdata.get("portrait") or ""
            from portrait_resolver import resolve_portrait, resolve_token_art
            if not portrait_path or not __import__("os").path.exists(portrait_path):
                portrait_path  = resolve_portrait(base)
            token_art_path = resolve_token_art(base)
        except Exception as _pe:
            print(f"[CombatMap] Portrait resolver (_add_token) : {_pe}")

        tok = {
            "type":        ttype,
            "name":        tok_data["name"],
            "source_name": "",  # non renseigné pour les tokens manuels
            "col":         col,
            "row":         row,
            "hp":          tok_data.get("hp", -1),
            "max_hp":      tok_data.get("max_hp", -1),
            "size":        size,
            "conditions":  [],
            "alignment":   tok_data.get("alignment", "ally" if ttype == "hero" else "hostile"),
            "portrait":    portrait_path,
            "token_art":   token_art_path,
        }
        self.tokens.append(tok)
        self._draw_one_token(tok)
        self._save_state()

    def _show_add_token_dialog(self, ttype: str, default_name: str) -> "dict | None":
        """Fenêtre modale pour créer un token : nom + HP + alignement."""
        result = {}
        dw = tk.Toplevel(self.win)
        dw.title("Nouveau token")
        dw.geometry("300x240")
        dw.configure(bg="#0d1018")
        dw.resizable(False, False)
        dw.wait_visibility()
        dw.grab_set()

        fg_accent = {"hero": "#64b5f6", "monster": "#ef5350", "trap": "#ffd54f"}.get(ttype, "#aaaacc")

        tk.Label(dw, text=f"Nouveau {ttype}", bg="#0d1018", fg=fg_accent,
                 font=("Consolas", 10, "bold")).pack(pady=(10, 4))

        # Nom
        frm_name = tk.Frame(dw, bg="#0d1018")
        frm_name.pack(fill=tk.X, padx=14, pady=2)
        tk.Label(frm_name, text="Nom :", bg="#0d1018", fg="#aaaacc",
                 font=("Consolas", 8), width=8, anchor="w").pack(side=tk.LEFT)
        name_var = tk.StringVar(value=default_name)
        tk.Entry(frm_name, textvariable=name_var, bg="#252538", fg="#eeeeee",
                 font=("Consolas", 9), insertbackground=fg_accent,
                 relief="flat").pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)

        # HP (seulement pour héros et monstres)
        hp_var    = tk.StringVar(value="")
        maxhp_var = tk.StringVar(value="")
        if ttype in ("hero", "monster"):
            frm_hp = tk.Frame(dw, bg="#0d1018")
            frm_hp.pack(fill=tk.X, padx=14, pady=2)
            tk.Label(frm_hp, text="PV :", bg="#0d1018", fg="#aaaacc",
                     font=("Consolas", 8), width=8, anchor="w").pack(side=tk.LEFT)
            tk.Entry(frm_hp, textvariable=hp_var, bg="#252538", fg="#ef9a9a",
                     font=("Consolas", 9), insertbackground="#ef5350",
                     relief="flat", width=6).pack(side=tk.LEFT, ipady=3, padx=(0,4))
            tk.Label(frm_hp, text="/", bg="#0d1018", fg="#666688",
                     font=("Consolas", 9)).pack(side=tk.LEFT)
            tk.Entry(frm_hp, textvariable=maxhp_var, bg="#252538", fg="#ef9a9a",
                     font=("Consolas", 9), insertbackground="#ef5350",
                     relief="flat", width=6).pack(side=tk.LEFT, ipady=3, padx=(4,0))
            tk.Label(frm_hp, text="(PV actuels / max)", bg="#0d1018", fg="#555577",
                     font=("Consolas", 7)).pack(side=tk.LEFT, padx=4)

        # ── Alignement ────────────────────────────────────────────────────────
        frm_align = tk.Frame(dw, bg="#0d1018")
        frm_align.pack(fill=tk.X, padx=14, pady=(6, 2))
        tk.Label(frm_align, text="Align. :", bg="#0d1018", fg="#aaaacc",
                 font=("Consolas", 8), width=8, anchor="w").pack(side=tk.LEFT)

        # Valeur par défaut : hero → allié, autres → hostile
        default_align = "ally" if ttype == "hero" else "hostile"
        align_var = tk.StringVar(value=default_align)

        _ALIGN_CFG = [
            ("🔴 Hostile", "hostile", "#e53935", "#3a0a0a"),
            ("🟡 Neutre",  "neutral", "#fdd835", "#3a3200"),
            ("🟢 Allié",   "ally",    "#43a047", "#0a2a0a"),
        ]
        align_btns = {}

        def _select_align(v):
            align_var.set(v)
            for val, btn in align_btns.items():
                cfg = next(c for c in _ALIGN_CFG if c[1] == val)
                if val == v:
                    btn.config(bg=cfg[3], fg=cfg[2], relief="sunken")
                else:
                    btn.config(bg="#1a1a2a", fg="#555577", relief="flat")

        for _lbl, _val, _fg, _bg in _ALIGN_CFG:
            btn = tk.Button(frm_align, text=_lbl,
                            bg="#1a1a2a", fg="#555577",
                            font=("Consolas", 8), relief="flat",
                            padx=6, pady=2, cursor="hand2",
                            command=lambda v=_val: _select_align(v))
            btn.pack(side=tk.LEFT, padx=2)
            align_btns[_val] = btn

        # Activer le bouton par défaut
        _select_align(default_align)

        def _confirm(event=None):
            name = name_var.get().strip()
            if not name:
                return
            try:
                hp     = int(hp_var.get())    if hp_var.get().strip()    else -1
                max_hp = int(maxhp_var.get()) if maxhp_var.get().strip() else hp
            except ValueError:
                hp = max_hp = -1
            result["name"]      = name
            result["hp"]        = hp
            result["max_hp"]    = max(hp, max_hp) if hp > 0 else -1
            result["alignment"] = align_var.get()
            dw.destroy()

        def _cancel(event=None):
            dw.destroy()

        dw.bind("<Return>", _confirm)
        dw.bind("<Escape>", _cancel)
        btn_row = tk.Frame(dw, bg="#0d1018")
        btn_row.pack(pady=10)
        tk.Button(btn_row, text="Créer", bg="#1a2a1a", fg=fg_accent,
                  font=("Consolas", 9, "bold"), relief="flat", padx=12, pady=4,
                  command=_confirm).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_row, text="Annuler", bg="#1a1a2a", fg="#666688",
                  font=("Consolas", 9), relief="flat", padx=8, pady=4,
                  command=_cancel).pack(side=tk.LEFT, padx=6)

        dw.wait_window()
        return result if result else None

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
        cols = simpledialog.askinteger("Colonnes", "Colonnes (5–160) :",
            initialvalue=self.cols, minvalue=5, maxvalue=160, parent=self.win)
        if cols is None:
            return
        rows = simpledialog.askinteger("Lignes", "Lignes (5–100) :",
            initialvalue=self.rows, minvalue=5, maxvalue=100, parent=self.win)
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

        # ── Injection dans autogen (texte uniquement — compatible tous modèles) ──
        # Hors-combat uniquement : pendant le combat, les agents ont déjà la carte
        # dans leur system prompt et une injection ici provoquerait des réponses
        # non sollicitées hors-tour.
        try:
            from combat_tracker import COMBAT_STATE as _CS_desc
            _combat_active_desc = _CS_desc.get("active", False)
        except Exception:
            _combat_active_desc = False

        if self.inject_fn is not None and not _combat_active_desc:
            # Hors-combat : l'appel à inject_fn va déjà ajouter le texte à l'UI
            self.inject_fn(desc)
            if self.msg_queue is not None and img_path:
                self.msg_queue.put({
                    "sender": "Système de Sauvegarde",
                    "text":   f"Image de la carte sauvegardée : 📁 {img_path}",
                    "color":  "#9e9e9e",
                })
        else:
            # En combat : pas d'injection pour éviter le spam, on affiche l'état exact pour le MJ uniquement
            if self.msg_queue is not None:
                self.msg_queue.put({
                    "sender": "Carte de Combat",
                    "text":   desc + (f"\n📁 {img_path}" if img_path else ""),
                    "color":  "#64b5f6",
                })

    def _build_map_description(self) -> str:
        """Description textuelle restreinte à la zone visible à l'écran (zoom + scroll).
        Inclut les altitudes de vol et les distances 3D pour les agents."""
        import math as _math
        cp_px  = self.cell_px
        mw, mh = self.cols * cp_px, self.rows * cp_px
        mask   = self._fog_mask if self._fog_mask else Image.new("L", (mw, mh), 255)
        mask_arr = np.array(mask)

        def _cell_covered(c, r):
            px = min(int((c + 0.5) * cp_px), mw - 1)
            py = min(int((r + 0.5) * cp_px), mh - 1)
            return mask_arr[py, px] > 127

        def _horiz_ft(t1, t2) -> float:
            c1, r1 = int(t1["col"]), int(t1["row"])
            c2, r2 = int(t2["col"]), int(t2["row"])
            s1 = max(1, int(float(t1.get("size", 1))))
            s2 = max(1, int(float(t2.get("size", 1))))
            
            def _dist1d(a, a_sz, b, b_sz):
                a_end = a + a_sz - 1
                b_end = b + b_sz - 1
                if a_end < b: return b - a_end
                if b_end < a: return a - b_end
                return 0
                
            return max(_dist1d(c1, s1, c2, s2), _dist1d(r1, s1, r2, s2)) * 5.0

        def _d3d_ft(t1, t2) -> float:
            h = _horiz_ft(t1, t2)
            v = abs(int(t1.get("altitude_ft", 0)) - int(t2.get("altitude_ft", 0)))
            return max(float(h), float(v))

        def _tok_label(tok) -> str:
            name = tok.get("name", tok["type"])
            alt  = int(tok.get("altitude_ft", 0))
            pos  = f"Col {int(tok['col'])+1}, Lig {int(tok['row'])+1}"
            if alt > 0:
                return f"{name} ({tok['type']}) → {pos}  ✈ EN VOL {alt}ft"
            return f"{name} ({tok['type']}) → {pos}  [sol]"

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
            "Distances : 1-1-1 en 3D — dist_3D = max(horiz, Δalt). Mêlée ≤5ft 3D. Reach ≤10ft 3D.",
            "",
        ]

        visible_tokens, hidden_tokens = [], []
        for tok in self.tokens:
            c, r = int(tok["col"]), int(tok["row"])
            if not (col_min <= c <= col_max and row_min <= r <= row_max):
                continue
            label = _tok_label(tok)
            if not _cell_covered(c, r):
                visible_tokens.append((tok, label))
            else:
                hidden_tokens.append(label)

        if visible_tokens:
            lines.append("Tokens visibles :")
            for _, lbl in visible_tokens: lines.append(f"  • {lbl}")
        else:
            lines.append("Aucun token visible dans ce cadre.")
        if hidden_tokens:
            lines.append("Tokens sous brouillard dans ce cadre :")
            for lbl in hidden_tokens: lines.append(f"  ? {lbl}")

        # ── Bloc distances 3D entre tokens visibles ────────────────────────────
        vis_toks = [tok for tok, _ in visible_tokens]

        def _is_ally_desc(t):
            a = t.get("alignment", "")
            if a == "ally": return True
            if a == "hostile": return False
            return t.get("type") == "hero"

        def _is_hostile_desc(t):
            a = t.get("alignment", "")
            if a == "hostile": return True
            if a == "ally": return False
            return t.get("type") == "monster"

        heroes   = [t for t in vis_toks if _is_ally_desc(t)]
        enemies  = [t for t in vis_toks if _is_hostile_desc(t)]

        if heroes and enemies:
            lines.append("\n📏 DISTANCES 3D HÉROS → ENNEMIS :")
            for h in heroes:
                h_alt = int(h.get("altitude_ft", 0))
                h_name = h.get("name", "Héros")
                for e in sorted(enemies, key=lambda m: _d3d_ft(h, m))[:3]:
                    horiz = _horiz_ft(h, e)
                    dalt  = abs(h_alt - int(e.get("altitude_ft", 0)))
                    d3d   = _d3d_ft(h, e)
                    e_name = e.get("name", "Ennemi")
                    if dalt == 0:
                        breakdown = f"{horiz:.0f}ft horiz, même altitude"
                    else:
                        breakdown = f"max({horiz:.0f}ft ↔, {dalt}ft ↕) = {d3d:.0f}ft 3D"
                    if d3d <= 5:
                        verdict = "mêlée ✅"
                    elif d3d <= 10:
                        verdict = "mêlée Reach ✅"
                    else:
                        verdict = "portée distance 🏹"
                    lines.append(f"  • {h_name} → {e_name} : {breakdown} — {verdict}")

        if len(vis_toks) >= 2:
            lines.append("\n📏 DISTANCES 3D ENTRE ALLIÉS :")
            done = set()
            for i, t1 in enumerate(heroes):
                for t2 in heroes[i+1:]:
                    key = (id(t1), id(t2))
                    if key in done: continue
                    done.add(key)
                    horiz = _horiz_ft(t1, t2)
                    dalt  = abs(int(t1.get("altitude_ft",0)) - int(t2.get("altitude_ft",0)))
                    d3d   = _d3d_ft(t1, t2)
                    breakdown = (f"max({horiz:.0f}ft ↔, {dalt}ft ↕) = {d3d:.0f}ft 3D"
                                 if dalt else f"{horiz:.0f}ft")
                    lines.append(f"  • {t1.get('name','?')} ↔ {t2.get('name','?')} : {breakdown}")

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

        # ── Grille (couleur dynamique selon le mode actif) ───────────────────
        if self._show_grid and cp >= 4:
            grid_c = getattr(self, "_grid_color", _C_GRID)
            bg_arr = np.array(bg, dtype=np.float32)
            gc = np.array(grid_c[:3], dtype=np.float32)
            ga = grid_c[3] / 255.0
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