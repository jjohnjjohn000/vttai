"""
chat_mixin.py — ChatMixin : gestion du panneau de chat et de l'audio.

Contient :
  - display_message, append_message
  - audio_worker, process_queue
  - _append_relay_button, _remove_tag_line, _append_spell_confirm
  - _on_chat_click, _on_chat_motion
  - show_context_menu, delete_selected_message, edit_selected_message
"""

import queue
import tkinter as tk
from tkinter import scrolledtext

from voice_interface import play_voice


class ChatMixin:
    """Mixin pour DnDApp — panneau de chat, file audio, interactions utilisateur."""

    _PLAYER_NAMES = {"Kaelen", "Elara", "Thorne", "Lyra"}

    # ─── Affichage simple (legacy, utilisé par certains anciens appels) ────────

    def display_message(self, sender, text, color="#e0e0e0"):
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.insert(tk.END, f"[{sender}]\n", "sender")
        self.chat_display.insert(tk.END, f"{text}\n\n", "text")
        self.chat_display.tag_config("sender", foreground="#ffcc00", font=("Consolas", 11, "bold"))
        self.chat_display.tag_config("text", foreground=color)
        self.chat_display.see(tk.END)
        self.chat_display.config(state=tk.DISABLED)

    # ─── Worker audio (thread daemon) ─────────────────────────────────────────

    def audio_worker(self):
        while True:
            try:
                text, name = self.audio_queue.get(timeout=1.0)
                face = self.face_windows.get(name)
                if face:
                    face.set_talking(True)
                try:
                    play_voice(text, name)
                except Exception as e:
                    print(f"Erreur audio de {name}: {e}")
                finally:
                    if face:
                        face.set_talking(False)
                self.audio_queue.task_done()
            except queue.Empty:
                continue

    # ─── Pompe de messages (appelée par root.after) ───────────────────────────

    def process_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                action = msg.get("action")
                if action == "relay_button":
                    self._append_relay_button(msg["char_name"], msg["reply_text"])
                elif action == "spell_confirm":
                    self._append_spell_confirm(
                        msg["char_name"], msg["spell_name"],
                        msg["spell_level"], msg["target"],
                        msg["resume_callback"]
                    )
                elif action == "action_confirm":
                    self._append_action_confirm(
                        msg["char_name"], msg["intention"],
                        msg["regle"],     msg["cible"],
                        msg["resume_callback"]
                    )
                elif action == "set_llm_running":
                    running = msg["value"]
                    self._llm_running = running
                    active = running and not self._waiting_for_mj
                    self.btn_stop.config(state=tk.NORMAL if active else tk.DISABLED,
                                         bg="#cc0000" if active else "#880000")
                elif action == "set_waiting_for_mj":
                    waiting = msg["value"]
                    self._waiting_for_mj = waiting
                    active = self._llm_running and not waiting
                    self.btn_stop.config(state=tk.NORMAL if active else tk.DISABLED,
                                         bg="#cc0000" if active else "#880000")
                else:
                    self.append_message(msg["sender"], msg["text"], msg["color"])
        except queue.Empty:
            pass
        self.root.after(100, self.process_queue)

    # ─── Ajout de messages taggés ─────────────────────────────────────────────

    def append_message(self, sender: str, text: str, color: str):
        """Ajoute un message taggé dans le chat (pour pouvoir l'éditer/supprimer)."""
        self.msg_counter += 1
        msg_id   = self.msg_counter
        tag_name = f"msg_{msg_id}"
        tag_sender = f"sender_{msg_id}"

        self.chat_display.config(state=tk.NORMAL)

        self.chat_display.insert(tk.END, "\n[", tag_name)
        self.chat_display.insert(tk.END, sender, tag_sender)
        self.chat_display.insert(tk.END, f"]: {text}\n", tag_name)

        self.chat_display.tag_config(tag_name,   foreground=color)
        self.chat_display.tag_config(tag_sender, foreground=color,
                                     font=("Consolas", 11, "bold"),
                                     underline=False)

        self.chat_display.config(state=tk.DISABLED)
        self.chat_display.see(tk.END)

        self.messages_index.append({
            "id":     msg_id,
            "sender": sender,
            "text":   text,
            "color":  color,
            "tag":    tag_name,
        })

    # ─── Bouton relay (message privé partageable au groupe) ───────────────────

    def _append_relay_button(self, char_name: str, reply_text: str):
        """Insère un bouton-texte cliquable (tag) dans le chat — sans window_create."""
        color = self.CHAR_COLORS.get(char_name, "#aaaaaa")
        self.msg_counter += 1
        tag_relay   = f"relay_{self.msg_counter}"
        tag_dismiss = f"dismiss_{self.msg_counter}"

        self.chat_display.config(state=tk.NORMAL)

        self.chat_display.insert(tk.END, f"\n  💬 ", "relay_hint")
        self.chat_display.insert(tk.END, f"[📢 {char_name} partage au groupe]", tag_relay)
        self.chat_display.insert(tk.END, "  ")
        self.chat_display.insert(tk.END, "[✕]", tag_dismiss)
        self.chat_display.insert(tk.END, "\n")

        self.chat_display.tag_config("relay_hint", foreground="#555577",
                                     font=("Arial", 8, "italic"))
        self.chat_display.tag_config(tag_relay, foreground=color,
                                     font=("Arial", 9, "bold"), underline=True)
        self.chat_display.tag_config(tag_dismiss, foreground="#444466",
                                     font=("Arial", 8))

        def _do_relay(event=None):
            self._remove_tag_line(tag_relay)
            self._remove_tag_line(tag_dismiss)
            self.append_message(char_name, reply_text, color)
            self.audio_queue.put((reply_text, char_name))
            relayed = f"[{char_name}, s'adressant au groupe] {reply_text}"
            if self._llm_running and not self._waiting_for_mj:
                self._pending_interrupt_input = relayed
                self._pending_interrupt_display = None
                self._inject_stop()
            else:
                self.user_input = relayed
                self.input_event.set()

        def _do_dismiss(event=None):
            self._remove_tag_line(tag_relay)
            self._remove_tag_line(tag_dismiss)

        self.chat_display.tag_bind(tag_relay,   "<Button-1>", _do_relay)
        self.chat_display.tag_bind(tag_dismiss, "<Button-1>", _do_dismiss)
        self.chat_display.tag_bind(tag_relay,   "<Enter>",
                                   lambda e: self.chat_display.config(cursor="hand2"))
        self.chat_display.tag_bind(tag_relay,   "<Leave>",
                                   lambda e: self.chat_display.config(cursor=""))
        self.chat_display.tag_bind(tag_dismiss, "<Enter>",
                                   lambda e: self.chat_display.config(cursor="hand2"))
        self.chat_display.tag_bind(tag_dismiss, "<Leave>",
                                   lambda e: self.chat_display.config(cursor=""))

        self.chat_display.config(state=tk.DISABLED)
        self.chat_display.see(tk.END)

    def _remove_tag_line(self, tag_name: str):
        """Supprime la ligne entière d'un tag dans le chat."""
        try:
            self.chat_display.config(state=tk.NORMAL)
            ranges = self.chat_display.tag_ranges(tag_name)
            if ranges:
                line_start = self.chat_display.index(f"{ranges[0]} linestart")
                line_end   = self.chat_display.index(f"{ranges[1]} lineend +1c")
                self.chat_display.delete(line_start, line_end)
            self.chat_display.config(state=tk.DISABLED)
        except Exception:
            pass

    # ─── Widget de confirmation de sort inline ────────────────────────────────

    def _append_spell_confirm(self, char_name: str, spell_name: str,
                               spell_level: int, target: str, resume_callback):
        """
        Affiche un widget de confirmation de sort dans le chat.
        Le MJ peut ajuster le niveau et confirmer/refuser.
        resume_callback(confirmed: bool, actual_level: int) est appelé depuis
        le thread principal via msg_queue → process_queue (thread-safe).
        """
        from state_manager import use_spell_slot
        color = self.CHAR_COLORS.get(char_name, "#aaaaaa")
        self.msg_counter += 1
        n = self.msg_counter

        tag_header  = f"spell_hdr_{n}"
        tag_confirm = f"spell_ok_{n}"
        tag_deny    = f"spell_no_{n}"

        level_var = tk.IntVar(value=spell_level)

        self.chat_display.config(state=tk.NORMAL)

        self.chat_display.insert(tk.END, f"\n✨ {char_name} lance ", "spell_hint")
        self.chat_display.insert(tk.END, spell_name, tag_header)
        cible_txt = f" → {target}" if target and target.lower() not in ("?", "-", "") else ""
        self.chat_display.insert(tk.END, f"{cible_txt}\n", "spell_hint")

        frame = tk.Frame(self.chat_display, bg="#1a1a2e", pady=3, padx=6)

        tk.Label(frame, text="Niveau :", bg="#1a1a2e", fg="#aaaaaa",
                 font=("Arial", 8)).pack(side=tk.LEFT, padx=(0, 4))

        spx = tk.Spinbox(frame, from_=1, to=9, width=2, textvariable=level_var,
                         bg="#2a2a3e", fg=color, font=("Consolas", 9, "bold"),
                         buttonbackground="#2a2a3e", relief="flat",
                         highlightthickness=1, highlightcolor=color)
        spx.pack(side=tk.LEFT, padx=(0, 8))

        confirmed = [False]

        def _confirm():
            confirmed[0] = True
            lvl = level_var.get()
            result = use_spell_slot(char_name, str(lvl))
            self.append_message("✨ Sort", f"{char_name} — {spell_name} niv.{lvl}{cible_txt} → {result}", color)
            frame.destroy()
            _remove_spell_lines()
            resume_callback(True, lvl)

        def _deny():
            self.append_message("🚫 Sort refusé", f"{char_name} ne peut pas lancer {spell_name}.", "#cc4444")
            frame.destroy()
            _remove_spell_lines()
            resume_callback(False, spell_level)

        tk.Button(frame, text="✓ Confirmer", bg="#1a3a1a", fg="#66cc66",
                  font=("Arial", 8, "bold"), relief="flat", padx=6, pady=2,
                  activebackground="#2a4a2a", command=_confirm).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(frame, text="✗ Refuser", bg="#3a1a1a", fg="#cc6666",
                  font=("Arial", 8, "bold"), relief="flat", padx=6, pady=2,
                  activebackground="#4a2a2a", command=_deny).pack(side=tk.LEFT)

        self.chat_display.window_create(tk.END, window=frame)
        self.chat_display.insert(tk.END, "\n")

        self.chat_display.tag_config("spell_hint", foreground="#7766aa",
                                      font=("Arial", 8, "italic"))
        self.chat_display.tag_config(tag_header, foreground=color,
                                      font=("Arial", 9, "bold"))
        self.chat_display.config(state=tk.DISABLED)
        self.chat_display.see(tk.END)

        def _remove_spell_lines():
            try:
                self.chat_display.config(state=tk.NORMAL)
                for tag in [tag_header, "spell_hint"]:
                    ranges = self.chat_display.tag_ranges(tag)
                    if ranges:
                        line_start = self.chat_display.index(f"{ranges[0]} linestart")
                        line_end   = self.chat_display.index(f"{ranges[-1]} lineend +1c")
                        self.chat_display.delete(line_start, line_end)
                        break
                self.chat_display.config(state=tk.DISABLED)
            except Exception:
                pass

    # ─── Widget de confirmation d'action inline ───────────────────────────────

    def _append_action_confirm(self, char_name: str, intention: str,
                                regle: str, cible: str, resume_callback):
        """
        Affiche une carte de confirmation d'action dans le chat.
        Le MJ voit : intention narrative, règle 5e exacte, cible.
        Il peut confirmer (avec note optionnelle) ou refuser.
        resume_callback(confirmed: bool, mj_note: str) est appelé
        depuis le thread principal (thread-safe via process_queue).
        """
        color = self.CHAR_COLORS.get(char_name, "#aaaaaa")
        self.msg_counter += 1
        n = self.msg_counter
        tag_card = f"action_card_{n}"

        self.chat_display.config(state=tk.NORMAL)

        # Ligne d'en-tête
        self.chat_display.insert(tk.END, "\n", tag_card)
        self.chat_display.insert(tk.END,
            f"⚔️ ACTION — {char_name}\n", f"action_hdr_{n}")

        # Cadre principal de la carte
        frame = tk.Frame(self.chat_display, bg="#12181a",
                         relief="flat", padx=8, pady=6,
                         highlightthickness=1,
                         highlightbackground=color)

        # Ligne Intention
        row_i = tk.Frame(frame, bg="#12181a")
        row_i.pack(fill=tk.X, pady=1)
        tk.Label(row_i, text="Intention :", bg="#12181a", fg="#888899",
                 font=("Consolas", 8, "bold"), width=11, anchor="w").pack(side=tk.LEFT)
        tk.Label(row_i, text=intention, bg="#12181a", fg="#ddeeff",
                 font=("Consolas", 9), wraplength=380, justify=tk.LEFT,
                 anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Ligne Règle 5e
        row_r = tk.Frame(frame, bg="#12181a")
        row_r.pack(fill=tk.X, pady=1)
        tk.Label(row_r, text="Règle 5e :", bg="#12181a", fg="#888899",
                 font=("Consolas", 8, "bold"), width=11, anchor="w").pack(side=tk.LEFT)
        tk.Label(row_r, text=regle, bg="#12181a", fg=color,
                 font=("Consolas", 9, "bold"), wraplength=380, justify=tk.LEFT,
                 anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Ligne Cible
        row_c = tk.Frame(frame, bg="#12181a")
        row_c.pack(fill=tk.X, pady=1)
        tk.Label(row_c, text="Cible :", bg="#12181a", fg="#888899",
                 font=("Consolas", 8, "bold"), width=11, anchor="w").pack(side=tk.LEFT)
        tk.Label(row_c, text=cible, bg="#12181a", fg="#bbbbcc",
                 font=("Consolas", 9), wraplength=380, justify=tk.LEFT,
                 anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Séparateur
        tk.Frame(frame, bg="#2a2a3a", height=1).pack(fill=tk.X, pady=(5, 3))

        # Zone note MJ + boutons
        row_btns = tk.Frame(frame, bg="#12181a")
        row_btns.pack(fill=tk.X)

        tk.Label(row_btns, text="Note MJ :", bg="#12181a", fg="#888899",
                 font=("Arial", 8)).pack(side=tk.LEFT, padx=(0, 4))
        note_entry = tk.Entry(row_btns, bg="#1e2230", fg="#eeeeee",
                              font=("Consolas", 9), insertbackground="white",
                              relief="flat", width=28)
        note_entry.pack(side=tk.LEFT, padx=(0, 8), ipady=2)

        def _confirm(event=None):
            note = note_entry.get().strip()
            frame.destroy()
            _cleanup_header()
            self.append_message(
                f"✅ MJ → {char_name}",
                f"Action autorisée : {intention}" + (f"  — {note}" if note else ""),
                "#44aa44",
            )
            resume_callback(True, note)

        def _deny(event=None):
            note = note_entry.get().strip()
            frame.destroy()
            _cleanup_header()
            self.append_message(
                f"❌ MJ → {char_name}",
                f"Action refusée : {intention}" + (f"  — {note}" if note else ""),
                "#aa4444",
            )
            resume_callback(False, note)

        note_entry.bind("<Return>", _confirm)

        tk.Button(row_btns, text="✓ Autoriser", bg="#0d2a0d", fg="#66ee66",
                  font=("Arial", 8, "bold"), relief="flat", padx=8, pady=2,
                  activebackground="#1a4a1a", command=_confirm).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(row_btns, text="✗ Refuser", bg="#2a0d0d", fg="#ee6666",
                  font=("Arial", 8, "bold"), relief="flat", padx=8, pady=2,
                  activebackground="#4a1a1a", command=_deny).pack(side=tk.LEFT)

        self.chat_display.window_create(tk.END, window=frame)
        self.chat_display.insert(tk.END, "\n")

        self.chat_display.tag_config(f"action_hdr_{n}",
                                      foreground=color,
                                      font=("Consolas", 9, "bold"))
        self.chat_display.config(state=tk.DISABLED)
        self.chat_display.see(tk.END)

        def _cleanup_header():
            try:
                self.chat_display.config(state=tk.NORMAL)
                ranges = self.chat_display.tag_ranges(f"action_hdr_{n}")
                if ranges:
                    ls = self.chat_display.index(f"{ranges[0]} linestart")
                    le = self.chat_display.index(f"{ranges[-1]} lineend +1c")
                    self.chat_display.delete(ls, le)
                self.chat_display.config(state=tk.DISABLED)
            except Exception:
                pass

    # ─── Interactions clic gauche (remplissage /msg) ──────────────────────────

    def _on_chat_click(self, event):
        """Clic gauche sur chat_display — détecte un tag sender_* et remplit l'entrée."""
        import re as _re_click
        idx = self.chat_display.index(f"@{event.x},{event.y}")
        for tag in self.chat_display.tag_names(idx):
            if tag.startswith("sender_"):
                ranges = self.chat_display.tag_ranges(tag)
                if not ranges:
                    continue
                sender_text = self.chat_display.get(ranges[0], ranges[1])
                clean = _re_click.sub(r'^[^\w]*', '', sender_text)
                clean = _re_click.sub(r'\s*\(.*?\)\s*$', '', clean).strip()
                if clean in self._PLAYER_NAMES:
                    self.entry.delete(0, tk.END)
                    self.entry.insert(0, f"/msg {clean} ")
                    self.entry.focus_set()
                return

    def _on_chat_motion(self, event):
        """Change le curseur en main si on survole un nom de joueur cliquable."""
        import re as _re_mot
        idx = self.chat_display.index(f"@{event.x},{event.y}")
        for tag in self.chat_display.tag_names(idx):
            if tag.startswith("sender_"):
                ranges = self.chat_display.tag_ranges(tag)
                if ranges:
                    txt   = self.chat_display.get(ranges[0], ranges[1])
                    clean = _re_mot.sub(r'^[^\w]*', '', txt)
                    clean = _re_mot.sub(r'\s*\(.*?\)\s*$', '', clean).strip()
                    if clean in self._PLAYER_NAMES:
                        self.chat_display.config(cursor="hand2")
                        return
        self.chat_display.config(cursor="")

    # ─── Menu contextuel (clic droit) ─────────────────────────────────────────

    def show_context_menu(self, event):
        """Détecte le message sous le curseur et affiche le menu contextuel."""
        if self.context_menu is None:
            self.context_menu = tk.Menu(self.root, tearoff=0, bg="#3d3d3d", fg="white")
            self.context_menu.add_command(label="✏️ Éditer ce message", command=self.edit_selected_message)
            self.context_menu.add_command(label="🗑️ Supprimer ce message", command=self.delete_selected_message)

        click_index = self.chat_display.index(f"@{event.x},{event.y}")

        self.selected_msg_id = None
        for msg in self.messages_index:
            tag = msg["tag"]
            ranges = self.chat_display.tag_ranges(tag)
            if ranges:
                tag_start, tag_end = ranges[0], ranges[1]
                if self.chat_display.compare(tag_start, "<=", click_index) and \
                   self.chat_display.compare(click_index, "<=", tag_end):
                    self.selected_msg_id = msg["id"]
                    break

        if self.selected_msg_id is not None:
            self.context_menu.post(event.x_root, event.y_root)

    def delete_selected_message(self):
        """Supprime visuellement le message sélectionné."""
        if self.selected_msg_id is None:
            return
        for i, msg in enumerate(self.messages_index):
            if msg["id"] == self.selected_msg_id:
                tag = msg["tag"]
                ranges = self.chat_display.tag_ranges(tag)
                if ranges:
                    self.chat_display.config(state=tk.NORMAL)
                    self.chat_display.delete(ranges[0], ranges[1])
                    self.chat_display.config(state=tk.DISABLED)
                self.messages_index.pop(i)
                break
        self.selected_msg_id = None

    def edit_selected_message(self):
        """Ouvre une fenêtre pour éditer le message sélectionné."""
        if self.selected_msg_id is None:
            return
        target = next((m for m in self.messages_index if m["id"] == self.selected_msg_id), None)
        if not target:
            return

        edit_win = tk.Toplevel(self.root)
        edit_win.title("✏️ Éditer le message")
        edit_win.geometry("600x300")
        edit_win.configure(bg="#1e1e1e")
        edit_win.grab_set()

        tk.Label(edit_win, text=f"Message de : {target['sender']}",
                bg="#1e1e1e", fg="#ffcc00", font=("Arial", 11, "bold")).pack(pady=(10, 5))

        text_box = scrolledtext.ScrolledText(edit_win, height=8, bg="#3d3d3d", fg="white", font=("Consolas", 11))
        text_box.pack(fill=tk.BOTH, expand=True, padx=10)
        text_box.insert("1.0", target["text"])

        def confirm_edit():
            new_text = text_box.get("1.0", tk.END).strip()
            tag = target["tag"]
            ranges = self.chat_display.tag_ranges(tag)
            if ranges:
                self.chat_display.config(state=tk.NORMAL)
                self.chat_display.delete(ranges[0], ranges[1])
                full_text = f"\n[{target['sender']}]: {new_text}\n"
                self.chat_display.insert(ranges[0], full_text, tag)
                self.chat_display.tag_config(tag, foreground=target["color"])
                self.chat_display.config(state=tk.DISABLED)
            target["text"] = new_text
            edit_win.destroy()

        tk.Button(edit_win, text="✅ Confirmer", bg="#4CAF50", fg="white",
                font=("Arial", 10, "bold"), command=confirm_edit).pack(pady=10)