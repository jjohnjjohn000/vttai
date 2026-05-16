"""
panels_scene_mixin.py

Contient le widget de scène, le popout d'image de lieu et l'éditeur de scène (avec IA).
"""

import os
import re
import json
import threading
import tkinter as tk
from tkinter import filedialog, scrolledtext

from panels_core_mixin import _ghost_close
from window_state import _save_window_state, _get_win_geometry
from state_manager import get_scene, save_scene


class PanelsSceneMixin:
    """Mixin gérant l'interface de la scène active et de l'image de lieu."""

    def _refresh_scene_widget(self):
        """Met à jour les labels du widget scène dans la sidebar."""
        try:
            s = get_scene()
            lieu = s.get("lieu", "?")
            heure = s.get("heure", "")
            has_image = bool(s.get("location_image", "").strip())
            img_icon = "  📸" if has_image else ""
            self._scene_lieu_label.config(
                text=f"📍 {lieu}" + (f"  [{heure}]" if heure else "") + img_icon
            )
            npcs = s.get("npcs_presents", [])
            if npcs:
                self._scene_npcs_label.config(text="👥 " + ", ".join(npcs[:3]) + ("…" if len(npcs) > 3 else ""))
            else:
                self._scene_npcs_label.config(text="👥 Aucun PNJ")
        except Exception as e:
            print(f"[scene widget] {e}")

    def open_location_image_popout(self):
        """Ouvre (ou ramène au premier plan) le popout d'image du lieu.
        La fenêtre peut rester ouverte en permanence. Elle se rafraîchit
        automatiquement quand la scène change (nouveau lieu ou nouvelle image)."""

        # Ramène au premier plan si déjà ouverte
        if getattr(self, "_location_popout", None):
            try:
                self._location_popout.deiconify()
                self._location_popout.lift()
                return
            except Exception:
                self._location_popout = None

        win = tk.Toplevel(self.root)
        win.withdraw()  # Fix XWayland mapping freeze
        win.title("🗺️ Lieu")
        win.configure(bg="#0a0e0a")
        self._location_popout = win

        # ── Restauration géométrie ────────────────────────────────────────────
        _key = "location_image"
        saved = self._win_state.get(_key)
        if saved and all(k in saved for k in ("w","h","x","y")):
            win.geometry(f"{saved['w']}x{saved['h']}+{saved['x']}+{saved['y']}")
        else:
            win.geometry("420x480")

        # ── Persistance + nettoyage à la fermeture ────────────────────────────
        self._win_state["_open_location_image"] = True
        _save_window_state(self._win_state)

        def _on_close():
            g = _get_win_geometry(win)
            if g:
                self._win_state[_key] = g
            self._win_state.pop("_open_location_image", None)
            _save_window_state(self._win_state)
            self._location_popout = None
            _ghost_close(win, self.root)

        win.protocol("WM_DELETE_WINDOW", _on_close)

        # ── Polling géométrie toutes les 2 s ──────────────────────────────────
        def _poll_geom():
            try:
                if not win.winfo_exists(): return
                g = _get_win_geometry(win)
                if g:
                    self._win_state[_key] = g
                    _save_window_state(self._win_state)
                win.after(2000, _poll_geom)
            except Exception:
                pass
        win.after(2000, _poll_geom)

        # ── État interne ──────────────────────────────────────────────────────
        _state = {
            "last_path":  None,   # dernier chemin d'image chargé
            "last_lieu":  None,   # dernier nom de lieu affiché
            "photo_ref":  None,   # référence PhotoImage (anti-GC)
            "pil_orig":   None,   # Image PIL originale (pour resize)
        }

        # ── En-tête : titre du lieu ───────────────────────────────────────────
        hdr = tk.Frame(win, bg="#0d1a0d")
        hdr.pack(fill=tk.X)

        lieu_lbl = tk.Label(
            hdr, text="—", bg="#0d1a0d", fg="#81c784",
            font=("Consolas", 10, "bold"), anchor="w",
            wraplength=360, justify=tk.LEFT
        )
        lieu_lbl.pack(side=tk.LEFT, padx=10, pady=(7, 6), fill=tk.X, expand=True)

        # Bouton envoyer aux agents
        btn_send = tk.Button(
            hdr, text="🎭", bg="#0d1a0d", fg="#64b5f6",
            font=("TkDefaultFont", 10), relief="flat", padx=6,
            cursor="hand2",
            command=self._broadcast_location_image
        )
        btn_send.pack(side=tk.RIGHT, padx=(0, 6), pady=4)
        # Tooltip au survol
        def _tip_enter(e): btn_send.config(bg="#0d2030", fg="#90caf9")
        def _tip_leave(e): btn_send.config(bg="#0d1a0d", fg="#64b5f6")
        btn_send.bind("<Enter>", _tip_enter)
        btn_send.bind("<Leave>", _tip_leave)

        # Séparateur
        tk.Frame(win, bg="#1a3a1a", height=1).pack(fill=tk.X)

        # ── Canvas principal ──────────────────────────────────────────────────
        canvas = tk.Canvas(win, bg="#0a0e0a", highlightthickness=0, cursor="fleur")
        canvas.pack(fill=tk.BOTH, expand=True)

        # ── Barre d'état en bas ───────────────────────────────────────────────
        status_bar = tk.Frame(win, bg="#060a06")
        status_bar.pack(fill=tk.X)
        status_lbl = tk.Label(
            status_bar, text="Aucune image définie",
            bg="#060a06", fg="#3a5a3a",
            font=("Consolas", 8, "italic"), anchor="w"
        )
        status_lbl.pack(side=tk.LEFT, padx=8, pady=3)

        size_lbl = tk.Label(
            status_bar, text="",
            bg="#060a06", fg="#2a4a2a",
            font=("Consolas", 8), anchor="e"
        )
        size_lbl.pack(side=tk.RIGHT, padx=8, pady=3)

        # ── Rendu de l'image sur le canvas ───────────────────────────────────
        def _render_image():
            """Redessine l'image sur le canvas en respectant le ratio."""
            pil_img = _state["pil_orig"]
            if pil_img is None:
                return
            cw = max(canvas.winfo_width(),  1)
            ch = max(canvas.winfo_height(), 1)
            ow, oh = pil_img.size
            # Fit avec letterboxing
            ratio = min(cw / ow, ch / oh)
            nw, nh = max(1, int(ow * ratio)), max(1, int(oh * ratio))
            x0 = (cw - nw) // 2
            y0 = (ch - nh) // 2
            try:
                from PIL.Image import Resampling
                resample = Resampling.LANCZOS
            except ImportError:
                import PIL.Image as _PI
                resample = _PI.ANTIALIAS if hasattr(_PI, "ANTIALIAS") else _PI.LANCZOS
            resized = pil_img.resize((nw, nh), resample)

            # Vignettage subtil sur les bords
            try:
                from PIL import ImageDraw, ImageFilter
                mask = _state.get("_vignette_mask")
                if mask is None or mask.size != (nw, nh):
                    import PIL.Image as _PI2
                    mask = _PI2.new("L", (nw, nh), 255)
                    draw = ImageDraw.Draw(mask)
                    margin = max(nw, nh) // 6
                    for i in range(margin):
                        alpha = int(255 * (i / margin) ** 2)
                        draw.rectangle([i, i, nw-i-1, nh-i-1], outline=alpha)
                    mask = mask.filter(ImageFilter.GaussianBlur(margin // 3))
                    _state["_vignette_mask"] = mask
                result = resized.copy()
                result.putalpha(mask.resize((nw, nh)))
                import PIL.Image as _PI3
                bg_img = _PI3.new("RGBA", (nw, nh), (10, 14, 10, 255))
                bg_img.paste(result, (0, 0), result)
                resized = bg_img.convert("RGB")
            except Exception:
                pass  # Sans PIL avancé, on affiche sans vignette

            try:
                from PIL.ImageTk import PhotoImage as _PTK
                photo = _PTK(resized)
            except Exception:
                import tkinter as _tk2
                try:
                    import io, base64 as _b64
                    buf = io.BytesIO()
                    resized.save(buf, format="PPM")
                    buf.seek(0)
                    photo = tk.PhotoImage(data=_b64.b64encode(buf.read()))
                except Exception:
                    return

            _state["photo_ref"] = photo
            canvas.delete("all")
            # Fond noir total
            canvas.create_rectangle(0, 0, cw, ch, fill="#0a0e0a", outline="")
            canvas.create_image(x0, y0, anchor="nw", image=photo)
            # Cadre décoratif fin
            pad = 4
            canvas.create_rectangle(
                x0 - pad, y0 - pad, x0 + nw + pad, y0 + nh + pad,
                outline="#1a3a1a", width=1
            )
            size_lbl.config(text=f"{ow}×{oh}")

        def _show_no_image(msg="Aucune image pour ce lieu"):
            """Affiche un placeholder élégant quand pas d'image."""
            _state["pil_orig"] = None
            _state["photo_ref"] = None
            canvas.delete("all")
            cw = max(canvas.winfo_width(),  200)
            ch = max(canvas.winfo_height(), 200)
            # Grille de points comme fond
            for i in range(0, cw, 24):
                for j in range(0, ch, 24):
                    canvas.create_oval(i, j, i+1, j+1, fill="#141e14", outline="")
            # Symbole central
            canvas.create_text(cw//2, ch//2 - 18, text="🗺️",
                                font=("Arial", 36), fill="#1e3a1e")
            canvas.create_text(cw//2, ch//2 + 24, text=msg,
                                font=("Consolas", 9, "italic"), fill="#2a4a2a")
            canvas.create_text(cw//2, ch//2 + 42, text="Ajoutez une image via ✏️ Scène Active",
                                font=("Consolas", 8), fill="#1a2a1a")
            size_lbl.config(text="")

        # ── Polling de rafraîchissement ───────────────────────────────────────
        def _refresh():
            """Vérifie toutes les 1.5 s si la scène a changé et re-rendu si besoin."""
            try:
                if not win.winfo_exists():
                    return
            except Exception:
                return

            scene    = get_scene()
            new_lieu = scene.get("lieu", "")
            new_path = scene.get("location_image", "").strip()

            # Mise à jour du titre si le lieu a changé
            if new_lieu != _state["last_lieu"]:
                _state["last_lieu"] = new_lieu
                win.title(f"🗺️ {new_lieu}" if new_lieu else "🗺️ Lieu")
                lieu_lbl.config(text=new_lieu or "—")

            # Rechargement image si le chemin a changé
            if new_path != _state["last_path"]:
                _state["last_path"] = new_path
                _state["_vignette_mask"] = None   # invalide le cache de vignette

                if not new_path:
                    _state["pil_orig"] = None
                    status_lbl.config(text="Aucune image définie", fg="#3a5a3a")
                    _show_no_image()
                else:
                    import os as _os
                    if not _os.path.isfile(new_path):
                        _state["pil_orig"] = None
                        status_lbl.config(text=f"⚠️ Fichier introuvable : {new_path}", fg="#aa4444")
                        _show_no_image("Fichier introuvable")
                    else:
                        try:
                            from PIL import Image as _PI
                            img = _PI.open(new_path).convert("RGB")
                            _state["pil_orig"] = img
                            import os.path as _osp
                            status_lbl.config(
                                text=_osp.basename(new_path), fg="#4a7a4a"
                            )
                            _render_image()
                        except ImportError:
                            status_lbl.config(
                                text="⚠️ Pillow requis : pip install pillow", fg="#aa8844"
                            )
                            _show_no_image("pip install pillow pour afficher les images")
                        except Exception as e:
                            status_lbl.config(text=f"⚠️ {e}", fg="#aa4444")
                            _show_no_image("Erreur de chargement")

            win.after(1500, _refresh)

        # ── Re-rendu lors du redimensionnement (debounce 150 ms) ─────────────
        _resize_job = [None]

        def _on_resize(event):
            if event.widget is not win:
                return
            if _resize_job[0]:
                win.after_cancel(_resize_job[0])
            _resize_job[0] = win.after(150, _on_resize_debounced)

        def _on_resize_debounced():
            _state["_vignette_mask"] = None
            if _state["pil_orig"] is not None:
                _render_image()
            else:
                _show_no_image()

        win.bind("<Configure>", _on_resize)

        # ── Lancement initial ─────────────────────────────────────────────────
        win.after(100, _refresh)   # 1er appel après que le canvas est rendu
        
        win.after(20, win.deiconify)
        win.after(40, win.lift)

    def open_scene_editor(self):
        """Fenêtre d'édition du contexte de scène."""
        win = tk.Toplevel(self.root)
        win.title("🗺️ Contexte de Scène")
        win.geometry("680x620")
        win.configure(bg="#0d1117")
        win.grab_set()
        self._track_window("modal_scene_editor", win)
        win.protocol("WM_DELETE_WINDOW", lambda: _ghost_close(win, self.root))

        scene = get_scene()

        # ── En-tête ──
        hdr = tk.Frame(win, bg="#0d2010")
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="🗺️  Contexte de la Scène Actuelle", bg="#0d2010", fg="#81c784",
                 font=("Arial", 13, "bold")).pack(side=tk.LEFT, padx=14, pady=10)

        # Bouton IA pour générer la scène
        tk.Button(hdr, text="🪄 Générer par IA", bg="#1a3a5c", fg="#64b5f6",
                  font=("Arial", 9, "bold"), relief="flat", cursor="hand2",
                  command=lambda: generate_scene_ai()).pack(side=tk.RIGHT, padx=14, pady=10)

        tk.Label(hdr, text="Injecté dans le contexte de tous les agents",
                 bg="#0d2010", fg="#555", font=("Arial", 8)).pack(side=tk.RIGHT, padx=14)

        # FIX SEGFAULT : pas de Canvas+<Configure> dans Toplevel — frame simple
        inner = tk.Frame(win, bg="#0d1117")
        inner.pack(fill=tk.BOTH, expand=True, padx=4)

        def lbl(text):
            tk.Label(inner, text=text, bg="#0d1117", fg="#81c784",
                     font=("Arial", 9, "bold")).pack(anchor="w", padx=12, pady=(8, 1))

        def entry_field(default=""):
            e = tk.Entry(inner, bg="#161b22", fg="white", font=("Consolas", 11),
                         insertbackground="white", relief="flat")
            e.pack(fill=tk.X, padx=12, ipady=4)
            e.insert(0, default)
            return e

        def text_field(default="", height=3):
            t = tk.Text(inner, height=height, bg="#161b22", fg="white", font=("Consolas", 10),
                        insertbackground="white", relief="flat", wrap=tk.WORD)
            t.pack(fill=tk.X, padx=12)
            t.insert("1.0", default)
            return t

        def list_field(items, label_text):
            lbl(label_text)
            t = tk.Text(inner, height=3, bg="#161b22", fg="#a5d6a7", font=("Consolas", 10),
                        insertbackground="white", relief="flat", wrap=tk.WORD)
            t.pack(fill=tk.X, padx=12)
            t.insert("1.0", "\n".join(items))
            return t

        # ── Champs ──
        lbl("📍 Lieu / Endroit précis")
        f_lieu = entry_field(scene.get("lieu", ""))

        row2 = tk.Frame(inner, bg="#0d1117")
        row2.pack(fill=tk.X, padx=12, pady=(8, 0))
        tk.Label(row2, text="🕐 Heure", bg="#0d1117", fg="#81c784", font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        f_heure = tk.Entry(row2, bg="#161b22", fg="white", font=("Consolas", 11),
                           insertbackground="white", relief="flat", width=14)
        f_heure.pack(side=tk.LEFT, padx=(6, 20), ipady=3)
        f_heure.insert(0, scene.get("heure", ""))
        tk.Label(row2, text="Météo / Lumière", bg="#0d1117", fg="#81c784", font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        f_meteo = tk.Entry(row2, bg="#161b22", fg="white", font=("Consolas", 11),
                           insertbackground="white", relief="flat")
        f_meteo.pack(side=tk.LEFT, padx=6, fill=tk.X, expand=True, ipady=3)
        f_meteo.insert(0, scene.get("meteo", ""))

        lbl("Ambiance / Atmosphère")
        f_ambiance = text_field(scene.get("ambiance", ""), height=2)

        f_npcs   = list_field(scene.get("npcs_presents",[]),   "PNJs présents (un par ligne)")
        f_objets = list_field(scene.get("objets_notables",[]), "Elements notables (un par ligne)")

        lbl("Menaces / Tension en cours")
        f_menaces = text_field(scene.get("menaces", ""), height=2)

        lbl("Notes MJ (non injectees aux agents)")
        f_notes = text_field(scene.get("notes_mj", ""), height=2)

        # ── Section Image du lieu ───────────────────────────────────────────
        tk.Frame(inner, bg="#0d1117", height=1).pack(fill=tk.X, padx=12, pady=(10, 0))
        img_hdr = tk.Frame(inner, bg="#0d1117")
        img_hdr.pack(fill=tk.X, padx=12, pady=(6, 2))
        tk.Label(img_hdr, text="📸 Image du lieu", bg="#0d1117", fg="#81c784",
                 font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        tk.Label(img_hdr, text="(PNG / JPG / WEBP — visible par les agents Gemini)",
                 bg="#0d1117", fg="#444455", font=("Arial", 7, "italic")).pack(side=tk.LEFT, padx=6)

        img_row = tk.Frame(inner, bg="#0d1117")
        img_row.pack(fill=tk.X, padx=12, pady=(0, 4))

        # Variable pour stocker le chemin
        _img_path_var = tk.StringVar(value=scene.get("location_image", ""))

        img_entry = tk.Entry(img_row, textvariable=_img_path_var, bg="#161b22", fg="#a5d6a7",
                             font=("Consolas", 9), insertbackground="white", relief="flat")
        img_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)

        def _pick_image():
            import tkinter.filedialog as _fd
            path = _fd.askopenfilename(
                parent=win,
                title="Choisir une image du lieu",
                filetypes=[
                    ("Images", "*.png *.jpg *.jpeg *.webp *.gif"),
                    ("PNG", "*.png"), ("JPEG", "*.jpg *.jpeg"),
                    ("WebP", "*.webp"), ("Tous", "*.*"),
                ]
            )
            if path:
                _img_path_var.set(path)
                _update_thumb()

        def _clear_image():
            _img_path_var.set("")
            _update_thumb()

        tk.Button(img_row, text="📂", bg="#1a2a1a", fg="#81c784",
                  font=("TkDefaultFont", 9), relief="flat", padx=6,
                  command=_pick_image).pack(side=tk.LEFT, padx=(4, 2))
        tk.Button(img_row, text="✕", bg="#2a1a1a", fg="#e57373",
                  font=("Arial", 9), relief="flat", padx=4,
                  command=_clear_image).pack(side=tk.LEFT, padx=(0, 0))

        # Thumbnail preview
        _thumb_label = tk.Label(inner, bg="#0d1117", text="", anchor="w")
        _thumb_label.pack(fill=tk.X, padx=12, pady=(2, 4))

        def _update_thumb(*_):
            path = _img_path_var.get().strip()
            if not path or not os.path.isfile(path):
                _thumb_label.config(image="", text="" if not path else "⚠️ Fichier introuvable",
                                    fg="#e57373")
                _thumb_label._img_ref = None
                return
            try:
                from PIL import Image as _PILImage, ImageTk as _PILTk
                img = _PILImage.open(path)
                img.thumbnail((220, 110), getattr(_PILImage, 'Resampling', _PILImage).LANCZOS)
                photo = _PILTk.PhotoImage(img)
                _thumb_label.config(image=photo, text="")
                _thumb_label._img_ref = photo   # Empêche le GC de détruire l'image
            except ImportError:
                # Pillow absent : afficher juste le nom du fichier
                fname = os.path.basename(path)
                _thumb_label.config(image="", text=f"✅ {fname}", fg="#81c784")
                _thumb_label._img_ref = None
            except Exception as e:
                _thumb_label.config(image="", text=f"⚠️ Aperçu impossible : {e}", fg="#e57373")
                _thumb_label._img_ref = None

        _img_path_var.trace_add("write", _update_thumb)
        _update_thumb()  # Affiche la vignette actuelle au chargement

        def generate_scene_ai():
            import tkinter.simpledialog as sd
            
            location_query = sd.askstring(
                "Générer une Scène par IA",
                "Où sommes-nous et y a-t-il des détails spécifiques ?\n\n(L'IA cherchera dans les livres et aventures, puis remplira la scène)",
                parent=win
            )
            
            if not location_query:
                return
                
            self.msg_queue.put({
                "sender": "Système",
                "text": f"⏳ Recherche locale en cours pour : {location_query}...",
                "color": "#64b5f6"
            })
            
            def _ai_worker():
                try:
                    # 1. Recherche basique silencieuse dans les JSON d'aventure et de lore
                    query_lower = location_query.lower()
                    snippets = []
                    
                    def _traverse(node, depth=0):
                        if depth > 15: return  # Sécurité contre les arbres JSON trop profonds
                        
                        if isinstance(node, dict):
                            text = ""
                            name = str(node.get("name", ""))
                            
                            if "entries" in node:
                                for entry in node["entries"]:
                                    if isinstance(entry, str):
                                        text += entry + "\n"
                            
                            if query_lower in name.lower() or query_lower in text.lower():
                                if text.strip():
                                    snippets.append(f"[{name}] {text.strip()}")
                                    
                            for v in node.values():
                                _traverse(v, depth + 1)
                                
                        elif isinstance(node, list):
                            for item in node:
                                _traverse(item, depth + 1)

                    for directory in ["adventure", "book"]:
                        if not os.path.exists(directory): continue
                        for filename in os.listdir(directory):
                            if filename.endswith(".json"):
                                filepath = os.path.join(directory, filename)
                                try:
                                    with open(filepath, "r", encoding="utf-8") as f:
                                        data = json.load(f)
                                        _traverse(data)
                                except Exception:
                                    pass
                                    
                    context_text = "\n\n".join(snippets[:8])
                    if not context_text:
                        context_text = "Aucune information spécifique trouvée dans les livres. Invente la scène en te basant sur tes connaissances D&D 5e générales."

                    # Notifie que la recherche locale est finie
                    self.msg_queue.put({
                        "sender": "Système",
                        "text": "✅ Recherche locale terminée. Création de la scène par l'IA...",
                        "color": "#64b5f6"
                    })

                    # 2. Appel au LLM (Chroniqueur)
                    import autogen as _ag
                    from app_config import get_chronicler_config
                    from llm_config import build_llm_config, _default_model
                    
                    sys_prompt = """Tu es le Chroniqueur IA d'une campagne D&D 5e.
On te donne une description ou un nom de lieu, et un contexte extrait des livres d'aventure.
Génère les détails de la scène en t'imprégnant de l'ambiance du texte source.

RÈGLES ABSOLUES :
1. Réponds UNIQUEMENT avec du JSON valide.
2. Ne mets aucun texte en dehors des accolades du JSON.

FORMAT DE RÉPONSE :
{
  "lieu": "Nom précis du lieu",
  "heure": "Matin / Après-midi / Soir / Nuit",
  "meteo": "Météo et lumière (ex: Brume légère, pénombre)",
  "ambiance": "1 à 2 phrases décrivant l'atmosphère",
  "npcs_presents":["PNJ 1", "PNJ 2"],
  "objets_notables":["Objet 1", "Objet 2"],
  "menaces": "Menace immédiate ou 'Aucune'",
  "notes_mj": "Secrets ou notes issus du contexte"
}"""

                    user_prompt = f"Lieu recherché : {location_query}\n\nContexte extrait des livres :\n{context_text}"
                    
                    chron_cfg = get_chronicler_config()
                    llm_cfg   = build_llm_config(
                        chron_cfg.get("model", _default_model),
                        temperature=0.4,
                    )
                    
                    # Injection propre de la configuration
                    client_kwargs = {k: v for k, v in llm_cfg.items() if k not in ("functions", "tools")}
                    client = _ag.OpenAIWrapper(**client_kwargs)
                    
                    response = client.create(messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user",   "content": user_prompt},
                    ])

                    raw_text = (response.choices[0].message.content or "").strip()
                    
                    # Extraction robuste du JSON
                    match = re.search(r'\{.*\}', raw_text, re.DOTALL)
                    if match:
                        clean = match.group(0)
                    else:
                        raise ValueError("Le LLM n'a pas renvoyé de JSON valide.")
                    
                    data = json.loads(clean)
                    
                    # 3. Mise à jour de l'UI
                    def _update_ui():
                        try:
                            f_lieu.delete(0, tk.END)
                            f_heure.delete(0, tk.END)
                            f_meteo.delete(0, tk.END)
                            f_ambiance.delete("1.0", tk.END)
                            f_npcs.delete("1.0", tk.END)
                            f_objets.delete("1.0", tk.END)
                            f_menaces.delete("1.0", tk.END)
                            f_notes.delete("1.0", tk.END)
                            
                            def format_list(val):
                                if isinstance(val, list): return "\n".join(str(x) for x in val)
                                return str(val)
                            
                            f_lieu.insert(0, str(data.get("lieu", "")))
                            f_heure.insert(0, str(data.get("heure", "")))
                            f_meteo.insert(0, str(data.get("meteo", "")))
                            f_ambiance.insert("1.0", str(data.get("ambiance", "")))
                            
                            f_npcs.insert("1.0", format_list(data.get("npcs_presents",[])))
                            f_objets.insert("1.0", format_list(data.get("objets_notables",[])))
                            f_menaces.insert("1.0", str(data.get("menaces", "")))
                            f_notes.insert("1.0", str(data.get("notes_mj", "")))
                            
                            self.msg_queue.put({
                                "sender": "Système",
                                "text": "✅ Contexte de la scène généré ! N'oubliez pas de cliquer sur [Sauvegarder la scène].",
                                "color": "#81c784"
                            })
                        except Exception as inner_e:
                            self.msg_queue.put({
                                "sender": "⚠️ Système",
                                "text": f"Erreur lors de l'insertion dans l'interface : {inner_e}",
                                "color": "#e57373"
                            })

                    win.after(0, _update_ui)

                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    self.msg_queue.put({
                        "sender": "⚠️ Système",
                        "text": f"Erreur lors de la génération IA : {e}",
                        "color": "#e57373"
                    })

            threading.Thread(target=_ai_worker, daemon=True).start()

        # ── Boutons ──
        btn_frame = tk.Frame(win, bg="#0d1117")
        btn_frame.pack(fill=tk.X, padx=16, pady=12)

        def parse_list(widget):
            return[l.strip() for l in widget.get("1.0", tk.END).strip().splitlines() if l.strip()]

        def save_and_close():
            old_image = scene.get("location_image", "")
            new_image = _img_path_var.get().strip()
            new_scene = {
                "lieu":            f_lieu.get().strip(),
                "heure":           f_heure.get().strip(),
                "meteo":           f_meteo.get().strip(),
                "ambiance":        f_ambiance.get("1.0", tk.END).strip(),
                "npcs_presents":   parse_list(f_npcs),
                "objets_notables": parse_list(f_objets),
                "menaces":         f_menaces.get("1.0", tk.END).strip(),
                "notes_mj":        f_notes.get("1.0", tk.END).strip(),
                "location_image":  new_image,
            }
            save_scene(new_scene)
            self._refresh_scene_widget()
            self.msg_queue.put({
                "sender": "Système",
                "text": f"🗺️ Scène mise à jour : {new_scene['lieu']}",
                "color": "#81c784"
            })
            # Si l'image a changé et qu'il y en a une, proposer l'envoi automatique
            if new_image and new_image != old_image and getattr(self, '_agents', None):
                self.msg_queue.put({
                    "sender": "🖼️ Système",
                    "text": "📸 Nouvelle image de lieu détectée — envoi aux agents multimodaux...",
                    "color": "#81c784"
                })
                self.root.after(500, self._broadcast_location_image)
            _ghost_close(win, self.root)

        def reset_scene():
            from state_manager import DEFAULT_SCENE
            save_scene(DEFAULT_SCENE.copy())
            self._refresh_scene_widget()
            _ghost_close(win, self.root)

        tk.Button(btn_frame, text="✅ Sauvegarder la scène", bg="#1a4a1a", fg="#81c784",
                  font=("Arial", 11, "bold"), relief="flat",
                  command=save_and_close).pack(side=tk.LEFT)
        tk.Button(btn_frame, text="📸 Montrer le lieu aux agents", bg="#0d2030", fg="#64b5f6",
                  font=("Arial", 9, "bold"), relief="flat", padx=8,
                  command=lambda: (save_and_close(), self.root.after(300, self._broadcast_location_image))
                  ).pack(side=tk.LEFT, padx=8)
        tk.Button(btn_frame, text="🔄 Réinitialiser", bg="#2a2a2a", fg="#888",
                  font=("Arial", 9), relief="flat",
                  command=reset_scene).pack(side=tk.LEFT, padx=10)
        tk.Button(btn_frame, text="Annuler", bg="#2a2a2a", fg="#888",
                  font=("Arial", 9), relief="flat",
                  command=lambda: _ghost_close(win, self.root)).pack(side=tk.RIGHT)