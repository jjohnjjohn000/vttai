import tkinter as tk

class ChatMixinConfirms:
    """Mixin pour DnDApp — widgets interactifs de confirmation (sorts, résultats, actions)."""

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
        color = getattr(self, "CHAR_COLORS", {}).get(char_name, "#aaaaaa")
        self.msg_counter += 1
        n = self.msg_counter

        tag_header  = f"spell_hdr_{n}"
        tag_confirm = f"spell_ok_{n}"
        tag_deny    = f"spell_no_{n}"

        level_var = tk.IntVar(value=spell_level)
        if not hasattr(self, "_tk_vars_keepalive"): self._tk_vars_keepalive =[]
        self._tk_vars_keepalive.append(level_var)

        self.chat_display.config(state=tk.NORMAL)

        self.chat_display.insert(tk.END, f"\n✨ {char_name} lance ", "spell_hint")
        self.chat_display.insert(tk.END, spell_name, tag_header)
        cible_txt = f" → {target}" if target and target.lower() not in ("?", "-", "") else ""
        self.chat_display.insert(tk.END, f"{cible_txt}\n", "spell_hint")

        # Hyperlien : clic sur le nom → fiche du sort
        if hasattr(self, "_open_spell_sheet"):
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
                if hasattr(self, "append_message"):
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
            if hasattr(self, "append_message"):
                self.append_message("✨ Sort", f"{char_name} — {spell_name} niv.{lvl}{cible_txt} → {result}", color)
            frame.destroy()
            _remove_spell_lines()
            resume_callback(True, lvl)

        def _deny():
            if hasattr(self, "append_message"):
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
                                mode: str = "damage",
                                target: str | None = None,
                                damage: int | None = None):
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
        color = getattr(self, "CHAR_COLORS", {}).get(char_name, "#aaaaaa")
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
            "smite":   f" ✨ {type_label} — appliquer ? ",
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
                lbl = "Touché ✅" if mode == "attack" else f"{type_label} appliqué ✅"
                if hasattr(self, "append_message"):
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
                lbl = "Raté ❌" if mode == "attack" else f"{type_label} ignoré ❌"
                if hasattr(self, "append_message"):
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
            # ── Mode soin : boutons Appliquer / Annuler ─────────────────────────
            def _apply_heal(event=None):
                if _callback_done[0]:
                    return
                _callback_done[0] = True
                note = note_entry.get().strip()
                frame.destroy()
                _cleanup_header()

                # --- APPLICATION EFFECTIVE DES SOINS ---
                import re as _re_heal
                from state_manager import update_hp, load_state, save_state
                
                # 1. Extraction du montant
                _amt = 0
                _m_heal = _re_heal.search(r'Total\s*=\s*(\d+)', results_text)
                if _m_heal:
                    _amt = int(_m_heal.group(1))

                # 2. Déduction de Lay on Hands
                _m_loh = _re_heal.search(r'\[Imposition des mains\]\s*-(\d+)', results_text)
                if _m_loh:
                    _loh_amt = int(_m_loh.group(1))
                    _st = load_state()
                    if char_name in _st.get("characters", {}):
                        _feats = _st["characters"][char_name].setdefault("features", {})
                        _curr_loh = _feats.get("lay_on_hands", 0)
                        _feats["lay_on_hands"] = max(0, _curr_loh - _loh_amt)
                        save_state(_st)
                        
                # 3. Soins sur la ou les cibles
                _m_tgts = _re_heal.findall(r'soigner\s+([A-Za-zÀ-ÿ0-9\s\-\']+?)\s+de', results_text)
                _actual_targets = [t.strip() for t in _m_tgts] if _m_tgts else[]
                
                if not _actual_targets and target:
                    _actual_targets = [target]
                
                if not _actual_targets:
                    _m_tgt_fb = _re_heal.search(r'→\s*([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9\s\-\']+?)(?:\s*[\n:(]|$)', results_text)
                    if _m_tgt_fb:
                        _actual_targets =[_m_tgt_fb.group(1).strip()]
                
                if _amt > 0 and _actual_targets:
                    for t in _actual_targets:
                        update_hp(t, _amt)
                        
                # Synchronisation UI
                try:
                    if hasattr(self, "_combat_tracker_win") and getattr(self, "_combat_tracker_win", None):
                        self.root.after(0, self._combat_tracker_win.sync_pc_hp_from_state)
                except Exception:
                    pass
                
                try:
                    if hasattr(self, "_refresh_char_stats"):
                        self.root.after(0, self._refresh_char_stats)
                except Exception:
                    pass

                _tgts_str = ", ".join(_actual_targets) if _actual_targets else "la cible"
                _amt_str = f" de {_amt} PV" if _amt > 0 else ""
                
                if hasattr(self, "append_message"):
                    self.append_message(
                        f"💚 Soin — {type_label}",
                        f"Soin{_amt_str} appliqué à {_tgts_str}" + (f"  — {note}" if note else ""),
                        "#44cc44",
                    )
                resume_callback(note)

            def _cancel_heal(event=None):
                if _callback_done[0]:
                    return
                _callback_done[0] = True
                frame.destroy()
                _cleanup_header()
                if hasattr(self, "append_message"):
                    self.append_message(f"🚫 Soin refusé — {type_label}", "Soin annulé par le MJ.", "#cc4444")
                resume_callback("")

            note_entry.bind("<Return>", _apply_heal)
            tk.Button(row_btns, text="💚 Appliquer soin", bg="#0d2a0d", fg="#66ee66",
                      font=("Arial", 9, "bold"), relief="flat", padx=10, pady=3,
                      activebackground="#1a4a1a", command=_apply_heal).pack(side=tk.LEFT, padx=(0, 4))
            tk.Button(row_btns, text="✗ Annuler", bg="#2a0d0d", fg="#ee6666",
                      font=("Arial", 9, "bold"), relief="flat", padx=10, pady=3,
                      activebackground="#4a1a1a", command=_cancel_heal).pack(side=tk.LEFT)

        elif mode == "save":
            # ── AUTO-ROLL (JDS) ──────────────────────────────────────────────
            try:
                import re as _re_save
                tracker = getattr(self, "_combat_tracker_win", None)
                _combatants = getattr(tracker, "combatants", []) if tracker else []

                _target_name = target if target else None

                if not _target_name:
                    _m_tgt = _re_save.search(
                        r'→\s*([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9\s\-\']+?)(?:\s*[\n:(]|$)',
                        results_text)
                    if _m_tgt:
                        _target_name = _m_tgt.group(1).strip()

                if not _target_name:
                    _map_win = getattr(self, "_combat_map_win", None)
                    if _map_win and hasattr(_map_win, "_selected_tokens") and _map_win._selected_tokens:
                        _sel_id = next(iter(_map_win._selected_tokens))
                        _tok = next((t for t in getattr(_map_win, "tokens", []) if id(t) == _sel_id), None)
                        if _tok:
                            _target_name = _tok.get("name")

                combatant = None
                if _target_name:
                    combatant = next(
                        (c for c in _combatants
                         if c.name.lower() == _target_name.lower()
                         or _target_name.lower() in c.name.lower()),
                        None)

                if combatant is not None:
                    import re
                    comb = results_text.lower()
                    
                    stat_map = {
                        "force": "str", "str": "str", "strength": "str",
                        "dextérité": "dex", "dex": "dex", "dexterity": "dex",
                        "constitution": "con", "con": "con",
                        "intelligence": "int", "int": "int",
                        "sagesse": "wis", "wis": "wis", "wisdom": "wis",
                        "charisme": "cha", "cha": "cha", "charisma": "cha"
                    }
                    stat_key = next((v for k, v in stat_map.items() if re.search(r'\b' + k + r'\b', comb)), None)
                    
                    dc_match = re.search(r'(?:dc|dd)\s*(\d+)', comb)
                    dc_val = int(dc_match.group(1)) if dc_match else None

                    if stat_key:
                        bonus = 0
                        if combatant.is_pc:
                            if stat_key == "con":
                                try:
                                    from state_manager import load_state
                                    st = load_state()
                                    c_data = st.get("characters", {}).get(combatant.name, {})
                                    bonus = c_data.get("con_mod", 0)
                                except Exception: pass
                        else:
                            b_name = combatant.bestiary_name
                            if b_name:
                                try:
                                    from npc_bestiary_panel import get_monster
                                    monster = get_monster(b_name)
                                    if monster:
                                        bonus = (monster.get(stat_key, 10) - 10) // 2
                                        saves = monster.get("save", {})
                                        if stat_key in saves:
                                            m = re.search(r'([+-]?\d+)', str(saves[stat_key]))
                                            if m: bonus = int(m.group(1))
                                except Exception: pass

                        import random
                        d20 = random.randint(1, 20)
                        total = d20 + bonus
                        
                        roll_frame = tk.Frame(frame, bg="#0a1222", padx=6, pady=4, relief="flat", highlightthickness=1, highlightbackground="#3498db")
                        roll_frame.pack(fill=tk.X, pady=(0, 6), before=row_btns)
                        
                        res_color = "#88bbee"
                        res_icon = "🎲"
                        if dc_val is not None:
                            if total >= dc_val:
                                res_color = "#66ee66"
                                res_icon = "✅ (Réussi)"
                            else:
                                res_color = "#ee6666"
                                res_icon = "❌ (Raté)"
                                
                        sign = "+" if bonus >= 0 else ""
                        
                        tk.Label(roll_frame, text=f"Jet auto pour {combatant.name} :", bg="#0a1222", fg="#88bbee", font=("Consolas", 8, "bold")).pack(side=tk.LEFT, padx=(0, 8))
                        tk.Label(roll_frame, text=f"d20({d20}) {sign}{bonus} = ", bg="#0a1222", fg="#dddddd", font=("Consolas", 9)).pack(side=tk.LEFT)
                        tk.Label(roll_frame, text=f"{total} {res_icon}", bg="#0a1222", fg=res_color, font=("Consolas", 10, "bold")).pack(side=tk.LEFT)
            except Exception as e:
                print(f"[Auto-Roll Save] Erreur : {e}")

            # ── Mode sauvegarde : Sauvegarde réussie / Sauvegarde ratée ──────
            def _save_success(event=None):
                if _callback_done[0]:
                    return
                _callback_done[0] = True
                note = note_entry.get().strip()
                frame.destroy()
                _cleanup_header()
                if hasattr(self, "append_message"):
                    self.append_message(
                        f"🛡️ MJ — {type_label}",
                        "Sauvegarde RÉUSSIE ✅ (sort raté)"
                        + (f"  — {note}" if note else ""),
                        "#4488cc",
                    )
                resume_callback(True, note)

            def _save_failure(event=None):
                if _callback_done[0]:
                    return
                _callback_done[0] = True
                note = note_entry.get().strip()
                frame.destroy()
                _cleanup_header()
                if hasattr(self, "append_message"):
                    self.append_message(
                        f"💥 MJ — {type_label}",
                        "Sauvegarde RATÉE ❌ (sort touché)"
                        + (f"  — {note}" if note else ""),
                        "#cc4444",
                    )
                resume_callback(False, note)

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
        elif mode == "movement":
            # ── Mode mouvement : Confirmer la position cible ─────────────────
            def _confirm_move(event=None):
                if _callback_done[0]:
                    return
                _callback_done[0] = True
                frame.destroy()
                _cleanup_header()
                if hasattr(self, "append_message"):
                    self.append_message(f"📍 MJ — {type_label}", "Mouvement validé", "#44cc44")
                resume_callback(True)

            def _refuse_move(event=None):
                if _callback_done[0]:
                    return
                _callback_done[0] = True
                frame.destroy()
                _cleanup_header()
                if hasattr(self, "append_message"):
                    self.append_message(f"📍 MJ — {type_label}", "Mouvement refusé", "#cc4444")
                resume_callback(False)

            tk.Button(row_btns, text="✅ Confirmer le déplacement", bg="#0d2a0d", fg="#66ee66",
                      font=("Arial", 9, "bold"), relief="flat", padx=10, pady=3,
                      activebackground="#1a4a1a", cursor="hand2", command=_confirm_move).pack(side=tk.LEFT, padx=(0, 6))
            tk.Button(row_btns, text="❌ Refuser", bg="#2a0d0d", fg="#ee6666",
                      font=("Arial", 9, "bold"), relief="flat", padx=10, pady=3,
                      activebackground="#4a1a1a", cursor="hand2", command=_refuse_move).pack(side=tk.LEFT)

            if hasattr(self, "_combat_map_win") and self._combat_map_win:
                try:
                    c, r = damage if isinstance(damage, tuple) else (0, 0)
                    self._combat_map_win.request_movement_preview(target, c, r)
                except Exception as e:
                    print(f"Erreur trace preview : {e}")
        else:
            # ── Mode dégâts / autre : Continuer + Annuler ────────────────────
            def _ok(event=None):
                if _callback_done[0]:
                    return
                _callback_done[0] = True
                note = note_entry.get().strip()
                frame.destroy()
                _cleanup_header()
                if note and hasattr(self, "append_message"):
                    self.append_message(
                        f"✏️ MJ — {type_label}",
                        note,
                        "#aaaacc",
                    )
                resume_callback(note)

                # ── Appliquer les dégâts dans le combat tracker ──────────────
                _tgt = target
                _dmg = damage
                if _tgt is None or _dmg is None:
                    import re as _re
                    _m = _re.search(
                        r'→\s*(.+?)\s*:\s*(\d+)\s*dégât',
                        results_text,
                        _re.IGNORECASE,
                    )
                    if _m:
                        if _tgt is None:
                            _tgt = _m.group(1).strip()
                        if _dmg is None:
                            _dmg = int(_m.group(2))
                if _tgt and _dmg is not None and _dmg > 0:
                    _tracker = getattr(self, "_combat_tracker_win", None)
                    if _tracker is not None:
                        try:
                            _tracker.apply_damage_to_npc(_tgt, _dmg)
                        except Exception as _e:
                            print(f"[ChatMixin] apply_damage_to_npc failed: {_e}")

            def _cancel(event=None):
                if _callback_done[0]:
                    return
                _callback_done[0] = True
                frame.destroy()
                _cleanup_header()
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
        """
        color = getattr(self, "CHAR_COLORS", {}).get(char_name, "#aaaaaa")
        self.msg_counter += 1
        n = self.msg_counter
        tag_card = f"action_card_{n}"

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
            color,
        )

        counter_txt = ""
        if sub_index is not None and sub_total is not None and sub_total > 1:
            counter_txt = f"  [{sub_index}/{sub_total}]"

        self.chat_display.config(state=tk.NORMAL)

        self.chat_display.insert(tk.END, "\n", tag_card)
        self.chat_display.insert(tk.END,
            f"⚔️ {type_label.upper()}{counter_txt} — {char_name}\n",
            f"action_hdr_{n}")

        frame = tk.Frame(self.chat_display, bg="#12181a",
                         relief="flat", padx=8, pady=6,
                         highlightthickness=2,
                         highlightbackground=type_color)

        badge_frame = tk.Frame(frame, bg=type_color)
        badge_frame.pack(anchor="w", pady=(0, 4))
        tk.Label(badge_frame, text=f" {type_label} ",
                 bg=type_color, fg="white",
                 font=("Consolas", 8, "bold"), padx=4).pack()

        row_i = tk.Frame(frame, bg="#12181a")
        row_i.pack(fill=tk.X, pady=1)
        tk.Label(row_i, text="Intention :", bg="#12181a", fg="#888899",
                 font=("Consolas", 8, "bold"), width=11, anchor="w").pack(side=tk.LEFT)
        tk.Label(row_i, text=intention, bg="#12181a", fg="#ddeeff",
                 font=("Consolas", 9), wraplength=380, justify=tk.LEFT,
                 anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

        row_r = tk.Frame(frame, bg="#12181a")
        row_r.pack(fill=tk.X, pady=1)
        tk.Label(row_r, text="Règle 5e :", bg="#12181a", fg="#888899",
                 font=("Consolas", 8, "bold"), width=11, anchor="nw").pack(side=tk.LEFT, anchor="n")
        
        if hasattr(self, "_make_regle_with_links"):
            regle_widget = self._make_regle_with_links(row_r, regle, type_color, "#12181a")
        else:
            regle_widget = tk.Label(row_r, text=regle, bg="#12181a", fg=type_color, font=("Consolas", 9, "bold"), wraplength=380, justify=tk.LEFT, anchor="w")
        regle_widget.pack(side=tk.LEFT, fill=tk.X, expand=True)

        row_c = tk.Frame(frame, bg="#12181a")
        row_c.pack(fill=tk.X, pady=1)
        tk.Label(row_c, text="Cible :", bg="#12181a", fg="#888899",
                 font=("Consolas", 8, "bold"), width=11, anchor="w").pack(side=tk.LEFT)
        tk.Label(row_c, text=cible, bg="#12181a", fg="#bbbbcc",
                 font=("Consolas", 9), wraplength=380, justify=tk.LEFT,
                 anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

        # ── Cas spécifique : Prévisualisation de Mouvement ───────────────────
        _MOVE_KEYWORDS = ("mouvement", "déplace", "deplace", "dash", "foncer", "sprint", "avance", "recule", "fonce", "move")
        _type_is_move = any(k in type_low for k in _MOVE_KEYWORDS)
        _type_is_generic = type_low in ("", "action", "action bonus", "réaction", "reaction")
        _intent_has_move = any(k in intention.lower() for k in _MOVE_KEYWORDS)
        is_move = _type_is_move or (_type_is_generic and _intent_has_move)

        _SPECTRAL_PREFIXES = {
            "spiritual weapon": "Arme",   "arme spirituelle": "Arme",
            "marteau spirituel": "Arme",  "flaming sphere": "Sphère",
            "sphère de feu": "Sphère",    "bigby": "Main",
            "main de bigby": "Main",      "moonbeam": "Rayon",
            "rayon de lune": "Rayon",     "cloud of daggers": "Dagues",
            "nuage de dagues": "Dagues",
        }
        _combined_ir = (intention + " " + regle).lower()
        _spectral_token_name = None
        for _kw, _pfx in _SPECTRAL_PREFIXES.items():
            if _kw in _combined_ir:
                _spectral_token_name = f"{_pfx} ({char_name})"
                break
        _preview_token = _spectral_token_name if _spectral_token_name else char_name

        if is_move and hasattr(self, "_combat_map_win") and getattr(self, "_combat_map_win", None):
            def _calc_coords():
                import re
                _cur_col, _cur_row = 0, 0
                try:
                    for _tok in getattr(self._combat_map_win, "tokens",[]):
                        if _tok.get("name") == _preview_token:
                            _cur_col = int(round(_tok.get("col", 0)))
                            _cur_row = int(round(_tok.get("row", 0)))
                            break
                except Exception:
                    pass
                
                r_low = regle.lower()
                i_low = intention.lower()
                c_low = cible.lower()
                _combined = r_low + " " + i_low + " " + c_low

                _dist = 6
                _m_ft    = re.search(r'(\d+)\s*ft', _combined)
                _m_cases = re.search(r'(\d+)\s*cases?', _combined)
                _m_met   = re.search(r'(\d+(?:[.,]\d+)?)\s*m(?:ètres?|etres?|\b)', _combined)
                if _m_ft:    _dist = max(1, round(int(_m_ft.group(1)) / 5.0))
                elif _m_cases: _dist = int(_m_cases.group(1))
                elif _m_met: _dist = max(1, round(float(_m_met.group(1).replace(",", ".")) / 1.5))

                def _cap_to_dist(dest_col, dest_row):
                    _dc = dest_col - _cur_col
                    _dr = dest_row - _cur_row
                    _cheb = max(abs(_dc), abs(_dr))
                    if _cheb <= _dist or _cheb == 0:
                        return dest_col, dest_row
                    _ratio = _dist / _cheb
                    return (
                        _cur_col + round(_dc * _ratio),
                        _cur_row + round(_dr * _ratio),
                    )

                _m_exact_cible = re.match(r'^col(?:onne)?\s*(\d+)[,\s]+(?:lig(?:ne)?|rang(?:ée?)?)\s*(\d+)$', c_low.strip(), re.IGNORECASE)
                _m_abs_r = re.search(r'col(?:onne)?\s*(\d+)[,\s]+(?:lig(?:ne)?|rang(?:ée?)?)\s*(\d+)', r_low, re.IGNORECASE)
                _m_abs_c = re.search(r'col(?:onne)?\s*(\d+)[,\s]+(?:lig(?:ne)?|rang(?:ée?)?)\s*(\d+)', _combined, re.IGNORECASE)

                if _m_exact_cible:
                    return _cap_to_dist(int(_m_exact_cible.group(1)) - 1, int(_m_exact_cible.group(2)) - 1)
                if _m_abs_r:
                    return _cap_to_dist(int(_m_abs_r.group(1)) - 1, int(_m_abs_r.group(2)) - 1)

                if not _m_ft and not _m_cases and not _m_met and _m_abs_c:
                    return _cap_to_dist(int(_m_abs_c.group(1)) - 1, int(_m_abs_c.group(2)) - 1)

                try:
                    for _other in getattr(self._combat_map_win, "tokens",[]):
                        _oname = _other.get("name", "").lower()
                        if _oname and _oname in _combined and _other.get("name") != _preview_token:
                            _oc = int(round(_other.get("col", 0)))
                            _or = int(round(_other.get("row", 0)))
                            _raw_dc = _oc - _cur_col
                            _raw_dr = _or - _cur_row
                            _mag    = max(abs(_raw_dc), abs(_raw_dr)) or 1
                            _dcol   = round(_raw_dc / _mag)
                            _drow   = round(_raw_dr / _mag)
                            return _cur_col + _dcol * _dist, _cur_row + _drow * _dist
                except Exception:
                    pass

                _DIR_EXACT =[("nord-est", (1, -1)), ("nord-ouest", (-1, -1)), ("sud-est", (1, 1)), ("sud-ouest", (-1, 1))]
                _DIR_WORD =[("nord", (0, -1)), ("sud", (0, 1)), ("ouest", (-1, 0)), ("est", (1, 0)),
                             ("north", (0, -1)), ("south", (0, 1)), ("west", (-1, 0)), ("east", (1, 0))]
                
                for _kd, (_dc, _dr) in _DIR_EXACT:
                    if _kd in _combined: return _cur_col + _dc * _dist, _cur_row + _dr * _dist
                for _kd, (_dc, _dr) in _DIR_WORD:
                    if _kd == "est" and not re.search(r"(vers l'|à l'|direction )\b" + _kd + r"\b", _combined):
                        continue
                    if re.search(r'\b' + _kd + r'\b', _combined):
                        return _cur_col + _dc * _dist, _cur_row + _dr * _dist
                        
                return None

            coords = _calc_coords()
            if coords:
                preview_col, preview_row = coords
                try:
                    self._combat_map_win.request_movement_preview(_preview_token, preview_col, preview_row)
                    _preview_lbl = (
                        f"💡 Prévisualisation : {_spectral_token_name} → Col {preview_col+1}, Lig {preview_row+1}. Vous pouvez le déplacer."
                        if _spectral_token_name else
                        "💡 Un carré de prévisualisation est sur la carte. Vous pouvez le déplacer."
                    )
                    tk.Label(frame, text=_preview_lbl,
                             bg="#12181a", fg="#4fc3f7", font=("Consolas", 8, "italic")).pack(fill=tk.X, pady=(2,0))
                except Exception as e:
                    print(f"Erreur trace preview : {e}")

        tk.Frame(frame, bg="#2a2a3a", height=1).pack(fill=tk.X, pady=(5, 3))

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
            extra = None
            if is_move and hasattr(self, "_combat_map_win") and self._combat_map_win:
                extra = self._combat_map_win.get_movement_preview(_preview_token)
                self._combat_map_win.clear_movement_preview(_preview_token)
            
            frame.destroy()
            _cleanup_header()
            suffix = f" ({sub_index}/{sub_total})" if sub_index and sub_total and sub_total > 1 else ""
            if hasattr(self, "append_message"):
                self.append_message(
                    f"✅ MJ → {char_name}",
                    f"[{type_label}]{suffix} autorisé : {intention}" + (f"  — {note}" if note else ""),
                    "#44aa44",
                )
            if note:
                try:
                    from combat_tracker_state import add_combat_history
                    add_combat_history(
                        f"  → 📝 Note MJ [{type_label}] {char_name} : {note}"
                    )
                    if hasattr(self, "_update_agent_combat_prompts"):
                        self._update_agent_combat_prompts()
                except Exception as _e:
                    print(f"[action_confirm] Note MJ history : {_e}")
            resume_callback(True, note, extra_data=extra)

        def _deny(event=None):
            note = note_entry.get().strip()
            if is_move and hasattr(self, "_combat_map_win") and self._combat_map_win:
                self._combat_map_win.clear_movement_preview(_preview_token)
            
            frame.destroy()
            _cleanup_header()
            suffix = f" ({sub_index}/{sub_total})" if sub_index and sub_total and sub_total > 1 else ""
            if hasattr(self, "append_message"):
                self.append_message(
                    f"❌ MJ → {char_name}",
                    f"[{type_label}]{suffix} refusé : {intention}" + (f"  — {note}" if note else ""),
                    "#aa4444",
                )
            if note:
                try:
                    from combat_tracker_state import add_combat_history
                    add_combat_history(
                        f"  → 📝 Note MJ [{type_label}] {char_name} : {note}"
                    )
                    if hasattr(self, "_update_agent_combat_prompts"):
                        self._update_agent_combat_prompts()
                except Exception as _e:
                    print(f"[action_confirm] Note MJ history : {_e}")
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