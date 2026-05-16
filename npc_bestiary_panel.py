"""
npc_bestiary_panel.py — Gestionnaire de PNJs et bestiary D&D 5e (Façade de rétrocompatibilité).
──────────────────────────────────────────────────────────────────
Ce fichier sert de point d'entrée unique pour maintenir la compatibilité avec
le reste de l'application. Le code original a été divisé en 6 modules distincts
pour faciliter la maintenance.
"""

# 1. Utilitaires, Formateurs, et Intégration LLM
from npc_utils import (
    _SKILL_TO_STAT, 
    _SKILL_FR, 
    _STAT_COLORS,
    _npc_images_dir,
    _npc_image_path,
    load_npc_image_bytes, 
    save_npc_image_bytes,
    _build_npc_persona,
    speak_as_npc, 
    _fmt_entries, 
    _fmt_damage_list,
    _fmt_condition_list, 
    _fmt_action_list, 
    _fmt_cr,
    _fmt_type, 
    _fmt_ac, 
    _fmt_speed, 
    _ability_mod
)

# 2. Gestionnaire de Bestiaire et Cache
from npc_bestiary_manager import (
    _BESTIARY_DIR, 
    _LEGENDARY_FILE, 
    _CACHE_FILE,
    _BESTIARY_DATA,
    _FLUFF_DATA,
    _LEGENDARY_DATA,
    _BESTIARY_NAMES,
    _apply_mod,
    _resolve_copy,
    _expand_versions,
    _load_bestiary,
    search_monsters, 
    get_monster, 
    _apply_monster_upgrade,
    get_monster_fluff,
    get_legendary_group
)

# 3. Mixins (généralement pas importés directement, mais exposés au cas où)
from npc_sheet_top_mixins import MonsterSheetImageSpeakMixin, MonsterSheetSearchMixin
from npc_sheet_action_mixins import MonsterSheetRenderMixin, MonsterSheetActionMixin

# 4. Classes d'Interface Utilisateur (UI) Principales
from npc_sheet_window import MonsterSheetWindow
from npc_group_panel import GroupNPCPanel

__all__ = [
    "GroupNPCPanel",
    "MonsterSheetWindow",
    "search_monsters",
    "get_monster",
    "get_monster_fluff",
    "get_legendary_group",
    "load_npc_image_bytes",
    "save_npc_image_bytes",
    "speak_as_npc",
]