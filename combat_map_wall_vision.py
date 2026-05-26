import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox
import os
import tempfile
import base64
import math
try:
    import numpy as np
    from PIL import Image, ImageTk, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from combat_map_constants import *
from combat_map_constants import _sep, _darken_rgb, _darken_rgb_tuple, _compress_ranges, _C_BG_A, _C_BG_B, _C_FOG_CLEAR, _C_FOG_DM, _C_FOG_PLAYER, _C_GRID, _rgb_to_hex

# ─── Couleurs murs ────────────────────────────────────────────────────────────
_C_WALL_DM      = (0, 220, 255, 200)   # cyan translucide (vue MJ)
_C_WALL_OUTLINE = (0, 180, 220, 255)   # contour un peu plus sombre
_WALL_WIDTH     = 3                    # épaisseur du trait en pixels logiques

_DEFAULT_DARKVISION_FT = 40  # Mode souterrain — valeur par défaut

# ─── Nombre de rayons pour le raycasting ──────────────────────────────────────
_NUM_RAYS = 720   # un rayon toutes les 0.5°


class WallVisionMixin:
    pass

    # ══════════════════════════════════════════════════════════════════════════
    #  OUTIL MUR — dessin / suppression de segments
    # ══════════════════════════════════════════════════════════════════════════

    def _wall_click(self, cx: float, cy: float):
        """Ajoute un point de mur. Les clics suivants étendent la ligne en continu.
        Clic droit pour valider (crée les segments)."""
        scale = self._cp / self.cell_px
        wx, wy = cx / scale, cy / scale   # coords monde

        self._wall_pts.append((wx, wy))
        r = 4
        iid = self.canvas.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            outline="#00deff", fill="#0a1a2a", width=2, tags=("wall_preview", "wall_geom"))
        self._wall_ids.append(iid)

        if len(self._wall_pts) > 1:
            p1 = self._wall_pts[-2]
            p2 = self._wall_pts[-1]
            x1, y1 = p1[0] * scale, p1[1] * scale
            x2, y2 = p2[0] * scale, p2[1] * scale
            iid_line = self.canvas.create_line(
                x1, y1, x2, y2,
                fill="#00deff", width=2,
                tags=("wall_preview", "wall_geom")
            )
            self._wall_ids.append(iid_line)

    def _wall_preview_move(self, cx: float, cy: float):
        """Prévisualisation du segment en cours pendant le déplacement de la souris."""
        if not self._wall_pts:
            return
        self.canvas.delete("wall_preview_line")
        scale = self._cp / self.cell_px
        sx = self._wall_pts[-1][0] * scale
        sy = self._wall_pts[-1][1] * scale
        self.canvas.create_line(
            sx, sy, cx, cy,
            fill="#00deff", width=2, dash=(6, 3),
            tags=("wall_preview", "wall_preview_line", "wall_geom"))

    def _wall_apply(self):
        """Valide la ligne continue, crée les segments de mur, et termine l'action."""
        if len(self._wall_pts) > 1:
            count = 0
            for i in range(len(self._wall_pts) - 1):
                p1 = self._wall_pts[i]
                p2 = self._wall_pts[i+1]
                # Ignorer les segments trop courts
                dx = p2[0] - p1[0]
                dy = p2[1] - p1[1]
                if dx * dx + dy * dy >= 1.0:
                    self._walls.append({"p1": p1, "p2": p2})
                    count += 1
            if count > 0:
                for d in getattr(self, "_doors", []):
                    if hasattr(self, "_cut_walls_with_door"):
                        self._cut_walls_with_door(d)
                self._wall_pil = None
                self._composite()
                self._save_state()
        self._wall_cancel()

    def _wall_cancel(self):
        """Annule complètement le mur en cours sans enregistrer."""
        for iid in self._wall_ids:
            self.canvas.delete(iid)
        self.canvas.delete("wall_preview_line")
        self.canvas.delete("wall_geom")
        self._wall_pts.clear()
        self._wall_ids.clear()

    def _restore_wall_preview(self):
        """Redessine la ligne en cours de construction (après un zoom par exemple)."""
        for iid in self._wall_ids:
            self.canvas.delete(iid)
        self._wall_ids.clear()
        self.canvas.delete("wall_preview_line")
        self.canvas.delete("wall_geom")
        
        if not self._wall_pts:
            return
            
        scale = self._cp / self.cell_px
        for i, pt in enumerate(self._wall_pts):
            cx, cy = pt[0] * scale, pt[1] * scale
            r = 4
            iid = self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                outline="#00deff", fill="#0a1a2a", width=2, tags=("wall_preview", "wall_geom")
            )
            self._wall_ids.append(iid)
            if i > 0:
                prev_x, prev_y = self._wall_pts[i-1][0] * scale, self._wall_pts[i-1][1] * scale
                iid_line = self.canvas.create_line(
                    prev_x, prev_y, cx, cy,
                    fill="#00deff", width=2, tags=("wall_preview", "wall_geom")
                )
                self._wall_ids.append(iid_line)

    def _wall_erase_at(self, cx: float, cy: float):
        """Efface la section de mur qui tombe dans le cercle du pinceau (2 ft = 0.4 case)."""
        import math
        scale = self._cp / self.cell_px
        wx, wy = cx / scale, cy / scale
        R = self.cell_px * 0.4   # 2 ft brush radius in world coords

        changed = False
        new_walls = []
        for wall in self._walls:
            A = wall["p1"]
            B = wall["p2"]
            survivors = self._clip_segment_outside_circle(A, B, (wx, wy), R)
            if survivors is None:
                # Segment entirely inside circle → removed
                changed = True
            elif len(survivors) == 1:
                p1, p2 = survivors[0]
                seg_len = math.hypot(p2[0]-p1[0], p2[1]-p1[1])
                if seg_len < 1.0:
                    changed = True
                else:
                    if p1 != A or p2 != B:
                        changed = True
                    new_walls.append({"p1": p1, "p2": p2})
            else:
                changed = True
                for p1, p2 in survivors:
                    seg_len = math.hypot(p2[0]-p1[0], p2[1]-p1[1])
                    if seg_len >= 1.0:
                        new_walls.append({"p1": p1, "p2": p2})

        if changed:
            self._walls = new_walls
            self._wall_pil = None
            self._composite()

    @staticmethod
    def _clip_segment_outside_circle(A, B, C, R):
        """Clip segment A→B, returning the portion(s) OUTSIDE circle (C, R).
        Returns None if entirely inside, or list of (p1,p2) tuples."""
        import math
        dx, dy = B[0] - A[0], B[1] - A[1]
        seg_len_sq = dx*dx + dy*dy
        if seg_len_sq < 1e-9:
            if math.hypot(A[0]-C[0], A[1]-C[1]) <= R:
                return None
            return [(A, B)]

        fx, fy = A[0] - C[0], A[1] - C[1]
        a = seg_len_sq
        b = 2 * (fx*dx + fy*dy)
        c = fx*fx + fy*fy - R*R
        disc = b*b - 4*a*c

        if disc < 0:
            return [(A, B)]

        sq = math.sqrt(disc)
        t1 = (-b - sq) / (2*a)
        t2 = (-b + sq) / (2*a)

        t1 = max(0.0, min(1.0, t1))
        t2 = max(0.0, min(1.0, t2))

        if t1 >= t2 - 1e-9:
            # Segment chord doesn't cross the interior (or only kisses boundary)
            # Just test the true midpoint of the entire A->B segment
            mx = A[0] + 0.5 * dx
            my = A[1] + 0.5 * dy
            if math.hypot(mx - C[0], my - C[1]) < R - 1e-5:
                return None
            return [(A, B)]

        parts = []
        if t1 > 0.01:
            p = (A[0] + t1*dx, A[1] + t1*dy)
            parts.append((A, p))
        if t2 < 0.99:
            p = (A[0] + t2*dx, A[1] + t2*dy)
            parts.append((p, B))

        if not parts:
            return None
        return parts

    @staticmethod
    def _wall_near_segment(pts: list, x: float, y: float, tol: float) -> bool:
        """Vérifie si (x,y) est à moins de tol d'un segment."""
        if len(pts) < 2:
            return False
        x1, y1 = pts[0]
        x2, y2 = pts[1]
        dx, dy = x2 - x1, y2 - y1
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq < 1e-9:
            return (x - x1) ** 2 + (y - y1) ** 2 <= tol * tol
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / seg_len_sq))
        px, py = x1 + t * dx, y1 + t * dy
        return (x - px) ** 2 + (y - py) ** 2 <= tol * tol

    @staticmethod
    def _wall_seg_dist(pts: list, x: float, y: float) -> float:
        """Distance du point (x,y) au segment."""
        x1, y1 = pts[0]
        x2, y2 = pts[1]
        dx, dy = x2 - x1, y2 - y1
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq < 1e-9:
            return math.hypot(x - x1, y - y1)
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / seg_len_sq))
        px, py = x1 + t * dx, y1 + t * dy
        return math.hypot(x - px, y - py)

    def _clear_all_walls(self):
        """Supprime tous les murs après confirmation."""
        if not self._walls:
            return
        if messagebox.askyesno("Effacer murs",
                               f"Supprimer les {len(self._walls)} mur(s) ?",
                               parent=self.win):
            self._walls.clear()
            self._wall_pil = None
            self._composite()
            self._save_state()

    # ══════════════════════════════════════════════════════════════════════════
    #  RENDU PIL DES MURS (calque DM-only)
    # ══════════════════════════════════════════════════════════════════════════

    def _build_wall_pil(self, W: int, H: int) -> "Image.Image":
        """Construit le calque RGBA des murs à la résolution (W, H).
        Visible uniquement en vue MJ."""
        from PIL import ImageDraw as _ID
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if not self._walls:
            return img
        draw = _ID.Draw(img)
        tx0, ty0 = getattr(self, "_tile_rect", (0, 0, 0, 0))[:2]
        scale = self._cp / self.cell_px
        width = max(2, int(_WALL_WIDTH * self.zoom))
        for wall in self._walls:
            x1 = wall["p1"][0] * scale - tx0
            y1 = wall["p1"][1] * scale - ty0
            x2 = wall["p2"][0] * scale - tx0
            y2 = wall["p2"][1] * scale - ty0
            draw.line([(x1, y1), (x2, y2)], fill=_C_WALL_DM, width=width)
            # Petits cercles aux extrémités
            r = max(2, width)
            draw.ellipse([x1 - r, y1 - r, x1 + r, y1 + r], fill=_C_WALL_OUTLINE)
            draw.ellipse([x2 - r, y2 - r, x2 + r, y2 + r], fill=_C_WALL_OUTLINE)
        return img

    # ══════════════════════════════════════════════════════════════════════════
    #  VISION REVEAL — raycasting + fog clear
    # ══════════════════════════════════════════════════════════════════════════

    def _get_token_darkvision_ft(self, tok: dict) -> int:
        """Résout la distance de darkvision pour un token (en pieds D&D).
        Ordre de priorité :
          1. Valeur stockée sur le token (darkvision_ft)
          2. Données bestiary (monstre) ou race_data (héros)
          3. Défaut : 40 ft (mode souterrain)
        """
        # 1. Valeur explicite sur le token
        stored = tok.get("darkvision_ft", 0)
        if stored and stored > 0:
            return int(stored)

        # 2. Résolution automatique
        name = tok.get("name", "")
        ttype = tok.get("type", "monster")

        if ttype == "hero":
            try:
                from state_manager import load_state as _ls
                st = _ls()
                cdata = st.get("characters", {}).get(name, {})
                race = cdata.get("race", "")
                subrace = cdata.get("subrace", "")
                if race:
                    from race_data import get_race_darkvision
                    dv = get_race_darkvision(race, subrace or None)
                    if dv and dv > 0:
                        tok["darkvision_ft"] = dv  # cache
                        return dv
            except Exception:
                pass
        else:
            # Monstres : chercher dans le bestiary
            bname = tok.get("bestiary_name", "").strip()
            if not bname:
                bname = tok.get("source_name", "").strip()
            if bname:
                try:
                    dv = self._parse_bestiary_darkvision(bname)
                    if dv and dv > 0:
                        tok["darkvision_ft"] = dv
                        return dv
                except Exception:
                    pass

        return _DEFAULT_DARKVISION_FT

    @staticmethod
    def _parse_bestiary_darkvision(bname: str) -> int:
        """Cherche la valeur de darkvision dans les fichiers bestiary JSON."""
        import json, re, glob
        for path in glob.glob("bestiary/bestiary-*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                monsters = data if isinstance(data, list) else data.get("monster", [])
                for mon in monsters:
                    if mon.get("name", "").lower() == bname.lower():
                        senses = mon.get("senses", [])
                        if isinstance(senses, list):
                            for s in senses:
                                m = re.search(r"darkvision\s+(\d+)\s*ft", s, re.IGNORECASE)
                                if m:
                                    return int(m.group(1))
                        elif isinstance(senses, str):
                            m = re.search(r"darkvision\s+(\d+)\s*ft", senses, re.IGNORECASE)
                            if m:
                                return int(m.group(1))
            except Exception:
                continue
        return 0

    def _reveal_token_vision(self, tok: dict):
        """Révèle le fog de guerre dans le champ de vision du token,
        en tenant compte des murs (raycasting 360°).

        La distance de vision = darkvision du token (ou 40ft par défaut mode souterrain).
        """
        from PIL import ImageDraw as _ID

        dv_ft = self._get_token_darkvision_ft(tok)
        if dv_ft <= 0:
            dv_ft = _DEFAULT_DARKVISION_FT

        # Taille du token en cases
        size = float(tok.get("size", 1))
        tx = tok["col"] * self.cell_px
        ty = tok["row"] * self.cell_px
        tsize = size * self.cell_px

        # Les 4 coins du token (légèrement en retrait pour éviter d'être bloqué exactement sur une ligne)
        corners = [
            (tx + 1, ty + 1),                 # Haut-Gauche
            (tx + tsize - 1, ty + 1),         # Haut-Droite
            (tx + 1, ty + tsize - 1),         # Bas-Gauche
            (tx + tsize - 1, ty + tsize - 1)  # Bas-Droite
        ]

        # Rayon de vision en pixels monde (1 case = cell_px = 5 ft)
        radius_world = (dv_ft / 5.0) * self.cell_px

        # Construire les segments de mur en coordonnées monde
        wall_segs = []
        for w in self._walls:
            wall_segs.append((w["p1"][0], w["p1"][1], w["p2"][0], w["p2"][1]))

        # Injecter les portes comme bloqueurs de vision
        for d in getattr(self, "_doors", []):
            import math
            d_px = d.get("px", (d.get("col", 0) + 0.5) * self.cell_px)
            d_py = d.get("py", (d.get("row", 0) + 0.5) * self.cell_px)
            angle_rad = math.radians(d.get("rotation", 0))
            w_d = self.cell_px * d.get("length_scale", 1.0)
            h_d = self.cell_px * 0.25
            d_corners = [(-w_d/2, -h_d/2), (w_d/2, -h_d/2), (w_d/2, h_d/2), (-w_d/2, h_d/2)]
            
            is_open = d.get("open", False)
            open_a = d.get("open_angle", 90 if is_open else 0)

            if open_a > 0:
                m = 1 if not d.get("mirrored", False) else -1
                open_rad = math.radians(m * open_a)
                hx, hy = m * (-w_d / 2), 0
                cos_o = math.cos(open_rad)
                sin_o = math.sin(open_rad)
                open_corners = []
                for dx, dy in d_corners:
                    nx = hx + (dx - hx) * cos_o - (dy - hy) * sin_o
                    ny = hy + (dx - hx) * sin_o + (dy - hy) * cos_o
                    open_corners.append((nx, ny))
                d_corners = open_corners

            cos_a = math.cos(angle_rad)
            sin_a = math.sin(angle_rad)
            rot = []
            for dx, dy in d_corners:
                rx = dx * cos_a - dy * sin_a
                ry = dx * sin_a + dy * cos_a
                rot.append((d_px + rx, d_py + ry))
                
            for i in range(4):
                wall_segs.append((rot[i][0], rot[i][1], rot[(i+1)%4][0], rot[(i+1)%4][1]))

        # Appliquer sur le fog mask
        mw = self.cols * self.cell_px
        mh = self.rows * self.cell_px
        if self._fog_mask is None:
            self._fog_mask = Image.new("L", (mw, mh), 255)

        draw = _ID.Draw(self._fog_mask)

        # Raycasting pour chaque coin
        for ox, oy in corners:
            polygon_pts = []
            for i in range(_NUM_RAYS):
                angle = 2.0 * math.pi * i / _NUM_RAYS
                dx = math.cos(angle)
                dy = math.sin(angle)
                # Endpoint du rayon (portée max)
                ex = ox + dx * radius_world
                ey = oy + dy * radius_world
                best_t = 1.0   # paramètre le plus proche [0, 1]

                # Tester l'intersection avec chaque mur
                for (wx1, wy1, wx2, wy2) in wall_segs:
                    t = self._ray_seg_intersect(ox, oy, ex, ey, wx1, wy1, wx2, wy2)
                    if t is not None and t < best_t:
                        best_t = t

                hit_x = ox + (ex - ox) * best_t
                hit_y = oy + (ey - oy) * best_t
                polygon_pts.append((hit_x, hit_y))

            if len(polygon_pts) >= 3:
                draw.polygon(polygon_pts, fill=0)   # 0 = révélé

        # Rafraîchir
        self._fog_pil = None
        self._rebuild_fog()
        self._composite()

    @staticmethod
    def _ray_seg_intersect(ox, oy, ex, ey, sx1, sy1, sx2, sy2):
        """Intersection rayon (ox,oy)→(ex,ey) avec segment (sx1,sy1)→(sx2,sy2).
        Retourne t ∈ [0,1] paramètre sur le rayon, ou None si pas d'intersection.
        Le segment est testé sur u ∈ [0,1]."""
        rdx = ex - ox
        rdy = ey - oy
        sdx = sx2 - sx1
        sdy = sy2 - sy1
        denom = rdx * sdy - rdy * sdx
        if abs(denom) < 1e-10:
            return None
        t = ((sx1 - ox) * sdy - (sy1 - oy) * sdx) / denom
        u = ((sx1 - ox) * rdy - (sy1 - oy) * rdx) / denom
        if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
            return t
        return None

    def _reveal_vision_menu_action(self, tok: dict):
        """Action du menu contextuel : révéler la vision d'un token avec undo."""
        self._fog_push_undo()
        self._reveal_token_vision(tok)
        self._save_state()

        dv_ft = self._get_token_darkvision_ft(tok)
        name = tok.get("name", "?")
        if self.msg_queue:
            self.msg_queue.put({
                "sender": "🗺️ Vision",
                "text": f"Vision révélée pour {name} — darkvision {dv_ft} ft.",
                "color": "#00deff",
            })

    def _reveal_vision_for_selection(self, sel_toks: list):
        """Révèle la vision pour chaque token sélectionné."""
        self._fog_push_undo()
        for tok in sel_toks:
            self._reveal_token_vision(tok)
        self._save_state()

        names = [t.get("name", "?") for t in sel_toks]
        if self.msg_queue:
            self.msg_queue.put({
                "sender": "🗺️ Vision",
                "text": f"Vision révélée pour {', '.join(names)}.",
                "color": "#00deff",
            })

    def _edit_token_darkvision(self, tok: dict):
        """Dialogue pour modifier la darkvision d'un token."""
        current = tok.get("darkvision_ft", 0)
        if current <= 0:
            current = self._get_token_darkvision_ft(tok)
        val = simpledialog.askinteger(
            "Darkvision",
            f"Distance de darkvision en pieds (actuel : {current} ft) :",
            initialvalue=current, minvalue=0, maxvalue=300,
            parent=self.win)
        if val is not None:
            tok["darkvision_ft"] = val
            self._save_state()
