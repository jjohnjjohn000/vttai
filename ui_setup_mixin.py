"""
ui_setup_mixin.py — UISetupMixin : construction de l'interface principale.

Contient : setup_ui, _build_char_cards, _hp_color, update_stats_panel.
"""

import tkinter as tk
from tkinter import scrolledtext

from state_manager import load_state, set_character_active, is_character_active
from npc_bestiary_panel import GroupNPCPanel


class _ChatEntryWrapper(tk.Text):
    """Wrapper pour utiliser tk.Text (multiligne) avec la même API que tk.Entry."""
    def get(self, start=None, end=None):
        if start is None and end is None:
            return super().get("1.0", "end-1c")
        return super().get(start, end)
        
    def delete(self, first, last=None):
        if first == 0 or first == "0": first = "1.0"
        if last == tk.END: last = "end"
        super().delete(first, last)
        
    def insert(self, index, chars, *args):
        if index == 0 or index == "0": index = "1.0"
        elif index == tk.END: index = "end"
        super().insert(index, chars, *args)

    def icursor(self, index):
        """Simule entry.icursor(index) pour positionner le curseur."""
        if index == tk.END or index == "end":
            self.mark_set(tk.INSERT, "end-1c")
        else:
            self.mark_set(tk.INSERT, f"1.0+{int(index)}c")
        self.see(tk.INSERT)

    def get_cursor_pos(self):
        """Renvoie la position du curseur sous forme d'entier (comme tk.Entry)."""
        return len(self.get("1.0", tk.INSERT))


