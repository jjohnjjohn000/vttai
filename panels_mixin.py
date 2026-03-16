"""
panels_mixin.py — PanelsMixin : tous les panneaux et fenêtres flottantes.

Contient :
  - _refresh_scene_widget, _refresh_calendar_widget, _advance_calendar
  - open_calendar_popout
  - open_location_image_popout
  - open_scene_editor
  - _rebuild_npc_menu, _refresh_npc_dropdown, _on_npc_selected, open_npc_manager
  - open_quest_journal
  - open_dice_roller (×2 — la seconde définition est l'effective)
  - open_skill_check_dialog
  - open_combat_map
"""

import os
import re
import threading
import tkinter as tk
from tkinter import scrolledtext, filedialog

from window_state import _save_window_state, _get_win_geometry
from state_manager import (
    load_state, save_state, get_scene, save_scene,
    get_npcs, save_npcs, AVAILABLE_VOICES,
    get_quests, save_quests, get_active_quests_prompt, QUEST_STATUSES,
    get_calendar, save_calendar, advance_day, get_calendar_prompt,
    lunar_phase, BAROVIAN_MONTHS, DAYS_PER_MONTH,
    get_location_image_base64, roll_dice,
)
from llm_config import DND_SKILLS, ABILITY_COLORS, llm_config
from app_config import reload_app_config
from config_panel import open_config_panel as _open_cfg_panel
from combat_map_panel import open_combat_map as _open_combat_map


