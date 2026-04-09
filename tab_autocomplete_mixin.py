"""
tab_autocomplete_mixin.py
─────────────────────────
Mixin qui ajoute l'autocomplétion Tab à self.entry (chat).

COMPORTEMENT
───────────
  Tab en début de token   → complète / cycle parmi les candidats
  Tab après commande      → complète / cycle parmi les noms de cibles (combat_tracker)
  Tab sur 2e arg de /dmg  → complète / cycle parmi les types de dégâts
  Tab sur 1er arg de /msg → complète / cycle parmi les noms d'agents
  Shift+Tab               → cycle en sens inverse
  Escape                  → annule la complétion en cours (restaure le brouillon)

INTÉGRATION
───────────
1. Hériter de TabAutocompleteMixin dans la classe principale (DnDApp).
2. Dans setup_ui() (ui_setup_mixin.py), remplacer :

        self.entry.bind("<Tab>", lambda e: "break")

   par :

        self.entry.bind("<Tab>",       self._on_tab_complete)
        self.entry.bind("<Shift-Tab>", self._on_tab_complete_back)
        self.entry.bind("<Escape>",    self._on_tab_cancel)

3. C'est tout — aucun autre changement requis.
"""

from __future__ import annotations
import re
import tkinter as tk

# ─── Commandes reconnues ──────────────────────────────────────────────────────
SLASH_COMMANDS: list[str] = [
    "/dmg",
    "/heal",
    "/msg",
    "/vote",
    "/round",
]

# Pour chaque commande : index d'argument (0-based) → type de complétion
#   "target"   = noms dans COMBAT_STATE (ou CHAR_COLORS en fallback)
#   "agent"    = noms d'agents PJ uniquement (self._agents)
#   "dmg_type" = types de dégâts D&D 5e
#   "number"   = valeur par défaut "10"
_COMPLETION_MAP: dict[str, dict[int, str]] = {
    "/dmg":   {0: "target", 1: "number", 2: "dmg_type"},
    "/heal":  {0: "target", 1: "number"},
    "/msg":   {0: "agent"},
    "/vote":  {},        # choix libres — pas de complétion
    "/round": {},        # sans argument
}

# Types de dégâts (même liste que dans damage_link_ui_handler)
_DMG_TYPES_COMPLETION: list[str] = [
    "Tranchant", "Contondant", "Perforant",
    "Acide", "Feu", "Froid", "Foudre",
    "Nécrotique", "Radiant", "Poison",
    "Psychique", "Tonnerre", "Force",
]


