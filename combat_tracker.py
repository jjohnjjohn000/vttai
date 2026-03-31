"""
combat_tracker.py
─────────────────
Fichier 10/10 : Classe principale assemblant les Mixins.
Point d'entrée pour le reste de l'application (Moteur IA, main.py, etc.).
"""

import tkinter as tk
import threading

# 1. Imports de l'état global et des constantes
from combat_tracker_state import (
    COMBAT_STATE,
    get_combat_prompt,
    mark_speech_used,
    _is_fully_silenced
)
from combat_tracker_constants import C, _BESTIARY_OK
from combat_tracker_combatant import Combatant

# Si on a besoin de _load_bestiary pour le préchargement asynchrone
if _BESTIARY_OK:
    try:
        from npc_bestiary_panel import _load_bestiary as _bestiary_load
    except ImportError:
        pass

# 2. Imports des Mixins
from combat_tracker_ui_mixin import CombatTrackerUIMixin
from combat_tracker_row_mixin import CombatTrackerRowMixin
from combat_tracker_state_mixin import CombatTrackerStateMixin
from combat_tracker_flow_mixin import CombatTrackerFlowMixin
from combat_tracker_npc_mixin import CombatTrackerNPCMixin
from combat_tracker_utils_mixin import CombatTrackerUtilsMixin


class CombatTracker(
    CombatTrackerUIMixin,
    CombatTrackerRowMixin,
    CombatTrackerStateMixin,
    CombatTrackerFlowMixin,
    CombatTrackerNPCMixin,
    CombatTrackerUtilsMixin
):
    """Fenêtre Toplevel de gestion de combat D&D 5e (assemblée par Mixins)."""

    def __init__(self, root: tk.Tk, state_loader,
                 chat_queue=None, pc_turn_callback=None,
                 advance_turn_callback=None, app=None):
        """
        root              : tk.Tk principal
        state_loader      : callable → dict (load_state de state_manager)
        chat_queue        : queue.Queue pour injecter des messages dans le chat
        pc_turn_callback  : callable(char_name: str) → déclenché automatiquement
                            quand c'est le tour d'un PJ, pour injecter le trigger
                            autogen sans attendre la saisie du MJ.
        """
        self.root              = root
        self.app               = app
        self._load_state       = state_loader
        self.chat_queue        = chat_queue
        self.pc_turn_callback  = pc_turn_callback
        self.advance_turn_callback = advance_turn_callback   # ← nouveau
        
        self.combatants: list[Combatant] =[]
        self.current_idx = -1
        self.round_num   = 0
        self.combat_active = False
        
        self._rows: dict = {}          # uid → frame widgets
        self._row_widgets: dict = {}   # uid → {hp_lbl, bar_canvas, draw_hp_bar} — mises à jour in-place
        self._save_timer = None        # timer de sauvegarde différée (debounce)
        self.kill_pool: list  =[]     # combatants retirés via Kill Pool

        # Appels aux méthodes fournies par les Mixins
        self._build_window()
        self._restore_combat_state()

        # Préchauffage du bestiary en arrière-plan pour éviter le freeze
        # au premier _ct_pick() (chargement du JSON ~2–5 MB)
        if _BESTIARY_OK:
            threading.Thread(target=_bestiary_load, daemon=True,
                             name="bestiary-preload").start()

# Re-exportation propre des éléments utilisés par l'application
__all__ =[
    "CombatTracker",
    "Combatant",
    "COMBAT_STATE",
    "get_combat_prompt",
    "mark_speech_used",
    "_is_fully_silenced"
]