class PanelsMixin:
    """Mixin pour DnDApp — panneaux flottants et fenêtres modales."""

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

    # ─── CALENDRIER BAROVIEN ──────────────────────────────────────────────────


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
            win.destroy()
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
                ew.destroy()

            tk.Button(ew, text="✅ Sauvegarder", bg="#1a1a2e", fg=FG,
                      font=("Arial", 9, "bold"), relief="flat",
                      command=_save_note).pack(pady=8)

        # ── Lancement ─────────────────────────────────────────────────────
        _do_refresh()

    # ─── POPOUT IMAGE DU LIEU ─────────────────────────────────────────────────


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
            win.destroy()

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


    def open_scene_editor(self):
        """Fenêtre d'édition du contexte de scène."""
        win = tk.Toplevel(self.root)
        win.title("🗺️ Contexte de Scène")
        win.geometry("680x620")
        win.configure(bg="#0d1117")
        win.grab_set()
        self._track_window("modal_scene_editor", win)

        scene = get_scene()

        # ── En-tête ──
        hdr = tk.Frame(win, bg="#0d2010")
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="🗺️  Contexte de la Scène Actuelle", bg="#0d2010", fg="#81c784",
                 font=("Arial", 13, "bold")).pack(side=tk.LEFT, padx=14, pady=10)
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

        f_npcs   = list_field(scene.get("npcs_presents", []),   "PNJs présents (un par ligne)")
        f_objets = list_field(scene.get("objets_notables", []), "Elements notables (un par ligne)")

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
                img.thumbnail((220, 110), _PILImage.LANCZOS)
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

        # ── Boutons ──
        btn_frame = tk.Frame(win, bg="#0d1117")
        btn_frame.pack(fill=tk.X, padx=16, pady=12)

        def parse_list(widget):
            return [l.strip() for l in widget.get("1.0", tk.END).strip().splitlines() if l.strip()]

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
            if new_image and new_image != old_image and self._agents:
                self.msg_queue.put({
                    "sender": "🖼️ Système",
                    "text": "📸 Nouvelle image de lieu détectée — envoi aux agents multimodaux...",
                    "color": "#81c784"
                })
                self.root.after(500, self._broadcast_location_image)
            win.destroy()

        def reset_scene():
            from state_manager import DEFAULT_SCENE
            save_scene(DEFAULT_SCENE.copy())
            self._refresh_scene_widget()
            win.destroy()

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
                  command=win.destroy).pack(side=tk.RIGHT)

    # --- MÉTHODES PNJ ---

    def _rebuild_npc_menu(self):
        """Remplit self._npc_menu — appelé au 1er clic, jamais pendant setup_ui."""
        if self._npc_menu is None: return
        self._npc_menu.delete(0, "end")
        self._npc_menu.add_command(label="— MJ Normal —",
                                   command=lambda: self._on_npc_selected("— MJ Normal —"))
        for npc in get_npcs():
            n = npc["name"]
            self._npc_menu.add_command(label=n, command=lambda name=n: self._on_npc_selected(name))


    def _refresh_npc_dropdown(self):
        """Invalide le menu PNJ (rebuild au prochain clic)."""
        self._npc_menu = None
        if self.active_npc:
            names = [n["name"] for n in get_npcs()]
            if self.active_npc["name"] not in names:
                self._on_npc_selected("— MJ Normal —")


    def _on_npc_selected(self, selected_name: str):
        """Callback quand le MJ sélectionne un PNJ ou revient en mode MJ normal."""
        self._npc_var.set(selected_name)
        # Synchronise le bouton inline dans la zone de saisie
        _inline_label = "MJ" if selected_name == "— MJ Normal —" else selected_name
        try:
            self._inline_npc_var.set(_inline_label)
        except Exception:
            pass
        if selected_name == "— MJ Normal —":
            self.active_npc = None
            self._npc_indicator.config(text="", fg="white")
            self.entry.config(bg="#3d3d3d")
        else:
            npcs = get_npcs()
            npc = next((n for n in npcs if n["name"] == selected_name), None)
            self.active_npc = npc
            if npc:
                color = npc.get("color", "#c77dff")
                self._npc_indicator.config(
                    text=f"Voix: {npc.get('voice','?')}  Vitesse: {npc.get('speed','+0%')}",
                    fg=color
                )
                self.entry.config(bg="#2d1d3d")  # Teinte violette pour rappeler le mode PNJ


    def open_config_panel(self):
        """Ouvre le panneau de configuration général de l'application."""
        def _on_saved(new_cfg):
            """Callback appelé après sauvegarde : recharge la config et met les agents à jour live."""
            reload_app_config()
            # Si les agents sont déjà initialisés, reconstruire leurs prompts immédiatement
            if hasattr(self, "_agents") and self._agents:
                try:
                    self._rebuild_agent_prompts()
                    self.msg_queue.put({
                        "sender": "⚙️ Config",
                        "text":   "✅ Paramètres appliqués aux agents en cours de session.",
                        "color":  "#aaaacc",
                    })
                except Exception as e:
                    print(f"[Config] Erreur mise à jour agents : {e}")

        _open_cfg_panel(
            root       = self.root,
            win_state  = self._win_state,
            track_fn   = self._track_window,
            on_saved   = _on_saved,
        )

    def open_npc_manager(self):
        """Ouvre la fenêtre de gestion des PNJs (ajout / édition / suppression)."""
        win = tk.Toplevel(self.root)
        win.title("⚙️ Gestionnaire de PNJs")
        win.geometry("680x520")
        win.configure(bg="#1e1e1e")
        win.grab_set()
        self._track_window("modal_npc_manager", win)

        npcs = get_npcs()

        # --- En-tête ---
        tk.Label(win, text="🎭 Personnages Non-Joueurs", bg="#1e1e1e", fg="#c77dff",
                 font=("Arial", 13, "bold")).pack(pady=(12, 4))
        tk.Label(win, text="Définissez les PNJs que le MJ peut incarner avec leur voix TTS.",
                 bg="#1e1e1e", fg="#888888", font=("Arial", 9)).pack(pady=(0, 8))

        # --- Liste scrollable ---
        list_frame = tk.Frame(win, bg="#1e1e1e")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=14)

        canvas = tk.Canvas(list_frame, bg="#1e1e1e", highlightthickness=0)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        self._npc_scroll_frame = tk.Frame(canvas, bg="#1e1e1e")

        self._npc_scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self._npc_scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Header colonnes
        header = tk.Frame(self._npc_scroll_frame, bg="#2a2a2a")
        header.pack(fill=tk.X, pady=(0, 4))
        for text, w in [("Nom", 12), ("Voix Edge-TTS", 22), ("Vitesse", 7), ("Couleur", 8), ("", 8)]:
            tk.Label(header, text=text, bg="#2a2a2a", fg="#ffcc00",
                     font=("Arial", 9, "bold"), width=w, anchor="w").pack(side=tk.LEFT, padx=3)

        # Lignes de PNJs
        self._npc_rows = []  # liste de dicts {name_var, voice_var, speed_var, color_var}

        def build_rows():
            for widget in self._npc_scroll_frame.winfo_children():
                if isinstance(widget, tk.Frame) and widget != header:
                    widget.destroy()
            self._npc_rows.clear()
            for i, npc in enumerate(npcs):
                row_bg = "#252526" if i % 2 == 0 else "#2d2d2d"
                row = tk.Frame(self._npc_scroll_frame, bg=row_bg)
                row.pack(fill=tk.X, pady=1)

                name_var  = tk.StringVar(value=npc.get("name", ""))
                voice_var = tk.StringVar(value=npc.get("voice", "fr-FR-HenriNeural"))
                speed_var = tk.StringVar(value=npc.get("speed", "+0%"))
                color_var = tk.StringVar(value=npc.get("color", "#c77dff"))

                tk.Entry(row, textvariable=name_var, width=12, bg="#3d3d3d", fg="white",
                         font=("Consolas", 10), insertbackground="white").pack(side=tk.LEFT, padx=3, ipady=3)

                voice_menu = tk.OptionMenu(row, voice_var, *AVAILABLE_VOICES)
                voice_menu.config(bg="#3d2d4d", fg="white", font=("Consolas", 9),
                                  width=20, relief="flat", highlightthickness=0)
                voice_menu["menu"].config(bg="#3d2d4d", fg="white", font=("Consolas", 9))
                voice_menu.pack(side=tk.LEFT, padx=3)

                tk.Entry(row, textvariable=speed_var, width=7, bg="#3d3d3d", fg="white",
                         font=("Consolas", 10), insertbackground="white").pack(side=tk.LEFT, padx=3, ipady=3)

                color_entry = tk.Entry(row, textvariable=color_var, width=8, bg="#3d3d3d",
                                       font=("Consolas", 10), insertbackground="white")
                color_entry.pack(side=tk.LEFT, padx=3, ipady=3)
                # Aperçu couleur live
                def _update_color(var=color_var, entry=color_entry, *args):
                    try:
                        c = var.get()
                        entry.config(fg=c)
                    except Exception:
                        entry.config(fg="white")
                color_var.trace_add("write", _update_color)
                _update_color()

                def remove_row(idx=i):
                    npcs.pop(idx)
                    build_rows()

                tk.Button(row, text="✕", bg="#5a1a1a", fg="#ff6b6b",
                          font=("Arial", 9, "bold"), width=3,
                          command=remove_row).pack(side=tk.LEFT, padx=4)

                self._npc_rows.append({
                    "name": name_var, "voice": voice_var,
                    "speed": speed_var, "color": color_var
                })

        build_rows()

        # --- Barre du bas : Ajouter + Sauvegarder ---
        bottom = tk.Frame(win, bg="#1e1e1e")
        bottom.pack(fill=tk.X, padx=14, pady=10)

        def add_npc():
            npcs.append({
                "name": "Nouveau PNJ",
                "voice": "fr-FR-HenriNeural",
                "speed": "+0%",
                "color": "#c77dff"
            })
            build_rows()

        def save_and_close():
            # Lit toutes les lignes du formulaire
            updated = []
            for row_vars in self._npc_rows:
                name = row_vars["name"].get().strip()
                if name:
                    updated.append({
                        "name":  name,
                        "voice": row_vars["voice"].get(),
                        "speed": row_vars["speed"].get(),
                        "color": row_vars["color"].get(),
                    })
            save_npcs(updated)
            # Mise à jour du VOICE_MAPPING dynamique pour les PNJs
            from voice_interface import VOICE_MAPPING, SPEED_MAPPING
            for npc in updated:
                key = f"__npc__{npc['name']}"
                VOICE_MAPPING[key]  = npc["voice"]
                SPEED_MAPPING[key]  = npc["speed"]
            self._refresh_npc_dropdown()
            win.destroy()

        tk.Button(bottom, text="＋ Ajouter un PNJ", bg="#2d4a2d", fg="#4CAF50",
                  font=("Arial", 10, "bold"), command=add_npc).pack(side=tk.LEFT)
        tk.Button(bottom, text="✅ Sauvegarder", bg="#4CAF50", fg="white",
                  font=("Arial", 10, "bold"), command=save_and_close).pack(side=tk.RIGHT)
        tk.Button(bottom, text="Annuler", bg="#3d3d3d", fg="white",
                  font=("Arial", 10), command=win.destroy).pack(side=tk.RIGHT, padx=8)


    # --- JOURNAL DE QUÊTES ---

    def open_quest_journal(self):
        """Fenêtre de gestion du journal de quêtes."""
        win = tk.Toplevel(self.root)
        win.title("📜 Journal de Quêtes")
        win.geometry("820x620")
        win.configure(bg="#0d1117")
        win.grab_set()
        self._track_window("modal_quest_journal", win)

        quests = get_quests()

        STATUS_COLORS = {
            "active":    "#64b5f6",
            "completed": "#81c784",
            "failed":    "#e57373",
        }
        STATUS_LABELS = {
            "active":    "⚔️ Active",
            "completed": "✅ Complétée",
            "failed":    "💀 Échouée",
        }
        CATEGORY_COLORS = {
            "Principale": "#ffcc00",
            "Secondaire": "#ce93d8",
            "Personnelle": "#80deea",
        }

        # ── En-tête ──────────────────────────────────────────────
        header = tk.Frame(win, bg="#161b22")
        header.pack(fill=tk.X)
        tk.Label(header, text="📜  Journal de Quêtes", bg="#161b22", fg="#64b5f6",
                 font=("Arial", 14, "bold")).pack(side=tk.LEFT, padx=16, pady=10)
        tk.Button(header, text="＋ Nouvelle quête", bg="#1a3a5c", fg="#64b5f6",
                  font=("Arial", 10, "bold"), relief="flat",
                  command=lambda: open_quest_editor(None)).pack(side=tk.RIGHT, padx=12, pady=8)

        # ── Filtre par statut ─────────────────────────────────────
        filter_frame = tk.Frame(win, bg="#0d1117")
        filter_frame.pack(fill=tk.X, padx=12, pady=(6, 0))
        filter_var = tk.StringVar(value="all")

        def set_filter(v):
            filter_var.set(v)
            refresh_list()

        for val, label in [("all","Toutes"), ("active","Actives"), ("completed","Complétées"), ("failed","Échouées")]:
            color = STATUS_COLORS.get(val, "#aaaaaa")
            tk.Button(filter_frame, text=label, bg="#161b22", fg=color,
                      font=("Arial", 9, "bold"), relief="flat", padx=8,
                      command=lambda v=val: set_filter(v)).pack(side=tk.LEFT, padx=2)

        # ── Zone principale : liste à gauche, détail à droite ────
        pane = tk.Frame(win, bg="#0d1117")
        pane.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        # Colonne liste
        list_col = tk.Frame(pane, bg="#0d1117", width=280)
        list_col.pack(side=tk.LEFT, fill=tk.Y)
        list_col.pack_propagate(False)

        list_canvas = tk.Canvas(list_col, bg="#0d1117", highlightthickness=0, width=270)
        list_scroll = tk.Scrollbar(list_col, orient="vertical", command=list_canvas.yview)
        list_inner = tk.Frame(list_canvas, bg="#0d1117")
        list_inner.bind("<Configure>", lambda e: list_canvas.configure(scrollregion=list_canvas.bbox("all")))
        list_canvas.create_window((0, 0), window=list_inner, anchor="nw")
        list_canvas.configure(yscrollcommand=list_scroll.set)
        list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Séparateur vertical
        tk.Frame(pane, bg="#30363d", width=1).pack(side=tk.LEFT, fill=tk.Y, padx=6)

        # Colonne détail
        detail_col = tk.Frame(pane, bg="#0d1117")
        detail_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Widget de détail (label + scrolledtext pour notes/objectifs)
        detail_title  = tk.Label(detail_col, text="", bg="#0d1117", fg="#64b5f6",
                                  font=("Arial", 13, "bold"), wraplength=460, justify=tk.LEFT)
        detail_title.pack(anchor="w", pady=(6,2), padx=8)

        detail_meta   = tk.Label(detail_col, text="", bg="#0d1117", fg="#888888",
                                  font=("Arial", 9), justify=tk.LEFT)
        detail_meta.pack(anchor="w", padx=8)

        detail_desc   = tk.Label(detail_col, text="", bg="#0d1117", fg="#e0e0e0",
                                  font=("Consolas", 10), wraplength=460, justify=tk.LEFT)
        detail_desc.pack(anchor="w", pady=(8,4), padx=8)

        obj_frame = tk.Frame(detail_col, bg="#0d1117")
        obj_frame.pack(fill=tk.X, padx=8)

        notes_label   = tk.Label(detail_col, text="", bg="#0d1117", fg="#e9c46a",
                                  font=("Consolas", 9, "italic"), wraplength=460, justify=tk.LEFT)
        notes_label.pack(anchor="w", pady=(6,0), padx=8)

        btn_row = tk.Frame(detail_col, bg="#0d1117")
        btn_row.pack(anchor="w", padx=8, pady=10)

        selected_id = [None]  # mutable ref

        def show_detail(quest_id):
            selected_id[0] = quest_id
            q = next((x for x in quests if x["id"] == quest_id), None)
            if not q:
                return
            color = STATUS_COLORS.get(q["status"], "#aaaaaa")
            cat_color = CATEGORY_COLORS.get(q.get("category",""), "#888888")
            detail_title.config(text=q["title"], fg=color)
            detail_meta.config(
                text=f"[{q.get('category','?')}]  •  {STATUS_LABELS.get(q['status'],'?')}",
                fg=cat_color
            )
            detail_desc.config(text=q.get("description",""))

            # Objectifs avec checkboxes
            for w in obj_frame.winfo_children():
                w.destroy()
            objs = q.get("objectives", [])
            if objs:
                tk.Label(obj_frame, text="Objectifs :", bg="#0d1117", fg="#aaaaaa",
                         font=("Arial", 9, "bold")).pack(anchor="w", pady=(4,2))
            for i, obj in enumerate(objs):
                row = tk.Frame(obj_frame, bg="#0d1117")
                row.pack(fill=tk.X, pady=1)
                done_var = tk.BooleanVar(value=obj.get("done", False))
                chk = tk.Checkbutton(row, variable=done_var, bg="#0d1117",
                                     activebackground="#0d1117",
                                     selectcolor="#1a3a5c",
                                     fg="#81c784" if obj.get("done") else "#e0e0e0",
                                     text=obj["text"], font=("Consolas", 10),
                                     anchor="w", justify=tk.LEFT, wraplength=400)
                chk.pack(side=tk.LEFT)
                def toggle_obj(idx=i, var=done_var, q_id=quest_id):
                    qx = next((x for x in quests if x["id"] == q_id), None)
                    if qx:
                        qx["objectives"][idx]["done"] = var.get()
                        save_quests(quests)
                done_var.trace_add("write", lambda *a, idx=i, var=done_var, q_id=quest_id: toggle_obj(idx, var, q_id))

            notes_label.config(text=f"⚠️ {q['notes']}" if q.get("notes") else "")

            # Boutons d'action
            for w in btn_row.winfo_children():
                w.destroy()
            tk.Button(btn_row, text="✏️ Modifier",  bg="#1a3a5c", fg="#64b5f6",
                      font=("Arial", 9, "bold"), relief="flat",
                      command=lambda qid=quest_id: open_quest_editor(qid)).pack(side=tk.LEFT, padx=(0,6))

            # Boutons de changement de statut
            for st, lbl, bg, fg in [
                ("active",    "↩ Réactiver", "#1a3a1a", "#64b5f6"),
                ("completed", "✅ Compléter", "#1a3a1a", "#81c784"),
                ("failed",    "💀 Échouer",  "#3a1a1a", "#e57373"),
            ]:
                if q["status"] != st:
                    def set_status(s=st, qid=quest_id):
                        qx = next((x for x in quests if x["id"] == qid), None)
                        if qx:
                            qx["status"] = s
                            save_quests(quests)
                            refresh_list()
                            show_detail(qid)
                    tk.Button(btn_row, text=lbl, bg=bg, fg=fg,
                              font=("Arial", 9, "bold"), relief="flat",
                              command=set_status).pack(side=tk.LEFT, padx=(0,4))

            tk.Button(btn_row, text="🗑 Supprimer", bg="#3a1a1a", fg="#e57373",
                      font=("Arial", 9, "bold"), relief="flat",
                      command=lambda qid=quest_id: delete_quest(qid)).pack(side=tk.RIGHT)

        def delete_quest(quest_id):
            nonlocal quests
            quests[:] = [q for q in quests if q["id"] != quest_id]
            save_quests(quests)
            selected_id[0] = None
            for w in detail_col.winfo_children():
                if hasattr(w, 'config'):
                    try: w.config(text="")
                    except: pass
            for w in obj_frame.winfo_children():
                w.destroy()
            for w in btn_row.winfo_children():
                w.destroy()
            refresh_list()

        def refresh_list():
            for w in list_inner.winfo_children():
                w.destroy()

            flt = filter_var.get()
            shown = [q for q in quests if flt == "all" or q["status"] == flt]

            if not shown:
                tk.Label(list_inner, text="Aucune quête.", bg="#0d1117",
                         fg="#555555", font=("Consolas", 10)).pack(pady=20)
                return

            # Group by category
            from collections import defaultdict
            by_cat = defaultdict(list)
            for q in shown:
                by_cat[q.get("category","Sans catégorie")].append(q)

            for cat, qlist in by_cat.items():
                cat_color = CATEGORY_COLORS.get(cat, "#888888")
                tk.Label(list_inner, text=f"  {cat}", bg="#161b22", fg=cat_color,
                         font=("Arial", 9, "bold"), anchor="w").pack(fill=tk.X, pady=(6,1))

                for q in qlist:
                    is_sel = q["id"] == selected_id[0]
                    card_bg = "#1a2a3a" if is_sel else "#161b22"
                    card = tk.Frame(list_inner, bg=card_bg, cursor="hand2")
                    card.pack(fill=tk.X, pady=1, padx=2)

                    st_color = STATUS_COLORS.get(q["status"], "#888")
                    status_dot = "●" if q["status"] == "active" else ("✓" if q["status"] == "completed" else "✗")
                    tk.Label(card, text=status_dot, bg=card_bg, fg=st_color,
                             font=("Arial", 11, "bold"), width=2).pack(side=tk.LEFT, padx=(6,2))

                    title_fg = "#e0e0e0" if q["status"] == "active" else "#777777"
                    title_lbl = tk.Label(card, text=q["title"], bg=card_bg, fg=title_fg,
                                         font=("Consolas", 10), anchor="w", wraplength=210, justify=tk.LEFT)
                    title_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=6, padx=4)

                    # Barre de progression objectifs
                    objs = q.get("objectives", [])
                    if objs:
                        done_count = sum(1 for o in objs if o.get("done"))
                        prog = tk.Label(card, text=f"{done_count}/{len(objs)}", bg=card_bg,
                                        fg="#555555", font=("Arial", 8))
                        prog.pack(side=tk.RIGHT, padx=6)

                    def on_click(event, qid=q["id"]):
                        show_detail(qid)
                        refresh_list()
                    card.bind("<Button-1>", on_click)
                    title_lbl.bind("<Button-1>", on_click)

        def open_quest_editor(quest_id):
            """Fenêtre d'édition/création d'une quête."""
            q = next((x for x in quests if x["id"] == quest_id), None) if quest_id else None

            ew = tk.Toplevel(win)
            ew.title("✏️ Modifier la quête" if q else "＋ Nouvelle quête")
            ew.geometry("600x640")
            ew.configure(bg="#0d1117")
            ew.grab_set()

            import uuid

            tk.Label(ew, text="Titre", bg="#0d1117", fg="#888", font=("Arial", 9)).pack(anchor="w", padx=14, pady=(12,0))
            title_var = tk.StringVar(value=q["title"] if q else "")
            tk.Entry(ew, textvariable=title_var, bg="#161b22", fg="white", font=("Consolas", 11),
                     insertbackground="white", relief="flat").pack(fill=tk.X, padx=14, ipady=4)

            tk.Label(ew, text="Catégorie", bg="#0d1117", fg="#888", font=("Arial", 9)).pack(anchor="w", padx=14, pady=(8,0))
            cat_var = tk.StringVar(value=q.get("category","Principale") if q else "Principale")
            cat_menu = tk.OptionMenu(ew, cat_var, "Principale", "Secondaire", "Personnelle")
            cat_menu.config(bg="#161b22", fg="white", font=("Consolas", 10), relief="flat", highlightthickness=0)
            cat_menu["menu"].config(bg="#161b22", fg="white")
            cat_menu.pack(fill=tk.X, padx=14)

            tk.Label(ew, text="Statut", bg="#0d1117", fg="#888", font=("Arial", 9)).pack(anchor="w", padx=14, pady=(8,0))
            status_var = tk.StringVar(value=q.get("status","active") if q else "active")
            status_menu = tk.OptionMenu(ew, status_var, *QUEST_STATUSES)
            status_menu.config(bg="#161b22", fg="white", font=("Consolas", 10), relief="flat", highlightthickness=0)
            status_menu["menu"].config(bg="#161b22", fg="white")
            status_menu.pack(fill=tk.X, padx=14)

            tk.Label(ew, text="Description", bg="#0d1117", fg="#888", font=("Arial", 9)).pack(anchor="w", padx=14, pady=(8,0))
            desc_box = tk.Text(ew, height=4, bg="#161b22", fg="white", font=("Consolas", 10),
                               insertbackground="white", relief="flat", wrap=tk.WORD)
            desc_box.pack(fill=tk.X, padx=14)
            if q: desc_box.insert("1.0", q.get("description",""))

            tk.Label(ew, text="Objectifs (un par ligne, préfixe [x] = complété)", bg="#0d1117",
                     fg="#888", font=("Arial", 9)).pack(anchor="w", padx=14, pady=(8,0))
            obj_box = tk.Text(ew, height=5, bg="#161b22", fg="white", font=("Consolas", 10),
                              insertbackground="white", relief="flat", wrap=tk.WORD)
            obj_box.pack(fill=tk.X, padx=14)
            if q:
                for obj in q.get("objectives", []):
                    prefix = "[x] " if obj.get("done") else "[ ] "
                    obj_box.insert(tk.END, prefix + obj["text"] + "\n")

            tk.Label(ew, text="Notes / Avertissements", bg="#0d1117", fg="#888", font=("Arial", 9)).pack(anchor="w", padx=14, pady=(8,0))
            notes_box = tk.Text(ew, height=3, bg="#161b22", fg="white", font=("Consolas", 10),
                                insertbackground="white", relief="flat", wrap=tk.WORD)
            notes_box.pack(fill=tk.X, padx=14)
            if q: notes_box.insert("1.0", q.get("notes",""))

            def save_quest():
                raw_objs = obj_box.get("1.0", tk.END).strip().splitlines()
                objectives = []
                for line in raw_objs:
                    line = line.strip()
                    if not line:
                        continue
                    done = line.startswith("[x]") or line.startswith("[X]")
                    text = line.lstrip("[xX] ").lstrip("[ ] ").strip()
                    if text:
                        objectives.append({"text": text, "done": done})

                new_q = {
                    "id":          q["id"] if q else str(uuid.uuid4())[:8],
                    "title":       title_var.get().strip() or "Sans titre",
                    "status":      status_var.get(),
                    "category":    cat_var.get(),
                    "description": desc_box.get("1.0", tk.END).strip(),
                    "objectives":  objectives,
                    "notes":       notes_box.get("1.0", tk.END).strip(),
                }
                if q:
                    idx = next((i for i, x in enumerate(quests) if x["id"] == q["id"]), None)
                    if idx is not None:
                        quests[idx] = new_q
                else:
                    quests.append(new_q)

                save_quests(quests)
                refresh_list()
                if selected_id[0] == new_q["id"] or not q:
                    show_detail(new_q["id"])
                ew.destroy()

            tk.Button(ew, text="✅ Sauvegarder", bg="#1a3a5c", fg="#64b5f6",
                      font=("Arial", 11, "bold"), relief="flat",
                      command=save_quest).pack(pady=12)

        # Initial render
        refresh_list()
        if quests:
            show_detail(quests[0]["id"])

    # --- STOP LLMs ---

    def open_dice_roller(self):
        """Fenêtre flottante de lancer de dés rapide.
        Option pour envoyer le résultat dans le chat ou l'afficher uniquement dans la fenêtre.
        """
        if getattr(self, "_dice_roller_win", None):
            try:
                self._dice_roller_win.deiconify()
                self._dice_roller_win.lift()
                return
            except Exception:
                self._dice_roller_win = None

        BG      = "#0f0a1a"
        BG2     = "#1a1030"
        BG3     = "#231540"
        FG      = "#e8d8ff"
        FG_DIM  = "#7a6a9a"
        ACC     = "#9c5cf5"
        ACC2    = "#ce93d8"
        GREEN   = "#81c784"
        RED     = "#e57373"
        GOLD    = "#ffd54f"
        FONT    = ("Consolas", 10)
        FONT_B  = ("Consolas", 10, "bold")
        FONT_XL = ("Consolas", 24, "bold")

        win = tk.Toplevel(self.root)
        win.title("🎲 Lanceur de Dés")
        win.configure(bg=BG)
        win.resizable(False, False)
        self._dice_roller_win = win
        self._track_window("dice_roller", win)

        # ── Variables d'état ──────────────────────────────────────────────────
        self._dice_count    = tk.IntVar(value=1)
        self._dice_bonus    = tk.IntVar(value=0)
        self._dice_selected = tk.StringVar(value="d20")
        self._dice_to_chat  = tk.BooleanVar(value=True)
        self._dice_char     = tk.StringVar(value="MJ")
        self._dice_history  = []   # liste de strings

        # ── Titre ─────────────────────────────────────────────────────────────
        hdr = tk.Frame(win, bg=BG, pady=8)
        hdr.pack(fill=tk.X, padx=12)
        tk.Label(hdr, text="⚀ LANCEUR DE DÉS", bg=BG, fg=ACC2,
                 font=("Arial", 12, "bold")).pack()

        # ── Résultat principal ────────────────────────────────────────────────
        res_frame = tk.Frame(win, bg=BG2, relief="flat", bd=0)
        res_frame.pack(fill=tk.X, padx=12, pady=(0, 8))

        self._dice_result_var = tk.StringVar(value="—")
        self._dice_detail_var = tk.StringVar(value="")

        tk.Label(res_frame, textvariable=self._dice_result_var,
                 bg=BG2, fg=GOLD, font=("Consolas", 36, "bold"),
                 anchor="center").pack(fill=tk.X, pady=(10, 0))
        tk.Label(res_frame, textvariable=self._dice_detail_var,
                 bg=BG2, fg=FG_DIM, font=FONT,
                 anchor="center").pack(fill=tk.X, pady=(0, 10))

        # ── Sélection du dé ───────────────────────────────────────────────────
        dice_types = ["d4", "d6", "d8", "d10", "d12", "d20", "d100"]
        die_icons  = {"d4":"▲","d6":"⬡","d8":"◆","d10":"⬠","d12":"⬟","d20":"⬡","d100":"○"}

        dice_row = tk.Frame(win, bg=BG)
        dice_row.pack(padx=12, pady=(0, 6))

        self._dice_btns = {}
        for dt in dice_types:
            def _pick(t=dt):
                self._dice_selected.set(t)
                for d, b in self._dice_btns.items():
                    b.config(bg=BG3 if d != t else ACC,
                             fg=FG  if d != t else "#fff",
                             relief="flat")
            b = tk.Button(dice_row, text=f"{die_icons.get(dt,'●')}\n{dt}",
                          bg=ACC if dt == "d20" else BG3,
                          fg="#fff" if dt == "d20" else FG,
                          font=("Consolas", 9, "bold"),
                          width=4, height=2, relief="flat",
                          activebackground=ACC, activeforeground="#fff",
                          command=_pick)
            b.pack(side=tk.LEFT, padx=2)
            self._dice_btns[dt] = b

        # ── Compteur & Bonus ──────────────────────────────────────────────────
        opts_row = tk.Frame(win, bg=BG)
        opts_row.pack(padx=12, pady=(0, 8))

        # Nombre de dés
        cnt_frame = tk.Frame(opts_row, bg=BG)
        cnt_frame.pack(side=tk.LEFT, padx=(0, 16))
        tk.Label(cnt_frame, text="Nombre", bg=BG, fg=FG_DIM, font=FONT).pack()
        cnt_ctrl = tk.Frame(cnt_frame, bg=BG)
        cnt_ctrl.pack()
        tk.Button(cnt_ctrl, text="−", bg=BG3, fg=ACC2, font=FONT_B, width=2, relief="flat",
                  command=lambda: self._dice_count.set(max(1, self._dice_count.get()-1))
                  ).pack(side=tk.LEFT)
        tk.Label(cnt_ctrl, textvariable=self._dice_count, bg=BG, fg=FG,
                 font=FONT_B, width=3, anchor="center").pack(side=tk.LEFT)
        tk.Button(cnt_ctrl, text="+", bg=BG3, fg=ACC2, font=FONT_B, width=2, relief="flat",
                  command=lambda: self._dice_count.set(min(20, self._dice_count.get()+1))
                  ).pack(side=tk.LEFT)

        # Bonus
        bon_frame = tk.Frame(opts_row, bg=BG)
        bon_frame.pack(side=tk.LEFT, padx=(0, 16))
        tk.Label(bon_frame, text="Bonus", bg=BG, fg=FG_DIM, font=FONT).pack()
        bon_ctrl = tk.Frame(bon_frame, bg=BG)
        bon_ctrl.pack()
        tk.Button(bon_ctrl, text="−", bg=BG3, fg=RED, font=FONT_B, width=2, relief="flat",
                  command=lambda: self._dice_bonus.set(self._dice_bonus.get()-1)
                  ).pack(side=tk.LEFT)
        tk.Label(bon_ctrl, textvariable=self._dice_bonus, bg=BG, fg=FG,
                 font=FONT_B, width=4, anchor="center").pack(side=tk.LEFT)
        tk.Button(bon_ctrl, text="+", bg=BG3, fg=GREEN, font=FONT_B, width=2, relief="flat",
                  command=lambda: self._dice_bonus.set(self._dice_bonus.get()+1)
                  ).pack(side=tk.LEFT)

        # Personnage
        char_frame = tk.Frame(opts_row, bg=BG)
        char_frame.pack(side=tk.LEFT)
        tk.Label(char_frame, text="Personnage", bg=BG, fg=FG_DIM, font=FONT).pack()
        tk.Entry(char_frame, textvariable=self._dice_char,
                 bg=BG3, fg=FG, font=FONT, width=9,
                 insertbackground=ACC2, relief="flat").pack()

        # ── Bouton LANCER ─────────────────────────────────────────────────────
        def _do_roll():
            import random
            dt    = self._dice_selected.get()          # ex "d20"
            n     = self._dice_count.get()
            bonus = self._dice_bonus.get()
            char  = self._dice_char.get().strip() or "MJ"
            sides = int(dt[1:])
            rolls = [random.randint(1, sides) for _ in range(n)]
            total = sum(rolls) + bonus

            # Affichage dans la fenêtre
            self._dice_result_var.set(str(total))
            dice_str = f"{n}{dt}"
            bonus_str = f" + {bonus}" if bonus > 0 else (f" − {abs(bonus)}" if bonus < 0 else "")
            detail = f"{char} · {dice_str}{bonus_str}   dés: {rolls}"
            self._dice_detail_var.set(detail)

            # Couleur du résultat (critique / échec critique)
            if dt == "d20" and n == 1:
                if rolls[0] == 20:
                    self._dice_result_var.set("🌟 " + str(total))
                elif rolls[0] == 1:
                    self._dice_result_var.set("💀 " + str(total))

            # Historique
            self._dice_history.insert(0, detail)
            del self._dice_history[10:]
            _refresh_history()

            # Envoi dans le chat si activé
            if self._dice_to_chat.get():
                result_str = roll_dice(
                    character_name=char,
                    dice_type=f"{n}{dt}",
                    bonus=bonus,
                )
                self.msg_queue.put({
                    "sender": f"🎲 {char}",
                    "text": result_str,
                    "color": GOLD,
                })

        roll_btn = tk.Button(win, text="🎲  LANCER", bg=ACC, fg="#fff",
                             font=("Arial", 13, "bold"), relief="flat",
                             activebackground="#b07dff", activeforeground="#fff",
                             pady=8, command=_do_roll)
        roll_btn.pack(fill=tk.X, padx=12, pady=(0, 8))
        # Raccourci clavier
        win.bind("<Return>", lambda e: _do_roll())
        win.bind("<space>",  lambda e: _do_roll())

        # ── Option : envoyer dans le chat ─────────────────────────────────────
        chat_toggle = tk.Checkbutton(
            win, text="📢 Afficher dans le chat",
            variable=self._dice_to_chat,
            bg=BG, fg=FG_DIM, selectcolor=BG3,
            activebackground=BG, activeforeground=FG,
            font=FONT, anchor="w", relief="flat",
        )
        chat_toggle.pack(fill=tk.X, padx=14, pady=(0, 6))

        # ── Historique ────────────────────────────────────────────────────────
        sep = tk.Frame(win, bg="#2a1a40", height=1)
        sep.pack(fill=tk.X, padx=12, pady=(2, 4))

        tk.Label(win, text="HISTORIQUE", bg=BG, fg=FG_DIM,
                 font=("Consolas", 8, "bold")).pack(anchor="w", padx=14)

        self._dice_hist_frame = tk.Frame(win, bg=BG)
        self._dice_hist_frame.pack(fill=tk.X, padx=12, pady=(2, 10))

        def _refresh_history():
            for w in self._dice_hist_frame.winfo_children():
                w.destroy()
            for entry in self._dice_history:
                tk.Label(self._dice_hist_frame, text=entry, bg=BG, fg=FG_DIM,
                         font=("Consolas", 8), anchor="w",
                         wraplength=280, justify=tk.LEFT).pack(fill=tk.X)

    def open_dice_roller(self):
        """Fenêtre flottante de lancer de dés rapide.
        Toggle pour envoyer dans le chat ou afficher uniquement dans la fenêtre.
        """
        if getattr(self, "_dice_roller_win", None):
            try:
                self._dice_roller_win.deiconify()
                self._dice_roller_win.lift()
                return
            except Exception:
                self._dice_roller_win = None

        BG     = "#0f0a1a"
        BG2    = "#1a1030"
        BG3    = "#231540"
        FG     = "#e8d8ff"
        FG_DIM = "#7a6a9a"
        ACC    = "#9c5cf5"
        ACC2   = "#ce93d8"
        GREEN  = "#81c784"
        RED    = "#e57373"
        GOLD   = "#ffd54f"
        FONT   = ("Consolas", 10)
        FONT_B = ("Consolas", 10, "bold")

        win = tk.Toplevel(self.root)
        win.title("🎲 Lanceur de Dés")
        win.configure(bg=BG)
        win.resizable(False, False)
        self._dice_roller_win = win
        self._track_window("dice_roller", win)

        # Variables d'état
        dice_count    = tk.IntVar(value=1)
        dice_bonus    = tk.IntVar(value=0)
        dice_selected = tk.StringVar(value="d20")
        dice_to_chat  = tk.BooleanVar(value=True)
        dice_char     = tk.StringVar(value="MJ")
        result_var    = tk.StringVar(value="—")
        detail_var    = tk.StringVar(value="Choisir un dé et lancer")
        history       = []

        # ── Titre ─────────────────────────────────────────────────────────
        tk.Label(win, text="⚀  LANCEUR DE DÉS", bg=BG, fg=ACC2,
                 font=("Arial", 12, "bold")).pack(pady=(10, 4))

        # ── Résultat ──────────────────────────────────────────────────────
        res_frame = tk.Frame(win, bg=BG2, pady=6)
        res_frame.pack(fill=tk.X, padx=12, pady=(0, 8))
        tk.Label(res_frame, textvariable=result_var,
                 bg=BG2, fg=GOLD, font=("Consolas", 40, "bold"),
                 width=8, anchor="center").pack()
        tk.Label(res_frame, textvariable=detail_var,
                 bg=BG2, fg=FG_DIM, font=("Consolas", 9),
                 anchor="center", wraplength=280).pack()

        # ── Boutons de dés ────────────────────────────────────────────────
        dice_types = ["d4", "d6", "d8", "d10", "d12", "d20", "d100"]
        die_icons  = {"d4":"▲","d6":"■","d8":"◆","d10":"⬟","d12":"⬠","d20":"★","d100":"●"}
        die_btns   = {}

        dice_row = tk.Frame(win, bg=BG)
        dice_row.pack(padx=12, pady=(0, 8))

        def _select_die(t):
            dice_selected.set(t)
            for d, b in die_btns.items():
                b.config(bg=ACC if d == t else BG3,
                         fg="#fff" if d == t else FG)

        for dt in dice_types:
            b = tk.Button(dice_row, text=f"{die_icons.get(dt,'●')}\n{dt}",
                          bg=ACC if dt == "d20" else BG3,
                          fg="#fff" if dt == "d20" else FG,
                          font=("Consolas", 9, "bold"),
                          width=4, height=2, relief="flat",
                          activebackground=ACC, activeforeground="#fff",
                          command=lambda t=dt: _select_die(t))
            b.pack(side=tk.LEFT, padx=2)
            die_btns[dt] = b

        # ── Compteur · Bonus · Personnage ─────────────────────────────────
        opts = tk.Frame(win, bg=BG)
        opts.pack(padx=12, pady=(0, 8))

        # Nombre de dés
        cnt_f = tk.Frame(opts, bg=BG); cnt_f.pack(side=tk.LEFT, padx=(0,14))
        tk.Label(cnt_f, text="Nombre", bg=BG, fg=FG_DIM, font=FONT).pack()
        cnt_row = tk.Frame(cnt_f, bg=BG); cnt_row.pack()
        tk.Button(cnt_row, text="−", bg=BG3, fg=ACC2, font=FONT_B, width=2, relief="flat",
                  command=lambda: dice_count.set(max(1, dice_count.get()-1))).pack(side=tk.LEFT)
        tk.Label(cnt_row, textvariable=dice_count, bg=BG, fg=FG,
                 font=FONT_B, width=3, anchor="center").pack(side=tk.LEFT)
        tk.Button(cnt_row, text="+", bg=BG3, fg=ACC2, font=FONT_B, width=2, relief="flat",
                  command=lambda: dice_count.set(min(20, dice_count.get()+1))).pack(side=tk.LEFT)

        # Bonus
        bon_f = tk.Frame(opts, bg=BG); bon_f.pack(side=tk.LEFT, padx=(0,14))
        tk.Label(bon_f, text="Bonus", bg=BG, fg=FG_DIM, font=FONT).pack()
        bon_row = tk.Frame(bon_f, bg=BG); bon_row.pack()
        tk.Button(bon_row, text="−", bg=BG3, fg=RED, font=FONT_B, width=2, relief="flat",
                  command=lambda: dice_bonus.set(dice_bonus.get()-1)).pack(side=tk.LEFT)
        tk.Label(bon_row, textvariable=dice_bonus, bg=BG, fg=FG,
                 font=FONT_B, width=4, anchor="center").pack(side=tk.LEFT)
        tk.Button(bon_row, text="+", bg=BG3, fg=GREEN, font=FONT_B, width=2, relief="flat",
                  command=lambda: dice_bonus.set(dice_bonus.get()+1)).pack(side=tk.LEFT)

        # Personnage
        char_f = tk.Frame(opts, bg=BG); char_f.pack(side=tk.LEFT)
        tk.Label(char_f, text="Personnage", bg=BG, fg=FG_DIM, font=FONT).pack()
        tk.Entry(char_f, textvariable=dice_char,
                 bg=BG3, fg=FG, font=FONT, width=9,
                 insertbackground=ACC2, relief="flat").pack()

        # ── Bouton LANCER ─────────────────────────────────────────────────
        def _do_roll():
            import random
            dt    = dice_selected.get()
            n     = dice_count.get()
            bonus = dice_bonus.get()
            char  = dice_char.get().strip() or "MJ"
            sides = int(dt[1:])
            rolls = [random.randint(1, sides) for _ in range(n)]
            total = sum(rolls) + bonus

            # Détection critique d20
            label = str(total)
            if dt == "d20" and n == 1:
                if rolls[0] == 20:  label = "🌟 CRITIQUE!"
                elif rolls[0] == 1: label = "💀 ÉCHEC CRITIQUE"
            result_var.set(label)

            bonus_str = (f" +{bonus}" if bonus > 0 else f" {bonus}" if bonus < 0 else "")
            detail_var.set(f"{char} · {n}{dt}{bonus_str}   dés: {rolls}")

            # Historique
            history.insert(0, f"{char} · {n}{dt}{bonus_str} = {total}   {rolls}")
            del history[10:]
            _refresh_hist()

            # Chat si activé
            if dice_to_chat.get():
                result_str = roll_dice(
                    character_name=char,
                    dice_type=f"{n}{dt}",
                    bonus=bonus,
                )
                self.msg_queue.put({
                    "sender": f"🎲 {char}",
                    "text": result_str,
                    "color": GOLD,
                })

        tk.Button(win, text="🎲   LANCER", bg=ACC, fg="#fff",
                  font=("Arial", 13, "bold"), relief="flat", pady=8,
                  activebackground="#b07dff", activeforeground="#fff",
                  command=_do_roll).pack(fill=tk.X, padx=12, pady=(0, 6))

        win.bind("<Return>", lambda e: _do_roll())
        win.bind("<space>",  lambda e: _do_roll())

        # ── Toggle chat ───────────────────────────────────────────────────
        toggle_frame = tk.Frame(win, bg=BG)
        toggle_frame.pack(fill=tk.X, padx=12, pady=(0, 6))

        def _toggle_chat():
            if dice_to_chat.get():
                chat_btn.config(text="📢 Envoyer dans le chat  ✓",
                                bg="#1a2a1a", fg=GREEN)
            else:
                chat_btn.config(text="🔇 Fenêtre seulement",
                                bg=BG3, fg=FG_DIM)

        chat_btn = tk.Button(
            toggle_frame,
            text="📢 Envoyer dans le chat  ✓",
            bg="#1a2a1a", fg=GREEN,
            font=FONT, relief="flat", anchor="w", padx=8, pady=4,
            activebackground=BG3, activeforeground=FG,
            command=lambda: [dice_to_chat.set(not dice_to_chat.get()), _toggle_chat()]
        )
        chat_btn.pack(fill=tk.X)

        # ── Historique ────────────────────────────────────────────────────
        tk.Frame(win, bg="#2a1a40", height=1).pack(fill=tk.X, padx=12, pady=(6, 4))
        tk.Label(win, text="HISTORIQUE", bg=BG, fg=FG_DIM,
                 font=("Consolas", 8, "bold")).pack(anchor="w", padx=14)

        hist_frame = tk.Frame(win, bg=BG)
        hist_frame.pack(fill=tk.X, padx=12, pady=(2, 12))

        def _refresh_hist():
            for w in hist_frame.winfo_children():
                w.destroy()
            for entry in history:
                tk.Label(hist_frame, text=entry, bg=BG, fg=FG_DIM,
                         font=("Consolas", 8), anchor="w",
                         wraplength=290, justify=tk.LEFT).pack(fill=tk.X, pady=1)


    def open_skill_check_dialog(self):
        """Fenêtre de demande de jet de compétence : choisir perso + skill."""
        if not self._agents:
            self.msg_queue.put({"sender": "Système", "text": "⚠️ Les agents ne sont pas encore initialisés. Lancez la partie d'abord.", "color": "#FF9800"})
            return

        win = tk.Toplevel(self.root)
        win.title("🎲 Demande de Jet de Compétence")
        win.geometry("560x660")
        win.configure(bg="#0d1117")
        win.grab_set()
        win.resizable(False, False)
        self._track_window("modal_skill_check", win)

        # ── En-tête ──────────────────────────────────────────────────────────
        hdr = tk.Frame(win, bg="#0d2010")
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="🎲  Demande de Jet de Compétence", bg="#0d2010", fg="#81c784",
                 font=("Arial", 13, "bold")).pack(side=tk.LEFT, padx=14, pady=10)
        tk.Label(hdr, text="Prompt direct — agents tiers non impliqués",
                 bg="#0d2010", fg="#555555", font=("Arial", 8)).pack(side=tk.RIGHT, padx=14)

        # ── Sélecteur de personnage ───────────────────────────────────────────
        char_frame = tk.Frame(win, bg="#0d1117")
        char_frame.pack(fill=tk.X, padx=16, pady=(12, 4))
        tk.Label(char_frame, text="Personnage :", bg="#0d1117", fg="#cccccc",
                 font=("Arial", 10, "bold")).pack(side=tk.LEFT)

        char_names = list(self._agents.keys())
        selected_char = tk.StringVar(value=char_names[0])

        # Couleurs perso
        CHAR_COLORS = {"Kaelen": "#e57373", "Elara": "#64b5f6", "Thorne": "#ce93d8", "Lyra": "#81c784"}

        btn_chars = {}
        for name in char_names:
            c = CHAR_COLORS.get(name, "#aaaaaa")
            b = tk.Button(char_frame, text=name, bg="#1e2a1e", fg=c,
                          font=("Arial", 10, "bold"), relief="flat", padx=10, pady=4,
                          command=lambda n=name: select_char(n))
            b.pack(side=tk.LEFT, padx=4)
            btn_chars[name] = b

        def select_char(name):
            selected_char.set(name)
            for n, b in btn_chars.items():
                c = CHAR_COLORS.get(n, "#aaaaaa")
                if n == name:
                    b.config(bg=c, fg="#0d1117")
                else:
                    b.config(bg="#1e2a1e", fg=c)

        select_char(char_names[0])  # sélection initiale visuelle

        # ── Champ difficulté (DC) ────────────────────────────────────────────
        dc_frame = tk.Frame(win, bg="#0d1117")
        dc_frame.pack(fill=tk.X, padx=16, pady=(4, 2))
        tk.Label(dc_frame, text="Difficulté (DC) :", bg="#0d1117", fg="#cccccc",
                 font=("Arial", 10, "bold")).pack(side=tk.LEFT)

        dc_var = tk.StringVar(value="")
        dc_entry = tk.Entry(dc_frame, textvariable=dc_var, width=6, bg="#161b22", fg="white",
                            font=("Consolas", 12), insertbackground="white", relief="flat",
                            justify="center")
        dc_entry.pack(side=tk.LEFT, padx=8, ipady=4)
        tk.Label(dc_frame, text="(laisser vide = secret)",
                 bg="#0d1117", fg="#555555", font=("Arial", 8, "italic")).pack(side=tk.LEFT)

        # ── Champ raison / contexte ───────────────────────────────────────────
        reason_frame = tk.Frame(win, bg="#0d1117")
        reason_frame.pack(fill=tk.X, padx=16, pady=(4, 2))
        tk.Label(reason_frame, text="Raison / Contexte :", bg="#0d1117", fg="#cccccc",
                 font=("Arial", 10, "bold")).pack(anchor="w")
        reason_var = tk.StringVar(value="")
        reason_entry = tk.Entry(reason_frame, textvariable=reason_var, bg="#161b22", fg="#dddddd",
                                font=("Consolas", 10), insertbackground="white", relief="flat")
        reason_entry.pack(fill=tk.X, ipady=4, pady=(2, 0))
        tk.Label(reason_frame, text="ex: Tu remarques une ombre derrière la porte",
                 bg="#0d1117", fg="#333355", font=("Arial", 7, "italic")).pack(anchor="w")

        # ── Grille de compétences ─────────────────────────────────────────────
        grid_frame = tk.Frame(win, bg="#0d1117")
        grid_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(8, 4))

        selected_skill: dict | None = [None]   # [skill_name, ability_name]
        skill_buttons: list = []

        def select_skill(skill, ability, btn):
            selected_skill[0] = (skill, ability)
            for b, _, _ in skill_buttons:
                b.config(relief="flat", bd=0)
            btn.config(relief="solid", bd=2)
            update_confirm()

        for col_idx, (ability, skills) in enumerate(DND_SKILLS.items()):
            if not skills:
                continue
            col = tk.Frame(grid_frame, bg="#0d1117")
            col.grid(row=0, column=col_idx, padx=5, pady=4, sticky="n")

            ability_color = ABILITY_COLORS[ability]
            tk.Label(col, text=ability.upper(), bg="#0d1117", fg=ability_color,
                     font=("Arial", 8, "bold")).pack(anchor="w", pady=(0, 3))

            for skill, ab_code in skills:
                btn = tk.Button(
                    col, text=f"{skill}", bg="#161b22", fg="#dddddd",
                    font=("Consolas", 9), relief="flat", anchor="w", padx=6, pady=3,
                    activebackground=ability_color, activeforeground="#0d1117",
                    cursor="hand2"
                )
                # Sous-label de l'ability code
                def make_cmd(s=skill, a=ability, b_ref=btn):
                    return lambda: select_skill(s, a, b_ref)
                btn.config(command=make_cmd())
                btn.pack(fill=tk.X, pady=1)
                skill_buttons.append((btn, skill, ability))

        # ── Zone de confirmation ──────────────────────────────────────────────
        confirm_frame = tk.Frame(win, bg="#0d1117")
        confirm_frame.pack(fill=tk.X, padx=16, pady=(4, 12))

        summary_label = tk.Label(confirm_frame, text="Sélectionnez un personnage et une compétence",
                                  bg="#0d1117", fg="#555555", font=("Arial", 9, "italic"))
        summary_label.pack(pady=(0, 6))

        btn_confirm = tk.Button(confirm_frame, text="▶ Envoyer le Jet",
                                bg="#1a3a2a", fg="#555555",
                                font=("Arial", 11, "bold"), state=tk.DISABLED,
                                command=lambda: confirm_and_send())
        btn_confirm.pack(fill=tk.X, ipady=6)

        def update_confirm(*_):
            char = selected_char.get()
            sk = selected_skill[0]
            if char and sk:
                skill, ability = sk
                dc_txt = f"  DC {dc_var.get()}" if dc_var.get().strip() else ""
                reason_txt = f"  — {reason_var.get().strip()[:40]}" if reason_var.get().strip() else ""
                summary_label.config(
                    text=f"→ [{char}] : jet de {skill} ({ability}){dc_txt}{reason_txt}",
                    fg="#81c784"
                )
                btn_confirm.config(state=tk.NORMAL, fg="white", bg="#1a5c2a")
            else:
                summary_label.config(text="Sélectionnez un personnage et une compétence", fg="#555555")
                btn_confirm.config(state=tk.DISABLED, fg="#555555", bg="#1a3a2a")

        # Mise à jour quand DC ou raison changent
        dc_var.trace_add("write", update_confirm)
        reason_var.trace_add("write", update_confirm)

        def confirm_and_send():
            char = selected_char.get()
            sk = selected_skill[0]
            if not char or not sk:
                return
            skill, ability = sk
            dc_raw = dc_var.get().strip()
            dc_val = int(dc_raw) if dc_raw.isdigit() else None
            reason = reason_var.get().strip() or None
            win.destroy()
            threading.Thread(
                target=self._execute_skill_check,
                args=(char, skill, ability, dc_val, reason),
                daemon=True
            ).start()
    # ─── CARTE DE COMBAT ──────────────────────────────────────────────────────

    def open_combat_map(self):
        """Ouvre (ou ramène au premier plan) la fenêtre de carte de combat."""
        if getattr(self, "_combat_map_win", None):
            try:
                self._combat_map_win.win.deiconify()
                self._combat_map_win.win.lift()
                return
            except Exception:
                self._combat_map_win = None

        self._combat_map_win = _open_combat_map(
            parent    = self.root,
            win_state = self._win_state,
            save_fn   = lambda: _save_window_state(self._win_state),
            track_fn  = self._track_window,
            msg_queue = self.msg_queue,
            inject_fn = lambda text: (
                setattr(self, "user_input", text),
                self.msg_queue.put({"sender": "Carte de Combat", "text": text, "color": "#64b5f6"}),
                self.input_event.set(),
            ),
        )