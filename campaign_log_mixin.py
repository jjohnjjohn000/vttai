"""
campaign_log_mixin.py — Intégration du journal chronologique + Mémoires dans DnDApp.

Fournit CampaignLogMixin à injecter dans DnDApp :
  - open_campaign_log_viewer  : fenêtre à onglets (Chroniques / Mémoires)
  - trigger_archive_session   : archive la session la plus ancienne manuellement
  - _auto_archive_old_sessions: appelé automatiquement par session_mixin après
                                 chaque fin de session
  - _generate_archive_summary : génère un résumé LLM d'un bloc de sessions

Prérequis sur l'instance hôte :
  self.root, self.msg_queue, self._win_state, self._track_window()
  self._agents (pour savoir quels chars sont actifs)
"""

import threading
import tkinter as tk
from tkinter import ttk, messagebox

from campaign_log  import (
    get_campaign_log, auto_archive_if_needed,
    RECENT_SESSION_WINDOW,
)
from state_manager import (
    load_state, save_state,
    get_memories, save_memories, add_memory, update_memory, delete_memory,
    MEMORY_CATEGORIES,
)
from app_config    import get_chronicler_config


# ── Constantes de thème ───────────────────────────────────────────────────────
_BG     = "#1a1a2e"
_BG2    = "#16213e"
_BG3    = "#1e2744"
_ACCENT = "#c8b8ff"
_FG     = "#e8e8f0"
_GOLD   = "#FFD700"
_RED    = "#ff6b6b"
_GREEN  = "#81c784"
_BLUE   = "#64b5f6"

# Style ttk pour les onglets
_NOTEBOOK_STYLE_APPLIED = False

def _apply_notebook_style():
    global _NOTEBOOK_STYLE_APPLIED
    if _NOTEBOOK_STYLE_APPLIED:
        return
    style = ttk.Style()
    style.theme_use("default")
    style.configure("Dark.TNotebook", background=_BG, borderwidth=0)
    style.configure("Dark.TNotebook.Tab",
                     background="#2a2a4e", foreground=_FG,
                     padding=[14, 6], font=("Georgia", 11, "bold"))
    style.map("Dark.TNotebook.Tab",
              background=[("selected", "#3a3a6e")],
              foreground=[("selected", _GOLD)])
    _NOTEBOOK_STYLE_APPLIED = True


