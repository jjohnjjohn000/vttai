"""
config_panel.py — Panneau de configuration de l'application.

Fenêtre Toplevel à onglets couvrant :
  • Agents joueurs  : modèle LLM + température par personnage
  • Chroniqueur     : modèle, température, mémoires, system prompt
  • GroupChat       : max_round, allow_repeat_speaker
  • Mémoires        : seuils de détection contextuelle
  • Voix & UI       : TTS, rafraîchissement

Usage :
    from config_panel import open_config_panel
    open_config_panel(root, win_state, track_fn, on_saved_callback)
"""

import tkinter as tk
from tkinter import scrolledtext, ttk

from app_config import (
    APP_CONFIG, DEFAULTS, KNOWN_MODELS,
    load_app_config, save_app_config, reload_app_config,
    get_ptt_config,
)
from piper_tts import KNOWN_PIPER_VOICES, piper_available

# ─── Palette cohérente avec l'app principale ──────────────────────────────────
BG       = "#0d1117"
BG2      = "#161b22"
BG3      = "#1e2430"
ACCENT   = "#58a6ff"
GOLD     = "#f0b429"
GREEN    = "#4CAF50"
RED      = "#F44336"
PURPLE   = "#c77dff"
FG       = "#e0e0e0"
FG_DIM   = "#8b949e"
FONT     = ("Consolas", 10)
FONT_LBL = ("Arial", 9, "bold")

CHAR_COLORS = {
    "Kaelen": "#64b5f6",
    "Elara":  "#ce93d8",
    "Thorne": "#ff8a65",
    "Lyra":   "#80cbc4",
}


def _section(parent, title, color=ACCENT):
    """Crée un label de section avec séparateur."""
    f = tk.Frame(parent, bg=BG)
    f.pack(fill=tk.X, padx=0, pady=(14, 2))
    tk.Frame(f, bg=color, height=1).pack(fill=tk.X)
    tk.Label(f, text=f"  {title}", bg=BG, fg=color,
             font=("Arial", 9, "bold")).pack(anchor="w", pady=(3, 0))
    return f


def _row(parent, label, widget_fn, **kw):
    """Ligne label + widget côte à côte."""
    r = tk.Frame(parent, bg=BG)
    r.pack(fill=tk.X, padx=20, pady=3)
    tk.Label(r, text=label, bg=BG, fg=FG_DIM, font=FONT_LBL,
             width=28, anchor="w").pack(side=tk.LEFT)
    w = widget_fn(r, **kw)
    w.pack(side=tk.LEFT, fill=tk.X, expand=True)
    return w


def _model_dropdown(parent, var):
    """Dropdown de sélection de modèle avec entrée libre."""
    frame = tk.Frame(parent, bg=BG)
    menu = tk.OptionMenu(frame, var, *KNOWN_MODELS)
    menu.config(bg=BG2, fg=FG, activebackground=BG3, activeforeground=ACCENT,
                highlightthickness=0, relief="flat", font=FONT, width=38)
    menu["menu"].config(bg=BG2, fg=FG, activebackground=BG3,
                        activeforeground=ACCENT, font=FONT)
    menu.pack(side=tk.LEFT)
    # Champ libre pour saisir un modèle hors liste
    entry = tk.Entry(frame, textvariable=var, bg=BG2, fg=ACCENT,
                     font=FONT, insertbackground=ACCENT, relief="flat",
                     width=42)
    entry.pack(side=tk.LEFT, padx=(6, 0), ipady=3)
    return frame


def _temp_slider(parent, var):
    """Slider 0.0 → 2.0 pour la température."""
    frame = tk.Frame(parent, bg=BG)
    scale = tk.Scale(frame, variable=var, from_=0.0, to=2.0, resolution=0.05,
                     orient=tk.HORIZONTAL, bg=BG, fg=FG, troughcolor=BG2,
                     activebackground=ACCENT, highlightthickness=0,
                     length=220, showvalue=True, font=("Consolas", 8))
    scale.pack(side=tk.LEFT)
    return frame


def _int_slider(parent, var, from_, to, label_suffix=""):
    frame = tk.Frame(parent, bg=BG)
    scale = tk.Scale(frame, variable=var, from_=from_, to=to, resolution=1,
                     orient=tk.HORIZONTAL, bg=BG, fg=FG, troughcolor=BG2,
                     activebackground=ACCENT, highlightthickness=0,
                     length=220, showvalue=True, font=("Consolas", 8))
    scale.pack(side=tk.LEFT)
    if label_suffix:
        tk.Label(frame, text=label_suffix, bg=BG, fg=FG_DIM,
                 font=("Consolas", 8)).pack(side=tk.LEFT, padx=4)
    return frame


def _checkbox(parent, var, label):
    cb = tk.Checkbutton(parent, variable=var, text=label,
                        bg=BG, fg=FG, selectcolor=BG2,
                        activebackground=BG, activeforeground=ACCENT,
                        font=FONT)
    cb.pack(anchor="w", padx=20, pady=2)
    return cb


# ─── Onglets ──────────────────────────────────────────────────────────────────

def _tab_agents(nb, cfg, vars_):
    """Onglet : LLM des agents joueurs."""
    tab = tk.Frame(nb, bg=BG)

    header = tk.Frame(tab, bg="#0a1520", pady=8)
    header.pack(fill=tk.X)
    tk.Label(header, text="🧙 Modèles LLM — Agents Joueurs",
             bg="#0a1520", fg=ACCENT, font=("Arial", 11, "bold")).pack(side=tk.LEFT, padx=16)
    tk.Label(header,
             text="Modifié en temps réel si la session est active · Redémarrer pour appliquer au GroupChatManager",
             bg="#0a1520", fg=FG_DIM, font=("Arial", 8)).pack(side=tk.RIGHT, padx=16)

    scroll_frame = tk.Frame(tab, bg=BG)
    scroll_frame.pack(fill=tk.BOTH, expand=True, pady=8)

    for char in ["Kaelen", "Elara", "Thorne", "Lyra"]:
        color = CHAR_COLORS.get(char, FG)
        char_cfg = cfg.get("agents", {}).get(char, DEFAULTS["agents"][char])

        _section(scroll_frame, f"⚔  {char}", color)

        # Modèle
        m_var = tk.StringVar(value=char_cfg.get("model", DEFAULTS["agents"][char]["model"]))
        _row(scroll_frame, "Modèle LLM", _model_dropdown, var=m_var)

        # Température
        t_var = tk.DoubleVar(value=char_cfg.get("temperature", 0.7))
        _row(scroll_frame, "Température (créativité)", _temp_slider, var=t_var)

        vars_["agents"][char] = {"model": m_var, "temperature": t_var}

    # ── Combat LLM ────────────────────────────────────────────────────────────
    _section(scroll_frame, "⚔  Mode Combat — LLM partagé par tous les PJ", RED)

    tk.Label(scroll_frame,
             text=("  En combat, tous les PJ sont basculés vers ce modèle unique\n"
                   "  (plus rapide et moins cher). Restauré automatiquement en fin de combat."),
             bg=BG, fg=FG_DIM, font=("Consolas", 8), justify=tk.LEFT,
             ).pack(anchor="w", padx=20, pady=(0, 4))

    combat_cfg = cfg.get("combat", DEFAULTS.get("combat", {}))
    combat_model_var = tk.StringVar(
        value=combat_cfg.get("model", "gemini-3.1-flash-lite-preview")
    )
    _row(scroll_frame, "Modèle LLM Combat", _model_dropdown, var=combat_model_var)

    vars_["combat"] = {"model": combat_model_var}

    return tab


