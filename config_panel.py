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
)

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


def _tab_voice_ui(nb, cfg, vars_):
    """Onglet : voix TTS et paramètres UI."""
    tab = tk.Frame(nb, bg=BG)

    header = tk.Frame(tab, bg="#0a1520", pady=8)
    header.pack(fill=tk.X)
    tk.Label(header, text="🎤 Voix & Interface",
             bg="#0a1520", fg=GREEN, font=("Arial", 11, "bold")).pack(side=tk.LEFT, padx=16)
    tk.Label(header, text="TTS edge-tts / ffplay et rafraîchissement UI",
             bg="#0a1520", fg=FG_DIM, font=("Arial", 8)).pack(side=tk.RIGHT, padx=16)

    voice = cfg.get("voice", DEFAULTS["voice"])
    ui    = cfg.get("ui",    DEFAULTS["ui"])

    _section(tab, "Synthèse vocale (TTS)", GREEN)

    v_var = tk.BooleanVar(value=voice.get("enabled", True))
    _checkbox(tab, v_var, "Activer la synthèse vocale (edge-tts + ffplay)")

    _section(tab, "Rafraîchissement interface", GREEN)

    pg_var = tk.IntVar(value=ui.get("poll_geometry_ms", 2000))
    _row(tab, "Polling géométrie fenêtres (ms)", _int_slider,
         var=pg_var, from_=500, to=10000,
         label_suffix="  (2000 recommandé)")

    sr_var = tk.IntVar(value=ui.get("stats_refresh_ms", 2000))
    _row(tab, "Rafraîchissement HP sidebar (ms)", _int_slider,
         var=sr_var, from_=500, to=10000,
         label_suffix="  (2000 recommandé)")

    vars_["voice"] = {"enabled": v_var}
    vars_["ui"]    = {"poll_geometry_ms": pg_var, "stats_refresh_ms": sr_var}
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
        "memories": {}, "voice": {}, "ui": {}
    }

    # Créer les 5 onglets
    tab_agents  = _tab_agents(nb, cfg, vars_)
    tab_chron   = _tab_chronicler(nb, cfg, vars_)
    tab_gc      = _tab_groupchat(nb, cfg, vars_)
    tab_mem     = _tab_memories(nb, cfg, vars_)
    tab_voice   = _tab_voice_ui(nb, cfg, vars_)

    nb.add(tab_agents, text=" 🧙 Agents ")
    nb.add(tab_chron,  text=" 📜 Chroniqueur ")
    nb.add(tab_gc,     text=" ⚙️ GroupChat ")
    nb.add(tab_mem,    text=" 🧠 Mémoires ")
    nb.add(tab_voice,  text=" 🎤 Voix & UI ")

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
        new_cfg: dict = {
            "agents":     {},
            "chronicler": {},
            "groupchat":  {},
            "memories":   {},
            "voice":      {},
            "ui":         {},
        }

        for char, cvars in vars_["agents"].items():
            new_cfg["agents"][char] = {
                "model":       cvars["model"].get(),
                "temperature": round(cvars["temperature"].get(), 2),
            }

        cv = vars_["chronicler"]
        new_cfg["chronicler"] = {
            "model":               cv["model"].get(),
            "temperature":         round(cv["temperature"].get(), 2),
            "memories_importance": cv["memories_importance"].get(),
            "system_prompt":       cv["system_prompt_box"].get("1.0", tk.END).strip(),
        }

        gv = vars_["groupchat"]
        new_cfg["groupchat"] = {
            "max_round":            gv["max_round"].get(),
            "allow_repeat_speaker": gv["allow_repeat_speaker"].get(),
        }

        mv = vars_["memories"]
        new_cfg["memories"] = {
            "compact_importance_min":    mv["compact_importance_min"].get(),
            "contextual_tag_min_length": mv["contextual_tag_min_length"].get(),
        }

        vv = vars_["voice"]
        new_cfg["voice"] = {"enabled": vv["enabled"].get()}

        uv = vars_["ui"]
        new_cfg["ui"] = {
            "poll_geometry_ms":  uv["poll_geometry_ms"].get(),
            "stats_refresh_ms":  uv["stats_refresh_ms"].get(),
        }

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
