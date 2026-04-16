"""
class_data.py — Chargeur de données de classes D&D 5e depuis les fichiers JSON.

Lit les fichiers class/class-*.json (format 5etools) et expose des fonctions
pour obtenir les dés de vie, emplacements de sorts, capacités de classe, etc.

Les résultats sont mis en cache en mémoire pour éviter de relire les fichiers
à chaque appel.
"""

import json
import os
import re
from functools import lru_cache
from typing import Optional

_CLASS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "class")


# ─── Chargeur brut avec cache ─────────────────────────────────────────────────

@lru_cache(maxsize=20)
def _load_class_json(class_name: str) -> dict:
    """Charge et met en cache le fichier JSON d'une classe."""
    name = class_name.strip().lower()
    path = os.path.join(_CLASS_DIR, f"class-{name}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Fichier de classe introuvable : {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_class_entry(class_name: str) -> dict:
    """Retourne le premier objet 'class' du fichier JSON."""
    data = _load_class_json(class_name)
    classes = data.get("class", [])
    if not classes:
        raise ValueError(f"Aucune classe trouvée dans le fichier pour '{class_name}'")
    return classes[0]


# ─── Hit Die ──────────────────────────────────────────────────────────────────

def get_hit_die(class_name: str) -> int:
    """Retourne le nombre de faces du dé de vie (ex: Paladin → 10, Wizard → 6)."""
    entry = _get_class_entry(class_name)
    hd = entry.get("hd", {})
    return hd.get("faces", 8)  # défaut d8


# ─── Spell Slots ──────────────────────────────────────────────────────────────

def get_spell_slots(class_name: str, level: int) -> dict:
    """
    Retourne les emplacements de sorts maximaux pour une classe à un niveau donné.

    Retourne un dict {str(spell_level): count} avec seulement les niveaux > 0.
    Ex: Paladin L15 → {"1": 4, "2": 3, "3": 3, "4": 2, "5": 1}
    Ex: Rogue L15  → {}
    """
    entry = _get_class_entry(class_name)

    # Chercher la table "rowsSpellProgression" dans classTableGroups
    for group in entry.get("classTableGroups", []):
        rows = group.get("rowsSpellProgression")
        if rows is None:
            continue

        # rows est indexé par niveau (0 = niveau 1, 1 = niveau 2, etc.)
        if level < 1 or level > len(rows):
            return {}

        row = rows[level - 1]  # liste de slots [niv1, niv2, ...]
        result = {}
        for i, slots in enumerate(row):
            if slots > 0:
                result[str(i + 1)] = slots
        return result

    # Pas de progression de sorts → classe non-lanceur (Rogue, Fighter, etc.)
    return {}


# ─── Caster Progression ──────────────────────────────────────────────────────

def get_caster_progression(class_name: str) -> Optional[str]:
    """
    Retourne le type de progression de lanceur de sorts.
    "full"  → Wizard, Cleric, Druid, Bard, Sorcerer
    "1/2"   → Paladin, Ranger
    "1/3"   → Arcane Trickster (sous-classe, pas classe)
    None    → Fighter, Rogue, Barbarian, Monk
    """
    entry = _get_class_entry(class_name)
    return entry.get("casterProgression")


# ─── Class Features ──────────────────────────────────────────────────────────

def _parse_feature_level(feature) -> tuple:
    """
    Parse un élément de classFeatures.
    Retourne (nom, niveau) ou None si non parsable.

    Formats possibles :
      "Divine Sense|Paladin||1"
      "Destroy Undead (CR 1/2)|Cleric||5"
      {"classFeature": "Sacred Oath|Paladin||3", ...}
    """
    if isinstance(feature, dict):
        raw = feature.get("classFeature", "")
    elif isinstance(feature, str):
        raw = feature
    else:
        return None

    parts = raw.split("|")
    if len(parts) < 4:
        return None

    name = parts[0].strip()
    try:
        level = int(parts[3].strip())
    except (ValueError, IndexError):
        return None

    return (name, level)


def get_class_features(class_name: str, level: int) -> list:
    """
    Retourne la liste des noms de capacités de classe jusqu'au niveau donné.
    Exclut les "Ability Score Improvement" pour la lisibilité.
    """
    entry = _get_class_entry(class_name)
    features = []
    for feat in entry.get("classFeatures", []):
        parsed = _parse_feature_level(feat)
        if parsed is None:
            continue
        name, feat_level = parsed
        if feat_level <= level and "Ability Score Improvement" not in name:
            features.append(name)
    return features


# ─── Subclass Features ────────────────────────────────────────────────────────

def _find_subclass(class_name: str, subclass_short: str) -> Optional[dict]:
    """Trouve un objet subclass par son shortName."""
    data = _load_class_json(class_name)
    for sub in data.get("subclass", []):
        if sub.get("shortName", "").lower() == subclass_short.strip().lower():
            return sub
    return None


def get_subclass_features(class_name: str, subclass_short: str, level: int) -> list:
    """
    Retourne les noms de capacités de sous-classe jusqu'au niveau donné.

    Format subclassFeatures : "FeatureName|ClassName||SubclassShort||Level"
    """
    sub = _find_subclass(class_name, subclass_short)
    if sub is None:
        return []

    features = []
    for feat_str in sub.get("subclassFeatures", []):
        parts = feat_str.split("|")
        # Format: "Name|Class||SubShort||Level" ou "Name|Class||SubShort|Source|Level"
        name = parts[0].strip()
        try:
            lvl = int(parts[-1].strip())
        except (ValueError, IndexError):
            continue
        if lvl <= level:
            features.append(name)
    return features


# ─── Proficiencies ────────────────────────────────────────────────────────────

def get_proficiencies(class_name: str) -> dict:
    """
    Retourne les maîtrises de départ de la classe.
    {
        "armor": ["light", "medium", ...],
        "weapons": ["simple", "martial", ...],
        "saves": ["wis", "cha"],
    }
    """
    entry = _get_class_entry(class_name)
    profs = entry.get("startingProficiencies", {})

    # Nettoyer les références 5etools ({@item ...}) des armes
    def _clean(items):
        result = []
        for it in items:
            if isinstance(it, str):
                # Extraire le texte lisible de {@item dagger|phb|daggers} → "daggers"
                m = re.match(r'\{@\w+\s+[^|]*\|[^|]*\|([^}]+)\}', it)
                result.append(m.group(1) if m else it)
            elif isinstance(it, dict):
                # Ex: {"choose": {"from": [...], "count": 2}}
                pass  # ignorer les choix
        return result

    return {
        "armor":   _clean(profs.get("armor", [])),
        "weapons": _clean(profs.get("weapons", [])),
        "saves":   entry.get("proficiency", []),
    }


# ─── Subclass Domain Spells ──────────────────────────────────────────────────

def get_subclass_spells(class_name: str, subclass_short: str, level: int) -> list:
    """
    Retourne les sorts de domaine/serment débloqués jusqu'au niveau donné.
    Ex: Life Domain → ["bless", "cure wounds", "lesser restoration", ...]
    """
    sub = _find_subclass(class_name, subclass_short)
    if sub is None:
        return []

    spells = []
    for spell_block in sub.get("additionalSpells", []):
        prepared = spell_block.get("prepared", {})
        for req_level_str, spell_list in sorted(prepared.items(), key=lambda x: int(x[0])):
            req_level = int(req_level_str)
            if req_level <= level:
                for sp in spell_list:
                    if isinstance(sp, str):
                        spells.append(sp.title())
    return spells


# ─── Combat Prompt Generator ─────────────────────────────────────────────────

def get_combat_prompt(class_name: str, subclass_short: str = "", level: int = 11) -> str:
    """
    Génère un bloc de texte formaté décrivant les capacités de combat
    de la classe, pour injection dans les system prompts des agents.

    Inclut : capacités de classe, capacités de sous-classe, sorts de domaine.
    """
    class_name_lower = class_name.strip().lower()
    entry = _get_class_entry(class_name_lower)
    class_display = entry.get("name", class_name.title())

    lines = [
        f"CAPACITÉS DE COMBAT ({class_display} niv.{level}) :"
    ]

    # Dé de vie
    hd = get_hit_die(class_name_lower)
    lines.append(f"  • Dé de vie : d{hd}")

    # Caster progression
    #caster = get_caster_progression(class_name_lower)
    #if caster:
    #    slots = get_spell_slots(class_name_lower, level)
    #    slots_str = "/".join(str(s) for s in slots.values()) if slots else "—"
    #    lines.append(f"  • Lanceur de sorts ({caster}) — emplacements : {slots_str}")

    # Features de classe
    class_feats = get_class_features(class_name_lower, level)
    if class_feats:
        # Regrouper — filtrer les doublons et les features de gestion
        seen = set()
        clean =[]
        for f in class_feats:
            base = re.sub(r'\s*\([^)]*\)', '', f)  # "Destroy Undead (CR 1)" → "Destroy Undead"
            if base not in seen:
                seen.add(base)
                # On ajoute une consigne agressive directement accolée à l'Extra Attack
                if "Extra Attack" in base:
                    clean.append("Extra Attack[⚠️ INTERDIT DE GROUPER : déclare une SEULE attaque par message]")
                else:
                    clean.append(f)
        lines.append(f"  • Capacités de classe : {', '.join(clean)}")

    # Subclass features
    if subclass_short:
        sub_feats = get_subclass_features(class_name_lower, subclass_short, level)
        if sub_feats:
            lines.append(f"  • Capacités ({subclass_short}) : {', '.join(sub_feats)}")

        # Domain/Oath spells
        sub_spells = get_subclass_spells(class_name_lower, subclass_short, level)
        if sub_spells:
            try:
                from spell_data import get_spell
                fmt_spells =[]
                for sp in sub_spells:
                    sp_data = get_spell(sp)
                    if sp_data:
                        _u = sp_data.get("cast_time_raw", [{}])[0].get("unit", "action").lower() if sp_data.get("cast_time_raw") else "action"
                        if "bonus" in _u:
                            _u_fr = "Action Bonus"
                        elif "reaction" in _u:
                            _u_fr = "Réaction"
                        else:
                            _u_fr = "Action"
                        fmt_spells.append(f"{sp} [{_u_fr}]")
                    else:
                        fmt_spells.append(sp)
                lines.append(f"  • Sorts de domaine/serment : {', '.join(fmt_spells)}")
            except Exception:
                lines.append(f"  • Sorts de domaine/serment : {', '.join(sub_spells)}")

    # Proficiencies
    profs = get_proficiencies(class_name_lower)
    armor = profs.get("armor", [])
    weapons = profs.get("weapons", [])
    if armor or weapons:
        parts = []
        if armor:
            parts.append(f"Armures: {', '.join(armor)}")
        if weapons:
            parts.append(f"Armes: {', '.join(weapons)}")
        lines.append(f"  • Maîtrises : {' | '.join(parts)}")

    return "\n".join(lines)


# ─── 5etools Text Cleanup ────────────────────────────────────────────────────

def _clean_5etools_text(text: str) -> str:
    """
    Nettoie le markup 5etools dans les textes de capacités.
    Ex: {@damage 2d8} → 2d8, {@spell hallow} → hallow,
        {@item longsword|phb} → longsword
    """
    if not isinstance(text, str):
        return str(text)
    # {@tag content} → content (premier segment avant |)
    text = re.sub(r'\{@\w+\s+([^|}]+)(?:\|[^}]*)?\}', r'\1', text)
    # {@tag content|source|display} → display (3ème segment)
    text = re.sub(r'\{@\w+\s+[^|]+\|[^|]+\|([^}]+)\}', r'\1', text)
    return text


def _entries_to_text(entries, indent=0) -> str:
    """
    Convertit une liste d'entrées 5etools en texte lisible.
    Les entrées peuvent être des str ou des dicts avec des sous-entrées.
    """
    lines = []
    prefix = "  " * indent
    for entry in entries:
        if isinstance(entry, str):
            lines.append(prefix + _clean_5etools_text(entry))
        elif isinstance(entry, dict):
            etype = entry.get("type", "")
            name  = entry.get("name", "")
            sub_entries = entry.get("entries", [])

            if etype == "entries" and name:
                lines.append(f"\n{prefix}▸ {name}")
                lines.append(_entries_to_text(sub_entries, indent + 1))
            elif etype == "list":
                for item in entry.get("items", []):
                    if isinstance(item, str):
                        lines.append(f"{prefix}  • {_clean_5etools_text(item)}")
                    elif isinstance(item, dict):
                        iname = item.get("name", "")
                        ientries = item.get("entries", [])
                        itxt = _clean_5etools_text(item.get("entry", ""))
                        if iname and itxt:
                            lines.append(f"{prefix}  • {iname} : {itxt}")
                        elif iname and ientries:
                            lines.append(f"{prefix}  • {iname}")
                            lines.append(_entries_to_text(ientries, indent + 2))
                        elif itxt:
                            lines.append(f"{prefix}  • {itxt}")
            elif etype == "table":
                caption = entry.get("caption", "")
                if caption:
                    lines.append(f"{prefix}  [{caption}]")
                col_labels = entry.get("colLabels", [])
                if col_labels:
                    header = " | ".join(_clean_5etools_text(c) for c in col_labels)
                    lines.append(f"{prefix}  {header}")
                    lines.append(f"{prefix}  {'─' * len(header)}")
                for row in entry.get("rows", []):
                    cells = []
                    for cell in row:
                        if isinstance(cell, str):
                            cells.append(_clean_5etools_text(cell))
                        elif isinstance(cell, dict):
                            cells.append(_clean_5etools_text(str(cell.get("exact", cell.get("text", "")))))
                        else:
                            cells.append(str(cell))
                    lines.append(f"{prefix}  {' | '.join(cells)}")
            elif sub_entries:
                if name:
                    lines.append(f"\n{prefix}▸ {name}")
                lines.append(_entries_to_text(sub_entries, indent + 1))
    return "\n".join(l for l in lines if l.strip())


# ─── Feature Detail Retrieval ─────────────────────────────────────────────────

def get_feature_details(class_name: str, feature_name: str,
                        subclass_short: str = "") -> Optional[dict]:
    """
    Retourne les détails complets d'une capacité de classe ou sous-classe.

    Retourne:
      {"name": str, "level": int, "source": str, "text": str}
    ou None si non trouvée.
    """
    data = _load_class_json(class_name.strip().lower())

    # Chercher dans classFeature[]
    for feat in data.get("classFeature", []):
        if feat.get("name", "").lower() == feature_name.strip().lower():
            text = _entries_to_text(feat.get("entries", []))
            return {
                "name":   feat["name"],
                "level":  feat.get("level", 0),
                "source": feat.get("source", "?"),
                "text":   text,
            }

    # Chercher dans subclassFeature[]
    for feat in data.get("subclassFeature", []):
        if feat.get("name", "").lower() == feature_name.strip().lower():
            # Filtrer par sous-classe si spécifié
            if subclass_short:
                feat_sub = feat.get("subclassShortName", "")
                if feat_sub.lower() != subclass_short.strip().lower():
                    continue
            text = _entries_to_text(feat.get("entries", []))
            return {
                "name":   feat["name"],
                "level":  feat.get("level", 0),
                "source": feat.get("source", "?"),
                "text":   text,
            }

    return None


def get_all_feature_details(class_name: str, subclass_short: str = "",
                            level: int = 20) -> list:
    """
    Retourne TOUS les détails de features (classe + sous-classe) jusqu'au niveau donné.
    Chaque élément: {"name", "level", "source", "text", "type": "class"|"subclass"}
    """
    data = _load_class_json(class_name.strip().lower())
    results = []
    seen = set()

    # Class features
    for feat in data.get("classFeature", []):
        feat_level = feat.get("level", 0)
        fname = feat.get("name", "")
        if feat_level <= level and fname and "Ability Score Improvement" not in fname:
            key = fname.lower()
            if key not in seen:
                seen.add(key)
                results.append({
                    "name":   fname,
                    "level":  feat_level,
                    "source": feat.get("source", "?"),
                    "text":   _entries_to_text(feat.get("entries", [])),
                    "type":   "class",
                })

    # Subclass features
    if subclass_short:
        for feat in data.get("subclassFeature", []):
            feat_level = feat.get("level", 0)
            fname = feat.get("name", "")
            feat_sub = feat.get("subclassShortName", "")
            if (feat_level <= level and fname
                    and feat_sub.lower() == subclass_short.strip().lower()):
                key = f"sub:{fname.lower()}"
                if key not in seen:
                    seen.add(key)
                    results.append({
                        "name":   fname,
                        "level":  feat_level,
                        "source": feat.get("source", "?"),
                        "text":   _entries_to_text(feat.get("entries", [])),
                        "type":   "subclass",
                    })

    # Trier par niveau puis par nom
    results.sort(key=lambda x: (x["level"], x["name"]))
    return results


# ─── Capacités de classe sans jet de dés ──────────────────────────────────────
#
# Dictionnaire des capacités qui NE nécessitent PAS de d20.
# Clé   : mot-clé lowercase pour la détection dans intention/regle
# Valeur: (class_name, feature_name, narrative_hint)
# {name} dans narrative_hint est remplacé par le nom du personnage à l'exécution.
#
NO_ROLL_FEATURES: dict = {
    # ── Paladin ───────────────────────────────────────────────────────────────
    "divine sense": (
        "paladin", "Divine Sense",
        "Narre en 1-2 phrases la concentration de {name} : ses paupières qui se ferment, "
        "l'aura sacrée qui rayonne brièvement. Attends que le MJ décrive ce qu'il perçoit.",
    ),
    "lay on hands": (
        "paladin", "Lay on Hands",
        "Narre le toucher sacré de {name} : la chaleur irradiant de sa paume. "
        "Le MJ appliquera les PV restaurés.",
    ),
    "aura of protection": (
        "paladin", "Aura of Protection",
        "Capacité passive — toujours active. Rappelle au MJ qu'elle s'applique "
        "aux jets de sauvegarde des alliés dans les 3 m.",
    ),
    "aura of courage": (
        "paladin", "Aura of Courage",
        "Capacité passive — toujours active. Aucune action requise.",
    ),
    "divine health": (
        "paladin", "Divine Health",
        "Capacité passive. Aucune mécanique à résoudre.",
    ),
    "improved divine smite": (
        "paladin", "Improved Divine Smite",
        "Passif automatique — s'ajoute aux jets d'attaque. Déclare plutôt une attaque.",
    ),
    "cleansing touch": (
        "paladin", "Cleansing Touch",
        "Narre le toucher purificateur de {name}. Le MJ confirmera quel sort est dissipé.",
    ),
    # ── Guerrier ──────────────────────────────────────────────────────────────
    "action surge": (
        "fighter", "Action Surge",
        "Narre l'élan soudain de {name}. "
        "Déclare l'Action supplémentaire dans un nouveau [ACTION].",
    ),
    "second wind": (
        "fighter", "Second Wind",
        "Narre la respiration forcée de {name}. "
        "Le MJ lancera le dé de récupération.",
    ),
    "indomitable": (
        "fighter", "Indomitable",
        "Utilisé en réaction à un jet de sauvegarde raté. Relance ce jet spécifique.",
    ),
    # ── Barbare ───────────────────────────────────────────────────────────────
    "rage": (
        "barbarian", "Rage",
        "Narre le basculement de {name} dans la furie : les veines saillant, "
        "le rugissement. Durée 1 minute (10 rounds).",
    ),
    "reckless attack": (
        "barbarian", "Reckless Attack",
        "Avantage sur le jet d'attaque suivant, mais les attaques contre {name} "
        "ont aussi avantage. Déclare l'attaque dans un [ACTION] distinct.",
    ),
    # ── Roublard ──────────────────────────────────────────────────────────────
    "cunning action": (
        "rogue", "Cunning Action",
        "Action Bonus : Se désengager, Se précipiter, ou Se cacher. "
        "Déclare laquelle dans un [ACTION] distinct.",
    ),
    "uncanny dodge": (
        "rogue", "Uncanny Dodge",
        "Réaction quand une attaque touche {name} : dégâts divisés par 2. "
        "Aucun jet de {name} — c'est la cible qui subit.",
    ),
    "evasion": (
        "rogue", "Evasion",
        "Passif — s'applique automatiquement sur les jets de Dex vs zone.",
    ),
    "steady aim": (
        "rogue", "Steady Aim",
        "Action Bonus (Tasha) : {name} se concentre pour ajuster son prochain tir. "
        "Avantage sur le prochain jet d'attaque, mais {name} ne peut plus se déplacer "
        "ce tour. Aucun jet de dés — déclare l'attaque dans un [ACTION] suivant.",
    ),
    # ── Druide ────────────────────────────────────────────────────────────────
    "wild shape": (
        "druid", "Wild Shape",
        "Narre la transformation de {name}. "
        "Le MJ confirmera la forme choisie et ses stats.",
    ),
    # ── Barde ─────────────────────────────────────────────────────────────────
    "bardic inspiration": (
        "bard", "Bardic Inspiration",
        "Narre les mots ou la mélodie que {name} offre à l'allié ciblé. "
        "L'allié gagne un dé d'Inspiration à utiliser quand il le souhaite.",
    ),
    # ── Moine ─────────────────────────────────────────────────────────────────
    "patient defense": (
        "monk", "Patient Defense",
        "Action Bonus : {name} prend l'action Esquiver. "
        "Les attaques le ciblant ont désavantage jusqu'au prochain tour.",
    ),
    "stunning strike": (
        "monk", "Stunning Strike",
        "Après avoir touché — la cible sauvegarde (Con). "
        "Aucun jet de {name} : le MJ gère la sauvegarde de la cible.",
    ),
}


def get_no_roll_feature(intention: str, regle: str) -> "tuple | None":
    """
    Détecte si intention ou regle correspond à une capacité sans jet.

    Priorité à regle (source de vérité mécanique), puis intention.
    Retourne (class_name, feature_name, narrative_hint) ou None.
    """
    # Cherche d'abord dans regle seul (plus fiable)
    r_low = regle.lower()
    for kw, val in NO_ROLL_FEATURES.items():
        if kw in r_low:
            return val
    # Puis dans intention (moins prioritaire)
    i_low = intention.lower()
    for kw, val in NO_ROLL_FEATURES.items():
        if kw in i_low:
            return val
    return None