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

class MapManagerMixin:
    pass
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
            tok = {k: v for k, v in t.items() if k != "ids"}
            tok.setdefault("hp",          -1)
            tok.setdefault("max_hp",      -1)
            tok.setdefault("ac",          -1)
            tok.setdefault("size",         1)
            tok.setdefault("conditions",  [])
            tok.setdefault("altitude_ft",  0)   # 0 = au sol, >0 = en vol (pieds D&D)
            self.tokens.append(tok)

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

        for obs in data.get("obstacles", []):
            pts = obs.get("pts", [])
            if len(pts) >= 2:
                self._obstacles.append({
                    "pts":   [tuple(p) for p in pts],
                    "color": obs.get("color", "#cc4400"),
                    "label": obs.get("label", ""),
                    "type":  obs.get("type", "poly"),
                })

    def _save_state(self):
        """Sauvegarde la carte active dans son fichier JSON + win_state."""
        self._save_current_map()
        # win_state garde la carte active et les géométries de fenêtres
        self.win_state["active_map_name"] = self._active_map_name
        self.save_fn()

    # ─── Système multi-cartes ─────────────────────────────────────────────────

    def _get_maps_dir(self) -> str:
        """Retourne (et crée si besoin) le dossier campagne/<nom>/maps/."""
        try:
            from app_config import get_campaign_name
            camp_name = get_campaign_name()
        except Exception:
            camp_name = "campagne"
        camp_name = "".join(
            c for c in camp_name if c.isalnum() or c in (" ", "-", "_")
        ).strip() or "campagne"
        maps_dir = os.path.join("campagne", camp_name, "maps")
        os.makedirs(maps_dir, exist_ok=True)
        return maps_dir

    def _map_file(self, name: str) -> str:
        """Retourne le chemin complet du fichier JSON d'une carte."""
        safe = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_")).strip()
        safe = safe or "carte"
        return os.path.join(self._get_maps_dir(), f"{safe}.json")

    def _list_maps(self) -> list:
        """Retourne la liste triée des noms de cartes sauvegardées."""
        try:
            maps_dir = self._get_maps_dir()
            names = []
            for fname in sorted(os.listdir(maps_dir)):
                if fname.endswith(".json"):
                    names.append(fname[:-5])
            return names if names else []
        except Exception:
            return []

    def _init_maps_system(self):
        """
        Initialise le système multi-cartes au démarrage.
        Charge la carte active depuis win_state, ou crée 'Carte 1' si aucune.
        Rétro-compatible : migre l'ancien combat_map_data si présent.
        """
        maps_dir = self._get_maps_dir()

        # ── Migration rétro-compat : ancien format → fichier ─────────────────
        legacy = self.win_state.get("combat_map_data")
        if legacy:
            if not self._list_maps():
                # Premier lancement avec le nouveau système : migrer l'existant
                default_name = "Carte 1"
                try:
                    import json
                    with open(self._map_file(default_name), "w", encoding="utf-8") as f:
                        json.dump(legacy, f, indent=2, ensure_ascii=False)
                    print(f"[MapSystem] Migré ancien état → {default_name}")
                except Exception as e:
                    print(f"[MapSystem] Erreur migration : {e}")
            
            # Purger la donnée obsolète pour empêcher un dump JSON massif toutes les 2s
            self.win_state.pop("combat_map_data", None)
            try:
                self.save_fn()
            except Exception:
                pass

        # ── Sélectionner la carte active ──────────────────────────────────────
        maps = self._list_maps()
        saved_name = self.win_state.get("active_map_name", "")
        if saved_name and saved_name in maps:
            self._active_map_name = saved_name
        elif maps:
            self._active_map_name = maps[0]
        else:
            # Aucune carte → en créer une vide
            self._active_map_name = "Carte 1"
            self._save_current_map()   # crée le fichier vide

        self._load_map(self._active_map_name)

    def _current_map_data(self) -> dict:
        """Sérialise l'état courant de la carte en dict sauvegardable."""
        import base64, io as _io
        fog_b64 = ""
        if self._fog_mask is not None:
            buf = _io.BytesIO()
            self._fog_mask.save(buf, "PNG")
            fog_b64 = base64.b64encode(buf.getvalue()).decode()
        try:
            scroll_x = self.canvas.xview()[0]
            scroll_y = self.canvas.yview()[0]
        except Exception:
            scroll_x = getattr(self, "_scroll_fx", 0.0)
            scroll_y = getattr(self, "_scroll_fy", 0.0)

        return {
            "cols":             self.cols,
            "rows":             self.rows,
            "cell_px":          self.cell_px,
            "zoom":             self.zoom,
            "scroll_x":         scroll_x,
            "scroll_y":         scroll_y,
            "fog_mask_b64":     fog_b64,
            "tokens":           [{k: v for k, v in t.items()
                                  if not k.startswith("_") and k != "ids"}
                                 for t in self.tokens if not t.get("is_preview", False)],
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
            "obstacles":        [{"pts": obs["pts"], "color": obs["color"],
                                  "label": obs["label"], "type": obs["type"]}
                                 for obs in self._obstacles],
        }

    def _save_current_map(self):
        """Sauvegarde l'état courant dans le fichier JSON de la carte active (écriture atomique)."""
        if not self._active_map_name:
            return
        import json, tempfile, os as _os
        tmp_path = None
        try:
            data = self._current_map_data()
            dest = self._map_file(self._active_map_name)
            dir_name = _os.path.dirname(dest)
            with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=dir_name,
                    delete=False, suffix=".tmp") as tmp:
                json.dump(data, tmp, indent=2, ensure_ascii=False)
                tmp_path = tmp.name
            _os.replace(tmp_path, dest)
        except Exception as e:
            print(f"[MapSystem] Erreur sauvegarde '{self._active_map_name}' : {e}")
            if tmp_path:
                try:
                    _os.unlink(tmp_path)
                except Exception:
                    pass

    def _load_map(self, name: str):
        """Charge une carte depuis son fichier JSON dans l'état courant."""
        try:
            import json
            path = self._map_file(name)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {}
        except Exception as e:
            print(f"[MapSystem] Erreur chargement '{name}' : {e}")
            data = {}

        # Reset de l'état courant avant de charger
        self.zoom    = 1.0
        self.cols    = 30
        self.rows    = 20
        self.tokens  = []
        self.cell_px = CELL_PX_DEFAULT
        self.map_layers = []
        self._active_layer_idx = 0
        self._ensure_default_layer()
        self._scroll_fx = 0.0
        self._scroll_fy = 0.0
        self._fog_mask  = None
        self._fog_pil   = None
        self._bg_pil    = None
        self._obs_pil   = None
        self._notes     = []
        self._doors     = []
        self._obstacles = []
        self._map_pil_cache_dict = {}
        self._fog_undo_stack = []
        self._selected_tokens.clear()
        self._drag_token    = None
        self._drag_origins  = {}

        self._load_from_saved(data)

    def _switch_map(self, name: str):
        """Sauvegarde la carte courante et charge une autre."""
        if name == self._active_map_name:
            return
        self._save_current_map()
        self._active_map_name = name
        self.win_state["active_map_name"] = name
        self.save_fn()
        self._load_map(name)
        # Rebuild complet du canvas
        self._img_id = 0
        self.canvas.delete("all")
        self._poly_points.clear()
        self._poly_ids.clear()
        self._obs_poly_pts.clear()
        self._obs_poly_ids.clear()
        self.canvas.delete("ruler")
        self._ruler_start_pt = None
        self._ruler_ids = []
        self._full_redraw()
        self.win.after(80, self._restore_view)
        self._refresh_map_selector()
        self.win.title(f"Carte de Combat — {name}")
        if self.msg_queue:
            self.msg_queue.put({
                "sender": "🗺️ Carte",
                "text":   f"Carte chargée : {name}",
                "color":  "#64b5f6",
            })

    # ── Actions CRUD cartes ───────────────────────────────────────────────────

    def _add_map(self):
        """Dialogue + création d'une nouvelle carte vide."""
        maps = self._list_maps()
        n = len(maps) + 1
        default = f"Carte {n}"
        while default in maps:
            n += 1
            default = f"Carte {n}"

        name = simpledialog.askstring(
            "Nouvelle carte", "Nom de la nouvelle carte :",
            initialvalue=default, parent=self.win)
        if not name or not name.strip():
            return
        name = name.strip()
        maps = self._list_maps()
        if name in maps:
            messagebox.showinfo("Carte existante",
                                f"Une carte '{name}' existe déjà.", parent=self.win)
            return
        # Créer fichier vide puis basculer dessus
        self._save_current_map()
        self._active_map_name = name
        # Réinitialiser l'état pour une carte vierge
        self.cols, self.rows, self.zoom = 30, 20, 1.0
        self.cell_px = CELL_PX_DEFAULT
        self.tokens, self._notes, self._doors, self._obstacles = [], [], [], []
        self.map_layers = []
        self._ensure_default_layer()
        self._fog_mask = Image.new("L", (self.cols * self.cell_px,
                                        self.rows * self.cell_px), 255)
        self._fog_undo_stack = []
        self._save_current_map()
        self.win_state["active_map_name"] = name
        self.save_fn()
        # Rebuild UI
        self._img_id = 0
        self.canvas.delete("all")
        self._full_redraw()
        self._refresh_map_selector()
        self.win.title(f"Carte de Combat — {name}")

    def _delete_map(self):
        """Supprime la carte active (avec confirmation). Bascule sur une autre."""
        maps = self._list_maps()
        if len(maps) <= 1:
            messagebox.showinfo("Suppression impossible",
                                "Il faut au moins une carte.", parent=self.win)
            return
        name = self._active_map_name
        if not messagebox.askyesno("Supprimer carte",
                                   f"Supprimer définitivement « {name} » ?",
                                   parent=self.win):
            return
        try:
            os.remove(self._map_file(name))
        except Exception as e:
            print(f"[MapSystem] Erreur suppression '{name}' : {e}")
        maps = self._list_maps()
        new_name = maps[0] if maps else "Carte 1"
        if not maps:
            self._active_map_name = new_name
            self._save_current_map()
        self._active_map_name = ""   # force le switch
        self._switch_map(new_name)

    def _rename_map(self):
        """Renomme la carte active (renomme le fichier JSON)."""
        old_name = self._active_map_name
        new_name = simpledialog.askstring(
            "Renommer la carte", "Nouveau nom :",
            initialvalue=old_name, parent=self.win)
        if not new_name or not new_name.strip():
            return
        new_name = new_name.strip()
        if new_name == old_name:
            return
        maps = self._list_maps()
        if new_name in maps:
            messagebox.showinfo("Nom déjà pris",
                                f"Une carte '{new_name}' existe déjà.", parent=self.win)
            return
        # Sauvegarder sous le nouveau nom puis supprimer l'ancien
        self._save_current_map()
        old_path = self._map_file(old_name)
        new_path = self._map_file(new_name)
        try:
            os.rename(old_path, new_path)
        except Exception as e:
            print(f"[MapSystem] Erreur renommage : {e}")
            return
        self._active_map_name = new_name
        self.win_state["active_map_name"] = new_name
        self.save_fn()
        self._refresh_map_selector()
        self.win.title(f"Carte de Combat — {new_name}")

    def _duplicate_map(self):
        """Duplique la carte active sous un nouveau nom."""
        import json
        old_name = self._active_map_name
        self._save_current_map()
        maps = self._list_maps()
        default = f"{old_name} (copie)"
        n = 2
        while default in maps:
            default = f"{old_name} (copie {n})"
            n += 1
        new_name = simpledialog.askstring(
            "Dupliquer la carte", "Nom de la copie :",
            initialvalue=default, parent=self.win)
        if not new_name or not new_name.strip():
            return
        new_name = new_name.strip()
        maps = self._list_maps()
        if new_name in maps:
            messagebox.showinfo("Nom déjà pris",
                                f"Une carte '{new_name}' existe déjà.", parent=self.win)
            return
        try:
            with open(self._map_file(old_name), "r", encoding="utf-8") as f:
                data = json.load(f)
            with open(self._map_file(new_name), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[MapSystem] Erreur duplication : {e}")
            return
        self._switch_map(new_name)

    # ── Barre de sélection de cartes ─────────────────────────────────────────

    def _build_map_selector_bar(self):
        """Construit la barre de sélection de cartes entre toolbar et canvas."""
        bar = tk.Frame(self.win, bg="#0e0e1c", pady=3)
        bar.pack(fill=tk.X, side=tk.TOP)
        self._map_bar = bar

        tk.Label(bar, text="CARTE :", bg="#0e0e1c", fg="#5555aa",
                 font=("Consolas", 8, "bold")).pack(side=tk.LEFT, padx=(8, 4))

        # ── Bouton + (ajouter) ────────────────────────────────────────────────
        tk.Button(bar, text="+ Carte", bg="#0e1e2c", fg="#64b5f6",
                  font=("Consolas", 8, "bold"), relief="flat", padx=7, pady=2,
                  activebackground="#1a2a3a", activeforeground="#90caf9",
                  cursor="hand2", command=self._add_map
                  ).pack(side=tk.LEFT, padx=2)

        # ── Bouton ✕ (supprimer) ──────────────────────────────────────────────
        tk.Button(bar, text="✕ Suppr.", bg="#200a0a", fg="#e57373",
                  font=("Consolas", 8), relief="flat", padx=7, pady=2,
                  activebackground="#3a1010", activeforeground="#ef9a9a",
                  cursor="hand2", command=self._delete_map
                  ).pack(side=tk.LEFT, padx=2)

        # ── Séparateur ────────────────────────────────────────────────────────
        tk.Frame(bar, bg="#252545", width=1, height=20).pack(side=tk.LEFT, padx=6)

        # ── Dropdown sélecteur ────────────────────────────────────────────────
        tk.Label(bar, text="Active :", bg="#0e0e1c", fg="#9999bb",
                 font=("Consolas", 8)).pack(side=tk.LEFT, padx=(0, 3))

        self._map_selector_var = tk.StringVar(value=self._active_map_name)
        maps = self._list_maps() or [self._active_map_name]
        self._map_dropdown = tk.OptionMenu(
            bar, self._map_selector_var, *maps,
            command=self._on_map_selected)
        self._map_dropdown.config(
            bg="#1a1a2e", fg="#e0e0ff", font=("Consolas", 9, "bold"),
            relief="flat", padx=8, pady=2,
            highlightthickness=0, activebackground="#2a2a4e",
            indicatoron=True, width=18)
        self._map_dropdown["menu"].config(
            bg="#1a1a2e", fg="#e0e0ff", font=("Consolas", 9),
            activebackground="#2a2a4e", activeforeground="#ffffff")
        self._map_dropdown.pack(side=tk.LEFT, padx=2)

        # ── Bouton ✏ renommer ─────────────────────────────────────────────────
        tk.Button(bar, text="✏ Renommer", bg="#1e1e2e", fg="#ffb74d",
                  font=("Consolas", 8), relief="flat", padx=7, pady=2,
                  activebackground="#2c1a00", activeforeground="#ffe082",
                  cursor="hand2", command=self._rename_map
                  ).pack(side=tk.LEFT, padx=2)

        # ── Bouton ⧉ dupliquer ────────────────────────────────────────────────
        tk.Button(bar, text="⧉ Dupliquer", bg="#1a1a2e", fg="#ce93d8",
                  font=("Consolas", 8), relief="flat", padx=7, pady=2,
                  activebackground="#1a0a2a", activeforeground="#e1bee7",
                  cursor="hand2", command=self._duplicate_map
                  ).pack(side=tk.LEFT, padx=2)

        # ── Nom courant affiché à droite ──────────────────────────────────────
        self._map_name_lbl = tk.Label(
            bar, text=f"📍 {self._active_map_name}",
            bg="#0e0e1c", fg="#6666cc",
            font=("Consolas", 8, "italic"))
        self._map_name_lbl.pack(side=tk.RIGHT, padx=10)

    def _on_map_selected(self, name: str):
        """Callback du OptionMenu — bascule sur la carte sélectionnée."""
        self._switch_map(name)

    def _refresh_map_selector(self):
        """Met à jour le dropdown et le label avec la liste courante des cartes."""
        if self._map_selector_var is None:
            return
        maps = self._list_maps()
        if not maps:
            maps = [self._active_map_name]
        # Rebuild du menu
        menu = self._map_dropdown["menu"]
        menu.delete(0, "end")
        for name in maps:
            menu.add_command(
                label=name,
                command=lambda n=name: (
                    self._map_selector_var.set(n),
                    self._on_map_selected(n)
                ))
        self._map_selector_var.set(self._active_map_name)
        if hasattr(self, "_map_name_lbl"):
            self._map_name_lbl.config(text=f"📍 {self._active_map_name}")

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