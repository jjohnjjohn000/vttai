"""
panels_calendar_mixin.py

Contient la gestion du calendrier barovien (widgets, avancement et popout interactif).
"""

import tkinter as tk
from panels_core_mixin import _ghost_close
from window_state import _save_window_state, _get_win_geometry
from state_manager import (
    get_calendar, save_calendar, advance_day, 
    lunar_phase, BAROVIAN_MONTHS, DAYS_PER_MONTH
)


class PanelsCalendarMixin:
    """Mixin gérant l'interface du calendrier barovien."""

    def _refresh_calendar_widget(self):
        """Met à jour les labels du mini-widget calendrier dans la sidebar."""
        try:
            cal = get_calendar()
            d, m, y = cal["day"], cal["month"], cal["year"]
            month_name = BAROVIAN_MONTHS[m - 1]
            icon, short, long_name = lunar_phase(d)
            self._cal_date_label.config(text=f"{d} {month_name}, An {y}")
            self._cal_moon_label.config(text=f"{icon} {long_name}")
        except Exception as e:
            print(f"[calendar widget] {e}")

    def _advance_calendar(self, n: int):
        """Avance le calendrier de n jours et notifie le chat."""
        cal = advance_day(n)
        self._refresh_calendar_widget()
        d, m, y = cal["day"], cal["month"], cal["year"]
        month_name = BAROVIAN_MONTHS[m - 1]
        icon, _, long_name = lunar_phase(d)
        self.msg_queue.put({
            "sender": "📅 Calendrier",
            "text":   f"{'▶ +1 jour' if n == 1 else f'▶▶ +{n} jours'}  →  "
                      f"{d} {month_name}, An {y}  {icon} {long_name}",
            "color":  "#9b8fc7"
        })
        # Rafraîchit le popout s'il est ouvert
        if getattr(self, "_calendar_popout", None):
            try:
                self._calendar_popout._do_refresh()
            except Exception:
                pass

    def open_calendar_popout(self):
        """Ouvre (ou ramène) le popout du calendrier barovien."""

        if getattr(self, "_calendar_popout", None):
            try:
                self._calendar_popout.deiconify()
                self._calendar_popout.lift()
                return
            except Exception:
                self._calendar_popout = None

        # ── Palette gothique / lunaire ─────────────────────────────────────
        BG       = "#07070f"
        BG2      = "#0d0d1e"
        BG3      = "#12122a"
        HEADER   = "#0a0a1a"
        CELL_BG  = "#0f0f20"
        CELL_HOV = "#1a1a35"
        FG       = "#c8b8ff"
        FG_DIM   = "#4a3a6a"
        FG_MID   = "#7a6a9a"
        ACCENT   = "#7c5cbf"
        TODAY_C  = "#5c3a8f"
        MOON_C   = "#e8d8ff"
        NOTE_C   = "#e9c46a"
        FULL_C   = "#fffde7"

        win = tk.Toplevel(self.root)
        win.withdraw()  # Fix XWayland mapping freeze
        win.title("📅 Calendrier Barovien")
        win.configure(bg=BG)
        win.resizable(False, False)
        self._calendar_popout = win

        _key   = "calendar"
        saved  = self._win_state.get(_key)
        if saved and all(k in saved for k in ("w","h","x","y")):
            win.geometry(f"{saved['w']}x{saved['h']}+{saved['x']}+{saved['y']}")
        else:
            win.geometry("420x540")

        self._win_state["_open_calendar"] = True
        _save_window_state(self._win_state)

        def _on_close():
            g = _get_win_geometry(win)
            if g: self._win_state[_key] = g
            self._win_state.pop("_open_calendar", None)
            _save_window_state(self._win_state)
            self._calendar_popout = None
            _ghost_close(win, self.root)
        win.protocol("WM_DELETE_WINDOW", _on_close)

        # ── Vue courante (mois/année affiché, pas forcément today) ────────
        _view = {"month": None, "year": None}  # sera initialisé dans _do_refresh

        # ── Titre ─────────────────────────────────────────────────────────
        title_frame = tk.Frame(win, bg=HEADER)
        title_frame.pack(fill=tk.X)
        tk.Label(title_frame, text="Calendrier des Douze Lunes",
                 bg=HEADER, fg=FG_MID, font=("Consolas", 9, "italic")).pack(pady=(8, 0))

        # ── Navigation mois ────────────────────────────────────────────────
        nav_frame = tk.Frame(win, bg=HEADER)
        nav_frame.pack(fill=tk.X, padx=12, pady=(4, 8))

        btn_prev_y = tk.Button(nav_frame, text="《", bg=HEADER, fg=FG_DIM,
                               font=("Arial", 10, "bold"), relief="flat",
                               activebackground=BG3, activeforeground=FG,
                               command=lambda: _change_view(0, -1))
        btn_prev_y.pack(side=tk.LEFT)
        btn_prev_m = tk.Button(nav_frame, text="‹", bg=HEADER, fg=ACCENT,
                               font=("Arial", 13, "bold"), relief="flat",
                               activebackground=BG3, activeforeground=FG,
                               command=lambda: _change_view(-1, 0))
        btn_prev_m.pack(side=tk.LEFT)

        month_label = tk.Label(nav_frame, text="", bg=HEADER, fg=FG,
                               font=("Consolas", 12, "bold"), width=22, anchor="center")
        month_label.pack(side=tk.LEFT, expand=True)

        btn_next_m = tk.Button(nav_frame, text="›", bg=HEADER, fg=ACCENT,
                               font=("Arial", 13, "bold"), relief="flat",
                               activebackground=BG3, activeforeground=FG,
                               command=lambda: _change_view(1, 0))
        btn_next_m.pack(side=tk.RIGHT)
        btn_next_y = tk.Button(nav_frame, text="》", bg=HEADER, fg=FG_DIM,
                               font=("Arial", 10, "bold"), relief="flat",
                               activebackground=BG3, activeforeground=FG,
                               command=lambda: _change_view(0, 1))
        btn_next_y.pack(side=tk.RIGHT)

        def _change_view(dm, dy):
            m = _view["month"] + dm
            y = _view["year"]  + dy
            if m > 12: m, y = 1, y + 1
            if m < 1:  m, y = 12, y - 1
            _view["month"], _view["year"] = m, y
            _render_grid()

        # ── En-têtes des jours ─────────────────────────────────────────────
        days_hdr = tk.Frame(win, bg=BG2)
        days_hdr.pack(fill=tk.X, padx=8)
        for d_name in ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]:
            tk.Label(days_hdr, text=d_name, bg=BG2, fg=FG_DIM,
                     font=("Consolas", 8), width=5, anchor="center").pack(
                side=tk.LEFT, expand=True)

        # ── Grille des jours (Canvas pour perf + hover propre) ────────────
        CELL_W, CELL_H = 54, 56
        COLS, ROWS = 7, 4
        GRID_W = CELL_W * COLS
        GRID_H = CELL_H * ROWS

        grid_frame = tk.Frame(win, bg=BG, padx=8, pady=4)
        grid_frame.pack()

        canvas = tk.Canvas(grid_frame, width=GRID_W, height=GRID_H,
                           bg=BG, highlightthickness=0)
        canvas.pack()

        # ── Barre de statut : date today ──────────────────────────────────
        status_frame = tk.Frame(win, bg=BG3)
        status_frame.pack(fill=tk.X)
        tk.Frame(win, bg=ACCENT, height=1).pack(fill=tk.X)

        today_lbl = tk.Label(status_frame, text="",
                             bg=BG3, fg=FG, font=("Consolas", 10, "bold"))
        today_lbl.pack(side=tk.LEFT, padx=14, pady=6)
        moon_lbl  = tk.Label(status_frame, text="",
                             bg=BG3, fg=MOON_C, font=("Arial", 13))
        moon_lbl.pack(side=tk.LEFT, pady=6)

        # ── Zone note du jour (click) ─────────────────────────────────────
        note_frame = tk.Frame(win, bg=BG)
        note_frame.pack(fill=tk.X, padx=8, pady=(4, 0))
        note_lbl = tk.Label(note_frame, text="", bg=BG, fg=NOTE_C,
                            font=("Consolas", 8, "italic"), anchor="w",
                            wraplength=380, justify=tk.LEFT)
        note_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        edit_note_btn = tk.Button(note_frame, text="✏", bg=BG, fg=FG_DIM,
                                  font=("TkDefaultFont", 8), relief="flat",
                                  command=lambda: _edit_note())
        edit_note_btn.pack(side=tk.RIGHT, padx=4)

        # Boutons avance rapide (bas)
        adv_frame = tk.Frame(win, bg=BG)
        adv_frame.pack(fill=tk.X, padx=8, pady=(4, 8))
        for label, n in [("+1 jour", 1), ("+7 jours", 7), ("+28 jours", 28)]:
            tk.Button(adv_frame, text=label, bg=BG3, fg=FG_MID,
                      font=("Consolas", 8, "bold"), relief="flat",
                      padx=8, pady=3,
                      activebackground=CELL_HOV, activeforeground=FG,
                      command=lambda n=n: (_adv(n))).pack(side=tk.LEFT, padx=(0, 4))

        def _adv(n):
            self._advance_calendar(n)
            # Resync la vue sur le nouveau today
            cal = get_calendar()
            _view["month"], _view["year"] = cal["month"], cal["year"]
            _do_refresh()

        # ── Rendu grille ──────────────────────────────────────────────────
        _hover_day = [None]

        def _render_grid():
            canvas.delete("all")
            cal   = get_calendar()
            today = (cal["day"], cal["month"], cal["year"])
            vm, vy = _view["month"], _view["year"]
            month_name = BAROVIAN_MONTHS[vm - 1]
            month_label.config(text=f"{month_name}  ·  An {vy}")

            # Tous les jours du mois en 4×7
            for i, day in enumerate(range(1, DAYS_PER_MONTH + 1)):
                col = i % COLS
                row = i // COLS
                x0  = col * CELL_W
                y0  = row * CELL_H

                is_today  = (day == today[0] and vm == today[1] and vy == today[2])
                is_hover  = (_hover_day[0] == day)
                icon, short, phase_long = lunar_phase(day)
                is_full   = (short == "PL")
                is_new    = (short == "NL")

                # Fond cellule
                if is_today:
                    cell_color = TODAY_C
                elif is_hover:
                    cell_color = CELL_HOV
                else:
                    cell_color = CELL_BG if row % 2 == 0 else BG2
                canvas.create_rectangle(x0+1, y0+1, x0+CELL_W-1, y0+CELL_H-1,
                                        fill=cell_color, outline=BG3, width=1)

                # Bordure accent pour aujourd'hui
                if is_today:
                    canvas.create_rectangle(x0+1, y0+1, x0+CELL_W-1, y0+CELL_H-1,
                                            fill="", outline=ACCENT, width=2)

                # Numéro du jour
                day_fg = FULL_C if is_full else (FG if is_today else FG_MID if is_new else FG_DIM)
                canvas.create_text(x0 + CELL_W//2, y0 + 11,
                                   text=str(day), fill=day_fg,
                                   font=("Consolas", 8, "bold" if is_today else "normal"),
                                   anchor="center")

                # Icône lunaire
                moon_fg = FULL_C if is_full else (MOON_C if is_today else FG_MID)
                canvas.create_text(x0 + CELL_W//2, y0 + CELL_H//2 + 8,
                                   text=icon, fill=moon_fg,
                                   font=("Arial", 16), anchor="center")

                # Point note si existante
                note_key = f"{vy}-{vm}-{day}"
                if get_calendar().get("notes", {}).get(note_key):
                    canvas.create_oval(x0+CELL_W-10, y0+4, x0+CELL_W-4, y0+10,
                                       fill=NOTE_C, outline="")

        def _on_canvas_click(event):
            col = event.x // CELL_W
            row = event.y // CELL_H
            day = row * COLS + col + 1
            if 1 <= day <= DAYS_PER_MONTH:
                cal = get_calendar()
                cal["day"], cal["month"], cal["year"] = day, _view["month"], _view["year"]
                save_calendar(cal)
                _do_refresh()
                self._refresh_calendar_widget()
                self.msg_queue.put({
                    "sender": "📅 Calendrier",
                    "text":   f"Date fixée : {day} {BAROVIAN_MONTHS[_view['month']-1]}, An {_view['year']}",
                    "color":  "#9b8fc7"
                })

        def _on_canvas_motion(event):
            col  = event.x // CELL_W
            row  = event.y // CELL_H
            day  = row * COLS + col + 1
            prev = _hover_day[0]
            if 1 <= day <= DAYS_PER_MONTH:
                _hover_day[0] = day
            else:
                _hover_day[0] = None
            if _hover_day[0] != prev:
                _render_grid()

        def _on_canvas_leave(event):
            if _hover_day[0] is not None:
                _hover_day[0] = None
                _render_grid()

        canvas.bind("<Button-1>",  _on_canvas_click)
        canvas.bind("<Motion>",    _on_canvas_motion)
        canvas.bind("<Leave>",     _on_canvas_leave)

        # ── Refresh complet ───────────────────────────────────────────────
        def _do_refresh():
            cal = get_calendar()
            d, m, y = cal["day"], cal["month"], cal["year"]
            if _view["month"] is None:
                _view["month"], _view["year"] = m, y
            _render_grid()
            month_name = BAROVIAN_MONTHS[m - 1]
            icon, _, long_name = lunar_phase(d)
            today_lbl.config(text=f"{d} {month_name}, An {y}  ")
            moon_lbl.config(text=f"{icon}  {long_name}")
            # Note du jour
            note_key = f"{y}-{m}-{d}"
            note_txt = cal.get("notes", {}).get(note_key, "")
            note_lbl.config(text=f"📌 {note_txt}" if note_txt else "— aucune note —")

        win._do_refresh = _do_refresh   # exposition pour _advance_calendar

        def _edit_note():
            cal = get_calendar()
            d, m, y = cal["day"], cal["month"], cal["year"]
            note_key = f"{y}-{m}-{d}"
            month_name = BAROVIAN_MONTHS[m - 1]

            ew = tk.Toplevel(win)
            ew.title(f"📌 Note — {d} {month_name} An {y}")
            ew.geometry("360x160")
            ew.configure(bg=BG)
            ew.resizable(False, False)
            ew.grab_set()

            tk.Label(ew, text=f"{d} {month_name}, An {y}",
                     bg=BG, fg=FG_MID, font=("Consolas", 9, "italic")).pack(pady=(10, 4))
            txt = tk.Text(ew, height=3, bg=BG3, fg=FG, font=("Consolas", 10),
                          insertbackground=FG, relief="flat", wrap=tk.WORD)
            txt.pack(fill=tk.X, padx=14)
            txt.insert("1.0", cal.get("notes", {}).get(note_key, ""))

            def _save_note():
                note = txt.get("1.0", tk.END).strip()
                cal2 = get_calendar()
                if note:
                    cal2.setdefault("notes", {})[note_key] = note
                else:
                    cal2.get("notes", {}).pop(note_key, None)
                save_calendar(cal2)
                _do_refresh()
                _ghost_close(ew, self.root)

            tk.Button(ew, text="✅ Sauvegarder", bg="#1a1a2e", fg=FG,
                      font=("Arial", 9, "bold"), relief="flat",
                      command=_save_note).pack(pady=8)

        # ── Lancement ─────────────────────────────────────────────────────
        _do_refresh()
        
        win.after(20, win.deiconify)
        win.after(40, win.lift)