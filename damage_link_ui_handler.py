"""
damage_link_ui_handler.py
─────────────────────────
Snippet à intégrer dans la classe principale de l'app (DnDApp ou équivalent).

1. Dans le handler de msg_queue (là où tu traites les messages entrants),
   ajoute le cas "damage_link" AVANT le cas générique "sender/text" :

        elif msg.get("action") == "damage_link":
            self._handle_damage_link(msg)

2. Ajoute la méthode _handle_damage_link et _open_damage_popup à la classe.
"""

import tkinter as tk
import tkinter.simpledialog as _sd

# ─── Types de dégâts D&D 5e (français) ───────────────────────────────────────
_DMG_TYPES = [
    "—",
    "Tranchant",
    "Contondant",
    "Perforant",
    "Acide",
    "Feu",
    "Froid",
    "Foudre",
    "Nécrotique",
    "Radiant",
    "Poison",
    "Psychique",
    "Tonnerre",
    "Force",
]

# ─── À coller dans la classe principale (ex : DnDApp) ────────────────────────

def _handle_damage_link(self, msg: dict):
    """
    Affiche les détails des dégâts dans le chat, puis insère un widget-bouton
    cliquable « ⚔️ X dégâts → CIBLE  [Modifier / Confirmer] ».

    Appelé depuis le consumer de msg_queue quand action == "damage_link".
    """
    sender      = msg.get("sender", "🎲 Dégâts")
    text        = msg.get("dmg_text", "")
    char_name   = msg.get("char_name", "?")
    cible       = msg.get("cible", "?")
    dmg_total   = msg.get("dmg_total")         # int ou None
    is_crit     = msg.get("is_crit", False)
    resume_cb   = msg.get("resume_callback")   # callback(final_amount: int)
    color       = self.CHAR_COLORS.get(char_name, "#4fc3f7")

    # ── 1. Afficher le texte des dés dans le chat (lecture seule) ──────────
    if text:
        self.append_message(sender, text, color)     # méthode existante

    if dmg_total is None or resume_cb is None:
        return

    # ── 2. Insérer l'hyperlien cliquable ───────────────────────────────────
    crit_pfx  = "🎯 CRITIQUE — " if is_crit else ""
    link_text = (
        f"  ⚔️  {char_name}  →  {cible}  :  "
        f"{crit_pfx}{dmg_total} dégâts"
        f"   ─   [Modifier / Confirmer]\n"
    )
    tag = f"dmg_lnk_{id(resume_cb)}"

    self.chat_display.config(state=tk.NORMAL)
    self.chat_display.insert(tk.END, link_text, tag)
    self.chat_display.tag_configure(
        tag,
        foreground="#ff9944",
        font=("Consolas", 9, "underline"),
    )

    def _on_click(event,
                  _cn=char_name, _ci=cible,
                  _txt=text, _tot=dmg_total,
                  _crit=is_crit, _cb=resume_cb, _tag=tag):
        # Désactiver visuellement après le premier clic
        try:
            self.chat_display.config(cursor="")
            self.chat_display.tag_configure(_tag, foreground="#886633",
                                         font=("Consolas", 9))
            self.chat_display.tag_unbind(_tag, "<Button-1>")
            self.chat_display.tag_unbind(_tag, "<Enter>")
            self.chat_display.tag_unbind(_tag, "<Leave>")
        except Exception:
            pass
        # Appel direct — after() avalait les exceptions silencieusement
        try:
            self._open_damage_popup(_cn, _ci, _txt, _tot, _crit, _cb)
        except Exception as _e:
            print(f"[damage_link] ❌ Erreur ouverture popup : {_e}")

    def _on_enter(event, _tag=tag):
        self.chat_display.config(cursor="hand2")
        self.chat_display.tag_configure(_tag, foreground="#ffcc88")

    def _on_leave(event, _tag=tag):
        self.chat_display.config(cursor="")
        self.chat_display.tag_configure(_tag, foreground="#ff9944")

    self.chat_display.tag_bind(tag, "<Button-1>", _on_click)
    self.chat_display.tag_bind(tag, "<Enter>",    _on_enter)
    self.chat_display.tag_bind(tag, "<Leave>",    _on_leave)
    self.chat_display.config(state=tk.DISABLED)
    self.chat_display.see(tk.END)