class UISetupMixin:
    """Mixin pour DnDApp — construction de l'UI principale."""

    def setup_ui(self):
        # --- PANNEAU PRINCIPAL (Chat) ---
        chat_frame = tk.Frame(self.root, bg="#1e1e1e")
        chat_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.chat_display = scrolledtext.ScrolledText(
            chat_frame, wrap=tk.WORD, bg="#2d2d2d", fg="#e0e0e0",
            font=("Consolas", 11), state=tk.DISABLED
        )
        self.chat_display.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Index des messages pour édition/suppression
        self.messages_index =[]  # Liste de dicts: {id, sender, text, tag_start, tag_end}
        self.msg_counter = 0

        # Menu clic droit — FIX SEGFAULT : création paresseuse au premier clic droit
        self.context_menu = None
        self.chat_display.bind("<Button-3>", self.show_context_menu)  # Clic droit
        self.chat_display.bind("<Button-1>", self._on_chat_click)     # Clic gauche → /msg
        self.chat_display.bind("<Motion>",   self._on_chat_motion)    # Curseur main/flèche

        self.selected_msg_id = None  # ID du message ciblé par le menu

        # --- ZONE DE SAISIE ---
        input_frame = tk.Frame(chat_frame, bg="#1e1e1e")
        input_frame.pack(fill=tk.X)

        # ── Champ de texte multiligne pleine largeur ──
        self.entry = _ChatEntryWrapper(
            input_frame, bg="#3d3d3d", fg="white", 
            font=("Consolas", 12), insertbackground="white", 
            height=3, wrap=tk.WORD
        )
        self.entry.pack(side=tk.TOP, fill=tk.X, expand=True, pady=(0, 6))

        def _on_return(event):
            # Si Majuscule (Shift) n'est PAS enfoncée, on envoie le texte
            if not (event.state & 0x0001):
                self.send_text()
                return "break" # Empêche l'ajout du \n

        self.entry.bind("<Return>",   _on_return)
        self.entry.bind("<KP_Enter>", _on_return)

        # ── Historique des entrées (↑ / ↓) ──────────────────────────────────
        self._chat_history   =[]   # liste des messages envoyés
        self._chat_hist_idx  = -1   # -1 = pas en navigation
        self._chat_hist_draft = ""  # brouillon sauvegardé avant navigation
        self.entry.bind("<Up>",   self._on_hist_up)
        self.entry.bind("<Down>", self._on_hist_down)

        # ── Désactiver la navigation Tab quand l'entrée est focusée ─────────
        self.entry.bind("<Tab>",       self._on_tab_complete)
        self.entry.bind("<Shift-Tab>", self._on_tab_complete_back)
        self.entry.bind("<Escape>",    self._on_tab_cancel)

        # ── LIGNE DES BOUTONS (Sous le champ texte) ─────────────────────────
        buttons_frame = tk.Frame(input_frame, bg="#1e1e1e")
        buttons_frame.pack(side=tk.TOP, fill=tk.X)

        # ── Bouton "Parler en tant que" inline ──────────────────────────────
        # Affiche le PNJ actif (ou "MJ") et ouvre le même menu que le sélecteur
        # latéral — permet de changer de voix sans quitter la zone de saisie.
        self._inline_npc_var = tk.StringVar(value="MJ")

        def _show_inline_npc_menu():
            if self._npc_menu is None or not self._npc_menu.winfo_exists():
                self._npc_menu = tk.Menu(
                    self.root, tearoff=0,
                    bg="#3d2d4d", fg="white", font=("Consolas", 10),
                    activebackground="#5d3d7d", activeforeground="white",
                )
                self._rebuild_npc_menu()
            btn = self._inline_npc_btn
            self._npc_menu.tk_popup(
                btn.winfo_rootx(),
                btn.winfo_rooty() + btn.winfo_height(),
            )

        self._inline_npc_btn = tk.Button(
            buttons_frame,
            textvariable=self._inline_npc_var,
            bg="#3d2d4d", fg="#c77dff",
            font=("Consolas", 9, "bold"),
            activebackground="#5d3d7d", activeforeground="white",
            relief="flat", padx=6, pady=2,
            command=_show_inline_npc_menu,
        )
        self._inline_npc_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.tts_mj_enabled = tk.BooleanVar(value=False)
        self.chk_tts_mj = tk.Checkbutton(
            buttons_frame, text="🔊 Auto-TTS", variable=self.tts_mj_enabled,
            bg="#1e1e1e", fg="#aaaaaa", selectcolor="#2d2d2d",
            activebackground="#1e1e1e", activeforeground="white",
            font=("Arial", 9)
        )
        self.chk_tts_mj.pack(side=tk.LEFT, padx=(5, 5))

        btn_send = tk.Button(buttons_frame, text="Envoyer", bg="#4CAF50", fg="white", font=("Arial", 10, "bold"), command=self.send_text)
        btn_send.pack(side=tk.LEFT, padx=(0, 5))

        # ── Bouton Push-to-Talk (maintenir = enregistrer, relâcher = envoyer) ──
        # On stocke la référence dans self.btn_voice pour que _on_ptt_press /
        # _on_ptt_release puissent modifier son apparence en temps réel.
        self.btn_voice = tk.Button(
            buttons_frame, text="🎤 Parler",
            bg="#2196F3", fg="white",
            font=("Arial", 10, "bold"),
        )
        self.btn_voice.pack(side=tk.LEFT, padx=(0, 5))

        # ButtonPress  → début enregistrement
        # ButtonRelease → arrêt + transcription (dans un thread daemon)
        self.btn_voice.bind("<ButtonPress-1>",   lambda e: self._on_ptt_press())
        self.btn_voice.bind("<ButtonRelease-1>", lambda e: self._on_ptt_release())

        self.btn_stop = tk.Button(buttons_frame, text="⏹ Stop LLMs", bg="#880000", fg="white",
                                  font=("Arial", 10, "bold"), command=self.stop_llms, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT)

        self.btn_pause = tk.Button(
            buttons_frame, text="⏸ Pause",
            bg="#e67e22", fg="white",
            font=("Arial", 10, "bold"),
            command=self.toggle_session_pause,
        )
        self.btn_pause.pack(side=tk.LEFT, padx=(5, 0))

        # ── Contrôle de volume ───────────────────────────────────────────────
        self.build_volume_control(buttons_frame).pack(side=tk.LEFT, padx=(8, 0))

        tk.Button(
            buttons_frame, text="↑ Fenêtres",
            bg="#2a3a4a", fg="#aaccee",
            font=("Arial", 10, "bold"),
            relief="flat", padx=6,
            command=self.raise_all_windows,
        ).pack(side=tk.LEFT, padx=(5, 0))

        tk.Button(
            buttons_frame, text="🔍 Recherche",
            bg="#3a2a4a", fg="#ccaace",
            font=("Arial", 10, "bold"),
            relief="flat", padx=6,
            command=self.open_search_window,
        ).pack(side=tk.LEFT, padx=(5, 0))


        # --- PANNEAU LATÉRAL (Stats & Actions) ---
        stats_frame = tk.Frame(self.root, bg="#252526", width=250)
        stats_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=10, pady=10)

        # --- CONTENEUR BAS ---
        bottom_container = tk.Frame(stats_frame, bg="#252526")
        bottom_container.pack(side=tk.BOTTOM, fill=tk.X)

        # --- SECTION ÉTAT DU GROUPE (collapsible) ---
        _hdr_stats, _content_stats = self._make_collapsible_section(
            stats_frame, "[Stats] ÉTAT DU GROUPE", "sidebar_stats",
            title_fg="#ffcc00", title_bg="#1e1e2e", section_bg="#252526",
        )

        # --- FICHES PERSONNAGES COMPACTES (boutons → popout) ---
        self._char_card_frame = tk.Frame(_content_stats, bg="#252526")
        self._char_card_frame.pack(fill=tk.X, padx=6, pady=(4, 0))
        self._char_cards: dict = {}   # {name: {"frame", "hp_bar", "hp_label"}}
        self._build_char_cards()

        # --- PANNEAU PNJs DU GROUPE (bestiary) ---
        from window_state import _save_window_state
        self._group_npc_panel = GroupNPCPanel(
            parent_frame      = _content_stats,
            root              = self.root,
            win_state         = self._win_state,
            save_win_state_fn = lambda: _save_window_state(self._win_state),
            track_fn          = self._track_window,
            msg_queue         = self.msg_queue,
            audio_queue       = self.audio_queue,
            get_scene_fn      = lambda: __import__('state_manager').get_scene_prompt(),
        )

        # --- SECTION BOUTONS D'ACTION (collapsible) ---
        _hdr_actions, _content_actions = self._make_collapsible_section(
            bottom_container, "Actions", "sidebar_actions",
            title_fg="#aaaacc", title_bg="#1a1a2e", section_bg="#252526",
        )

        action_frame = tk.Frame(_content_actions, bg="#252526")
        action_frame.pack(fill=tk.X, pady=6, padx=10)

        tk.Button(action_frame, text="[Sac] Inventaire du Groupe", bg="#2a1e0a", fg="#f0c040",
                  font=("Arial", 10, "bold"), command=self.open_inventory_panel).pack(fill=tk.X, pady=3)

        tk.Button(action_frame, text="📜 Journal de Quêtes", bg="#1a3a5c", fg="#64b5f6",
                  font=("Arial", 10, "bold"), command=self.open_quest_journal).pack(fill=tk.X, pady=3)

        tk.Button(action_frame, text="📖 Chroniques & Mémoires", bg="#1e1a3a", fg="#c8b8ff",
                  font=("Arial", 10, "bold"), command=self.open_campaign_log_viewer).pack(fill=tk.X, pady=3)

        tk.Button(action_frame, text="🃏 Tirage Tarokka", bg="#4a1e3a", fg="#ffb8c6",
                  font=("Arial", 10, "bold"), command=self.open_tarokka_window).pack(fill=tk.X, pady=3)

        tk.Button(action_frame, text="🎲 Jet de Compétence", bg="#1a3a2a", fg="#81c784",
                  font=("Arial", 10, "bold"), command=self.open_skill_check_dialog).pack(fill=tk.X, pady=3)

        tk.Button(action_frame, text="⚔️ Tracker de Combat", bg="#3a0d0d", fg="#e74c3c",
                  font=("Arial", 10, "bold"), command=self.open_combat_tracker).pack(fill=tk.X, pady=3)

        tk.Button(action_frame, text="🗺️ Carte de Combat", bg="#0e1e2c", fg="#64b5f6",
                  font=("Arial", 10, "bold"), command=self.open_combat_map).pack(fill=tk.X, pady=3)

        tk.Button(action_frame, text="🎵 Mixer Audio", bg="#1a1a3a", fg="#c8b8ff",
                  font=("Arial", 10, "bold"), command=self.open_music_mixer).pack(fill=tk.X, pady=3)

        tk.Button(action_frame, text="⚡ Simulateur Rapide", bg="#1a1a3a", fg="#9b59b6",
                  font=("Arial", 10, "bold"),
                  command=lambda: __import__('combat_simulator').CombatSimulator(self.root, load_state, self.msg_queue, __import__('llm_config').get_default_llm_config(),
                                                  inject_to_agents_fn=lambda t: (
                                                      setattr(self, "user_input", t),
                                                      self.msg_queue.put({"sender": "⚡ Simulation", "text": t, "color": "#9b59b6"}),
                                                      self.input_event.set(),
                                                  ))
                  ).pack(fill=tk.X, pady=3)

        tk.Button(action_frame, text="💾 Sauvegarder", bg="#FF9800", fg="white",
                  font=("Arial", 10, "bold"), command=self.trigger_save).pack(fill=tk.X, pady=3)

        tk.Button(action_frame, text="⚙️ Configuration", bg="#2a2a3a", fg="#aaaacc",
                  font=("Arial", 10, "bold"), command=self.open_config_panel).pack(fill=tk.X, pady=3)

        tk.Button(action_frame, text="⚙️ Gérer les PNJs", bg="#4a3060", fg="#c77dff",
                  font=("Arial", 10, "bold"), command=self.open_npc_manager).pack(fill=tk.X, pady=3)



        tk.Frame(bottom_container, bg="#3a3a3a", height=1).pack(fill=tk.X, padx=8, pady=4)

        # --- SECTION LANCEUR DE DÉS (collapsible) ---
        _hdr_dice, _content_dice = self._make_collapsible_section(
            bottom_container, "[D] LANCEUR DE DÉS", "sidebar_dice",
            title_fg="#ce93d8", title_bg="#1a0e2e", section_bg="#252526",
        )

        dice_frame = tk.Frame(_content_dice, bg="#252526")
        dice_frame.pack(fill=tk.X, padx=10, pady=(4, 4))
                 
        self._dice_counters = {d: tk.IntVar(value=0) for d in [4, 6, 8, 10, 12, 20, 100]}
        
        dice_row1 = tk.Frame(dice_frame, bg="#252526")
        dice_row1.pack()
        dice_row2 = tk.Frame(dice_frame, bg="#252526")
        dice_row2.pack(pady=(2, 0))
        
        def build_dice_btn(parent, d):
            btn = tk.Button(parent, text=f"d{d}", bg="#3a2a4a", fg="white", font=("Consolas", 9, "bold"), width=3, relief="flat")
            def _cl(e, w=btn, val=d):
                v = self._dice_counters[val].get() + 1
                self._dice_counters[val].set(v)
                w.config(text=f"{v}d{val}", bg="#703080")
            def _cr(e, w=btn, val=d):
                v = max(0, self._dice_counters[val].get() - 1)
                self._dice_counters[val].set(v)
                w.config(text=f"{v}d{val}" if v>0 else f"d{val}", bg="#703080" if v>0 else "#3a2a4a")
            btn.bind("<Button-1>", _cl)
            btn.bind("<Button-3>", _cr)
            return btn
            
        build_dice_btn(dice_row1, 4).pack(side=tk.LEFT, padx=1)
        build_dice_btn(dice_row1, 6).pack(side=tk.LEFT, padx=1)
        build_dice_btn(dice_row1, 8).pack(side=tk.LEFT, padx=1)
        build_dice_btn(dice_row1, 10).pack(side=tk.LEFT, padx=1)
        
        build_dice_btn(dice_row2, 12).pack(side=tk.LEFT, padx=1)
        build_dice_btn(dice_row2, 20).pack(side=tk.LEFT, padx=1)
        build_dice_btn(dice_row2, 100).pack(side=tk.LEFT, padx=1)
        
        dice_actions = tk.Frame(dice_frame, bg="#252526")
        dice_actions.pack(pady=(4, 0))
        
        def _reset_all():
            for d in [4, 6, 8, 10, 12, 20, 100]:
                self._dice_counters[d].set(0)
            for w in dice_row1.winfo_children() + dice_row2.winfo_children():
                if isinstance(w, tk.Button):
                    txt = w.cget("text")
                    if "d" in txt:
                        base_d = txt.split("d")[-1]
                        w.config(text=f"d{base_d}", bg="#3a2a4a")
                        
        def _roll_dice():
            import random
            details, total = [], 0
            for d, var in self._dice_counters.items():
                c = var.get()
                if c > 0:
                    rolls = [random.randint(1, d) for _ in range(c)]
                    total += sum(rolls)
                    details.append(f"{c}d{d} {rolls}")
            if details:
                spc = getattr(self, "_npc_var", None)
                nom = "MJ"
                if spc:
                    val = spc.get()
                    if not val.startswith("— MJ"):
                        nom = val
                res_str = " + ".join(details) + f" = {total}"
                self.msg_queue.put({"sender": nom, "text": f"🎲 Jet : {res_str}", "color": "#ce93d8"})
                _reset_all()

        tk.Button(dice_actions, text="Lancer", bg="#4CAF50", fg="white", font=("Arial", 9, "bold"), relief="flat", command=_roll_dice, padx=6).pack(side=tk.LEFT, padx=2)
        tk.Button(dice_actions, text="Reset", bg="#F44336", fg="white", font=("Arial", 9, "bold"), relief="flat", command=_reset_all, padx=6).pack(side=tk.LEFT, padx=2)
        
        tk.Frame(bottom_container, bg="#3a3a3a", height=1).pack(fill=tk.X, padx=8, pady=4)

        # --- PANNEAU PNJ (CACHÉ) ---
        # Gardé non-packé pour conserver self._npc_var et self._npc_indicator 
        # utilisés par le backend/autres boutons sans les afficher sur l'UI principale.
        npc_frame = tk.Frame(bottom_container, bg="#252526")

        # Dropdown PNJ — FIX B : tk.Button + tk.Menu paresseux
        self._npc_var  = tk.StringVar(value="— MJ Normal —")
        self._npc_menu = None

        def _show_npc_menu():
            if self._npc_menu is None or not self._npc_menu.winfo_exists():
                self._npc_menu = tk.Menu(self.root, tearoff=0,
                                         bg="#3d2d4d", fg="white", font=("Consolas", 10),
                                         activebackground="#5d3d7d", activeforeground="white")
                self._rebuild_npc_menu()
            btn = self._npc_dropdown_btn
            if btn.winfo_exists():
                self._npc_menu.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height())

        self._npc_dropdown_btn = tk.Button(
            npc_frame, textvariable=self._npc_var,
            bg="#3d2d4d", fg="white", font=("Consolas", 10),
            activebackground="#5d3d7d", activeforeground="white",
            anchor="w", padx=8, relief="flat", command=_show_npc_menu)
        self._npc_dropdown_btn.pack(fill=tk.X, pady=2)

        self._npc_indicator = tk.Label(npc_frame, text="", bg="#252526",
                                        font=("Consolas", 9, "italic"))
        self._npc_indicator.pack(fill=tk.X)

        self._refresh_npc_dropdown()

        tk.Frame(bottom_container, bg="#3a3a3a", height=1).pack(fill=tk.X, padx=8, pady=4)

        # --- SECTION SCÈNE ACTIVE (collapsible) ---
        _hdr_scene, _content_scene = self._make_collapsible_section(
            bottom_container, "[Map] SCÈNE ACTIVE", "sidebar_scene",
            title_fg="#81c784", title_bg="#0d1a0d", section_bg="#1a2a1a",
        )
        # Add ✏️ and 📸 shortcut buttons directly into the collapsible header row
        tk.Button(_hdr_scene, text="[Img]", bg="#0d1a0d", fg="#64b5f6",
                  font=("Consolas", 8), relief="flat", padx=2,
                  command=self.open_location_image_popout).pack(side=tk.RIGHT, padx=1, pady=2)
        tk.Button(_hdr_scene, text="[Edit]", bg="#0d1a0d", fg="#81c784",
                  font=("Consolas", 8), relief="flat", padx=2,
                  command=self.open_scene_editor).pack(side=tk.RIGHT, padx=1, pady=2)

        scene_frame = tk.Frame(_content_scene, bg="#1a2a1a")
        scene_frame.pack(fill=tk.X, padx=0, pady=0)

        self._scene_lieu_label = tk.Label(scene_frame, text="...", bg="#1a2a1a", fg="#c8e6c9",
                                           font=("Consolas", 9, "bold"), anchor="w",
                                           wraplength=220, justify=tk.LEFT)
        self._scene_lieu_label.pack(fill=tk.X, padx=8, pady=(0, 1))

        self._scene_npcs_label = tk.Label(scene_frame, text="", bg="#1a2a1a", fg="#a5d6a7",
                                           font=("Consolas", 8, "italic"), anchor="w",
                                           wraplength=220, justify=tk.LEFT)
        self._scene_npcs_label.pack(fill=tk.X, padx=8, pady=(0, 4))

        self._refresh_scene_widget()

        # --- SECTION CALENDRIER (collapsible) ---
        _hdr_cal, _content_cal = self._make_collapsible_section(
            bottom_container, "[Cal] CALENDRIER", "sidebar_cal",
            title_fg="#9b8fc7", title_bg="#0d0d1a", section_bg="#0d0d1a",
        )
        # Add 🗓 popout button to the collapsible header
        tk.Button(_hdr_cal, text="[Cal]", bg="#0d0d1a", fg="#9b8fc7",
                  font=("Consolas", 8), relief="flat", padx=2,
                  command=self.open_calendar_popout).pack(side=tk.RIGHT, padx=1, pady=2)

        cal_frame = tk.Frame(_content_cal, bg="#0d0d1a")
        cal_frame.pack(fill=tk.X, padx=8, pady=(0, 6))

        self._cal_date_label = tk.Label(cal_frame, text="...", bg="#0d0d1a", fg="#c8b8ff",
                                        font=("Consolas", 9, "bold"), anchor="w",
                                        wraplength=220, justify=tk.LEFT)
        self._cal_date_label.pack(fill=tk.X, padx=8, pady=(0, 1))
        self._cal_moon_label = tk.Label(cal_frame, text="", bg="#0d0d1a", fg="#7a6a9a",
                                        font=("Consolas", 8, "italic"), anchor="w")
        self._cal_moon_label.pack(fill=tk.X, padx=8, pady=(0, 3))

        cal_btns = tk.Frame(cal_frame, bg="#0d0d1a")
        cal_btns.pack(fill=tk.X, padx=8, pady=(0, 5))
        tk.Button(cal_btns, text="+1 jour", bg="#1a1a2e", fg="#9b8fc7",
                  font=("Arial", 8, "bold"), relief="flat", padx=6, pady=2,
                  command=lambda: self._advance_calendar(1)).pack(side=tk.LEFT, padx=(0, 3))
        tk.Button(cal_btns, text="+7 jours", bg="#1a1a2e", fg="#9b8fc7",
                  font=("Arial", 8, "bold"), relief="flat", padx=6, pady=2,
                  command=lambda: self._advance_calendar(7)).pack(side=tk.LEFT)

        self._refresh_calendar_widget()

        # ── Liaison Push-to-Talk clavier ──────────────────────────────────────
        # Doit être appelé après la création de root pour que root.bind fonctionne.
        self.root.after(200, self._ptt_apply_hotkey)

    # ─── Fiches personnages (sidebar) ─────────────────────────────────────────

    def _build_char_cards(self):
        """Construit les 4 cartes compactes dans la sidebar. Appelé une seule fois."""
        state = load_state()
        for name, data in state.get("characters", {}).items():
            color   = self.CHAR_COLORS.get(name, "#aaaaaa")
            active  = data.get("active", True)

            card = tk.Frame(self._char_card_frame, bg="#1e2030", relief="flat",
                            cursor="hand2", padx=4, pady=3)
            card.pack(fill=tk.X, pady=2)

            top_row = tk.Frame(card, bg="#1e2030")
            top_row.pack(fill=tk.X)

            name_lbl = tk.Label(top_row, text=name, bg="#1e2030", fg=color,
                                font=("Arial", 9, "bold"), anchor="w")
            name_lbl.pack(side=tk.LEFT)

            # Badge 🚫 affiché uniquement si inactif
            badge_lbl = tk.Label(top_row, text="[Absent]", bg="#1e2030", fg="#666666",
                                 font=("Consolas", 7, "italic"), anchor="e")
            if not active:
                badge_lbl.pack(side=tk.RIGHT, padx=(0, 2))

            hp_lbl = tk.Label(top_row, text=f"{data['hp']}/{data['max_hp']}",
                              bg="#1e2030", fg="#aaaaaa", font=("Consolas", 8), anchor="e")
            hp_lbl.pack(side=tk.RIGHT)

            bar_bg = tk.Frame(card, bg="#3a3a3a", height=5)
            bar_bg.pack(fill=tk.X, pady=(1, 0))
            pct = max(0, min(1, data["hp"] / data["max_hp"])) if data["max_hp"] else 0
            bar_color = self._hp_color(pct) if active else "#444444"
            bar_fill = tk.Frame(bar_bg, bg=bar_color, height=5)
            bar_fill.place(relx=0, rely=0, relwidth=pct if active else 1.0, relheight=1)

            # Griser la carte si inactive
            if not active:
                card.config(bg="#181820")
                top_row.config(bg="#181820")
                name_lbl.config(fg="#555566", bg="#181820")
                hp_lbl.config(fg="#555566", bg="#181820")
                badge_lbl.config(bg="#181820")
                bar_bg.config(bg="#2a2a2a")
                bar_fill.config(bg="#444444")

            # Clic gauche → fiche popout
            for widget in (card, top_row, name_lbl, hp_lbl, bar_bg):
                widget.bind("<Button-1>", lambda e, n=name: self.open_char_popout(n))

            # Clic droit → menu contextuel
            for widget in (card, top_row, name_lbl, hp_lbl, bar_bg, badge_lbl):
                widget.bind("<Button-3>", lambda e, n=name: self._show_char_context_menu(e, n))

            self._char_cards[name] = {
                "card": card, "top_row": top_row,
                "name_lbl": name_lbl, "hp_lbl": hp_lbl,
                "badge_lbl": badge_lbl,
                "bar_bg": bar_bg, "bar_fill": bar_fill,
            }

    def _show_char_context_menu(self, event, char_name: str):
        """Affiche le menu contextuel clic-droit sur une fiche personnage."""
        active = is_character_active(char_name)
        menu = tk.Menu(self.root, tearoff=0,
                       bg="#2a2a3a", fg="white", font=("Consolas", 10),
                       activebackground="#4a4a6a", activeforeground="white")

        if active:
            menu.add_command(
                label=f"[Retirer de la scene]  {char_name}",
                command=lambda: self._toggle_char_active(char_name, False),
            )
        else:
            menu.add_command(
                label=f"[Ajouter a la scene]  {char_name}",
                command=lambda: self._toggle_char_active(char_name, True),
            )

        menu.add_separator()
        menu.add_command(
            label="Ouvrir la fiche",
            command=lambda: self.open_char_popout(char_name),
        )

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _toggle_char_active(self, char_name: str, active: bool):
        """Active ou désactive un personnage et rafraîchit la carte."""
        set_character_active(char_name, active)
        self._refresh_char_card(char_name)
        status = "entre dans la scene" if active else "quitte la scene"
        self.msg_queue.put({
            "sender": "⚙ Scene",
            "text":   f"{char_name} {status}.",
            "color":  "#888899",
        })

        # ── Synchroniser groupchat.agents + prompts avec la nouvelle présence ──
        # CRITIQUE : groupchat.agents est figé au démarrage de session. Sans cette
        # synchronisation, un personnage ajouté mid-session n'est jamais retourné
        # par _eligible_agents() (qui filtre depuis groupchat.agents), donc personne
        # ne parle. Un personnage retiré et réintégré doit aussi être traité.
        self._sync_groupchat_agents()
        if self._agents:
            self._rebuild_agent_prompts()

    def _sync_groupchat_agents(self):
        """Synchronise groupchat.agents avec la liste de présence actuelle.

        Cas couverts :
          - Personnage ajouté mid-session (absent au démarrage → absent de groupchat.agents)
          - Personnage retiré mid-session (doit disparaître de _eligible_agents)
          - Personnage réintégré après retrait (déjà dans groupchat.agents, re-éligible
            dès que get_active_characters() le retourne à nouveau — pas besoin de re-add)

        IMPORTANT : AutoGen peut maintenir PLUSIEURS références au GroupChat
        (self.groupchat, manager._groupchat, reply_func_list[*].config).
        On doit mettre à jour TOUTES ces références pour éviter une divergence.
        """
        if not self.groupchat or not self._agents:
            return

        from state_manager import get_active_characters as _get_active
        _ALL_PLAYERS =["Kaelen", "Elara", "Thorne", "Lyra"]
        active_names = set(_get_active())

        # ── Collecter TOUTES les références au GroupChat ──────────────────
        _gc_refs = set()
        _gc_refs.add(id(self.groupchat))
        _all_gcs = [self.groupchat]

        # Référence interne du manager
        _mgr = getattr(self, "_groupchat_manager", None)
        if _mgr is not None:
            _mgr_gc = getattr(_mgr, "_groupchat", None)
            if _mgr_gc is not None and id(_mgr_gc) not in _gc_refs:
                _gc_refs.add(id(_mgr_gc))
                _all_gcs.append(_mgr_gc)
            # Configs stockées dans _reply_func_list
            for _rft in getattr(_mgr, "_reply_func_list", []):
                _cfg = _rft.get("config")
                if _cfg is not None and hasattr(_cfg, "agents") and id(_cfg) not in _gc_refs:
                    _gc_refs.add(id(_cfg))
                    _all_gcs.append(_cfg)

        # ── Utiliser le PREMIER gc (le live) comme référence pour non_players ──
        _live_gc = _all_gcs[0] if _all_gcs else self.groupchat

        # Garder les agents non-joueurs (MJ, manager…) intacts
        non_players =[a for a in _live_gc.agents if a.name not in _ALL_PLAYERS]

        # Conserver les noms des joueurs actuellement dans le groupchat pour comparaison
        old_active_names = {a.name for a in _live_gc.agents if a.name in _ALL_PLAYERS}

        # Reconstruire la liste des joueurs depuis self._agents (source de vérité)
        # en respectant l'ordre canonique
        players = [
            self._agents[name]
            for name in _ALL_PLAYERS
            if name in active_names and name in self._agents
        ]

        new_players = [a.name for a in players if a.name not in old_active_names]
        removed_players = [name for name in old_active_names if name not in active_names]

        _new_agents = non_players + players

        # ── Mettre à jour TOUTES les références ──────────────────────────
        for _gc in _all_gcs:
            _gc.agents = _new_agents
        # self.groupchat aussi (peut avoir été écarté du set si même id)
        self.groupchat.agents = _new_agents

        print(f"[SYNC DEBUG] Updated {len(_all_gcs)} gc refs. agents={[a.name for a in _new_agents]}")

        # Synchroniser les messages d'Entrée en Scène pour forcer le speaker selector
        # Utiliser le live_gc pour les messages (c'est celui que run_chat lit)
        _msg_gc = _all_gcs[-1] if _all_gcs else self.groupchat
        if _msg_gc.messages is not None:
            if removed_players:
                _msg_gc.messages[:] = [
                    m for m in _msg_gc.messages
                    if not (
                        m.get("name") == "Alexis_Le_MJ"
                        and any(f"[ENTRÉE EN SCÈNE] {rn}" in str(m.get("content", "")) for rn in removed_players)
                    )
                ]
            for np in new_players:
                _msg_gc.messages.append({
                    "role":    "user",
                    "name":    "Alexis_Le_MJ",
                    "content": (
                        f"[ENTRÉE EN SCÈNE] {np} rejoint le groupe. "
                        f"{np}, quelle est ta première réaction ?"
                    ),
                })

        active_str = ", ".join(a.name for a in players) if players else "aucun"
        self.msg_queue.put({
            "sender": "⚙ Groupchat",
            "text":   f"Agents actifs mis à jour : {active_str}",
            "color":  "#556677",
        })

    def _refresh_char_card(self, char_name: str):
        """Rafraîchit visuellement une carte personnage après changement d'état active."""
        card_widgets = self._char_cards.get(char_name)
        if not card_widgets:
            return

        state   = load_state()
        data    = state.get("characters", {}).get(char_name, {})
        active  = data.get("active", True)
        color   = self.CHAR_COLORS.get(char_name, "#aaaaaa")
        hp, max_hp = data.get("hp", 0), data.get("max_hp", 1)
        pct = max(0, min(1, hp / max_hp)) if max_hp else 0

        if active:
            card_widgets["card"].config(bg="#1e2030")
            card_widgets["top_row"].config(bg="#1e2030")
            card_widgets["name_lbl"].config(fg=color, bg="#1e2030")
            card_widgets["hp_lbl"].config(fg="#aaaaaa", bg="#1e2030")
            card_widgets["badge_lbl"].config(bg="#1e2030")
            card_widgets["badge_lbl"].pack_forget()
            card_widgets["bar_bg"].config(bg="#3a3a3a")
            card_widgets["bar_fill"].config(bg=self._hp_color(pct))
            card_widgets["bar_fill"].place(relwidth=pct)
        else:
            card_widgets["card"].config(bg="#181820")
            card_widgets["top_row"].config(bg="#181820")
            card_widgets["name_lbl"].config(fg="#555566", bg="#181820")
            card_widgets["hp_lbl"].config(fg="#555566", bg="#181820")
            card_widgets["badge_lbl"].config(bg="#181820")
            card_widgets["badge_lbl"].pack(side=tk.RIGHT, padx=(0, 2))
            card_widgets["bar_bg"].config(bg="#2a2a2a")
            card_widgets["bar_fill"].config(bg="#444444")
            card_widgets["bar_fill"].place(relwidth=1.0)

    def _append_tool_confirm_link(self, char_name: str, tool_name: str,
                                   tool_args: dict, callback):
        """
        Insère dans chat_display un lien cliquable pour confirmer un appel d'outil.
        Le clic MJ débloque le thread AutoGen via callback().
        Si le MJ ne clique pas dans le délai, AutoGen auto-confirme côté engine.
        """
        # ── Formatage du libellé ─────────────────────────────────────────────
        _LABELS = {
            "roll_dice":               "🎲 Jet de dés",
            "use_spell_slot":          "🔮 Slot de sort",
            "update_hp":               "❤️ Mise à jour PV",
            "add_temp_hp":             "🛡 PV temporaires",
            "add_item_to_inventory":   "🎒 Ajout inventaire",
            "remove_item_from_inventory": "🗑 Retrait inventaire",
            "update_currency":         "💰 Mise à jour monnaie",
        }
        label = _LABELS.get(tool_name, f"⚙ {tool_name}")

        args_parts =[]
        for k, v in (tool_args or {}).items():
            if k == "character_name":
                continue          # redondant avec char_name
            args_parts.append(f"{k}={v}")
        args_str = "  " + "  ".join(args_parts) if args_parts else ""

        link_text = f"✨ {char_name} — {label}{args_str}   ▶ Confirmer"

        # ── Tag unique par callback ──────────────────────────────────────────
        tag = f"tool_confirm_{id(callback)}"
        color = self.CHAR_COLORS.get(char_name, "#FFD700")

        # ── Insertion dans le widget (thread-safe via root.after) ────────────
        def _insert():
            self.chat_display.config(state=tk.NORMAL)
            self.chat_display.insert(tk.END, link_text + "\n", (tag, "tool_confirm_link"))
            self.chat_display.config(state=tk.DISABLED)
            self.chat_display.see(tk.END)

            # Style du lien
            self.chat_display.tag_config(
                tag,
                foreground=color,
                underline=True,
                font=("Consolas", 11, "bold"),
            )
            self.chat_display.tag_raise(tag)

            # ── Clic → confirme et grisse le lien ───────────────────────────
            def _on_click(event, _cb=callback, _t=tag, _lt=link_text):
                self.chat_display.config(state=tk.NORMAL)
                # Remplacer le texte cliquable par "✓ Confirmé"
                idx_start = self.chat_display.tag_ranges(_t)
                if idx_start:
                    self.chat_display.delete(idx_start[0], idx_start[1])
                    done = _lt.replace("▶ Confirmer", "✓ Confirmé")
                    self.chat_display.insert(idx_start[0], done + "\n", ("tool_done",))
                self.chat_display.config(state=tk.DISABLED)
                self.chat_display.tag_unbind(_t, "<Button-1>")
                _cb()

            self.chat_display.tag_bind(tag, "<Button-1>", _on_click)
            self.chat_display.tag_bind(
                tag, "<Enter>", lambda e: self.chat_display.config(cursor="hand2"))
            self.chat_display.tag_bind(
                tag, "<Leave>", lambda e: self.chat_display.config(cursor=""))

            # Style "confirmé" (grisé, non souligné)
            self.chat_display.tag_config(
                "tool_done", foreground="#555566", underline=False,
                font=("Consolas", 10, "italic"),
            )

        self.root.after(0, _insert)

    @staticmethod
    def _hp_color(pct: float) -> str:
        if pct > 0.5: return "#4CAF50"
        if pct > 0.25: return "#FF9800"
        return "#F44336"

    def update_stats_panel(self):
        """Mise à jour légère des cartes compactes (HP + barre uniquement)."""
        try:
            state = load_state()
            for name, data in state.get("characters", {}).items():
                card_widgets = self._char_cards.get(name)
                if not card_widgets:
                    continue
                hp, max_hp = data["hp"], data["max_hp"]
                active = data.get("active", True)
                pct = max(0, min(1, hp / max_hp)) if max_hp else 0
                card_widgets["hp_lbl"].config(text=f"{hp}/{max_hp}")
                if active:
                    card_widgets["bar_fill"].config(bg=self._hp_color(pct))
                    card_widgets["bar_fill"].place(relwidth=pct)
                # Si inactif, la barre reste grisée (gérée par _refresh_char_card)
        except Exception as e:
            print(f"[update_stats_panel] Erreur : {e}")
        self.root.after(2000, self.update_stats_panel)

    def open_tarokka_window(self):
        """Ouvre la fenêtre indépendante de tirage du Tarokka."""
        from tarokka_window import TarokkaWindow
        if getattr(self, "_tarokka_win", None) and self._tarokka_win.top.winfo_exists():
            self._tarokka_win.top.lift()
        else:
            self._tarokka_win = TarokkaWindow(self.root, self.msg_queue)
            if hasattr(self, "_track_window"):
                self._track_window("tarokka", self._tarokka_win.top)