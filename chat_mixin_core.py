import tkinter as tk
from tkinter import scrolledtext

class ChatMixinCore:
    """Mixin pour DnDApp — panneau de chat basique, purge, interactions clic et menu contextuel."""

    _PLAYER_NAMES = {"Kaelen", "Elara", "Thorne", "Lyra"}
    _MAX_MESSAGES = 300   # cap anti-explosion de tags Tk → segfault Tcl

    # ─── Affichage simple (legacy, utilisé par certains anciens appels) ────────

    def display_message(self, sender, text, color="#e0e0e0"):
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.insert(tk.END, f"[{sender}]\n", "sender")
        self.chat_display.insert(tk.END, f"{text}\n\n", "text")
        self.chat_display.tag_config("sender", foreground="#ffcc00", font=("Consolas", 11, "bold"))
        self.chat_display.tag_config("text", foreground=color)
        self.chat_display.config(state=tk.DISABLED)
        def _force_scroll():
            try:
                self.chat_display.update_idletasks()
                self.chat_display.yview_moveto(1.0)
            except Exception: pass
        self.chat_display.after(50, _force_scroll)

    # ─── Ajout de messages taggés ─────────────────────────────────────────────

    def append_message(self, sender: str, text: str, color: str):
        """Ajoute un message taggé dans le chat. Utilise des tags partagés pour la performance."""
        self.msg_counter += 1
        msg_id   = self.msg_counter
        
        # Tags partagés pour prévenir l'explosion des tags Tk
        tag_color = f"color_{color.replace('#', '')}"
        tag_sender = f"sender_{color.replace('#', '')}"
        
        # Ce tag sert à identifier tout le bloc du message pour la suppression (pas de config visuelle)
        tag_name = f"msg_{msg_id}"

        self.chat_display.config(state=tk.NORMAL)

        self.chat_display.insert(tk.END, "\n[", tag_name)
        self.chat_display.insert(tk.END, sender, (tag_name, tag_sender))
        if (text.strip().startswith("[MISE À JOUR CARTE") 
            or "═══ CARTE DE COMBAT" in text.strip()
            or text.strip().startswith("[RÉSULTAT SYSTÈME")
            or text.strip().startswith("[TOUR EN COURS")
            or text.strip().startswith("[COMBO INTERDIT")
            or text.strip().startswith("[SYSTÈME — HORS TOUR")
            or "tool_use_failed" in text
            or "Tentative de récupération" in text
            or "PARAMÈTRE INVALIDE" in text
            or "DIRECTIVE SYSTÈME" in text
            or "VIOLATION PNJ" in text
            or "VIOLATION SYSTÈME" in text):
            parts = text.strip().split("\n", 1)
            header_txt = parts[0].strip()
            body_txt   = "\n" + parts[1] if len(parts) > 1 else ""
            
            tag_col_btn  = f"col_btn_{msg_id}"
            tag_col_body = f"col_body_{msg_id}"
            
            self.chat_display.insert(tk.END, "]: ", tag_name)
            self.chat_display.insert(tk.END, "▶\n", (tag_name, tag_col_btn))
            self.chat_display.insert(tk.END, body_txt + "\n", (tag_name, tag_col_body))
            
            self.chat_display.tag_config(tag_col_btn, foreground="#74b9ff", underline=True)
            self.chat_display.tag_config(tag_col_body, elide=True)
            
            def _toggle_map_msg(event, t_btn=tag_col_btn, t_body=tag_col_body, h=header_txt, tg_name=tag_name):
                self.chat_display.config(state=tk.NORMAL)
                is_elided = str(self.chat_display.tag_cget(t_body, "elide"))
                new_elide = False if is_elided in ("1", "true", "True") else True
                
                self.chat_display.tag_config(t_body, elide=new_elide)
                
                ranges = self.chat_display.tag_ranges(t_btn)
                if ranges:
                    self.chat_display.delete(ranges[0], ranges[1])
                    icon = "▶" if new_elide else "▼"
                    btn_text = f"{icon}\n" if new_elide else f"{icon} {h} (réduire)\n"
                    self.chat_display.insert(ranges[0], btn_text, (tg_name, t_btn))
                
                self.chat_display.config(state=tk.DISABLED)
            
            self.chat_display.tag_bind(tag_col_btn, "<Button-1>", _toggle_map_msg)
            self.chat_display.tag_bind(tag_col_btn, "<Enter>", lambda e: self.chat_display.config(cursor="hand2"))
            self.chat_display.tag_bind(tag_col_btn, "<Leave>", lambda e: self.chat_display.config(cursor=""))
        else:
            self.chat_display.insert(tk.END, f"]: {text}\n", (tag_name, tag_color))

        self.chat_display.tag_config(tag_color,   foreground=color)
        self.chat_display.tag_config(tag_sender, foreground=color,
                                     font=("Consolas", 11, "bold"),
                                     underline=False)

        self.chat_display.config(state=tk.DISABLED)
        def _force_scroll():
            try:
                self.chat_display.update_idletasks()
                self.chat_display.yview_moveto(1.0)
            except Exception: pass
        self.chat_display.after(50, _force_scroll)
        self.messages_index.append({
            "id":     msg_id,
            "sender": sender,
            "text":   text,
            "color":  color,
            "tag":    tag_name,
        })

        # ── Purge des anciens messages (anti-segfault explosion de tags Tk) ─────
        if len(self.messages_index) > self._MAX_MESSAGES:
            self._purge_oldest_messages(self._MAX_MESSAGES // 10)

        # ── Détection [RÉSULTAT SYSTÈME — * IMPOSSIBLE — NomAgent] ───────────
        import re as _re_imp
        _imp_m = _re_imp.search(
            r'\[RÉSULTAT SYSTÈME\s*[—\-][^\]\n—]*IMPOSSIBLE\s*[—\-]\s*(\w+)',
            text,
            _re_imp.IGNORECASE,
        )
        if _imp_m:
            _char = _imp_m.group(1)
            _instr_m = _re_imp.search(
                r'\[INSTRUCTION\]\s*(.*?)(?=\n\[|\Z)',
                text,
                _re_imp.IGNORECASE | _re_imp.DOTALL,
            )
            _instr = _instr_m.group(1).strip() if _instr_m else \
                "Annule cette tentative et déclare une action valide."
            self._pending_impossible_retrigger = (_char, _instr)

        # ── Noms de sorts cliquables ─────────────────────────────────────────
        _SPELL_TAG_SENDERS = {"Kaelen", "Elara", "Thorne", "Lyra",
                               "Alexis_Le_MJ", "Alexis_Le_MJ (Vocal)"}
        if sender in _SPELL_TAG_SENDERS or sender.startswith("🎭 "):
            if hasattr(self, "_tag_spells_in_message"):
                self._tag_spells_in_message(tag_name, text)

        # ── Détection *mots-clés* dans les messages MJ ────────────────────────
        _mj_senders = {"Alexis_Le_MJ", "Alexis_Le_MJ (Vocal)"}
        if sender in _mj_senders:
            import re as _re_kw, threading as _th_kw
            _keywords = _re_kw.findall(r'\*([^*]+)\*', text)
            if _keywords and hasattr(self, "_check_and_update_memories"):
                _th_kw.Thread(
                    target=self._check_and_update_memories,
                    args=(_keywords, text),
                    daemon=True,
                ).start()

    # ─── Purge des anciens messages (anti-explosion de tags Tk) ──────────────

    def _purge_oldest_messages(self, n: int):
        """
        Supprime les n plus anciens messages du widget Text et libère leurs tags.
        """
        to_remove = self.messages_index[:n]
        self.chat_display.config(state=tk.NORMAL)
        for entry in to_remove:
            msg_id = entry["id"]
            tag    = entry["tag"]
            ranges = self.chat_display.tag_ranges(tag)
            if ranges:
                start = str(ranges[0])
                try:
                    prev = self.chat_display.index(f"{start} -1c")
                    if self.chat_display.compare(prev, ">=", "1.0"):
                        start = prev
                except Exception:
                    pass
                end = str(ranges[-1])
                try:
                    next_c = self.chat_display.index(f"{end} +1c")
                    if self.chat_display.get(end, next_c) == "\n":
                        end = next_c
                except Exception:
                    pass
                try:
                    self.chat_display.delete(start, end)
                except Exception:
                    pass
            for t in (
                tag,
                f"sender_{msg_id}",
                f"col_btn_{msg_id}",
                f"col_body_{msg_id}",
            ):
                try:
                    self.chat_display.tag_delete(t)
                except Exception:
                    pass
        self.chat_display.config(state=tk.DISABLED)
        self.messages_index = self.messages_index[n:]

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
        """Supprime visuellement le message sélectionné (texte complet, y compris newlines)."""
        if self.selected_msg_id is None:
            return
        for i, msg in enumerate(self.messages_index):
            if msg["id"] == self.selected_msg_id:
                tag = msg["tag"]
                ranges = self.chat_display.tag_ranges(tag)
                if ranges:
                    self.chat_display.config(state=tk.NORMAL)
                    start = str(ranges[0])
                    try:
                        prev = self.chat_display.index(f"{start} -1c")
                        if self.chat_display.compare(prev, ">=", "1.0"):
                            start = prev
                    except Exception:
                        pass
                    end = str(ranges[-1])
                    try:
                        next_c = self.chat_display.index(f"{end} +1c")
                        char = self.chat_display.get(end, next_c)
                        if char == "\n":
                            end = next_c
                    except Exception:
                        pass
                    self.chat_display.delete(start, end)
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