def _tab_chronicler(nb, cfg, vars_):
    """Onglet : paramètres du Chroniqueur."""
    tab = tk.Frame(nb, bg=BG)

    header = tk.Frame(tab, bg="#0a1520", pady=8)
    header.pack(fill=tk.X)
    tk.Label(header, text="📜 Chroniqueur IA",
             bg="#0a1520", fg=GOLD, font=("Arial", 11, "bold")).pack(side=tk.LEFT, padx=16)
    tk.Label(header, text="Résumé de session & fusion de l'historique",
             bg="#0a1520", fg=FG_DIM, font=("Arial", 8)).pack(side=tk.RIGHT, padx=16)

    chron = cfg.get("chronicler", DEFAULTS["chronicler"])

    _section(tab, "Modèle & génération", GOLD)

    m_var = tk.StringVar(value=chron.get("model", DEFAULTS["chronicler"]["model"]))
    _row(tab, "Modèle LLM", _model_dropdown, var=m_var)

    t_var = tk.DoubleVar(value=chron.get("temperature", 0.3))
    _row(tab, "Température", _temp_slider, var=t_var)

    _section(tab, "Mémoires injectées", GOLD)

    imp_var = tk.IntVar(value=chron.get("memories_importance", 1))
    _row(tab, "Importance min. des mémoires", _int_slider,
         var=imp_var, from_=1, to=3,
         label_suffix="  1=Toutes  2=Notables+  3=Critiques seul.")

    _section(tab, "System prompt du Chroniqueur", GOLD)

    prompt_frame = tk.Frame(tab, bg=BG)
    prompt_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(4, 10))
    prompt_box = scrolledtext.ScrolledText(
        prompt_frame, height=10, bg=BG2, fg=FG,
        font=("Consolas", 9), insertbackground=ACCENT, relief="flat", wrap=tk.WORD)
    prompt_box.pack(fill=tk.BOTH, expand=True)
    prompt_box.insert("1.0", chron.get("system_prompt", DEFAULTS["chronicler"]["system_prompt"]))

    vars_["chronicler"] = {
        "model":              m_var,
        "temperature":        t_var,
        "memories_importance": imp_var,
        "system_prompt_box":  prompt_box,
    }
    return tab


def _tab_groupchat(nb, cfg, vars_):
    """Onglet : paramètres du GroupChat AutoGen."""
    tab = tk.Frame(nb, bg=BG)

    header = tk.Frame(tab, bg="#0a1520", pady=8)
    header.pack(fill=tk.X)
    tk.Label(header, text="⚙️  GroupChat AutoGen",
             bg="#0a1520", fg=PURPLE, font=("Arial", 11, "bold")).pack(side=tk.LEFT, padx=16)
    tk.Label(header, text="Paramètres du moteur de conversation multi-agents",
             bg="#0a1520", fg=FG_DIM, font=("Arial", 8)).pack(side=tk.RIGHT, padx=16)

    gc = cfg.get("groupchat", DEFAULTS["groupchat"])

    _section(tab, "Limites de session", PURPLE)

    mr_var = tk.IntVar(value=gc.get("max_round", 100))
    _row(tab, "Rounds max par session", _int_slider,
         var=mr_var, from_=10, to=500,
         label_suffix="  (100 ≈ ~30 minutes de jeu)")

    _section(tab, "Comportement", PURPLE)

    rep_var = tk.BooleanVar(value=gc.get("allow_repeat_speaker", False))
    _checkbox(tab, rep_var, "Autoriser le même agent à parler deux fois de suite")

    _section(tab, "Modèle du GroupChatManager", PURPLE)

    tk.Label(tab, text="  Le GroupChatManager utilise le modèle du Chroniqueur (onglet précédent).",
             bg=BG, fg=FG_DIM, font=("Consolas", 9, "italic")).pack(anchor="w", padx=20)

    vars_["groupchat"] = {
        "max_round":            mr_var,
        "allow_repeat_speaker": rep_var,
    }
    return tab


def _tab_memories(nb, cfg, vars_):
    """Onglet : paramètres du système de mémoires."""
    tab = tk.Frame(nb, bg=BG)

    header = tk.Frame(tab, bg="#0a1520", pady=8)
    header.pack(fill=tk.X)
    tk.Label(header, text="🧠 Mémoires",
             bg="#0a1520", fg="#80cbc4", font=("Arial", 11, "bold")).pack(side=tk.LEFT, padx=16)
    tk.Label(header, text="Injection permanente + détection contextuelle dynamique",
             bg="#0a1520", fg=FG_DIM, font=("Arial", 8)).pack(side=tk.RIGHT, padx=16)

    mem = cfg.get("memories", DEFAULTS["memories"])

    _section(tab, "Bloc permanent (injecté à chaque message)", "#80cbc4")
    tk.Label(tab,
             text="  Injecté dans le system_message de chaque agent au démarrage et à chaque tour.",
             bg=BG, fg=FG_DIM, font=("Consolas", 9, "italic")).pack(anchor="w", padx=20)

    ci_var = tk.IntVar(value=mem.get("compact_importance_min", 2))
    _row(tab, "Importance min. (bloc compact)", _int_slider,
         var=ci_var, from_=1, to=3,
         label_suffix="  1=Tout  2=Notable+  3=Critique seul.")

    _section(tab, "Détection contextuelle dynamique", "#80cbc4")
    tk.Label(tab,
             text="  Activée à chaque message : scanne le texte pour trouver des mémoires mentionnées.",
             bg=BG, fg=FG_DIM, font=("Consolas", 9, "italic")).pack(anchor="w", padx=20)

    tl_var = tk.IntVar(value=mem.get("contextual_tag_min_length", 4))
    _row(tab, "Longueur min. des tags", _int_slider,
         var=tl_var, from_=2, to=10,
         label_suffix="  (4 recommandé — évite les faux positifs)")

    vars_["memories"] = {
        "compact_importance_min":    ci_var,
        "contextual_tag_min_length": tl_var,
    }
    return tab


def _scan_installed_voices(models_dir: str) -> list[str]:
    """
    Scanne models_dir pour les fichiers .onnx installés.
    Retourne une liste triée : d'abord les installés, ensuite les connus non installés
    (préfixés '↓ ' pour indiquer qu'ils seraient téléchargeables).
    Si le dossier est vide ou absent, retourne uniquement KNOWN_PIPER_VOICES.
    """
    import os
    installed = []
    try:
        if os.path.isdir(models_dir):
            installed = sorted(
                os.path.splitext(f)[0]
                for f in os.listdir(models_dir)
                if f.endswith(".onnx")
            )
    except Exception:
        pass

    known_not_installed = [
        f"↓ {v}" for v in KNOWN_PIPER_VOICES if v not in installed
    ]
    return installed + known_not_installed if (installed or known_not_installed) else list(KNOWN_PIPER_VOICES)


def _piper_voice_dropdown(parent, var, voices: list[str]):
    """Dropdown Piper avec liste dynamique + entrée libre."""
    frame = tk.Frame(parent, bg=BG2)
    frame.pack(side=tk.LEFT, fill=tk.X, expand=True)  # ← rattache le frame au parent (row)
    options = voices if voices else ["(aucun modèle installé)"]
    menu = tk.OptionMenu(frame, var, *options)
    menu.config(bg=BG3, fg=FG, activebackground=BG, activeforeground=ACCENT,
                highlightthickness=0, relief="flat", font=FONT, width=26)
    menu["menu"].config(bg=BG3, fg=FG, activebackground=BG,
                        activeforeground=ACCENT, font=FONT)
    menu.pack(side=tk.LEFT)
    entry = tk.Entry(frame, textvariable=var, bg=BG3, fg=ACCENT,
                     font=FONT, insertbackground=ACCENT, relief="flat", width=28)
    entry.pack(side=tk.LEFT, padx=(6, 0), ipady=3)
    return frame, menu


