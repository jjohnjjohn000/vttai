"""
panels_npc_mixin.py

Contient la gestion des PNJ : menu déroulant du MJ et le gestionnaire unifié de PNJ.
"""

import tkinter as tk
from panels_core_mixin import _ghost_close
from state_manager import get_npcs, save_npcs, get_available_voices


class PanelsNPCMixin:
    """Mixin gérant l'interface des PNJ et le basculement de voix du MJ."""

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

    def open_npc_manager(self):
        """
        Gestionnaire unifié des PNJs — TTS + Bestiary + Image + Fiche.

        Chaque PNJ dispose de :
          • Nom, Couleur, Voix TTS, Vitesse TTS
          • Sélecteur de monstre du bestiary (autocomplétion)
          • PV actuels (initialisés depuis le bestiary)
          • Bouton Fiche → MonsterSheetWindow
          • Bouton Parler → génération LLM in-character
          • Bouton Image → sélecteur fichier + envoi aux agents Gemini

        Sauvegarde synchronisée :
          • save_npcs()       — config voix/TTS (pour la dropdown MJ)
        """
        from npc_bestiary_panel import (
            search_monsters, get_monster, MonsterSheetWindow,
            speak_as_npc, save_npc_image_bytes, load_npc_image_bytes,
            _fmt_cr, _fmt_type,
        )
        from state_manager import get_group_npcs, save_group_npcs

        # ── Fusion des deux sources de données ────────────────────────────────
        # On fusionne npcs (TTS) et group_npcs (bestiary) sur la clé "name"
        tts_list   = get_npcs()
        group_list = get_group_npcs()
        group_by_name = {n["name"]: n for n in group_list}

        # Voix par défaut adaptée au backend actuel
        _default_voice = get_available_voices()[0]

        # Liste de travail unifiée
        merged: list[dict] = []
        seen_names = set()
        for npc in tts_list:
            name = npc.get("name", "")
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            group_data = group_by_name.get(name, {})
            merged.append({
                "name":          name,
                "color":         npc.get("color", group_data.get("color", "#c77dff")),
                "voice":         npc.get("voice", _default_voice),
                "speed":         npc.get("speed", "+0%"),
                "bestiary_name": npc.get("bestiary_name") or group_data.get("bestiary_name") or "",
                "hp_current":    npc.get("hp_current") if npc.get("hp_current") is not None else group_data.get("hp_current"),
                "notes":         npc.get("notes", "") or group_data.get("notes", ""),
            })
        # PNJs présents dans group_npcs mais absents de npcs
        for name, gn in group_by_name.items():
            if name not in seen_names:
                merged.append({
                    "name":          name,
                    "color":         gn.get("color", "#c77dff"),
                    "voice":         _default_voice,
                    "speed":         "+0%",
                    "bestiary_name": gn.get("bestiary_name", ""),
                    "hp_current":    gn.get("hp_current"),
                    "notes":         gn.get("notes", ""),
                })

        # ── Fenêtre principale ────────────────────────────────────────────────
        BG  = "#12121a"
        BG2 = "#1a1a2a"
        BG3 = "#22223a"
        FG  = "#e0e0e0"
        FG_DIM = "#555566"
        GOLD   = "#ffd54f"
        GREEN  = "#81c784"
        PURPLE = "#ce93d8"
        RED    = "#e57373"

        win = tk.Toplevel(self.root)
        win.title("Gestionnaire de PNJs")
        win.geometry("1020x600")
        win.configure(bg=BG)
        win.minsize(860, 400)
        # Pas de grab_set : on veut pouvoir ouvrir MonsterSheetWindow en parallèle
        self._track_window("modal_npc_manager", win)
        win.protocol("WM_DELETE_WINDOW", lambda: _ghost_close(win, self.root))

        # ── En-tête ───────────────────────────────────────────────────────────
        hdr = tk.Frame(win, bg=BG2, pady=8)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="Gestionnaire de PNJs", bg=BG2, fg=PURPLE,
                 font=("Arial", 13, "bold")).pack(side=tk.LEFT, padx=14)
        tk.Label(hdr, text="TTS  •  Bestiary  •  Image  •  Fiche  •  Parler en tant que",
                 bg=BG2, fg=FG_DIM, font=("Arial", 9)).pack(side=tk.LEFT, padx=8)

        # ── En-têtes colonnes ─────────────────────────────────────────────────
        col_hdr = tk.Frame(win, bg=BG3, pady=4)
        col_hdr.pack(fill=tk.X, padx=0)
        for txt, w in [("Nom",    13), ("Couleur",  8), ("Voix TTS",   20),
                       ("Vitesse", 7), ("Monstre",  18), ("PV",         6),
                       ("Actions", 24)]:
            tk.Label(col_hdr, text=txt, bg=BG3, fg=GOLD,
                     font=("Arial", 8, "bold"), width=w, anchor="w"
                     ).pack(side=tk.LEFT, padx=4)

        # ── Zone scrollable ───────────────────────────────────────────────────
        list_outer = tk.Frame(win, bg=BG)
        list_outer.pack(fill=tk.BOTH, expand=True, padx=0)

        canvas   = tk.Canvas(list_outer, bg=BG, highlightthickness=0)
        vsb      = tk.Scrollbar(list_outer, orient="vertical", command=canvas.yview)
        scroll_f = tk.Frame(canvas, bg=BG)
        scroll_f.bind("<Configure>",
                      lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_f, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        for w2 in (canvas, scroll_f):
            w2.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        # ── Données de ligne (vars Tk) ────────────────────────────────────────
        # Chaque entrée : {name_var, color_var, voice_var, speed_var,
        #                  bestiary_var, hp_var, notes_var}
        self._npc_rows = []
        _open_sheets: dict[str, MonsterSheetWindow] = {}

        def _build_rows():
            for w2 in scroll_f.winfo_children():
                w2.destroy()
            self._npc_rows.clear()

            for i, npc in enumerate(merged):
                row_bg = BG2 if i % 2 == 0 else BG3
                row    = tk.Frame(scroll_f, bg=row_bg)
                row.pack(fill=tk.X, pady=1, padx=2)

                name_var     = tk.StringVar(value=npc.get("name", ""))
                color_var    = tk.StringVar(value=npc.get("color", "#c77dff"))
                voice_var    = tk.StringVar(value=npc.get("voice", get_available_voices()[0]))
                speed_var    = tk.StringVar(value=npc.get("speed", "+0%"))
                bestiary_var = tk.StringVar(value=npc.get("bestiary_name", "") or "")
                hp_var       = tk.StringVar(value=str(npc.get("hp_current") or ""))
                notes_var    = tk.StringVar(value=npc.get("notes", ""))

                # Nom
                tk.Entry(row, textvariable=name_var, width=13, bg="#252535",
                         fg=FG, font=("Consolas", 10),
                         insertbackground=FG, relief="flat"
                         ).pack(side=tk.LEFT, padx=3, ipady=4)

                # Couleur + aperçu
                color_e = tk.Entry(row, textvariable=color_var, width=8,
                                   bg="#252535", font=("Consolas", 10),
                                   insertbackground=FG, relief="flat")
                color_e.pack(side=tk.LEFT, padx=3, ipady=4)

                def _upd_col(var=color_var, e=color_e, *_a):
                    try:    e.config(fg=var.get())
                    except: e.config(fg=FG)
                color_var.trace_add("write", _upd_col)
                _upd_col()

                # Voix TTS — liste dynamique selon le backend configuré (piper ou edge-tts)
                _voices = get_available_voices()
                vm = tk.OptionMenu(row, voice_var, *_voices)
                vm.config(bg="#2a1a3a", fg=PURPLE, font=("Consolas", 8),
                          width=18, relief="flat", highlightthickness=0,
                          activebackground="#3a2a4a", activeforeground=PURPLE)
                vm["menu"].config(bg="#2a1a3a", fg=PURPLE, font=("Consolas", 8))
                vm.pack(side=tk.LEFT, padx=3)

                # Vitesse
                tk.Entry(row, textvariable=speed_var, width=7, bg="#252535",
                         fg=FG, font=("Consolas", 10),
                         insertbackground=FG, relief="flat"
                         ).pack(side=tk.LEFT, padx=3, ipady=4)

                # ── Sélecteur Monstre ─────────────────────────────────────────
                mon_frame = tk.Frame(row, bg=row_bg)
                mon_frame.pack(side=tk.LEFT, padx=2)

                mon_e = tk.Entry(mon_frame, textvariable=bestiary_var,
                                 width=16, bg="#1a0808", fg=RED,
                                 font=("Consolas", 9),
                                 insertbackground=RED, relief="flat")
                mon_e.pack(side=tk.LEFT, ipady=4)

                # Dropdown suggestion bestiary
                _sug_lb: list[tk.Listbox] = []

                def _hide_sug(lb_ref=_sug_lb):
                    if lb_ref:
                        try: lb_ref[0].destroy()
                        except: pass
                        lb_ref.clear()

                def _on_mon_key(event, ev=bestiary_var, row_w=row,
                                lb_ref=_sug_lb, entry=mon_e):
                    _hide_sug(lb_ref)
                    q = ev.get().strip()
                    if len(q) < 2:
                        return
                    sugs = search_monsters(q, 8)
                    if not sugs:
                        return
                    lb = tk.Listbox(win, bg="#1a0808", fg=RED,
                                    font=("Consolas", 9),
                                    height=min(len(sugs), 8),
                                    relief="flat", selectbackground="#3a1818",
                                    borderwidth=1)
                    for s in sugs:
                        lb.insert(tk.END, s)
                    # Positionne sous l'entry
                    entry.update_idletasks()
                    x = entry.winfo_rootx() - win.winfo_rootx()
                    y = entry.winfo_rooty() - win.winfo_rooty() + entry.winfo_height()
                    lb.place(x=x, y=y, width=180)
                    lb.lift()
                    lb_ref.append(lb)

                    def _pick(e2, lb2=lb, ev2=ev, lb_r=lb_ref):
                        sel = lb2.curselection()
                        if sel:
                            ev2.set(lb2.get(sel[0]))
                        _hide_sug(lb_r)
                    lb.bind("<<ListboxSelect>>", _pick)
                    lb.bind("<FocusOut>", lambda e2, r=lb_ref: _hide_sug(r))

                def _on_mon_confirm(event=None, ev=bestiary_var, hp_v=hp_var,
                                    lb_ref=_sug_lb):
                    _hide_sug(lb_ref)
                    name_b = ev.get().strip()
                    if not name_b:
                        return
                    m = get_monster(name_b)
                    if not m:
                        sugs = search_monsters(name_b, 1)
                        if sugs:
                            ev.set(sugs[0])
                            m = get_monster(sugs[0])
                    # Propose les PV par défaut si champ vide
                    if m and not hp_v.get().strip():
                        avg = m.get("hp", {}).get("average")
                        if avg:
                            hp_v.set(str(avg))

                mon_e.bind("<KeyRelease>", _on_mon_key)
                mon_e.bind("<Return>",     _on_mon_confirm)
                mon_e.bind("<FocusOut>",   _on_mon_confirm)

                # ── PV ────────────────────────────────────────────────────────
                tk.Entry(row, textvariable=hp_var, width=6, bg="#1a100a",
                         fg="#ffb74d", font=("Consolas", 10),
                         insertbackground="#ffb74d", relief="flat"
                         ).pack(side=tk.LEFT, padx=3, ipady=4)

                # ── Boutons actions ───────────────────────────────────────────
                btn_f = tk.Frame(row, bg=row_bg)
                btn_f.pack(side=tk.LEFT, padx=4)

                def _btn(parent, txt, bg, fg, cmd):
                    return tk.Button(parent, text=txt, bg=bg, fg=fg,
                                     font=("Arial", 8, "bold"), relief="flat",
                                     padx=5, pady=2, cursor="hand2",
                                     command=cmd)

                # Bouton Fiche
                def _open_sheet(nv=name_var, bv=bestiary_var, cv=color_var,
                                hv=hp_var):   # hv capturé ICI par valeur, pas par la closure de boucle
                    npc_n    = nv.get().strip()
                    bestiary = bv.get().strip()
                    color    = cv.get().strip()
                    if not npc_n:
                        return
                    ex = _open_sheets.get(npc_n)
                    if ex:
                        try:
                            ex.win.deiconify()
                            ex.win.lift()
                            return
                        except Exception:
                            pass

                    def _on_sel(new_b, bv2=bv, hv2=hv):
                        bv2.set(new_b)
                        m = get_monster(new_b)
                        if m and not hv2.get().strip():
                            avg = m.get("hp", {}).get("average")
                            if avg:
                                hv2.set(str(avg))

                    sheet = MonsterSheetWindow(
                        self.root, npc_n, bestiary or None,
                        on_select_callback=_on_sel,
                        win_state=self._win_state,
                        track_fn=self._track_window,
                        chat_queue=self.msg_queue,
                        audio_queue=getattr(self, "audio_queue", None),
                        npc_color=color,
                        get_scene_fn=lambda: __import__('state_manager').get_scene_prompt(),
                    )
                    _open_sheets[npc_n] = sheet

                    def _on_close(n=npc_n):
                        _open_sheets.pop(n, None)
                        try: sheet.win.destroy()
                        except: pass
                    sheet.win.protocol("WM_DELETE_WINDOW", _on_close)

                _btn(btn_f, "Fiche", "#1a0808", RED, _open_sheet
                     ).pack(side=tk.LEFT, padx=2)

                # Bouton Parler
                def _speak(nv=name_var, bv=bestiary_var, cv=color_var):
                    npc_n    = nv.get().strip()
                    bestiary = bv.get().strip()
                    color    = cv.get().strip()
                    if not npc_n:
                        return
                    monster = get_monster(bestiary) if bestiary else None
                    scene   = ""
                    try:
                        scene = __import__('state_manager').get_scene_prompt()
                    except Exception:
                        pass
                    self.msg_queue.put({
                        "sender": "Systeme",
                        "text":   f"{npc_n} prend la parole...",
                        "color":  "#555566",
                    })
                    speak_as_npc(
                        npc_n, monster, "",
                        self.msg_queue,
                        getattr(self, "audio_queue", None),
                        color=color, scene_context=scene,
                    )

                _btn(btn_f, "Parler", "#0e1a10", GREEN, _speak
                     ).pack(side=tk.LEFT, padx=2)

                # Bouton Image
                def _set_image(nv=name_var):
                    from tkinter import filedialog as _fd, messagebox as _mb
                    npc_n = nv.get().strip()
                    if not npc_n:
                        return
                    path = _fd.askopenfilename(
                        parent=win,
                        title=f"Image pour {npc_n}",
                        filetypes=[("Images", "*.png *.jpg *.jpeg *.webp"),
                                   ("Tous",   "*.*")],
                    )
                    if not path:
                        return
                    try:
                        from PIL import Image
                        import io
                        img = Image.open(path).convert("RGBA")
                        img.thumbnail((512, 512))
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        save_npc_image_bytes(npc_n, buf.getvalue())
                        self.msg_queue.put({
                            "sender": "Image NPC",
                            "text":   f"Image de {npc_n} enregistree.",
                            "color":  GREEN,
                        })
                    except Exception as e:
                        _mb.showerror("Image", f"Erreur : {e}", parent=win)

                _btn(btn_f, "Image", "#101820", "#64b5f6", _set_image
                     ).pack(side=tk.LEFT, padx=2)

                # Bouton Groupe — ajoute/retire ce PNJ du GroupNPCPanel
                def _make_groupe_btn(parent, nv=name_var, bv=bestiary_var,
                                     cv=color_var, hv=hp_var, row_bg_=row_bg):
                    """Crée un bouton [+Grp]/[−Grp] qui reflète l'état actuel."""
                    btn_holder = tk.Frame(parent, bg=row_bg_)
                    btn_holder.pack(side=tk.LEFT, padx=2)

                    def _in_group(npc_name: str) -> bool:
                        from state_manager import get_group_npcs
                        return any(n.get("name") == npc_name
                                   for n in get_group_npcs())

                    def _refresh_btn(btn, npc_name: str):
                        if _in_group(npc_name):
                            btn.config(text="−Grp", bg="#2a1a00", fg="#ff9800")
                        else:
                            btn.config(text="+Grp", bg="#0a2010", fg="#81c784")

                    def _toggle(btn_ref):
                        from state_manager import get_group_npcs, save_group_npcs
                        npc_name = nv.get().strip()
                        if not npc_name:
                            return
                        npcs = get_group_npcs()
                        if _in_group(npc_name):
                            # Retirer du groupe
                            npcs = [n for n in npcs if n.get("name") != npc_name]
                            save_group_npcs(npcs)
                        else:
                            # Ajouter au groupe
                            bestiary = bv.get().strip() or None
                            hp_raw   = hv.get().strip()
                            try:    hp = int(hp_raw)
                            except: hp = None
                            # PV par défaut depuis le bestiary si non renseigné
                            if hp is None and bestiary:
                                m = get_monster(bestiary)
                                if m:
                                    hp = m.get("hp", {}).get("average")
                            npcs.append({
                                "name":          npc_name,
                                "color":         cv.get().strip() or "#c77dff",
                                "bestiary_name": bestiary,
                                "hp_current":    hp,
                                "notes":         "",
                            })
                            save_group_npcs(npcs)
                        _refresh_btn(btn_ref, npc_name)
                        # Rafraîchit le GroupNPCPanel s'il est ouvert
                        panel = getattr(self, "_group_npc_panel", None)
                        if panel:
                            try: panel._refresh()
                            except Exception: pass

                    # Créer le bouton, puis initialiser son état
                    btn = tk.Button(btn_holder, text="+Grp",
                                    bg="#0a2010", fg="#81c784",
                                    font=("Arial", 7, "bold"), relief="flat",
                                    padx=4, pady=2, cursor="hand2")
                    btn.config(command=lambda b=btn: _toggle(b))
                    btn.pack()
                    _refresh_btn(btn, nv.get().strip())
                    return btn_holder

                _make_groupe_btn(btn_f)

                # Bouton supprimer
                def _remove(idx=i):
                    merged.pop(idx)
                    _build_rows()

                _btn(btn_f, "X", "#2a0808", "#ff6b6b", _remove
                     ).pack(side=tk.LEFT, padx=2)

                self._npc_rows.append({
                    "name":     name_var,
                    "color":    color_var,
                    "voice":    voice_var,
                    "speed":    speed_var,
                    "bestiary": bestiary_var,
                    "hp":       hp_var,
                    "notes":    notes_var,
                })

        _build_rows()

        # ── Barre du bas ──────────────────────────────────────────────────────
        bottom = tk.Frame(win, bg=BG2, pady=8)
        bottom.pack(fill=tk.X, padx=10)

        def _add_npc():
            merged.append({
                "name": "Nouveau PNJ", "color": "#c77dff",
                "voice": get_available_voices()[0], "speed": "+0%",
                "bestiary_name": "", "hp_current": None, "notes": "",
            })
            _build_rows()

        def _save_and_close():
            """Écrit dans npcs (TTS) et met à jour bestiary/PV/notes
            pour les PNJs déjà présents dans le groupe (sans en ajouter)."""
            tts_updated   = []

            for rv in self._npc_rows:
                name     = rv["name"].get().strip()
                if not name:
                    continue
                color    = rv["color"].get().strip()   or "#c77dff"
                voice    = rv["voice"].get()
                speed    = rv["speed"].get()            or "+0%"
                bestiary = rv["bestiary"].get().strip()
                hp_raw   = rv["hp"].get().strip()
                notes    = rv["notes"].get().strip()

                try:    hp = int(hp_raw)
                except: hp = None

                tts_updated.append({
                    "name":          name,
                    "color":         color,
                    "voice":         voice,
                    "speed":         speed,
                    "bestiary_name": bestiary or None,
                    "hp_current":    hp,
                    "notes":         notes,
                })

            # Sauvegarde TTS
            save_npcs(tts_updated)

            # Mise à jour bestiary/PV/notes pour les PNJs déjà dans le groupe
            # — ne crée pas de nouveaux membres, met à jour les existants seulement
            try:
                existing = get_group_npcs()
                existing_by_name = {n["name"]: n for n in existing}
                changed = False
                for rv in self._npc_rows:
                    name = rv["name"].get().strip()
                    if name not in existing_by_name:
                        continue   # pas dans le groupe → on ne l'ajoute pas
                    bestiary = rv["bestiary"].get().strip()
                    hp_raw   = rv["hp"].get().strip()
                    notes    = rv["notes"].get().strip()
                    try:    hp = int(hp_raw)
                    except: hp = None
                    entry = existing_by_name[name]
                    if (entry.get("bestiary_name") != (bestiary or None)
                            or entry.get("hp_current") != hp
                            or entry.get("notes", "") != notes
                            or entry.get("color") != (rv["color"].get().strip() or "#c77dff")):
                        entry["bestiary_name"] = bestiary or None
                        entry["hp_current"]    = hp
                        entry["notes"]         = notes
                        entry["color"]         = rv["color"].get().strip() or "#c77dff"
                        changed = True
                if changed:
                    save_group_npcs(existing)
            except Exception as _e:
                print(f"[NPC Manager] Erreur màj group_npcs : {_e}")

            # Mise à jour VOICE_MAPPING dynamique
            try:
                from voice_interface import VOICE_MAPPING, SPEED_MAPPING
                for npc in tts_updated:
                    bare_key = npc["name"]
                    npc_key  = f"__npc__{bare_key}"
                    # Stocke sous les DEUX clés :
                    #   __npc__<nom>  → lecture via _get_piper_voice_id (piper)
                    #   <nom>         → lecture directe via VOICE_MAPPING (edge-tts + piper)
                    VOICE_MAPPING[npc_key]  = npc["voice"]
                    VOICE_MAPPING[bare_key] = npc["voice"]
                    SPEED_MAPPING[npc_key]  = npc["speed"]
                    SPEED_MAPPING[bare_key] = npc["speed"]
            except Exception:
                pass

            # Rafraîchit le menu NPC dans l'UI principale
            self._refresh_npc_dropdown()

            # Rafraîchit le GroupNPCPanel s'il existe
            panel = getattr(self, "_group_npc_panel", None)
            if panel:
                try:
                    panel._refresh()
                except Exception:
                    pass

            _ghost_close(win, self.root)

        tk.Button(bottom, text="+ Ajouter un PNJ", bg="#1a2a1a", fg=GREEN,
                  font=("Arial", 10, "bold"), relief="flat", padx=10,
                  command=_add_npc).pack(side=tk.LEFT)

        tk.Label(bottom, text="Fermer la fiche pour valider le monstre selectionne",
                 bg=BG2, fg=FG_DIM, font=("Arial", 8)).pack(side=tk.LEFT, padx=14)

        tk.Button(bottom, text="Annuler", bg="#2a2a3a", fg="#888",
                  font=("Arial", 10), relief="flat", padx=8,
                  command=lambda: _ghost_close(win, self.root)).pack(side=tk.RIGHT, padx=6)

        tk.Button(bottom, text="Sauvegarder", bg="#4CAF50", fg="white",
                  font=("Arial", 10, "bold"), relief="flat", padx=12,
                  command=_save_and_close).pack(side=tk.RIGHT)