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
        state_txt = "ouverte" if door["open"] else "fermée"
        label_txt = f" ({door.get('label', '')})" if door.get("label") else ""
        if hasattr(self, "_status_var"):
            self._status_var.set(
                f"Porte{label_txt} — maintenant {state_txt}"
            )

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

    def add_hotlink_note(self, label: str, title: str, content: str):
        """Ajoute une note hotlink au centre de l'écran avec les données de recherche."""
        # Calculer le centre de l'écran
        W_full, H_full = self._wh
        x0f, x1f = self.canvas.xview()
        y0f, y1f = self.canvas.yview()
        vx0 = max(0, int(x0f * W_full))
        vy0 = max(0, int(y0f * H_full))
        vx1 = min(W_full, int(x1f * W_full))
        vy1 = min(H_full, int(y1f * H_full))
        cx = (vx0 + vx1) / 2
        cy = (vy0 + vy1) / 2

        color = "#e1bee7"  # Couleur spécifique pour les hotlinks (violet clair)
        n = {
            "px":  cx / self.zoom,
            "py":  cy / self.zoom,
            "text": f"🔗 {label}",
            "color": color,
            "canvas_ids":[],
            "hotlink_data": {
                "title": title,
                "content": content
            }
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
        z   = self.zoom   # Zoom is still used for font scaling dynamically
        scale = self._cp / self.cell_px
        cx  = n["px"] * scale
        cy  = n["py"] * scale
        col = n["color"]
        fs  = max(7, int(9 * z))

        # Fond noir semi-transparent (stipple gray50 ≈ 50%)
        hw = self.NOTE_W / 2
        hh = self.NOTE_H / 3   # plus compact, juste pour le texte

        bg = self.canvas.create_rectangle(
            cx - hw, cy - hh, cx + hw, cy + hh,
            fill="#000000", outline="", stipple="gray50",
            tags=("note",))

        # Halo noir (lisibilité) — décalé 1 px dans toutes directions
        halos =[]
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

        # --- NOUVEAUX BINDINGS ---
        for iid in ids:
            self.canvas.tag_bind(iid, "<ButtonPress-1>",
                lambda e, note=n: self._note_press(e, note))
            self.canvas.tag_bind(iid, "<ButtonRelease-1>",
                lambda e, note=n: self._note_release(e, note))
            self.canvas.tag_bind(iid, "<Double-Button-1>",
                lambda e, note=n: self._note_double_click(e, note))
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

    # ── Drag et Clic depuis items de la note ──────────────────────────────────

    def _note_press(self, event, note: dict):
        """Enregistre le clic et initie un drag si l'outil Note est actif."""
        # On enregistre la position initiale pour distinguer un clic d'un drag
        self._note_click_start_xy = (event.x, event.y)
        
        # Le drag n'est permis qu'avec l'outil "note"
        if self.tool != "note":
            return
            
        cx, cy = self._canvas_xy(event)
        self._drag_note = note
        self._drag_note_off = (cx - note["px"] * self.zoom,
                               cy - note["py"] * self.zoom)

    def _note_release(self, event, note: dict):
        """Détecte si la note a été cliquée (sans être déplacée) pour l'ouvrir."""
        start_xy = getattr(self, "_note_click_start_xy", None)
        if start_xy:
            dx = abs(event.x - start_xy[0])
            dy = abs(event.y - start_xy[1])
            # Si le déplacement est inférieur à 3 pixels, c'est un simple clic
            if dx <= 3 and dy <= 3:
                if "hotlink_data" in note:
                    self._open_hotlink_view(note)
        self._note_click_start_xy = None

    def _note_double_click(self, event, note: dict):
        """Gère le double-clic directement sur l'item (prioritaire sur le canvas)."""
        if "hotlink_data" in note:
            # Sur un hotlink, on ouvre la vue texte (pour éviter d'ouvrir l'édition)
            self._open_hotlink_view(note)
        else:
            # Sur une note normale, on ouvre l'éditeur de texte
            self._edit_note(note)

    # ── Double-clic canvas (hors items bindés) ────────────────────────────────

    def _mb1_double(self, event):
        cx, cy = self._canvas_xy(event)
        # Double-clic sur une note → éditer ou ouvrir hotlink
        hit = self._note_at(cx, cy)
        if hit is not None:
            if "hotlink_data" in hit:
                self._open_hotlink_view(hit)
            else:
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
        door_hit = self._door_at(cx, cy)
        if door_hit is not None:
            self._edit_door_label(door_hit)

    # ─── HOTLINKS (Lien vers Moteur Aventure) ─────────────────────────────────

    def add_hotlink_note(self, label: str, title: str, content: str):
        """Ajoute une note hotlink au centre de l'écran avec les données de recherche."""
        W_full, H_full = self._wh
        x0f, x1f = self.canvas.xview()
        y0f, y1f = self.canvas.yview()
        vx0 = max(0, int(x0f * W_full))
        vy0 = max(0, int(y0f * H_full))
        vx1 = min(W_full, int(x1f * W_full))
        vy1 = min(H_full, int(y1f * H_full))
        cx = (vx0 + vx1) / 2
        cy = (vy0 + vy1) / 2

        color = "#e1bee7"  # Couleur violet clair spécifique pour les hotlinks
        n = {
            "px":  cx / self.zoom,
            "py":  cy / self.zoom,
            "text": f"🔗 {label}",
            "color": color,
            "canvas_ids":[],
            "hotlink_data": {
                "title": title,
                "content": content
            }
        }
        self._notes.append(n)
        self._draw_one_note(n)
        self._save_state()

    def _insert_text_with_images(self, text_widget, content):
        from PIL import Image, ImageTk
        import re
        import os
        if not hasattr(text_widget, "image_refs"):
            text_widget.image_refs = []
        if not hasattr(text_widget, "raw_image_paths"):
            text_widget.raw_image_paths = []
            
        pattern = r'<img src="(.*?)" />'
        last_index = 0
        for match in re.finditer(pattern, content):
            text_before = content[last_index:match.start()]
            if text_before:
                text_widget.insert(tk.END, text_before)
                
            img_path = match.group(1)
            full_path = os.path.join("images", img_path)
            
            if os.path.exists(full_path):
                try:
                    text_widget.raw_image_paths.append(img_path)
                    img = Image.open(full_path)
                    max_w, max_h = 750, 550
                    img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                    text_widget.image_refs.append(photo)
                    text_widget.insert(tk.END, "\n")
                    text_widget.image_create(tk.END, image=photo)
                    text_widget.insert(tk.END, "\n")
                except Exception as e:
                    print(f"[MapNote] Erreur chargement image {full_path}: {e}")
                    text_widget.insert(tk.END, f"\n[Image manquante: {img_path}]\n")
            else:
                text_widget.insert(tk.END, f"\n[Image introuvable: {img_path}]\n")
                
            last_index = match.end()
            
        text_remaining = content[last_index:]
        if text_remaining:
            text_widget.insert(tk.END, text_remaining)

    def _open_hotlink_view(self, note: dict):
        """Ouvre une fenêtre pour afficher le contenu du hotlink avec option traduction."""
        data = note.get("hotlink_data", {})
        title = data.get("title", "Note liée")
        content = data.get("content", note.get("text", ""))

        win = tk.Toplevel(self.win)
        win.title(title)
        win.geometry("850x650")
        win.configure(bg="#0d1018")
        
        tool_frame = tk.Frame(win, bg="#1a1a2e", pady=5, padx=5)
        tool_frame.pack(fill=tk.X, side=tk.TOP)
        
        btn_save = tk.Button(tool_frame, text="💾 Sauvegarder modifs", bg="#2e7d32", fg="white",
                             font=("Consolas", 10, "bold"), relief="flat")
        btn_save.pack(side=tk.LEFT, padx=5)

        btn_translate = tk.Button(tool_frame, text="🌍 Traduire en Français (DeepL)", bg="#1e3a5f", fg="#81d4fa",
                                  font=("Consolas", 10, "bold"), relief="flat")
        btn_translate.pack(side=tk.RIGHT, padx=5)
        
        def _share_to_agents():
            if not hasattr(text_widget, "raw_image_paths") or not text_widget.raw_image_paths:
                messagebox.showinfo("Partage", "Aucune image à partager dans cette note.", parent=win)
                return
            img_path = text_widget.raw_image_paths[0]
            full_path = os.path.join("images", img_path)
            if os.path.exists(full_path):
                with open(full_path, "rb") as f:
                    img_bytes = f.read()
                current_text = text_widget.get("1.0", tk.END).strip()
                main_app = getattr(self, "app", self)
                if hasattr(main_app, "_broadcast_shared_image"):
                    main_app._broadcast_shared_image(img_bytes, title, current_text)
                    messagebox.showinfo("Succès", "Image transmise en interne aux agents multimodaux.", parent=win)
                else:
                    messagebox.showerror("Erreur", "L'interface principale n'est pas accessible.", parent=win)
            else:
                messagebox.showerror("Erreur", "Image non trouvée sur le disque.", parent=win)

        btn_share = tk.Button(tool_frame, text="📸 Partager Image(s) aux Agents", bg="#004d40", fg="#b2dfdb",
                              font=("Consolas", 10, "bold"), relief="flat", command=_share_to_agents)
        btn_share.pack(side=tk.RIGHT, padx=5)

        text_widget = tk.Text(win, wrap=tk.WORD, bg="#151520", fg="#e0e0e0", font=("Georgia", 11),
                              padx=15, pady=15, selectbackground="#4a4a75", undo=True)
        text_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        import tkinter.ttk as ttk
        scroll = ttk.Scrollbar(text_widget, command=text_widget.yview)
        text_widget.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self._insert_text_with_images(text_widget, content)
        # On garde le texte éditable

        def _save_local():
            current_text = text_widget.get("1.0", tk.END).strip()
            if hasattr(text_widget, "raw_image_paths") and text_widget.raw_image_paths:
                for img_path in text_widget.raw_image_paths:
                    current_text += f'\n<img src="{img_path}" />\n'
            note["hotlink_data"]["content"] = current_text
            self._save_state()
            win.title(title + " — (Sauvegardé ✓)")

        btn_save.configure(command=_save_local)

        # Fonction de traduction si le MJ a oublié de traduire avant d'épingler
        def _translate_local():
            import os, json, urllib.request, urllib.error, threading
            from tkinter import messagebox
            api_key = os.getenv("TRANSLATE_DEEPL_API_KEY")
            if not api_key:
                messagebox.showerror("Erreur", "Clé DeepL introuvable dans .env", parent=win)
                return

            btn_translate.configure(state="disabled", text="⏳ Traduction en cours...")
            source_text = text_widget.get("1.0", tk.END).strip()
            if hasattr(text_widget, "raw_image_paths") and text_widget.raw_image_paths:
                for img_path in text_widget.raw_image_paths:
                    source_text += f'\n<img src="{img_path}" />\n'

            def fetch():
                try:
                    endpoint = "https://api-free.deepl.com/v2/translate" if api_key.endswith(":fx") else "https://api.deepl.com/v2/translate"
                    payload = json.dumps({
                        "text": [source_text],
                        "target_lang": "FR",
                        "tag_handling": "xml",
                        "ignore_tags": ["img"]
                    }).encode('utf-8')
                    req = urllib.request.Request(endpoint, data=payload, method="POST")
                    req.add_header("Authorization", f"DeepL-Auth-Key {api_key}")
                    req.add_header("Content-Type", "application/json")
                    with urllib.request.urlopen(req) as response:
                        res = json.loads(response.read().decode('utf-8'))
                        t_text = res["translations"][0]["text"]
                    text_widget.after(0, lambda: apply_t(t_text))
                except Exception as e:
                    text_widget.after(0, lambda: err(str(e)))

            def apply_t(t_text):
                text_widget.configure(state="normal")
                text_widget.delete("1.0", tk.END)
                if hasattr(text_widget, "image_refs"):
                    text_widget.image_refs.clear()
                self._insert_text_with_images(text_widget, t_text)
                # Reste éditable
                btn_translate.configure(text="🌍 Traduit en Français", bg="#2e7d32", fg="white")
                
                # Re-append images for saving
                current_text = text_widget.get("1.0", tk.END).strip()
                if hasattr(text_widget, "raw_image_paths") and text_widget.raw_image_paths:
                    for img_path in text_widget.raw_image_paths:
                        current_text += f'\n<img src="{img_path}" />\n'
                        
                # Sauvegarde la traduction sur la carte instantanément
                note["hotlink_data"]["content"] = current_text
                self._save_state()

            def err(e_msg):
                messagebox.showerror("Erreur", str(e_msg), parent=win)
                btn_translate.configure(state="normal", text="🌍 Réessayer")

            threading.Thread(target=fetch, daemon=True).start()

        btn_translate.configure(command=_translate_local)


    # ─── Outil Porte ─────────────────────────────────────────────────────────

    def _door_handle_at(self, cx: float, cy: float) -> "dict | None":
        """Retourne la porte dont la poignée contient (cx, cy) en coords canvas, ou None."""
        import math
        cp = self._cp
        h_d = cp * 0.25
        scale = cp / self.cell_px
        
        for d in reversed(self._doors):
            w_d = cp * d.get("length_scale", 1.0)
            d_px = d.get("px", (d.get("col", 0) + 0.5) * self.cell_px)
            d_py = d.get("py", (d.get("row", 0) + 0.5) * self.cell_px)
            nx, ny = d_px * scale, d_py * scale
            
            angle_rad = math.radians(d.get("rotation", 0))
            is_open = d.get("open", False)
            open_a = d.get("open_angle", 90 if is_open else 0)
            
            mirrored = d.get("mirrored", False)
            m = 1 if not mirrored else -1
            
            hx, hy = m * (-w_d / 2), 0
            swing_px, swing_py = m * (w_d / 2), 0
            
            if open_a > 0:
                open_rad = math.radians(m * open_a)
                cos_o = math.cos(open_rad)
                sin_o = math.sin(open_rad)
                sx = hx + (swing_px - hx) * cos_o - (swing_py - hy) * sin_o
                sy = hy + (swing_px - hx) * sin_o + (swing_py - hy) * cos_o
            else:
                sx, sy = swing_px, swing_py
                
            cos_a = math.cos(angle_rad)
            sin_a = math.sin(angle_rad)
            handle_x = nx + sx * cos_a - sy * sin_a
            handle_y = ny + sx * sin_a + sy * cos_a
            
            handle_r = max(6, w_d * 0.15)
            if (cx - handle_x)**2 + (cy - handle_y)**2 <= handle_r**2:
                return d
                
        return None

    def _door_at(self, cx: float, cy: float) -> "dict | None":
        """Retourne la porte dont le cadre (virtuel) contient (cx, cy) en coords canvas, ou None."""
        import math
        cp = self._cp
        h_d = cp * 0.25
        tol = cp * 0.3
        scale = cp / self.cell_px
        
        for d in reversed(self._doors):
            w_d = cp * d.get("length_scale", 1.0)
            d_px = d.get("px", (d.get("col", 0) + 0.5) * self.cell_px)
            d_py = d.get("py", (d.get("row", 0) + 0.5) * self.cell_px)
            nx, ny = d_px * scale, d_py * scale
            
            angle_rad = math.radians(d.get("rotation", 0))
            
            tx = cx - nx
            ty = cy - ny
            
            cos_a = math.cos(-angle_rad)
            sin_a = math.sin(-angle_rad)
            lx = tx * cos_a - ty * sin_a
            ly = tx * sin_a + ty * cos_a
            
            is_open = d.get("open", False)
            open_a = d.get("open_angle", 90 if is_open else 0)
            mirrored = d.get("mirrored", False)
            m = 1 if not mirrored else -1

            if open_a > 0:
                open_rad = math.radians(m * open_a)
                hx, hy = m * (-w_d / 2), 0
                cos_io = math.cos(-open_rad)
                sin_io = math.sin(-open_rad)
                px = hx + (lx - hx) * cos_io - (ly - hy) * sin_io
                py = hy + (lx - hx) * sin_io + (ly - hy) * cos_io
                if (-w_d/2 - tol <= px <= w_d/2 + tol) and (-h_d/2 - tol <= py <= h_d/2 + tol):
                    return d
            else:
                if (-w_d/2 - tol <= lx <= w_d/2 + tol) and (-h_d/2 - tol <= ly <= h_d/2 + tol):
                    return d
        return None

    def _door_create(self, cx: float, cy: float):
        """Ouvre une mini-fenêtre pour saisir un label et crée la porte (fermée) à la position cliquée."""
        px = cx / self.zoom
        py = cy / self.zoom

        dw = tk.Toplevel(self.win)
        dw.title("Nouvelle porte")
        dw.geometry("280x160")
        dw.configure(bg="#0d1018")
        dw.resizable(False, False)
        dw.wait_visibility()
        dw.grab_set()

        tk.Label(dw, text=f"Nouvelle Porte",
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

        tk.Label(dw, text="Largeur de la porte :",
                 bg="#0d1018", fg="#aaaacc",
                 font=("Consolas", 8)).pack(pady=(4, 0))
        size_var = tk.StringVar(value="5 feet")
        opt = tk.OptionMenu(dw, size_var, "3 feet", "5 feet", "10 feet")
        opt.config(bg="#252538", fg="#eeeeee", font=("Consolas", 9), relief="flat", highlightthickness=0)
        opt["menu"].config(bg="#252538", fg="#eeeeee", font=("Consolas", 9))
        opt.pack(pady=2)

        def _confirm(event=None):
            label = entry.get().strip()
            sel = size_var.get()
            scale = 1.0
            if "3 " in sel: scale = 0.6
            elif "10 " in sel: scale = 2.0
            
            dw.destroy()
            door = {"px": px, "py": py, "rotation": 0, "open": False,
                    "label": label, "length_scale": scale, "canvas_ids": []}
            self._doors.append(door)
            self._cut_walls_with_door(door)
            self._redraw_one_door(door)
            self._save_state()

        entry.bind("<Return>", _confirm)
        tk.Button(dw, text="Créer (fermée)", bg="#2c1200", fg="#ff9966",
                  font=("Consolas", 9, "bold"), relief="flat",
                  command=_confirm).pack(pady=8)

    def _delete_door(self, door: dict):
        for cid in door.get("canvas_ids", []):
            self.canvas.delete(cid)
        if door in self._doors:
            self._doors.remove(door)
        self._save_state()

    def _cut_walls_with_door(self, door: dict):
        import math
        d_px = door.get("px", (door.get("col", 0) + 0.5) * self.cell_px)
        d_py = door.get("py", (door.get("row", 0) + 0.5) * self.cell_px)
        C = (d_px, d_py)
        scale_len = door.get("length_scale", 1.0)
        R = self.cell_px * 0.5 * scale_len
        new_walls = []
        for w in getattr(self, "_walls", []):
            A = w["p1"]
            B = w["p2"]
            dx, dy = B[0] - A[0], B[1] - A[1]
            lensq = dx*dx + dy*dy
            if lensq < 1e-5:
                new_walls.append(w)
                continue
            fAx, fAy = A[0] - C[0], A[1] - C[1]
            a = lensq
            b = 2 * (fAx * dx + fAy * dy)
            c = fAx*fAx + fAy*fAy - R*R
            delta = b*b - 4*a*c
            if delta <= 0:
                new_walls.append(w)
                continue
            sq_delta = math.sqrt(delta)
            t1 = (-b - sq_delta) / (2*a)
            t2 = (-b + sq_delta) / (2*a)
            if t1 > t2: t1, t2 = t2, t1
            valid_segs = []
            if t1 > 0:
                valid_segs.append((0, min(1.0, t1)))
            if t2 < 1.0:
                valid_segs.append((max(0.0, t2), 1.0))
            if not valid_segs:
                 pass
            elif len(valid_segs) == 1 and valid_segs[0] == (0, 1.0):
                 new_walls.append(w)
            else:
                 for (st, et) in valid_segs:
                     P1 = (A[0] + st*dx, A[1] + st*dy)
                     P2 = (A[0] + et*dx, A[1] + et*dy)
                     new_walls.append({"p1": P1, "p2": P2})
        self._walls = new_walls
        self._wall_pil = None
        self._composite()

    def _door_is_player_visible(self, door: dict) -> bool:
        if getattr(self, "_dm_view", True):
            return True
        if getattr(self, "_fog_mask", None) is None:
            return True
        mW, mH = self._fog_mask.size
        if mW == 0 or mH == 0:
            return False
            
        import math
        d_px = door.get("px", (door.get("col", 0) + 0.5) * self.cell_px)
        d_py = door.get("py", (door.get("row", 0) + 0.5) * self.cell_px)
        
        w = door.get("length_scale", 1.0) * self.cell_px
        h = 0.25 * self.cell_px
        ang = math.radians(door.get("rotation", 0))
        cos_a = math.cos(ang)
        sin_a = math.sin(ang)
        
        pts_local = [
            (0, 0),
            (-w/2, -h/2), (w/2, -h/2), (w/2, h/2), (-w/2, h/2),
            (-w/2, 0), (w/2, 0), (0, -h/2), (0, h/2)
        ]
        
        for lx, ly in pts_local:
            px = d_px + lx * cos_a - ly * sin_a
            py = d_py + lx * sin_a + ly * cos_a
            cf = px / self.cell_px
            rf = py / self.cell_px
            fpx = min(max(0, int(cf * mW / self.cols)), mW - 1)
            fpy = min(max(0, int(rf * mH / self.rows)), mH - 1)
            val = self._fog_mask.getpixel((fpx, fpy))
            if val <= 127:
                return True
        return False

    def _draw_one_door(self, door: dict):
        """Overlay d'état de porte sur l'image : rectangle orienté.
        Porte FERMÉE : rectangle fin plein rouge.
        Porte OUVERTE : rectangle fin plein vert.
        """
        if hasattr(self, "_door_is_player_visible") and not self._door_is_player_visible(door):
            door["canvas_ids"] = []
            return

        import math
        cp   = self._cp
        d_px = door.get("px", (door.get("col", 0) + 0.5) * self.cell_px)
        d_py = door.get("py", (door.get("row", 0) + 0.5) * self.cell_px)
        scale = cp / self.cell_px
        cx_ = d_px * scale
        cy_ = d_py * scale
        angle_deg = door.get("rotation", 0)
        angle_rad = math.radians(angle_deg)
        
        # Dimensions porte (orientée horizontalle à angle 0)
        w = cp * door.get("length_scale", 1.0)
        h = cp * 0.25 # porte fine

        corners = [
            (-w / 2, -h / 2),
            ( w / 2, -h / 2),
            ( w / 2,  h / 2),
            (-w / 2,  h / 2)
        ]

        mirrored = door.get("mirrored", False)
        m = 1 if not mirrored else -1  # flip x for mirrored hinge

        is_open = door.get("open", False)
        open_a = door.get("open_angle", 90 if is_open else 0)

        hx, hy = m * (-w / 2), 0
        swing_px, swing_py = m * (w / 2), 0

        if open_a > 0:
            open_rad = math.radians(m * open_a)
            cos_o = math.cos(open_rad)
            sin_o = math.sin(open_rad)
            open_corners = []
            for dx, dy in corners:
                nx = hx + (dx - hx) * cos_o - (dy - hy) * sin_o
                ny = hy + (dx - hx) * sin_o + (dy - hy) * cos_o
                open_corners.append((nx, ny))
            corners = open_corners
            sx = hx + (swing_px - hx) * cos_o - (swing_py - hy) * sin_o
            sy = hy + (swing_px - hx) * sin_o + (swing_py - hy) * cos_o
        else:
            sx, sy = swing_px, swing_py

        rot_corners = []
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        for dx, dy in corners:
            rx = dx * cos_a - dy * sin_a
            ry = dx * sin_a + dy * cos_a
            rot_corners.extend([cx_ + rx, cy_ + ry])

        ids  = []

        if door["open"]:
            fill_col = "#43a047"
            out_col = "#1b5e20"
            label_col = "#a5d6a7"
        else:
            fill_col = "#e53935"
            out_col = "#b71c1c"
            label_col = "#ef9a9a"

        ids.append(self.canvas.create_polygon(
            rot_corners, fill=fill_col, outline=out_col, width=2, tags="door"))

        if not door.get("open", False):
            # Arrow pointing toward the opening side
            arrow_pts = [(-w*0.15, -h*0.15), (w*0.15, -h*0.15), (0, h*0.4)]
            rot_arrow = []
            for dx, dy in arrow_pts:
                rx = dx * cos_a - dy * sin_a
                ry = dx * sin_a + dy * cos_a
                rot_arrow.extend([cx_ + rx, cy_ + ry])
            ids.append(self.canvas.create_polygon(
                rot_arrow, fill="#ffffff", outline="", tags="door"))

            # Hinge dot on the correct side
            hx, hy = m * (-w / 2), 0
            hx_rot = hx * cos_a - hy * sin_a
            hy_rot = hx * sin_a + hy * cos_a
            hrx = cx_ + hx_rot
            hry = cy_ + hy_rot
            hrv = max(3, w * 0.06)
            ids.append(self.canvas.create_oval(
                hrx - hrv, hry - hrv, hrx + hrv, hry + hrv,
                fill="#ffaaaa", outline="#e53935", width=1, tags="door"))

        handle_x = cx_ + sx * cos_a - sy * sin_a
        handle_y = cy_ + sx * sin_a + sy * cos_a
        
        if getattr(self, "_hovered_door_handle", None) is door:
            handle_r = max(8, w * 0.16)
            outline_col = "#ffffff"
        else:
            handle_r = max(5, w * 0.1)
            outline_col = "#f57f17"
            
        ids.append(self.canvas.create_oval(
            handle_x - handle_r, handle_y - handle_r,
            handle_x + handle_r, handle_y + handle_r,
            fill="#ffeb3b", outline=outline_col, width=2, tags=("door", "door_handle")))

        if door.get("label"):
            font_sz = max(6, int(cp * 0.20))
            try:
                txt_id = self.canvas.create_text(
                    cx_, cy_,
                    text=door["label"], fill=label_col,
                    font=("Consolas", font_sz, "bold"), angle=-angle_deg, tags="door")
                ids.append(txt_id)
            except Exception:
                # Fallback si l'angle n'est pas supporté (ancienne version tk)
                txt_id = self.canvas.create_text(
                    cx_, cy_,
                    text=door["label"], fill=label_col,
                    font=("Consolas", font_sz, "bold"), tags="door")
                ids.append(txt_id)

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
            
        visible_doors = []
        for d in self._doors:
            if hasattr(self, "_door_is_player_visible") and getattr(self, "_dm_view", False) == False and not self._door_is_player_visible(d):
                continue
            visible_doors.append(d)
            
        if not visible_doors:
            return ""
            
        lines = ["\n🚪 PORTES :"]
        for d in visible_doors:
            state  = "ouverte" if d["open"] else "FERMÉE"
            label  = f" — {d['label']}" if d.get("label") else ""
            d_px = d.get("px", (d.get("col", 0) + 0.5) * self.cell_px)
            d_py = d.get("py", (d.get("row", 0) + 0.5) * self.cell_px)
            col = int(d_px / self.cell_px)
            row = int(d_py / self.cell_px)
            lines.append(
                f"  • Col {col+1}, Lig {row+1}{label} : {state}")
        return "\n".join(lines)

    def _notes_description(self) -> str:
        if not self._notes:
            return ""
        lines =[]
        for n in self._notes:
            # On ignore les notes MJ (hotlinks) pour l'IA
            if n.get("hotlink_data"):
                continue
            # px/py sont en espace-map (indépendant du zoom)
            col = int(n["px"] / self.cell_px)
            row = int(n["py"] / self.cell_px)
            lines.append(f"  📌 Col {col+1}, Lig {row+1} : {n['text']}")
            
        if not lines:
            return ""
            
        return "\nNotes sur la carte :\n" + "\n".join(lines)

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
            # 1. Mettre à jour silencieusement le prompt système sans perturber le chat
            if hasattr(self, "update_sys_prompt_fn") and self.update_sys_prompt_fn is not None:
                self.update_sys_prompt_fn()

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