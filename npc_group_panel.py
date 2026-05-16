"""
npc_group_panel.py — Panneau latéral (sidebar) gérant les PNJs actifs du groupe.
"""

import tkinter as tk

from npc_sheet_window import MonsterSheetWindow
from npc_bestiary_manager import get_monster
from npc_utils import speak_as_npc, _fmt_type, _fmt_cr

class GroupNPCPanel:
    """
    Panneau latéral listant les PNJs actuellement dans le groupe.
    Cliquer sur un nom ouvre MonsterSheetWindow.
    Géré dans state_manager via get_group_npcs / save_group_npcs.
    """

    BG     = "#0d1a0d"
    BG2    = "#0f1f0f"
    FG     = "#a5d6a7"
    FG_DIM = "#3a5a3a"
    ACCENT = "#4CAF50"

    def __init__(self, parent_frame: tk.Frame, root, win_state: dict,
                 save_win_state_fn, track_fn, msg_queue, audio_queue=None,
                 get_scene_fn=None):
        self.root             = root
        self._win_state       = win_state
        self._save_ws         = save_win_state_fn
        self._track           = track_fn
        self._msg_queue       = msg_queue
        self._audio_queue     = audio_queue
        self._get_scene_fn    = get_scene_fn
        self._open_sheets: dict[str, MonsterSheetWindow] = {}

        # Import ici pour éviter une dépendance circulaire
        from state_manager import get_group_npcs, save_group_npcs
        self._get_npcs  = get_group_npcs
        self._save_npcs = save_group_npcs

        # ── Conteneur principal ───────────────────────────────────────────────
        self._frame = tk.Frame(parent_frame, bg=self.BG)
        self._frame.pack(fill=tk.X, padx=8, pady=(0, 4))

        # En-tête
        hdr = tk.Frame(self._frame, bg=self.BG)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="👥 PNJs DU GROUPE", bg=self.BG, fg=self.FG,
                 font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=6, pady=(6, 2))
        tk.Button(hdr, text="＋", bg=self.BG, fg=self.ACCENT,
                  font=("Arial", 10, "bold"), relief="flat",
                  command=self._add_npc).pack(side=tk.RIGHT, padx=4, pady=2)

        # Zone de la liste
        self._list_frame = tk.Frame(self._frame, bg=self.BG)
        self._list_frame.pack(fill=tk.X)

        self._refresh()

    def _refresh(self):
        """Reconstruit la liste des PNJs du groupe."""
        for w in self._list_frame.winfo_children():
            w.destroy()

        npcs = self._get_npcs()
        if not npcs:
            tk.Label(self._list_frame, text="Aucun PNJ dans le groupe",
                     bg=self.BG, fg=self.FG_DIM,
                     font=("Consolas", 8, "italic"),
                     anchor="w", padx=8, pady=4).pack(fill=tk.X)
            return

        for i, npc in enumerate(npcs):
            name      = npc.get("name", "?")
            bestiary  = npc.get("bestiary_name", "")
            color     = npc.get("color", self.FG)
            hp_cur    = npc.get("hp_current")
            row_bg    = "#0d1a0d" if i % 2 == 0 else "#0f220f"

            row = tk.Frame(self._list_frame, bg=row_bg)
            row.pack(fill=tk.X, pady=1)

            # ── Indicateur monstre associé ────────────────────────────────────
            icon = "📋" if bestiary else "❓"
            tk.Label(row, text=icon, bg=row_bg,
                     fg=color if bestiary else self.FG_DIM,
                     font=("TkDefaultFont", 9)).pack(side=tk.LEFT, padx=(4, 1), pady=3)

            # ── Nom cliquable → ouvre la fiche ────────────────────────────────
            name_lbl = tk.Label(row, text=name, bg=row_bg, fg=color,
                                font=("Consolas", 9, "bold"), anchor="w",
                                cursor="hand2")
            name_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=3)
            for w2 in (row, name_lbl):
                w2.bind("<Button-1>",
                        lambda e, n=name, b=bestiary: self._open_sheet(n, b))

            # ── PV cliquables → édition inline ────────────────────────────────
            if hp_cur is not None:
                m_data = get_monster(bestiary) if bestiary else None
                hp_max = m_data.get("hp", {}).get("average", "?") if m_data else "?"
                hp_color = (
                    "#81c784" if (isinstance(hp_max, int) and hp_cur > hp_max * 0.5)
                    else "#FF9800" if (isinstance(hp_max, int) and hp_cur > hp_max * 0.25)
                    else "#e57373"
                )
                hp_lbl = tk.Label(row, text=f"❤ {hp_cur}/{hp_max}",
                                  bg=row_bg, fg=hp_color,
                                  font=("Consolas", 8), cursor="hand2")
                hp_lbl.pack(side=tk.RIGHT, padx=(0, 2))
                hp_lbl.bind("<Button-1>",
                            lambda e, n=name, idx=i: self._edit_hp_dialog(n, idx))

            # ── Bouton 🎭 Parler en tant que ──────────────────────────────────
            speak_btn = tk.Button(
                row, text="🎭", bg=row_bg, fg="#9b8fc7",
                font=("Arial", 9), relief="flat", padx=3, pady=1,
                cursor="hand2",
                command=lambda n=name, b=bestiary, c=color:
                    self._speak_as_dialog(n, b, c)
            )
            speak_btn.pack(side=tk.RIGHT, padx=1)

            # ── Bouton 📋 Fiche rapide ────────────────────────────────────────
            sheet_btn = tk.Button(
                row, text="📋", bg=row_bg, fg=self.FG_DIM,
                font=("Arial", 9), relief="flat", padx=3, pady=1,
                cursor="hand2",
                command=lambda n=name, b=bestiary: self._open_sheet(n, b)
            )
            sheet_btn.pack(side=tk.RIGHT, padx=1)

            # ── Bouton supprimer ──────────────────────────────────────────────
            tk.Button(row, text="✕", bg=row_bg, fg="#553333",
                      font=("Arial", 7), relief="flat", padx=2,
                      cursor="hand2",
                      command=lambda idx=i: self._remove_npc(idx)
                      ).pack(side=tk.RIGHT, padx=2)

    def _open_sheet(self, npc_name: str, bestiary_name: str | None):
        """Ouvre (ou ramène) la fiche de monstre pour ce PNJ."""
        existing = self._open_sheets.get(npc_name)
        if existing:
            try:
                existing.win.deiconify()
                existing.win.lift()
                return
            except Exception:
                pass

        # Couleur du PNJ
        npcs = self._get_npcs()
        npc_color = next(
            (n.get("color", "#e0e0e0") for n in npcs if n.get("name") == npc_name),
            "#e0e0e0"
        )

        def _on_select(new_bestiary: str):
            """Callback quand le MJ sélectionne un monstre dans la fiche."""
            npcs = self._get_npcs()
            for npc in npcs:
                if npc.get("name") == npc_name:
                    npc["bestiary_name"] = new_bestiary
                    # Initialise les PV au max du monstre
                    m = get_monster(new_bestiary)
                    if m and npc.get("hp_current") is None:
                        npc["hp_current"] = m.get("hp", {}).get("average")
                    break
            self._save_npcs(npcs)
            self._refresh()
            self._msg_queue.put({
                "sender": "📋 PNJ",
                "text":   f"{npc_name} → fiche de monstre : {new_bestiary}",
                "color":  "#a5d6a7"
            })

        sheet = MonsterSheetWindow(
            self.root, npc_name, bestiary_name,
            on_select_callback=_on_select,
            win_state=self._win_state,
            track_fn=self._track,
            chat_queue=self._msg_queue,
            audio_queue=self._audio_queue,
            npc_color=npc_color,
            get_scene_fn=self._get_scene_fn,
        )
        self._open_sheets[npc_name] = sheet

        def _on_close():
            self._open_sheets.pop(npc_name, None)
            try:
                # X11 fix : withdraw + ghost
                try: sheet.win.selection_clear()
                except Exception: pass
                try:
                    sheet.win.unbind_all("<MouseWheel>")
                    sheet.win.unbind_all("<Button-4>")
                    sheet.win.unbind_all("<Button-5>")
                except Exception: pass
                sheet.win.withdraw()
                sheet.win.update_idletasks()
                if not hasattr(self.root, "_ghosted_panels"):
                    self.root._ghosted_panels = []
                self.root._ghosted_panels.append(sheet.win)
            except Exception:
                pass

        sheet.win.protocol("WM_DELETE_WINDOW", _on_close)

    # ─── Dialogue "Parler en tant que" ────────────────────────────────────────

    def _speak_as_dialog(self, npc_name: str, bestiary_name: str | None,
                         npc_color: str):
        """Fenêtre rapide : MJ tape un prompt → LLM génère la réplique du PNJ."""
        monster = get_monster(bestiary_name) if bestiary_name else None

        dlg = tk.Toplevel(self.root)
        dlg.title(f"🎭 Parler en tant que {npc_name}")
        dlg.configure(bg="#0e1a10")
        dlg.geometry("420x220")
        dlg.resizable(False, False)
        dlg.wait_visibility()
        dlg.grab_set()

        tk.Label(dlg, text=f"🎭 {npc_name} prend la parole",
                 bg="#0e1a10", fg="#a5d6a7",
                 font=("Arial", 11, "bold")).pack(pady=(14, 4))

        if monster:
            sub = f"{_fmt_type(monster.get('type','?'))}  •  FP {_fmt_cr(monster.get('cr','?'))}"
            tk.Label(dlg, text=sub, bg="#0e1a10", fg="#4a7a4a",
                     font=("Consolas", 8)).pack()

        tk.Label(dlg, text="Contexte / question (optionnel) :",
                 bg="#0e1a10", fg="#888", font=("Arial", 8)
                 ).pack(anchor="w", padx=14, pady=(10, 2))

        prompt_var = tk.StringVar()
        entry = tk.Entry(dlg, textvariable=prompt_var,
                         bg="#0d1f0d", fg="white", font=("Consolas", 10),
                         insertbackground="white", relief="flat")
        entry.pack(fill=tk.X, padx=14, ipady=6)
        entry.focus_set()

        scene_var = tk.BooleanVar(value=True)
        tk.Checkbutton(dlg, text="Inclure le contexte de scène",
                       variable=scene_var, bg="#0e1a10", fg="#7aad7a",
                       selectcolor="#0e1a10", activebackground="#0e1a10",
                       font=("Arial", 8)).pack(anchor="w", padx=14, pady=4)

        def _send():
            prompt = prompt_var.get().strip()
            scene  = ""
            if scene_var.get() and self._get_scene_fn:
                try:
                    scene = self._get_scene_fn()
                except Exception:
                    pass
            self._msg_queue.put({
                "sender": "🎭 Système",
                "text":   f"{npc_name} prend la parole…",
                "color":  "#555566",
            })
            speak_as_npc(
                npc_name, monster, prompt,
                self._msg_queue, self._audio_queue,
                color=npc_color, scene_context=scene,
            )
            dlg.destroy()

        entry.bind("<Return>", lambda e: _send())
        tk.Button(dlg, text="Générer la réplique", bg="#1a3a1a", fg="#81c784",
                  font=("Arial", 10, "bold"), relief="flat", pady=6,
                  command=_send).pack(fill=tk.X, padx=14, pady=(4, 14))

    # ─── Édition HP inline ────────────────────────────────────────────────────

    def _edit_hp_dialog(self, npc_name: str, idx: int):
        """Mini-dialog pour modifier les PV d'un PNJ directement."""
        npcs = self._get_npcs()
        if idx >= len(npcs):
            return
        npc     = npcs[idx]
        hp_cur  = npc.get("hp_current", 0)
        bestiary = npc.get("bestiary_name", "")
        m_data  = get_monster(bestiary) if bestiary else None
        hp_max  = m_data.get("hp", {}).get("average", "?") if m_data else "?"

        dlg = tk.Toplevel(self.root)
        dlg.title(f"❤ PV — {npc_name}")
        dlg.configure(bg="#1a0d0d")
        dlg.geometry("280x150")
        dlg.resizable(False, False)
        dlg.wait_visibility()
        dlg.grab_set()

        tk.Label(dlg, text=f"PV actuels de {npc_name}",
                 bg="#1a0d0d", fg="#e57373", font=("Arial", 10, "bold")
                 ).pack(pady=(12, 4))
        tk.Label(dlg, text=f"(max : {hp_max})",
                 bg="#1a0d0d", fg="#888", font=("Consolas", 8)).pack()

        hp_var = tk.StringVar(value=str(hp_cur))
        entry  = tk.Entry(dlg, textvariable=hp_var, bg="#2a0d0d", fg="white",
                          font=("Consolas", 14, "bold"), justify="center",
                          insertbackground="white", relief="flat", width=8)
        entry.pack(pady=8, ipady=6)
        entry.select_range(0, tk.END)
        entry.focus_set()

        def _save_hp():
            try:
                new_hp = int(hp_var.get())
                npcs[idx]["hp_current"] = new_hp
                self._save_npcs(npcs)
                self._refresh()
                dlg.destroy()
            except ValueError:
                entry.config(bg="#3a0000")

        entry.bind("<Return>", lambda e: _save_hp())
        tk.Button(dlg, text="Appliquer", bg="#2a1010", fg="#e57373",
                  font=("Arial", 9, "bold"), relief="flat",
                  command=_save_hp).pack()

    def _add_npc(self):
        """Ouvre une mini-fenêtre pour ajouter un PNJ au groupe."""
        dialog = tk.Toplevel(self.root)
        dialog.title("＋ Ajouter un PNJ au groupe")
        dialog.geometry("400x310")
        dialog.configure(bg="#0d1117")
        dialog.resizable(False, True)
        dialog.wait_visibility()
        dialog.grab_set()

        tk.Label(dialog, text="Nom du PNJ :", bg="#0d1117", fg="#a5d6a7",
                 font=("Arial", 10, "bold")).pack(anchor="w", padx=14, pady=(14, 2))
        name_var = tk.StringVar()
        tk.Entry(dialog, textvariable=name_var, bg="#161b22", fg="white",
                 font=("Consolas", 11), insertbackground="white",
                 relief="flat").pack(fill=tk.X, padx=14, ipady=5)

        tk.Label(dialog, text="Couleur (hex, ex: #a5d6a7) :", bg="#0d1117", fg="#888",
                 font=("Arial", 8)).pack(anchor="w", padx=14, pady=(8, 2))
        color_var = tk.StringVar(value="#a5d6a7")
        tk.Entry(dialog, textvariable=color_var, bg="#161b22", fg="white",
                 font=("Consolas", 10), insertbackground="white",
                 relief="flat", width=14).pack(anchor="w", padx=14, ipady=3)

        # ── Section Sorts ─────────────────────────────────────────────────────
        tk.Frame(dialog, bg="#2a3040", height=1).pack(fill=tk.X, padx=10, pady=(12, 4))

        spell_hdr = tk.Frame(dialog, bg="#0d1117")
        spell_hdr.pack(fill=tk.X, padx=14)
        tk.Label(spell_hdr, text="✨ Sorts du PNJ :", bg="#0d1117", fg="#9b8fc7",
                 font=("Arial", 9, "bold")).pack(side=tk.LEFT)

        _npc_spells: list = []   # sorts choisis pour ce PNJ

        def _open_spell_picker():
            try:
                from spell_data import SpellPickerDialog
            except ImportError:
                return
            def _on_pick(sp: dict):
                if not any(s["name"] == sp["name"] for s in _npc_spells):
                    _npc_spells.append(sp)
                _refresh_spell_lbl()
            SpellPickerDialog(dialog, _on_pick,
                              title="✨ Sorts — " + (name_var.get() or "PNJ"))

        def _clear_spells():
            _npc_spells.clear()
            _refresh_spell_lbl()

        def _refresh_spell_lbl():
            if _npc_spells:
                txt = "  ".join(
                    f"✨ {s['name']} (Niv {'TM' if s.get('level',1)==0 else s.get('level',1)})"
                    for s in _npc_spells
                )
                spells_lbl.config(text=txt, fg="#a855f7")
            else:
                spells_lbl.config(text="(aucun sort)", fg="#444466")

        btn_row = tk.Frame(dialog, bg="#0d1117")
        btn_row.pack(fill=tk.X, padx=14, pady=(2, 2))
        tk.Button(btn_row, text="＋ Ajouter sort",
                  bg="#1a103a", fg="#9b8fc7",
                  font=("Arial", 8, "bold"), relief="flat",
                  padx=8, pady=2,
                  command=_open_spell_picker).pack(side=tk.LEFT)
        tk.Button(btn_row, text="✕ Vider",
                  bg="#1a0808", fg="#885555",
                  font=("Arial", 7), relief="flat",
                  padx=4, pady=2,
                  command=_clear_spells).pack(side=tk.LEFT, padx=6)

        spells_lbl = tk.Label(dialog, text="(aucun sort)",
                              bg="#0d1117", fg="#444466",
                              font=("Consolas", 7, "italic"), anchor="w",
                              wraplength=370, justify=tk.LEFT)
        spells_lbl.pack(fill=tk.X, padx=14, pady=(2, 8))

        def _save():
            name = name_var.get().strip()
            if not name:
                return
            npcs = self._get_npcs()
            entry = {
                "name":  name,
                "color": color_var.get().strip() or "#a5d6a7",
                "bestiary_name": None,
                "hp_current": None,
                "notes": "",
            }
            if _npc_spells:
                entry["spells"] = list(_npc_spells)
            npcs.append(entry)
            self._save_npcs(npcs)
            self._refresh()
            dialog.destroy()

        tk.Button(dialog, text="✅ Ajouter", bg="#1a3a1a", fg="#81c784",
                  font=("Arial", 10, "bold"), relief="flat",
                  command=_save).pack(pady=10)

    def _remove_npc(self, idx: int):
        npcs = self._get_npcs()
        if 0 <= idx < len(npcs):
            npcs.pop(idx)
            self._save_npcs(npcs)
            self._refresh()