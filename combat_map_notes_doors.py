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

class NotesDoorsMixin:
    pass
    # ─── Actions sur portes ───────────────────────────────────────────────────

    def _door_toggle_open(self, door):
        door["open"] = not door["open"]
        self._redraw_one_door(door)
        self._save_state()

    def _edit_door_label(self, door):
        new_label = simpledialog.askstring(
            "Label de la porte", "Nouveau label (vide = effacer) :",
            initialvalue=door.get("label", ""), parent=self.win)
        if new_label is None:
            return
        door["label"] = new_label.strip()
        self._redraw_one_door(door)
        self._save_state()

    # ─── Actions sur notes ────────────────────────────────────────────────────

    def _pick_note_color(self, note):
        from tkinter import colorchooser
        color = colorchooser.askcolor(
            color=note.get("color", "#ffe082"),
            title="Couleur de la note", parent=self.win)
        if color and color[1]:
            note["color"] = color[1]
            self._redraw_one_note(note)
            self._save_state()

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
        # Double-clic sur une note → éditer
        hit = self._note_at(cx, cy)
        if hit is not None:
            self._edit_note(hit)
            return
        # Double-clic sur un token en mode select → renommer
        if self.tool == "select":
            cp = self._cp
            for tok in self.tokens:
                tcx = (tok["col"] + 0.5) * cp
                tcy = (tok["row"] + 0.5) * cp
                if abs(tcx - cx) <= cp * 0.55 and abs(tcy - cy) <= cp * 0.55:
                    self._rename_token(tok)
                    return
        # Double-clic sur une porte → éditer son label
        col, row = self._canvas_to_cell(cx, cy)
        door_hit = self._door_at(col, row)
        if door_hit is not None:
            self._edit_door_label(door_hit)

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
                msg = (
                    f"[Carte] {name} déplacé : "
                    f"Col {old_col+1},Lig {old_row+1} → "
                    f"Col {actual_col+1},Lig {actual_row+1} "
                    f"({dist_m:.1f} m)"
                )
                # Notifier les agents du déplacement (validé par autogen_engine)
                self._notify_token_moved(name, tok["type"],
                                         old_col, old_row, actual_col, actual_row,
                                         source="engine")
                return msg
        return f"[Carte] Token '{name}' introuvable — vérifiez qu'il est placé sur la carte."

    def _notify_token_moved(self, name: str, ttype: str,
                            old_col: int, old_row: int,
                            new_col: int, new_row: int,
                            source: str = "mj",
                            alignment: str = ""):
        """
        Notifie le chat et les agents autogen qu'un token a bougé.

        source    = "mj"     → déplacement manuel (drag ou téléportation)
        source    = "engine" → déplacement validé par autogen_engine (action déclarée)
        alignment = "hostile" | "neutral" | "ally" | ""
                    Prioritaire sur ttype pour le label et la couleur du message.

        Le message est injecté dans autogen via inject_fn UNIQUEMENT pour les
        déplacements MJ (source="mj"), afin que les agents en soient informés
        avant leur prochaine action. Les déplacements engine sont déjà dans
        l'historique autogen, pas besoin de les réinjecter.
        """
        # ── Bloqué pendant la pause ───────────────────────────────────────────
        # Les modifications de carte faites pendant la pause sont silencieuses :
        # aucun message chat, aucun inject_fn → les héros ne réagiront pas.
        # L'état de la carte sera reflété via _rebuild_agent_prompts à la reprise.
        if getattr(getattr(self, "app", None), "_session_paused", False):
            return

        dcol   = new_col - old_col
        drow   = new_row - old_row
        dist_m = max(abs(dcol), abs(drow)) * 1.5

        # ── Label de direction ────────────────────────────────────────────────
        dirs = []
        if drow < 0: dirs.append("nord")
        if drow > 0: dirs.append("sud")
        if dcol > 0: dirs.append("est")
        if dcol < 0: dirs.append("ouest")
        dir_txt = "-".join(dirs) if dirs else "sur place"

        # ── Label et couleur : alignement prioritaire sur le type ─────────────
        # Un allié nommé "Rictavio" de type "monster" doit afficher "l'allié",
        # pas "l'ennemi".
        if alignment == "ally":
            type_label = "l'allié"
            msg_color  = "#81c784"   # vert
        elif alignment == "neutral":
            type_label = "le neutre"
            msg_color  = "#fdd835"   # jaune
        elif alignment == "hostile":
            type_label = "l'ennemi"
            msg_color  = "#ef9a9a"   # rouge pâle
        else:
            # Pas d'alignement explicite → fallback sur ttype
            type_label = {
                "hero":    "le héros",
                "monster": "l'ennemi",
                "trap":    "l'élément",
            }.get(ttype, "le token")
            msg_color = {
                "hero":    "#64b5f6",
                "monster": "#ef9a9a",
                "trap":    "#ffe082",
            }.get(ttype, "#aaaacc")

        # ── Message court pour le chat ────────────────────────────────────────
        chat_txt = (
            f"🗺️ [Carte] {type_label.capitalize()} **{name}** "
            f"déplacé vers Col {new_col+1}, Lig {new_row+1} "
            f"({dist_m:.1f} m vers le {dir_txt})"
        )
        if self.msg_queue is not None:
            self.msg_queue.put({
                "sender": "Carte",
                "text":   chat_txt,
                "color":  msg_color,
            })

        # ── Injection autogen (MJ uniquement) ─────────────────────────────────
        # Pendant le combat les agents reçoivent déjà la carte à jour via leur
        # system prompt (get_map_prompt → _rebuild_agent_prompts).  Ré-injecter
        # dans le chat déclencherait des réponses hors-tour (violations + spam).
        # On n'injecte que hors-combat.
        try:
            from combat_tracker import COMBAT_STATE as _CS_map
            _combat_active_for_inject = _CS_map.get("active", False)
        except Exception:
            _combat_active_for_inject = False

        if source == "mj" and not _combat_active_for_inject:
            # 1. Mettre à jour silencieusement le prompt système
            if hasattr(self, "update_sys_prompt_fn") and self.update_sys_prompt_fn is not None:
                self.update_sys_prompt_fn()
                
            # 2. Injecter la pastille complète dans l'historique du chat (collapsible)
            if self.inject_fn is not None:
                lines = [
                    f"[MISE À JOUR CARTE — MJ]",
                    f"Le héros/monstre vient d'être déplacé par le MJ :",
                    f"  Ancienne position : Col {old_col+1}, Lig {old_row+1}",
                    f"  Nouvelle position : Col {new_col+1}, Lig {new_row+1} ({dist_m:.1f} m vers le {dir_txt})",
                    "",
                    "Rappel — distances 3D : dist_3D = √(horiz²+Δalt²). Mêlée possible uniquement si dist_3D ≤ 5ft.",
                    "",
                    "Positions actuelles de tous les tokens (altitude incluse) :"
                ]
                for data in self.tokens:
                    c_col = data.get("col", 0) + 1
                    c_row = data.get("row", 0) + 1
                    c_alt = data.get("altitude_ft", 0)
                    c_type = data.get("type", "unknown")
                    alt_str = f" [sol]" if c_alt == 0 else f" ✈ EN VOL {c_alt}ft"
                    lines.append(f"  • {data.get('name', '?')} ({c_type}) → Col {c_col}, Lig {c_row}{alt_str}")
                
                full_txt = "\n".join(lines)
                self.inject_fn(full_txt)

    def _notify_tokens_deleted(self, names: list):
        """
        Notifie le chat et les agents autogen qu'un ou plusieurs tokens ont été supprimés.
        """
        if not names:
            return

        # ── Bloqué pendant la pause ───────────────────────────────────────────
        # Suppression silencieuse : pas de message chat, pas d'inject_fn.
        # Les agents découvriront l'absence du token via leur system prompt
        # à la reprise (get_map_prompt → _rebuild_agent_prompts).
        if getattr(getattr(self, "app", None), "_session_paused", False):
            return

        names_str = ", ".join(names)
        chat_txt = f"🗺️ [Carte] Jeton(s) retiré(s) de la carte : **{names_str}**"
        
        if self.msg_queue is not None:
            self.msg_queue.put({
                "sender": "Carte",
                "text":   chat_txt,
                "color":  "#9e9e9e",
            })
            
        try:
            from combat_tracker import COMBAT_STATE as _CS_map
            _combat_active_for_inject = _CS_map.get("active", False)
        except Exception:
            _combat_active_for_inject = False

        # 1. Mettre à jour silencieusement le prompt système
        if hasattr(self, "update_sys_prompt_fn") and self.update_sys_prompt_fn is not None:
            self.update_sys_prompt_fn()
            
        # 2. Injecter la pastille complète dans l'historique du chat (collapsible)
        if not _combat_active_for_inject and self.inject_fn is not None:
            lines = [
                f"[MISE À JOUR CARTE — MJ]",
                f"Le(s) token(s) suivant(s) ont été retiré(s) de la carte par le MJ : {names_str}",
                "",
                "Positions actuelles de tous les tokens restants (altitude incluse) :"
            ]
            for data in self.tokens:
                c_col = data.get("col", 0) + 1
                c_row = data.get("row", 0) + 1
                c_alt = data.get("altitude_ft", 0)
                c_type = data.get("type", "unknown")
                alt_str = f" [sol]" if c_alt == 0 else f" ✈ EN VOL {c_alt}ft"
                lines.append(f"  • {data.get('name', '?')} ({c_type}) → Col {c_col}, Lig {c_row}{alt_str}")
            
            lines.append("")
            lines.append("[INSTRUCTION NARRATIVE]")
            lines.append(f"Le ou les éléments suivants ont disparu ou été retirés du champ de bataille : {names_str}. "
                         f"Si cela te concerne ou si l'un d'eux était un ennemi/allié majeur, fais 1 courte phrase de réaction Roleplay. Sinon, réponds simplement [SILENCE].")
            
            full_txt = "\n".join(lines)
            self.inject_fn(full_txt)