import tkinter as tk
import random as _rnd
import re as _re

class ChatMixinSkillNpc:
    """Mixin pour DnDApp — confirmations de compétences et outils de tour PNJ."""

    # ─── Widget de confirmation de jet de compétence/sauvegarde ──────────────

    def _append_skill_check_confirm(self, char_name: str, skill_label: str,
                                     stat_label: str, bonus: int,
                                     dc, has_advantage: bool, has_disadvantage: bool,
                                     intention: str,
                                     resume_callback):
        """
        Boîte de jet de compétence ou de sauvegarde.
        Affiche 2d20 (avec sélection Avantage/Normal/Désavantage),
        permet au MJ d'ajuster le bonus et de confirmer ou refuser.

        resume_callback(confirmed: bool, total: int, mj_note: str)
        """
        color  = getattr(self, "CHAR_COLORS", {}).get(char_name, "#aaaaaa")
        self.msg_counter += 1
        n = self.msg_counter

        BG      = "#07101e"
        BG2     = "#0c1928"
        ACCENT  = "#2a6492"
        FG      = "#b8d8f0"
        FG_DIM  = "#4a6878"

        # ── Tirage initial ───────────────────────────────────────────────────
        r1_init, r2_init = _rnd.randint(1, 20), _rnd.randint(1, 20)
        roll_vars  =[tk.IntVar(value=r1_init), tk.IntVar(value=r2_init)]
        adv_var    = tk.StringVar(value=(
            "avantage"    if has_advantage    else
            "désavantage" if has_disadvantage else
            "normal"
        ))
        bonus_var  = tk.IntVar(value=bonus)
        
        if not hasattr(self, "_tk_vars_keepalive"): self._tk_vars_keepalive =[]
        self._tk_vars_keepalive.extend([*roll_vars, adv_var, bonus_var])

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

        if intention:
            intent_lbl = tk.Label(frame, text=f"Intention : {intention}", bg=BG, fg="#cccccc", font=("Consolas", 9, "italic"), wraplength=450, justify=tk.LEFT)
            intent_lbl.pack(anchor="w", pady=(0, 4))

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
        self._tk_vars_keepalive.append(result_var)
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
            if hasattr(self, "append_message"):
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
            if hasattr(self, "append_message"):
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

        lbl_r2.config(text=f"[{r2_init}]")
        _update_display()

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
        """
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

        def _double_dice_expr(expr: str) -> str:
            def _dbl(m):
                n     = int(m.group(1)) if m.group(1) else 1
                sides = m.group(2)
                return f"{n * 2}d{sides}"
            return _re.sub(r'(\d*)d(\d+)', _dbl, expr)

        def _send(text: str, color: str = GOLD):
            if hasattr(self, "msg_queue") and self.msg_queue:
                self.msg_queue.put({
                    "sender": f"⚔ {c_name}",
                    "text":   text,
                    "color":  color,
                })
            
            try:
                from combat_tracker import COMBAT_STATE
                if COMBAT_STATE.get("active"):
                    import re as _re_send
                    clean_txt = _re_send.sub(r'\*\*', '', text).replace('\n', ' | ')
                    COMBAT_STATE.setdefault("combat_history",[]).append(f"• {c_name} : {clean_txt}")
            except Exception:
                pass

        target_var = tk.StringVar(
            value=targets[0].name if targets else "— aucune —")
        if not hasattr(self, "_tk_vars_keepalive"): self._tk_vars_keepalive =[]
        self._tk_vars_keepalive.append(target_var)

        outer = tk.Frame(self.chat_display, bg=BG2, bd=0,
                         highlightthickness=1,
                         highlightbackground="#2a3040")

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

        tk.Label(hdr, text="⚔  Tour de ",
                 bg=BG_HDR, fg=GOLD,
                 font=("Consolas", 9, "bold"), anchor="w"
                 ).pack(side=tk.LEFT)

        def _open_npc_sheet(event=None, _n=c_name, _b=bname):
            try:
                from npc_bestiary_panel import MonsterSheetWindow
                MonsterSheetWindow(
                    self.root, _n, _b or None,
                    chat_queue=getattr(self, "msg_queue", None),
                    audio_queue=getattr(self, "audio_queue", None),
                )
            except Exception as e:
                print(f"[NPC Sheet] Erreur ouverture fiche : {e}")

        _name_lbl = tk.Label(hdr, text=c_name,
                             bg=BG_HDR, fg=GOLD,
                             font=("Consolas", 9, "bold", "underline"),
                             cursor="hand2", anchor="w")
        _name_lbl.pack(side=tk.LEFT)
        _name_lbl.bind("<Button-1>", _open_npc_sheet)

        meta_txt = f"  {m_type}  ·  FP {cr_str}  ·  CA {ac_str}  ·  PV {hp_avg}"
        if hp_expr:
            meta_txt += f" ({hp_expr})"
        tk.Label(hdr, text=meta_txt,
                 bg=BG_HDR, fg=FG_DIM,
                 font=("Consolas", 7)).pack(side=tk.LEFT)

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

        def _sep(color="#1e2a38"):
            tk.Frame(outer, bg=color, height=1).pack(fill=tk.X)

        _sep()

        def _build_section(title: str, actions_list: list, hdr_color: str):
            if not actions_list:
                return

            sh = tk.Frame(outer, bg=BG_HDR, padx=8, pady=2)
            sh.pack(fill=tk.X)
            tk.Label(sh, text=title,
                     bg=BG_HDR, fg=hdr_color,
                     font=("Consolas", 7, "bold")).pack(side=tk.LEFT)

            for action in actions_list:
                raw_name = action.get("name", "?")
                recharge_val = None
                
                m_tag = _re.search(r'\{@recharge\s+(\d+)\}', raw_name)
                if m_tag:
                    recharge_val = int(m_tag.group(1))
                    aname = _re.sub(r'\s*\{@recharge\s+\d+\}', f' (Recharge {recharge_val}-6)', raw_name)
                else:
                    m_text = _re.search(r'\(Recharge\s+(\d+)(?:-\d+)?\)', raw_name, _re.IGNORECASE)
                    if m_text:
                        recharge_val = int(m_text.group(1))
                    aname = raw_name

                entries = action.get("entries", [])
                rolls   = _parse_rolls(entries)
                desc_full = _clean(rolls["desc"])

                arow = tk.Frame(outer, bg=BG_ACT, padx=8, pady=3)
                arow.pack(fill=tk.X)

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

                crit_var = tk.BooleanVar(value=False)
                self._tk_vars_keepalive.append(crit_var)

                from state_manager import get_npc_cooldown, set_npc_cooldown
                on_cooldown = False
                if recharge_val is not None:
                    on_cooldown = get_npc_cooldown(c_name, aname)

                def _consume_if_needed(name=aname):
                    if recharge_val is not None and not get_npc_cooldown(c_name, name):
                        set_npc_cooldown(c_name, name, True)

                def _qbtn(txt, row_bg, fg, cmd, parent=btns):
                    if on_cooldown and not txt.startswith("♻") and not txt.startswith("🟢"):
                        row_bg = "#2a2a2a"
                        fg = "#666666"
                        txt = f"[En Recharge] {txt}"

                    b = tk.Button(
                        parent, text=txt,
                        bg=row_bg, fg=fg, activebackground=row_bg,
                        activeforeground=fg,
                        font=("Consolas", 7, "bold"),
                        relief="flat", bd=0,
                        padx=6, pady=2, cursor="hand2",
                        command=cmd)
                    b.pack(side=tk.LEFT, padx=(0, 3))
                    b.bind("<Enter>", lambda e, w=b, c=fg, bg=row_bg:
                           w.config(bg=_darken_hex(bg, 1.4)))
                    b.bind("<Leave>", lambda e, w=b, bg=row_bg:
                           w.config(bg=bg))
                    return b

                if recharge_val is not None:
                    if on_cooldown:
                        def _roll_recharge(r=recharge_val, name=aname):
                            d6 = _rnd.randint(1, 6)
                            if d6 >= r:
                                res_txt = "🟢 **Réussi !** L'action est rechargée."
                                color = GREEN
                                set_npc_cooldown(c_name, name, False)
                            else:
                                res_txt = "🔴 **Échec.** Doit encore recharger."
                                color = RED
                            msg = f"**{name}** — Jet de Recharge (Recharge {r}-6)\n  d6({d6}) : {res_txt}"
                            _send(msg, color)
                        
                        _qbtn(f"♻ Tenter Recharge {recharge_val}+", "#302607", "#ffd54f", _roll_recharge)
                    else:
                        def _mark_used(name=aname):
                            set_npc_cooldown(c_name, name, True)
                            _send(f"**{name}** a été utilisé et doit être rechargé.", FG_DIM)
                        _qbtn("🟢 Action Prête", "#1a351a", GREEN, _mark_used)

                if rolls["hit"] is not None:
                    bonus = rolls["hit"]
                    sign  = "+" if bonus >= 0 else ""

                    crit_lbl = tk.Label(
                        btns, text="", bg=BG_ACT, fg="#ff4444",
                        font=("Consolas", 7, "bold"), padx=4)

                    def _set_crit(is_crit: bool, cv=crit_var, lbl=crit_lbl):
                        cv.set(is_crit)
                        lbl.config(text="🎯 CRIT" if is_crit else "")

                    def _atk(b=bonus, n=aname, set_c=_set_crit):
                        _consume_if_needed(n)
                        d20  = _rnd.randint(1, 20)
                        tot  = d20 + b
                        s    = "+" if b >= 0 else ""
                        is_crit = d20 == 20
                        set_c(is_crit)
                        crit = (" 🎯 CRITIQUE!" if is_crit
                                else " ☠ FUMBLE"  if d20 == 1 else "")
                        tgt  = target_var.get()
                        msg  = (f"**{n}** → {tgt}\n"
                                f"  d20({d20}) {s}{b} = **{tot}**{crit}")
                        _send(msg, RED)

                    _qbtn(f"⚔ Atk {sign}{bonus}", BG_ATK, RED, _atk)

                    def _toggle_crit(set_c=_set_crit, cv=crit_var):
                        set_c(not cv.get())
                    tk.Button(
                        btns, text="🎯",
                        bg=BG_ATK, fg="#cc4444",
                        activebackground="#3a0808", activeforeground="#ff6666",
                        font=("Consolas", 7), relief="flat", bd=0,
                        padx=3, pady=2, cursor="hand2",
                        command=_toggle_crit
                    ).pack(side=tk.LEFT, padx=(0, 4))

                    crit_lbl.pack(side=tk.LEFT, padx=(0, 6))

                for i, (expr, dmg_type) in enumerate(rolls["damages"]):
                    t_lbl = f" {dmg_type}" if dmg_type else ""
                    btn_t = (f"💥 {expr}{t_lbl}" if i == 0
                             else f"+ {expr}{t_lbl}")

                    def _dmg(e=expr, t=dmg_type, n=aname, cv=crit_var,
                             set_c=_set_crit if rolls["hit"] is not None else None):
                        _consume_if_needed(n)
                        is_crit  = cv.get()
                        eff_expr = _double_dice_expr(e) if is_crit else e
                        total, detail = _roll_dice(eff_expr)
                        ts         = f" {t}" if t else ""
                        crit_note  = " *(CRITIQUE — dés ×2)*" if is_crit else ""
                        msg = (f"**{n}** — Dégâts{ts}{crit_note}\n"
                               f"  {eff_expr} → {detail} = **{total}**")
                        _send(msg, ORANGE)
                        if set_c is not None:
                            set_c(False)

                    _qbtn(btn_t, BG_DMG, ORANGE, _dmg)

                if rolls["dc"] is not None:
                    sv_lbl = rolls["dc_save"].upper() if rolls["dc_save"] else "JdS"
                    dc_val = rolls["dc"]

                    def _dc(dc=dc_val, sv=sv_lbl, n=aname):
                        _consume_if_needed(n)
                        tgt = target_var.get()
                        msg = (f"**{n}** — JdS DD {dc} ({sv})\n"
                               f"  {tgt} doit réussir !")
                        _send(msg, BLUE)

                    _qbtn(f"DD {dc_val} {sv_lbl}", BG_DC, BLUE, _dc)

                if (rolls["hit"] is None and rolls["dc"] is None
                        and not rolls["damages"]):
                    name_lbl.config(fg=FG_DIM,
                                    font=("Consolas", 8, "italic"))

            _sep()

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

        _build_section("◆ ACTIONS",                 monster.get("action",    []), RED)
        _build_section("◈ ACTIONS BONUS",           monster.get("bonus",     []), ORANGE)
        _build_section("◇ RÉACTIONS",               monster.get("reaction",  []), PURPLE)
        _build_section("★ ACTIONS LÉGENDAIRES",     monster.get("legendary", []), GOLD)

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

        tk.Frame(outer, bg="#1c2a3a", height=2).pack(fill=tk.X)

        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.insert(tk.END, "\n")
        self.chat_display.window_create(tk.END, window=outer)
        self.chat_display.insert(tk.END, "\n")
        self.chat_display.config(state=tk.DISABLED)
        
        def _force_scroll():
            try:
                self.chat_display.update_idletasks()
                self.chat_display.yview_moveto(1.0)
            except Exception: pass
        self.chat_display.after(50, _force_scroll)
        self.chat_display.after(250, _force_scroll)