def _tab_voice_ui(nb, cfg, vars_):
    """Onglet : voix TTS (edge-tts en ligne / Piper local) et paramètres UI."""
    tab = tk.Frame(nb, bg=BG)

    # ── En-tête ──────────────────────────────────────────────────────────────
    header = tk.Frame(tab, bg="#0a1520", pady=8)
    header.pack(fill=tk.X)
    tk.Label(header, text="Voix & Interface",
             bg="#0a1520", fg=GREEN, font=("Arial", 11, "bold")).pack(side=tk.LEFT, padx=16)
    tk.Label(header, text="edge-tts (en ligne) ou Piper TTS (local, hors-ligne)",
             bg="#0a1520", fg=FG_DIM, font=("Arial", 8)).pack(side=tk.RIGHT, padx=16)

    voice  = cfg.get("voice",  DEFAULTS["voice"])
    piper  = cfg.get("piper",  DEFAULTS["piper"])
    ui_cfg = cfg.get("ui",     DEFAULTS["ui"])

    # ── Activer/désactiver TTS global ────────────────────────────────────────
    _section(tab, "Synthèse vocale (TTS)", GREEN)
    v_var = tk.BooleanVar(value=voice.get("enabled", True))
    _checkbox(tab, v_var, "Activer la synthèse vocale")

    # ── Sélecteur de backend ─────────────────────────────────────────────────
    _section(tab, "Backend TTS", GREEN)

    backend_var = tk.StringVar(value=voice.get("backend", "edge-tts"))

    backend_frame = tk.Frame(tab, bg=BG)
    backend_frame.pack(fill=tk.X, padx=20, pady=(4, 0))

    # Panneaux conditionnels
    edgetts_panel = tk.Frame(tab, bg=BG2, relief="flat", bd=0)
    piper_panel   = tk.Frame(tab, bg=BG2, relief="flat", bd=0)

    def _update_panels(*_):
        if backend_var.get() == "piper":
            edgetts_panel.pack_forget()
            piper_panel.pack(fill=tk.X, padx=20, pady=(0, 8))
        else:
            piper_panel.pack_forget()
            edgetts_panel.pack(fill=tk.X, padx=20, pady=(0, 8))

    for label, value in [
        (" En ligne  —  edge-tts (Microsoft Neural, qualité haute, fr-CA disponible)", "edge-tts"),
        (" Local     —  Piper TTS (ONNX, hors-ligne, fr_FR, ~60-80 Mo par voix)",      "piper"),
    ]:
        tk.Radiobutton(
            backend_frame, text=label, variable=backend_var, value=value,
            bg=BG, fg=FG, selectcolor=BG2, activebackground=BG,
            activeforeground=ACCENT, font=FONT,
            command=_update_panels,
        ).pack(anchor="w", pady=1)

    # ── Panneau edge-tts ──────────────────────────────────────────────────────
    tk.Label(edgetts_panel,
             text=(
                 "  Voix actives :\n"
                 "    Kaelen → fr-FR-HenriNeural\n"
                 "    Elara  → fr-FR-DeniseNeural\n"
                 "    Thorne → fr-CA-AntoineNeural  ← accent québécois\n"
                 "    Lyra   → fr-FR-EloiseNeural\n\n"
                 "  Modifier dans voice_interface.py > VOICE_MAPPING"
             ),
             bg=BG2, fg=FG_DIM, font=("Consolas", 8), justify=tk.LEFT,
             ).pack(anchor="w", padx=12, pady=8)

    # ── Panneau Piper ─────────────────────────────────────────────────────────
    piper_voices_cfg = piper.get("voices", DEFAULTS["piper"]["voices"])

    # ── Dossier modèles + bouton Rafraîchir ───────────────────────────────────
    pdir_frame = tk.Frame(piper_panel, bg=BG2)
    pdir_frame.pack(fill=tk.X, padx=8, pady=(8, 4))
    tk.Label(pdir_frame, text="Dossier modèles (.onnx) :", bg=BG2, fg=FG_DIM,
             font=FONT_LBL, width=22, anchor="w").pack(side=tk.LEFT)
    pdir_var = tk.StringVar(value=piper.get("models_dir", "piper_models"))
    tk.Entry(pdir_frame, textvariable=pdir_var, bg=BG3, fg=ACCENT,
             font=FONT, insertbackground=ACCENT, relief="flat", width=26,
             ).pack(side=tk.LEFT, padx=(4, 4), ipady=3)

    # Compteur de modèles installés
    models_count_var = tk.StringVar(value="")
    tk.Label(pdir_frame, textvariable=models_count_var, bg=BG2, fg=FG_DIM,
             font=("Consolas", 8)).pack(side=tk.LEFT, padx=(0, 6))

    # ── Voix par personnage (dans un sous-frame à position fixe) ─────────────
    tk.Label(piper_panel, text="  Voix par personnage :", bg=BG2, fg=FG_DIM,
             font=FONT_LBL).pack(anchor="w", padx=8, pady=(6, 0))

    # Conteneur dédié — position fixe dans piper_panel, toujours AVANT warning et check
    chars_container = tk.Frame(piper_panel, bg=BG2)
    chars_container.pack(fill=tk.X, padx=0, pady=0)

    piper_voice_vars: dict[str, tk.StringVar]  = {}
    piper_pitch_vars: dict[str, tk.DoubleVar]  = {}
    _row_frames:      dict[str, tk.Frame]      = {}

    CHARS = ["Kaelen", "Elara", "Thorne", "Lyra"]

    # Textes de test personnalisés par personnage
    _PREVIEW_TEXTS = {
        "Kaelen": "Je suis Kaelen. Mon épée est prête, et mon cœur ne tremble pas.",
        "Elara":  "Je suis Elara. La magie coule en moi comme une rivière de lumière.",
        "Thorne": "Moi, c'est Thorne. J'ai survécu à pire, croyez-moi.",
        "Lyra":   "Je suis Lyra. Les dieux me guident, et leur lumière éclaire mon chemin.",
    }

    def _preview_voice(char: str, voice_var: tk.StringVar, btn: tk.Button):
        """Lance un aperçu TTS Piper pour le personnage dans un thread daemon."""
        import threading
        voice_id = voice_var.get().strip()
        if not voice_id or voice_id.startswith("↓") or voice_id == "(aucun modèle installé)":
            return
        models_dir   = pdir_var.get().strip() or "piper_models"
        pitch        = piper_pitch_vars.get(char, tk.DoubleVar(value=0.0)).get()
        text         = _PREVIEW_TEXTS.get(char, f"Bonjour, je suis {char}.")

        def _run():
            try:
                btn.config(state="disabled", text="…")
            except Exception:
                pass
            try:
                from piper_tts import play_piper_voice
                play_piper_voice(text, char, voice_id, models_dir,
                                 pitch_semitones=pitch)
            except Exception as e:
                print(f"[Preview TTS] Erreur {char} : {e}")
            finally:
                try:
                    btn.config(state="normal", text="▶")
                except Exception:
                    pass

        threading.Thread(target=_run, daemon=True, name=f"piper-preview-{char}").start()

    # Config pitch sauvegardée
    _pitch_cfg = piper.get("pitch", DEFAULTS["piper"]["pitch"])

    def _rebuild_dropdowns(voices: list[str]):
        """Reconstruit les OptionMenu dans chars_container (position fixe)."""
        for char in CHARS:
            old_frame = _row_frames.pop(char, None)
            if old_frame:
                old_frame.destroy()

        for char in CHARS:
            color = CHAR_COLORS.get(char, FG)

            # ── Ligne principale : nom + dropdown + bouton aperçu ─────────────
            row = tk.Frame(chars_container, bg=BG2)
            row.pack(fill=tk.X, padx=12, pady=(4, 0))
            _row_frames[char] = row

            tk.Label(row, text=char, bg=BG2, fg=color,
                     font=("Arial", 9, "bold"), width=8, anchor="w").pack(side=tk.LEFT)

            pv = piper_voice_vars.get(char)
            if pv is None:
                default_v = piper_voices_cfg.get(
                    char, DEFAULTS["piper"]["voices"].get(char, "fr_FR-upmc-medium"))
                pv = tk.StringVar(value=default_v)
                piper_voice_vars[char] = pv

            _, _menu = _piper_voice_dropdown(row, pv, voices)

            # Bouton aperçu
            _btn_preview = tk.Button(
                row, text="▶",
                bg=BG3, fg=GREEN, font=("Arial", 9, "bold"),
                relief="flat", padx=6, pady=1, cursor="hand2",
            )
            _btn_preview.config(
                command=lambda c=char, v=pv, b=_btn_preview: _preview_voice(c, v, b)
            )
            _btn_preview.pack(side=tk.LEFT, padx=(8, 0))

            # ── Ligne pitch : slider + valeur ─────────────────────────────────
            pitch_row = tk.Frame(chars_container, bg=BG2)
            pitch_row.pack(fill=tk.X, padx=12, pady=(0, 4))

            tk.Label(pitch_row, text="", bg=BG2, width=8).pack(side=tk.LEFT)  # indent
            tk.Label(pitch_row, text="Pitch", bg=BG2, fg=FG_DIM,
                     font=("Consolas", 8), width=5, anchor="w").pack(side=tk.LEFT)

            ppv = piper_pitch_vars.get(char)
            if ppv is None:
                default_p = float(_pitch_cfg.get(char, _pitch_cfg.get("default", 0.0)))
                ppv = tk.DoubleVar(value=default_p)
                piper_pitch_vars[char] = ppv

            pitch_val_label = tk.Label(pitch_row, text=f"{ppv.get():+.1f} st",
                                       bg=BG2, fg=ACCENT, font=("Consolas", 8), width=7)

            def _on_pitch_change(val, lbl=pitch_val_label, var=ppv):
                lbl.config(text=f"{float(val):+.1f} st")

            scale = tk.Scale(
                pitch_row, variable=ppv,
                from_=-8.0, to=8.0, resolution=0.5,
                orient=tk.HORIZONTAL, bg=BG2, fg=FG, troughcolor=BG3,
                activebackground=ACCENT, highlightthickness=0,
                length=200, showvalue=False, font=("Consolas", 8),
                command=_on_pitch_change,
            )
            scale.pack(side=tk.LEFT, padx=(4, 4))
            pitch_val_label.pack(side=tk.LEFT)

            tk.Label(pitch_row, text="-8  grave  ←  0  →  aigu  +8",
                     bg=BG2, fg=FG_DIM, font=("Consolas", 7)).pack(side=tk.LEFT, padx=(6, 0))

    def _refresh_voices(*_):
        """Scanne le dossier et reconstruit tous les menus."""
        models_dir = pdir_var.get().strip() or "piper_models"
        voices = _scan_installed_voices(models_dir)
        n_installed = sum(1 for v in voices if not v.startswith("↓"))
        models_count_var.set(
            f"({n_installed} installé{'s' if n_installed > 1 else ''})"
            if n_installed else "(aucun installé)"
        )
        _rebuild_dropdowns(voices)

    # Bouton rafraîchir dans la ligne dossier
    tk.Button(pdir_frame, text="↺ Scanner",
              bg=BG3, fg=ACCENT, font=("Arial", 8), relief="flat", padx=8,
              command=_refresh_voices).pack(side=tk.LEFT)

    # Premier rendu initial
    _rebuild_dropdowns(_scan_installed_voices(piper.get("models_dir", "piper_models")))

    # Mise à jour automatique du compteur dès l'ouverture
    _models_dir_init = piper.get("models_dir", "piper_models")
    n = sum(1 for v in _scan_installed_voices(_models_dir_init) if not v.startswith("↓"))
    models_count_var.set(f"({n} installé{'s' if n > 1 else ''})" if n else "(aucun installé)")

    # ── Avertissement fr-CA ───────────────────────────────────────────────────
    warning_frame = tk.Frame(piper_panel, bg=BG2)
    warning_frame.pack(fill=tk.X, padx=12, pady=(4, 2))
    tk.Label(warning_frame,
             text=(
                 "  ↓ = disponible au téléchargement (pas encore installé)\n"
                 "  Aucun modèle Piper fr-CA officiel — fr_FR-upmc-medium recommandé.\n"
                 "  Pour l'accent québécois de Thorne : backend edge-tts (fr-CA-AntoineNeural)."
             ),
             bg=BG2, fg="#f0b429", font=("Consolas", 8), justify=tk.LEFT,
             ).pack(anchor="w")

    # ── Bouton vérification Piper ──────────────────────────────────────────────
    status_piper = tk.StringVar(value="")
    check_row  = tk.Frame(piper_panel, bg=BG2)
    check_row.pack(fill=tk.X, padx=8, pady=(4, 8))

    def _check_piper_install():
        import importlib
        importlib.invalidate_caches()
        ok = piper_available()
        if ok:
            status_piper.set("✓ piper-tts est installé")
        else:
            status_piper.set("✗ Non installé — lancer : pip install piper-tts")

    tk.Button(check_row, text="Vérifier installation Piper",
              bg=BG3, fg=FG, font=("Arial", 9), relief="flat", padx=10,
              command=_check_piper_install).pack(side=tk.LEFT)
    tk.Label(check_row, textvariable=status_piper, bg=BG2, fg=ACCENT,
             font=("Consolas", 8)).pack(side=tk.LEFT, padx=10)

    # Affichage initial correct
    _update_panels()

    # ── Push-to-Talk ─────────────────────────────────────────────────────────
    _section(tab, "Push-to-Talk (PTT)", ACCENT)

    ptt_cfg = cfg.get("ptt", DEFAULTS.get("ptt", {"hotkey": "F12"}))
    ptt_hotkey_var = tk.StringVar(value=ptt_cfg.get("hotkey", "F12"))

    ptt_row = tk.Frame(tab, bg=BG)
    ptt_row.pack(fill=tk.X, padx=20, pady=6)

    tk.Label(ptt_row, text="Touche PTT", bg=BG, fg=FG_DIM,
             font=FONT_LBL, width=28, anchor="w").pack(side=tk.LEFT)

    # Affichage de la touche courante
    ptt_display = tk.Label(
        ptt_row,
        textvariable=ptt_hotkey_var,
        bg=BG2, fg=ACCENT,
        font=("Consolas", 11, "bold"),
        width=12, anchor="center",
        relief="flat", pady=4,
    )
    ptt_display.pack(side=tk.LEFT, padx=(0, 8))

    ptt_status_var = tk.StringVar(value="")
    ptt_status_lbl = tk.Label(ptt_row, textvariable=ptt_status_var,
                               bg=BG, fg=GOLD, font=("Consolas", 9))
    ptt_status_lbl.pack(side=tk.LEFT, padx=4)

    _ptt_capture_active = [False]   # flag mutable dans la closure

    def _start_ptt_capture():
        """Entre en mode capture : le prochain appui de touche devient le nouveau hotkey."""
        if _ptt_capture_active[0]:
            return
        _ptt_capture_active[0] = True
        ptt_status_var.set("Appuyez sur la touche souhaitée…")
        ptt_display.config(bg="#1a2a1a", fg=GOLD)
        btn_capture.config(state=tk.DISABLED)

        def _on_key_capture(event):
            keysym = event.keysym
            # Ignorer les touches mortes / modificateurs seuls
            if keysym in ("Shift_L", "Shift_R", "Control_L", "Control_R",
                          "Alt_L", "Alt_R", "Super_L", "Super_R", "Meta_L",
                          "Meta_R", "Caps_Lock", "Num_Lock", "Scroll_Lock"):
                return "break"
            ptt_hotkey_var.set(keysym)
            ptt_display.config(bg=BG2, fg=ACCENT)
            ptt_status_var.set(f"✓ Touche capturée : {keysym}")
            tab.after(2000, lambda: ptt_status_var.set(""))
            tab.unbind("<KeyPress>")
            _ptt_capture_active[0] = False
            btn_capture.config(state=tk.NORMAL)
            return "break"

        tab.focus_set()
        tab.bind("<KeyPress>", _on_key_capture)

    btn_capture = tk.Button(
        ptt_row, text="Changer",
        bg=BG3, fg=ACCENT,
        font=("Arial", 9), relief="flat", padx=10,
        command=_start_ptt_capture,
    )
    btn_capture.pack(side=tk.LEFT)

    # Note explicative
    tk.Label(
        tab,
        text=(
            "  Maintenez la touche PTT enfoncée pour parler, relâchez pour envoyer.\n"
            "  Le bouton souris « 🎤 Parler » fonctionne aussi (maintenir = enregistrer).\n"
            "  Touches conseillées : F12, Insert, grave (` ~), KP_0 (pavé 0)."
        ),
        bg=BG, fg=FG_DIM, font=("Consolas", 8), justify=tk.LEFT,
    ).pack(anchor="w", padx=20, pady=(0, 6))

    vars_["ptt"] = {"hotkey": ptt_hotkey_var}

    # ── Rafraîchissement interface ────────────────────────────────────────────
    _section(tab, "Rafraîchissement interface", GREEN)

    pg_var = tk.IntVar(value=ui_cfg.get("poll_geometry_ms", 2000))
    _row(tab, "Polling géométrie fenêtres (ms)", _int_slider,
         var=pg_var, from_=500, to=10000, label_suffix="  (2000 recommandé)")

    sr_var = tk.IntVar(value=ui_cfg.get("stats_refresh_ms", 2000))
    _row(tab, "Rafraîchissement HP sidebar (ms)", _int_slider,
         var=sr_var, from_=500, to=10000, label_suffix="  (2000 recommandé)")

    vars_["voice"] = {
        "enabled":          v_var,
        "backend":          backend_var,
    }
    vars_["piper"] = {
        "models_dir":       pdir_var,
        "voice_vars":       piper_voice_vars,
        "pitch_vars":       piper_pitch_vars,
    }
    vars_["ui"] = {
        "poll_geometry_ms": pg_var,
        "stats_refresh_ms": sr_var,
    }
    return tab


