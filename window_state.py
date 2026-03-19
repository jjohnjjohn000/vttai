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
        m = re.match(r'(\d+)x(\d+)\+(-?\d+)\+(-?\d+)', win.geometry())
        if m:
            return {"w": int(m.group(1)), "h": int(m.group(2)),
                    "x": int(m.group(3)), "y": int(m.group(4))}
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
        """
        saved = self._win_state.get(key)
        if saved:
            _apply_win_geometry(win, saved, "")
        is_modal = key.startswith("modal_")

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
                if self.root.winfo_exists():
                    if not is_modal:
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