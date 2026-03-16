"""
character_mixin.py — CharacterMixin : fiche personnage détaillée, voix, input.

Contient :
  - open_char_popout  (onglets Stats + Sorts, édition inline, Short/Long Rest)
  - send_voice
  - wait_for_input

Sorts liés aux sources (v2) :
  - Chaque sort peut avoir un champ "source_key" (nom.lower()) liant au cache
    _SPELL_DATA de spell_data.py.
  - Si source_key est présent, un clic sur le nom ouvre SpellSheetWindow (fiche
    complète avec description riche, cast_time, range, components, durée...).
  - L'éditeur de sort distingue le mode "lié à une source" du mode "manuel" :
    • Lié : champs nom/niveau/école en lecture seule, bouton "Délier" pour passer
      en mode libre, bouton "Resync" pour récupérer les dernières données.
    • Manuel : formulaire libre comme avant.
  - Quand on importe via SpellPickerDialog, source_key est sauvegardé + toutes
    les données riches (cast_time, range, components, duration, source).
  - Badge de source [PHB] / [XGE] / etc. affiché en bout de ligne.
"""

import threading
import tkinter as tk

from state_manager import load_state, save_state
from window_state import _get_win_geometry, _save_window_state
from voice_interface import record_audio_and_transcribe
from character_faces import CharacterFaceWindow, CHARACTER_DATA