def _open_damage_popup(self,
                       char_name: str, cible: str,
                       dmg_text: str, total: int,
                       is_crit: bool,
                       resume_callback,
                       mode: str = "damage",
                       dmg_type: str = ""):
    """
    Popup tkinter qui permet au MJ de modifier les dégâts (ou soins) avant de les
    confirmer. Appelle resume_callback(final_amount[, selected_target]) à la fermeture.

    mode="damage" (défaut) — boîte de dégâts.  Affiche un dropdown cible + type de dégâts.
    mode="heal"            — boîte de soins (verte).  Affiche un dropdown cible (personnage soigné).

    Nouveautés :
      • Dropdown "Cible" / "Soigné" peuplé depuis COMBAT_STATE (combat_map) ou CHAR_COLORS.
      • Dropdown "Type de dégâts" (mode damage uniquement), pré-sélectionné si dmg_type fourni.
      • resume_callback est appelé avec (final, selected_target) si la signature l'accepte,
        sinon fallback (final) pour ne pas briser les appels depuis engine_receive.py.
    """
    _is_heal = (mode == "heal")

    # ── Récupération des combattants depuis COMBAT_STATE (combat_map) ──────
    _combat_names: list[str] = []
    try:
        from combat_tracker import COMBAT_STATE as _CS
        for _c in _CS.get("combatants", []):
            _n = _c.get("name") if isinstance(_c, dict) else str(_c)
            if _n and _n not in _combat_names:
                _combat_names.append(_n)
    except Exception:
        pass
    # Fallback : noms des PJs si le tracker est vide / non chargé
    if not _combat_names:
        _combat_names = list(self.CHAR_COLORS.keys())

    # ── Valeur initiale du dropdown ─────────────────────────────────────────
    # En mode heal la « cible » est le personnage soigné → char_name
    if _is_heal:
        _init_target = char_name if char_name and char_name not in ("?",) else (
            _combat_names[0] if _combat_names else "?"
        )
    else:
        _init_target = cible if cible and cible not in ("—", "?", "") else (
            _combat_names[0] if _combat_names else "?"
        )
    # S'assurer que la valeur initiale est dans la liste
    if _init_target not in _combat_names:
        _combat_names = [_init_target] + _combat_names

    _cible_var = tk.StringVar(value=_init_target)

    # ── Pré-sélection du type de dégâts ────────────────────────────────────
    _init_type = "—"
    if dmg_type:
        _init_type = next(
            (t for t in _DMG_TYPES if t.lower() == dmg_type.lower()),
            "—",
        )
    _type_var = tk.StringVar(value=_init_type)

    # ── Création du popup ───────────────────────────────────────────────────
    popup = tk.Toplevel(self.root)
    popup.title(
        f"{'💚 Soins' if _is_heal else '⚔️ Dégâts'} — {char_name}"
        + (f" → {cible}" if cible and cible not in ("—", "?", "") else "")
    )
    popup.configure(bg="#1e1e2e")
    popup.resizable(False, False)
    popup.attributes("-topmost", True)
    try:
        popup.grab_set()
    except tk.TclError:
        pass  # autre fenêtre modale active — le popup reste utilisable sans grab

    color      = self.CHAR_COLORS.get(char_name, "#4fc3f7")
    hdr_color  = "#27ae60" if _is_heal else color
    spx_fg     = "#88ffbb" if _is_heal else color
    btn_color  = "#27ae60" if _is_heal else color
    dd_bg      = "#252535"
    dd_fg      = "#88ffbb" if _is_heal else "#ff9944"

    # ── Helper : OptionMenu stylisé ─────────────────────────────────────────
    def _make_optionmenu(parent, var, choices, fg=dd_fg):
        om = tk.OptionMenu(parent, var, *choices)
        om.config(
            bg=dd_bg, fg=fg, activebackground="#333348",
            activeforeground=fg, relief="flat",
            font=("Consolas", 10, "bold"),
            highlightthickness=1, highlightcolor=fg,
            indicatoron=True, bd=0,
        )
        om["menu"].config(
            bg=dd_bg, fg=fg, activebackground="#333348",
            activeforeground=fg, font=("Consolas", 9),
        )
        return om

    # ── Header ──────────────────────────────────────────────────────────────
    hdr = tk.Frame(popup, bg=hdr_color, pady=5)
    hdr.pack(fill=tk.X)
    crit_tag  = "  🎯 CRITIQUE" if (is_crit and not _is_heal) else ""
    hdr_icon  = "💚" if _is_heal else "⚔️"
    hdr_label = (
        f"{hdr_icon}  {char_name}"
        + (f"  →  {cible}" if cible and cible not in ("—", "?", "") else "")
        + crit_tag
    )
    tk.Label(hdr,
             text=hdr_label,
             bg=hdr_color, fg="#0d0d0d",
             font=("Arial", 11, "bold")).pack(side=tk.LEFT, padx=12)

    # ── Détail (lecture seule) ───────────────────────────────────────────────
    if dmg_text:
        detail_frame = tk.Frame(popup, bg="#141422")
        detail_frame.pack(fill=tk.X, padx=8, pady=(8, 0))
        txt = tk.Text(detail_frame, wrap=tk.WORD,
                      bg="#141422", fg="#aabbcc",
                      font=("Consolas", 9), height=4,
                      relief="flat", state=tk.NORMAL,
                      highlightthickness=0)
        txt.insert(tk.END, dmg_text)
        txt.config(state=tk.DISABLED)
        txt.pack(fill=tk.X, padx=6, pady=4)

    # ── Séparateur ──────────────────────────────────────────────────────────
    tk.Frame(popup, bg="#2a2a3e", height=1).pack(fill=tk.X, padx=8, pady=(4, 0))

    # ── Dropdown : Cible / Personnage soigné ────────────────────────────────
    cible_frame = tk.Frame(popup, bg="#1e1e2e")
    cible_frame.pack(fill=tk.X, padx=12, pady=(10, 2))

    cible_lbl = "Soigné :" if _is_heal else "Cible :"
    tk.Label(cible_frame, text=cible_lbl,
             bg="#1e1e2e", fg="#aaaaaa",
             font=("Arial", 10), width=14, anchor="w").pack(side=tk.LEFT)

    _cible_om = _make_optionmenu(cible_frame, _cible_var, _combat_names, fg=dd_fg)
    _cible_om.pack(side=tk.LEFT, padx=(4, 0), fill=tk.X, expand=True)

    # ── Dropdown : Type de dégâts (mode damage uniquement) ──────────────────
    if not _is_heal:
        type_frame = tk.Frame(popup, bg="#1e1e2e")
        type_frame.pack(fill=tk.X, padx=12, pady=(2, 2))

        tk.Label(type_frame, text="Type de dégâts :",
                 bg="#1e1e2e", fg="#aaaaaa",
                 font=("Arial", 10), width=14, anchor="w").pack(side=tk.LEFT)

        _type_om = _make_optionmenu(type_frame, _type_var, _DMG_TYPES, fg="#ff9944")
        _type_om.pack(side=tk.LEFT, padx=(4, 0), fill=tk.X, expand=True)

    # ── Spinbox de modification ──────────────────────────────────────────────
    edit_frame = tk.Frame(popup, bg="#1e1e2e")
    edit_frame.pack(fill=tk.X, padx=12, pady=(6, 6))

    field_lbl = "Soins finaux :" if _is_heal else "Dégâts finaux :"
    tk.Label(edit_frame, text=field_lbl,
             bg="#1e1e2e", fg="#aaaaaa",
             font=("Arial", 10), width=14, anchor="w").pack(side=tk.LEFT)

    _var = tk.IntVar(value=total)
    spx = tk.Spinbox(edit_frame, from_=0, to=999,
                     textvariable=_var, width=6,
                     bg="#252535", fg=spx_fg,
                     font=("Consolas", 13, "bold"),
                     buttonbackground="#252535",
                     relief="flat",
                     highlightthickness=1, highlightcolor=spx_fg)
    spx.pack(side=tk.LEFT, padx=(4, 0))
    spx.focus_set()
    spx.selection_range(0, tk.END)

    # ── Boutons ──────────────────────────────────────────────────────────────
    btn_frame = tk.Frame(popup, bg="#1e1e2e")
    btn_frame.pack(fill=tk.X, padx=12, pady=(0, 12))

    _confirmed = [False]

    def _confirm():
        _confirmed[0] = True
        try:
            final = max(0, min(999, int(spx.get())))
        except ValueError:
            final = total
        _sel_target = _cible_var.get()
        popup.destroy()
        # Appel avec (final, selected_target) — fallback (final) pour les
        # callbacks engine_receive.py qui n'acceptent qu'un seul argument.
        try:
            resume_callback(final, _sel_target)
        except TypeError:
            resume_callback(final)

        # L'application de dégâts (apply_damage_to_npc) est désormais entièrement gérée par
        # engine_receive.py après _dl_ev.wait(). On ne le fait plus ici en double !

    def _cancel():
        # En cas d'annulation : timeout naturel du wait() dans engine_receive
        popup.destroy()
        # On ne rappelle PAS resume_callback → le wait() expire et _dmg_total
        # est utilisé comme fallback dans engine_receive.py

    popup.protocol("WM_DELETE_WINDOW", _cancel)
    spx.bind("<Return>", lambda e: _confirm())
    spx.bind("<Escape>", lambda e: _cancel())

    confirm_lbl = "✅ Appliquer soin" if _is_heal else "✅ Confirmer"
    tk.Button(btn_frame, text=confirm_lbl,
              bg=btn_color, fg="#0d0d0d",
              font=("Arial", 10, "bold"),
              relief="flat", padx=14, pady=5,
              cursor="hand2",
              command=_confirm).pack(side=tk.RIGHT, padx=(4, 0))

    tk.Button(btn_frame, text="Annuler",
              bg="#2a2a3a", fg="#888899",
              font=("Arial", 9),
              relief="flat", padx=10, pady=5,
              cursor="hand2",
              command=_cancel).pack(side=tk.RIGHT)