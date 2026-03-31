"""
Proxy module to keep backward compatibility.
The actual implementation has been split into the `combat_map` package.
"""
from combat_map_constants import *
from combat_map_constants import _sep, _darken_rgb, _darken_rgb_tuple, _compress_ranges
from combat_map_window import CombatMapWindow
from combat_map_player_view import PlayerMapView, open_combat_map, get_map_prompt