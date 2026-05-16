"""
npc_sheet_top_mixins.py — Mixins pour la fenêtre de fiche de monstre (Image, LLM, Recherche).
"""

import os
import threading
import base64 as _b64
import tkinter as tk
from tkinter import filedialog, messagebox

from npc_utils import (
    load_npc_image_bytes, 
    save_npc_image_bytes, 
    _npc_image_path, 
    speak_as_npc, 
    _fmt_type, 
    _fmt_cr
)
from npc_bestiary_manager import search_monsters, get_monster


class MonsterSheetImageSpeakMixin:
    """Mixin regroupant la gestion de l'image du PNJ et de la fonctionnalité LLM 'Parler en tant que'."""

    # ── Panneau image NPC ─────────────────────────────────────────────────────

    def _build_image_panel(self, parent):
        """Zone image NPC : charger, afficher, envoyer aux agents multimodaux."""
        frame = tk.Frame(parent, bg=self.BG2, pady=4)
        frame.pack(fill=tk.X, padx=0)

        # -- Miniature
        self._img_label = tk.Label(frame, bg=self.BG2, cursor="hand2",
                                   text="📷 Aucune image",
                                   fg=self.FG_DIM, font=("Consolas", 8))
        self._img_label.pack(side=tk.LEFT, padx=8)
        self._img_label.bind("<Button-1>", lambda e: self._browse_image())
        self._refresh_image_thumbnail()

        btn_col = tk.Frame(frame, bg=self.BG2)
        btn_col.pack(side=tk.LEFT, fill=tk.Y, padx=4)

        def _btn(txt, bg, fg, cmd):
            tk.Button(btn_col, text=txt, bg=bg, fg=fg, relief="flat",
                      font=("Arial", 8, "bold"), padx=6, pady=2,
                      command=cmd).pack(fill=tk.X, pady=1)

        _btn("📂 Charger image", "#1a2030", self.BLUE,  self._browse_image)
        _btn("✕ Supprimer",     "#200a0a", "#e57373",   self._clear_image)
        _btn("📡 Envoyer aux agents", "#0a2010", self.GREEN, self._send_image_to_agents)

        tk.Frame(parent, bg="#2a2a3a", height=1).pack(fill=tk.X)

    def _refresh_image_thumbnail(self):
        """Affiche ou met à jour la miniature dans l'image label."""
        lbl = getattr(self, "_img_label", None)
        if lbl is None:
            return
        data = self._img_bytes
        if not data:
            lbl.config(image="", text="📷 Aucune image", fg=self.FG_DIM, width=10)
            self._img_tk = None
            return
        try:
            from PIL import Image, ImageTk
            import io
            img = Image.open(io.BytesIO(data)).convert("RGBA")
            img.thumbnail((100, 100))
            self._img_tk = ImageTk.PhotoImage(img)
            lbl.config(image=self._img_tk, text="", width=100, height=100)
        except Exception:
            lbl.config(image="", text=f"🖼 image ({len(data)//1024}KB)", fg=self.GREEN)

    def _browse_image(self):
        """Ouvre un sélecteur de fichier pour charger une image NPC."""
        path = filedialog.askopenfilename(
            parent=self.win,
            title=f"Image pour {self.npc_name}",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.gif"), ("Tous", "*.*")],
        )
        if not path:
            return
        try:
            from PIL import Image
            import io
            img = Image.open(path).convert("RGBA")
            img.thumbnail((512, 512))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            self._img_bytes = buf.getvalue()
            save_npc_image_bytes(self.npc_name, self._img_bytes)
            self._refresh_image_thumbnail()
        except Exception as e:
            messagebox.showerror("Image", f"Impossible de charger l'image : {e}",
                                 parent=self.win)

    def _clear_image(self):
        self._img_bytes = None
        path = _npc_image_path(self.npc_name)
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        self._refresh_image_thumbnail()

    def _send_image_to_agents(self):
        """Envoie l'image NPC à tous les agents multimodaux (Gemini)."""
        if not self._img_bytes:
            messagebox.showinfo("Image", "Aucune image définie pour ce PNJ.",
                                parent=self.win)
            return
        if not self.chat_queue:
            return
        b64      = _b64.b64encode(self._img_bytes).decode()
        npc_name = self.npc_name
        msg_queue = self.chat_queue
        audio_q   = self.audio_queue

        self.chat_queue.put({
            "sender": "🖼️ Système",
            "text":   f"📸 Image de {npc_name} envoyée aux agents multimodaux.",
            "color":  "#81c784",
        })

        def _run():
            try:
                import autogen as _ag
                from app_config import get_agent_config
                from llm_config import build_llm_config

                monster = self._current_monster
                m_type  = _fmt_type(monster.get("type", "personnage")) if monster else "personnage"

                # On envoie à tous les agents Gemini (multimodaux)
                for agent_name in ["Kaelen", "Elara", "Thorne", "Lyra"]:
                    acfg  = get_agent_config(agent_name)
                    model = acfg.get("model", "")
                    if not model.startswith("gemini-"):
                        continue
                    llm_cfg = build_llm_config(model, temperature=acfg.get("temperature", 0.7))
                    client  = _ag.OpenAIWrapper(config_list=llm_cfg["config_list"])

                    prompt = (
                        f"[IMAGE NPC — CONTEXTE PRIVÉ]\n"
                        f"Le MJ te montre une illustration de {npc_name} ({m_type}).\n"
                        f"En 1-2 phrases courtes de roleplay, décris la première impression "
                        f"que {agent_name} ressent en apercevant ce personnage. "
                        f"Reste dans le personnage. Ne pose pas de question."
                    )
                    try:
                        resp = client.create(messages=[{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {
                                    "url": f"data:image/png;base64,{b64}"
                                }},
                            ]
                        }])
                        text = (resp.choices[0].message.content or "").strip()
                        if text:
                            msg_queue.put({"sender": agent_name, "text": text,
                                           "color": "#e0e0e0"})
                            if audio_q:
                                audio_q.put((text, agent_name))
                    except Exception as e:
                        msg_queue.put({"sender": "⚠ Image", "text": str(e),
                                       "color": "#F44336"})
            except Exception as e:
                msg_queue.put({"sender": "⚠ Image", "text": str(e), "color": "#F44336"})

        threading.Thread(target=_run, daemon=True).start()

    # ── Panneau "Parler en tant que" ──────────────────────────────────────────

    def _build_speak_as_panel(self, parent, monster: dict | None):
        """Construit le panneau dans un container donné (legacy compat — délègue)."""
        self._build_speak_as_content(parent, monster)

    def _build_speak_as_content(self, container: tk.Frame, monster: dict | None):
        """Construit ou reconstruit le contenu du panneau 'Parler en tant que'."""
        for w in container.winfo_children():
            w.destroy()

        container.configure(bg="#0e1a10")

        tk.Label(container, text=f"Parler en tant que {self.npc_name} :",
                 bg="#0e1a10", fg="#a5d6a7", font=("Arial", 9, "bold")
                 ).pack(anchor="w", padx=10, pady=(4, 2))

        row = tk.Frame(container, bg="#0e1a10")
        row.pack(fill=tk.X, padx=10, pady=(0, 6))

        self._speak_var = tk.StringVar()
        entry = tk.Entry(row, textvariable=self._speak_var,
                         bg="#0d1f0d", fg="white", font=("Consolas", 10),
                         insertbackground="white", relief="flat")
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5, padx=(0, 6))
        entry.insert(0, "Que dites-vous ?")
        entry.bind("<FocusIn>",
                   lambda e: (self._speak_var.get() == "Que dites-vous ?"
                              and (self._speak_var.set(""), None)))
        entry.bind("<Return>", lambda e, m=monster: self._do_speak(m))

        self._scene_ctx_var = tk.BooleanVar(value=True)
        tk.Checkbutton(row, text="Scène", variable=self._scene_ctx_var,
                       bg="#0e1a10", fg="#7aad7a", selectcolor="#0e1a10",
                       activebackground="#0e1a10", font=("Arial", 8)
                       ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(row, text="Generer", bg="#1a3a1a", fg="#81c784",
                  font=("Arial", 9, "bold"), relief="flat", padx=10, pady=4,
                  command=lambda m=monster: self._do_speak(m)
                  ).pack(side=tk.LEFT)

        if monster:
            sub = (f"{_fmt_type(monster.get('type','?'))}  "
                   f"FP {_fmt_cr(monster.get('cr','?'))}  "
                   f"Align.: {' '.join(monster.get('alignment', []))}")
            tk.Label(container, text=sub, bg="#0e1a10", fg="#3a5a3a",
                     font=("Consolas", 7)).pack(anchor="w", padx=10, pady=(0, 4))

        tk.Frame(container, bg="#2a3a2a", height=1).pack(fill=tk.X)

    def _refresh_speak_panel(self, monster: dict | None):
        """Met à jour le panneau 'Parler en tant que' quand le monstre change."""
        if hasattr(self, "_speak_frame") and self._speak_frame.winfo_exists():
            self._build_speak_as_content(self._speak_frame, monster)

    def _do_speak(self, monster: dict | None):
        """Déclenche la génération de réplique NPC via LLM."""
        if not self.chat_queue:
            return
        prompt = self._speak_var.get().strip()
        if not prompt or prompt == "Que dites-vous ?":
            prompt = ""

        scene = ""
        if self._scene_ctx_var.get() and self.get_scene_fn:
            try:
                scene = self.get_scene_fn()
            except Exception:
                pass

        self.chat_queue.put({
            "sender": "🎭 Système",
            "text":   f"{self.npc_name} prend la parole…",
            "color":  "#555566",
        })
        speak_as_npc(
            self.npc_name, monster, prompt,
            self.chat_queue, self.audio_queue,
            color=self.npc_color, scene_context=scene,
        )


class MonsterSheetSearchMixin:
    """Mixin gérant la barre de recherche et d'autocomplétion des monstres."""

    def _on_search_key(self, event=None):
        q = self._search_var.get().strip()
        suggestions = search_monsters(q, 10)
        self._show_suggestions(suggestions)

    def _on_search_confirm(self, event=None):
        q = self._search_var.get().strip()
        if not q:
            return
        # Essai direct
        m = get_monster(q)
        if m:
            self._hide_suggestions()
            self._show_monster(m["name"])
        else:
            # Prend la première suggestion
            suggestions = search_monsters(q, 1)
            if suggestions:
                self._search_var.set(suggestions[0])
                self._hide_suggestions()
                self._show_monster(suggestions[0])

    def _show_suggestions(self, names: list[str]):
        self._hide_suggestions()
        if not names:
            return
        x = self._canvas.winfo_x()
        y = 42  # sous la barre de recherche

        self._suggest_frame.place(x=10, y=y, width=280)
        self._suggest_frame.lift()

        for name in names:
            lbl = tk.Label(self._suggest_frame, text=name, bg=self.BG2,
                           fg=self.FG, font=("Consolas", 9),
                           anchor="w", padx=8, pady=3, cursor="hand2")
            lbl.pack(fill=tk.X)
            lbl.bind("<Button-1>", lambda e, n=name: self._pick_suggestion(n))
            lbl.bind("<Enter>",    lambda e, l=lbl: l.config(bg=self.BG3))
            lbl.bind("<Leave>",    lambda e, l=lbl: l.config(bg=self.BG2))
            self._suggest_labels.append(lbl)
        self._suggest_visible = True

    def _hide_suggestions(self):
        for lbl in self._suggest_labels:
            lbl.destroy()
        self._suggest_labels.clear()
        self._suggest_frame.place_forget()
        self._suggest_visible = False

    def _pick_suggestion(self, name: str):
        self._search_var.set(name)
        self._hide_suggestions()
        self._show_monster(name)

    def _confirm_selection(self):
        """Valide le monstre actuellement affiché et appelle le callback."""
        name = self._search_var.get().strip()
        if name and get_monster(name):
            if self.on_select_callback:
                self.on_select_callback(name)
            self.win.title(f"📋 {self.npc_name} — {name}")