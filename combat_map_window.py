import tkinter as tk
from combat_map_core_mixin import CoreMixin
from combat_map_layer_manager import LayerManagerMixin
from combat_map_map_manager import MapManagerMixin
from combat_map_renderer import RendererMixin
from combat_map_token_manager import TokenManagerMixin
from combat_map_ui_toolbar import UIToolbarMixin
from combat_map_obstacle_manager import ObstacleManagerMixin
from combat_map_fog_manager import FogManagerMixin
from combat_map_tool_events import ToolEventMixin
from combat_map_notes_doors import NotesDoorsMixin
from combat_map_navigation import NavigationMixin
from combat_map_selection import SelectionMixin
from combat_map_ruler import RulerMixin

class CombatMapWindow(
    CoreMixin,
    LayerManagerMixin,
    MapManagerMixin,
    RendererMixin,
    TokenManagerMixin,
    UIToolbarMixin,
    ObstacleManagerMixin,
    FogManagerMixin,
    ToolEventMixin,
    NotesDoorsMixin,
    NavigationMixin,
    SelectionMixin,
    RulerMixin
):
    pass
