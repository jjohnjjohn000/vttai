import tkinter as tk

class ChatMixinSpellsMap:
    """Mixin pour DnDApp — tagging de sorts, images de carte, et boutons relais."""

    # ─── Détection et tagging des noms de sorts dans le chat ─────────────────

    def _tag_spells_in_message(self, msg_tag: str, text: str):
        """
        Après insertion d'un message, détecte les noms de sorts connus dans le
        texte et les rend cliquables (ouvre SpellSheetWindow au clic).
        Thread-safe : appelé depuis le thread Tk uniquement (via append_message).

        Sécurités anti-segfault :
          - Max _MAX_SPELL_TAGS tags créés par message (évite boucles trop longues).
          - Garde trace des positions déjà taggées pour éviter les boucles infinies.
          - Un seul tag par position (pas de doublons de binding).
          - Limitée aux messages narratifs (agents + MJ) — voir append_message.
        """
        _MAX_SPELL_TAGS = 12   # max d'occurrences cliquables par message

        try:
            from spell_data import get_spell_pattern, get_spell, SpellSheetWindow, _SPELL_DATA
        except ImportError:
            return

        if not _SPELL_DATA:
            return  # sorts pas encore chargés → on ne bloque pas l'UI

        pattern = get_spell_pattern()
        if pattern is None:
            return

        # Déduplication : on ne traite chaque nom de sort qu'une seule fois par message
        seen_names: set[str] = set()
        matches = [m for m in pattern.finditer(text)
                   if m.group(0).lower() not in seen_names
                   and not seen_names.add(m.group(0).lower())]  # type: ignore[func-returns-value]
        if not matches:
            return

        # Récupère la plage du tag dans le widget
        ranges = self.chat_display.tag_ranges(msg_tag)
        if not ranges:
            return
        tag_start = str(ranges[0])
        tag_end   = str(ranges[-1])

        # Tags déjà posés dans ce widget (évite les doublons)
        _existing_tags: set[str] = set(self.chat_display.tag_names())

        self.chat_display.config(state=tk.NORMAL)
        _total_tagged = 0
        try:
            for match in matches:
                if _total_tagged >= _MAX_SPELL_TAGS:
                    break

                spell_name = match.group(0)   # casse originale dans le texte
                sp = get_spell(spell_name)
                if not sp:
                    continue

                # Cherche UNE SEULE occurrence par nom de sort par message
                # (pas de while True — évite la boucle infinie)
                search_from = tag_start
                _occurrences = 0
                _MAX_OCC = 3   # max 3 occurrences du même sort dans un message
                while _occurrences < _MAX_OCC and _total_tagged < _MAX_SPELL_TAGS:
                    idx = self.chat_display.search(
                        spell_name, search_from,
                        stopindex=tag_end,
                        nocase=True,
                        exact=True
                    )
                    if not idx:
                        break

                    end_idx = f"{idx}+{len(spell_name)}c"

                    # Vérifier que end_idx > search_from pour éviter boucle infinie
                    try:
                        if not self.chat_display.compare(end_idx, ">", search_from):
                            break
                    except tk.TclError:
                        break

                    # Tag unique par NOM DE SORT (réutilisé pour toutes les occurrences)
                    spell_safe = "".join(c for c in spell_name if c.isalnum() or c == "_")
                    spell_tag = f"clickspell_{spell_safe}"

                    # Ne pas re-créer un tag ou dupliquer les bindings si le sort a déjà été taggé avant
                    if spell_tag not in _existing_tags:
                        self.chat_display.tag_config(
                            spell_tag,
                            foreground="#e8c84a",
                            underline=True,
                            font=("Consolas", 10, "bold"),
                        )
                        def _open_sheet(event, _sp=sp):
                            SpellSheetWindow(self.root, _sp)
                        self.chat_display.tag_bind(spell_tag, "<Button-1>", _open_sheet)
                        self.chat_display.tag_bind(
                            spell_tag, "<Enter>",
                            lambda e: self.chat_display.config(cursor="hand2"),
                        )
                        self.chat_display.tag_bind(
                            spell_tag, "<Leave>",
                            lambda e: self.chat_display.config(cursor=""),
                        )
                        _existing_tags.add(spell_tag)

                    self.chat_display.tag_add(spell_tag, idx, end_idx)
                    _total_tagged += 1

                    search_from = end_idx
                    _occurrences += 1
        except tk.TclError:
            pass   # widget détruit ou état invalide — on abandonne silencieusement
        finally:
            self.chat_display.config(state=tk.DISABLED)

    # ─── Helpers sort — hyperliens vers la fiche ──────────────────────────────

    def _open_spell_sheet(self, spell_name: str):
        """Ouvre SpellSheetWindow pour le sort donné (non-modal)."""
        try:
            from spell_data import SpellSheetWindow
            SpellSheetWindow(self.root, spell_name)
        except Exception as _e:
            print(f"[SpellSheet] Impossible d'ouvrir \u00ab{spell_name}\u00bb : {_e}")

    def _make_regle_with_links(self, parent, regle: str, fg_color: str, bg: str):
        """
        Remplace le tk.Label statique de 'Règle 5e' par un tk.Frame
        avec les noms de sorts rendus cliquables (hyperliens → SpellSheetWindow).

        Détecte deux formats :
          • [SORT: NomSort | Niveau: X | Cible: Y]   (balise moteur avec crochets)
          • Sort : NomSort | Niveau : X | Cible : Y  (format affiché sans crochets)
        """
        import re as _re_rgl
        container = tk.Frame(parent, bg=bg)
        try:
            from spell_data import get_spell_pattern
            spell_re = get_spell_pattern()
        except Exception:
            spell_re = None

        segments, prev = [], 0
        
        if spell_re:
            for m in spell_re.finditer(regle):
                if m.start() > prev:
                    segments.append((regle[prev:m.start()], False, None))
                nom = m.group(1).strip()
                segments.append((m.group(0), True, nom))
                prev = m.end()
                
        if prev < len(regle):
            segments.append((regle[prev:], False, None))
        if not segments:
            segments = [(regle, False, None)]

        # Aucun sort détecté → label simple (comportement identique à l'original)
        if not any(s[1] for s in segments):
            tk.Label(container, text=regle, bg=bg, fg=fg_color,
                     font=("Consolas", 9, "bold"), wraplength=380,
                     justify=tk.LEFT, anchor="w").pack(
                         side=tk.LEFT, fill=tk.X, expand=True)
            return container

        for text, is_link, nom in segments:
            if not text:
                continue
            if is_link and nom:
                pre = text[:text.find(nom)]
                suf = text[text.find(nom) + len(nom):]
                if pre:
                    tk.Label(container, text=pre, bg=bg, fg=fg_color,
                             font=("Consolas", 9, "bold")).pack(side=tk.LEFT)
                lnk = tk.Label(container, text=nom, bg=bg, fg="#5bc8ff",
                               font=("Consolas", 9, "bold", "underline"),
                               cursor="hand2")
                lnk.pack(side=tk.LEFT)
                lnk.bind("<Button-1>", lambda _e, n=nom: self._open_spell_sheet(n))
                lnk.bind("<Enter>",    lambda _e, w=lnk: w.config(fg="#a0e8ff"))
                lnk.bind("<Leave>",    lambda _e, w=lnk: w.config(fg="#5bc8ff"))
                if suf:
                    tk.Label(container, text=suf, bg=bg, fg=fg_color,
                             font=("Consolas", 9, "bold")).pack(side=tk.LEFT)
            else:
                tk.Label(container, text=text, bg=bg, fg=fg_color,
                         font=("Consolas", 9, "bold"), wraplength=380,
                         justify=tk.LEFT, anchor="w").pack(side=tk.LEFT)
        return container

    # ─── Image pointeur MJ ───────────────────────────────────────────────────

    def _append_map_pointer(self, img_bytes: "bytes | None",
                            comment: str, sender: str):
        """
        Insère une image de carte (avec pointeur) directement dans le chat,
        suivie du commentaire MJ. L'image est cliquable pour l'agrandir.
        La référence PhotoImage est conservée dans self._map_pointer_photos
        pour éviter le garbage collect.
        """
        import io as _io
        try:
            from PIL import Image as _PilImage, ImageTk as _ImageTk
        except ImportError:
            # Fallback sans image : afficher seulement le commentaire
            self.append_message(sender, comment or "📍 Point sur la carte", "#ff8a80")
            return

        # Conserver les PhotoImages pour éviter le GC (Tk perd l'image sinon)
        if not hasattr(self, "_map_pointer_photos"):
            self._map_pointer_photos = []

        self.chat_display.config(state=tk.NORMAL)
        self.msg_counter += 1
        tag_name = f"msg_{self.msg_counter}"

        # ── Commentaire header ────────────────────────────────────────────────
        self.chat_display.insert(tk.END, "\n", tag_name)
        
        tag_color = "color_ff8a80"
        tag_sender = "sender_ff8a80"
        
        self.chat_display.insert(
            tk.END,
            f"[{sender}]",
            (tag_name, tag_sender))
            
        self.chat_display.tag_config(
            tag_sender,
            foreground="#ff8a80",
            font=("Consolas", 11, "bold"))

        if comment:
            self.chat_display.insert(tk.END, f"\n{comment}\n", (tag_name, tag_color))
        else:
            self.chat_display.insert(tk.END, "\n", tag_name)

        self.chat_display.tag_config(tag_color, foreground="#ff8a80")

        # ── Image inline ──────────────────────────────────────────────────────
        if img_bytes:
            try:
                pil_img = _PilImage.open(_io.BytesIO(img_bytes)).convert("RGBA")

                # Redimensionner pour le chat (max 480px de large)
                MAX_W = 480
                iw, ih = pil_img.size
                if iw > MAX_W:
                    ratio   = MAX_W / iw
                    pil_img = pil_img.resize(
                        (MAX_W, int(ih * ratio)), getattr(_PilImage, 'Resampling', _PilImage).LANCZOS)

                photo = _ImageTk.PhotoImage(pil_img)
                self._map_pointer_photos.append(photo)   # anti-GC

                # Frame conteneur cliquable
                img_tag = f"map_img_{self.msg_counter}"
                frame = tk.Frame(self.chat_display, bg="#0d0d1a",
                                 cursor="hand2", relief="flat", bd=1,
                                 highlightthickness=1,
                                 highlightbackground="#3a2a4a")
                lbl = tk.Label(frame, image=photo, bg="#0d0d1a",
                               cursor="hand2")
                lbl.pack()

                # Clic → popup agrandi
                def _show_full(event, _bytes=img_bytes, _name=comment):
                    self._popup_map_image(_bytes, _name)

                lbl.bind("<Button-1>", _show_full)
                frame.bind("<Button-1>", _show_full)

                self.chat_display.window_create(tk.END, window=frame)
                self.chat_display.insert(tk.END, "\n", tag_name)

            except Exception as e:
                print(f"[MapPointer] Erreur affichage image : {e}")
                self.chat_display.insert(
                    tk.END, "[image non disponible]\n", tag_name)

        self.chat_display.insert(tk.END, "\n", tag_name)
        self.chat_display.config(state=tk.DISABLED)
        def _force_scroll():
            try:
                self.chat_display.update_idletasks()
                self.chat_display.yview_moveto(1.0)
            except Exception: pass
        self.chat_display.after(50, _force_scroll)
        self.chat_display.after(250, _force_scroll)

        self.messages_index.append({
            "id":     self.msg_counter,
            "sender": sender,
            "text":   comment,
            "color":  "#ff8a80",
            "tag":    tag_name,
        })

    def _popup_map_image(self, img_bytes: bytes, title: str = ""):
        """Affiche l'image de carte en plein écran dans une fenêtre popup."""
        import io as _io
        try:
            from PIL import Image as _PI, ImageTk as _IT
        except ImportError:
            return

        popup = tk.Toplevel(self.root)
        popup.title(title[:60] if title else "Carte — Pointeur MJ")
        popup.configure(bg="#0a0a14")
        popup.bind("<Escape>", lambda e: popup.destroy())
        popup.bind("<Button-1>", lambda e: popup.destroy())

        try:
            pil_img = _PI.open(_io.BytesIO(img_bytes)).convert("RGBA")
            # Adapter à l'écran (max 90% de la résolution)
            screen_w = popup.winfo_screenwidth()
            screen_h = popup.winfo_screenheight()
            max_w, max_h = int(screen_w * 0.9), int(screen_h * 0.85)
            iw, ih = pil_img.size
            ratio  = min(max_w / iw, max_h / ih, 1.0)
            if ratio < 1.0:
                pil_img = pil_img.resize(
                    (int(iw * ratio), int(ih * ratio)), getattr(_PI, 'Resampling', _PI).LANCZOS)
            photo = _IT.PhotoImage(pil_img)
            # Anti-GC sur la popup
            if not hasattr(self, "_map_pointer_photos"):
                self._map_pointer_photos = []
            self._map_pointer_photos.append(photo)

            iw2, ih2 = pil_img.size
            popup.geometry(f"{iw2}x{ih2 + 28}")
            tk.Label(popup, image=photo, bg="#0a0a14").pack()
            tk.Label(popup, text="Clic ou Échap pour fermer",
                     bg="#0a0a14", fg="#444466",
                     font=("Consolas", 8)).pack()
        except Exception as e:
            tk.Label(popup, text=f"Erreur : {e}", bg="#0a0a14",
                     fg="#e57373").pack(padx=20, pady=20)

    # ─── Bouton relay (message privé partageable au groupe) ───────────────────

    def _append_relay_button(self, char_name: str, reply_text: str):
        """Insère un bouton-texte cliquable (tag) dans le chat — sans window_create."""
        from chat_log_writer import strip_mechanical_blocks
        
        color = getattr(self, "CHAR_COLORS", {}).get(char_name, "#aaaaaa")
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
            tts_relay = strip_mechanical_blocks(reply_text)
            if tts_relay:
                self.audio_queue.put((tts_relay, char_name))
            relayed = f"[{char_name}, s'adressant au groupe] {reply_text}"
            if getattr(self, "_llm_running", False) and not getattr(self, "_waiting_for_mj", False):
                self._pending_interrupt_input = relayed
                self._pending_interrupt_display = None
                if hasattr(self, "_inject_stop"):
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
        def _force_scroll():
            try:
                self.chat_display.update_idletasks()
                self.chat_display.yview_moveto(1.0)
            except Exception: pass
        self.chat_display.after(50, _force_scroll)

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