"""
campaign_log_mixin.py — Intégration du journal chronologique dans DnDApp.

Fournit CampaignLogMixin à injecter dans DnDApp :
  - open_campaign_log_viewer  : fenêtre de consultation du journal archivé
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
from state_manager import load_state, save_state
from app_config    import get_chronicler_config


class CampaignLogMixin:
    """Mixin pour DnDApp — consultation et archivage du journal chronologique."""

    # ─── Ouverture du visualiseur ────────────────────────────────────────────

    def open_campaign_log_viewer(self):
        """Ouvre (ou ramène) la fenêtre de consultation du journal archivé."""
        if getattr(self, "_campaign_log_win", None):
            try:
                self._campaign_log_win.deiconify()
                self._campaign_log_win.lift()
                return
            except Exception:
                self._campaign_log_win = None

        win = tk.Toplevel(self.root)
        win.title("📜 Chroniques de la Campagne")
        win.configure(bg="#1a1a2e")
        self._campaign_log_win = win
        try:
            self._track_window("campaign_log_viewer", win)
        except Exception:
            win.geometry("900x680")

        self._build_campaign_log_viewer(win)

    def _build_campaign_log_viewer(self, win: tk.Toplevel):
        """Construit le contenu de la fenêtre journal."""
        BG     = "#1a1a2e"
        BG2    = "#16213e"
        ACCENT = "#c8b8ff"
        FG     = "#e8e8f0"
        GOLD   = "#FFD700"
        RED    = "#ff6b6b"

        # ── En-tête ───────────────────────────────────────────────────────────
        header = tk.Frame(win, bg=BG, pady=8)
        header.pack(fill="x", padx=10)

        tk.Label(
            header, text="📜  Chroniques de la Campagne",
            font=("Georgia", 15, "bold"), fg=GOLD, bg=BG,
        ).pack(side="left", padx=8)

        btn_frame = tk.Frame(header, bg=BG)
        btn_frame.pack(side="right", padx=8)

        tk.Button(
            btn_frame, text="[+] Archiver maintenant",
            bg="#2a2a4e", fg=ACCENT, font=("Consolas", 10),
            relief="flat", cursor="hand2",
            command=lambda: self._manual_archive_from_viewer(win),
        ).pack(side="left", padx=4)

        tk.Button(
            btn_frame, text="[R] Rafraîchir",
            bg="#2a2a4e", fg=FG, font=("Consolas", 10),
            relief="flat", cursor="hand2",
            command=lambda: self._refresh_campaign_log_viewer(win),
        ).pack(side="left", padx=4)

        # ── Stats compactes ───────────────────────────────────────────────────
        log   = get_campaign_log()
        stats = log.summary_stats()
        state = load_state()
        n_recent = len(state.get("session_logs", []))

        stats_bar = tk.Frame(win, bg=BG2, pady=4)
        stats_bar.pack(fill="x", padx=10, pady=(0, 6))
        stats_txt = (
            f"  {stats['count']} bloc(s) archivé(s)  ·  "
            f"{stats.get('total_chars', 0):,} chars  ·  "
            f"{len(stats.get('sessions_covered', []))} sessions archivées  ·  "
            f"{n_recent} session(s) récente(s) non-archivées"
        )
        tk.Label(stats_bar, text=stats_txt, fg="#aaaacc", bg=BG2,
                 font=("Consolas", 9)).pack(anchor="w", padx=8)

        # ── Panneau principal : liste gauche + détail droit ───────────────────
        main_pane = tk.PanedWindow(win, orient="horizontal", bg=BG,
                                   sashwidth=5, sashrelief="flat")
        main_pane.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # ── Liste des entrées ─────────────────────────────────────────────────
        list_frame = tk.Frame(main_pane, bg=BG2, width=260)
        main_pane.add(list_frame, minsize=220)

        tk.Label(list_frame, text="Entrées archivées",
                 font=("Consolas", 10, "bold"), fg=ACCENT, bg=BG2,
                 anchor="w").pack(fill="x", padx=8, pady=(6, 2))

        listbox_frame = tk.Frame(list_frame, bg=BG2)
        listbox_frame.pack(fill="both", expand=True)

        scrollbar_list = tk.Scrollbar(listbox_frame, bg=BG2)
        scrollbar_list.pack(side="right", fill="y")

        listbox = tk.Listbox(
            listbox_frame,
            bg=BG2, fg=FG, selectbackground="#3a3a6e",
            font=("Consolas", 10), relief="flat",
            yscrollcommand=scrollbar_list.set,
            activestyle="none", cursor="hand2",
        )
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar_list.config(command=listbox.yview)

        # ── Détail de l'entrée sélectionnée ──────────────────────────────────
        detail_frame = tk.Frame(main_pane, bg=BG)
        main_pane.add(detail_frame, minsize=400)

        detail_header = tk.Frame(detail_frame, bg=BG)
        detail_header.pack(fill="x")

        lbl_entry_title = tk.Label(
            detail_header, text="Sélectionne une entrée…",
            font=("Georgia", 12, "bold"), fg=GOLD, bg=BG, anchor="w",
        )
        lbl_entry_title.pack(side="left", padx=8, pady=4)

        lbl_entry_meta = tk.Label(
            detail_header, text="",
            font=("Consolas", 9), fg="#aaaacc", bg=BG, anchor="w",
        )
        lbl_entry_meta.pack(side="left", padx=4)

        # Zone texte
        txt_frame = tk.Frame(detail_frame, bg=BG)
        txt_frame.pack(fill="both", expand=True, padx=4, pady=4)

        scrollbar_txt = tk.Scrollbar(txt_frame)
        scrollbar_txt.pack(side="right", fill="y")

        detail_text = tk.Text(
            txt_frame, bg=BG2, fg=FG,
            font=("Georgia", 11), relief="flat",
            wrap="word", state="disabled",
            yscrollcommand=scrollbar_txt.set,
            padx=12, pady=8,
        )
        detail_text.pack(side="left", fill="both", expand=True)
        scrollbar_txt.config(command=detail_text.yview)

        # Zone mots-clés
        kw_frame = tk.Frame(detail_frame, bg=BG2, pady=4)
        kw_frame.pack(fill="x", padx=4, pady=(0, 4))
        lbl_keywords = tk.Label(
            kw_frame, text="", fg="#aaaacc", bg=BG2,
            font=("Consolas", 9), anchor="w", wraplength=580,
        )
        lbl_keywords.pack(anchor="w", padx=8)

        # ── Remplissage de la liste ───────────────────────────────────────────
        entries = log.entries
        entry_refs: list[dict] = []

        # Ajouter les sessions récentes non archivées (lecture seule)
        recent_logs = state.get("session_logs", [])
        if recent_logs:
            listbox.insert("end", "── Sessions récentes ──")
            listbox.itemconfig("end", fg="#666688")
            entry_refs.append(None)  # séparateur
            for slog in recent_logs:
                label = f"  Session {slog['session']}  ({slog.get('date','?')[:10]})"
                listbox.insert("end", label)
                entry_refs.append({"_type": "recent", "data": slog})

        if entries:
            listbox.insert("end", "── Archivées ──")
            listbox.itemconfig("end", fg="#666688")
            entry_refs.append(None)  # séparateur
            for e in entries:
                imp = "★" * e.get("importance", 2)
                label = f"  {imp} {e.get('label', e['id'])}"
                listbox.insert("end", label)
                entry_refs.append({"_type": "archived", "data": e})

        # ── Callback de sélection ─────────────────────────────────────────────
        def _on_select(event=None):
            sel = listbox.curselection()
            if not sel:
                return
            idx = sel[0]
            if idx >= len(entry_refs):
                return
            ref = entry_refs[idx]
            if ref is None:
                return  # séparateur

            if ref["_type"] == "recent":
                slog = ref["data"]
                lbl_entry_title.config(
                    text=f"Session {slog['session']} (non archivée)"
                )
                lbl_entry_meta.config(
                    text=f"  {slog.get('date','?')}"
                )
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

        # Stocker les refs pour le refresh
        win._listbox      = listbox
        win._entry_refs   = entry_refs
        win._detail_text  = detail_text
        win._lbl_title    = lbl_entry_title
        win._lbl_meta     = lbl_entry_meta
        win._lbl_keywords = lbl_keywords

    # ── Helpers UI ────────────────────────────────────────────────────────────

    def _refresh_campaign_log_viewer(self, win: tk.Toplevel):
        """Recharge le contenu de la fenêtre journal."""
        # Détruire les widgets enfants et reconstruire
        for w in win.winfo_children():
            w.destroy()
        get_campaign_log().reload()
        self._build_campaign_log_viewer(win)

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

        Appelé en background thread depuis auto_archive_if_needed.
        """
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

            # Construire le contenu de la transcription
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

            # Ajouter le contexte des archives déjà existantes
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
            return ""  # fallback : concaténation brute assurée par campaign_log.py

    # ─── Auto-archivage (appelé par session_mixin) ────────────────────────────

    def _auto_archive_old_sessions(self):
        """
        Vérifie si les session_logs dépassent la fenêtre glissante et archive
        les plus anciens. Doit être appelé depuis un thread non-UI (background).
        """
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
