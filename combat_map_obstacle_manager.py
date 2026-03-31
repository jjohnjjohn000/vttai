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

class ObstacleManagerMixin:
    pass
    # ─── Obstacles (polygone + main levée) ───────────────────────────────────

    def _pick_obstacle_color(self):
        """Ouvre le sélecteur de couleur natif pour choisir la couleur des obstacles."""
        from tkinter import colorchooser
        color = colorchooser.askcolor(
            color=self._obs_color,
            title="Couleur de l'obstacle",
            parent=self.win)
        if color and color[1]:
            self._obs_color = color[1]
            self._obs_color_btn.config(bg=self._obs_color)

    # ── Outil polygone ────────────────────────────────────────────────────────

    def _obs_poly_add(self, cx: float, cy: float):
        """Ajoute un sommet au polygone obstacle en cours."""
        pts = self._obs_poly_pts
        col = self._obs_color
        if pts:
            x0, y0 = pts[-1]
            iid = self.canvas.create_line(
                x0 * self.zoom, y0 * self.zoom,
                cx, cy,
                fill=col, width=2, dash=(4, 2), tags="obs_preview")
            self._obs_poly_ids.append(iid)
        r = 4
        iid = self.canvas.create_oval(
            cx-r, cy-r, cx+r, cy+r,
            outline=col, fill="#1a1a1a", width=2, tags="obs_preview")
        self._obs_poly_ids.append(iid)
        # Stocke en coordonnées monde (indépendant du zoom)
        pts.append((cx / self.zoom, cy / self.zoom))
        self._obs_poly_update_preview(cx, cy)

    def _obs_poly_update_preview(self, cx: float, cy: float):
        self.canvas.delete("obs_preview_cursor")
        pts = self._obs_poly_pts
        if not pts:
            return
        col = self._obs_color
        x0, y0 = pts[-1]
        self.canvas.create_line(
            x0 * self.zoom, y0 * self.zoom, cx, cy,
            fill=col, width=2, dash=(4, 3),
            tags=("obs_preview", "obs_preview_cursor"))
        if len(pts) >= 2:
            x1, y1 = pts[0]
            self.canvas.create_line(
                cx, cy, x1 * self.zoom, y1 * self.zoom,
                fill=col, width=1, dash=(2, 6),
                tags=("obs_preview", "obs_preview_cursor"))

    def _obs_poly_apply(self):
        """Valide le polygone et ouvre une mini-fenêtre pour le label."""
        pts = self._obs_poly_pts
        if len(pts) < 3:
            self._obs_cancel()
            return
        self.canvas.delete("obs_preview")
        pts_copy = list(pts)
        color    = self._obs_color
        self._obs_poly_pts = []
        self._obs_poly_ids = []
        self._obs_ask_label(pts_copy, color, "poly")

    # ── Outil main levée ──────────────────────────────────────────────────────

    def _obs_free_start(self, cx: float, cy: float):
        """Commence un tracé main levée."""
        self._obs_free_pts = [(cx / self.zoom, cy / self.zoom)]
        self._obs_free_id  = self.canvas.create_line(
            cx, cy, cx, cy,
            fill=self._obs_color, width=3, tags="obs_preview")

    def _obs_free_move(self, cx: float, cy: float):
        """Ajoute un point au tracé en cours."""
        if not self._obs_free_pts:
            return
        self._obs_free_pts.append((cx / self.zoom, cy / self.zoom))
        # Met à jour la ligne canvas
        flat = [c * self.zoom for pt in self._obs_free_pts for c in pt]
        if len(flat) >= 4:
            self.canvas.coords(self._obs_free_id, *flat)

    def _obs_free_end(self):
        """Termine le tracé main levée et ouvre la fenêtre de label."""
        pts = self._obs_free_pts
        self.canvas.delete("obs_preview")
        self._obs_free_id  = 0
        self._obs_free_pts = []
        if len(pts) < 2:
            return
        # Ferme automatiquement le contour si assez de points
        if len(pts) >= 3:
            pts = pts + [pts[0]]
        self._obs_ask_label(pts, self._obs_color, "free")

    # ── Dialogue label + validation ───────────────────────────────────────────

    def _obs_ask_label(self, pts: list, color: str, obs_type: str):
        """Mini-fenêtre pour nommer l'obstacle avant de l'enregistrer."""
        dw = tk.Toplevel(self.win)
        dw.title("Nouvel obstacle")
        dw.geometry("300x120")
        dw.configure(bg="#0d1018")
        dw.resizable(False, False)
        dw.wait_visibility()
        dw.grab_set()

        tk.Label(dw, text="Label de l'obstacle (optionnel) :",
                 bg="#0d1018", fg="#ff9955",
                 font=("Consolas", 9, "bold")).pack(pady=(12, 2))
        entry = tk.Entry(dw, bg="#252538", fg="#eeeeee",
                         font=("Consolas", 10), insertbackground="#ff9955",
                         relief="flat", width=28)
        entry.pack(padx=14, ipady=3)
        entry.focus_set()

        def _confirm(event=None):
            label = entry.get().strip()
            dw.destroy()
            obs = {"pts": pts, "color": color, "label": label, "type": obs_type}
            self._obstacles.append(obs)
            self._obs_pil = None   # invalide le cache
            self._composite()
            self._save_state()

        entry.bind("<Return>", _confirm)
        tk.Button(dw, text="Valider", bg="#2c1000", fg="#ff9955",
                  font=("Consolas", 9, "bold"), relief="flat",
                  command=_confirm).pack(pady=8)

    # ── Suppression ───────────────────────────────────────────────────────────

    def _obs_delete_at(self, cx: float, cy: float):
        """Supprime l'obstacle dont la forme contient le point (cx, cy) canvas.
        Utilisé par le clic droit en mode obstacle_poly / select."""
        wx, wy = cx / self.zoom, cy / self.zoom
        for obs in reversed(self._obstacles):
            if self._obs_contains(obs["pts"], wx, wy) or \
               self._obs_near_segments(obs["pts"], wx, wy, tol=12 / self.zoom):
                self._obstacles.remove(obs)
                self._obs_pil = None
                self._composite()
                self._save_state()
                return

    def _obs_erase_at(self, cx: float, cy: float):
        """Outil efface : supprime tout obstacle dont un segment passe dans le
        rayon du pinceau autour de (cx, cy) en coordonnées canvas.
        Fonctionne sur les traits fins (type free) ET les polygones remplis."""
        brush_r = max(self._brush_var.get(), 1) * self._cp * 0.5
        tol_world = brush_r / self.zoom
        wx, wy = cx / self.zoom, cy / self.zoom
        removed = []
        for obs in self._obstacles:
            pts = obs["pts"]
            # Test 1 : proximité sur les segments (traits fins main levée)
            if self._obs_near_segments(pts, wx, wy, tol=tol_world):
                removed.append(obs)
                continue
            # Test 2 : point dans le polygone rempli (obstacle épais)
            if len(pts) >= 3 and self._obs_contains(pts, wx, wy):
                removed.append(obs)
        if removed:
            for obs in removed:
                self._obstacles.remove(obs)
            self._obs_pil = None
            self._composite()

    @staticmethod
    def _obs_near_segments(pts: list, x: float, y: float, tol: float) -> bool:
        """Retourne True si le point (x,y) est à moins de tol de l'un des
        segments de la polyligne pts. Idéal pour les traits fins main levée."""
        n = len(pts)
        if n < 2:
            return False
        for i in range(n - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            dx, dy = x2 - x1, y2 - y1
            seg_len_sq = dx * dx + dy * dy
            if seg_len_sq < 1e-9:
                dist_sq = (x - x1) ** 2 + (y - y1) ** 2
            else:
                t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / seg_len_sq))
                px, py = x1 + t * dx, y1 + t * dy
                dist_sq = (x - px) ** 2 + (y - py) ** 2
            if dist_sq <= tol * tol:
                return True
        return False

    def _draw_erase_cursor(self, cx: float, cy: float):
        """Dessine un cercle de prévisualisation du rayon de l'efface."""
        self.canvas.delete("erase_preview")
        r = max(self._brush_var.get(), 1) * self._cp * 0.5
        self.canvas.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            outline="#ff6b6b", width=1, dash=(4, 3),
            tags="erase_preview")

    @staticmethod
    def _obs_contains(pts: list, x: float, y: float) -> bool:
        """Ray-casting pour savoir si (x,y) est dans le polygone pts."""
        n = len(pts)
        if n < 3:
            return False
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = pts[i]
            xj, yj = pts[j]
            if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-9) + xi):
                inside = not inside
            j = i
        return inside

    # ── Annulation ────────────────────────────────────────────────────────────

    def _obs_cancel(self):
        """Annule le polygone/tracé en cours sans rien enregistrer."""
        self.canvas.delete("obs_preview")
        self.canvas.delete("obs_preview_cursor")
        self._obs_poly_pts = []
        self._obs_poly_ids = []
        self._obs_free_pts = []
        self._obs_free_id  = 0

    # ── Rendu PIL des obstacles ────────────────────────────────────────────────

    def _build_obstacle_pil(self, W: int, H: int) -> "Image.Image":
        """
        Construit le calque PIL RGBA des obstacles à la résolution (W, H).
        Retourne une image transparente si aucun obstacle.
        Ce calque est composité ENTRE bg et fog → visible par les joueurs.
        """
        from PIL import ImageDraw as _ID
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if not self._obstacles:
            return img
        draw = _ID.Draw(img)
        tx0, ty0 = getattr(self, "_tile_rect", (0, 0, 0, 0))[:2]
        for obs in self._obstacles:
            pts = obs["pts"]
            if len(pts) < 2:
                continue
            color_hex = obs.get("color", "#cc4400")
            # Parse hex → RGBA avec opacité 200/255
            h = color_hex.lstrip("#")
            try:
                r, g, b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
            except Exception:
                r, g, b = 180, 60, 0
            fill_rgba    = (r, g, b, 200)
            outline_rgba = (min(255, r+60), min(255, g+60), min(255, b+60), 255)
            # Convertit les coords monde → pixels et applique l'offset de la vue (zoom/scroll)
            scaled = [(px * self.zoom - tx0, py * self.zoom - ty0) for px, py in pts]
            if len(scaled) >= 3:
                draw.polygon(scaled, fill=fill_rgba, outline=outline_rgba)
            else:
                x0, y0 = scaled[0]
                x1, y1 = scaled[-1]
                draw.line([x0, y0, x1, y1], fill=outline_rgba, width=3)
            # Label
            label = obs.get("label", "")
            if label:
                cx = sum(p[0] for p in scaled) / len(scaled)
                cy = sum(p[1] for p in scaled) / len(scaled)
                draw.text((cx, cy), label, fill=(255,255,255,230))
        return img

    # ─── Actions sur obstacles ────────────────────────────────────────────────

    def _edit_obstacle_label(self, obs):
        new_label = simpledialog.askstring(
            "Label de l'obstacle", "Nouveau label (vide = effacer) :",
            initialvalue=obs.get("label", ""), parent=self.win)
        if new_label is None:
            return
        obs["label"] = new_label.strip()
        self._obs_pil = None
        self._composite()
        self._save_state()

    def _pick_obstacle_color_for(self, obs):
        from tkinter import colorchooser
        color = colorchooser.askcolor(
            color=obs.get("color", "#cc4400"),
            title="Couleur de l'obstacle", parent=self.win)
        if color and color[1]:
            obs["color"] = color[1]
            self._obs_pil = None
            self._composite()
            self._save_state()

    def _delete_obstacle(self, obs):
        if obs in self._obstacles:
            self._obstacles.remove(obs)
        self._obs_pil = None
        self._composite()
        self._save_state()

    def _clear_all_obstacles(self):
        if not self._obstacles:
            return
        if messagebox.askyesno("Effacer obstacles",
                               f"Supprimer les {len(self._obstacles)} obstacle(s) ?",
                               parent=self.win):
            self._obstacles.clear()
            self._obs_pil = None
            self._composite()
            self._save_state()

    def _clear_all_tokens(self):
        if not self.tokens:
            return
        if messagebox.askyesno("Effacer tokens",
                               f"Supprimer les {len(self.tokens)} token(s) ?",
                               parent=self.win):
            self.canvas.delete("token")
            self.tokens.clear()
            self._selected_tokens.clear()
            self._save_state()