def _tab_llm_resources(nb, cfg):
    """Onglet : état des clés API, modèles configurés vs actifs, chaîne de fallback."""
    import os
    import threading as _thr

    tab = tk.Frame(nb, bg=BG)

    # ── En-tête ───────────────────────────────────────────────────────────────
    header = tk.Frame(tab, bg="#0a1520", pady=8)
    header.pack(fill=tk.X)
    tk.Label(header, text="🔑 Ressources LLM",
             bg="#0a1520", fg=GOLD, font=("Arial", 11, "bold")).pack(side=tk.LEFT, padx=16)
    tk.Label(header, text="Clés API · Modèles configurés vs actifs · Chaîne de fallback",
             bg="#0a1520", fg=FG_DIM, font=("Arial", 8)).pack(side=tk.RIGHT, padx=16)

    # ── Zone scrollable ───────────────────────────────────────────────────────
    outer = tk.Frame(tab, bg=BG)
    outer.pack(fill=tk.BOTH, expand=True)

    canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
    sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=sb.set)
    sb.pack(side=tk.RIGHT, fill=tk.Y)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    inner = tk.Frame(canvas, bg=BG)
    win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    inner.bind("<Configure>",
               lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>",
                lambda e: canvas.itemconfig(win_id, width=e.width))

    def _on_wheel(e):
        canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    canvas.bind("<Enter>",  lambda e: canvas.bind_all("<MouseWheel>", _on_wheel))
    canvas.bind("<Leave>",  lambda e: canvas.unbind_all("<MouseWheel>"))

    # ── Helpers locaux ────────────────────────────────────────────────────────
    def _mask(k: str) -> str:
        if len(k) <= 12:
            return k[:4] + "****"
        return k[:8] + "…" + k[-4:]

    def _collect_gemini_keys() -> list[tuple[str, str]]:
        keys = []
        k = os.getenv("GEMINI_API_KEY", "")
        if k:
            keys.append(("GEMINI_API_KEY", k))
        for i in range(1, 10):
            k = os.getenv(f"GEMINI_API_KEY_{i}", "")
            if k and k not in [v for _, v in keys]:
                keys.append((f"GEMINI_API_KEY_{i}", k))
        return keys

    def _collect_groq_keys() -> list[tuple[str, str]]:
        keys = []
        k = os.getenv("GROQ_API_KEY", "")
        if k:
            keys.append(("GROQ_API_KEY", k))
        for i in range(1, 10):
            k = os.getenv(f"GROQ_API_KEY_{i}", "")
            if k and k not in [v for _, v in keys]:
                keys.append((f"GROQ_API_KEY_{i}", k))
        return keys

    def _collect_openrouter_keys() -> list[tuple[str, str]]:
        keys = []
        k = os.getenv("OPENROUTER_API_KEY", "")
        if k:
            keys.append(("OPENROUTER_API_KEY", k))
        for i in range(1, 10):
            k = os.getenv(f"OPENROUTER_API_KEY_{i}", "")
            if k and k not in [v for _, v in keys]:
                keys.append((f"OPENROUTER_API_KEY_{i}", k))
        return keys

    def _card(parent, pady=(4, 2)) -> tk.Frame:
        f = tk.Frame(parent, bg=BG2, relief="flat")
        f.pack(fill=tk.X, padx=20, pady=pady)
        return f

    def _key_row(parent, name: str, val: str, test_fn=None) -> tk.StringVar:
        """Ligne clé : nom | valeur masquée | status | bouton Test."""
        row = tk.Frame(parent, bg=BG2)
        row.pack(fill=tk.X, padx=12, pady=3)
        tk.Label(row, text=f"  {name}", bg=BG2, fg=FG_DIM,
                 font=FONT, width=20, anchor="w").pack(side=tk.LEFT)
        tk.Label(row, text=_mask(val), bg=BG2, fg=ACCENT,
                 font=("Consolas", 9)).pack(side=tk.LEFT, padx=(0, 12))
        sv = tk.StringVar(value="")
        sl = tk.Label(row, textvariable=sv, bg=BG2, font=("Consolas", 8),
                      width=24, anchor="w")
        sl.pack(side=tk.LEFT)
        if test_fn:
            tk.Button(row, text="Tester", bg=BG3, fg=ACCENT,
                      font=("Arial", 8), relief="flat", padx=6,
                      command=lambda: test_fn(sv, sl)).pack(side=tk.LEFT, padx=4)
        return sv

    # ── Fonctions de test réseau ──────────────────────────────────────────────
    def _test_gemini(key_val: str):
        def _fn(sv, sl):
            sv.set("⏳ test en cours…")
            sl.config(fg=GOLD)
            def _run():
                try:
                    import httpx
                    r = httpx.post(
                        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                        headers={"Authorization": f"Bearer {key_val}",
                                 "Content-Type": "application/json"},
                        json={"model": "gemini-2.0-flash",
                              "messages": [{"role": "user", "content": "1+1=?"}],
                              "max_tokens": 5},
                        timeout=12.0,
                    )
                    if r.status_code == 200:
                        sv.set("✓ Clé valide")
                        sl.config(fg=GREEN)
                    elif r.status_code == 429:
                        sv.set("⚠ Quota épuisé (429)")
                        sl.config(fg=GOLD)
                    elif r.status_code in (401, 403):
                        sv.set("✗ Clé invalide (401/403)")
                        sl.config(fg=RED)
                    else:
                        sv.set(f"? HTTP {r.status_code}")
                        sl.config(fg=FG_DIM)
                except Exception as e:
                    sv.set(f"✗ Erreur réseau")
                    sl.config(fg=RED)
            _thr.Thread(target=_run, daemon=True).start()
        return _fn

    def _test_groq(key_val: str):
        def _fn(sv, sl):
            sv.set("⏳ test en cours…")
            sl.config(fg=GOLD)
            def _run():
                try:
                    import httpx
                    r = httpx.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key_val}",
                                 "Content-Type": "application/json"},
                        json={"model": "llama-3.1-8b-instant",
                              "messages": [{"role": "user", "content": "1+1=?"}],
                              "max_tokens": 5},
                        timeout=10.0,
                    )
                    if r.status_code == 200:
                        sv.set("✓ Clé valide")
                        sl.config(fg=GREEN)
                    elif r.status_code == 429:
                        sv.set("⚠ Quota épuisé (429)")
                        sl.config(fg=GOLD)
                    elif r.status_code in (401, 403):
                        sv.set("✗ Clé invalide (401)")
                        sl.config(fg=RED)
                    else:
                        sv.set(f"? HTTP {r.status_code}")
                        sl.config(fg=FG_DIM)
                except Exception:
                    sv.set("✗ Erreur réseau")
                    sl.config(fg=RED)
            _thr.Thread(target=_run, daemon=True).start()
        return _fn

    def _test_deepseek(key_val: str):
        def _fn(sv, sl):
            sv.set("⏳ test en cours…")
            sl.config(fg=GOLD)
            def _run():
                try:
                    import httpx
                    r = httpx.post(
                        "https://api.deepseek.com/chat/completions",
                        headers={"Authorization": f"Bearer {key_val}",
                                 "Content-Type": "application/json"},
                        json={"model": "deepseek-chat",
                              "messages": [{"role": "user", "content": "1+1=?"}],
                              "max_tokens": 5},
                        timeout=12.0,
                    )
                    if r.status_code == 200:
                        sv.set("✓ Clé valide")
                        sl.config(fg=GREEN)
                    elif r.status_code == 402:
                        sv.set("⚠ Crédit insuffisant (402)")
                        sl.config(fg=GOLD)
                    elif r.status_code in (401, 403):
                        sv.set("✗ Clé invalide (401)")
                        sl.config(fg=RED)
                    else:
                        sv.set(f"? HTTP {r.status_code}")
                        sl.config(fg=FG_DIM)
                except Exception:
                    sv.set("✗ Erreur réseau")
                    sl.config(fg=RED)
            _thr.Thread(target=_run, daemon=True).start()
        return _fn

    def _test_openrouter(key_val: str):
        def _fn(sv, sl):
            sv.set("⏳ chargement…")
            sl.config(fg=GOLD)
            def _run():
                try:
                    import httpx
                    r = httpx.get(
                        "https://openrouter.ai/api/v1/key",
                        headers={"Authorization": f"Bearer {key_val}"},
                        timeout=5.0,
                    )
                    if r.status_code == 200:
                        from llm_config import format_openrouter_status
                        data = r.json().get("data", {})
                        txt = format_openrouter_status(data)
                        sv.set(txt.split("\n")[0] if txt else "✓ Clé valide")
                        sl.config(fg=GREEN)
                    else:
                        sv.set("✗ Clé invalide ou erreur")
                        sl.config(fg=RED)
                except Exception as e:
                    sv.set(f"✗ Erreur réseau")
                    sl.config(fg=RED)
            _thr.Thread(target=_run, daemon=True).start()
        return _fn

    # ── Construction du contenu (rebuildable) ─────────────────────────────────
    def _build():
        for w in inner.winfo_children():
            w.destroy()

        from dotenv import load_dotenv
        load_dotenv(override=True)

        gemini_keys = _collect_gemini_keys()
        groq_keys   = _collect_groq_keys()
        or_keys     = _collect_openrouter_keys()
        ds_key      = os.getenv("DEEPSEEK_API_KEY", "")

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 1 — Clés API
        # ══════════════════════════════════════════════════════════════════════
        _section(inner, "🔑 CLÉS API DÉTECTÉES", GOLD)

        # ── Gemini ────────────────────────────────────────────────────────────
        c = _card(inner)
        h = tk.Frame(c, bg=BG2)
        h.pack(fill=tk.X, padx=12, pady=(8, 4))
        ok_g = bool(gemini_keys)
        tk.Label(h, text="Gemini", bg=BG2, fg=ACCENT,
                 font=("Arial", 9, "bold"), width=12, anchor="w").pack(side=tk.LEFT)
        tk.Label(h, text=("✓" if ok_g else "✗"),
                 bg=BG2, fg=(GREEN if ok_g else RED),
                 font=("Arial", 10, "bold")).pack(side=tk.LEFT)
        tk.Label(h, text=f"  {len(gemini_keys)} clé(s) dans .env",
                 bg=BG2, fg=(FG if ok_g else FG_DIM),
                 font=("Consolas", 8)).pack(side=tk.LEFT, padx=8)
        if ok_g:
            tk.Label(h,
                     text=f"→ rotation automatique · cache_seed=None activé",
                     bg=BG2, fg=FG_DIM, font=("Consolas", 7)).pack(side=tk.RIGHT, padx=8)

        for key_name, key_val in gemini_keys:
            _key_row(c, key_name, key_val, _test_gemini(key_val))
        if not ok_g:
            tk.Label(c, text="  Aucune clé GEMINI_API_KEY* trouvée dans .env",
                     bg=BG2, fg=RED, font=("Consolas", 8)).pack(anchor="w", padx=16, pady=4)
        tk.Frame(c, bg=BG2, height=6).pack()

        # ── Groq ──────────────────────────────────────────────────────────────
        c2 = _card(inner)
        h2 = tk.Frame(c2, bg=BG2)
        h2.pack(fill=tk.X, padx=12, pady=(8, 4))
        ok_gr = bool(groq_keys)
        tk.Label(h2, text="Groq", bg=BG2, fg=PURPLE,
                 font=("Arial", 9, "bold"), width=12, anchor="w").pack(side=tk.LEFT)
        tk.Label(h2, text=("✓" if ok_gr else "—"),
                 bg=BG2, fg=(GREEN if ok_gr else FG_DIM),
                 font=("Arial", 10, "bold")).pack(side=tk.LEFT)
        tk.Label(h2, text=f"  {len(groq_keys)} clé(s) dans .env",
                 bg=BG2, fg=(FG if ok_gr else FG_DIM),
                 font=("Consolas", 8)).pack(side=tk.LEFT, padx=8)

        for key_name, key_val in groq_keys:
            _key_row(c2, key_name, key_val, _test_groq(key_val))
        if not ok_gr:
            tk.Label(c2, text="  GROQ_API_KEY non définie — Groq désactivé dans le fallback",
                     bg=BG2, fg=FG_DIM, font=("Consolas", 8)).pack(anchor="w", padx=16, pady=4)
        tk.Frame(c2, bg=BG2, height=6).pack()

        # ── OpenRouter ────────────────────────────────────────────────────────
        c3 = _card(inner)
        h3 = tk.Frame(c3, bg=BG2)
        h3.pack(fill=tk.X, padx=12, pady=(8, 4))
        ok_or = bool(or_keys)
        tk.Label(h3, text="OpenRouter", bg=BG2, fg="#80cbc4",
                 font=("Arial", 9, "bold"), width=12, anchor="w").pack(side=tk.LEFT)
        tk.Label(h3, text=("✓" if ok_or else "—"),
                 bg=BG2, fg=(GREEN if ok_or else FG_DIM),
                 font=("Arial", 10, "bold")).pack(side=tk.LEFT)
        tk.Label(h3, text=f"  {len(or_keys)} clé(s) dans .env",
                 bg=BG2, fg=(FG if ok_or else FG_DIM),
                 font=("Consolas", 8)).pack(side=tk.LEFT, padx=8)

        for key_name, key_val in or_keys:
            _key_row(c3, key_name, key_val, _test_openrouter(key_val))
        if not ok_or:
            tk.Label(c3, text="  Aucune clé OPENROUTER_API_KEY* trouvée dans .env",
                     bg=BG2, fg=FG_DIM, font=("Consolas", 8)).pack(anchor="w", padx=16, pady=4)
        tk.Frame(c3, bg=BG2, height=6).pack()

        # ── DeepSeek ──────────────────────────────────────────────────────────
        c4 = _card(inner, pady=(4, 8))
        h4 = tk.Frame(c4, bg=BG2)
        h4.pack(fill=tk.X, padx=12, pady=(8, 6))
        ok_ds = bool(ds_key)
        tk.Label(h4, text="DeepSeek", bg=BG2, fg="#64b5f6",
                 font=("Arial", 9, "bold"), width=12, anchor="w").pack(side=tk.LEFT)
        tk.Label(h4, text=("✓" if ok_ds else "—"),
                 bg=BG2, fg=(GREEN if ok_ds else FG_DIM),
                 font=("Arial", 10, "bold")).pack(side=tk.LEFT)
        if ok_ds:
            tk.Label(h4, text=f"  {_mask(ds_key)}", bg=BG2, fg=ACCENT,
                     font=("Consolas", 9)).pack(side=tk.LEFT, padx=8)
            sv_ds = tk.StringVar(value="")
            sl_ds = tk.Label(h4, textvariable=sv_ds, bg=BG2,
                             font=("Consolas", 8), width=24, anchor="w")
            sl_ds.pack(side=tk.LEFT, padx=4)
            tk.Button(h4, text="Tester", bg=BG3, fg=ACCENT,
                      font=("Arial", 8), relief="flat", padx=6,
                      command=lambda: _test_deepseek(ds_key)(sv_ds, sl_ds)
                      ).pack(side=tk.LEFT)
        else:
            tk.Label(h4, text="  DEEPSEEK_API_KEY non définie",
                     bg=BG2, fg=FG_DIM, font=("Consolas", 8)).pack(side=tk.LEFT, padx=8)

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 2 — Agents : configuré vs répondu
        # ══════════════════════════════════════════════════════════════════════
        _section(inner, "🧙 AGENTS — MODÈLE CONFIGURÉ vs MODÈLE AYANT RÉPONDU", ACCENT)

        agent_card = _card(inner)

        try:
            import agent_logger as _al
            last_responded = dict(_al._agent_last_responded_models)
        except Exception:
            last_responded = {}

        def _canonical(m: str) -> str:
            for p in ("groq/", "openrouter/"):
                if m.startswith(p):
                    return m[len(p):]
            return m

        for char, color in [("Kaelen", CHAR_COLORS["Kaelen"]),
                             ("Elara",  CHAR_COLORS["Elara"]),
                             ("Thorne", CHAR_COLORS["Thorne"]),
                             ("Lyra",   CHAR_COLORS["Lyra"])]:
            configured = (cfg.get("agents", {})
                          .get(char, DEFAULTS["agents"][char])
                          .get("model", DEFAULTS["agents"][char]["model"]))
            responded  = last_responded.get(char, "")

            row = tk.Frame(agent_card, bg=BG2)
            row.pack(fill=tk.X, padx=12, pady=5)

            # Nom du personnage
            tk.Label(row, text=char, bg=BG2, fg=color,
                     font=("Arial", 9, "bold"), width=9, anchor="w").pack(side=tk.LEFT)

            col = tk.Frame(row, bg=BG2)
            col.pack(side=tk.LEFT, fill=tk.X, expand=True)

            # Ligne configuré
            r1 = tk.Frame(col, bg=BG2)
            r1.pack(fill=tk.X)
            tk.Label(r1, text="configuré :", bg=BG2, fg=FG_DIM,
                     font=("Consolas", 8), width=11, anchor="w").pack(side=tk.LEFT)
            tk.Label(r1, text=configured, bg=BG2, fg=FG,
                     font=("Consolas", 9)).pack(side=tk.LEFT)

            # Ligne répondu
            r2 = tk.Frame(col, bg=BG2)
            r2.pack(fill=tk.X)
            tk.Label(r2, text="répondu  :", bg=BG2, fg=FG_DIM,
                     font=("Consolas", 8), width=11, anchor="w").pack(side=tk.LEFT)

            if not responded:
                tk.Label(r2, text="aucun appel depuis l'ouverture du panneau",
                         bg=BG2, fg=FG_DIM,
                         font=("Consolas", 8, "italic")).pack(side=tk.LEFT)
            else:
                is_fallback = _canonical(responded) != _canonical(configured)
                resp_color  = RED if is_fallback else GREEN
                tk.Label(r2, text=responded, bg=BG2, fg=resp_color,
                         font=("Consolas", 9, "bold")).pack(side=tk.LEFT)
                if is_fallback:
                    tk.Label(r2, text="  ⚠ FALLBACK", bg=BG2, fg=RED,
                             font=("Arial", 8, "bold")).pack(side=tk.LEFT)
                else:
                    tk.Label(r2, text="  ✓", bg=BG2, fg=GREEN,
                             font=("Arial", 9, "bold")).pack(side=tk.LEFT)

            tk.Frame(agent_card, bg=BG3, height=1).pack(fill=tk.X, padx=12)

        tk.Label(agent_card,
                 text="  ℹ Les valeurs 'répondu' se mettent à jour dès qu'un agent parle en session.",
                 bg=BG2, fg=FG_DIM, font=("Consolas", 7)).pack(anchor="w", padx=12, pady=(4, 6))

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 3 — Chaîne de fallback
        # ══════════════════════════════════════════════════════════════════════
        _section(inner, "⛓ CHAÎNE DE FALLBACK GEMINI (ordre de tentative)", PURPLE)

        fb_card = _card(inner, pady=(4, 12))

        # En-tête colonnes
        hdr_fb = tk.Frame(fb_card, bg=BG3)
        hdr_fb.pack(fill=tk.X, padx=8, pady=(6, 2))
        for txt, w, anch in [("#", 4, "e"), ("Modèle", 34, "w"),
                               ("Fournisseur", 14, "w"), ("Clés dispo", 10, "w"), ("Note", 0, "w")]:
            tk.Label(hdr_fb, text=txt, bg=BG3, fg=FG_DIM,
                     font=("Arial", 8, "bold"),
                     width=w, anchor=anch).pack(side=tk.LEFT, padx=4)

        FALLBACK_ROWS = [
            # (modèle affiché, fournisseur, nb_clés_fn, note, warning?)
            ("gemini-2.5-flash",               "Gemini",      len(gemini_keys), "stable · recommandé",         False),
            ("gemini-2.5-pro",                 "Gemini",      len(gemini_keys), "stable · recommandé",         False),
            ("gemini-2.0-flash",               "Gemini",      len(gemini_keys), "stable",                      False),
            ("gemma-4-31b-it",                 "Gemini",      len(gemini_keys), "preview — 404 possible",      True),
            ("gemma-4-26b-a4b-it",             "Gemini",      len(gemini_keys), "preview — 404 possible",      True),
            ("gemini-3-flash-preview",         "Gemini",      len(gemini_keys), "preview — 404 possible",      True),
            ("gemini-3.1-flash-lite-preview",  "Gemini",      len(gemini_keys), "preview — 404 possible",      True),
            ("groq/llama-4-scout-17b-16e-…",   "Groq",        len(groq_keys),   "cross-provider",              False),
            ("openrouter/llama-3.3-70b:free",  "OpenRouter",  len(or_keys), "dernier recours",           False),
            ("openrouter/mistral-small:free",  "OpenRouter",  len(or_keys), "dernier recours",           False),
            ("openrouter/arcee-trinity:free",  "OpenRouter",  len(or_keys), "dernier recours",           False),
        ]

        for i, (model, provider, n_keys, note, warn) in enumerate(FALLBACK_ROWS, 1):
            row = tk.Frame(fb_card, bg=BG2 if i % 2 == 0 else BG)
            row.pack(fill=tk.X, padx=8, pady=1)

            tk.Label(row, text=f"{i:2d}.", bg=row["bg"], fg=FG_DIM,
                     font=("Consolas", 9), width=4, anchor="e").pack(side=tk.LEFT, padx=4)
            tk.Label(row, text=model, bg=row["bg"], fg=FG,
                     font=("Consolas", 9), width=34, anchor="w").pack(side=tk.LEFT, padx=4)
            p_colors = {"Gemini": GOLD, "Groq": PURPLE, "OpenRouter": "#80cbc4"}
            tk.Label(row, text=provider, bg=row["bg"],
                     fg=p_colors.get(provider, FG),
                     font=("Consolas", 8), width=14, anchor="w").pack(side=tk.LEFT, padx=4)

            clé_color = (GREEN if n_keys > 0 else RED)
            clé_txt = (f"{n_keys} clé(s)" if n_keys > 0 else "✗ manquante")
            tk.Label(row, text=clé_txt, bg=row["bg"], fg=clé_color,
                     font=("Consolas", 8), width=10, anchor="w").pack(side=tk.LEFT, padx=4)
            note_color = GOLD if warn else FG_DIM
            tk.Label(row, text=note, bg=row["bg"], fg=note_color,
                     font=("Consolas", 7)).pack(side=tk.LEFT, padx=4)

        tk.Label(fb_card,
                 text=(
                     "\n  ℹ  cache_seed=None : AutoGen repart de l'index 0 à chaque appel "
                     "→ toutes les clés sont tentées dans l'ordre.\n"
                     "  ⚠  Les modèles 'preview' renvoient parfois HTTP 404 (modèle inexistant) "
                     "au lieu de 429 — le fallback les ignore correctement."
                 ),
                 bg=BG2, fg=FG_DIM, font=("Consolas", 7), justify=tk.LEFT,
                 ).pack(anchor="w", padx=12, pady=(4, 8))

    # ── Bouton Actualiser ─────────────────────────────────────────────────────
    tk.Button(header, text="↺ Actualiser", bg=BG3, fg=ACCENT,
              font=("Arial", 8), relief="flat", padx=8,
              command=_build).pack(side=tk.LEFT, padx=12)

    _build()
    return tab


