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

class FogManagerMixin:
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

    # ─── Outil pinceau fog ────────────────────────────────────────────────────

    def _fog_push_undo(self):
        """Sauvegarde l'état courant du fog mask pour Ctrl+Z."""
        if self._fog_mask is None:
            return
        self._fog_undo_stack.append(self._fog_mask.copy())
        if len(self._fog_undo_stack) > 15:
            self._fog_undo_stack.pop(0)

    def _undo_fog(self):
        """Ctrl+Z — restaure le dernier état du fog mask."""
        if not self._fog_undo_stack:
            self._status_var.set("Aucun état à annuler.")
            return
        self._fog_mask = self._fog_undo_stack.pop()
        self._fog_pil = None
        self._rebuild_fog()
        self._composite()
        self._save_state()
        self._status_var.set(f"Annulé. ({len(self._fog_undo_stack)} état(s) restants)")

    def _brush_fog(self, cx: float, cy: float):
        """Applique le pinceau de fog (révéler ou cacher) à la position canvas (cx, cy)."""
        from PIL import ImageDraw as _ID
        mw = self.cols * self.cell_px
        mh = self.rows * self.cell_px
        if self._fog_mask is None:
            self._fog_mask = Image.new("L", (mw, mh), 255)

        cp  = self._cp
        inv = self.cell_px / cp   # facteur canvas → fog mask
        # Centre du pinceau en coordonnées fog mask
        mx = cx * inv
        my = cy * inv
        r  = max(1, self._brush_var.get()) * self.cell_px  # rayon en px fog

        fill = 0 if self.tool == "brush_reveal" else 255
        draw = _ID.Draw(self._fog_mask)
        draw.ellipse([mx - r, my - r, mx + r, my + r], fill=fill)

        self._fog_pil = None
        self._schedule_tile_refresh(delay=16)

    def _show_selection_context_menu(self, event, hit_tok):
        """Menu contextuel sur right-click d'un token faisant partie de la sélection."""
        sel_toks = [t for t in self.tokens if id(t) in self._selected_tokens]
        n = len(sel_toks)
        label = f"{n} token{'s' if n > 1 else ''} sélectionné{'s' if n > 1 else ''}"

        menu = tk.Menu(self.canvas, tearoff=0,
                       bg="#1a1a2e", fg="#dde0e8",
                       activebackground="#2a2a4e", activeforeground="#ffffff",
                       font=("Consolas", 9))
        menu.add_command(label=f"── {label} ──", state="disabled")
        menu.add_separator()
        menu.add_command(
            label="⚔  Ajouter à l'initiative",
            command=lambda: self._add_selection_to_initiative(sel_toks))
        menu.add_command(
            label="▦  Regrouper (grille 2 de large)",
            command=lambda: self._group_selection(sel_toks))
        menu.add_separator()
        menu.add_command(
            label="✕  Supprimer la sélection",
            command=lambda: self._delete_selection(sel_toks))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _add_selection_to_initiative(self, sel_toks):
        """
        Ajoute les tokens sélectionnés au CombatTracker ouvert.
        - Héros déjà présents → ignorés (pas de doublon).
        - Monstres → ajoutés comme PNJ avec init aléatoire.
        - Héros absents → ajoutés comme PJ.
        """
        # Récupérer le tracker depuis l'app parente si possible
        tracker = None
        try:
            import gc
            from combat_tracker import CombatTracker
            for obj in gc.get_objects():
                if isinstance(obj, CombatTracker):
                    try:
                        if obj.win.winfo_exists():
                            tracker = obj
                            break
                    except Exception:
                        pass
        except Exception:
            pass

        if tracker is None:
            if self.msg_queue:
                self.msg_queue.put({
                    "sender": "⚔️ Carte",
                    "text": "Ouvrez d'abord le Combat Tracker (bouton ⚔️ Combat).",
                    "color": "#e67e22",
                })
            return

        from combat_tracker import Combatant, PC_COLORS, PC_DEX_BONUS, HERO_NAMES
        import random

        already_in = {c.name for c in tracker.combatants}
        added = []
        skipped = []

        for tok in sel_toks:
            name = tok.get("name", "")
            tok_type = tok.get("type", "monster")

            if tok_type == "hero":
                # Héros déjà présent → ignorer
                if name in already_in:
                    skipped.append(name)
                    continue
                # Charger les stats depuis state_manager si possible
                hp, max_hp = 30, 30
                try:
                    from state_manager import load_state as _ls
                    st = _ls()
                    cdata = st.get("characters", {}).get(name, {})
                    hp     = cdata.get("hp",     hp)
                    max_hp = cdata.get("max_hp", max_hp)
                except Exception:
                    pass
                dex = PC_DEX_BONUS.get(name, 2)
                init_val = random.randint(1, 20) + dex
                c = Combatant(
                    name=name, is_pc=True,
                    max_hp=max_hp, current_hp=hp,
                    ac=16, initiative=init_val,
                    dex_bonus=dex,
                    color=PC_COLORS.get(name, "#a0c0ff"),
                )
            else:
                # PNJ/monstre — toujours ajouter (peut y avoir plusieurs copies)
                display_name = name or tok_type.capitalize()
                init_val = random.randint(1, 20) + random.randint(-1, 3)
                c = Combatant(
                    name=display_name, is_pc=False,
                    max_hp=20, current_hp=20,
                    ac=13, initiative=init_val,
                    dex_bonus=1,
                    color="#e04040",
                )

            tracker.combatants.append(c)
            already_in.add(c.name)
            added.append(f"{c.name} (init {init_val})")

        tracker._sort_and_refresh()
        tracker._save_combat_state()

        parts = []
        if added:
            parts.append(f"Ajoutés : {', '.join(added)}")
        if skipped:
            parts.append(f"Déjà présents (ignorés) : {', '.join(skipped)}")
        if self.msg_queue and parts:
            self.msg_queue.put({
                "sender": "⚔️ Combat",
                "text": " | ".join(parts),
                "color": "#c8a820",
            })

    def _group_selection(self, sel_toks):
        """
        Dispose les tokens sélectionnés en grille de 2 cases de large,
        à partir de la position du token le plus en haut-gauche.
        Les tokens restent sélectionnés après le regroupement.
        """
        if not sel_toks:
            return

        # Origine = case la plus en haut-gauche de la sélection
        origin_col = min(int(round(t["col"])) for t in sel_toks)
        origin_row = min(int(round(t["row"])) for t in sel_toks)

        for i, tok in enumerate(sel_toks):
            col_offset = i % 2          # 0 ou 1
            row_offset = i // 2         # 0, 1, 2…
            tok["col"] = float(max(0, min(self.cols - 1,
                                          origin_col + col_offset)))
            tok["row"] = float(max(0, min(self.rows - 1,
                                          origin_row + row_offset)))
            self._redraw_one_token(tok)

        self._save_state()
        if self.msg_queue:
            self.msg_queue.put({
                "sender": "🗺️ Carte",
                "text": f"{len(sel_toks)} token(s) regroupés en grille 2×N à partir de Col {origin_col+1}, Lig {origin_row+1}.",
                "color": "#64b5f6",
            })

    def _delete_selection(self, sel_toks):
        """Supprime tous les tokens de la sélection."""
        names = [t.get("name", "?") for t in sel_toks]
        for tok in sel_toks:
            for iid in tok.get("ids", ()):
                self.canvas.delete(iid)
            self._selected_tokens.discard(id(tok))
            if tok in self.tokens:
                self.tokens.remove(tok)
        self._save_state()
        if hasattr(self, "_notify_tokens_deleted"):
            self._notify_tokens_deleted(names)

    def _mouse_move(self, event):
        cx, cy = self._canvas_xy(event)
        col, row = self._canvas_to_cell(cx, cy)
        if 0 <= col < self.cols and 0 <= row < self.rows:
            self._pos_var.set(f"Col {col+1} / Lig {row+1}")
        else:
            self._pos_var.set("")
        # Prévisualisation polygone fog
        if self.tool in ("reveal", "hide") and self._poly_points:
            self._poly_update_preview(cx, cy)
        # Prévisualisation polygone obstacle
        if self.tool == "obstacle_poly" and self._obs_poly_pts:
            self._obs_poly_update_preview(cx, cy)
        # Prévisualisation curseur efface
        if self.tool == "erase_obs":
            self._draw_erase_cursor(cx, cy)
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