class CampaignLogMixin:
    """Mixin pour DnDApp — Chroniques de campagne + Gestion des Mémoires."""

    # ─── Ouverture du visualiseur ────────────────────────────────────────────

    def open_campaign_log_viewer(self):
        """Ouvre (ou ramène) la fenêtre à onglets Chroniques / Mémoires."""
        if getattr(self, "_campaign_log_win", None):
            try:
                self._campaign_log_win.deiconify()
                self._campaign_log_win.lift()
                return
            except Exception:
                self._campaign_log_win = None

        win = tk.Toplevel(self.root)
        win.title("📖 Chroniques & Mémoires")
        win.configure(bg=_BG)
        self._campaign_log_win = win
        try:
            self._track_window("campaign_log_viewer", win)
        except Exception:
            win.geometry("960x720")

        _apply_notebook_style()

        notebook = ttk.Notebook(win, style="Dark.TNotebook")
        notebook.pack(fill="both", expand=True, padx=6, pady=6)

        # ── Onglet 1 : Chroniques ─────────────────────────────────────────────
        tab_chron = tk.Frame(notebook, bg=_BG)
        notebook.add(tab_chron, text="  📜 Chroniques  ")
        self._build_campaign_log_tab(tab_chron, win)

        # ── Onglet 2 : Mémoires ──────────────────────────────────────────────
        tab_mem = tk.Frame(notebook, bg=_BG)
        notebook.add(tab_mem, text="  🧠 Mémoires  ")
        self._build_memories_tab(tab_mem, win)

        win._notebook = notebook

    # ═════════════════════════════════════════════════════════════════════════
    # ONGLET CHRONIQUES
    # ═════════════════════════════════════════════════════════════════════════

    def _build_campaign_log_tab(self, parent: tk.Frame, win: tk.Toplevel):
        """Construit le contenu de l'onglet Chroniques."""
        # ── En-tête ──────────────────────────────────────────────────────────
        header = tk.Frame(parent, bg=_BG, pady=8)
        header.pack(fill="x", padx=10)

        tk.Label(
            header, text="📜  Chroniques de la Campagne",
            font=("Georgia", 14, "bold"), fg=_GOLD, bg=_BG,
        ).pack(side="left", padx=8)

        btn_frame = tk.Frame(header, bg=_BG)
        btn_frame.pack(side="right", padx=8)

        tk.Button(
            btn_frame, text="[+] Archiver maintenant",
            bg="#2a2a4e", fg=_ACCENT, font=("Consolas", 10),
            relief="flat", cursor="hand2",
            command=lambda: self._manual_archive_from_viewer(win),
        ).pack(side="left", padx=4)

        tk.Button(
            btn_frame, text="[R] Rafraîchir",
            bg="#2a2a4e", fg=_FG, font=("Consolas", 10),
            relief="flat", cursor="hand2",
            command=lambda: self._refresh_campaign_log_tab(parent, win),
        ).pack(side="left", padx=4)

        # ── Stats compactes ──────────────────────────────────────────────────
        log   = get_campaign_log()
        stats = log.summary_stats()
        state = load_state()
        n_recent = len(state.get("session_logs", []))

        stats_bar = tk.Frame(parent, bg=_BG2, pady=4)
        stats_bar.pack(fill="x", padx=10, pady=(0, 6))
        stats_txt = (
            f"  {stats['count']} bloc(s) archivé(s)  ·  "
            f"{stats.get('total_chars', 0):,} chars  ·  "
            f"{len(stats.get('sessions_covered', []))} sessions archivées  ·  "
            f"{n_recent} session(s) récente(s) non-archivées"
        )
        tk.Label(stats_bar, text=stats_txt, fg="#aaaacc", bg=_BG2,
                 font=("Consolas", 9)).pack(anchor="w", padx=8)

        # ── Panneau principal : liste gauche + détail droit ──────────────────
        main_pane = tk.PanedWindow(parent, orient="horizontal", bg=_BG,
                                   sashwidth=5, sashrelief="flat")
        main_pane.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # ── Liste des entrées ────────────────────────────────────────────────
        list_frame = tk.Frame(main_pane, bg=_BG2, width=260)
        main_pane.add(list_frame, minsize=220)

        tk.Label(list_frame, text="Entrées archivées",
                 font=("Consolas", 10, "bold"), fg=_ACCENT, bg=_BG2,
                 anchor="w").pack(fill="x", padx=8, pady=(6, 2))

        listbox_frame = tk.Frame(list_frame, bg=_BG2)
        listbox_frame.pack(fill="both", expand=True)

        scrollbar_list = tk.Scrollbar(listbox_frame, bg=_BG2)
        scrollbar_list.pack(side="right", fill="y")

        listbox = tk.Listbox(
            listbox_frame,
            bg=_BG2, fg=_FG, selectbackground="#3a3a6e",
            font=("Consolas", 10), relief="flat",
            yscrollcommand=scrollbar_list.set,
            activestyle="none", cursor="hand2",
        )
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar_list.config(command=listbox.yview)

        # ── Détail de l'entrée sélectionnée ─────────────────────────────────
        detail_frame = tk.Frame(main_pane, bg=_BG)
        main_pane.add(detail_frame, minsize=400)

        detail_header = tk.Frame(detail_frame, bg=_BG)
        detail_header.pack(fill="x")

        lbl_entry_title = tk.Label(
            detail_header, text="Sélectionne une entrée…",
            font=("Georgia", 12, "bold"), fg=_GOLD, bg=_BG, anchor="w",
        )
        lbl_entry_title.pack(side="left", padx=8, pady=4)

        lbl_entry_meta = tk.Label(
            detail_header, text="",
            font=("Consolas", 9), fg="#aaaacc", bg=_BG, anchor="w",
        )
        lbl_entry_meta.pack(side="left", padx=4)

        # Zone texte
        txt_frame = tk.Frame(detail_frame, bg=_BG)
        txt_frame.pack(fill="both", expand=True, padx=4, pady=4)

        scrollbar_txt = tk.Scrollbar(txt_frame)
        scrollbar_txt.pack(side="right", fill="y")

        detail_text = tk.Text(
            txt_frame, bg=_BG2, fg=_FG,
            font=("Georgia", 11), relief="flat",
            wrap="word", state="disabled",
            yscrollcommand=scrollbar_txt.set,
            padx=12, pady=8,
        )
        detail_text.pack(side="left", fill="both", expand=True)
        scrollbar_txt.config(command=detail_text.yview)

        # Zone mots-clés
        kw_frame = tk.Frame(detail_frame, bg=_BG2, pady=4)
        kw_frame.pack(fill="x", padx=4, pady=(0, 4))
        lbl_keywords = tk.Label(
            kw_frame, text="", fg="#aaaacc", bg=_BG2,
            font=("Consolas", 9), anchor="w", wraplength=580,
        )
        lbl_keywords.pack(anchor="w", padx=8)

        # ── Remplissage de la liste ──────────────────────────────────────────
        entries = log.entries
        entry_refs: list[dict] = []

        # Sessions récentes non archivées
        recent_logs = state.get("session_logs", [])
        if recent_logs:
            listbox.insert("end", "── Sessions récentes ──")
            listbox.itemconfig("end", fg="#666688")
            entry_refs.append(None)
            for slog in recent_logs:
                label = f"  Session {slog['session']}  ({slog.get('date','?')[:10]})"
                listbox.insert("end", label)
                entry_refs.append({"_type": "recent", "data": slog})

        if entries:
            listbox.insert("end", "── Archivées ──")
            listbox.itemconfig("end", fg="#666688")
            entry_refs.append(None)
            for e in entries:
                imp = "★" * e.get("importance", 2)
                label = f"  {imp} {e.get('label', e['id'])}"
                listbox.insert("end", label)
                entry_refs.append({"_type": "archived", "data": e})

        # ── Callback de sélection ────────────────────────────────────────────
        def _on_select(event=None):
            sel = listbox.curselection()
            if not sel:
                return
            idx = sel[0]
            if idx >= len(entry_refs):
                return
            ref = entry_refs[idx]
            if ref is None:
                return

            if ref["_type"] == "recent":
                slog = ref["data"]
                lbl_entry_title.config(
                    text=f"Session {slog['session']} (non archivée)"
                )
                lbl_entry_meta.config(text=f"  {slog.get('date','?')}")
                lbl_keywords.config(text="→ Pas encore archivée dans le journal long terme")
                _set_detail_text(detail_text, slog.get("resume", ""))
            else:
                entry = ref["data"]
                lbl_entry_title.config(text=entry.get("label", entry["id"]))
                r = entry.get("session_range", [0, 0])
                reads = entry.get("agent_reads", {})
                reads_str = ", ".join(f"{k} ({v[:10]})" for k, v in reads.items()) or "aucun"
                lbl_entry_meta.config(
                    text=(
                        f"  Sessions {r[0]}–{r[1]}  ·  "
                        f"Archivé le {entry.get('date_archived','?')[:10]}  ·  "
                        f"Lu par : {reads_str}"
                    )
                )
                kws = entry.get("keywords", [])
                lbl_keywords.config(text="Mots-clés : " + ", ".join(kws))
                _set_detail_text(detail_text, entry.get("summary", ""))

        listbox.bind("<<ListboxSelect>>", _on_select)

    def _refresh_campaign_log_tab(self, parent: tk.Frame, win: tk.Toplevel):
        """Recharge le contenu de l'onglet Chroniques."""
        for w in parent.winfo_children():
            w.destroy()
        get_campaign_log().reload()
        self._build_campaign_log_tab(parent, win)

    # ═════════════════════════════════════════════════════════════════════════
    # ONGLET MÉMOIRES
    # ═════════════════════════════════════════════════════════════════════════

    def _build_memories_tab(self, parent: tk.Frame, win: tk.Toplevel):
        """Construit l'onglet de gestion des mémoires."""

        # ── En-tête ──────────────────────────────────────────────────────────
        header = tk.Frame(parent, bg=_BG, pady=6)
        header.pack(fill="x", padx=10)

        tk.Label(header, text="🧠  Mémoires du Groupe",
                 font=("Georgia", 14, "bold"), fg=_GOLD, bg=_BG,
                 ).pack(side="left", padx=8)

        btn_hdr = tk.Frame(header, bg=_BG)
        btn_hdr.pack(side="right", padx=8)

        tk.Button(btn_hdr, text="[+] Nouvelle Mémoire",
                  bg="#1a3a2a", fg=_GREEN, font=("Consolas", 10),
                  relief="flat", cursor="hand2",
                  command=lambda: self._open_memory_editor(parent, win, None),
                  ).pack(side="left", padx=4)

        # ── Filtres ──────────────────────────────────────────────────────────
        filter_bar = tk.Frame(parent, bg=_BG2, pady=4)
        filter_bar.pack(fill="x", padx=10, pady=(0, 4))

        tk.Label(filter_bar, text="Filtre :", fg="#aaaacc", bg=_BG2,
                 font=("Consolas", 9)).pack(side="left", padx=(8, 4))

        cat_var = tk.StringVar(value="Toutes")
        cat_choices = ["Toutes"] + [
            f"{v['icon']} {v['label']}" for v in MEMORY_CATEGORIES.values()
        ]
        cat_menu = tk.OptionMenu(filter_bar, cat_var, *cat_choices)
        cat_menu.config(bg="#2a2a4e", fg=_FG, font=("Consolas", 9),
                        activebackground="#3a3a6e", relief="flat",
                        highlightthickness=0)
        cat_menu["menu"].config(bg="#2a2a4e", fg=_FG, font=("Consolas", 9))
        cat_menu.pack(side="left", padx=4)

        imp_var = tk.StringVar(value="Toutes")
        imp_menu = tk.OptionMenu(filter_bar, imp_var,
                                  "Toutes", "★ Mineur+", "★★ Notable+", "★★★ Critique")
        imp_menu.config(bg="#2a2a4e", fg=_FG, font=("Consolas", 9),
                        activebackground="#3a3a6e", relief="flat",
                        highlightthickness=0)
        imp_menu["menu"].config(bg="#2a2a4e", fg=_FG, font=("Consolas", 9))
        imp_menu.pack(side="left", padx=4)

        show_hidden_var = tk.BooleanVar(value=False)
        tk.Checkbutton(filter_bar, text="Cachées", variable=show_hidden_var,
                       bg=_BG2, fg="#aaaacc", selectcolor="#2a2a4e",
                       font=("Consolas", 9), activebackground=_BG2,
                       command=lambda: win.after(10, _refresh_list),
                       ).pack(side="left", padx=8)

        # ── Panneau principal ────────────────────────────────────────────────
        main_pane = tk.PanedWindow(parent, orient="horizontal", bg=_BG,
                                   sashwidth=5, sashrelief="flat")
        main_pane.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # ── Liste gauche ─────────────────────────────────────────────────────
        list_frame = tk.Frame(main_pane, bg=_BG2, width=280)
        main_pane.add(list_frame, minsize=240)

        lbl_count = tk.Label(list_frame, text="",
                             font=("Consolas", 9, "bold"), fg=_ACCENT, bg=_BG2,
                             anchor="w")
        lbl_count.pack(fill="x", padx=8, pady=(6, 2))

        listbox_frame = tk.Frame(list_frame, bg=_BG2)
        listbox_frame.pack(fill="both", expand=True)

        sb = tk.Scrollbar(listbox_frame, bg=_BG2)
        sb.pack(side="right", fill="y")

        listbox = tk.Listbox(
            listbox_frame, bg=_BG2, fg=_FG,
            selectbackground="#3a3a6e", font=("Consolas", 10),
            relief="flat", yscrollcommand=sb.set,
            activestyle="none", cursor="hand2",
        )
        listbox.pack(side="left", fill="both", expand=True)
        sb.config(command=listbox.yview)

        # ── Détail droit ─────────────────────────────────────────────────────
        detail_frame = tk.Frame(main_pane, bg=_BG)
        main_pane.add(detail_frame, minsize=400)

        detail_hdr = tk.Frame(detail_frame, bg=_BG)
        detail_hdr.pack(fill="x")

        lbl_title = tk.Label(detail_hdr, text="Sélectionne une mémoire…",
                             font=("Georgia", 12, "bold"), fg=_GOLD, bg=_BG,
                             anchor="w")
        lbl_title.pack(side="left", padx=8, pady=4)

        lbl_meta = tk.Label(detail_hdr, text="",
                            font=("Consolas", 9), fg="#aaaacc", bg=_BG,
                            anchor="w")
        lbl_meta.pack(side="left", padx=4)

        # Zone contenu
        txt_frame = tk.Frame(detail_frame, bg=_BG)
        txt_frame.pack(fill="both", expand=True, padx=4, pady=4)

        sb_txt = tk.Scrollbar(txt_frame)
        sb_txt.pack(side="right", fill="y")

        detail_text = tk.Text(
            txt_frame, bg=_BG2, fg=_FG,
            font=("Georgia", 11), relief="flat",
            wrap="word", state="disabled",
            yscrollcommand=sb_txt.set, padx=12, pady=8,
        )
        detail_text.pack(side="left", fill="both", expand=True)
        sb_txt.config(command=detail_text.yview)

        # Zone tags
        tag_frame = tk.Frame(detail_frame, bg=_BG2, pady=4)
        tag_frame.pack(fill="x", padx=4, pady=(0, 2))
        lbl_tags = tk.Label(tag_frame, text="", fg="#aaaacc", bg=_BG2,
                            font=("Consolas", 9), anchor="w", wraplength=580)
        lbl_tags.pack(anchor="w", padx=8)

        # Boutons action
        action_bar = tk.Frame(detail_frame, bg=_BG, pady=4)
        action_bar.pack(fill="x", padx=8)

        btn_edit = tk.Button(action_bar, text="✏️ Éditer", bg="#2a2a4e", fg=_ACCENT,
                             font=("Consolas", 10), relief="flat", cursor="hand2",
                             state="disabled")
        btn_edit.pack(side="left", padx=4)

        btn_toggle = tk.Button(action_bar, text="👁 Masquer", bg="#2a2a4e", fg="#e0a040",
                               font=("Consolas", 10), relief="flat", cursor="hand2",
                               state="disabled")
        btn_toggle.pack(side="left", padx=4)

        btn_delete = tk.Button(action_bar, text="🗑 Supprimer", bg="#3a1a1a", fg=_RED,
                               font=("Consolas", 10), relief="flat", cursor="hand2",
                               state="disabled")
        btn_delete.pack(side="left", padx=4)

        # ── Données internes ─────────────────────────────────────────────────
        mem_refs: list = []  # list[dict | None] (None = séparateur)
        selected_mem = [None]  # mutable pour closures

        def _get_cat_filter():
            v = cat_var.get()
            if v == "Toutes":
                return None
            for key, meta in MEMORY_CATEGORIES.items():
                if meta["label"] in v:
                    return key
            return None

        def _get_imp_filter():
            v = imp_var.get()
            if "★★★" in v:
                return 3
            if "★★" in v:
                return 2
            if "★" in v:
                return 1
            return 1

        def _refresh_list():
            listbox.delete(0, "end")
            mem_refs.clear()
            selected_mem[0] = None

            cat_filter = _get_cat_filter()
            imp_filter = _get_imp_filter()
            show_hidden = show_hidden_var.get()

            all_mems = get_memories(
                categorie=cat_filter,
                importance_min=imp_filter,
                visible_only=not show_hidden,
            )

            # Grouper par catégorie
            by_cat: dict[str, list] = {}
            for m in all_mems:
                by_cat.setdefault(m["categorie"], []).append(m)

            total = 0
            for cat_key in MEMORY_CATEGORIES:
                mems = by_cat.get(cat_key, [])
                if not mems:
                    continue
                meta = MEMORY_CATEGORIES[cat_key]
                listbox.insert("end", f"── {meta['icon']} {meta['label']} ──")
                listbox.itemconfig("end", fg="#666688")
                mem_refs.append(None)

                for m in sorted(mems, key=lambda x: -x.get("importance", 1)):
                    stars = "★" * m.get("importance", 1)
                    vis = "" if m.get("visible", True) else " 👁‍🗨"
                    label = f"  {stars} {m['titre']}{vis}"
                    listbox.insert("end", label)
                    if not m.get("visible", True):
                        listbox.itemconfig("end", fg="#666688")
                    mem_refs.append(m)
                    total += 1

            lbl_count.config(text=f"{total} mémoire(s)")

            # Réinitialiser détail
            lbl_title.config(text="Sélectionne une mémoire…")
            lbl_meta.config(text="")
            _set_detail_text(detail_text, "")
            lbl_tags.config(text="")
            btn_edit.config(state="disabled")
            btn_toggle.config(state="disabled")
            btn_delete.config(state="disabled")

        def _on_select(event=None):
            sel = listbox.curselection()
            if not sel:
                return
            idx = sel[0]
            if idx >= len(mem_refs):
                return
            mem = mem_refs[idx]
            if mem is None:
                return  # séparateur

            selected_mem[0] = mem
            meta = MEMORY_CATEGORIES.get(mem["categorie"], {"icon": "•", "label": mem["categorie"]})
            stars = "★" * mem.get("importance", 1) + "☆" * (3 - mem.get("importance", 1))

            lbl_title.config(text=f"{meta['icon']} {mem['titre']}")
            vis_txt = "✅ Visible" if mem.get("visible", True) else "👁‍🗨 Cachée"
            lbl_meta.config(
                text=(
                    f"  [{stars}]  ·  {meta['label']}  ·  "
                    f"Session {mem.get('session_ajout', '?')}  ·  "
                    f"{vis_txt}"
                )
            )
            _set_detail_text(detail_text, mem.get("contenu", ""))
            tags = mem.get("tags", [])
            lbl_tags.config(text="Tags : " + ", ".join(tags) if tags else "")

            btn_edit.config(state="normal")
            btn_toggle.config(
                state="normal",
                text="👁 Révéler" if not mem.get("visible", True) else "👁 Masquer",
            )
            btn_delete.config(state="normal")

        listbox.bind("<<ListboxSelect>>", _on_select)

        def _do_edit():
            if selected_mem[0]:
                self._open_memory_editor(parent, win, selected_mem[0])

        def _do_toggle():
            mem = selected_mem[0]
            if not mem:
                return
            new_vis = not mem.get("visible", True)
            update_memory(mem["id"], visible=new_vis)
            _refresh_list()

        def _do_delete():
            mem = selected_mem[0]
            if not mem:
                return
            if messagebox.askyesno(
                "Supprimer",
                f"Supprimer définitivement « {mem['titre']} » ?",
                parent=win,
            ):
                delete_memory(mem["id"])
                _refresh_list()

        btn_edit.config(command=_do_edit)
        btn_toggle.config(command=_do_toggle)
        btn_delete.config(command=_do_delete)

        # Lier les filtres
        cat_var.trace_add("write", lambda *_: win.after(10, _refresh_list))
        imp_var.trace_add("write", lambda *_: win.after(10, _refresh_list))

        # Initial load
        _refresh_list()

        # Stocker refresh pour accès externe
        parent._refresh_memories = _refresh_list

    # ─── Éditeur de mémoire (popup) ──────────────────────────────────────────

    def _open_memory_editor(self, tab_parent, win, existing_mem: dict | None):
        """Ouvre un dialogue d'édition/création de mémoire."""
        is_new = existing_mem is None
        dialog = tk.Toplevel(win)
        dialog.title("Nouvelle mémoire" if is_new else f"Éditer — {existing_mem.get('titre', '')}")
        dialog.configure(bg=_BG)
        dialog.geometry("520x560")
        dialog.transient(win)
        dialog.grab_set()

        row = 0

        # Catégorie
        tk.Label(dialog, text="Catégorie :", fg=_ACCENT, bg=_BG,
                 font=("Consolas", 10)).grid(row=row, column=0, sticky="w", padx=12, pady=6)
        cat_var = tk.StringVar(
            value=existing_mem.get("categorie", "lieu") if existing_mem else "lieu"
        )
        cat_keys = list(MEMORY_CATEGORIES.keys())
        cat_labels = [f"{v['icon']} {v['label']}" for v in MEMORY_CATEGORIES.values()]
        cat_display = tk.StringVar(
            value=cat_labels[cat_keys.index(cat_var.get())] if cat_var.get() in cat_keys else cat_labels[0]
        )
        cat_menu = tk.OptionMenu(dialog, cat_display, *cat_labels)
        cat_menu.config(bg="#2a2a4e", fg=_FG, font=("Consolas", 10), relief="flat",
                        highlightthickness=0)
        cat_menu["menu"].config(bg="#2a2a4e", fg=_FG, font=("Consolas", 10))
        cat_menu.grid(row=row, column=1, sticky="ew", padx=12, pady=6)
        row += 1

        # Titre
        tk.Label(dialog, text="Titre :", fg=_ACCENT, bg=_BG,
                 font=("Consolas", 10)).grid(row=row, column=0, sticky="w", padx=12, pady=6)
        title_entry = tk.Entry(dialog, bg=_BG2, fg=_FG, font=("Georgia", 11),
                               insertbackground="white")
        title_entry.grid(row=row, column=1, sticky="ew", padx=12, pady=6)
        if existing_mem:
            title_entry.insert(0, existing_mem.get("titre", ""))
        row += 1

        # Contenu
        tk.Label(dialog, text="Contenu :", fg=_ACCENT, bg=_BG,
                 font=("Consolas", 10)).grid(row=row, column=0, sticky="nw", padx=12, pady=6)
        content_text = tk.Text(dialog, bg=_BG2, fg=_FG, font=("Georgia", 11),
                               wrap="word", height=8, insertbackground="white",
                               padx=8, pady=6)
        content_text.grid(row=row, column=1, sticky="nsew", padx=12, pady=6)
        if existing_mem:
            content_text.insert("1.0", existing_mem.get("contenu", ""))
        row += 1

        # Tags
        tk.Label(dialog, text="Tags :", fg=_ACCENT, bg=_BG,
                 font=("Consolas", 10)).grid(row=row, column=0, sticky="w", padx=12, pady=6)
        tags_entry = tk.Entry(dialog, bg=_BG2, fg=_FG, font=("Consolas", 10),
                              insertbackground="white")
        tags_entry.grid(row=row, column=1, sticky="ew", padx=12, pady=6)
        if existing_mem and existing_mem.get("tags"):
            tags_entry.insert(0, ", ".join(existing_mem["tags"]))
        tk.Label(dialog, text="(séparés par des virgules)", fg="#666688", bg=_BG,
                 font=("Consolas", 8)).grid(row=row, column=1, sticky="se", padx=16)
        row += 1

        # Importance
        tk.Label(dialog, text="Importance :", fg=_ACCENT, bg=_BG,
                 font=("Consolas", 10)).grid(row=row, column=0, sticky="w", padx=12, pady=6)
        imp_var = tk.IntVar(value=existing_mem.get("importance", 2) if existing_mem else 2)
        imp_frame = tk.Frame(dialog, bg=_BG)
        imp_frame.grid(row=row, column=1, sticky="w", padx=12, pady=6)
        for val, lbl in [(1, "★ Mineur"), (2, "★★ Notable"), (3, "★★★ Critique")]:
            tk.Radiobutton(imp_frame, text=lbl, variable=imp_var, value=val,
                           bg=_BG, fg=_FG, selectcolor="#2a2a4e",
                           font=("Consolas", 10), activebackground=_BG,
                           ).pack(side="left", padx=6)
        row += 1

        # Visible
        vis_var = tk.BooleanVar(value=existing_mem.get("visible", True) if existing_mem else True)
        tk.Checkbutton(dialog, text="Visible pour les agents", variable=vis_var,
                       bg=_BG, fg=_FG, selectcolor="#2a2a4e",
                       font=("Consolas", 10), activebackground=_BG,
                       ).grid(row=row, column=1, sticky="w", padx=12, pady=6)
        row += 1

        # Session
        tk.Label(dialog, text="Session :", fg=_ACCENT, bg=_BG,
                 font=("Consolas", 10)).grid(row=row, column=0, sticky="w", padx=12, pady=6)
        session_entry = tk.Entry(dialog, bg=_BG2, fg=_FG, font=("Consolas", 10),
                                 insertbackground="white", width=6)
        session_entry.grid(row=row, column=1, sticky="w", padx=12, pady=6)
        if existing_mem:
            session_entry.insert(0, str(existing_mem.get("session_ajout", 0)))
        else:
            # Auto-detect latest session
            state = load_state()
            logs = state.get("session_logs", [])
            latest = logs[-1]["session"] if logs else 0
            session_entry.insert(0, str(latest))
        row += 1

        # Boutons
        dialog.grid_columnconfigure(1, weight=1)
        dialog.grid_rowconfigure(2, weight=1)  # contenu expandable

        btn_bar = tk.Frame(dialog, bg=_BG)
        btn_bar.grid(row=row, column=0, columnspan=2, pady=12)

        def _save():
            # Résoudre catégorie
            disp = cat_display.get()
            resolved_cat = "lieu"
            for k, v in MEMORY_CATEGORIES.items():
                if v["label"] in disp:
                    resolved_cat = k
                    break

            titre = title_entry.get().strip()
            contenu = content_text.get("1.0", "end").strip()
            tags_raw = tags_entry.get().strip()
            tags_list = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
            importance = imp_var.get()
            visible = vis_var.get()
            session = 0
            try:
                session = int(session_entry.get().strip())
            except ValueError:
                pass

            if not titre:
                messagebox.showwarning("Champ requis", "Le titre est obligatoire.", parent=dialog)
                return
            if not contenu:
                messagebox.showwarning("Champ requis", "Le contenu est obligatoire.", parent=dialog)
                return

            if is_new:
                add_memory(
                    categorie=resolved_cat,
                    titre=titre,
                    contenu=contenu,
                    tags=tags_list,
                    importance=importance,
                    session_ajout=session,
                )
            else:
                update_memory(
                    existing_mem["id"],
                    categorie=resolved_cat,
                    titre=titre,
                    contenu=contenu,
                    tags=tags_list,
                    importance=importance,
                    visible=visible,
                    session_ajout=session,
                )

            dialog.destroy()
            # Refresh la liste
            if hasattr(tab_parent, "_refresh_memories"):
                tab_parent._refresh_memories()

        tk.Button(btn_bar, text="💾 Sauvegarder", bg="#1a3a2a", fg=_GREEN,
                  font=("Consolas", 11, "bold"), relief="flat", padx=16, pady=4,
                  cursor="hand2", command=_save).pack(side="left", padx=8)

        tk.Button(btn_bar, text="Annuler", bg="#2a2a4e", fg=_FG,
                  font=("Consolas", 11), relief="flat", padx=16, pady=4,
                  cursor="hand2", command=dialog.destroy).pack(side="left", padx=8)

    # ═════════════════════════════════════════════════════════════════════════
    # ARCHIVAGE  (inchangé)
    # ═════════════════════════════════════════════════════════════════════════

    def _refresh_campaign_log_viewer(self, win: tk.Toplevel):
        """Recharge le contenu de la fenêtre journal (compat legacy)."""
        # Rebuild the Chroniques tab inside the notebook
        nb = getattr(win, "_notebook", None)
        if nb:
            tab_chron = nb.nametowidget(nb.tabs()[0])
            for w in tab_chron.winfo_children():
                w.destroy()
            get_campaign_log().reload()
            self._build_campaign_log_tab(tab_chron, win)
        else:
            # Fallback : full rebuild
            for w in win.winfo_children():
                w.destroy()
            get_campaign_log().reload()
            self.open_campaign_log_viewer()

    def _manual_archive_from_viewer(self, win: tk.Toplevel):
        """Archive manuellement la/les session(s) la/les plus ancienne(s)."""
        self.msg_queue.put({
            "sender": "📜 Chroniques",
            "text":   "Archivage manuel en cours…",
            "color":  "#c8b8ff",
        })
        threading.Thread(
            target=self._do_archive_and_refresh,
            args=(win,),
            daemon=True,
        ).start()

    def _do_archive_and_refresh(self, win: tk.Toplevel):
        if getattr(self, '_session_paused', False):
            self.msg_queue.put({
                "sender": "⏸ Session",
                "text": "Session en pause — archivage différé. Appuyez sur ▶ Reprendre puis relancez.",
                "color": "#e67e22",
            })
            return
        state = load_state()
        archived = auto_archive_if_needed(
            state         = state,
            save_state_fn = save_state,
            summary_fn    = self._generate_archive_summary,
        )
        if archived:
            self.msg_queue.put({
                "sender": "📜 Chroniques",
                "text":   "Archivage terminé — journal mis à jour.",
                "color":  "#c8b8ff",
            })
            try:
                self.root.after(0, lambda: self._refresh_campaign_log_viewer(win))
            except Exception:
                pass
        else:
            self.msg_queue.put({
                "sender": "📜 Chroniques",
                "text":   (
                    f"Aucune session à archiver — "
                    f"il faut plus de {RECENT_SESSION_WINDOW} sessions récentes."
                ),
                "color":  "#aaaacc",
            })

    # ─── Génération LLM du résumé d'archivage ────────────────────────────────

    def _generate_archive_summary(self, session_entries: list[dict]) -> str:
        """
        Utilise le Chroniqueur IA pour générer un résumé condensé d'un
        groupe de sessions avant archivage.
        """
        if getattr(self, '_session_paused', False):
            print("[CampaignLogMixin] Génération résumé annulée — session en pause.")
            return ""
        try:
            import autogen
            from llm_config import build_llm_config, _default_model
            from campaign_log import get_full_campaign_history_prompt

            _chron    = get_chronicler_config()
            _chron_llm = build_llm_config(
                _chron.get("model", _default_model),
                temperature=_chron.get("temperature", 0.3),
            )
            client = autogen.OpenAIWrapper(config_list=_chron_llm["config_list"])

            parts = []
            for e in sorted(session_entries, key=lambda x: x["session"]):
                parts.append(
                    f"═══ Session {e['session']} ({e.get('date','?')}) ═══\n{e['resume']}"
                )
            sessions_text = "\n\n".join(parts)

            session_nums = [e["session"] for e in session_entries]
            session_label = (
                f"Session {session_nums[0]}"
                if len(session_nums) == 1
                else f"Sessions {min(session_nums)} à {max(session_nums)}"
            )

            existing_history = get_full_campaign_history_prompt()
            history_context  = f"\n\nHistorique déjà archivé :\n{existing_history}" if existing_history else ""

            system_prompt = (
                "Tu es le Chroniqueur IA d'une campagne D&D 5e en cours. "
                "Ton rôle est de consolider plusieurs résumés de session en un seul bloc "
                "narratif cohérent et immersif, qui sera archivé comme mémoire long terme. "
                "Ce bloc doit :\n"
                "  • Être rédigé à la 3e personne comme un chroniqueur historique\n"
                "  • Capturer les événements clés, décisions importantes, PNJs rencontrés\n"
                "  • Souligner les éléments de continuité narrative (quêtes, relations, menaces)\n"
                "  • Être suffisamment détaillé pour qu'un agent puisse le retrouver par mots-clés\n"
                "  • Ne PAS inclure les détails mécaniques purs (jets de dés, HP exacts)\n"
                "Longueur cible : 300–600 mots."
            )

            user_prompt = (
                f"Voici les résumés bruts des {session_label} à consolider :\n\n"
                f"{sessions_text}"
                f"{history_context}\n\n"
                f"Rédige maintenant le bloc d'archive narratif consolidé pour ces sessions."
            )

            response = client.create(messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ])
            return response.choices[0].message.content.strip()

        except Exception as e:
            print(f"[CampaignLogMixin] Erreur génération résumé LLM : {e}")
            return ""

    # ─── Auto-archivage (appelé par session_mixin) ────────────────────────────

    def _auto_archive_old_sessions(self):
        """
        Vérifie si les session_logs dépassent la fenêtre glissante et archive
        les plus anciens. Doit être appelé depuis un thread non-UI (background).
        """
        if getattr(self, '_session_paused', False):
            print("[CampaignLogMixin] Auto-archivage différé — session en pause.")
            return
        state = load_state()
        archived = auto_archive_if_needed(
            state         = state,
            save_state_fn = save_state,
            summary_fn    = self._generate_archive_summary,
        )
        if archived:
            self.msg_queue.put({
                "sender": "📜 Chroniques",
                "text":   (
                    "Sessions anciennes archivées dans le journal long terme.\n"
                    "Les agents pourront y accéder par mots-clés lors des prochaines sessions."
                ),
                "color":  "#c8b8ff",
            })


# ── Helper interne ────────────────────────────────────────────────────────────

def _set_detail_text(widget: tk.Text, content: str):
    """Remplace le contenu d'un widget Text (thread Tk seulement)."""
    widget.config(state="normal")
    widget.delete("1.0", "end")
    widget.insert("end", content)
    widget.config(state="disabled")
    widget.see("1.0")