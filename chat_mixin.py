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
from chat_log_writer import strip_mechanical_blocks


class ChatMixin:
    """Mixin pour DnDApp — panneau de chat, file audio, interactions utilisateur."""

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
        self.root.after_idle(lambda: self.chat_display.yview_moveto(1.0))

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
                elif action == "map_pointer":
                    self._append_map_pointer(
                        msg.get("img_bytes"),
                        msg.get("comment", ""),
                        msg.get("sender", "🗺️ MJ"),
                    )
                elif action == "map_pointer_broadcast":
                    # Diffusion image + commentaire aux agents joueurs (hors thread Tk)
                    import threading as _th_ptr
                    _th_ptr.Thread(
                        target=self._broadcast_pointer_image,
                        args=(
                            msg.get("img_bytes"),
                            msg.get("comment", ""),
                            msg.get("col", 0),
                            msg.get("row", 0),
                            msg.get("map_name", ""),
                            msg.get("notes_txt", ""),
                        ),
                        daemon=True,
                    ).start()
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
                        chain_abort_callback=msg.get("chain_abort_callback"),
                    )
                elif action == "skill_check_confirm":
                    try:
                        self._append_skill_check_confirm(
                            msg["char_name"],
                            msg["skill_label"],
                            msg.get("stat_label", ""),
                            msg.get("bonus", 0),
                            msg.get("dc"),
                            msg.get("has_advantage", False),
                            msg.get("has_disadvantage", False),
                            msg["resume_callback"],
                        )
                    except Exception as _e_sc:
                        import traceback as _tb_sc
                        print(f"[process_queue] Erreur skill_check_confirm : {_e_sc}")
                        _tb_sc.print_exc()
                        # Débloquer le thread AutoGen si l'UI a planté
                        try:
                            msg["resume_callback"](False, 0, "")
                        except Exception:
                            pass
                elif action == "tool_confirm":
                    self._append_tool_confirm_link(
                        msg["sender"],
                        msg["tool_name"],
                        msg.get("tool_args", {}),
                        msg["resume_callback"],
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
                elif action == "npc_turn_tools":
                    self._append_npc_turn_tools(
                        msg["combatant"],
                        msg["monster"],
                        msg["targets"],
                    )
                elif action == "damage_link":
                    self._handle_damage_link(msg)
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
        if (text.strip().startswith("[MISE À JOUR CARTE") 
            or "═══ CARTE DE COMBAT" in text.strip()
            or text.strip().startswith("[RÉSULTAT SYSTÈME")
            or text.strip().startswith("[TOUR EN COURS")
            or text.strip().startswith("[COMBO INTERDIT")
            or text.strip().startswith("[SYSTÈME — HORS TOUR")
            or "tool_use_failed" in text
            or "Tentative de récupération" in text
            or "PARAMÈTRE INVALIDE" in text
            or "DIRECTIVE SYSTÈME" in text):
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
            self.chat_display.insert(tk.END, f"]: {text}\n", tag_name)

        self.chat_display.tag_config(tag_name,   foreground=color)
        self.chat_display.tag_config(tag_sender, foreground=color,
                                     font=("Consolas", 11, "bold"),
                                     underline=False)

        self.chat_display.config(state=tk.DISABLED)
        self.root.after_idle(lambda: self.chat_display.yview_moveto(1.0))

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

    # ─── Purge des anciens messages (anti-explosion de tags Tk) ──────────────

    def _purge_oldest_messages(self, n: int):
        """
        Supprime les n plus anciens messages du widget Text et libère leurs tags.

        La table interne de tags Tcl grossit à chaque append_message (au moins
        2 tags permanents par message, jusqu'à 6 avec sorts cliquables).
        Au-delà de ~600 tags, see() / search() parcourent un B-tree si grand
        qu'ils corrompent un pointeur interne → segfault non récupérable.
        tag_delete() réduit effectivement cette table.
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
        self.chat_display.insert(
            tk.END,
            f"[{sender}]",
            (tag_name, f"sender_{self.msg_counter}"))
        self.chat_display.tag_config(
            f"sender_{self.msg_counter}",
            foreground="#ff8a80",
            font=("Consolas", 11, "bold"))

        if comment:
            self.chat_display.insert(tk.END, f"\n{comment}\n", tag_name)
        else:
            self.chat_display.insert(tk.END, "\n", tag_name)

        self.chat_display.tag_config(tag_name, foreground="#ff8a80")

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
                        (MAX_W, int(ih * ratio)), _PilImage.LANCZOS)

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
        self.root.after_idle(lambda: self.chat_display.yview_moveto(1.0))

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
                    (int(iw * ratio), int(ih * ratio)), _PI.LANCZOS)
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
            tts_relay = strip_mechanical_blocks(reply_text)
            if tts_relay:
                self.audio_queue.put((tts_relay, char_name))
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
        self.root.after_idle(lambda: self.chat_display.yview_moveto(1.0))

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

        # Hyperlien : clic sur le nom → fiche du sort
        self.chat_display.tag_bind(
            tag_header, "<Button-1>",
            lambda _e, n=spell_name: self._open_spell_sheet(n),
        )
        self.chat_display.tag_bind(
            tag_header, "<Enter>",
            lambda _e: self.chat_display.config(cursor="hand2"),
        )
        self.chat_display.tag_bind(
            tag_header, "<Leave>",
            lambda _e: self.chat_display.config(cursor=""),
        )

        frame = tk.Frame(self.chat_display, bg="#1a1a2e", pady=3, padx=6)

        tk.Label(frame, text="Niveau :", bg="#1a1a2e", fg="#aaaaaa",
                 font=("Arial", 8)).pack(side=tk.LEFT, padx=(0, 4))

        spx = tk.Spinbox(frame, from_=spell_level, to=9, width=2, textvariable=level_var,
                         bg="#2a2a3e", fg=color, font=("Consolas", 9, "bold"),
                         buttonbackground="#2a2a3e", relief="flat",
                         highlightthickness=1, highlightcolor=color)
        spx.pack(side=tk.LEFT, padx=(0, 8))

        confirmed = [False]

        def _confirm():
            confirmed[0] = True
            lvl = level_var.get()
            # Un slot doit être >= au niveau minimum du sort
            if lvl < spell_level:
                self.append_message(
                    "⚠️ Sort invalide",
                    f"{spell_name} requiert un slot de niveau {spell_level} minimum "
                    f"(slot niv.{lvl} sélectionné — annulé).",
                    "#cc8800",
                )
                resume_callback(False, spell_level)
                frame.destroy()
                _remove_spell_lines()
                return
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
                                      font=("Arial", 9, "bold", "underline"))
        self.chat_display.config(state=tk.DISABLED)
        def _force_scroll():
            try:
                self.chat_display.update_idletasks()
                self.chat_display.yview_moveto(1.0)
            except Exception: pass
        self.chat_display.after(50, _force_scroll)
        self.chat_display.after(250, _force_scroll)

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
        mode="healing" → bouton 💚 Appliquer soin
                         resume_callback(mj_note: str)
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
        # Couleur de cadre selon le mode
        if mode == "healing":
            type_color = "#27ae60"  # vert soin
        elif mode == "save":
            type_color = "#3498db"  # bleu sauvegarde
        else:
            type_color = next(
                (v for k, v in _TYPE_COLORS.items() if k in type_low),
                color,
            )

        # Couleur de fond selon le mode
        _bg_color = "#0a1a10" if mode == "healing" else "#0a0e1a" if mode == "save" else "#0d1a10"

        _hdr_icon = "💚" if mode == "healing" else "🛡️" if mode == "save" else "🎲"
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.insert(tk.END, "\n")
        self.chat_display.insert(tk.END,
            f"{_hdr_icon} RÉSULTATS — {type_label.upper()} — {char_name}\n",
            f"result_hdr_{n}")

        frame = tk.Frame(self.chat_display, bg=_bg_color,
                         relief="flat", padx=8, pady=6,
                         highlightthickness=2,
                         highlightbackground=type_color)

        # Badge type + libellé selon le mode
        _mode_labels = {
            "attack":  f" 🎯 {type_label} — jet d'attaque ",
            "smite":   f" ✨ Divine Smite — appliquer ? ",
            "damage":  f" 🎲 {type_label} — dégâts ",
            "healing": f" 💚 {type_label} — soin ",
            "save":    f" 🛡️ {type_label} — jet de sauvegarde ",
        }
        badge_text = _mode_labels.get(mode, f" 🎲 {type_label} — résultats ")
        badge = tk.Frame(frame, bg=type_color)
        badge.pack(anchor="w", pady=(0, 4))
        tk.Label(badge, text=badge_text,
                 bg=type_color, fg="white",
                 font=("Consolas", 8, "bold"), padx=4).pack()

        # Zone résultats (texte monospace, fond sombre)
        _result_fg = "#88eebb" if mode == "healing" else "#88bbee" if mode == "save" else "#a8e6af"
        result_box = tk.Text(frame, bg="#060e08", fg=_result_fg,
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

        # ── Filet de sécurité anti-lockdown ──────────────────────────────────
        # Si le frame est détruit sans qu'aucun bouton ait été pressé
        # (Annuler externe, fermeture de fenêtre…), on appelle resume_callback
        # avec les valeurs neutres appropriées pour débloquer le chat.
        _callback_done = [False]

        def _safe_resume_on_destroy(event=None):
            if _callback_done[0]:
                return
            _callback_done[0] = True
            _cleanup_header()
            if mode in ("attack", "smite"):
                resume_callback(False, "")   # Annulé → raté / ignoré
            elif mode == "save":
                resume_callback(True, "")    # Annulé → sauvegarde réussie (neutre)
            else:
                resume_callback("")          # Annulé → 0 modif (damage / healing)

        frame.bind("<Destroy>", _safe_resume_on_destroy)

        if mode in ("attack", "smite"):
            # ── Mode attaque / smite : Touché ✓ ou Raté ✗ ──────────────────
            def _hit(event=None):
                if _callback_done[0]:
                    return
                _callback_done[0] = True
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
                if _callback_done[0]:
                    return
                _callback_done[0] = True
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
        elif mode == "healing":
            # ── Mode soin : bouton Appliquer soin ─────────────────────────
            def _apply_heal(event=None):
                if _callback_done[0]:
                    return
                _callback_done[0] = True
                note = note_entry.get().strip()
                frame.destroy()
                _cleanup_header()
                self.append_message(
                    f"💚 Soin — {type_label}",
                    f"Soin appliqué" + (f"  — {note}" if note else ""),
                    "#44cc44",
                )
                resume_callback(note)

            note_entry.bind("<Return>", _apply_heal)
            tk.Button(row_btns, text="💚 Appliquer soin", bg="#0d2a0d", fg="#66ee66",
                      font=("Arial", 9, "bold"), relief="flat", padx=10, pady=3,
                      activebackground="#1a4a1a", command=_apply_heal).pack(side=tk.LEFT)

        elif mode == "save":
            # ── Mode sauvegarde : Sauvegarde réussie / Sauvegarde ratée ──────
            def _save_success(event=None):
                if _callback_done[0]:
                    return
                _callback_done[0] = True
                note = note_entry.get().strip()
                frame.destroy()
                _cleanup_header()
                self.append_message(
                    f"🛡️ MJ — {type_label}",
                    "Sauvegarde RÉUSSIE ✅ (sort raté)"
                    + (f"  — {note}" if note else ""),
                    "#4488cc",
                )
                resume_callback(True, note)   # True = cible a réussi → sort raté

            def _save_failure(event=None):
                if _callback_done[0]:
                    return
                _callback_done[0] = True
                note = note_entry.get().strip()
                frame.destroy()
                _cleanup_header()
                self.append_message(
                    f"💥 MJ — {type_label}",
                    "Sauvegarde RATÉE ❌ (sort touché)"
                    + (f"  — {note}" if note else ""),
                    "#cc4444",
                )
                resume_callback(False, note)  # False = cible a raté → sort touché

            note_entry.bind("<Return>", _save_failure)
            tk.Button(
                row_btns,
                text="🛡️ Sauvegarde réussie (sort raté)",
                bg="#0d1022", fg="#88bbee",
                font=("Arial", 9, "bold"), relief="flat", padx=10, pady=3,
                activebackground="#1a1f3a", cursor="hand2",
                command=_save_success,
            ).pack(side=tk.LEFT, padx=(0, 6))
            tk.Button(
                row_btns,
                text="💥 Sauvegarde ratée (sort touché)",
                bg="#2a0d0d", fg="#ee6666",
                font=("Arial", 9, "bold"), relief="flat", padx=10, pady=3,
                activebackground="#4a1a1a", cursor="hand2",
                command=_save_failure,
            ).pack(side=tk.LEFT)
        else:
            # ── Mode dégâts / autre : Continuer + Annuler ────────────────────
            def _ok(event=None):
                if _callback_done[0]:
                    return
                _callback_done[0] = True
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

            def _cancel(event=None):
                if _callback_done[0]:
                    return
                _callback_done[0] = True
                frame.destroy()
                _cleanup_header()
                # Annuler = 0 dégâts (note vide → pas de modification)
                resume_callback("")

            note_entry.bind("<Return>", _ok)
            tk.Button(row_btns, text="▶ Continuer", bg="#0d2a0d", fg="#66ee66",
                      font=("Arial", 9, "bold"), relief="flat", padx=10, pady=3,
                      activebackground="#1a4a1a", command=_ok).pack(side=tk.LEFT)
            tk.Button(row_btns, text="✗ Annuler", bg="#2a0d0d", fg="#ee6666",
                      font=("Arial", 9, "bold"), relief="flat", padx=10, pady=3,
                      activebackground="#4a1a1a", command=_cancel).pack(side=tk.LEFT, padx=(6, 0))

        self.chat_display.window_create(tk.END, window=frame)
        self.chat_display.insert(tk.END, "\n")

        self.chat_display.tag_config(f"result_hdr_{n}",
                                      foreground=type_color,
                                      font=("Consolas", 9, "bold"))
        self.chat_display.config(state=tk.DISABLED)
        def _force_scroll():
            try:
                self.chat_display.update_idletasks()
                self.chat_display.yview_moveto(1.0)
            except Exception: pass
        self.chat_display.after(50, _force_scroll)
        self.chat_display.after(250, _force_scroll)

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
                                sub_total: int | None = None,
                                chain_abort_callback=None):
        """
        Affiche une carte de confirmation de sous-action dans le chat.
        Chaque attaque individuelle, action bonus, mouvement ou action gratuite
        reçoit sa propre carte séquentielle — le MJ confirme ou refuse chacune.

        type_label          : ex. "Action — Attaque 1/2", "Action Bonus", "Mouvement"
        sub_index           : position 1-based dans la séquence (None si unique)
        sub_total           : nombre total de sous-actions dans la séquence
        resume_callback(confirmed: bool, mj_note: str)
        chain_abort_callback: appelé SANS argument quand le MJ refuse, pour
                              détruire toutes les cartes restantes de la chaîne.
                              None si l'action est isolée (aucune chaîne active).
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
        regle_widget = self._make_regle_with_links(row_r, regle, type_color, "#12181a")
        regle_widget.pack(side=tk.LEFT, fill=tk.X, expand=True)

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
            # ── Abandon de chaîne ────────────────────────────────────────────
            # On appelle chain_abort_callback AVANT resume_callback pour que
            # les cartes restantes soient détruites avant que le thread de jeu
            # ne reçoive le signal de refus.
            if chain_abort_callback is not None:
                try:
                    chain_abort_callback()
                except Exception as _cae:
                    print(f"[chain_abort] Erreur : {_cae}")
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
        # Différer le scroll : window_create insère le frame à hauteur 0 tant que
        # Tk n'a pas calculé le wrapping des Labels (wraplength=380). Un after(50)
        # laisse suffisamment de temps au geometry manager pour finaliser la taille
        # du frame avant de scroller — sinon see(tk.END) arrive trop tôt et la
        # carte d'action reste hors de la vue.
        def _force_scroll():
            try:
                self.chat_display.update_idletasks()
                self.chat_display.yview_moveto(1.0)
            except Exception: pass
        self.chat_display.after(50, _force_scroll)
        self.chat_display.after(250, _force_scroll)

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

    # ─── Widget de confirmation de jet de compétence/sauvegarde ──────────────

    def _append_skill_check_confirm(self, char_name: str, skill_label: str,
                                     stat_label: str, bonus: int,
                                     dc, has_advantage: bool, has_disadvantage: bool,
                                     resume_callback):
        """
        Boîte de jet de compétence ou de sauvegarde.
        Affiche 2d20 (avec sélection Avantage/Normal/Désavantage),
        permet au MJ d'ajuster le bonus et de confirmer ou refuser.

        resume_callback(confirmed: bool, total: int, mj_note: str)
        """
        import random as _rnd

        color  = self.CHAR_COLORS.get(char_name, "#aaaaaa")
        self.msg_counter += 1
        n = self.msg_counter

        BG      = "#07101e"
        BG2     = "#0c1928"
        ACCENT  = "#2a6492"
        FG      = "#b8d8f0"
        FG_DIM  = "#4a6878"

        # ── Tirage initial ───────────────────────────────────────────────────
        r1_init, r2_init = _rnd.randint(1, 20), _rnd.randint(1, 20)
        roll_vars  = [tk.IntVar(value=r1_init), tk.IntVar(value=r2_init)]
        adv_var    = tk.StringVar(value=(
            "avantage"    if has_advantage    else
            "désavantage" if has_disadvantage else
            "normal"
        ))
        bonus_var  = tk.IntVar(value=bonus)

        # ── En-tête dans le chat ─────────────────────────────────────────────
        hdr_tag  = f"skill_hdr_{n}"
        stat_part = f" ({stat_label})" if stat_label else ""
        dc_part   = f"  — DC {dc}" if dc else ""
        badge_txt = (
            " 🛡️ Jet de sauvegarde " if "sauvegarde" in skill_label.lower()
            else " 🎲 Jet de compétence "
        )

        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.insert(tk.END, "\n")
        self.chat_display.insert(
            tk.END,
            f"🎲 JET — {skill_label.upper()}{stat_part} — {char_name}{dc_part}\n",
            hdr_tag,
        )
        self.chat_display.tag_config(hdr_tag, foreground=ACCENT,
                                      font=("Consolas", 9, "bold"))

        # ── Cadre principal ──────────────────────────────────────────────────
        frame = tk.Frame(self.chat_display, bg=BG,
                         relief="flat", padx=10, pady=8,
                         highlightthickness=2, highlightbackground=ACCENT)

        # Badge type
        badge = tk.Frame(frame, bg=ACCENT)
        badge.pack(anchor="w", pady=(0, 6))
        tk.Label(badge, text=badge_txt, bg=ACCENT, fg="white",
                 font=("Consolas", 8, "bold"), padx=4).pack()

        # ── Ligne bonus ──────────────────────────────────────────────────────
        row_bonus = tk.Frame(frame, bg=BG)
        row_bonus.pack(fill=tk.X, pady=(0, 4))
        sign0 = "+" if bonus >= 0 else ""
        tk.Label(row_bonus, text="Bonus base :", bg=BG, fg=FG_DIM,
                 font=("Consolas", 8), width=13, anchor="w").pack(side=tk.LEFT)
        tk.Label(row_bonus, text=f"{sign0}{bonus}", bg=BG, fg=color,
                 font=("Consolas", 10, "bold")).pack(side=tk.LEFT, padx=(0, 16))
        tk.Label(row_bonus, text="Modif MJ :", bg=BG, fg=FG_DIM,
                 font=("Consolas", 8)).pack(side=tk.LEFT)

        def _update_display(*_):
            """Recalcule le résultat et met à jour les labels."""
            r1, r2   = roll_vars[0].get(), roll_vars[1].get()
            mode     = adv_var.get()
            raw      = max(r1, r2) if mode == "avantage" else min(r1, r2) if mode == "désavantage" else r1
            total    = raw + bonus_var.get()
            sgn      = "+" if bonus_var.get() >= 0 else ""
            crit_tag = " 🎯 CRITIQUE!" if raw == 20 else " ☠ FUMBLE" if raw == 1 else ""
            dc_tag   = ""
            if dc:
                try:
                    dc_tag = f"  {'✅' if total >= int(dc) else '❌'} DC {dc}"
                except Exception:
                    pass
            result_var.set(f"d20({raw}) {sgn}{bonus_var.get()} = {total}{crit_tag}{dc_tag}")
            # Couleur du résultat
            if raw == 20:
                result_lbl.config(fg="#88ff88")
            elif raw == 1:
                result_lbl.config(fg="#ff6666")
            elif dc:
                try:
                    result_lbl.config(fg="#88ddff" if total >= int(dc) else "#ff9966")
                except Exception:
                    result_lbl.config(fg=FG)
            else:
                result_lbl.config(fg=FG)
            # Highlight des dés
            if mode == "normal":
                lbl_r1.config(fg="#ffee44", bg="#0d2030")
                lbl_r2.config(fg=FG_DIM,   bg=BG2)
            elif mode == "avantage":
                if r1 >= r2:
                    lbl_r1.config(fg="#88ff88", bg="#0d2030")
                    lbl_r2.config(fg=FG_DIM,   bg=BG2)
                else:
                    lbl_r1.config(fg=FG_DIM,   bg=BG2)
                    lbl_r2.config(fg="#88ff88", bg="#0d2030")
            else:  # désavantage
                if r1 <= r2:
                    lbl_r1.config(fg="#ff8866", bg="#0d2030")
                    lbl_r2.config(fg=FG_DIM,   bg=BG2)
                else:
                    lbl_r1.config(fg=FG_DIM,   bg=BG2)
                    lbl_r2.config(fg="#ff8866", bg="#0d2030")

        bonus_spx = tk.Spinbox(row_bonus, from_=-20, to=20, width=4,
                                textvariable=bonus_var,
                                bg="#142030", fg=FG, font=("Consolas", 9, "bold"),
                                buttonbackground="#142030", relief="flat",
                                highlightthickness=1, highlightcolor=ACCENT,
                                command=_update_display)
        bonus_spx.pack(side=tk.LEFT, padx=(4, 0))
        bonus_spx.bind("<KeyRelease>", _update_display)

        # ── Ligne des dés ────────────────────────────────────────────────────
        row_dice = tk.Frame(frame, bg=BG)
        row_dice.pack(fill=tk.X, pady=(6, 4))
        tk.Label(row_dice, text="Dés :", bg=BG, fg=FG_DIM,
                 font=("Consolas", 8), width=13, anchor="w").pack(side=tk.LEFT)

        lbl_r1 = tk.Label(row_dice, text=f"[{r1_init}]",
                           bg="#0d2030", fg="#ffee44",
                           font=("Consolas", 14, "bold"),
                           padx=8, pady=3, relief="flat",
                           highlightthickness=1, highlightbackground="#2a4060")
        lbl_r1.pack(side=tk.LEFT, padx=(0, 6))

        lbl_r2 = tk.Label(row_dice, text=f"[{r2_init}]",
                           bg=BG2, fg=FG_DIM,
                           font=("Consolas", 14, "bold"),
                           padx=8, pady=3, relief="flat",
                           highlightthickness=1, highlightbackground="#1a3050")
        lbl_r2.pack(side=tk.LEFT, padx=(0, 12))

        def _reroll(*_):
            roll_vars[0].set(_rnd.randint(1, 20))
            roll_vars[1].set(_rnd.randint(1, 20))
            lbl_r1.config(text=f"[{roll_vars[0].get()}]")
            lbl_r2.config(text=f"[{roll_vars[1].get()}]")
            _update_display()

        tk.Button(row_dice, text="🎲 Relancer", bg="#142030", fg="#66aadd",
                  font=("Arial", 8), relief="flat", padx=8, pady=2,
                  activebackground="#1e3048", cursor="hand2",
                  command=_reroll).pack(side=tk.LEFT)

        # ── Mode Avantage/Normal/Désavantage ─────────────────────────────────
        row_adv = tk.Frame(frame, bg=BG)
        row_adv.pack(fill=tk.X, pady=(2, 6))
        tk.Label(row_adv, text="Mode :", bg=BG, fg=FG_DIM,
                 font=("Consolas", 8), width=13, anchor="w").pack(side=tk.LEFT)

        for _mode_val, _mode_txt, _mode_fg in [
            ("désavantage", "⬇ Désav.",  "#ff8866"),
            ("normal",      "◈ Normal",   FG),
            ("avantage",    "⬆ Avant.",   "#88ff88"),
        ]:
            tk.Radiobutton(
                row_adv, text=_mode_txt, variable=adv_var, value=_mode_val,
                bg=BG, fg=_mode_fg, activebackground=BG, selectcolor=BG,
                font=("Arial", 8, "bold"), command=_update_display,
            ).pack(side=tk.LEFT, padx=(0, 8))

        # ── Ligne résultat ───────────────────────────────────────────────────
        tk.Frame(frame, bg="#1a3050", height=1).pack(fill=tk.X, pady=(4, 4))
        result_var = tk.StringVar()
        row_result = tk.Frame(frame, bg=BG)
        row_result.pack(fill=tk.X, pady=(0, 6))
        tk.Label(row_result, text="Résultat :", bg=BG, fg=FG_DIM,
                 font=("Consolas", 8), width=13, anchor="w").pack(side=tk.LEFT)
        result_lbl = tk.Label(row_result, textvariable=result_var,
                               bg=BG, fg=FG,
                               font=("Consolas", 11, "bold"))
        result_lbl.pack(side=tk.LEFT)

        # ── Note MJ ──────────────────────────────────────────────────────────
        row_note = tk.Frame(frame, bg=BG)
        row_note.pack(fill=tk.X, pady=(0, 6))
        tk.Label(row_note, text="Note MJ :", bg=BG, fg=FG_DIM,
                 font=("Arial", 8), width=13, anchor="w").pack(side=tk.LEFT)
        note_entry = tk.Entry(row_note, bg="#0d1828", fg=FG,
                              font=("Consolas", 9), insertbackground="white",
                              relief="flat", width=34)
        note_entry.pack(side=tk.LEFT, padx=(4, 0), ipady=2)
        note_entry.focus_set()

        # ── Filet anti-lockdown ───────────────────────────────────────────────
        # NOTE : _cleanup_hdr est défini ICI (avant _safe_destroy) pour éviter
        # UnboundLocalError si la frame est détruite avant la fin de la fonction.
        def _cleanup_hdr():
            try:
                self.chat_display.config(state=tk.NORMAL)
                ranges = self.chat_display.tag_ranges(hdr_tag)
                if ranges:
                    ls = self.chat_display.index(f"{ranges[0]} linestart")
                    le = self.chat_display.index(f"{ranges[-1]} lineend +1c")
                    self.chat_display.delete(ls, le)
                self.chat_display.config(state=tk.DISABLED)
            except Exception:
                pass

        _callback_done = [False]

        def _safe_destroy(event=None):
            if _callback_done[0]:
                return
            _callback_done[0] = True
            _cleanup_hdr()
            resume_callback(False, 0, "")

        frame.bind("<Destroy>", _safe_destroy)

        # ── Confirmer ────────────────────────────────────────────────────────
        def _confirm(event=None):
            if _callback_done[0]:
                return
            _callback_done[0] = True
            # Capturer AVANT destroy
            r1v    = roll_vars[0].get()
            r2v    = roll_vars[1].get()
            mode_v = adv_var.get()
            bon_v  = bonus_var.get()
            note_v = note_entry.get().strip()
            raw_v  = max(r1v, r2v) if mode_v == "avantage" else min(r1v, r2v) if mode_v == "désavantage" else r1v
            tot_v  = raw_v + bon_v
            frame.destroy()
            _cleanup_hdr()
            sgn_   = "+" if bon_v >= 0 else ""
            crit_  = " 🎯 CRITIQUE!" if raw_v == 20 else " ☠ FUMBLE" if raw_v == 1 else ""
            dc_r_  = ""
            if dc:
                try:
                    dc_r_ = f"  {'✅' if tot_v >= int(dc) else '❌'} DC {dc}"
                except Exception:
                    pass
            self.append_message(
                f"🎲 {char_name}",
                f"[{skill_label}] d20({raw_v}) {sgn_}{bon_v} = {tot_v}{crit_}{dc_r_}"
                + (f"  — {note_v}" if note_v else ""),
                "#88ccff",
            )
            resume_callback(True, tot_v, note_v)

        # ── Refuser ──────────────────────────────────────────────────────────
        def _deny(event=None):
            if _callback_done[0]:
                return
            _callback_done[0] = True
            frame.destroy()
            _cleanup_hdr()
            self.append_message(
                f"❌ MJ — {skill_label}",
                f"Jet de {char_name} refusé.",
                "#cc4444",
            )
            resume_callback(False, 0, "")

        note_entry.bind("<Return>", _confirm)

        row_btns = tk.Frame(frame, bg=BG)
        row_btns.pack(fill=tk.X)
        tk.Button(row_btns, text="✓ Confirmer", bg="#0d2a1a", fg="#66ee88",
                  font=("Arial", 9, "bold"), relief="flat", padx=10, pady=3,
                  activebackground="#1a4a2a", cursor="hand2",
                  command=_confirm).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(row_btns, text="✗ Refuser", bg="#2a0d0d", fg="#ee6666",
                  font=("Arial", 9, "bold"), relief="flat", padx=10, pady=3,
                  activebackground="#4a1a1a", cursor="hand2",
                  command=_deny).pack(side=tk.LEFT)

        self.chat_display.window_create(tk.END, window=frame)
        self.chat_display.insert(tk.END, "\n")
        self.chat_display.config(state=tk.DISABLED)

        def _force_scroll():
            try:
                self.chat_display.update_idletasks()
                self.chat_display.yview_moveto(1.0)
            except Exception:
                pass
        self.chat_display.after(50, _force_scroll)
        self.chat_display.after(250, _force_scroll)

        # Mettre à jour le die-2 label et déclencher l'affichage initial
        lbl_r2.config(text=f"[{r2_init}]")
        _update_display()

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
        import json, os
        from state_manager import (
            get_memories, add_memory, update_memory,
            MEMORY_CATEGORIES,
        )

        def _call_claude(prompt):
            """Appel LLM pour classification/résumé mémoire.
            Utilise le même fournisseur que le reste de l'app (build_llm_config),
            plus besoin d'une ANTHROPIC_API_KEY séparée.
            """
            import re as _re
            try:
                import autogen as _ag
                from llm_config import build_llm_config, _default_model
                from app_config import get_chronicler_config

                # Utilise le modèle du Chroniqueur (léger et rapide)
                _chron = get_chronicler_config()
                _model = _chron.get("model", _default_model)
                _cfg   = build_llm_config(_model, temperature=0.2)
                client = _ag.OpenAIWrapper(config_list=_cfg["config_list"])

                response = client.create(messages=[
                    {"role": "user", "content": prompt}
                ])
                raw = (response.choices[0].message.content or "").strip()

                # Nettoyer les fences markdown si le modèle les ajoute quand même
                raw = _re.sub(r"^```(?:json)?\s*", "", raw)
                raw = _re.sub(r"\s*```$", "", raw.strip())
                return raw.strip()

            except Exception as e:
                print(f"[Memory] Erreur LLM mémoire : {e}")
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

    # ─── Outils MJ : tour du PNJ ─────────────────────────────────────────────

    def _append_npc_turn_tools(self, combatant, monster: dict, targets: list):
        """
        Insère dans le chat un bloc interactif avec les outils MJ du tour :
          • Sélecteur de cible (dropdown)
          • Attaques cliquables (jet d'attaque + jets de dégâts)
          • DD / jets de sauvegarde
          • Actions, bonus, réactions, légendaires
          • Traits (résumé)
          • Vitesse, CA, FP
        Appelé via process_queue (action = "npc_turn_tools").
        """
        import re as _re
        import random as _rnd

        try:
            from npc_bestiary_panel import (
                _fmt_speed, _fmt_cr, _fmt_type, _fmt_entries, _fmt_ac,
            )
        except ImportError:
            return

        # ── Palette ──────────────────────────────────────────────────────────
        BG      = "#0d1117"
        BG2     = "#13191f"
        BG_HDR  = "#0b0f18"
        BG_ATK  = "#200a0a"
        BG_DMG  = "#1e1100"
        BG_DC   = "#091020"
        BG_ACT  = "#0d1117"
        FG      = "#dde3ec"
        FG_DIM  = "#55606e"
        FG_MID  = "#99a0ac"
        GOLD    = "#ffd54f"
        RED     = "#e57373"
        ORANGE  = "#ffb86c"
        BLUE    = "#64b5f6"
        GREEN   = "#81c784"
        PURPLE  = "#ce93d8"
        TEAL    = "#4dd0e1"

        c_name = combatant.name
        bname  = getattr(combatant, "bestiary_name", "") or ""

        # ── Utilitaires ───────────────────────────────────────────────────────
        def _clean(txt: str) -> str:
            return _re.sub(r'\{@\w+\s*([^}]*)\}', r'\1', txt)

        def _parse_rolls(entries: list) -> dict:
            """Extrait bonus d'attaque, dégâts et DD depuis les entries 5etools."""
            full = _fmt_entries(entries)
            raw  = "\n".join(e for e in entries if isinstance(e, str))
            hit_m    = _re.search(r'\{@hit\s+(-?\d+)\}', raw)
            dc_m     = _re.search(r'\{@dc\s+(\d+)\}', raw)
            dmg_tags  = _re.findall(r'\{@damage\s+([^}]+)\}', raw)
            type_tags = _re.findall(
                r'\{@damage\s+[^}]+\}\s*([a-zA-Zéâ]+(?:\s+[a-zA-Zéâ]+)?)', raw)
            damages = [
                (dmg_tags[i].strip(), type_tags[i].strip() if i < len(type_tags) else "")
                for i in range(len(dmg_tags))
            ]
            # fallback : cherche NdX+Y damage dans le texte nettoyé
            if not damages:
                for expr, typ in _re.findall(
                        r'(\d+d\d+(?:[+-]\d+)?)\s+([a-zA-Zé]+)\s+damage', full, _re.I):
                    damages.append((expr, typ))
            save_m = _re.search(
                r'\{@dc\s+\d+\}[^{]*\{@skill\s+([^}]+)\}'
                r'|jet\s+de\s+sauvegarde\s+(?:de\s+)?(\w+)'
                r'|(\w+)\s+saving\s+throw',
                raw, _re.IGNORECASE)
            dc_save = ""
            if save_m:
                dc_save = next(
                    (g for g in save_m.groups() if g), "").strip()
            return {
                "hit":     int(hit_m.group(1)) if hit_m else None,
                "dc":      int(dc_m.group(1))  if dc_m  else None,
                "dc_save": dc_save,
                "damages": damages,
                "desc":    full,
            }

        def _roll_dice(expr: str) -> tuple[int, str]:
            total, parts = 0, []
            for term in _re.finditer(r'([+-]?\s*\d*d\d+|[+-]?\s*\d+)',
                                     expr.strip()):
                t = term.group(0).replace(' ', '')
                if 'd' in t:
                    sign = -1 if t.startswith('-') else 1
                    t2   = t.lstrip('+-')
                    n_s, sides_s = t2.split('d')
                    n     = int(n_s) if n_s else 1
                    sides = int(sides_s)
                    rolls = [_rnd.randint(1, sides) for _ in range(n)]
                    total += sign * sum(rolls)
                    parts.append(f"[{','.join(str(r) for r in rolls)}]")
                else:
                    v = int(t.replace(' ', ''))
                    total += v
                    parts.append(str(v))
            return total, '+'.join(parts).replace('+-', '-')

        def _send(text: str, color: str = GOLD):
            if self.chat_queue:
                self.chat_queue.put({
                    "sender": f"⚔ {c_name}",
                    "text":   text,
                    "color":  color,
                })

        # ── Variable de cible (partagée par tous les boutons) ─────────────────
        target_var = tk.StringVar(
            value=targets[0].name if targets else "— aucune —")

        # ── Bloc racine ───────────────────────────────────────────────────────
        outer = tk.Frame(self.chat_display, bg=BG2, bd=0,
                         highlightthickness=1,
                         highlightbackground="#2a3040")

        # ── En-tête ───────────────────────────────────────────────────────────
        m_type   = _fmt_type(monster.get("type", "?"))
        cr_str   = _fmt_cr(monster.get("cr", "?"))
        ac_str   = _fmt_ac(monster.get("ac", []))
        spd_raw  = monster.get("speed", {})
        spd_str  = _fmt_speed(spd_raw) if isinstance(spd_raw, dict) else str(spd_raw)
        hp_raw   = monster.get("hp", {})
        hp_avg   = hp_raw.get("average", "?") if isinstance(hp_raw, dict) else "?"
        hp_expr  = hp_raw.get("formula", "")  if isinstance(hp_raw, dict) else ""

        hdr = tk.Frame(outer, bg=BG_HDR, padx=8, pady=5)
        hdr.pack(fill=tk.X)

        tk.Label(hdr, text=f"⚔  Tour de {c_name}",
                 bg=BG_HDR, fg=GOLD,
                 font=("Consolas", 9, "bold"), anchor="w"
                 ).pack(side=tk.LEFT)

        meta_txt = f"  {m_type}  ·  FP {cr_str}  ·  CA {ac_str}  ·  PV {hp_avg}"
        if hp_expr:
            meta_txt += f" ({hp_expr})"
        tk.Label(hdr, text=meta_txt,
                 bg=BG_HDR, fg=FG_DIM,
                 font=("Consolas", 7)).pack(side=tk.LEFT)

        # ── Ligne vitesse + sélecteur de cible ───────────────────────────────
        info = tk.Frame(outer, bg=BG2, padx=8, pady=4)
        info.pack(fill=tk.X)

        tk.Label(info, text=f"🏃 {spd_str}",
                 bg=BG2, fg=GREEN,
                 font=("Consolas", 8)).pack(side=tk.LEFT, padx=(0, 14))

        if targets:
            tk.Label(info, text="Cible :",
                     bg=BG2, fg=FG_DIM,
                     font=("Consolas", 8)).pack(side=tk.LEFT, padx=(0, 4))

            target_names = [t.name for t in targets]
            opt = tk.OptionMenu(info, target_var, *target_names)
            opt.config(
                bg="#1c2638", fg=FG,
                activebackground="#2a3a50", activeforeground=FG,
                font=("Consolas", 8), relief="flat",
                highlightthickness=0, bd=0, padx=4, pady=2,
            )
            opt["menu"].config(
                bg="#1c2638", fg=FG,
                activebackground="#2a3a50", activeforeground=FG,
                font=("Consolas", 8),
            )
            opt.pack(side=tk.LEFT)

        # ── Séparateur ────────────────────────────────────────────────────────
        def _sep(color="#1e2a38"):
            tk.Frame(outer, bg=color, height=1).pack(fill=tk.X)

        _sep()

        # ── Constructeur de section d'actions ─────────────────────────────────
        def _build_section(title: str, actions_list: list, hdr_color: str):
            if not actions_list:
                return

            # En-tête de section
            sh = tk.Frame(outer, bg=BG_HDR, padx=8, pady=2)
            sh.pack(fill=tk.X)
            tk.Label(sh, text=title,
                     bg=BG_HDR, fg=hdr_color,
                     font=("Consolas", 7, "bold")).pack(side=tk.LEFT)

            for action in actions_list:
                aname   = action.get("name", "?")
                entries = action.get("entries", [])
                rolls   = _parse_rolls(entries)
                desc_full = _clean(rolls["desc"])

                arow = tk.Frame(outer, bg=BG_ACT, padx=8, pady=3)
                arow.pack(fill=tk.X)

                # Nom de l'action — survol affiche la desc complète dans le chat
                name_lbl = tk.Label(
                    arow, text=f"▸ {aname}",
                    bg=BG_ACT, fg=FG_MID,
                    font=("Consolas", 8, "bold"),
                    anchor="w", cursor="hand2")
                name_lbl.pack(side=tk.LEFT, padx=(0, 8))
                name_lbl.bind("<Enter>", lambda e, l=name_lbl: l.config(fg=GOLD))
                name_lbl.bind("<Leave>", lambda e, l=name_lbl: l.config(fg=FG_MID))
                name_lbl.bind("<Button-1>",
                    lambda e, n=aname, d=desc_full:
                        _send(f"▸ **{n}**\n{d[:400]}", "#9ba8b8"))

                btns = tk.Frame(arow, bg=BG_ACT)
                btns.pack(side=tk.LEFT, fill=tk.X)

                def _qbtn(txt, row_bg, fg, cmd, parent=btns):
                    b = tk.Button(
                        parent, text=txt,
                        bg=row_bg, fg=fg, activebackground=row_bg,
                        activeforeground=fg,
                        font=("Consolas", 7, "bold"),
                        relief="flat", bd=0,
                        padx=6, pady=2, cursor="hand2",
                        command=cmd)
                    b.pack(side=tk.LEFT, padx=(0, 3))
                    b.bind("<Enter>", lambda e, w=b, c=fg:
                           w.config(bg=_darken_hex(row_bg, 1.4)))
                    b.bind("<Leave>", lambda e, w=b, bg=row_bg:
                           w.config(bg=bg))
                    return b

                # Bouton attaque (jet d20 + bonus, mentionne la cible)
                if rolls["hit"] is not None:
                    bonus = rolls["hit"]
                    sign  = "+" if bonus >= 0 else ""

                    def _atk(b=bonus, n=aname):
                        d20  = _rnd.randint(1, 20)
                        tot  = d20 + b
                        s    = "+" if b >= 0 else ""
                        crit = (" 🎯 CRITIQUE!" if d20 == 20
                                else " ☠ FUMBLE"  if d20 == 1 else "")
                        tgt  = target_var.get()
                        msg  = (f"**{n}** → {tgt}\n"
                                f"  d20({d20}) {s}{b} = **{tot}**{crit}")
                        _send(msg, RED)

                    _qbtn(f"⚔ Atk {sign}{bonus}", BG_ATK, RED, _atk)

                # Bouton(s) dégâts
                for i, (expr, dmg_type) in enumerate(rolls["damages"]):
                    t_lbl = f" {dmg_type}" if dmg_type else ""
                    btn_t = (f"💥 {expr}{t_lbl}" if i == 0
                             else f"+ {expr}{t_lbl}")

                    def _dmg(e=expr, t=dmg_type, n=aname):
                        total, detail = _roll_dice(e)
                        ts  = f" {t}" if t else ""
                        msg = (f"**{n}** — Dégâts{ts}\n"
                               f"  {e} → {detail} = **{total}**")
                        _send(msg, ORANGE)

                    _qbtn(btn_t, BG_DMG, ORANGE, _dmg)

                # Bouton DD / sauvegarde
                if rolls["dc"] is not None:
                    sv_lbl = rolls["dc_save"].upper() if rolls["dc_save"] else "JdS"
                    dc_val = rolls["dc"]

                    def _dc(dc=dc_val, sv=sv_lbl, n=aname):
                        tgt = target_var.get()
                        msg = (f"**{n}** — JdS DD {dc} ({sv})\n"
                               f"  {tgt} doit réussir !")
                        _send(msg, BLUE)

                    _qbtn(f"DD {dc_val} {sv_lbl}", BG_DC, BLUE, _dc)

                # Si aucun bouton de jet : clic sur le nom enverra la description
                if (rolls["hit"] is None and rolls["dc"] is None
                        and not rolls["damages"]):
                    name_lbl.config(fg=FG_DIM,
                                    font=("Consolas", 8, "italic"))

            _sep()

        # ── Helper hover (légère surbrillance boutons) ────────────────────────
        def _darken_hex(hex_color: str, factor: float) -> str:
            try:
                h = hex_color.lstrip("#")
                if len(h) == 6:
                    r = min(255, int(int(h[0:2], 16) * factor))
                    g = min(255, int(int(h[2:4], 16) * factor))
                    b = min(255, int(int(h[4:6], 16) * factor))
                    return f"#{r:02x}{g:02x}{b:02x}"
            except Exception:
                pass
            return hex_color

        # ── Rendu des sections ────────────────────────────────────────────────
        _build_section("◆ ACTIONS",                 monster.get("action",    []), RED)
        _build_section("◈ ACTIONS BONUS",           monster.get("bonus",     []), ORANGE)
        _build_section("◇ RÉACTIONS",               monster.get("reaction",  []), PURPLE)
        _build_section("★ ACTIONS LÉGENDAIRES",     monster.get("legendary", []), GOLD)

        # ── Traits (3 premiers, résumé) ───────────────────────────────────────
        traits = monster.get("trait", [])
        if traits:
            th = tk.Frame(outer, bg=BG_HDR, padx=8, pady=2)
            th.pack(fill=tk.X)
            tk.Label(th, text="◉ TRAITS",
                     bg=BG_HDR, fg=TEAL,
                     font=("Consolas", 7, "bold")).pack(side=tk.LEFT)
            for trait in traits[:3]:
                tname = trait.get("name", "?")
                tdesc = _clean(_fmt_entries(trait.get("entries", [])))
                trow  = tk.Frame(outer, bg=BG_ACT, padx=8, pady=2)
                trow.pack(fill=tk.X)
                tk.Label(trow, text=f"• {tname}:",
                         bg=BG_ACT, fg=TEAL,
                         font=("Consolas", 7, "bold")).pack(side=tk.LEFT)
                tk.Label(trow,
                         text=tdesc[:160] + ("…" if len(tdesc) > 160 else ""),
                         bg=BG_ACT, fg=FG_DIM,
                         font=("Consolas", 7),
                         wraplength=440, justify=tk.LEFT
                         ).pack(side=tk.LEFT, padx=4)
            _sep()

        # ── Pied-de-bloc ──────────────────────────────────────────────────────
        tk.Frame(outer, bg="#1c2a3a", height=2).pack(fill=tk.X)

        # ── Insertion dans le chat (ScrolledText.window_create) ───────────────
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.insert(tk.END, "\n")
        self.chat_display.window_create(tk.END, window=outer)
        self.chat_display.insert(tk.END, "\n")
        self.chat_display.config(state=tk.DISABLED)
        self.root.after_idle(lambda: self.chat_display.yview_moveto(1.0))


from damage_link_ui_handler import (
    _handle_damage_link as _handle_damage_link,
    _open_damage_popup  as _open_damage_popup,
)

ChatMixin._handle_damage_link = _handle_damage_link
ChatMixin._open_damage_popup  = _open_damage_popup