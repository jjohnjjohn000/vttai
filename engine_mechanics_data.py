"""
engine_mechanics_data.py — Données et séparation des actions
Partie 1/4 du module engine_mechanics.

Exporte :
  CHAR_MECHANICS          — dict de stats D&D 5e niveau 11 pour chaque PJ
  split_into_subactions   — décompose un bloc [ACTION] en sous-actions
"""

import re as _re

# ─── Stats mécaniques D&D 5e 2014, niveau 11 ──────────────────────────────────
CHAR_MECHANICS: dict = {
    "Kaelen": {  # Paladin 11 — STR20 DEX14 CON16 INT10 WIS14 CHA18 — Prof+4
        "strength": 20, "str_mod": +5, "dexterity": 14, "dex_mod": +2,
        "constitution": 16, "con_mod": +3, "intelligence": 10, "int_mod": +0,
        "wisdom": 14, "wis_mod": +2, "charisma": 18, "cha_mod": +4,
        "spell_mod": +4,
        "atk_melee": +11, "atk_ranged": +7, "atk_spell": +9,
        "speed": 30,
        "dmg_melee": (2, 6, +8), "n_attacks": 2, "save_dc": 18,
        "extra_attack": True,
        "skills": {"athlétisme":+10,"religion":+5,"persuasion":+9,
                   "perspicacité":+7,"intimidation":+9,"perception":+7},
        "saves":  {"force":+10,"dextérité":+7,"constitution":+8,
                   "intelligence":+5,"sagesse":+7,"charisme":+9},
    },
    "Elara": {   # Mage 11 — STR8 DEX16 CON14 INT20 WIS14 CHA10 — Prof+4
        "strength": 8, "str_mod": -1, "dexterity": 16, "dex_mod": +3,
        "constitution": 14, "con_mod": +2, "intelligence": 20, "int_mod": +5,
        "wisdom": 14, "wis_mod": +2, "charisma": 10, "cha_mod": +0,
        "spell_mod": +5,
        "atk_melee": +3, "atk_ranged": +8, "atk_spell": +10,
        "speed": 30,
        "dmg_melee": (1, 4, -1), "n_attacks": 1, "save_dc": 18,
        "extra_attack": False,
        "skills": {"arcanes":+15,"histoire":+10,"investigation":+10,
                   "nature":+10,"religion":+10,"perception":+7,"perspicacité":+7},
        "saves":  {"force":-1,"dextérité":+8,"constitution":+7,
                   "intelligence":+10,"sagesse":+7,"charisme":+5},
    },
    "Thorne": {  # Voleur Assassin 11 — STR12 DEX20 CON14 INT16 WIS12 CHA14 — Prof+4
        "strength": 12, "str_mod": +1, "dexterity": 20, "dex_mod": +5,
        "constitution": 14, "con_mod": +2, "intelligence": 16, "int_mod": +3,
        "wisdom": 12, "wis_mod": +1, "charisma": 14, "cha_mod": +2,
        "spell_mod": +0,
        "atk_melee": +11, "atk_ranged": +11, "atk_spell": None,
        "speed": 30,
        "dmg_melee": (1, 6, +5), "dmg_sneak": (6, 6, 0),
        "n_attacks": 2, "save_dc": None,
        "extra_attack": False,
        "skills": {"discrétion":+15,"escamotage":+15,"tromperie":+12,
                   "perception":+11,"perspicacité":+6,"acrobaties":+10,
                   "investigation":+8,"athlétisme":+6,"intimidation":+7},
        "saves":  {"force":+6,"dextérité":+10,"constitution":+7,
                   "intelligence":+8,"sagesse":+6,"charisme":+7},
    },
    "Lyra": {    # Clerc Vie 11 — STR14 DEX12 CON14 INT12 WIS20 CHA16 — Prof+4
        "strength": 14, "str_mod": +2, "dexterity": 12, "dex_mod": +1,
        "constitution": 14, "con_mod": +2, "intelligence": 12, "int_mod": +1,
        "wisdom": 20, "wis_mod": +5, "charisma": 16, "cha_mod": +3,
        "spell_mod": +5,
        "atk_melee": +7, "atk_ranged": +6, "atk_spell": +10,
        "speed": 30,
        "dmg_melee": (1, 8, +2), "n_attacks": 1, "save_dc": 18,
        "extra_attack": False,
        "skills": {"médecine":+15,"perspicacité":+10,"religion":+6,
                   "persuasion":+8,"perception":+10,"histoire":+6},
        "saves":  {"force":+7,"dextérité":+6,"constitution":+7,
                   "intelligence":+6,"sagesse":+10,"charisme":+8},
    },
}


# ─── split_into_subactions ────────────────────────────────────────────────────