class TabAutocompleteMixin:
    """
    Mixin pour DnDApp — autocomplétion Tab dans self.entry.

    État interne (initialisé à la première frappe Tab) :
      _tab_candidates  : liste des complétions possibles
      _tab_idx         : index courant dans _tab_candidates
      _tab_prev_text   : texte de l'entrée APRÈS la dernière complétion Tab
                         (sert à détecter si l'utilisateur a modifié manuellement)
      _tab_draft       : brouillon avant le premier Tab de la session (pour Escape)
    """

    # ─── Entrée principale ────────────────────────────────────────────────────

    def _on_tab_complete(self, event) -> str:
        self._tab_cycle(direction=+1)
        return "break"   # empêche le focus-traversal tkinter

    def _on_tab_complete_back(self, event) -> str:
        self._tab_cycle(direction=-1)
        return "break"

    def _on_tab_cancel(self, event) -> str:
        """Escape : restaure le brouillon d'avant la session Tab courante."""
        draft = getattr(self, "_tab_draft", None)
        if draft is not None:
            self.entry.delete(0, tk.END)
            self.entry.insert(0, draft)
            self.entry.icursor(tk.END)
            # Réinitialise l'état
            self._tab_candidates = []
            self._tab_idx        = -1
            self._tab_prev_text  = None
            self._tab_draft      = None
        self._tab_hide_hint()
        return "break"

    # ─── Moteur de cycle ──────────────────────────────────────────────────────

    def _tab_cycle(self, direction: int = +1) -> None:
        current_text = self.entry.get()
        pos          = self.entry.index(tk.INSERT)
        prefix       = current_text[:pos]
        suffix       = current_text[pos:]

        # ── Déterminer si on continue un cycle existant ou on en démarre un ──
        prev = getattr(self, "_tab_prev_text", None)
        # Si le préfixe se termine par un espace, on commence un nouvel argument :
        # forcer un nouveau contexte même si le texte n'a pas changé manuellement.
        continuing = (prev is not None and current_text == prev
                      and not prefix.endswith(" "))

        if not continuing:
            # Nouveau contexte — recalculer les candidats
            token, start, candidates = self._resolve_candidates(prefix)
            if not candidates:
                return
            self._tab_candidates  = candidates
            self._tab_idx         = -1                    # incrémenté juste après
            self._tab_draft       = current_text          # brouillon Escape
            self._tab_token_start = start
            self._tab_suffix      = suffix
        else:
            token                 = ""                    # non utilisé en cycle
            start                 = getattr(self, "_tab_token_start", 0)
            candidates            = getattr(self, "_tab_candidates", [])
            suffix                = getattr(self, "_tab_suffix", "")
            if not candidates:
                return

        # ── Avancer l'index (avec wrap) ───────────────────────────────────────
        idx = getattr(self, "_tab_idx", -1)
        idx = (idx + direction) % len(candidates)
        self._tab_idx = idx

        completion = candidates[idx]

        # ── Reconstruire le texte de l'entrée ────────────────────────────────
        # Préfixe avant le token + complétion choisie, sans espace automatique.
        # L'utilisateur appuie lui-même sur Espace pour confirmer et passer
        # à l'argument suivant.
        new_prefix = prefix[:start] + completion
        new_text   = new_prefix + suffix
        new_pos    = len(new_prefix)

        self._tab_prev_text = new_text   # mémoriser pour le prochain Tab

        self.entry.delete(0, tk.END)
        self.entry.insert(0, new_text)
        self.entry.icursor(new_pos)

        # ── Afficher l'indice visuel dans le hint label ───────────────────────
        self._tab_show_hint(candidates, idx)

    # ─── Résolution des candidats selon le contexte ───────────────────────────

    def _resolve_candidates(self, prefix: str) -> tuple[str, int, list[str]]:
        """
        Analyse `prefix` et retourne (token_partiel, pos_début, candidats).

        Cas couverts :
          A. Complétion d'une commande slash      /dm  → /dmg, /dmg…
          B. 1er argument d'une commande         /dmg K → Kaelen, …
          C. 2e argument de /dmg                 /dmg Kaelen T → Tranchant, …
          D. 1er argument de /msg                /msg Ka → Kaelen, …
        """
        # ── A : complétion de la commande elle-même ───────────────────────────
        m_cmd = re.match(r'^(/\w*)$', prefix)
        if m_cmd:
            partial = m_cmd.group(1).lower()
            cands   = [c for c in SLASH_COMMANDS if c.lower().startswith(partial)]
            return (partial, 0, cands)

        # ── B/C/D : commande reconnue + argument(s) ───────────────────────────
        m_full = re.match(r'^(/\w+)((?:\s+\S+)*)(\s*)$', prefix)
        if not m_full:
            return ("", len(prefix), [])

        raw_cmd      = m_full.group(1).lower()
        args_str     = m_full.group(2)    # " Kaelen" ou " Kaelen Tranchant" …
        trailing_sp  = m_full.group(3)    # espace(s) après le dernier token

        comp_map = _COMPLETION_MAP.get(raw_cmd)
        if comp_map is None:
            return ("", len(prefix), [])

        # Tokeniser les arguments déjà saisis
        arg_tokens = args_str.split()               # ['Kaelen'] ou ['Kaelen', 'Tr…']
        if trailing_sp:
            # On commence un NOUVEAU token (arg_idx = nb tokens complets)
            arg_idx       = len(arg_tokens)
            partial_token = ""
            token_start   = len(prefix)
        else:
            # Le dernier token est en cours
            arg_idx       = max(0, len(arg_tokens) - 1)
            partial_token = arg_tokens[-1] if arg_tokens else ""
            token_start   = len(prefix) - len(partial_token)

        comp_type = comp_map.get(arg_idx)
        if comp_type is None:
            return ("", len(prefix), [])

        candidates = self._candidates_for_type(comp_type, partial_token)
        return (partial_token, token_start, candidates)

    def _candidates_for_type(self, comp_type: str, partial: str) -> list[str]:
        """Retourne la liste des complétions pour un type donné."""
        partial_lo = partial.lower()

        if comp_type == "target":
            pool = self._tab_all_combat_names()

        elif comp_type == "agent":
            if hasattr(self, "_agents") and self._agents:
                pool = list(self._agents.keys())
            else:
                pool = list(self.CHAR_COLORS.keys())

        elif comp_type == "dmg_type":
            pool = _DMG_TYPES_COMPLETION

        elif comp_type == "number":
            # Valeur par défaut proposée ; filtrée si l'utilisateur a déjà tapé autre chose
            return ["10"] if "10".startswith(partial_lo) else []

        else:
            return []

        return [n for n in pool if n.lower().startswith(partial_lo)]

    # ─── Sources de données ───────────────────────────────────────────────────

    def _tab_all_combat_names(self) -> list[str]:
        """Noms depuis COMBAT_STATE, fallback sur CHAR_COLORS."""
        names: list[str] = []
        try:
            from combat_tracker import COMBAT_STATE as _CS
            for c in _CS.get("combatants", []):
                n = c.get("name") if isinstance(c, dict) else str(c)
                if n and n not in names:
                    names.append(n)
        except Exception:
            pass
        if not names:
            names = list(self.CHAR_COLORS.keys())
        return names

    # ─── Hint visuel ─────────────────────────────────────────────────────────
    # Fenêtre flottante (Toplevel overrideredirect + topmost) sous self.entry.
    # Contrairement à un Label placé sur self.root, une Toplevel n'est jamais
    # recouverte par les widgets enfants de la fenêtre principale.

    def _tab_show_hint(self, candidates: list[str], idx: int) -> None:
        """
        Affiche / met à jour la fenêtre-hint de complétion Tab.
        Créée une seule fois ; réutilisée ensuite via deiconify/geometry.
        """
        # ── Créer la Toplevel la première fois ───────────────────────────────
        if not hasattr(self, "_tab_hint_win"):
            win = tk.Toplevel(self.root)
            win.overrideredirect(True)          # pas de barre de titre
            win.wm_attributes("-topmost", True) # toujours au-dessus
            win.withdraw()                      # cachée jusqu'au premier appel

            lbl = tk.Label(
                win,
                bg="#2a2a3e",
                fg="#aaaacc",
                font=("Consolas", 8),
                relief="flat",
                padx=6, pady=3,
                anchor="w",
            )
            lbl.pack(fill="x")

            self._tab_hint_win   = win
            self._tab_hint_label = lbl
            self._tab_hint_after = None

        win = self._tab_hint_win
        lbl = self._tab_hint_label

        # ── Construire le texte : « 2/5   [Tranchant]  Contondant… » ─────────
        count   = len(candidates)
        display = []
        for i, c in enumerate(candidates[:8]):       # max 8 affichés
            display.append(f"[{c}]" if i == idx else c)
        if count > 8:
            display.append(f"(+{count - 8})")
        hint_txt = f"  {idx + 1}/{count}   {'  '.join(display)}"
        lbl.config(text=hint_txt)

        # ── Positionner AU-DESSUS de self.entry (coordonnées écran absolues) ──
        self.entry.update_idletasks()
        win.update_idletasks()                      # besoin de winfo_height() du hint
        x = self.entry.winfo_rootx()
        y = self.entry.winfo_rooty() - win.winfo_height() - 2
        win.geometry(f"+{x}+{y}")
        win.deiconify()
        win.lift()

        # ── Auto-masquer après 4 s d'inactivité ──────────────────────────────
        if self._tab_hint_after:
            lbl.after_cancel(self._tab_hint_after)
        self._tab_hint_after = lbl.after(4000, self._tab_hide_hint)

    def _tab_hide_hint(self) -> None:
        """Cache la fenêtre hint sans la détruire."""
        win = getattr(self, "_tab_hint_win", None)
        if win is not None:
            win.withdraw()
        after = getattr(self, "_tab_hint_after", None)
        if after is not None:
            try:
                self._tab_hint_label.after_cancel(after)
            except Exception:
                pass
            self._tab_hint_after = None