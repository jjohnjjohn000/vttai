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
                       resume_callback):
    """
    Popup tkinter qui permet au MJ de modifier les dégâts avant de les
    confirmer. Appelle resume_callback(final_amount) à la fermeture.
    """
    popup = tk.Toplevel(self.root)
    popup.title(f"⚔️ Dégâts — {char_name} → {cible}")
    popup.configure(bg="#1e1e2e")
    popup.resizable(False, False)
    popup.attributes("-topmost", True)
    try:
        popup.grab_set()
    except tk.TclError:
        pass  # autre fenêtre modale active — le popup reste utilisable sans grab

    color = self.CHAR_COLORS.get(char_name, "#4fc3f7")

    # ── Header ──────────────────────────────────────────────────────────────
    hdr = tk.Frame(popup, bg=color, pady=5)
    hdr.pack(fill=tk.X)
    crit_tag = "  🎯 CRITIQUE" if is_crit else ""
    tk.Label(hdr,
             text=f"⚔️  {char_name}  →  {cible}{crit_tag}",
             bg=color, fg="#0d0d0d",
             font=("Arial", 11, "bold")).pack(side=tk.LEFT, padx=12)

    # ── Détail des dés (lecture seule) ──────────────────────────────────────
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

    # ── Spinbox de modification ──────────────────────────────────────────────
    edit_frame = tk.Frame(popup, bg="#1e1e2e")
    edit_frame.pack(fill=tk.X, padx=12, pady=10)

    tk.Label(edit_frame, text="Dégâts finaux :",
             bg="#1e1e2e", fg="#aaaaaa",
             font=("Arial", 10)).pack(side=tk.LEFT)

    _var = tk.IntVar(value=total)
    spx = tk.Spinbox(edit_frame, from_=0, to=999,
                     textvariable=_var, width=6,
                     bg="#252535", fg=color,
                     font=("Consolas", 13, "bold"),
                     buttonbackground="#252535",
                     relief="flat",
                     highlightthickness=1, highlightcolor=color)
    spx.pack(side=tk.LEFT, padx=(8, 0))
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
        popup.destroy()
        resume_callback(final)

    def _cancel():
        # En cas d'annulation : timeout naturel du wait() dans engine_receive
        popup.destroy()
        # On ne rappelle PAS resume_callback → le wait() expire et _dmg_total
        # est utilisé comme fallback dans engine_receive.py

    popup.protocol("WM_DELETE_WINDOW", _cancel)
    spx.bind("<Return>", lambda e: _confirm())
    spx.bind("<Escape>", lambda e: _cancel())

    tk.Button(btn_frame, text="✅ Confirmer",
              bg=color, fg="#0d0d0d",
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