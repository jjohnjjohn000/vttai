"""
panels_tools_mixin.py

Contient les panneaux d'outils annexes : Inventaire, Journal de Quêtes, Jet de Compétence et Carte de Combat.
"""

import threading
import tkinter as tk

from panels_core_mixin import _ghost_close
from window_state import _save_window_state
from state_manager import get_quests, save_quests, QUEST_STATUSES
from llm_config import DND_SKILLS, ABILITY_COLORS
from combat_map_panel import open_combat_map as _open_combat_map


class PanelsToolsMixin:
    """Mixin gérant les outils : inventaire, quêtes, jets de compétences, carte."""

    def open_inventory_panel(self):
        """Ouvre (ou ramène au premier plan) le panneau d'inventaire du groupe."""
        if getattr(self, "_inventory_win", None):
            try:
                self._inventory_win.win.deiconify()
                self._inventory_win.win.lift()
                return
            except Exception:
                self._inventory_win = None
        from inventory_panel import InventoryPanel
        self._inventory_win = InventoryPanel(self.root)
        try:
            self._track_window("inventory", self._inventory_win.win)
        except Exception:
            pass

    def open_quest_journal(self):
        """Fenêtre de gestion du journal de quêtes."""
        win = tk.Toplevel(self.root)
        win.title("📜 Journal de Quêtes")
        win.geometry("820x620")
        win.configure(bg="#0d1117")
        win.grab_set()
        self._track_window("modal_quest_journal", win)
        self._quest_journal_win = win   # référence pour QuestTrackerMixin

        def _close_quest_journal():
            _ghost_close(win, self.root)
            self._quest_journal_win = None

        win.protocol("WM_DELETE_WINDOW", _close_quest_journal)

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

        def _launch_quest_ai():
            win.grab_release()   # libère le grab pour que le chat reste lisible
            self.process_quests_with_llm()

        tk.Button(
            header,
            text="Analyse IA",
            bg="#2a1a4a",
            fg="#c8b8ff",
            font=("Arial", 10, "bold"),
            relief="flat",
            cursor="hand2",
            command=_launch_quest_ai,
        ).pack(side=tk.RIGHT, padx=4, pady=8)

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

        def _on_mousewheel(e):
            list_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        def _on_m_up(e):
            list_canvas.yview_scroll(-1, "units")
        def _on_m_dn(e):
            list_canvas.yview_scroll(1, "units")

        def _bind_mw(e):
            list_canvas.bind_all("<MouseWheel>", _on_mousewheel)
            list_canvas.bind_all("<Button-4>", _on_m_up)
            list_canvas.bind_all("<Button-5>", _on_m_dn)
        def _unbind_mw(e):
            list_canvas.unbind_all("<MouseWheel>")
            list_canvas.unbind_all("<Button-4>")
            list_canvas.unbind_all("<Button-5>")

        list_col.bind("<Enter>", _bind_mw)
        list_col.bind("<Leave>", _unbind_mw)

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

        _list_cards = {}

        def _update_selection_colors():
            for qid, widgets in _list_cards.items():
                bg = "#1a2a3a" if qid == selected_id[0] else "#161b22"
                for w in widgets:
                    try:
                        if w.winfo_exists():
                            w.config(bg=bg)
                    except Exception:
                        pass

        def refresh_list():
            for w in list_inner.winfo_children():
                w.destroy()
            _list_cards.clear()

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
                    st_lbl = tk.Label(card, text=status_dot, bg=card_bg, fg=st_color,
                                      font=("Arial", 11, "bold"), width=2)
                    st_lbl.pack(side=tk.LEFT, padx=(6,2))

                    title_fg = "#e0e0e0" if q["status"] == "active" else "#777777"
                    title_lbl = tk.Label(card, text=q["title"], bg=card_bg, fg=title_fg,
                                         font=("Consolas", 10), anchor="w", wraplength=210, justify=tk.LEFT)
                    title_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=6, padx=4)

                    widgets = [card, st_lbl, title_lbl]

                    # Barre de progression objectifs
                    objs = q.get("objectives", [])
                    if objs:
                        done_count = sum(1 for o in objs if o.get("done"))
                        prog = tk.Label(card, text=f"{done_count}/{len(objs)}", bg=card_bg,
                                        fg="#555555", font=("Arial", 8))
                        prog.pack(side=tk.RIGHT, padx=6)
                        widgets.append(prog)

                    _list_cards[q["id"]] = widgets

                    def on_click(event, qid=q["id"]):
                        show_detail(qid)
                        _update_selection_colors()
                    
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

            def _close_quest_editor():
                _ghost_close(ew, self.root)

            ew.protocol("WM_DELETE_WINDOW", _close_quest_editor)

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
                _close_quest_editor()

            tk.Button(ew, text="✅ Sauvegarder", bg="#1a3a5c", fg="#64b5f6",
                      font=("Arial", 11, "bold"), relief="flat",
                      command=save_quest).pack(pady=12)

        # Initial render
        refresh_list()
        if quests:
            show_detail(quests[0]["id"])

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
        win.protocol("WM_DELETE_WINDOW", lambda: _ghost_close(win, self.root))

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
            _ghost_close(win, self.root)
            threading.Thread(
                target=self._execute_skill_check,
                args=(char, skill, ability, dc_val, reason),
                daemon=True
            ).start()

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
            update_sys_prompt_fn = lambda: self._rebuild_agent_prompts(),
            app = self,
        )