# ─── Fonction principale ───────────────────────────────────────────────────────

def open_config_panel(root, win_state: dict, track_fn, on_saved=None):
    """
    Ouvre le panneau de configuration.

    Paramètres :
      root        – fenêtre principale Tk
      win_state   – dict de géométrie pour persistance
      track_fn    – DnDApp._track_window
      on_saved    – callback(new_cfg) appelé après sauvegarde (pour recharger les agents live)
    """
    cfg = load_app_config()

    win = tk.Toplevel(root)
    win.title("⚙️  Configuration — Moteur de l'Aube Brisée")
    win.configure(bg=BG)
    win.resizable(True, True)
    track_fn("modal_config", win)
    if "modal_config" not in win_state:
        win.geometry("760x640")

    # ── En-tête ──
    hdr = tk.Frame(win, bg="#060d18", pady=10)
    hdr.pack(fill=tk.X)
    tk.Label(hdr, text="⚙️  CONFIGURATION", bg="#060d18", fg=ACCENT,
             font=("Arial", 14, "bold")).pack(side=tk.LEFT, padx=18)
    tk.Label(hdr, text="app_config.json", bg="#060d18", fg=FG_DIM,
             font=("Consolas", 9)).pack(side=tk.RIGHT, padx=18)

    # ── Onglets ttk (style dark) ──
    style = ttk.Style()
    style.theme_use("default")
    style.configure("Dark.TNotebook",
                    background=BG, borderwidth=0, tabmargins=[0, 0, 0, 0])
    style.configure("Dark.TNotebook.Tab",
                    background=BG2, foreground=FG_DIM,
                    font=("Arial", 9, "bold"), padding=[12, 6])
    style.map("Dark.TNotebook.Tab",
              background=[("selected", BG)],
              foreground=[("selected", ACCENT)])

    nb = ttk.Notebook(win, style="Dark.TNotebook")
    nb.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

    vars_: dict = {
        "agents": {}, "chronicler": {}, "groupchat": {},
        "memories": {}, "voice": {}, "ui": {}, "piper": {}, "ptt": {},
        "combat": {},
    }

    # Créer les 6 onglets
    tab_agents  = _tab_agents(nb, cfg, vars_)
    tab_chron   = _tab_chronicler(nb, cfg, vars_)
    tab_gc      = _tab_groupchat(nb, cfg, vars_)
    tab_mem     = _tab_memories(nb, cfg, vars_)
    tab_voice   = _tab_voice_ui(nb, cfg, vars_)
    tab_llm     = _tab_llm_resources(nb, cfg)

    nb.add(tab_agents, text=" 🧙 Agents ")
    nb.add(tab_chron,  text=" 📜 Chroniqueur ")
    nb.add(tab_gc,     text=" ⚙️ GroupChat ")
    nb.add(tab_mem,    text=" 🧠 Mémoires ")
    nb.add(tab_voice,  text=" 🎤 Voix & UI ")
    nb.add(tab_llm,    text=" 🔑 Ressources LLM ")

    # ── Barre du bas ──
    bar = tk.Frame(win, bg="#060d18", pady=8)
    bar.pack(fill=tk.X, side=tk.BOTTOM)

    status_var = tk.StringVar(value="")
    tk.Label(bar, textvariable=status_var, bg="#060d18", fg=GREEN,
             font=("Consolas", 9)).pack(side=tk.LEFT, padx=18)

    def _reset_defaults():
        win.destroy()
        import app_config as _ac
        _ac.APP_CONFIG = dict(_ac.DEFAULTS)
        save_app_config(_ac.APP_CONFIG)
        open_config_panel(root, win_state, track_fn, on_saved)

    def _save():
        # Start with the existing config to preserve keys not managed by this UI (e.g., voice.volume, campaign_name)
        new_cfg = dict(cfg)

        if "agents" not in new_cfg: new_cfg["agents"] = {}
        for char, cvars in vars_["agents"].items():
            new_cfg["agents"][char] = {
                "model":       cvars["model"].get(),
                "temperature": round(cvars["temperature"].get(), 2),
            }

        cv = vars_["chronicler"]
        if "chronicler" not in new_cfg: new_cfg["chronicler"] = {}
        new_cfg["chronicler"].update({
            "model":               cv["model"].get(),
            "temperature":         round(cv["temperature"].get(), 2),
            "memories_importance": cv["memories_importance"].get(),
            "system_prompt":       cv["system_prompt_box"].get("1.0", tk.END).strip(),
        })

        gv = vars_["groupchat"]
        if "groupchat" not in new_cfg: new_cfg["groupchat"] = {}
        new_cfg["groupchat"].update({
            "max_round":            gv["max_round"].get(),
            "allow_repeat_speaker": gv["allow_repeat_speaker"].get(),
        })

        mv = vars_["memories"]
        if "memories" not in new_cfg: new_cfg["memories"] = {}
        new_cfg["memories"].update({
            "compact_importance_min":    mv["compact_importance_min"].get(),
            "contextual_tag_min_length": mv["contextual_tag_min_length"].get(),
        })

        vv = vars_["voice"]
        if "voice" not in new_cfg: new_cfg["voice"] = {}
        new_cfg["voice"].update({
            "enabled": vv["enabled"].get(),
            "backend": vv["backend"].get(),
        })

        pv = vars_["piper"]
        if "piper" not in new_cfg: new_cfg["piper"] = {}
        new_cfg["piper"].update({
            "models_dir": pv["models_dir"].get().strip() or "piper_models",
            "voices": {
                char: sv.get().strip()
                for char, sv in pv["voice_vars"].items()
            },
            "pitch": {
                char: round(dv.get(), 1)
                for char, dv in pv["pitch_vars"].items()
            },
        })
        # Conserver la voix default si absente
        if "default" not in new_cfg["piper"]["voices"]:
            new_cfg["piper"]["voices"]["default"] = (
                new_cfg["piper"]["voices"].get("Kaelen", "fr_FR-upmc-medium")
            )
        if "default" not in new_cfg["piper"]["pitch"]:
            new_cfg["piper"]["pitch"]["default"] = 0.0

        uv = vars_["ui"]
        if "ui" not in new_cfg: new_cfg["ui"] = {}
        new_cfg["ui"].update({
            "poll_geometry_ms":  uv["poll_geometry_ms"].get(),
            "stats_refresh_ms":  uv["stats_refresh_ms"].get(),
        })

        ptt_v = vars_.get("ptt", {})
        if "ptt" not in new_cfg: new_cfg["ptt"] = {}
        new_cfg["ptt"].update({
            "hotkey": ptt_v.get("hotkey", tk.StringVar(value="F12")).get(),
        })

        combat_v = vars_.get("combat", {})
        if "combat" not in new_cfg: new_cfg["combat"] = {}
        new_cfg["combat"].update({
            "model": combat_v.get("model", tk.StringVar(value="gemini-3.1-flash-lite-preview")).get(),
        })

        save_app_config(new_cfg)
        reload_app_config()
        status_var.set("✅ Sauvegardé dans app_config.json")
        win.after(3000, lambda: status_var.set(""))

        if on_saved:
            on_saved(new_cfg)

    tk.Button(bar, text="↺ Réinitialiser défauts",
              bg=BG2, fg=FG_DIM, font=("Arial", 9), relief="flat",
              padx=10, command=_reset_defaults).pack(side=tk.LEFT, padx=(0, 8))

    tk.Button(bar, text="✕ Annuler",
              bg=BG2, fg=RED, font=("Arial", 9, "bold"), relief="flat",
              padx=14, command=win.destroy).pack(side=tk.RIGHT, padx=18)

    tk.Button(bar, text="💾 Sauvegarder",
              bg="#1a3a2a", fg=GREEN, font=("Arial", 10, "bold"), relief="flat",
              padx=16, command=_save).pack(side=tk.RIGHT, padx=6)