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

from voice_interface import play_voice, prefetch_voice, play_prefetched
from agent_logger    import log_tts_end


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
        """
        Consomme audio_queue et joue les voix séquentiellement.
        Pendant la lecture de l'entrée N, on pré-génère les chunks TTS
        de l'entrée N+1 en parallèle → latence réduite entre personnages.
        """
        import threading as _th

        # Entrée courante : (text, name, prefetched_files | None)
        current_text  = None
        current_name  = None
        current_files = None   # None = pas encore préfetchée

        def _prefetch_worker(text, name, result_holder):
            result_holder[0] = prefetch_voice(text, name)

        prefetch_thread: _th.Thread | None = None
        next_text  = None
        next_name  = None
        next_holder: list = [None]   # [files] rempli par le thread

        while True:
            # ── Obtenir la prochaine entrée ────────────────────────────────
            if next_text is not None:
                # On a déjà préfetché la suivante pendant la lecture
                current_text  = next_text
                current_name  = next_name
                # Attendre que le prefetch soit fini s'il ne l'est pas encore
                if prefetch_thread and prefetch_thread.is_alive():
                    prefetch_thread.join()
                current_files = next_holder[0] or []
                next_text = next_name = None
                next_holder = [None]
                prefetch_thread = None
            else:
                try:
                    current_text, current_name = self.audio_queue.get(timeout=1.0)
                    current_files = None   # sera généré ci-dessous si la queue est vide
                except queue.Empty:
                    continue

            # ── Préfetch immédiat si pas encore fait ──────────────────────
            if current_files is None:
                # Pas de prochaine entrée connue — générer maintenant (bloquant)
                current_files = prefetch_voice(current_text, current_name)

            # ── Peek : y a-t-il une prochaine entrée dans la queue ? ──────
            try:
                next_text, next_name = self.audio_queue.get_nowait()
                # Lancer le prefetch en parallèle pendant qu'on joue
                next_holder = [None]
                prefetch_thread = _th.Thread(
                    target=_prefetch_worker,
                    args=(next_text, next_name, next_holder),
                    daemon=True,
                )
                prefetch_thread.start()
            except queue.Empty:
                next_text = next_name = None

            # ── Lecture ───────────────────────────────────────────────────
            face = self.face_windows.get(current_name)
            if face:
                face.set_talking(True)
            try:
                if current_files:
                    success = play_prefetched(current_files)
                else:
                    # Fallback : prefetch a échoué → lecture directe
                    success = play_voice(current_text, current_name)
                log_tts_end(current_name, success=bool(success))
            except Exception as e:
                log_tts_end(current_name, success=False)
                print(f"Erreur audio de {current_name}: {e}")
            finally:
                if face:
                    face.set_talking(False)
            self.audio_queue.task_done()

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
                elif action == "result_confirm":
                    self._append_result_confirm(
                        msg["char_name"],
                        msg["type_label"],
                        msg["results_text"],
                        msg["resume_callback"],
                        mode=msg.get("mode", "damage"),
                    )
                elif action == "action_confirm":
                    self._append_action_confirm(
                        msg["char_name"],
                        msg.get("type_label", "Action"),
                        msg["intention"],
                        msg["regle"],
                        msg["cible"],
                        msg["resume_callback"],
                        sub_index=msg.get("sub_index"),
                        sub_total=msg.get("sub_total"),
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

        # ── Noms de sorts cliquables ─────────────────────────────────────────
        # Seulement pour les messages narratifs des agents joueurs et du MJ.
        # Les messages système / simulation sont souvent très longs et bourrés
        # de noms qui feraient tourner search() Tk des centaines de fois → segfault.
        _SPELL_TAG_SENDERS = {"Kaelen", "Elara", "Thorne", "Lyra",
                               "Alexis_Le_MJ", "Alexis_Le_MJ (Vocal)"}
        if sender in _SPELL_TAG_SENDERS or sender.startswith("🎭 "):
            self._tag_spells_in_message(tag_name, text)

        # ── Détection *mots-clés* dans les messages MJ ────────────────────────
        # Si le message vient du MJ et contient *...*, on vérifie la mémoire
        # persistante et on crée/met-à-jour l'entrée en arrière-plan.
        _mj_senders = {"Alexis_Le_MJ", "Alexis_Le_MJ (Vocal)"}
        if sender in _mj_senders or sender.startswith("🎭 "):
            import re as _re_kw, threading as _th_kw
            _keywords = _re_kw.findall(r'\*([^*]+)\*', text)
            if _keywords:
                _th_kw.Thread(
                    target=self._check_and_update_memories,
                    args=(_keywords, text),
                    daemon=True,
                ).start()

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

                    # Tag unique par position
                    spell_tag = f"clickspell_{idx.replace('.', '_')}"

                    # Ne pas re-créer un tag déjà existant (pas de doublons de binding)
                    if spell_tag not in _existing_tags:
                        self.chat_display.tag_add(spell_tag, idx, end_idx)
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
                        _total_tagged += 1

                    search_from = end_idx
                    _occurrences += 1
        except tk.TclError:
            pass   # widget détruit ou état invalide — on abandonne silencieusement
        finally:
            self.chat_display.config(state=tk.DISABLED)

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

    # ─── Widget de confirmation des résultats de dés ────────────────────────────

    def _append_result_confirm(self, char_name: str, type_label: str,
                                results_text: str, resume_callback,
                                mode: str = "damage"):
        """
        Affiche une carte de confirmation après le lancer de dés.

        mode="attack"  → boutons ✓ Touché / ✗ Raté
                         resume_callback(hit: bool, mj_note: str)
        mode="damage"  → bouton ▶ Continuer
                         resume_callback(mj_note: str)
        mode="smite"   → boutons ✓ Appliquer / ✗ Passer
                         resume_callback(hit: bool, mj_note: str)
        """
        color = self.CHAR_COLORS.get(char_name, "#aaaaaa")
        self.msg_counter += 1
        n = self.msg_counter

        # Couleur de bordure selon le type (même palette que action_confirm)
        _TYPE_COLORS = {
            "action bonus": "#e67e22",
            "bonus":        "#e67e22",
            "réaction":     "#3498db",
            "reaction":     "#3498db",
            "mouvement":    "#27ae60",
            "gratuite":     "#8e44ad",
        }
        type_low   = type_label.lower()
        type_color = next(
            (v for k, v in _TYPE_COLORS.items() if k in type_low),
            color,
        )

        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.insert(tk.END, "\n")
        self.chat_display.insert(tk.END,
            f"🎲 RÉSULTATS — {type_label.upper()} — {char_name}\n",
            f"result_hdr_{n}")

        frame = tk.Frame(self.chat_display, bg="#0d1a10",
                         relief="flat", padx=8, pady=6,
                         highlightthickness=2,
                         highlightbackground=type_color)

        # Badge type + libellé selon le mode
        _mode_labels = {
            "attack": f" 🎯 {type_label} — jet d'attaque ",
            "smite":  f" ✨ Divine Smite — appliquer ? ",
            "damage": f" 🎲 {type_label} — dégâts ",
        }
        badge_text = _mode_labels.get(mode, f" 🎲 {type_label} — résultats ")
        badge = tk.Frame(frame, bg=type_color)
        badge.pack(anchor="w", pady=(0, 4))
        tk.Label(badge, text=badge_text,
                 bg=type_color, fg="white",
                 font=("Consolas", 8, "bold"), padx=4).pack()

        # Zone résultats (texte monospace, fond sombre)
        result_box = tk.Text(frame, bg="#060e08", fg="#a8e6af",
                             font=("Consolas", 8),
                             relief="flat", bd=0,
                             width=60, height=min(12, results_text.count("\n") + 2),
                             state=tk.NORMAL, wrap=tk.WORD)
        result_box.insert("1.0", results_text)
        result_box.config(state=tk.DISABLED)
        result_box.pack(fill=tk.X, pady=(0, 4))

        # Séparateur
        tk.Frame(frame, bg="#1a3a1a", height=1).pack(fill=tk.X, pady=(2, 4))

        # Note MJ + bouton Continuer
        row_btns = tk.Frame(frame, bg="#0d1a10")
        row_btns.pack(fill=tk.X)

        tk.Label(row_btns, text="Modif. MJ :", bg="#0d1a10", fg="#888899",
                 font=("Arial", 8)).pack(side=tk.LEFT, padx=(0, 4))
        note_entry = tk.Entry(row_btns, bg="#0a160c", fg="#eeeeee",
                              font=("Consolas", 9), insertbackground="white",
                              relief="flat", width=32)
        note_entry.pack(side=tk.LEFT, padx=(0, 8), ipady=2)
        note_entry.focus_set()

        if mode in ("attack", "smite"):
            # ── Mode attaque / smite : Touché ✓ ou Raté ✗ ──────────────────
            def _hit(event=None):
                note = note_entry.get().strip()
                frame.destroy()
                _cleanup_header()
                lbl = "Touché ✅" if mode == "attack" else "Divine Smite appliqué ✅"
                self.append_message(
                    f"⚔️ MJ — {type_label}",
                    lbl + (f"  — {note}" if note else ""),
                    "#44aa44",
                )
                resume_callback(True, note)

            def _miss(event=None):
                note = note_entry.get().strip()
                frame.destroy()
                _cleanup_header()
                lbl = "Raté ❌" if mode == "attack" else "Divine Smite ignoré ❌"
                self.append_message(
                    f"⚔️ MJ — {type_label}",
                    lbl + (f"  — {note}" if note else ""),
                    "#aa4444",
                )
                resume_callback(False, note)

            note_entry.bind("<Return>", _hit)
            tk.Button(row_btns,
                      text="✓ Touché" if mode == "attack" else "✓ Appliquer",
                      bg="#0d2a0d", fg="#66ee66",
                      font=("Arial", 9, "bold"), relief="flat", padx=10, pady=3,
                      activebackground="#1a4a1a", command=_hit
                      ).pack(side=tk.LEFT, padx=(0, 4))
            tk.Button(row_btns,
                      text="✗ Raté" if mode == "attack" else "✗ Passer",
                      bg="#2a0d0d", fg="#ee6666",
                      font=("Arial", 9, "bold"), relief="flat", padx=10, pady=3,
                      activebackground="#4a1a1a", command=_miss
                      ).pack(side=tk.LEFT)
        else:
            # ── Mode dégâts / autre : simple Continuer ───────────────────────
            def _ok(event=None):
                note = note_entry.get().strip()
                frame.destroy()
                _cleanup_header()
                if note:
                    self.append_message(
                        f"✏️ MJ — {type_label}",
                        note,
                        "#aaaacc",
                    )
                resume_callback(note)

            note_entry.bind("<Return>", _ok)
            tk.Button(row_btns, text="▶ Continuer", bg="#0d2a0d", fg="#66ee66",
                      font=("Arial", 9, "bold"), relief="flat", padx=10, pady=3,
                      activebackground="#1a4a1a", command=_ok).pack(side=tk.LEFT)

        self.chat_display.window_create(tk.END, window=frame)
        self.chat_display.insert(tk.END, "\n")

        self.chat_display.tag_config(f"result_hdr_{n}",
                                      foreground=type_color,
                                      font=("Consolas", 9, "bold"))
        self.chat_display.config(state=tk.DISABLED)
        self.chat_display.see(tk.END)

        def _cleanup_header():
            try:
                self.chat_display.config(state=tk.NORMAL)
                ranges = self.chat_display.tag_ranges(f"result_hdr_{n}")
                if ranges:
                    ls = self.chat_display.index(f"{ranges[0]} linestart")
                    le = self.chat_display.index(f"{ranges[-1]} lineend +1c")
                    self.chat_display.delete(ls, le)
                self.chat_display.config(state=tk.DISABLED)
            except Exception:
                pass

    # ─── Widget de confirmation d'action inline ───────────────────────────────

    def _append_action_confirm(self, char_name: str, type_label: str,
                                intention: str, regle: str, cible: str,
                                resume_callback,
                                sub_index: int | None = None,
                                sub_total: int | None = None):
        """
        Affiche une carte de confirmation de sous-action dans le chat.
        Chaque attaque individuelle, action bonus, mouvement ou action gratuite
        reçoit sa propre carte séquentielle — le MJ confirme ou refuse chacune.

        type_label : ex. "Action — Attaque 1/2", "Action Bonus", "Mouvement"
        sub_index  : position 1-based dans la séquence (None si unique)
        sub_total  : nombre total de sous-actions dans la séquence
        resume_callback(confirmed: bool, mj_note: str)
        """
        color = self.CHAR_COLORS.get(char_name, "#aaaaaa")
        self.msg_counter += 1
        n = self.msg_counter
        tag_card = f"action_card_{n}"

        # Badge couleur selon le type d'action
        _TYPE_COLORS = {
            "action bonus": "#e67e22",
            "bonus":        "#e67e22",
            "réaction":     "#3498db",
            "reaction":     "#3498db",
            "mouvement":    "#27ae60",
            "move":         "#27ae60",
            "gratuite":     "#8e44ad",
            "free":         "#8e44ad",
        }
        type_low   = type_label.lower()
        type_color = next(
            (v for k, v in _TYPE_COLORS.items() if k in type_low),
            color,   # par défaut : couleur du personnage (Action normale)
        )

        counter_txt = ""
        if sub_index is not None and sub_total is not None and sub_total > 1:
            counter_txt = f"  [{sub_index}/{sub_total}]"

        self.chat_display.config(state=tk.NORMAL)

        # Ligne d'en-tête
        self.chat_display.insert(tk.END, "\n", tag_card)
        self.chat_display.insert(tk.END,
            f"⚔️ {type_label.upper()}{counter_txt} — {char_name}\n",
            f"action_hdr_{n}")

        # Cadre principal de la carte
        frame = tk.Frame(self.chat_display, bg="#12181a",
                         relief="flat", padx=8, pady=6,
                         highlightthickness=2,
                         highlightbackground=type_color)

        # Badge type coloré en haut
        badge_frame = tk.Frame(frame, bg=type_color)
        badge_frame.pack(anchor="w", pady=(0, 4))
        tk.Label(badge_frame, text=f" {type_label} ",
                 bg=type_color, fg="white",
                 font=("Consolas", 8, "bold"), padx=4).pack()

        # Ligne Intention
        row_i = tk.Frame(frame, bg="#12181a")
        row_i.pack(fill=tk.X, pady=1)
        tk.Label(row_i, text="Intention :", bg="#12181a", fg="#888899",
                 font=("Consolas", 8, "bold"), width=11, anchor="w").pack(side=tk.LEFT)
        tk.Label(row_i, text=intention, bg="#12181a", fg="#ddeeff",
                 font=("Consolas", 9), wraplength=380, justify=tk.LEFT,
                 anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Ligne Règle 5e (peut être multiligne pour Extra Attack)
        row_r = tk.Frame(frame, bg="#12181a")
        row_r.pack(fill=tk.X, pady=1)
        tk.Label(row_r, text="Règle 5e :", bg="#12181a", fg="#888899",
                 font=("Consolas", 8, "bold"), width=11, anchor="nw").pack(side=tk.LEFT, anchor="n")
        tk.Label(row_r, text=regle, bg="#12181a", fg=type_color,
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
            suffix = f" ({sub_index}/{sub_total})" if sub_index and sub_total and sub_total > 1 else ""
            self.append_message(
                f"✅ MJ → {char_name}",
                f"[{type_label}]{suffix} autorisé : {intention}" + (f"  — {note}" if note else ""),
                "#44aa44",
            )
            resume_callback(True, note)

        def _deny(event=None):
            note = note_entry.get().strip()
            frame.destroy()
            _cleanup_header()
            suffix = f" ({sub_index}/{sub_total})" if sub_index and sub_total and sub_total > 1 else ""
            self.append_message(
                f"❌ MJ → {char_name}",
                f"[{type_label}]{suffix} refusé : {intention}" + (f"  — {note}" if note else ""),
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
                                      foreground=type_color,
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
        """Supprime visuellement le message sélectionné (texte complet, y compris newlines)."""
        if self.selected_msg_id is None:
            return
        for i, msg in enumerate(self.messages_index):
            if msg["id"] == self.selected_msg_id:
                tag = msg["tag"]
                ranges = self.chat_display.tag_ranges(tag)
                if ranges:
                    self.chat_display.config(state=tk.NORMAL)
                    # Le message est inséré avec un \n initial hors du tag.
                    # On recule d'un caractère pour l'englober, mais seulement
                    # s'il y a bien un caractère avant (évite de déborder en 1.0).
                    start = str(ranges[0])
                    try:
                        prev = self.chat_display.index(f"{start} -1c")
                        if self.chat_display.compare(prev, ">=", "1.0"):
                            start = prev
                    except Exception:
                        pass
                    # Prolonger jusqu'après le \n final
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
    # ─── Mémoire persistante : détection *mots-clés* ─────────────────────────

    def _check_and_update_memories(self, keywords: list, full_text: str):
        """
        Appelé dans un thread daemon quand le MJ écrit *mot-clé* dans le chat.

        Pour chaque mot-clé :
          1. Cherche dans les mémoires existantes par titre ou tag.
          2. Si trouvé ET que le message apporte de nouvelles infos → met à jour via Claude.
          3. Si non trouvé → crée une nouvelle mémoire avec catégorie/contenu détectés par l'IA.

        Notifie le chat (thread-safe) du résultat.
        """
        import json, os, requests
        from state_manager import (
            get_memories, add_memory, update_memory,
            MEMORY_CATEGORIES,
        )

        def _call_claude(prompt):
            """Appel à l'API Anthropic (claude-haiku) pour classification/résumé."""
            try:
                api_key = os.getenv("ANTHROPIC_API_KEY", "")
                if not api_key:
                    try:
                        from dotenv import dotenv_values
                        api_key = dotenv_values().get("ANTHROPIC_API_KEY", "")
                    except Exception:
                        pass
                if not api_key:
                    return ""
                resp = requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 400,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    timeout=15,
                )
                data = resp.json()
                return data.get("content", [{}])[0].get("text", "").strip()
            except Exception as e:
                print(f"[Memory] Erreur API Claude : {e}")
                return ""

        existing = get_memories(importance_min=1, visible_only=False)
        updated_ids = []
        created_titles = []

        for kw in keywords:
            kw_clean = kw.strip()
            kw_lower = kw_clean.lower()
            if not kw_clean:
                continue

            # ── 1. Recherche d'une mémoire existante ──────────────────────
            match = None
            for m in existing:
                if m["titre"].lower() == kw_lower:
                    match = m
                    break
            if not match:
                for m in existing:
                    if kw_lower in m["titre"].lower() or m["titre"].lower() in kw_lower:
                        match = m
                        break
            if not match:
                for m in existing:
                    for tag in m.get("tags", []):
                        if len(tag) >= 3 and tag.lower() == kw_lower:
                            match = m
                            break
                    if match:
                        break

            if match:
                # ── 2. Vérifier si le message apporte de nouvelles infos ──
                prompt_update = (
                    f"Tu es un assistant pour une campagne D&D. "
                    f"Voici ce que nous savons déjà sur '{match['titre']}' :\n"
                    f"{match['contenu']}\n\n"
                    f"Le MJ vient de mentionner '*{kw_clean}*' dans ce message :\n"
                    f"\"{full_text}\"\n\n"
                    f"Y a-t-il dans ce message des informations NOUVELLES sur '{match['titre']}' "
                    f"qui ne sont pas déjà dans la mémoire ? "
                    f"Si oui, réponds avec un JSON UNIQUEMENT (sans backticks ni markdown) : "
                    f"{{\"new_info\": true, \"updated_content\": \"<contenu complet mis a jour>\", "
                    f"\"updated_tags\": [\"tag1\",\"tag2\"], \"importance\": 1}}\n"
                    f"Utilise 1, 2 ou 3 pour l'importance (1=mineur, 2=notable, 3=critique).\n"
                    f"Si non, réponds exactement : {{\"new_info\": false}}"
                )
                result = _call_claude(prompt_update)
                try:
                    data = json.loads(result)
                    if data.get("new_info"):
                        update_memory(
                            match["id"],
                            contenu=data.get("updated_content", match["contenu"]),
                            tags=data.get("updated_tags", match.get("tags", [])),
                            importance=int(data.get("importance", match.get("importance", 2))),
                        )
                        existing = get_memories(importance_min=1, visible_only=False)
                        updated_ids.append(match["titre"])
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
            else:
                # ── 3. Créer une nouvelle mémoire ─────────────────────────
                cats_list = ", ".join(MEMORY_CATEGORIES.keys())
                prompt_create = (
                    f"Tu es un assistant pour une campagne D&D. "
                    f"Le MJ vient de mentionner '*{kw_clean}*' dans ce message :\n"
                    f"\"{full_text}\"\n\n"
                    f"Crée une fiche mémoire pour '{kw_clean}'. "
                    f"Catégories disponibles : {cats_list}.\n"
                    f"Réponds avec un JSON UNIQUEMENT (sans backticks ni markdown) :\n"
                    f"{{\"categorie\": \"<cat>\", \"titre\": \"<titre precis>\", "
                    f"\"contenu\": \"<description concise 1-3 phrases>\", "
                    f"\"tags\": [\"tag1\",\"tag2\",\"tag3\"], \"importance\": 1}}\n"
                    f"Utilise 1, 2 ou 3 pour l'importance."
                )
                result = _call_claude(prompt_create)
                try:
                    data = json.loads(result)
                    cat = data.get("categorie", "evenement")
                    if cat not in MEMORY_CATEGORIES:
                        cat = "evenement"
                    add_memory(
                        categorie=cat,
                        titre=data.get("titre", kw_clean),
                        contenu=data.get("contenu", full_text[:200]),
                        tags=data.get("tags", [kw_clean]),
                        importance=int(data.get("importance", 2)),
                    )
                    existing = get_memories(importance_min=1, visible_only=False)
                    created_titles.append(data.get("titre", kw_clean))
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    print(f"[Memory] Erreur création mémoire '{kw_clean}': {e}")

        # ── Notification dans le chat ──────────────────────────────────────
        parts = []
        if updated_ids:
            parts.append(f"Mises à jour : {', '.join(updated_ids)}")
        if created_titles:
            parts.append(f"Nouvelles entrées : {', '.join(created_titles)}")
        if parts:
            self.msg_queue.put({
                "sender": "📌 Mémoire",
                "text":   " | ".join(parts),
                "color":  "#888844",
            })