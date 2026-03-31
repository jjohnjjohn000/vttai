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

class LayerManagerMixin:
    pass
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

