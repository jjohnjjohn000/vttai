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

class RulerMixin:
    pass
    # ─── Outil Règle ─────────────────────────────────────────────────────────

    def _ruler_start(self, cx: float, cy: float):
        self._ruler_start_pt = (cx, cy)
        self._ruler_ids = []

    def _ruler_update(self, cx: float, cy: float):
        for iid in getattr(self, "_ruler_ids", []):
            self.canvas.delete(iid)
        self._ruler_ids = []
        sp = getattr(self, "_ruler_start_pt", None)
        if sp is None:
            return
        x0, y0 = sp
        cp = self._cp
        # Distance en cases (Chebyshev D&D 5e) et en mètres
        dcol = abs(cx - x0) / cp
        drow = abs(cy - y0) / cp
        dist_cases = max(dcol, drow)
        dist_m     = dist_cases * 1.5
        dist_ft    = dist_cases * 5.0
        label = f"{dist_m:.1f} m  ({dist_ft:.0f} ft  /  {dist_cases:.1f} cases)"

        # Ligne de mesure
        self._ruler_ids.append(self.canvas.create_line(
            x0, y0, cx, cy,
            fill="#fff176", width=2, dash=(6, 3), tags="ruler"))
        # Points de départ et arrivée
        for rx, ry in [(x0, y0), (cx, cy)]:
            self._ruler_ids.append(self.canvas.create_oval(
                rx - 4, ry - 4, rx + 4, ry + 4,
                fill="#fff176", outline="", tags="ruler"))
        # Label de distance (avec halo noir)
        mx, my = (x0 + cx) / 2, (y0 + cy) / 2
        for ddx, ddy in [(-1,-1),(1,-1),(-1,1),(1,1)]:
            self._ruler_ids.append(self.canvas.create_text(
                mx + ddx, my + ddy, text=label,
                fill="#000000", font=("Consolas", 9, "bold"),
                anchor="center", tags="ruler"))
        self._ruler_ids.append(self.canvas.create_text(
            mx, my, text=label,
            fill="#fff176", font=("Consolas", 9, "bold"),
            anchor="center", tags="ruler"))
        self._status_var.set(f"Règle : {label}")

    def _ruler_end(self):
        for iid in getattr(self, "_ruler_ids", []):
            self.canvas.delete(iid)
        self._ruler_ids = []
        self._ruler_start_pt = None
        self._status_var.set("Règle — cliquer-glisser pour mesurer une distance")

    # ─── Outil Pointer MJ ─────────────────────────────────────────────────────

    def _pointer_click(self, cx: float, cy: float):
        """Clic avec l'outil Pointer : dialogue de commentaire puis envoi au chat."""
        col, row = self._canvas_to_cell(cx, cy)
        col_display = col + 1
        row_display = row + 1

        # ── Dialogue de commentaire ───────────────────────────────────────────
        dw = tk.Toplevel(self.win)
        dw.title("Pointer — commentaire MJ")
        dw.geometry("380x200")
        dw.configure(bg="#0d1018")
        dw.resizable(False, False)
        dw.wait_visibility()
        dw.grab_set()

        tk.Label(dw,
                 text=f"📍  Col {col_display}, Lig {row_display}",
                 bg="#0d1018", fg="#ff8a80",
                 font=("Consolas", 10, "bold")).pack(pady=(12, 4))

        tk.Label(dw,
                 text="Commentaire MJ (optionnel) :",
                 bg="#0d1018", fg="#aaaacc",
                 font=("Consolas", 8)).pack()

        txt = tk.Text(dw, bg="#1a1a2e", fg="#eeeeee",
                      font=("Consolas", 9), insertbackground="#ff8a80",
                      relief="flat", height=4, width=40, wrap=tk.WORD)
        txt.pack(padx=14, pady=4, fill=tk.X)
        txt.focus_set()

        result = {}

        def _send(event=None):
            comment = txt.get("1.0", tk.END).strip()
            result["comment"] = comment
            result["go"] = True
            dw.destroy()

        def _cancel(event=None):
            dw.destroy()

        dw.bind("<Control-Return>", _send)
        dw.bind("<Escape>", _cancel)

        btn_row = tk.Frame(dw, bg="#0d1018")
        btn_row.pack(pady=6)
        tk.Button(btn_row, text="📤 Envoyer",
                  bg="#1a1018", fg="#ff8a80",
                  font=("Consolas", 9, "bold"), relief="flat", padx=12, pady=4,
                  command=_send).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_row, text="Annuler",
                  bg="#1a1a2a", fg="#666688",
                  font=("Consolas", 9), relief="flat", padx=8, pady=4,
                  command=_cancel).pack(side=tk.LEFT, padx=6)
        tk.Label(btn_row, text="Ctrl+Entrée pour envoyer",
                 bg="#0d1018", fg="#444466",
                 font=("Consolas", 7)).pack(side=tk.LEFT, padx=6)

        dw.wait_window()

        if not result.get("go"):
            return

        comment = result.get("comment", "")

        # ── Rendu de l'image avec le pointeur ─────────────────────────────────
        try:
            img = self._render_pointer_image(cx, cy)
        except Exception as e:
            print(f"[Pointer] Erreur rendu : {e}")
            img = None

        # ── Sérialiser en PNG bytes ───────────────────────────────────────────
        img_bytes = None
        if img is not None:
            try:
                import io as _io
                buf = _io.BytesIO()
                img.save(buf, "PNG")
                img_bytes = buf.getvalue()
            except Exception as e:
                print(f"[Pointer] Erreur PNG : {e}")

        # ── Envoyer au chat ───────────────────────────────────────────────────
        if self.msg_queue is not None:
            map_name = self._active_map_name or "carte"
            header = f"📍 {map_name}  —  Col {col_display}, Lig {row_display}"
            if comment:
                header += f"\n{comment}"
            self.msg_queue.put({
                "action":    "map_pointer",
                "sender":    "🗺️ MJ",
                "comment":   header,
                "img_bytes": img_bytes,
                "col":       col_display,
                "row":       row_display,
            })
            # ── Diffusion aux agents joueurs (image + contexte) ───────────────
            if img_bytes is not None:
                self.msg_queue.put({
                    "action":    "map_pointer_broadcast",
                    "img_bytes": img_bytes,
                    "comment":   comment,
                    "col":       col_display,
                    "row":       row_display,
                    "map_name":  self._active_map_name or "",
                    "notes_txt": self._notes_description(),
                })

    def _render_pointer_image(self, cx: float, cy: float) -> "Image.Image":
        """
        Rend le viewport courant avec un pointeur visible (épingle rouge + halo)
        centré sur (cx, cy) en coordonnées canvas.
        """
        from PIL import ImageDraw as _ID, ImageFont as _IF

        # ── Rendu du viewport (réutilise _render_player_image avec fog MJ) ──
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

        # Damier
        ri  = (np.arange(VH) + vy0) // cp
        ci  = (np.arange(VW) + vx0) // cp
        chk = (ri[:, None] + ci[None, :]) % 2
        arr = np.where(chk[:, :, None] == 0,
                       np.array(_C_BG_A, dtype=np.uint8),
                       np.array(_C_BG_B, dtype=np.uint8))
        bg = Image.fromarray(arr.astype(np.uint8), "RGBA")

        # Calques image
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
                ix1 = min(vx1, img_cx0 + disp_w)
                iy1 = min(vy1, img_cy0 + disp_h)
                if ix1 <= ix0 or iy1 <= iy0:
                    continue
                dest_w = ix1 - ix0;  dest_h = iy1 - iy0
                frac_x0 = (ix0 - img_cx0) / disp_w
                frac_y0 = (iy0 - img_cy0) / disp_h
                frac_x1 = (ix1 - img_cx0) / disp_w
                frac_y1 = (iy1 - img_cy0) / disp_h
                src_crop = src.crop((
                    max(0, int(frac_x0 * sw)), max(0, int(frac_y0 * sh)),
                    min(sw, max(1, int(frac_x1 * sw))),
                    min(sh, max(1, int(frac_y1 * sh))),
                ))
                src_cw, _ = src_crop.size
                filt = Image.BILINEAR if dest_w > src_cw else Image.LANCZOS
                tile_img = src_crop.resize((dest_w, dest_h), filt)
                ml = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
                ml.paste(tile_img, (ix0 - vx0, iy0 - vy0))
                bg = Image.alpha_composite(bg, ml)
            except Exception:
                pass

        # Grille
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

        # Fog (vue MJ semi-transparent)
        if self._fog_mask is not None:
            mw_fog = self.cols * self.cell_px
            mh_fog = self.rows * self.cell_px
            fx0 = int(vx0 / W_full * mw_fog) if W_full > 0 else 0
            fy0 = int(vy0 / H_full * mh_fog) if H_full > 0 else 0
            fx1 = int(vx1 / W_full * mw_fog) if W_full > 0 else mw_fog
            fy1 = int(vy1 / H_full * mh_fog) if H_full > 0 else mh_fog
            fog_crop   = self._fog_mask.crop((
                max(0, fx0), max(0, fy0),
                min(mw_fog, max(fx0 + 1, fx1)),
                min(mh_fog, max(fy0 + 1, fy1))))
            fog_scaled = fog_crop.resize((VW, VH), Image.NEAREST)
            fog_arr    = np.array(fog_scaled, dtype=np.uint8)
            fog_rgba   = np.zeros((VH, VW, 4), dtype=np.uint8)
            covered    = fog_arr > 0
            fog_rgba[covered] = _C_FOG_DM   # vue MJ semi-transparent
            fog_layer  = Image.fromarray(fog_rgba, "RGBA")
            bg = Image.alpha_composite(bg, fog_layer)

        # Tokens
        for tok in self.tokens:
            tc, tr = int(round(tok["col"])), int(round(tok["row"]))
            tcx = (tc + 0.5) * cp - vx0
            tcy = (tr + 0.5) * cp - vy0
            if -cp < tcx < VW + cp and -cp < tcy < VH + cp:
                style = TOKEN_STYLES.get(tok["type"], TOKEN_STYLES["hero"])
                name  = tok.get("name", "")
                fill_rgb = (HERO_COLORS.get(name, style["fill"])
                            if tok["type"] == "hero" else style["fill"])
                self._draw_token_pil(bg, tcx, tcy, cp * 0.40,
                                     fill_rgb, style["outline"],
                                     style.get("shape", "circle"), name[:3] or "?")

        # Notes flottantes (post-its) — même rendu que _render_player_image
        if self._notes:
            bg = self._composite_notes_pil_viewport(bg, VW, VH, vx0, vy0)

        # ── Pointeur ──────────────────────────────────────────────────────────
        px = int(cx) - vx0
        py = int(cy) - vy0
        overlay = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
        draw    = _ID.Draw(overlay)

        # Halo de pulsation (cercles concentriques semi-transparents)
        for radius, alpha in [(48, 40), (36, 70), (24, 110)]:
            draw.ellipse([px - radius, py - radius,
                          px + radius, py + radius],
                         fill=(255, 80, 80, alpha), outline=None)

        # Épingle : cercle blanc + rouge avec outline noir
        pin_r = 14
        for ddx, ddy in [(-1,-1),(1,-1),(-1,1),(1,1)]:
            draw.ellipse([px - pin_r + ddx, py - pin_r + ddy,
                          px + pin_r + ddx, py + pin_r + ddy],
                         fill=(0, 0, 0, 200))
        draw.ellipse([px - pin_r, py - pin_r,
                      px + pin_r, py + pin_r],
                     fill=(255, 60, 60, 255), outline=(255, 255, 255, 255))
        draw.ellipse([px - 5, py - 5, px + 5, py + 5],
                     fill=(255, 255, 255, 220))

        # Tige de l'épingle
        stem_len = 28
        for ddx in [-1, 0, 1]:
            draw.line([(px + ddx, py + pin_r - 2),
                       (px + ddx, py + pin_r + stem_len)],
                      fill=(0, 0, 0, 180), width=1)
        draw.line([(px, py + pin_r - 2),
                   (px, py + pin_r + stem_len)],
                  fill=(255, 60, 60, 255), width=2)

        # Lignes de visée (crosshair léger)
        line_len = 22
        line_alpha = 160
        for dx, dy in [(-line_len, 0), (line_len, 0), (0, -line_len), (0, line_len)]:
            draw.line([(px, py), (px + dx, py + dy)],
                      fill=(255, 200, 200, line_alpha), width=1)

        # Coordonnées de la case
        col_lbl = int(cx // cp) + 1
        row_lbl = int(cy // cp) + 1
        coord_txt = f"Col {col_lbl}, Lig {row_lbl}"
        try:
            font = _IF.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 13)
        except Exception:
            font = _IF.load_default()
        label_x = px + pin_r + 4
        label_y = py - pin_r
        for ddx2, ddy2 in [(-1,-1),(1,-1),(-1,1),(1,1)]:
            draw.text((label_x + ddx2, label_y + ddy2), coord_txt,
                      fill=(0, 0, 0, 200), font=font)
        draw.text((label_x, label_y), coord_txt,
                  fill=(255, 230, 230, 255), font=font)

        bg = Image.alpha_composite(bg, overlay)

        # ── Bande de titre en bas de l'image ──────────────────────────────────
        BAR_H = 28
        final = Image.new("RGBA", (VW, VH + BAR_H), (0, 0, 0, 255))
        final.paste(bg, (0, 0))
        bar_draw = _ID.Draw(final)
        bar_draw.rectangle([0, VH, VW, VH + BAR_H], fill=(12, 12, 30, 255))
        map_name = self._active_map_name or "carte"
        bar_txt  = f"📍 {map_name}  —  Col {col_lbl}, Lig {row_lbl}"
        try:
            bar_font = _IF.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 11)
        except Exception:
            bar_font = _IF.load_default()
        bar_draw.text((8, VH + 7), bar_txt,
                      fill=(180, 140, 255, 255), font=bar_font)

        return final.convert("RGBA")

    @staticmethod
    def _draw_token_pil(img, cx, cy, rad, fill_rgb, outline_rgb, shape, label):
        """Dessine un token sur une image PIL (pour l'export pointeur)."""
        from PIL import ImageDraw as _ID2
        draw = _ID2.Draw(img)
        fill    = tuple(fill_rgb) + (220,)
        outline = tuple(outline_rgb) + (255,)
        if shape == "circle":
            draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad],
                         fill=fill, outline=outline)
        elif shape == "diamond":
            pts = [(cx, cy - rad), (cx + rad, cy),
                   (cx, cy + rad), (cx - rad, cy)]
            draw.polygon(pts, fill=fill, outline=outline)
        else:
            pts = [(cx, cy - rad),
                   (cx + rad * 0.88, cy + rad * 0.75),
                   (cx - rad * 0.88, cy + rad * 0.75)]
            draw.polygon(pts, fill=fill, outline=outline)

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


# ─── Fenêtre Vue Joueurs ──────────────────────────────────────────────────────