def split_into_subactions(type_label: str, intention: str,
                          regle: str, cible: str,
                          char_mechanics: dict | None = None,
                          char_name: str = "") -> list:
    """
    Retourne l'action déclarée.
    Plus de duplication automatique d'attaques : chaque bloc [ACTION] 
    correspond à UNE SEULE action ou attaque, comme demandé dans le prompt.
    """
    type_low   = (type_label or "").lower()
    intent_low = intention.lower()
    regle_low  = regle.lower()
    combined   = type_low + " " + intent_low + " " + regle_low

    # ── Court-circuit Mouvement ──
    # Si le Type est explicitement "Mouvement", on ne scan pas l'intention
    # pour éviter que des mots comme "attaquer" dans "pour pouvoir l'attaquer"
    # déclenchent un is_physical_attack = True.
    if "mouvement" in type_low:
        return [{
            "type_label": type_label or "Mouvement",
            "intention":  intention,
            "regle":      regle.strip() if regle else "",
            "cible":      cible,
            "single_attack": False,
        }]

    # ── Ready Action (Se Tenir Prêt) ──
    _READY_KW = ("ready", "se tenir prêt", "tenir prêt", "me tenir prêt",
                 "prépare une action", "préparer une action", "ready action",
                 "action préparée", "se prépare à")
    if any(k in combined for k in _READY_KW):
        return[{
            "type_label": type_label or "Action",
            "intention":  intention,
            "regle":      regle.strip(),
            "cible":      cible,
            "ready_action": True,
            "single_attack": False,
        }]

    # ── Détection Attaque Physique ──
    _GENERIC_ATK = (
        "attaque", "frappe", "coup", "tir", "poignarde", "tranche",
        "assaut", "perfore", "lacère", "abat", "sneak attack", "sournoise",
        "reckless", "téméraire", "deux armes", "dual wield",
        "corps-à-corps", "extra attack", "seconde attaque", "deuxième attaque",
    )
    _SPELL_DETECT = ("sort", "magie", "incant", "sacred flame", "flamme sacrée", 
                     "divine favor", "faveur divine", "bless", "bénédiction",
                     "spiritual weapon", "arme spirituelle", "marteau spirituel",
                     "flaming sphere", "sphère de feu", "bigby", "moonbeam",
                     "rayon de lune", "cloud of daggers", "nuage de dagues",
                     "soin", "soigne", "heal", "cure", "imposition", "lay on hands")
    _SMITE_SPELLS = ("wrathful smite", "courroux divin", "thunderous smite", 
                     "frappe tonnerre", "branding smite", "frappe lumière")
    
    # Utilisation des limites de mots (\b) pour éviter que "tir" ne matche dans "répartir"
    _is_spell = bool(_re.search(r'\b(?:' + '|'.join(_SPELL_DETECT + _SMITE_SPELLS) + r')', combined))
    _has_generic_atk = bool(_re.search(r'\b(?:' + '|'.join(_GENERIC_ATK) + r')', combined))
    
    # "divine smite" n'est pas un sort, c'est une feature ajoutée à une attaque
    _is_divine_smite = bool(_re.search(r'\b(?:divine smite|smite divin|châtiment divin|chatiment divin)', combined))
    
    # Identifier les actions qui sont manifestement des compétences/utilitaires et non des attaques
    _SKILL_OVERRIDE = ("se cacher", "cacher", "discrétion", "stealth", "aim", "steady aim", "visée", "viser", "jet de compétence", "skill check")
    _is_skill_action = bool(_re.search(r'\b(?:' + '|'.join(_SKILL_OVERRIDE) + r')', combined))
    
    _has_target = cible.lower().strip() not in ("", "none", "-", "n/a", "aucun", "aucune", "soi-même", "self", "personne", "moi-même", "moi meme", char_name.lower())
    _DODGE_KW = ("esquive", "dodge", "défensive", "defensive")
    _is_dodge = bool(_re.search(r'\b(?:' + '|'.join(_DODGE_KW) + r')', combined))
    _is_dash = bool(_re.search(r'\b(?:dash|foncer|sprint|course)', combined))
    _is_disengage = bool(_re.search(r'\b(?:disengage|désengager|desengager|désengagement|se désengager|se desengager)', combined))
    is_physical_attack = (_has_generic_atk or _is_divine_smite) and not _is_spell and not _is_dodge and not _is_dash and not _is_disengage and not _is_skill_action and _has_target

    # On nettoie le type_label pour enlever "1/2" ou "Extra Attack" s'il y en a, 
    # pour garder l'UI propre, bien que l'agent ne devrait plus les générer.
    clean_type = _re.sub(r'\s*\d+/\d+\s*', ' ', type_label).strip()

    return[{
        "type_label": clean_type or "Action",
        "intention":  intention,
        "regle":      regle.strip(),
        "cible":      cible,
        "single_attack": is_physical_attack,
    }]