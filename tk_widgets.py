# ====================================================================
# FIX C — SafeButton : Tk 8.6 sur Ubuntu 22.04 segfaulte quand un
# tk.Button contient un emoji hors-BMP (U+1F000+) ou un sélecteur de
# variation U+FE0F — le moteur de rendu Tcl cherche un glyphe dans
# Noto Color Emoji via le serveur de polices X11, ce qui corrompt la
# pile dans tkinter/__init__.py:3163 (tk.Button.__init__).
#
# Solution : remplacer les emoji dangereux par des équivalents ASCII
# UNIQUEMENT dans les widgets Button (Labels = chemin de rendu différent,
# plus robuste). SafeButton est un drop-in pour tk.Button.
# ====================================================================

import tkinter as tk

_EMOJI_TO_ASCII = {
    "🎤": "[Mic]", "📜": "[Q]",  "🎲": "[D]",  "💾": "[Sav]",
    "🛑": "[Stop]","⚙️": "[Cfg]","📸": "[Img]", "✏️": "[Edit]",
    "🗓": "[Cal]", "🗺️": "[Map]","📊": "[Stats]","📂": "[Open]",
    "🔄": "[Rst]", "🗑": "[Del]", "⚔️": "[Cbt]", "📅": "[Cal]",
    "✅": "[OK]",  "✨": "[Mag]", "⚡": "[Fast]","🎭": "[NPC]",
    "📈": "[Up]",  "📉": "[Dn]",  "🎯": "[Aim]", "🔮": "[Orb]",
    "💫": "[*]",   "🌙": "[Moon]","☽": "[Moon]", "🌟": "[Star]",
    "💥": "[Boom]","🩸": "[HP]",  "🧪": "[Pot]", "⚗️": "[Alch]",
    "🔑": "[Key]", "🗝️": "[Key]", "💰": "[Gold]","🏆": "[Win]",
    "⏹": "[Stop]", "⚔": "[Cbt]", "⚙": "[Cfg]", "✏": "[Edit]",
    "🔒": "[Priv]","⚠️": "[Warn]","❌": "[X]", "🗳️": "[Vote]",
    "💀": "[Mort]","🧠": "[IA]",  "🖼️": "[Img]","📌": "[Note]",
    "🛡️": "[Def]", "🔥": "[Feu]", "❄️": "[Froid]","👁️": "[Oeil]",
    "💭": "[Pense]","🏃": "[Mvt]", "👑": "[Roi]","💍": "[Bague]",
    "☠️": "[Mort]","🤕": "[Mal]", "🤢": "[Deg]","🥰": "[Love]",
    "😊": "[Joie]","😢": "[Triste]","😠": "[Mad]","😮": "[Surp]",
    "😨": "[Peur]","😒": "[Blase]","🤍": "[Coeur]"
}

def _safe_text(text: str) -> str:
    """Remplace les emoji dangereux pour tk.Button par des équivalents ASCII."""
    for emoji, replacement in _EMOJI_TO_ASCII.items():
        text = text.replace(emoji, replacement)
    # Filet de sécurité : supprimer tout caractère hors-BMP ou sélecteur U+FE0F restant
    result = []
    i = 0
    while i < len(text):
        c = text[i]
        cp = ord(c)
        if cp == 0xFE0F:          # variation selector-16 → silently drop
            i += 1
            continue
        if cp > 0xFFFF:           # hors-BMP → remplacer par rien
            i += 1
            continue
        # Drop strict des plages BMP souvent rendues en emoji (segfault Tk 8.6)
        if 0x2600 <= cp <= 0x27BF or 0x2300 <= cp <= 0x23FF:
            i += 1
            continue
        result.append(c)
        i += 1
    return "".join(result)


class SafeButton(tk.Button):
    def __init__(self, master=None, **kw):
        if "text" in kw: kw["text"] = _safe_text(str(kw["text"]))
        super().__init__(master, **kw)
    def config(self, **kw):
        if "text" in kw: kw["text"] = _safe_text(str(kw["text"]))
        super().config(**kw)
    configure = config

class SafeLabel(tk.Label):
    def __init__(self, master=None, **kw):
        if "text" in kw: kw["text"] = _safe_text(str(kw["text"]))
        super().__init__(master, **kw)
    def config(self, **kw):
        if "text" in kw: kw["text"] = _safe_text(str(kw["text"]))
        super().config(**kw)
    configure = config

class SafeCheckbutton(tk.Checkbutton):
    def __init__(self, master=None, **kw):
        if "text" in kw: kw["text"] = _safe_text(str(kw["text"]))
        super().__init__(master, **kw)
    def config(self, **kw):
        if "text" in kw: kw["text"] = _safe_text(str(kw["text"]))
        super().config(**kw)
    configure = config


def apply_safe_patches():
    """Monkey-patch global des widgets sujets au segfault Xft (Tk 8.6).
    Doit être appelé UNE SEULE FOIS, avant toute création de widget."""
    tk.Button = SafeButton
    tk.Label = SafeLabel
    tk.Checkbutton = SafeCheckbutton

    _orig_menu_add_command = tk.Menu.add_command
    def _safe_menu_add_command(self, *args, **kw):
        if "label" in kw: kw["label"] = _safe_text(str(kw["label"]))
        _orig_menu_add_command(self, *args, **kw)
    tk.Menu.add_command = _safe_menu_add_command

    _orig_menu_add_cascade = tk.Menu.add_cascade
    def _safe_menu_add_cascade(self, *args, **kw):
        if "label" in kw: kw["label"] = _safe_text(str(kw["label"]))
        _orig_menu_add_cascade(self, *args, **kw)
    tk.Menu.add_cascade = _safe_menu_add_cascade