class CharacterMixin:
    """Mixin pour DnDApp — fiches personnages et entrée vocale."""

    def open_char_popout(self, char_name: str):
        """Ouvre la fiche détaillée d'un personnage dans une fenêtre flottante.
        Deux onglets : 📊 Stats (tout éditable inline) | ✨ Sorts (liste CRUD)."""
        attr = f"_popout_{char_name}"
        existing = getattr(self, attr, None)
        if existing:
            try:
                existing.deiconify()
                existing.lift()
                return
            except Exception:
                pass

        state  = load_state()
        data   = state.get("characters", {}).get(char_name, {})
        color  = self.CHAR_COLORS.get(char_name, "#aaaaaa")

        win = tk.Toplevel(self.root)
        win.title(f"📋 {char_name}")
        win.configure(bg="#1e1e2e")
        win.resizable(True, True)
        win.minsize(300, 520)

        _key        = f"char_{char_name}"
        _saved_geom = self._win_state.get(_key)
        if _saved_geom:
            win.geometry(f"{_saved_geom['w']}x{_saved_geom['h']}+{_saved_geom['x']}+{_saved_geom['y']}")
        else:
            win.geometry("300x680")

        def _on_close():
            g = _get_win_geometry(win)
            if g:
                self._win_state[_key] = g
            self._win_state.pop(f"_open_{_key}", None)
            _save_window_state(self._win_state)
            face = self.face_windows.get(char_name)
            if face:
                face._alive = False
                self.face_windows.pop(char_name, None)
            setattr(self, attr, None)
            win.destroy()

        self._win_state[f"_open_{_key}"] = True
        _save_window_state(self._win_state)
        win.protocol("WM_DELETE_WINDOW", _on_close)
        setattr(self, attr, win)

        # ── Données statiques par personnage ──────────────────────────────────
        _CHAR_STATS = {
            # Emplacements de sort officiels D&D 5e niveau 15
            # Paladin (demi-lanceur L15)   : 4/3/3/2/1
            # Clerc / Mage (plein L15)     : 4/3/3/3/2/1/1/1/1  (niv 9 inclus)
            "Kaelen": {"hit_die": 10, "level": 15, "con_mod": 3, "ac": 20,
                       "max_slots": {"1":4,"2":3,"3":3,"4":2,"5":1}},
            "Elara":  {"hit_die": 6,  "level": 15, "con_mod": 1, "ac": 14,
                       "max_slots": {"1":4,"2":3,"3":3,"4":3,"5":2,"6":1,"7":1,"8":1,"9":1}},
            "Thorne": {"hit_die": 10, "level": 15, "con_mod": 3, "ac": 18,
                       "max_slots": {}},
            "Lyra":   {"hit_die": 8,  "level": 15, "con_mod": 2, "ac": 17,
                       "max_slots": {"1":4,"2":3,"3":3,"4":3,"5":2,"6":1,"7":1,"8":1,"9":1}},
        }
        cstats    = _CHAR_STATS.get(char_name, {"hit_die":8,"level":1,"con_mod":0,"ac":10,"max_slots":{}})
        hit_die   = data.get("hit_die",  cstats["hit_die"])
        level     = data.get("level",    cstats["level"])
        con_mod   = data.get("con_mod",  cstats["con_mod"])
        ac        = data.get("ac",       cstats["ac"])
        max_slots = cstats["max_slots"]

        # ── Avatar animé ──────────────────────────────────────────────────────
        char_bg    = CHARACTER_DATA.get(char_name, {}).get("bg", "#1e1e2e")
        face_frame = tk.Frame(win, bg=char_bg)
        face_frame.pack(fill=tk.X)
        try:
            face = CharacterFaceWindow(self.root, char_name, parent_frame=face_frame)
            self.face_windows[char_name] = face
            # Synchronise la référence dans agent_logger pour la bulle de pensée
            # (couvre log_llm_start/end appelés depuis llm_control_mixin et image_broadcast)
            try:
                from agent_logger import set_face_windows_ref
                set_face_windows_ref(self.face_windows)
            except Exception:
                pass
        except Exception as e:
            print(f"[popout] Erreur avatar {char_name}: {e}")

        # ── En-tête coloré ────────────────────────────────────────────────────
        hdr = tk.Frame(win, bg=color, pady=4)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text=char_name, bg=color, fg="#0d0d0d",
                 font=("Arial", 12, "bold")).pack(side=tk.LEFT, padx=14)
        llm_short = data.get("llm", "?").replace("gemini-", "G:").replace("groq/", "Q:")
        tk.Label(hdr, text=llm_short, bg=color, fg="#333333",
                 font=("Consolas", 8)).pack(side=tk.RIGHT, padx=10)

        # ── Barre d'onglets ───────────────────────────────────────────────────
        tabs_bar = tk.Frame(win, bg="#12121e")
        tabs_bar.pack(fill=tk.X)

        stats_frame  = tk.Frame(win, bg="#1e1e2e")
        spells_frame = tk.Frame(win, bg="#1e1e2e")

        def _show_tab(name):
            if name == "stats":
                spells_frame.pack_forget()
                stats_frame.pack(fill=tk.BOTH, expand=True)
                btn_stats.config(bg=color, fg="#0d0d0d")
                btn_spells.config(bg="#12121e", fg="#555566")
            else:
                stats_frame.pack_forget()
                spells_frame.pack(fill=tk.BOTH, expand=True)
                btn_stats.config(bg="#12121e", fg="#555566")
                btn_spells.config(bg=color, fg="#0d0d0d")

        btn_stats  = tk.Button(tabs_bar, text="📊 Stats",  font=("Arial", 9, "bold"),
                               relief="flat", padx=10, pady=5, cursor="hand2",
                               command=lambda: _show_tab("stats"))
        btn_spells = tk.Button(tabs_bar, text="✨ Sorts",  font=("Arial", 9, "bold"),
                               relief="flat", padx=10, pady=5, cursor="hand2",
                               command=lambda: _show_tab("spells"))
        btn_stats.pack(side=tk.LEFT, fill=tk.X, expand=True)
        btn_spells.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # ════════════════════════════════════════════════════════════════════
        # ── ONGLET STATS ──────────────────────────────────────────────────
        # ════════════════════════════════════════════════════════════════════
        body = tk.Frame(stats_frame, bg="#1e1e2e")
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)

        def _make_editable(row_frame, get_fn, set_fn,
                           min_v=0, max_v=999, fg_fn=None, font=("Consolas", 10, "bold")):
            """Label cliquable → spinbox inline. Retourne (lbl, spx)."""
            c = fg_fn(get_fn()) if fg_fn else color
            lbl = tk.Label(row_frame, text=str(get_fn()), bg="#1e1e2e",
                           fg=c, font=font, cursor="hand2")
            lbl.pack(side=tk.RIGHT)
            spx = tk.Spinbox(row_frame, from_=min_v, to=max_v, width=6,
                             bg="#252535", fg=c, font=font,
                             buttonbackground="#252535", relief="flat",
                             highlightthickness=1, highlightcolor=color)

            def _start(e=None):
                lbl.pack_forget()
                spx.config(fg=fg_fn(get_fn()) if fg_fn else color)
                spx.delete(0, tk.END); spx.insert(0, str(get_fn()))
                spx.pack(side=tk.RIGHT); spx.focus_set(); spx.select_range(0, tk.END)

            def _end(e=None):
                try:
                    v = max(min_v, min(max_v, int(spx.get())))
                    set_fn(v)
                except ValueError:
                    pass
                spx.pack_forget()
                v2 = get_fn()
                lbl.config(text=str(v2), fg=fg_fn(v2) if fg_fn else color)
                lbl.pack(side=tk.RIGHT)

            lbl.bind("<Button-1>", _start)
            spx.bind("<Return>",   _end)
            spx.bind("<FocusOut>", _end)
            spx.bind("<Escape>",   lambda e: (_end(),))
            return lbl, spx

        # ── Points de vie ─────────────────────────────────────────────────
        hp_row = tk.Frame(body, bg="#1e1e2e")
        hp_row.pack(fill=tk.X, pady=(0, 2))
        tk.Label(hp_row, text="❤️ PV", bg="#1e1e2e", fg="#aaaaaa",
                 font=("Arial", 9)).pack(side=tk.LEFT)

        def get_hp():     return load_state().get("characters",{}).get(char_name,{}).get("hp", 0)
        def get_max_hp(): return load_state().get("characters",{}).get(char_name,{}).get("max_hp", 0)
        def set_hp(v):
            s = load_state(); s["characters"][char_name]["hp"] = max(0, min(v, get_max_hp())); save_state(s)
        def set_max_hp(v):
            s = load_state(); s["characters"][char_name]["max_hp"] = max(1, v); save_state(s)

        slash_lbl = tk.Label(hp_row, text=" / ", bg="#1e1e2e", fg="#444455",
                              font=("Consolas", 10))
        slash_lbl.pack(side=tk.RIGHT)
        maxhp_lbl, maxhp_spx = _make_editable(
            hp_row, get_max_hp, set_max_hp, min_v=1, max_v=999,
            font=("Consolas", 9)
        )
        maxhp_lbl.config(fg="#888888"); maxhp_spx.config(fg="#888888")

        hp_lbl, hp_spx = _make_editable(
            hp_row, get_hp, set_hp, min_v=0, max_v=999,
            fg_fn=lambda v: self._hp_color(v / max(get_max_hp(), 1))
        )

        bar_bg   = tk.Frame(body, bg="#3a3a3a", height=8)
        bar_bg.pack(fill=tk.X, pady=(0, 6))
        pct_init = max(0, min(1, get_hp() / max(get_max_hp(), 1)))
        bar_fill = tk.Frame(bar_bg, bg=self._hp_color(pct_init), height=8)
        bar_fill.place(relx=0, rely=0, relwidth=pct_init, relheight=1)

        # ── Classe d'Armure ───────────────────────────────────────────────
        ac_row = tk.Frame(body, bg="#1e1e2e")
        ac_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(ac_row, text="🛡 CA", bg="#1e1e2e", fg="#aaaaaa",
                 font=("Arial", 9)).pack(side=tk.LEFT)

        def get_ac():
            return load_state().get("characters", {}).get(char_name, {}).get("ac", ac)
        def set_ac(v):
            s = load_state(); s["characters"][char_name]["ac"] = max(0, min(v, 30)); save_state(s)

        ac_lbl, ac_spx = _make_editable(
            ac_row, get_ac, set_ac, min_v=0, max_v=30,
            font=("Consolas", 11, "bold")
        )
        ac_lbl.config(fg=color)
        ac_spx.config(fg=color)

        # ── Hit Dice ──────────────────────────────────────────────────────
        hd_row = tk.Frame(body, bg="#1e1e2e")
        hd_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(hd_row, text=f"🎲 Hit Dice (d{hit_die})", bg="#1e1e2e", fg="#aaaaaa",
                 font=("Arial", 9)).pack(side=tk.LEFT)
        tk.Label(hd_row, text=f"/{level}", bg="#1e1e2e", fg="#444455",
                 font=("Consolas", 9)).pack(side=tk.RIGHT)

        def get_hd_avail():
            used = load_state().get("characters",{}).get(char_name,{}).get("hit_dice_used", 0)
            return max(0, level - used)
        def set_hd_avail(v):
            used = max(0, level - v)
            s = load_state(); s["characters"][char_name]["hit_dice_used"] = used; save_state(s)

        hd_lbl, hd_spx = _make_editable(
            hd_row, get_hd_avail, set_hd_avail, min_v=0, max_v=level,
            font=("Consolas", 9, "bold")
        )

        # ── Emplacements de sort ──────────────────────────────────────────
        slots        = data.get("spell_slots", {})
        slot_widgets = {}  # lvl → (lbl, pip_frame, spx, maxi)

        if slots or max_slots:
            tk.Label(body, text="✨ Emplacements de Sort", bg="#1e1e2e", fg="#aaaaaa",
                     font=("Arial", 9)).pack(anchor="w", pady=(0, 3))
            slots_frame = tk.Frame(body, bg="#1e1e2e")
            slots_frame.pack(fill=tk.X)
            all_levels = sorted(set(list(slots.keys()) + list(max_slots.keys())), key=int)

            for lvl in all_levels:
                cur  = slots.get(lvl, 0)
                maxi = max_slots.get(lvl, cur)

                def _get_slot(l=lvl):
                    return load_state().get("characters",{}).get(char_name,{}).get("spell_slots",{}).get(l, 0)
                def _set_slot(v, l=lvl, mx=maxi):
                    s = load_state()
                    s["characters"][char_name].setdefault("spell_slots",{})[l] = max(0, min(v, mx))
                    save_state(s)

                row = tk.Frame(slots_frame, bg="#1e1e2e")
                row.pack(fill=tk.X, pady=1)
                tk.Label(row, text=f"Niv {lvl}", bg="#1e1e2e", fg="#888888",
                         font=("Consolas", 9), width=5, anchor="w").pack(side=tk.LEFT)

                pip_frame = tk.Frame(row, bg="#1e1e2e")
                pip_frame.pack(side=tk.LEFT, padx=4)
                for i in range(maxi):
                    pip_bg = color if i < cur else "#333344"
                    tk.Frame(pip_frame, bg=pip_bg, width=10, height=10).pack(
                        side=tk.LEFT, padx=1)

                sl_lbl = tk.Label(row, text=f"{cur}/{maxi}", bg="#1e1e2e", fg=color,
                                  font=("Consolas", 9, "bold"), cursor="hand2")
                sl_lbl.pack(side=tk.RIGHT, padx=4)

                sl_spx = tk.Spinbox(row, from_=0, to=maxi, width=4, bg="#252535", fg=color,
                                    font=("Consolas", 9, "bold"), buttonbackground="#252535",
                                    relief="flat", highlightthickness=1, highlightcolor=color)

                def _start_slot(e=None, _l=sl_lbl, _s=sl_spx, _g=_get_slot):
                    _l.pack_forget()
                    _s.delete(0, tk.END); _s.insert(0, str(_g()))
                    _s.pack(side=tk.RIGHT); _s.focus_set()

                def _end_slot(e=None, _l=sl_lbl, _s=sl_spx, _g=_get_slot, _set=_set_slot,
                               _mx=maxi, _p=pip_frame):
                    try:
                        v = max(0, min(int(_s.get()), _mx))
                        _set(v)
                    except ValueError:
                        pass
                    _s.pack_forget()
                    cur2 = _g()
                    _l.config(text=f"{cur2}/{_mx}")
                    _l.pack(side=tk.RIGHT)
                    for i, pip in enumerate(_p.winfo_children()):
                        pip.config(bg=color if i < cur2 else "#333344")

                sl_lbl.bind("<Button-1>", _start_slot)
                sl_spx.bind("<Return>",   _end_slot)
                sl_spx.bind("<FocusOut>", _end_slot)
                sl_spx.bind("<Escape>",   lambda e, _end=_end_slot: _end())
                slot_widgets[lvl] = (sl_lbl, pip_frame, sl_spx, maxi)
        else:
            tk.Label(body, text="(Pas d'emplacements de sort)", bg="#1e1e2e",
                     fg="#444455", font=("Arial", 8, "italic")).pack(anchor="w")

        # ── Refresh global ────────────────────────────────────────────────
        def _rebuild_slots():
            d2 = load_state().get("characters", {}).get(char_name, {})
            sl = d2.get("spell_slots", {})
            for lvl, (lbl, pip_frame, spx, maxi) in slot_widgets.items():
                cur = sl.get(lvl, 0)
                lbl.config(text=f"{cur}/{maxi}")
                for i, pip in enumerate(pip_frame.winfo_children()):
                    pip.config(bg=color if i < cur else "#333344")

        def _refresh_all():
            try:
                d2 = load_state().get("characters", {}).get(char_name, {})
                h, mh = d2.get("hp", 0), d2.get("max_hp", 0)
                p  = max(0, min(1, h / mh)) if mh else 0
                hp_lbl.config(text=str(h), fg=self._hp_color(p))
                maxhp_lbl.config(text=str(mh))
                bar_fill.config(bg=self._hp_color(p))
                bar_fill.place(relwidth=p)
                used  = d2.get("hit_dice_used", 0)
                avail = max(0, level - used)
                hd_lbl.config(text=str(avail))
                ac_lbl.config(text=str(d2.get("ac", ac)))
                _rebuild_slots()
            except Exception:
                pass

        # ── Short Rest ────────────────────────────────────────────────────
        def _do_short_rest():
            import tkinter.simpledialog as _sd
            import random as _r
            s = load_state()
            d2 = s["characters"][char_name]
            h, mh = d2.get("hp", 0), d2.get("max_hp", 0)
            used  = d2.get("hit_dice_used", 0)
            avail = max(0, level - used)
            if avail == 0:
                from tkinter import messagebox as _mb
                _mb.showinfo("Short Rest", f"{char_name} n'a plus de Hit Dice !", parent=win)
                return
            nb = _sd.askinteger(
                "Short Rest",
                f"{char_name} — Combien de Hit Dice dépenser ?\n"
                f"d{hit_die} + {con_mod:+d} CON par dé    (disponibles : {avail}/{level})",
                minvalue=1, maxvalue=avail, parent=win)
            if not nb: return
            rolls  = [max(1, _r.randint(1, hit_die) + con_mod) for _ in range(nb)]
            healed = sum(rolls)
            new_hp = min(mh, h + healed)
            d2["hp"] = new_hp
            d2["hit_dice_used"] = used + nb
            save_state(s)
            detail = " + ".join(str(r) for r in rolls)
            self.msg_queue.put({"sender": "☽ Short Rest",
                                "text": f"{char_name} — {nb}d{hit_die} ({detail}) → +{healed} PV  ({h}→{new_hp}/{mh})",
                                "color": "#88aaff"})
            _refresh_all()

        def _do_long_rest():
            from tkinter import messagebox as _mb
            mh_now    = load_state().get("characters",{}).get(char_name,{}).get("max_hp", 0)
            recovered = max(1, level // 2)
            if not _mb.askyesno("Long Rest",
                                f"Long Rest pour {char_name} ?\n\n"
                                f"• PV restaurés à {mh_now}/{mh_now}\n"
                                f"• Hit Dice récupérés : {recovered} (max {level})\n"
                                f"• Tous les emplacements de sort restaurés",
                                parent=win): return
            s  = load_state()
            d2 = s["characters"][char_name]
            used = d2.get("hit_dice_used", 0)
            d2["hp"] = mh_now
            d2["hit_dice_used"]  = max(0, used - recovered)
            d2["spell_slots"]    = dict(max_slots)
            save_state(s)
            self.msg_queue.put({"sender": "☀ Long Rest",
                                "text": f"{char_name} — PV: {mh_now}/{mh_now} | "
                                        f"Hit Dice +{recovered} | Sorts restaurés",
                                "color": "#ffcc66"})
            _refresh_all()

        rest_frame = tk.Frame(body, bg="#1e1e2e")
        rest_frame.pack(fill=tk.X, pady=(8, 2))
        tk.Button(rest_frame, text="☽ Short Rest", bg="#1a2a3a", fg="#88aaff",
                  font=("Arial", 8, "bold"), relief="flat", bd=0, padx=6, pady=4,
                  activebackground="#2a3a4a", activeforeground="white",
                  command=_do_short_rest).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0,3))
        tk.Button(rest_frame, text="☀ Long Rest", bg="#2a2010", fg="#ffcc66",
                  font=("Arial", 8, "bold"), relief="flat", bd=0, padx=6, pady=4,
                  activebackground="#3a3020", activeforeground="white",
                  command=_do_long_rest).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3,0))

        # ════════════════════════════════════════════════════════════════════
        # ── ONGLET SORTS  (v3 — basé sur spells_prepared + spell_data) ──
        # Chaque personnage stocke uniquement une liste de noms anglais dans
        # campaign_state["characters"][name]["spells_prepared"].
        # Toutes les métadonnées (niveau, école, description) viennent
        # dynamiquement de spell_data.py, qui scanne les fichiers spells-*.json
        # du dossier spells/ en s'appuyant sur sources.json (sans hardcoding).
        # ════════════════════════════════════════════════════════════════════

        SCHOOL_COLORS = {
            "Abjuration": "#64b5f6", "Conjuration": "#81c784", "Divination": "#e9c46a",
            "Enchantment": "#f06292", "Evocation": "#e57373", "Illusion": "#ce93d8",
            "Necromancy": "#aaaaaa", "Transmutation": "#ffb74d",
        }

        # Préchargement du cache sorts + sources (non-bloquant)
        def _preload_spells():
            try:
                from spell_data import load_spells, load_sources_index
                load_spells()
                load_sources_index()
            except Exception:
                pass
        threading.Thread(target=_preload_spells, daemon=True).start()

        # ── Widgets de l'onglet ─────────────────────────────────────────────
        spell_list_outer = tk.Frame(spells_frame, bg="#1e1e2e")
        spell_list_outer.pack(fill=tk.BOTH, expand=True)

        sp_canvas = tk.Canvas(spell_list_outer, bg="#1e1e2e", highlightthickness=0)
        sp_scroll = tk.Scrollbar(spell_list_outer, orient="vertical", command=sp_canvas.yview)
        sp_canvas.configure(yscrollcommand=sp_scroll.set)
        sp_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        sp_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        sp_inner = tk.Frame(sp_canvas, bg="#1e1e2e")
        sp_window = sp_canvas.create_window((0, 0), window=sp_inner, anchor="nw")

        def _on_sp_configure(e):
            sp_canvas.configure(scrollregion=sp_canvas.bbox("all"))
        sp_inner.bind("<Configure>", _on_sp_configure)

        def _on_sp_canvas_configure(e):
            sp_canvas.itemconfig(sp_window, width=e.width)
        sp_canvas.bind("<Configure>", _on_sp_canvas_configure)

        # ── Barre de recherche + bouton Ajouter ─────────────────────────────
        search_var = tk.StringVar()
        spell_bar  = tk.Frame(spells_frame, bg="#12121e")
        spell_bar.pack(fill=tk.X)
        tk.Entry(spell_bar, textvariable=search_var, bg="#1e1e2e", fg="#aaaaaa",
                 insertbackground="white", font=("Consolas", 9),
                 relief="flat").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, pady=4)
        search_var.trace_add("write", lambda *_: _render_spells())

        stats_lbl = tk.Label(spell_bar, text="", bg="#12121e", fg="#444466",
                             font=("Consolas", 7))
        stats_lbl.pack(side=tk.LEFT, padx=4)

        tk.Button(spell_bar, text="＋ Sort", bg="#1a1a2e", fg=color,
                  font=("Arial", 9, "bold"), relief="flat", padx=8,
                  cursor="hand2",
                  command=lambda: _open_spell_picker()).pack(side=tk.RIGHT, padx=8, pady=4)

        # ── Helpers accès state ──────────────────────────────────────────────
        def _get_prepared() -> list:
            return list(load_state()
                        .get("characters", {})
                        .get(char_name, {})
                        .get("spells_prepared", []))

        def _set_prepared(names: list):
            s = load_state()
            s.setdefault("characters", {}).setdefault(char_name, {})["spells_prepared"] = names
            save_state(s)

        # ── Rendu de la liste ────────────────────────────────────────────────
        def _render_spells():
            for w in sp_inner.winfo_children():
                w.destroy()

            try:
                from spell_data import get_spell, load_spells
                load_spells()
            except Exception:
                get_spell = lambda n: None

            names  = _get_prepared()
            query  = search_var.get().lower().strip()

            stats_lbl.config(text=f"{len(names)} sorts")

            if not names:
                tk.Label(sp_inner, text="Aucun sort.\nCliquez ＋ pour en ajouter.",
                         bg="#1e1e2e", fg="#444455",
                         font=("Consolas", 9, "italic"), justify=tk.CENTER).pack(pady=20)
                return

            from collections import defaultdict
            by_level = defaultdict(list)
            for name in names:
                sp_data = get_spell(name)
                lvl = int(sp_data["level"]) if sp_data else 0
                if query and query not in name.lower() and query not in (
                        sp_data.get("school", "").lower() if sp_data else ""):
                    continue
                by_level[lvl].append((name, sp_data))

            if not by_level:
                tk.Label(sp_inner, text="Aucun sort correspond.",
                         bg="#1e1e2e", fg="#444455",
                         font=("Consolas", 9, "italic")).pack(pady=20)
                return

            for lvl in sorted(by_level.keys()):
                lvl_txt = "Cantrips" if lvl == 0 else f"Niveau {lvl}"
                hdr_row = tk.Frame(sp_inner, bg="#161622")
                hdr_row.pack(fill=tk.X, pady=(6, 1))
                tk.Label(hdr_row, text=lvl_txt, bg="#161622", fg=color,
                         font=("Arial", 8, "bold")).pack(side=tk.LEFT, padx=8, pady=3)
                tk.Label(hdr_row, text=str(len(by_level[lvl])), bg="#161622", fg="#444455",
                         font=("Consolas", 8)).pack(side=tk.RIGHT, padx=8)
                for spell_name, sp_data in by_level[lvl]:
                    _render_spell_row(spell_name, sp_data)

        def _render_spell_row(spell_name: str, sp_data):
            school = sp_data.get("school", "") if sp_data else ""
            school_color = SCHOOL_COLORS.get(school, "#888888")
            source = sp_data.get("source", "") if sp_data else ""
            conc   = sp_data.get("concentration", False) if sp_data else False
            rit    = sp_data.get("ritual", False) if sp_data else False

            row = tk.Frame(sp_inner, bg="#1a1a2a")
            row.pack(fill=tk.X, padx=4, pady=1)

            # Nom — cliquable pour ouvrir la fiche complète
            name_lbl = tk.Label(row, text=spell_name,
                                bg="#1a1a2a", fg="#e0e0e0",
                                font=("Consolas", 9, "bold"), anchor="w",
                                cursor="hand2")
            name_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 2), pady=3)

            def _open_sheet(e=None, _n=spell_name, _d=sp_data):
                try:
                    from spell_data import SpellSheetWindow
                    if _d:
                        SpellSheetWindow(win, _d)
                    else:
                        import tkinter.messagebox as mb
                        mb.showinfo("Sort inconnu",
                                    f"Aucune donnée trouvée pour « {_n} »\n"
                                    "(vérifiez que le fichier spells-*.json correspondant est présent).",
                                    parent=win)
                except Exception as _e:
                    print(f"[SpellSheet] {_e}")
            name_lbl.bind("<Button-1>", _open_sheet)
            name_lbl.bind("<Enter>", lambda e, l=name_lbl: l.config(fg="#e8c84a"))
            name_lbl.bind("<Leave>", lambda e, l=name_lbl: l.config(fg="#e0e0e0"))

            # Badges concentration / rituel
            if conc:
                tk.Label(row, text="◉", bg="#1a1a2a", fg="#ce93d8",
                         font=("Consolas", 7)).pack(side=tk.RIGHT, padx=1)
            if rit:
                tk.Label(row, text="®", bg="#1a1a2a", fg="#e9c46a",
                         font=("Consolas", 7)).pack(side=tk.RIGHT, padx=1)
            if source and source != "?":
                tk.Label(row, text=f"[{source}]", bg="#1a1a2a", fg="#554477",
                         font=("Consolas", 6)).pack(side=tk.RIGHT, padx=(0, 2))
            if school:
                tk.Label(row, text=school, bg="#1a1a2a", fg=school_color,
                         font=("Arial", 7, "italic")).pack(side=tk.RIGHT, padx=4)

            # Bouton supprimer
            def _remove(n=spell_name):
                names = _get_prepared()
                if n in names:
                    names.remove(n)
                    _set_prepared(names)
                    _render_spells()
            tk.Button(row, text="✕", bg="#1a1a2a", fg="#553333",
                      font=("Arial", 8), relief="flat", padx=2, cursor="hand2",
                      command=_remove).pack(side=tk.RIGHT, padx=(0, 2))

        # ── Dialogue de sélection de sort (SpellPickerDialog) ───────────────
        def _open_spell_picker():
            try:
                from spell_data import SpellPickerDialog, load_spells
                load_spells()
                def _on_select(sp_dict):
                    if not sp_dict:
                        return
                    name  = sp_dict.get("name", "")
                    if not name:
                        return
                    names = _get_prepared()
                    if name not in names:
                        names.append(name)
                        _set_prepared(names)
                    _render_spells()
                SpellPickerDialog(win, on_select=_on_select,
                                  title=f"Ajouter un sort — {char_name}")
            except Exception as e:
                print(f"[SpellPicker] {e}")
                import tkinter.messagebox as mb
                mb.showerror("Erreur", f"Impossible d'ouvrir le sélecteur de sorts :\n{e}",
                             parent=win)

        _render_spells()

        # ── Activation onglet Stats par défaut ─────────────────────────────
        _show_tab("stats")

        # ── Rafraîchissement auto toutes les 2 s ──────────────────────────
        def _refresh_popout():
            if not win.winfo_exists(): return
            _refresh_all()
            win.after(2000, _refresh_popout)
        win.after(2000, _refresh_popout)

    # ─── Entrée vocale ────────────────────────────────────────────────────────

    def send_voice(self):
        if not self.input_event.is_set():
            def voice_thread():
                self.msg_queue.put({"sender": "Système", "text": "🎤 Écoute en cours...", "color": "#2196F3"})
                texte = record_audio_and_transcribe()
                self.user_input = texte
                self.msg_queue.put({"sender": "Alexis_Le_MJ (Vocal)", "text": self.user_input, "color": "#4CAF50"})
                self.input_event.set()
            threading.Thread(target=voice_thread, daemon=True).start()

    def wait_for_input(self) -> str:
        self.input_event.clear()
        self.input_event.wait()
        return self.user_input