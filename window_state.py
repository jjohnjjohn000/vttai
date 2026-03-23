"""
window_state.py — Persistance de la géométrie des fenêtres Tk.

Fournit :
  - Fonctions utilitaires (_load/_save/_get/_apply)
  - WindowManagerMixin : méthodes à mixin dans DnDApp
    (_poll_main_geometry, _track_window, _restore_windows)
"""

import os
import json
import re

WINDOW_STATE_FILE = "window_state.json"


# ─── Fonctions bas-niveau ──────────────────────────────────────────────────────

def _load_window_state() -> dict:
    try:
        if os.path.exists(WINDOW_STATE_FILE):
            with open(WINDOW_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[WinState] Erreur chargement : {e}")
    return {}

def _save_window_state(state: dict):
    try:
        with open(WINDOW_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[WinState] Erreur sauvegarde : {e}")

def _get_win_geometry(win) -> dict | None:
    try:
        win.update_idletasks()
        # Lecture hybride pour éviter deux types de dérive différents sur X11 :
        #
        # — Axe Y : geometry() getter retourne le hint passé au WM (référentiel
        #   du frame), cohérent avec geometry() setter → pas de dérive titre.
        #   winfo_y() = y_hint + hauteur_barre_titre → drift 37 px à chaque cycle.
        #   → On lit Y depuis la chaîne geometry().
        #
        # — Axe X : geometry() getter retourne la valeur SETtée, pas la position
        #   réelle si le WM a snappé la fenêtre (ex. bord droit de moniteur).
        #   geometry("...+3530") → WM snappe à 3540 → geometry() dit encore 3530
        #   → drift non-cumulatif de 10 px à chaque restore.
        #   winfo_x() retourne la position client réelle APRÈS snap → stable.
        #   La bordure gauche WM (1-2 px) est négligeable et non-cumulative.
        #   → On lit X depuis winfo_x().
        m = re.match(r'(\d+)x(\d+)([+-]\d+)([+-]\d+)', win.geometry())
        if m:
            w = int(m.group(1))
            h = int(m.group(2))
            x = win.winfo_x()   # position X réelle (WM snapping pris en compte)
            y = int(m.group(4)) # hint Y depuis geometry() (évite la dérive barre de titre)
            if w > 1 and h > 1:
                return {"w": w, "h": h, "x": x, "y": y}
    except Exception:
        pass
    return None

def _apply_win_geometry(win, saved: dict | None, default: str):
    if saved and all(k in saved for k in ("w","h","x","y")):
        win.geometry(f"{saved['w']}x{saved['h']}+{saved['x']}+{saved['y']}")
    else:
        win.geometry(default)


# ─── Mixin ────────────────────────────────────────────────────────────────────

class WindowManagerMixin:
    """Mixin pour DnDApp — suivi et restauration de la géométrie des fenêtres.

    Prérequis sur l'instance hôte :
        self.root          — fenêtre principale Tk
        self._win_state    — dict initialisé depuis _load_window_state()
    """

    def _poll_main_geometry(self):
        """Sauvegarde la géométrie de la fenêtre principale toutes les 2 s."""
        try:
            if not self.root.winfo_exists():
                return
            g = _get_win_geometry(self.root)
            if g:
                self._win_state["main"] = g
                _save_window_state(self._win_state)
            self.root.after(2000, self._poll_main_geometry)
        except Exception:
            pass

    def _track_window(self, key: str, win):
        """Attache le suivi géométrie à une Toplevel. Restaure si déjà sauvegardée.
        Les clés préfixées 'modal_' sauvegardent la géométrie mais ne rouvrent pas
        la fenêtre automatiquement au démarrage (fenêtres modales bloquantes).

        IMPORTANT : on n'utilise PAS <Configure> pour sauvegarder — cet event
        se propage depuis tous les widgets enfants (canvas, frames scrollables…)
        et crée des cascades qui segfaultent les extensions C de Tk.
        À la place on utilise un polling léger toutes les 2 secondes.

        POSITIONNEMENT X11 : sur X11 le WM reparente la fenêtre de façon
        asynchrone APRÈS la création des widgets. Si on appelle geometry() avant
        que le WM ait fini, il peut overrider notre position. Pattern fiable :
          1. withdraw()      — cacher la fenêtre pendant le setup
          2. créer widgets   — appelant fait cela après _track_window
          3. after(150, ...) — laisser le WM reparenter et traiter ses events
          4. geometry() + deiconify() — positionner puis afficher sans flash
        """
        saved = self._win_state.get(key)
        is_modal = key.startswith("modal_")

        if saved:
            # Cacher immédiatement pour éviter le flash de repositionnement
            win.withdraw()

            def _deferred_position():
                try:
                    if not win.winfo_exists():
                        return
                    _apply_win_geometry(win, saved, "")
                    win.deiconify()
                    # Le WM peut décaler la fenêtre de quelques pixels lors du
                    # remap (deiconify). Ce décalage n'arrive qu'au moment de
                    # la remise en visibilité (placement initial). Une fois la
                    # fenêtre visible, geometry() n'est plus perturbé.
                    # On re-applique la géométrie 300 ms plus tard pour annuler
                    # tout ajustement du WM (bordures, snapping d'écran…).
                    def _correct_drift():
                        try:
                            if win.winfo_exists():
                                _apply_win_geometry(win, saved, "")
                        except Exception:
                            pass
                    win.after(300, _correct_drift)
                except Exception:
                    try:
                        win.deiconify()
                    except Exception:
                        pass

            # 150 ms : laisse le WM reparenter + tous les widgets être créés
            win.after(150, _deferred_position)

        # ── Polling géométrie (toutes les 2 s, seulement si fenêtre vivante) ──
        def _poll():
            try:
                if not win.winfo_exists():
                    return
                g = _get_win_geometry(win)
                if g:
                    self._win_state[key] = g
                    _save_window_state(self._win_state)
                win.after(2000, _poll)
            except Exception:
                pass

        win.after(2000, _poll)

        # ── Nettoyage du flag _open_ à la fermeture manuelle ─────────────────
        def _on_destroy_cleanup(event=None):
            try:
                # Ne pas effacer le flag si l'application est en train de se
                # fermer : pendant le teardown de root, toutes les Toplevels
                # reçoivent <Destroy> alors que root.winfo_exists() retourne
                # encore True — ce qui effacerait _open_* et sauvegarderait
                # l'état sans les fenêtres ouvertes.
                # _app_closing est positionné par le handler WM_DELETE_WINDOW
                # de root AVANT que root soit détruit.
                if getattr(self, "_app_closing", False):
                    return
                if not is_modal and self.root.winfo_exists():
                    self._win_state.pop(f"_open_{key}", None)
                    _save_window_state(self._win_state)
            except Exception:
                pass

        win.bind("<Destroy>", _on_destroy_cleanup)

        if not is_modal:
            self._win_state[f"_open_{key}"] = True
            _save_window_state(self._win_state)
        return win

    def _restore_windows(self):
        """Rouvre les fenêtres qui étaient ouvertes lors de la dernière session.
        Les délais sont échelonnés pour laisser Tk et gRPC se stabiliser."""
        delay = 0
        if self._win_state.get("_open_combat_tracker"):
            delay += 300
            self.root.after(delay, self.open_combat_tracker)
        if self._win_state.get("_open_inventory"):
            delay += 300
            self.root.after(delay, self.open_inventory_panel)
        if self._win_state.get("_open_quest_journal"):
            delay += 300
            self.root.after(delay, self.open_quest_journal)
        if self._win_state.get("_open_npc_manager"):
            delay += 300
            self.root.after(delay, self.open_npc_manager)
        if self._win_state.get("_open_location_image"):
            delay += 300
            self.root.after(delay, self.open_location_image_popout)
        if self._win_state.get("_open_calendar"):
            delay += 300
            self.root.after(delay, self.open_calendar_popout)
        if self._win_state.get("_open_combat_map"):
            delay += 300
            self.root.after(delay, self.open_combat_map)
        for name in ["Kaelen", "Elara", "Thorne", "Lyra"]:
            if self._win_state.get(f"_open_char_{name}"):
                delay += 400   # 400 ms entre chaque popout pour éviter les races gRPC/Tk
                self.root.after(delay, lambda n=name: self.open_char_popout(n))

    def raise_all_windows(self):
        """Remet toutes les fenêtres du programme en avant-plan.
        Appelé par le bouton ↑ Fenêtres dans la barre d'outils."""

        # ── Fenêtre principale ────────────────────────────────────────────────
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

        # ── Fenêtres Toplevel trackées ────────────────────────────────────────
        # Regroupées en (attribut, est_un_objet_panel) :
        #   False → attribut est directement un Toplevel
        #   True  → attribut est un objet panel avec un attribut .win
        _tracked = [
            ("_inventory_win",       True),   # InventoryPanel → .win
            ("_quest_journal_win",   False),
            ("_combat_map_win",      True),   # CombatMapWindow → .win
            ("_dice_roller_win",     False),
        ]
        for attr, has_win in _tracked:
            obj = getattr(self, attr, None)
            if obj is None:
                continue
            win = getattr(obj, "win", None) if has_win else obj
            if win is None:
                continue
            try:
                if win.winfo_exists():
                    win.deiconify()
                    win.lift()
            except Exception:
                pass

        # ── Combat tracker (objet CombatTracker → .win) ────────────────────
        ct = getattr(self, "_combat_tracker", None)
        if ct is not None:
            win = getattr(ct, "win", None)
            if win:
                try:
                    if win.winfo_exists():
                        win.deiconify()
                        win.lift()
                except Exception:
                    pass

        # ── Popouts personnages (_popout_Kaelen, etc.) ────────────────────────
        for name in ["Kaelen", "Elara", "Thorne", "Lyra"]:
            win = getattr(self, f"_popout_{name}", None)
            if win:
                try:
                    if win.winfo_exists():
                        win.deiconify()
                        win.lift()
                except Exception:
                    pass

        # ── Fenêtres de visages (face_windows dict) ───────────────────────────
        for face in getattr(self, "face_windows", {}).values():
            try:
                if face.winfo_exists():
                    face.lift()
            except Exception:
                pass