"""
race_data.py — Chargeur de données de races D&D 5e depuis les fichiers JSON.

Lit les fichiers race/races.json et race/fluff-races.json (format 5etools) et
expose des fonctions pour obtenir les bonus de caractéristiques, capacités
raciales, vision dans le noir, langues, etc.

Les résultats sont mis en cache en mémoire pour éviter de relire les fichiers
à chaque appel.

Arborescence attendue :
    <projet>/
        race/
            races.json
            fluff-races.json
"""

import json
import os
import re
from functools import lru_cache
from typing import Optional

_RACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "race")

# ─── Loaders bruts avec cache ─────────────────────────────────────────────────

@lru_cache(maxsize=2)
def _load_races_json() -> dict:
    path = os.path.join(_RACE_DIR, "races.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Fichier de races introuvable : {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=2)
def _load_fluff_json() -> dict:
    path = os.path.join(_RACE_DIR, "fluff-races.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ─── Helpers de recherche ─────────────────────────────────────────────────────

def _normalize(name: str) -> str:
    return name.strip().lower()


def get_race_entry(race_name: str, source: Optional[str] = None) -> dict:
    """
    Retourne l'entrée race pour race_name.

    Si source est fourni, filtre par source (ex: "PHB").
    Sinon préfère PHB > SRD > premier trouvé.
    Lève ValueError si introuvable.
    """
    data = _load_races_json()
    candidates = [
        r for r in data.get("race", [])
        if _normalize(r.get("name", "")) == _normalize(race_name)
    ]
    if not candidates:
        raise ValueError(f"Race introuvable : '{race_name}'")
    if source:
        for c in candidates:
            if _normalize(c.get("source", "")) == _normalize(source):
                return c
    # Ordre de préférence : PHB → SRD → MPMM → premier
    for preferred in ("PHB", "MPMM", "VGM"):
        for c in candidates:
            if c.get("source", "").upper() == preferred:
                return c
    return candidates[0]


def get_subrace_entry(race_name: str, subrace_name: str) -> Optional[dict]:
    """Retourne l'entrée subrace correspondante, ou None."""
    data = _load_races_json()
    for sr in data.get("subrace", []):
        if (
            _normalize(sr.get("raceName", "")) == _normalize(race_name)
            and _normalize(sr.get("name", "")) == _normalize(subrace_name)
        ):
            return sr
    return None


def get_available_races() -> list[str]:
    """Retourne la liste triée des noms de races uniques (PHB en priorité)."""
    data = _load_races_json()
    seen: set[str] = set()
    result: list[str] = []
    # PHB first
    for r in data.get("race", []):
        name = r.get("name", "")
        if name and name not in seen and r.get("source") == "PHB":
            seen.add(name)
            result.append(name)
    # Then others
    for r in data.get("race", []):
        name = r.get("name", "")
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return sorted(result)


def get_subraces(race_name: str) -> list[str]:
    """Retourne la liste des sous-races disponibles pour une race."""
    data = _load_races_json()
    return [
        sr.get("name", "")
        for sr in data.get("subrace", [])
        if _normalize(sr.get("raceName", "")) == _normalize(race_name)
        and sr.get("name")
    ]


# ─── Extracteurs de données ───────────────────────────────────────────────────

def get_race_speed(race_name: str) -> dict:
    """
    Retourne les vitesses de déplacement.
    Ex: {"walk": 30} ou {"walk": 25, "fly": 25}
    """
    entry = get_race_entry(race_name)
    speed = entry.get("speed", 30)
    if isinstance(speed, int):
        return {"walk": speed}
    if isinstance(speed, dict):
        return speed
    return {"walk": 30}


def get_race_size(race_name: str) -> list[str]:
    """Retourne la liste des tailles disponibles (ex: ['M'] ou ['S', 'M'])."""
    entry = get_race_entry(race_name)
    size_map = {"T": "Tiny", "S": "Small", "M": "Medium", "L": "Large",
                "H": "Huge", "G": "Gargantuan"}
    sizes = entry.get("size", ["M"])
    return [size_map.get(s, s) for s in sizes]


def get_race_darkvision(race_name: str, subrace_name: Optional[str] = None) -> int:
    """Retourne la portée de la vision dans le noir en pieds (0 si absente)."""
    entry = get_race_entry(race_name)
    base = entry.get("darkvision", 0)
    if subrace_name:
        sr = get_subrace_entry(race_name, subrace_name)
        if sr and "darkvision" in sr:
            base = sr["darkvision"]
    return base


def get_race_age(race_name: str) -> dict:
    """Retourne {"mature": int, "max": int} ou {} si absent."""
    entry = get_race_entry(race_name)
    return entry.get("age", {})


def get_race_ability_bonuses(race_name: str, subrace_name: Optional[str] = None) -> list[dict]:
    """
    Retourne la liste brute des blocs 'ability' de la race (et de la sous-race).

    Chaque bloc est un dict, par exemple :
      {"str": 2}
      {"cha": 2, "choose": {"from": ["str","dex","con","int","wis"], "count": 2}}
      {"choose": {"from": "asi", "count": 2, "amount": 1}}

    Le format brut est conservé car les règles varient beaucoup selon les sources.
    Utilisez format_ability_bonuses() pour un affichage lisible.
    """
    entry = get_race_entry(race_name)
    abilities = list(entry.get("ability", []))
    if subrace_name:
        sr = get_subrace_entry(race_name, subrace_name)
        if sr:
            abilities += sr.get("ability", [])
    return abilities


_ABILITY_NAMES = {
    "str": "Force", "dex": "Dextérité", "con": "Constitution",
    "int": "Intelligence", "wis": "Sagesse", "cha": "Charisme",
}


def format_ability_bonuses(race_name: str, subrace_name: Optional[str] = None) -> list[str]:
    """
    Retourne une liste de chaînes lisibles pour les bonus de caractéristiques.
    Ex: ["+2 Charisme", "+1 au choix (×2)"]
    """
    blocks = get_race_ability_bonuses(race_name, subrace_name)
    result: list[str] = []
    for block in blocks:
        fixed = {k: v for k, v in block.items() if k != "choose" and isinstance(v, int)}
        choose = block.get("choose")
        for stat, val in fixed.items():
            sign = "+" if val >= 0 else ""
            result.append(f"{sign}{val} {_ABILITY_NAMES.get(stat, stat.upper())}")
        if choose:
            from_list = choose.get("from", [])
            count = choose.get("count", 1)
            amount = choose.get("amount", 1)
            sign = "+" if amount >= 0 else ""
            if from_list == "asi":
                label = "toute caractéristique"
            elif isinstance(from_list, list) and len(from_list) >= 5:
                label = "au choix"
            elif isinstance(from_list, list):
                label = "/".join(_ABILITY_NAMES.get(s, s.upper()) for s in from_list)
            else:
                label = "au choix"
            suffix = f" (×{count})" if count > 1 else ""
            result.append(f"{sign}{amount} {label}{suffix}")
    return result if result else ["Aucun (ou au choix libre)"]


def get_race_languages(race_name: str, subrace_name: Optional[str] = None) -> list[str]:
    """Retourne la liste de langues lisibles."""
    entry = get_race_entry(race_name)
    lang_blocks = list(entry.get("languageProficiencies", []))
    if subrace_name:
        sr = get_subrace_entry(race_name, subrace_name)
        if sr:
            lang_blocks += sr.get("languageProficiencies", [])

    _lang_map = {
        "common": "Commun", "elvish": "Elfique", "dwarvish": "Nain",
        "giant": "Géant", "gnomish": "Gnome", "goblin": "Gobelin",
        "halfling": "Halfelin", "orc": "Orc", "abyssal": "Abyssal",
        "celestial": "Céleste", "draconic": "Draconique",
        "deep speech": "Langue des Profondeurs", "infernal": "Infernal",
        "primordial": "Primordial", "sylvan": "Sylvestre",
        "undercommon": "Langue Souterraine",
    }
    result: list[str] = []
    for block in lang_blocks:
        for k, v in block.items():
            if k == "anyStandard":
                count = v if isinstance(v, int) else 1
                result.append(f"+{count} langue(s) au choix")
            elif k == "any":
                count = v if isinstance(v, int) else 1
                result.append(f"+{count} langue(s) au choix")
            elif v is True:
                result.append(_lang_map.get(k.lower(), k.title()))
    return result if result else ["Commun"]


def get_race_skill_proficiencies(race_name: str, subrace_name: Optional[str] = None) -> list[str]:
    """Retourne les maîtrises de compétences issues de la race."""
    entry = get_race_entry(race_name)
    blocks = list(entry.get("skillProficiencies", []))
    if subrace_name:
        sr = get_subrace_entry(race_name, subrace_name)
        if sr:
            blocks += sr.get("skillProficiencies", [])

    result: list[str] = []
    for block in blocks:
        for k, v in block.items():
            if k == "any":
                count = v if isinstance(v, int) else 1
                result.append(f"+{count} compétence(s) au choix")
            elif v is True:
                result.append(k.title())
    return result


def get_race_resistance(race_name: str, subrace_name: Optional[str] = None) -> list[str]:
    """Retourne les résistances aux dégâts."""
    entry = get_race_entry(race_name)
    res = list(entry.get("resist", []))
    if subrace_name:
        sr = get_subrace_entry(race_name, subrace_name)
        if sr:
            res += sr.get("resist", [])
    return res


def get_race_immunity(race_name: str, subrace_name: Optional[str] = None) -> list[str]:
    """Retourne les immunités."""
    entry = get_race_entry(race_name)
    imm = list(entry.get("immune", []))
    if subrace_name:
        sr = get_subrace_entry(race_name, subrace_name)
        if sr:
            imm += sr.get("immune", [])
    return imm


# ─── Traits raciaux ───────────────────────────────────────────────────────────

def _flatten_entries(entries_node, depth: int = 0) -> str:
    """
    Aplatit récursivement les entrées 5etools en texte lisible.
    Gère les strings, listes, et dicts {"type": "entries"/"list"/...}
    """
    if isinstance(entries_node, str):
        # Nettoyer les tags {@condition charmed} → "charmed"
        text = re.sub(r'\{@\w+ ([^}|]+)(?:\|[^}]*)?\}', r'\1', entries_node)
        return text.strip()

    if isinstance(entries_node, list):
        parts = []
        for item in entries_node:
            part = _flatten_entries(item, depth)
            if part:
                parts.append(part)
        return "\n".join(parts)

    if isinstance(entries_node, dict):
        entry_type = entries_node.get("type", "")
        name = entries_node.get("name", "")
        sub_entries = entries_node.get("entries", [])

        if entry_type in ("entries", "section"):
            body = _flatten_entries(sub_entries, depth + 1)
            if name:
                return f"▸ {name}\n{body}"
            return body

        if entry_type == "list":
            items = entries_node.get("items", [])
            lines = []
            for it in items:
                text = _flatten_entries(it, depth + 1)
                lines.append(f"• {text}")
            return "\n".join(lines)

        if entry_type == "table":
            # Simplifié : juste caption + colLabels
            caption = entries_node.get("caption", "")
            cols = entries_node.get("colLabels", [])
            rows = entries_node.get("rows", [])
            lines = []
            if caption:
                lines.append(f"▸ {caption}")
            if cols:
                lines.append("  " + " | ".join(str(c) for c in cols))
            for row in rows[:8]:  # limiter à 8 lignes
                cells = [_flatten_entries(c, depth + 1) for c in row]
                lines.append("  " + " | ".join(cells))
            return "\n".join(lines)

        if entry_type in ("inset", "quote"):
            body = _flatten_entries(sub_entries, depth + 1)
            return f"  [{body}]"

        # Fallback
        body = _flatten_entries(sub_entries, depth + 1)
        if name:
            return f"▸ {name}\n{body}" if body else f"▸ {name}"
        return body

    return str(entries_node)


def get_race_traits(race_name: str, subrace_name: Optional[str] = None) -> list[dict]:
    """
    Retourne la liste des traits raciaux.

    Chaque trait est un dict :
      {"name": str, "text": str, "source": str, "type": "race"|"subrace"}
    """
    entry = get_race_entry(race_name)
    source = entry.get("source", "?")
    traits: list[dict] = []

    for raw in entry.get("entries", []):
        if not isinstance(raw, dict):
            continue
        name = raw.get("name", "")
        if not name:
            continue
        text = _flatten_entries(raw.get("entries", []))
        traits.append({"name": name, "text": text, "source": source, "type": "race"})

    if subrace_name:
        sr = get_subrace_entry(race_name, subrace_name)
        if sr:
            sr_source = sr.get("source", source)
            for raw in sr.get("entries", []):
                if not isinstance(raw, dict):
                    continue
                name = raw.get("name", "")
                if not name:
                    continue
                text = _flatten_entries(raw.get("entries", []))
                traits.append({"name": name, "text": text, "source": sr_source, "type": "subrace"})

    return traits


# ─── Fluff (lore) ─────────────────────────────────────────────────────────────

def get_race_fluff(race_name: str) -> str:
    """
    Retourne le texte de lore (fluff) d'une race depuis fluff-races.json.
    Retourne "" si indisponible.
    """
    try:
        data = _load_fluff_json()
    except Exception:
        return ""

    for item in data.get("raceFluff", []):
        if _normalize(item.get("name", "")) == _normalize(race_name):
            entries = item.get("entries", [])
            return _flatten_entries(entries)[:2000]  # limiter à 2000 chars

    return ""


# ─── Résumé compact (pour prompt LLM) ────────────────────────────────────────

def get_race_prompt_block(race_name: str, subrace_name: Optional[str] = None) -> str:
    """
    Génère un bloc de prompt compact décrivant les traits raciaux d'un personnage.
    Conçu pour être injecté dans les system_messages des agents.
    """
    if not race_name:
        return ""
    try:
        entry = get_race_entry(race_name)
    except Exception:
        return ""

    source = entry.get("source", "?")
    title = race_name
    if subrace_name:
        title += f" ({subrace_name})"

    lines = [f"## Race : {title} [{source}]"]

    # Vitesse
    speed = get_race_speed(race_name)
    speed_str = ", ".join(
        (f"{v} pi." if k == "walk" else f"{k.title()} {v} pi.")
        for k, v in speed.items()
    )
    lines.append(f"Vitesse : {speed_str}")

    # Taille
    sizes = get_race_size(race_name)
    lines.append(f"Taille : {', '.join(sizes)}")

    # Vision
    dv = get_race_darkvision(race_name, subrace_name)
    if dv:
        lines.append(f"Vision dans le noir : {dv} pi.")

    # Bonus de stats
    bonuses = format_ability_bonuses(race_name, subrace_name)
    if bonuses:
        lines.append(f"Bonus de caractéristiques : {', '.join(bonuses)}")

    # Langues
    langs = get_race_languages(race_name, subrace_name)
    if langs:
        lines.append(f"Langues : {', '.join(langs)}")

    # Compétences
    skills = get_race_skill_proficiencies(race_name, subrace_name)
    if skills:
        lines.append(f"Compétences raciales : {', '.join(skills)}")

    # Résistances / immunités
    res = get_race_resistance(race_name, subrace_name)
    if res:
        lines.append(f"Résistances : {', '.join(res)}")
    imm = get_race_immunity(race_name, subrace_name)
    if imm:
        lines.append(f"Immunités : {', '.join(imm)}")

    # Traits — noms seulement pour rester compact
    traits = get_race_traits(race_name, subrace_name)
    if traits:
        trait_names = [t["name"] for t in traits]
        lines.append(f"Traits : {', '.join(trait_names)}")

    return "\n".join(lines) + "\n"
