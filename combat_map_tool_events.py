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

class ToolEventMixin:
    pass
    # ─── Outils ───────────────────────────────────────────────────────────────

    def _escape_to_select(self):
        """Échappe vers l'outil Sélection.
        Si un polygone est en cours (reveal/hide/obstacle), l'annule d'abord.
        Dans tous les cas, active l'outil 'select'.
        """
        self._poly_cancel()
        self._obs_cancel()
        
        # --- NOUVEAU : Annuler un glisser-déposer de token en cours ---
        if getattr(self, "_drag_token", None) is not None:
            self._drag_token = None
            self._drag_origins = {}
            self.canvas.delete("drag_counter")
            self._redraw_all_tokens()  # Remet les tokens à leur place d'origine
            
        self._set_tool("select")

    def _set_tool(self, tool: str):
        prev_tool = self.tool
        self.tool = tool
        cursors  = {"select": "arrow", "reveal": "dotbox", "hide": "dot",
                    "pointer": "crosshair",
                    "brush_reveal": "dotbox", "brush_hide": "dot",
                    "ruler": "crosshair",
                    "add": "plus", "note": "pencil", "resize_map": "fleur",
                    "door": "hand2", "obstacle_poly": "crosshair",
                    "obstacle_free": "pencil",
                    "erase_obs": "dotbox"}
        statuses = {
            "select":        "Sélection — glisser tokens | double-clic : éditer | clic droit : menu contextuel",
            "pointer":       "Pointer — cliquer sur la carte pour ajouter un commentaire MJ + envoyer l'image au chat",
            "reveal":        "Révéler (polygone) — clic gauche : sommet | clic droit : appliquer | Échap : annuler",
            "hide":          "Cacher (polygone)  — clic gauche : sommet | clic droit : appliquer | Échap : annuler",
            "brush_reveal":  "Pinceau révéler — cliquer-glisser pour révéler | Rayon = taille du pinceau | Ctrl+Z : annuler",
            "brush_hide":    "Pinceau cacher  — cliquer-glisser pour masquer | Rayon = taille du pinceau | Ctrl+Z : annuler",
            "ruler":         "Règle — cliquer-glisser pour mesurer une distance (1 case = 1,5 m / 5 ft)",
            "add":           "Token    — cliquer sur une case pour placer un token",
            "note":          "Note     — clic gauche : placer un post-it | glisser : déplacer | double-clic : éditer | clic droit : supprimer",
            "door":          "Porte    — clic gauche : placer/basculer | clic droit : menu (éditer label, supprimer)",
            "obstacle_poly": "Obstacle (polygone) — clic gauche : sommet | clic droit : valider et nommer | Échap : annuler",
            "obstacle_free": "Obstacle (main levée) — cliquer-glisser pour dessiner | relâcher : valider | Échap : annuler | clic droit (select) : menu obstacle",
            "erase_obs":     "Efface — cliquer-glisser pour supprimer les obstacles touchés | le rayon contrôle la taille",
            "resize_map":    "Carte — glisser une poignée pour redimensionner | "
                             "glisser le centre pour déplacer | Shift = ratio fixe",
        }
        self.canvas.config(cursor=cursors.get(tool, "crosshair"))
        self._status_var.set(statuses.get(tool, ""))
        for key, (btn, fg_on, bg_on) in self._tool_btns.items():
            if key == tool:
                btn.config(bg=bg_on, fg=fg_on, relief="sunken")
            else:
                btn.config(bg="#252538", fg="#aaaacc", relief="flat")

        # --- NOUVEAU : Nettoyage de sécurité des widgets temporaires ---
        self.canvas.delete("drag_counter")

        # Annuler polygone en cours si on change d'outil
        if prev_tool in ("reveal", "hide") and tool not in ("reveal", "hide"):
            self._poly_cancel()

        # Annuler obstacle en cours si on change d'outil
        if prev_tool in ("obstacle_poly", "obstacle_free") and tool not in ("obstacle_poly", "obstacle_free"):
            self._obs_cancel()

        # Effacer le curseur d'efface si on quitte l'outil
        if prev_tool == "erase_obs" and tool != "erase_obs":
            self.canvas.delete("erase_preview")

        # Effacer la règle si on change d'outil
        if prev_tool == "ruler" and tool != "ruler":
            self.canvas.delete("ruler")
            self._ruler_start_pt = None
            self._ruler_ids = []
            
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

    # ─── Événements souris ────────────────────────────────────────────────────

    def _mb1_down(self, event):
        cx, cy = self._canvas_xy(event)
        self._last_fog_cell = None
        if self.tool == "pointer":
            self._pointer_click(cx, cy)
            return
        if self.tool == "resize_map":
            self._map_resize_begin(cx, cy, event)
        elif self.tool == "add":
            self._add_token(cx, cy)
        elif self.tool in ("reveal", "hide"):
            self._poly_add_point(cx, cy)
        elif self.tool in ("brush_reveal", "brush_hide"):
            self._fog_push_undo()
            self._brush_fog(cx, cy)
        elif self.tool == "ruler":
            self._ruler_start(cx, cy)
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
        elif self.tool == "obstacle_poly":
            self._obs_poly_add(cx, cy)
        elif self.tool == "obstacle_free":
            self._obs_free_start(cx, cy)
        elif self.tool == "erase_obs":
            self._obs_erase_at(cx, cy)
            self._draw_erase_cursor(cx, cy)
        elif self.tool == "select":
            if self._drag_token is None:
                self._box_select_begin(cx, cy)

    def _mb1_move(self, event):
        cx, cy = self._canvas_xy(event)
        if self._drag_note is not None:
            n = self._drag_note
            n["px"] = (cx - self._drag_note_off[0]) / self.zoom
            n["py"] = (cy - self._drag_note_off[1]) / self.zoom
            self._redraw_one_note(n)
        elif self._drag_token is not None:
            self._tok_drag(event, self._drag_token)
        elif self.tool in ("brush_reveal", "brush_hide"):
            self._brush_fog(cx, cy)
        elif self.tool == "erase_obs":
            self._obs_erase_at(cx, cy)
            self._draw_erase_cursor(cx, cy)
        elif self.tool == "ruler" and getattr(self, "_ruler_start_pt", None) is not None:
            self._ruler_update(cx, cy)
        elif self.tool == "obstacle_free" and self._obs_free_pts:
            self._obs_free_move(cx, cy)
        elif self._box_select_start is not None:
            self._box_select_update(cx, cy)
        elif self.tool == "resize_map":
            self._map_resize_drag(cx, cy, event)

    def _mb1_up(self, event):
        cx, cy = self._canvas_xy(event)
        self._last_fog_cell = None
        if self.tool == "obstacle_free" and self._obs_free_pts:
            self._obs_free_end()
            return
        if self.tool in ("brush_reveal", "brush_hide"):
            self._save_state()
            return
        if self.tool == "erase_obs":
            self.canvas.delete("erase_preview")
            self._save_state()
            return
        if self.tool == "ruler":
            self._ruler_end()
            return
        if self._drag_note is not None:
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

        if getattr(self, "_drag_token", None) is not None:
            self._drop_drag_anchor(event)
            return

        if self.tool in ("reveal", "hide"):
            self._poly_apply()
            return
        # Obstacle poly en cours → valider
        if self.tool == "obstacle_poly":
            self._obs_poly_apply()
            return
        # Clic droit en mode obstacle_free → supprimer obstacle touché
        if self.tool == "obstacle_free":
            self._obs_delete_at(cx, cy)
            return
        # Clic droit sur une porte → menu contextuel
        col, row = self._canvas_to_cell(cx, cy)
        door_hit = self._door_at(col, row)
        if door_hit is not None:
            self._show_door_context_menu(event, door_hit)
            return
        # Clic droit sur une note → menu contextuel
        hit = self._note_at(cx, cy)
        if hit is not None:
            self._show_note_context_menu(event, hit)
            return
        # Clic droit sur un obstacle (outil select) → menu contextuel
        if self.tool == "select":
            wx, wy = cx / self.zoom, cy / self.zoom
            for obs in reversed(self._obstacles):
                if self._obs_contains(obs["pts"], wx, wy):
                    self._show_obstacle_context_menu(event, obs)
                    return

        # ── Clic droit sur un token ────────────────────────────────────────────
        # Cherche le token sous le curseur
        hit_tok = None
        cp = self._cp
        for tok in reversed(self.tokens):
            size = float(tok.get("size", 1))
            tcx = (tok["col"] + size / 2.0) * cp
            tcy = (tok["row"] + size / 2.0) * cp
            rad = cp * size * 0.55
            if abs(tcx - cx) <= rad and abs(tcy - cy) <= rad:
                hit_tok = tok
                break

        if hit_tok is not None:
            # Si le token touché fait partie d'une sélection (≥1 token)
            # → menu contextuel pour toute la sélection
            if id(hit_tok) in self._selected_tokens and len(self._selected_tokens) >= 1:
                self._show_selection_context_menu(event, hit_tok)
                return
            # Token isolé → menu contextuel (renommer, déplacer, supprimer)
            self._show_token_context_menu(event, hit_tok)

    def _drop_drag_anchor(self, event):
        """Dépose un point de passage sur la case survolée pendant un drag."""
        tok = getattr(self, "_drag_token", None)
        if not tok: return
        cx, cy = self._canvas_xy(event)
        cp = self._cp
        new_col = (cx - self._drag_offset[0]) / cp - 0.5
        new_row = (cy - self._drag_offset[1]) / cp - 0.5
        
        if not hasattr(self, "_drag_waypoints"):
            self._drag_waypoints = []
            
        # On sauvegarde la case (arrondie) en tant qu'ancre
        self._drag_waypoints.append((round(new_col), round(new_row)))
        
        # On force un re-calcul visuel immédiat
        self._tok_drag(event, tok)

    # ─── Menus contextuels ────────────────────────────────────────────────────

    def _show_token_context_menu(self, event, tok):
        """Menu contextuel clic droit sur un token isolé (non sélectionné)."""
        menu = tk.Menu(self.canvas, tearoff=0,
                       bg="#1a1a2e", fg="#dde0e8",
                       activebackground="#2a2a4e", activeforeground="#ffffff",
                       font=("Consolas", 9))
        name = tok.get("name", "?")
        hp, max_hp = tok.get("hp", -1), tok.get("max_hp", -1)
        hp_txt = f"  PV {hp}/{max_hp}" if hp >= 0 else ""
        menu.add_command(label=f"── {name} ({tok['type']}){hp_txt} ──", state="disabled")
        menu.add_separator()
        menu.add_command(label="✏  Renommer",
                         command=lambda: self._rename_token(tok))
        menu.add_command(label="❤  Modifier PV",
                         command=lambda: self._edit_token_hp(tok))
        menu.add_command(label="🔀  Changer de case",
                         command=lambda: self._teleport_token(tok))
        menu.add_command(label="⚡  Conditions",
                         command=lambda: self._edit_token_conditions(tok))

        # Sous-menu taille
        size_menu = tk.Menu(menu, tearoff=0,
                            bg="#1a1a2e", fg="#dde0e8",
                            activebackground="#2a2a4e", activeforeground="#ffffff",
                            font=("Consolas", 9))
        for size_name, size_val in TOKEN_SIZES.items():
            size_menu.add_command(
                label=size_name,
                command=lambda sv=size_val, sn=size_name: self._set_token_size(tok, sv))
        menu.add_cascade(label="📐  Taille", menu=size_menu)

        # Altitude (vol / 3D)
        alt = tok.get("altitude_ft", 0)
        alt_lbl = f"✈  Altitude  [{alt}ft]" if alt > 0 else "✈  Altitude  [sol]"
        menu.add_command(label=alt_lbl,
                         command=lambda: self._edit_token_altitude(tok))

        # ─── AURA ───
        menu.add_command(label="🌀 Configurer l'aura",
                         command=lambda: self._edit_token_aura(tok))

        # ─── TRACKER / BESTIARY ──────────────────────────────────────────────
        menu.add_separator()

        # Fiche de monstre — visible seulement si bestiary_name est connu
        bname = tok.get("bestiary_name", "").strip()
        if not bname:
            # Tentative de résolution à la volée pour l'affichage du menu
            bname, _ = self._resolve_bestiary_name(tok)
            if bname:
                tok["bestiary_name"] = bname   # mise en cache immédiate

        if bname:
            menu.add_command(
                label=f"📋  Fiche : {bname}",
                command=lambda b=bname, n=name:
                    self._open_monster_sheet_for_token(n, b))

        # Envoyer au tracker — toujours disponible (gère les cas dégradés)
        menu.add_command(
            label="⚔️  Envoyer au Tracker",
            command=lambda: self._send_token_to_tracker(tok))

        menu.add_separator()
        menu.add_command(label="✕  Supprimer",
                         command=lambda: self._delete_single_token(tok))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _show_note_context_menu(self, event, note):
        """Menu contextuel clic droit sur une note."""
        menu = tk.Menu(self.canvas, tearoff=0,
                       bg="#1a1a2e", fg="#dde0e8",
                       activebackground="#2a2a4e", activeforeground="#ffffff",
                       font=("Consolas", 9))
        menu.add_command(label="── Note ──", state="disabled")
        menu.add_separator()
        menu.add_command(label="✏  Éditer le texte",
                         command=lambda: self._edit_note(note))
        menu.add_command(label="🎨  Changer la couleur",
                         command=lambda: self._pick_note_color(note))
        menu.add_separator()
        menu.add_command(label="✕  Supprimer",
                         command=lambda: self._delete_note(note))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _show_door_context_menu(self, event, door):
        """Menu contextuel clic droit sur une porte."""
        state_txt = "ouverte" if door["open"] else "fermée"
        menu = tk.Menu(self.canvas, tearoff=0,
                       bg="#1a1a2e", fg="#dde0e8",
                       activebackground="#2a2a4e", activeforeground="#ffffff",
                       font=("Consolas", 9))
        lbl = door.get("label", "") or "Porte"
        menu.add_command(label=f"── {lbl} ({state_txt}) ──", state="disabled")
        menu.add_separator()
        toggle_lbl = "Fermer" if door["open"] else "Ouvrir"
        menu.add_command(label=f"🚪  {toggle_lbl}",
                         command=lambda: self._door_toggle_open(door))
        menu.add_command(label="✏  Éditer le label",
                         command=lambda: self._edit_door_label(door))
        menu.add_separator()
        menu.add_command(label="✕  Supprimer",
                         command=lambda: self._delete_door(door))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _show_obstacle_context_menu(self, event, obs):
        """Menu contextuel clic droit sur un obstacle (outil select)."""
        menu = tk.Menu(self.canvas, tearoff=0,
                       bg="#1a1a2e", fg="#dde0e8",
                       activebackground="#2a2a4e", activeforeground="#ffffff",
                       font=("Consolas", 9))
        lbl = obs.get("label", "") or "Obstacle"
        menu.add_command(label=f"── {lbl} ──", state="disabled")
        menu.add_separator()
        menu.add_command(label="✏  Éditer le label",
                         command=lambda: self._edit_obstacle_label(obs))
        menu.add_command(label="🎨  Changer la couleur",
                         command=lambda: self._pick_obstacle_color_for(obs))
        menu.add_separator()
        menu.add_command(label="✕  Supprimer",
                         command=lambda: self._delete_obstacle(obs))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()