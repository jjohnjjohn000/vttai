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
from voice_interface import record_audio_and_transcribe, ptt_start, ptt_stop_and_transcribe
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

        # Polling continu de la géométrie (comme _track_window) :
        # sans ce polling, la position n'est sauvegardée qu'à la fermeture
        # manuelle — un crash entre-temps fait perdre tout déplacement/redimensionnement.
        def _poll_geom():
            try:
                if not win.winfo_exists():
                    return
                g = _get_win_geometry(win)
                if g:
                    self._win_state[_key] = g
                    _save_window_state(self._win_state)
                win.after(2000, _poll_geom)
            except Exception:
                pass
        win.after(2000, _poll_geom)

        # ── Données de classe depuis class_data.py ────────────────────────────
        # hit_die et max_slots sont dérivés de la classe D&D 5e.
        # con_mod et ac restent dans campaign_state.json (spécifiques au perso).
        from class_data import get_hit_die, get_spell_slots
        char_class = data.get("class", "fighter")
        level      = data.get("level", 1)
        try:
            hit_die   = get_hit_die(char_class)
            max_slots = get_spell_slots(char_class, level)
        except Exception:
            hit_die   = data.get("hit_die", 8)
            max_slots = {}
        con_mod   = data.get("con_mod",  0)
        ac        = data.get("ac",       10)

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

        # ── Bouton « Faire parler » ───────────────────────────────────────────
        speak_bar = tk.Frame(win, bg=char_bg)
        speak_bar.pack(fill=tk.X, padx=8, pady=(0, 6))

        speak_entry = tk.Entry(
            speak_bar, bg="#252535", fg="#666677",
            insertbackground="#cccccc", relief="flat",
            font=("Arial", 9),
        )
        speak_entry.insert(0, "Prends la parole...")

        def _on_entry_focus_in(e):
            if speak_entry.get() == "Prends la parole...":
                speak_entry.delete(0, tk.END)
                speak_entry.config(fg="#cccccc")

        def _on_entry_focus_out(e):
            if not speak_entry.get().strip():
                speak_entry.insert(0, "Prends la parole...")
                speak_entry.config(fg="#666677")

        speak_entry.bind("<FocusIn>",  _on_entry_focus_in)
        speak_entry.bind("<FocusOut>", _on_entry_focus_out)

        def _do_speak(event=None):
            hint = speak_entry.get().strip()
            if hint == "Prends la parole...":
                hint = ""
            if hint:
                msg = (
                    f"{char_name}, le MJ t'invite à prendre la parole sur ce sujet : {hint}. "
                    f"Exprime-toi en roleplay, en 1-3 phrases."
                )
            else:
                msg = (
                    f"{char_name}, prends la parole spontanément. "
                    f"Dis quelque chose d'intéressant en roleplay, en 1-3 phrases, "
                    f"en réagissant au contexte actuel."
                )
            # Injection dans le groupchat normal — AutoGen gère le contexte complet
            self.msg_queue.put({"sender": "Alexis_Le_MJ", "text": msg, "color": "#4CAF50"})
            self.user_input = msg
            self.input_event.set()
            speak_entry.delete(0, tk.END)
            speak_entry.insert(0, "Prends la parole...")
            speak_entry.config(fg="#666677")

        speak_entry.bind("<Return>", _do_speak)

        btn_speak = tk.Button(
            speak_bar, text="Parler",
            bg=color, fg="#0d0d0d",
            font=("Arial", 9, "bold"),
            relief="flat", cursor="hand2", padx=8, pady=3,
            command=_do_speak,
        )
        btn_speak.pack(side=tk.RIGHT)
        speak_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4, padx=(0, 4))

        # ── En-tête coloré ────────────────────────────────────────────────────
        hdr = tk.Frame(win, bg=color, pady=4)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text=char_name, bg=color, fg="#0d0d0d",
                 font=("Arial", 12, "bold")).pack(side=tk.LEFT, padx=14)
        def _get_actual_llm() -> str:
            """Retourne le modèle réellement utilisé par le moteur.
            Priorité :
              1. campaign_state (characters.<nom>.llm_session_override)  ← override UI de session
              2. campaign_state (characters.<nom>.llm)                   ← source de vérité
              3. app_config (agents.<nom>.model)                         ← fallback
            """
            try:
                from state_manager import load_state as _ls
                cs = _ls().get("characters", {}).get(char_name, {})
                override = cs.get("llm_session_override", "")
                if override:
                    return override
                cs_model = cs.get("llm", "")
                if cs_model:
                    return cs_model
            except Exception:
                pass
            try:
                from app_config import get_agent_config
                ac_model = get_agent_config(char_name).get("model", "")
                if ac_model:
                    return ac_model
            except Exception:
                pass
            return data.get("llm", "?")

        def _fmt_llm(model: str) -> str:
            return (model
                    .replace("gemini-", "G:")
                    .replace("groq/", "Q:")
                    .replace("openrouter/", "OR:")
                    .replace("deepseek/", "DS:"))

        llm_label = tk.Label(hdr, text=_fmt_llm(_get_actual_llm()), bg=color, fg="#333333",
                             font=("Consolas", 8), cursor="hand2",
                             relief="flat", padx=2)
        llm_label.pack(side=tk.RIGHT, padx=6)

        # ── Tooltip helper ────────────────────────────────────────────────────
        _tip_win = [None]
        def _show_tip(event=None):
            if _tip_win[0]:
                return
            tw = tk.Toplevel(win)
            tw.wm_overrideredirect(True)
            tw.attributes("-topmost", True)
            tw.geometry(f"+{event.x_root + 4}+{event.y_root + 16}")
            tk.Label(tw, text="Cliquer pour changer le modèle LLM",
                     bg="#333344", fg="#ccccff", font=("Arial", 8),
                     padx=6, pady=3, relief="solid", bd=1).pack()
            _tip_win[0] = tw
        def _hide_tip(event=None):
            if _tip_win[0]:
                try:
                    _tip_win[0].destroy()
                except Exception:
                    pass
                _tip_win[0] = None
        llm_label.bind("<Enter>", _show_tip)
        llm_label.bind("<Leave>", _hide_tip)

        def _apply_llm_override(new_model: str):
            """
            Applique le nouveau modèle pour cet agent :
              1. Sauvegarde llm_session_override dans campaign_state (clé séparée de 'llm').
              2. Met à jour l'agent AutoGen en mémoire (llm_config + client).
              3. Rafraîchit le label.
              4. Si un LLM tourne, l'interrompt et reprend avec le nouveau modèle.
            """
            _hide_tip()

            # ── 1. Persistance (clé séparée — ne touche pas à 'llm') ──────────
            try:
                from state_manager import load_state as _ls2, save_state as _ss2
                _s2 = _ls2()
                _s2.setdefault("characters", {}).setdefault(char_name, {})["llm_session_override"] = new_model
                _ss2(_s2)
            except Exception as _e1:
                print(f"[LLM Override] Erreur sauvegarde state pour {char_name}: {_e1}")

            # ── 2. Mise à jour de l'agent en mémoire ─────────────────────────
            try:
                import autogen as _ag2
                _agent = self._agents.get(char_name) if hasattr(self, "_agents") else None
                if _agent is not None:
                    from app_config import get_agent_config as _gac
                    from llm_config import build_llm_config as _blc
                    _temp = _gac(char_name).get("temperature", 0.7)
                    _new_cfg = _blc(new_model, temperature=_temp)
                    _agent.llm_config = _new_cfg
                    _agent.client = _ag2.OpenAIWrapper(
                        **{k: v for k, v in _new_cfg.items() if k != "functions"}
                    )
                    print(f"[LLM Override] {char_name} → {new_model} (agent mis à jour en mémoire)")
            except Exception as _e2:
                print(f"[LLM Override] Erreur mise à jour agent {char_name}: {_e2}")

            # ── 3. Rafraîchir le label ────────────────────────────────────────
            try:
                llm_label.config(text=_fmt_llm(new_model))
            except Exception:
                pass

            # ── 4. Interruption + reprise — seulement si c'est CET agent qui parle ──
            _is_running  = getattr(self, "_llm_running",    False)
            _is_waiting  = getattr(self, "_waiting_for_mj", True)
            # Vérifie que c'est bien char_name qui génère en ce moment.
            # AutoGen met à jour groupchat.last_speaker avant chaque génération,
            # donc last_speaker.name == char_name ↔ cet agent est actif.
            _gc = getattr(self, "groupchat", None)
            _last_speaker = getattr(_gc, "last_speaker", None)
            _is_this_agent_speaking = (
                _last_speaker is not None
                and getattr(_last_speaker, "name", None) == char_name
            )
            if _is_running and not _is_waiting and _is_this_agent_speaking:
                # Interruption silencieuse — le moteur reprend avec la continuation.
                # On précise que c'est le tour de cet agent et que sa réponse
                # précédente (encore en historique) est annulée : il doit
                # re-déclarer son tour complet avec tous les blocs [ACTION].
                self._pending_interrupt_input = (
                    f"[SYSTÈME — CONTINUATION — TOUR DE {char_name}] "
                    f"Le modèle LLM de {char_name} vient d'être remplacé en cours de génération. "
                    f"Sa réponse précédente est annulée et doit être ignorée. "
                    f"C'est toujours le tour de {char_name} : "
                    f"il doit re-déclarer son tour complet depuis le début, "
                    f"y compris tous les blocs [ACTION] requis."
                )
                self._pending_interrupt_display = None
                self._inject_stop()
                self.msg_queue.put({
                    "sender": "🔄 Système",
                    "text":   f"{char_name} : modèle basculé → {new_model}\nLLM interrompu — reprise en cours.",
                    "color":  "#aaaaff",
                })
            else:
                self.msg_queue.put({
                    "sender": "🔄 Système",
                    "text":   f"{char_name} : modèle basculé → {new_model}\n(Actif dès le prochain appel LLM.)",
                    "color":  "#aaaaff",
                })

        def _show_llm_dropdown(event=None):
            """Affiche un menu de sélection de modèle LLM."""
            _hide_tip()
            from app_config import KNOWN_MODELS as _KM
            current = _get_actual_llm()

            menu = tk.Menu(win, tearoff=0,
                           bg="#1a1a2e", fg="#e0e0ff",
                           activebackground="#3a3a6a", activeforeground="white",
                           font=("Consolas", 9))

            # Sections groupées par fournisseur
            _sections = [
                ("🌐 Gemini",      lambda m: not any(m.startswith(p) for p in ("groq/","openrouter/","deepseek/"))),
                ("🧠 DeepSeek",    lambda m: m.startswith("deepseek/")),
                ("⚡ Groq",        lambda m: m.startswith("groq/")),
                ("🔀 OpenRouter",  lambda m: m.startswith("openrouter/")),
            ]
            first_section = True
            for section_label, predicate in _sections:
                models_in_section = [m for m in _KM if predicate(m)]
                if not models_in_section:
                    continue
                if not first_section:
                    menu.add_separator()
                first_section = False
                menu.add_command(label=f"── {section_label} ──",
                                 state="disabled",
                                 foreground="#888899")
                for m in models_in_section:
                    prefix = "✓  " if m == current else "    "
                    short  = _fmt_llm(m)
                    display = f"{prefix}{short}"
                    # Pour les noms longs, afficher aussi le slug complet
                    if len(m) > 30:
                        display += f"  [{m.split('/')[-1][:28]}]"
                    menu.add_command(
                        label=display,
                        command=lambda _m=m: _apply_llm_override(_m),
                    )

            menu.post(event.x_root, event.y_root)

        llm_label.bind("<Button-1>", _show_llm_dropdown)

        # ── Barre d'onglets ───────────────────────────────────────────────────
        tabs_bar = tk.Frame(win, bg="#12121e")
        tabs_bar.pack(fill=tk.X)

        stats_frame  = tk.Frame(win, bg="#1e1e2e")
        spells_frame = tk.Frame(win, bg="#1e1e2e")
        class_frame  = tk.Frame(win, bg="#1e1e2e")

        def _show_tab(name):
            for f in (stats_frame, spells_frame, class_frame):
                f.pack_forget()
            for b in (btn_stats, btn_spells, btn_class):
                b.config(bg="#12121e", fg="#555566")
            if name == "stats":
                stats_frame.pack(fill=tk.BOTH, expand=True)
                btn_stats.config(bg=color, fg="#0d0d0d")
            elif name == "spells":
                spells_frame.pack(fill=tk.BOTH, expand=True)
                btn_spells.config(bg=color, fg="#0d0d0d")
            elif name == "class":
                class_frame.pack(fill=tk.BOTH, expand=True)
                btn_class.config(bg=color, fg="#0d0d0d")

        btn_stats  = tk.Button(tabs_bar, text="📊 Stats",  font=("Arial", 9, "bold"),
                               relief="flat", padx=10, pady=5, cursor="hand2",
                               command=lambda: _show_tab("stats"))
        btn_spells = tk.Button(tabs_bar, text="✨ Sorts",  font=("Arial", 9, "bold"),
                               relief="flat", padx=10, pady=5, cursor="hand2",
                               command=lambda: _show_tab("spells"))
        btn_class  = tk.Button(tabs_bar, text="⚔ Classe", font=("Arial", 9, "bold"),
                               relief="flat", padx=10, pady=5, cursor="hand2",
                               command=lambda: _show_tab("class"))
        btn_stats.pack(side=tk.LEFT, fill=tk.X, expand=True)
        btn_spells.pack(side=tk.LEFT, fill=tk.X, expand=True)
        btn_class.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # ════════════════════════════════════════════════════════════════════
        # ── ONGLET CLASSE ───────────────────────────────────────────────────
        # ════════════════════════════════════════════════════════════════════
        from class_data import (
            get_class_features, get_subclass_features, get_subclass_spells,
            get_proficiencies, get_caster_progression, get_hit_die, get_spell_slots,
            get_all_feature_details, get_feature_details,
        )

        cls_canvas = tk.Canvas(class_frame, bg="#1e1e2e", highlightthickness=0)
        cls_scroll = tk.Scrollbar(class_frame, orient="vertical", command=cls_canvas.yview)
        cls_inner  = tk.Frame(cls_canvas, bg="#1e1e2e")
        cls_canvas.create_window((0, 0), window=cls_inner, anchor="nw")
        cls_canvas.configure(yscrollcommand=cls_scroll.set)
        cls_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        cls_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cls_inner.bind("<Configure>", lambda e: cls_canvas.configure(scrollregion=cls_canvas.bbox("all")))
        # Mousewheel
        def _cls_mousewheel(e):
            cls_canvas.yview_scroll(int(-1*(e.delta or (1 if e.num == 4 else -1))*3), "units")
        cls_canvas.bind("<Button-4>", _cls_mousewheel)
        cls_canvas.bind("<Button-5>", _cls_mousewheel)
        cls_canvas.bind("<MouseWheel>", _cls_mousewheel)

        char_class    = data.get("class", "fighter")
        char_subclass = data.get("subclass", "")
        char_level    = data.get("level", 1)

        _cls_bg = "#1e1e2e"
        _sec_bg = "#252535"

        # ── Popup de détail de capacité ───────────────────────────────────
        def _open_feature_popup(feat_name, feat_data):
            """Ouvre une fenêtre indépendante avec la description complète."""
            popup = tk.Toplevel(win)
            popup.title(f"{feat_name} — {char_class.title()}")
            popup.geometry("520x480")
            popup.configure(bg="#1a1a2e")
            popup.attributes("-topmost", True)

            # Header
            phdr = tk.Frame(popup, bg=color, pady=6)
            phdr.pack(fill=tk.X)
            tk.Label(phdr, text=feat_name, bg=color, fg="#0d0d0d",
                     font=("Arial", 12, "bold")).pack(side=tk.LEFT, padx=12)
            _badges = []
            if feat_data.get("level"):
                _badges.append(f"Niv. {feat_data['level']}")
            if feat_data.get("source"):
                _badges.append(feat_data["source"])
            if feat_data.get("type") == "subclass":
                _badges.append(char_subclass)
            tk.Label(phdr, text=" | ".join(_badges), bg=color, fg="#333333",
                     font=("Consolas", 9)).pack(side=tk.RIGHT, padx=12)

            # Body — scrollable text
            txt_frame = tk.Frame(popup, bg="#1a1a2e")
            txt_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

            txt_scroll = tk.Scrollbar(txt_frame)
            txt_scroll.pack(side=tk.RIGHT, fill=tk.Y)

            txt_widget = tk.Text(
                txt_frame, wrap=tk.WORD, bg="#1a1a2e", fg="#cccccc",
                font=("Consolas", 10), padx=12, pady=8,
                relief="flat", highlightthickness=0,
                yscrollcommand=txt_scroll.set,
                state=tk.NORMAL, cursor="arrow",
            )
            txt_widget.pack(fill=tk.BOTH, expand=True)
            txt_scroll.config(command=txt_widget.yview)

            # Insérer texte avec formatting
            description = feat_data.get("text", "(Aucune description disponible)")
            lines = description.split("\n")
            txt_widget.tag_configure("heading", font=("Arial", 10, "bold"), foreground=color)
            txt_widget.tag_configure("body", font=("Consolas", 10), foreground="#cccccc")
            txt_widget.tag_configure("bullet", font=("Consolas", 10), foreground="#aabbcc")

            for line in lines:
                stripped = line.strip()
                if stripped.startswith("▸ "):
                    txt_widget.insert(tk.END, stripped + "\n", "heading")
                elif stripped.startswith("• "):
                    txt_widget.insert(tk.END, "  " + stripped + "\n", "bullet")
                else:
                    txt_widget.insert(tk.END, stripped + "\n\n", "body")

            txt_widget.config(state=tk.DISABLED)

            # Bouton fermer
            tk.Button(popup, text="Fermer", bg="#333344", fg="#cccccc",
                      font=("Arial", 9), relief="flat", padx=12, pady=4,
                      command=popup.destroy).pack(pady=(0, 8))

        # ── En-tête : Classe + Niveau + Sous-classe ──────────────────────────
        hdr_cls = tk.Frame(cls_inner, bg=color, pady=6)
        hdr_cls.pack(fill=tk.X, padx=8, pady=(8, 4))
        _cls_title = char_class.title()
        if char_subclass:
            _cls_title += f" — {char_subclass}"
        tk.Label(hdr_cls, text=f"⚔ {_cls_title}", bg=color, fg="#0d0d0d",
                 font=("Arial", 11, "bold")).pack(side=tk.LEFT, padx=8)
        tk.Label(hdr_cls, text=f"Niv. {char_level}", bg=color, fg="#333333",
                 font=("Consolas", 10, "bold")).pack(side=tk.RIGHT, padx=8)

        # ── Dé de vie & Caster Info ──────────────────────────────────────
        info_fr = tk.Frame(cls_inner, bg=_sec_bg, padx=8, pady=6)
        info_fr.pack(fill=tk.X, padx=8, pady=(4, 2))
        try:
            _hd = get_hit_die(char_class)
        except Exception:
            _hd = 8
        tk.Label(info_fr, text=f"🎲 Dé de vie : d{_hd}", bg=_sec_bg, fg="#cccccc",
                 font=("Arial", 10)).pack(anchor="w")
        _caster = get_caster_progression(char_class)
        if _caster:
            _caster_labels = {"full": "Lanceur complet", "1/2": "Demi-lanceur", "1/3": "Tiers-lanceur"}
            _caster_str = _caster_labels.get(_caster, _caster)
            try:
                _slots = get_spell_slots(char_class, char_level)
                _slots_str = " / ".join(str(v) for v in _slots.values()) if _slots else "—"
            except Exception:
                _slots_str = "?"
            tk.Label(info_fr, text=f"\u2728 {_caster_str} — Emplacements : {_slots_str}",
                     bg=_sec_bg, fg="#aabbdd", font=("Arial", 9)).pack(anchor="w", pady=(2, 0))
        else:
            tk.Label(info_fr, text="\u2694 Pas de sorts (classe martiale)",
                     bg=_sec_bg, fg="#666677", font=("Arial", 9, "italic")).pack(anchor="w", pady=(2, 0))

        # ── Maîtrises (Armor, Weapons, Saves) ─────────────────────────────
        try:
            _profs = get_proficiencies(char_class)
        except Exception:
            _profs = {"armor": [], "weapons": [], "saves": []}

        if any(_profs.values()):
            prof_sec = tk.Frame(cls_inner, bg=_sec_bg, padx=8, pady=6)
            prof_sec.pack(fill=tk.X, padx=8, pady=(2, 2))
            tk.Label(prof_sec, text="\U0001f6e1 Maîtrises", bg=_sec_bg, fg=color,
                     font=("Arial", 10, "bold")).pack(anchor="w")

            _save_names = {"str": "FOR", "dex": "DEX", "con": "CON",
                           "int": "INT", "wis": "SAG", "cha": "CHA"}
            _prof_items = [
                ("Armures",    ", ".join(a.title() for a in _profs.get("armor", []))),
                ("Armes",      ", ".join(w.title() for w in _profs.get("weapons", []))),
                ("Sauvegardes", ", ".join(_save_names.get(s, s.upper()) for s in _profs.get("saves", []))),
            ]
            for _lbl, _val in _prof_items:
                if _val:
                    _row = tk.Frame(prof_sec, bg=_sec_bg)
                    _row.pack(fill=tk.X, pady=1)
                    tk.Label(_row, text=f"  {_lbl} :", bg=_sec_bg, fg="#888899",
                             font=("Arial", 9), anchor="w").pack(side=tk.LEFT)
                    tk.Label(_row, text=_val, bg=_sec_bg, fg="#cccccc",
                             font=("Arial", 9), wraplength=220, justify=tk.LEFT).pack(side=tk.LEFT, padx=(4, 0))

        # ── Capacités (classe + sous-classe) — CLIQUABLES ─────────────────
        try:
            _all_feats = get_all_feature_details(char_class, char_subclass, char_level)
        except Exception:
            _all_feats = []

        if _all_feats:
            feat_sec = tk.Frame(cls_inner, bg=_sec_bg, padx=8, pady=6)
            feat_sec.pack(fill=tk.X, padx=8, pady=(2, 2))

            _n_class = sum(1 for f in _all_feats if f["type"] == "class")
            _n_sub   = sum(1 for f in _all_feats if f["type"] == "subclass")
            _title_parts = []
            if _n_class:
                _title_parts.append(f"{_n_class} classe")
            if _n_sub:
                _title_parts.append(f"{_n_sub} {char_subclass}")
            tk.Label(feat_sec, text=f"\u2b50 Capacités ({' + '.join(_title_parts)})",
                     bg=_sec_bg, fg=color, font=("Arial", 10, "bold")).pack(anchor="w")
            tk.Label(feat_sec, text="Cliquer pour voir les détails", bg=_sec_bg,
                     fg="#555566", font=("Arial", 8, "italic")).pack(anchor="w")

            _current_level = None
            for _idx, _feat in enumerate(_all_feats):
                # Séparateur de niveau
                if _feat["level"] != _current_level:
                    _current_level = _feat["level"]
                    _lvl_sep = tk.Frame(feat_sec, bg=_sec_bg)
                    _lvl_sep.pack(fill=tk.X, pady=(6, 2))
                    tk.Label(_lvl_sep, text=f"── Niveau {_current_level} ──",
                             bg=_sec_bg, fg="#555566",
                             font=("Consolas", 8)).pack(anchor="w")

                _is_sub = (_feat["type"] == "subclass")
                _feat_fg = "#ddbbaa" if _is_sub else "#ccddee"
                _feat_icon = "🔥" if _is_sub else "⭐"

                _fr = tk.Frame(feat_sec, bg=_sec_bg)
                _fr.pack(fill=tk.X, pady=1)
                _name_lbl = tk.Label(
                    _fr, text=f"  {_feat_icon}  {_feat['name']}",
                    bg=_sec_bg, fg=_feat_fg,
                    font=("Arial", 9), cursor="hand2",
                    anchor="w",
                )
                _name_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
                # Source badge
                tk.Label(_fr, text=_feat.get("source", ""),
                         bg=_sec_bg, fg="#444455",
                         font=("Consolas", 7)).pack(side=tk.RIGHT)

                # Bind click — closure capture
                def _on_click(e, fd=_feat):
                    _open_feature_popup(fd["name"], fd)
                _name_lbl.bind("<Button-1>", _on_click)

                # Hover effect
                def _on_enter(e, lbl=_name_lbl, fg=_feat_fg):
                    lbl.config(fg=color, font=("Arial", 9, "bold"))
                def _on_leave(e, lbl=_name_lbl, fg=_feat_fg):
                    lbl.config(fg=fg, font=("Arial", 9))
                _name_lbl.bind("<Enter>", _on_enter)
                _name_lbl.bind("<Leave>", _on_leave)

        # ── Sorts de domaine / serment ─────────────────────────────────
        if char_subclass:
            try:
                _dom_spells = get_subclass_spells(char_class, char_subclass, char_level)
            except Exception:
                _dom_spells = []
            if _dom_spells:
                dom_sec = tk.Frame(cls_inner, bg=_sec_bg, padx=8, pady=6)
                dom_sec.pack(fill=tk.X, padx=8, pady=(2, 2))
                _domain_label = "Sorts de Serment" if char_class == "paladin" else "Sorts de Domaine"
                tk.Label(dom_sec, text=f"\U0001f4d6 {_domain_label}",
                         bg=_sec_bg, fg=color, font=("Arial", 10, "bold")).pack(anchor="w")
                # Afficher en grille 2 par ligne
                _spell_row = None
                for i, _sp in enumerate(_dom_spells):
                    if i % 2 == 0:
                        _spell_row = tk.Frame(dom_sec, bg=_sec_bg)
                        _spell_row.pack(fill=tk.X, pady=1)
                    tk.Label(_spell_row, text=f"  \u2022 {_sp}", bg=_sec_bg, fg="#aaddbb",
                             font=("Arial", 9), anchor="w", width=22).pack(side=tk.LEFT)

        # ── Emplacements de sort (table complète) ───────────────────────
        if _caster:
            slots_sec = tk.Frame(cls_inner, bg=_sec_bg, padx=8, pady=6)
            slots_sec.pack(fill=tk.X, padx=8, pady=(2, 8))
            tk.Label(slots_sec, text="\U0001f4ca Table de progression",
                     bg=_sec_bg, fg=color, font=("Arial", 10, "bold")).pack(anchor="w")
            # Compact table : show spell slots at levels 1-20
            _tbl_hdr = tk.Frame(slots_sec, bg=_sec_bg)
            _tbl_hdr.pack(fill=tk.X, pady=(4, 2))
            tk.Label(_tbl_hdr, text="Niv", bg=_sec_bg, fg="#666677",
                     font=("Consolas", 8, "bold"), width=4, anchor="w").pack(side=tk.LEFT)
            for sp_lvl in range(1, 10):
                tk.Label(_tbl_hdr, text=str(sp_lvl), bg=_sec_bg, fg="#666677",
                         font=("Consolas", 8, "bold"), width=3, anchor="center").pack(side=tk.LEFT)

            for _clvl in range(1, 21):
                try:
                    _row_slots = get_spell_slots(char_class, _clvl)
                except Exception:
                    _row_slots = {}
                if not _row_slots and _clvl > 1:
                    continue  # Skip rows with no slots for non-caster early levels
                _tbl_row = tk.Frame(slots_sec, bg=_sec_bg)
                _tbl_row.pack(fill=tk.X)
                _is_current = (_clvl == char_level)
                _niv_fg = color if _is_current else "#888899"
                _niv_font = ("Consolas", 8, "bold") if _is_current else ("Consolas", 8)
                tk.Label(_tbl_row, text=f"{_clvl:>2}", bg=_sec_bg, fg=_niv_fg,
                         font=_niv_font, width=4, anchor="w").pack(side=tk.LEFT)
                for sp_lvl in range(1, 10):
                    val = _row_slots.get(str(sp_lvl), 0)
                    _val_fg = "#cccccc" if val > 0 else "#333344"
                    if _is_current and val > 0:
                        _val_fg = color
                    tk.Label(_tbl_row, text=str(val) if val > 0 else "\u2014", bg=_sec_bg, fg=_val_fg,
                             font=_niv_font, width=3, anchor="center").pack(side=tk.LEFT)

        # Bas de page spacer
        tk.Frame(cls_inner, bg=_cls_bg, height=20).pack(fill=tk.X)

        # ════════════════════════════════════════════════════════════════════
        # ── ONGLET STATS ────────────────────────────────────────────────────
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
                spx.pack(side=tk.RIGHT); spx.focus_set(); spx.selection_range(0, tk.END)

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
                # ── Mise à jour du label LLM si le modèle a changé ──────────────
                # (ex: fallback quota automatique, ou changement via dropdown)
                current_short = _fmt_llm(_get_actual_llm())
                if llm_label.cget("text") != current_short:
                    llm_label.config(text=current_short)
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

    # ─── Liaison clavier PTT ─────────────────────────────────────────────────

    def _ptt_apply_hotkey(self):
        """
        Lit la touche PTT depuis APP_CONFIG et la lie à root.
        Délie l'ancienne touche si elle a changé.
        Appelé au démarrage (setup_ui) et après chaque sauvegarde de config.
        """
        try:
            from app_config import get_ptt_config
            hotkey = get_ptt_config().get("hotkey", "F12").strip()
        except Exception:
            hotkey = "F12"

        old_hotkey = getattr(self, "_ptt_current_hotkey", None)

        # Délier l'ancienne touche si elle diffère
        if old_hotkey and old_hotkey != hotkey:
            try:
                self.root.unbind(f"<KeyPress-{old_hotkey}>")
                self.root.unbind(f"<KeyRelease-{old_hotkey}>")
            except Exception:
                pass

        # Lier la nouvelle touche (idempotent si inchangée)
        if hotkey:
            try:
                self.root.bind(f"<KeyPress-{hotkey}>",   lambda e: self._on_ptt_press())
                self.root.bind(f"<KeyRelease-{hotkey}>", lambda e: self._on_ptt_release())
                self._ptt_current_hotkey = hotkey
                print(f"[PTT] Touche liée : {hotkey}  (+ bouton souris 🎤 Parler)")
            except Exception as e:
                print(f"[PTT] Erreur bind '{hotkey}' : {e}")

    def _on_ptt_press(self):
        """Démarre l'enregistrement push-to-talk.
        Sur Linux/X11, la touche maintenue génère des KeyPress/KeyRelease répétés
        (~50 ms). On annule le KeyRelease en attente (debounce) pour ne jamais
        interrompre un enregistrement en cours."""
        # Annuler le release différé s'il n'a pas encore tiré (key-repeat X11)
        after_id = getattr(self, "_ptt_release_after_id", None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
            self._ptt_release_after_id = None

        if getattr(self, "_ptt_active", False):
            return  # enregistrement déjà en cours — ignorer le repeat

        if self.input_event.is_set():
            self.msg_queue.put({
                "sender": "Système",
                "text":   "⚠ Le moteur traite encore la réponse — patientez.",
                "color":  "#FF9800",
            })
            return

        self._ptt_active = True

        # Feedback visuel : bouton rouge pendant l'enregistrement
        try:
            self.btn_voice.config(bg="#c0392b", text="⏺ Enregistrement...")
        except Exception:
            pass

        self.msg_queue.put({
            "sender": "Système",
            "text":   "🎤 Enregistrement… (relâchez pour envoyer)",
            "color":  "#2196F3",
        })
        ptt_start()

    def _on_ptt_release(self):
        """Planifie l'arrêt PTT avec un délai de 120 ms (debounce X11 key-repeat).
        Si un nouveau KeyPress arrive avant le délai, le release est annulé."""
        if not getattr(self, "_ptt_active", False):
            return

        # Délai suffisant pour absorber la période de répétition X11 (~30 ms)
        # mais court enough pour ne pas se sentir
        self._ptt_release_after_id = self.root.after(120, self._do_ptt_stop)

    def _do_ptt_stop(self):
        """Exécute l'arrêt réel du PTT — appelé après le délai de debounce."""
        self._ptt_release_after_id = None
        if not getattr(self, "_ptt_active", False):
            return
        self._ptt_active = False

        # Restaurer l'apparence du bouton immédiatement (thread Tk)
        try:
            self.btn_voice.config(bg="#2196F3", text="🎤 Parler")
        except Exception:
            pass

        def _transcribe_and_send():
            texte = ptt_stop_and_transcribe()
            if not texte or texte.startswith("["):
                self.msg_queue.put({
                    "sender": "Système",
                    "text":   f"🎤 {texte or '[Aucun audio]'}",
                    "color":  "#FF9800",
                })
                return
            self.user_input = texte
            self.msg_queue.put({
                "sender": "Alexis_Le_MJ (Vocal)",
                "text":   texte,
                "color":  "#4CAF50",
            })
            self.input_event.set()

        threading.Thread(target=_transcribe_and_send, daemon=True, name="ptt-transcribe").start()

    # ─── Ancienne méthode (conservée pour compatibilité éventuelle) ──────────
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