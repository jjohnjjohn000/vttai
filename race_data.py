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

def split_race_source(formatted_name: str) -> tuple[str, str]:
    """Extrait le nom et la source depuis 'Nom [Source]'. Retourne (Nom, Source)."""
    import re
    match = re.match(r"(.+?)\s+\[(.+)\]$", formatted_name.strip())
    if match:
        return match.group(1), match.group(2)
    return formatted_name.strip(), ""

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
    """Retourne la liste triée des noms de races uniques avec leur source (PHB en priorité).
    Format attendu : 'NomRace [SOURCE]'
    """
    data = _load_races_json()
    seen: set[str] = set()
    result: list[str] = []
    # PHB first
    for r in data.get("race", []):
        name = r.get("name", "")
        source = r.get("source", "")
        if name and name not in seen and source == "PHB":
            seen.add(name)
            result.append(f"{name} [{source}]")
    # Then others
    for r in data.get("race", []):
        name = r.get("name", "")
        source = r.get("source", "")
        if name and name not in seen:
            seen.add(name)
            result.append(f"{name} [{source}]")
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


def get_race_ability_bonuses(race_name: str, subrace_name: Optional[str] = None, source: Optional[str] = None) -> list[dict]:
    """
    Retourne la liste brute des blocs 'ability' de la race (et de la sous-race).

    Chaque bloc est un dict, par exemple :
      {"str": 2}
      {"cha": 2, "choose": {"from": ["str","dex","con","int","wis"], "count": 2}}
      {"choose": {"from": "asi", "count": 2, "amount": 1}}

    Le format brut est conservé car les règles varient beaucoup selon les sources.
    Utilisez format_ability_bonuses() pour un affichage lisible.
    """
    entry = get_race_entry(race_name, source=source)
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


def format_ability_bonuses(race_name: str, subrace_name: Optional[str] = None, source: Optional[str] = None) -> list[str]:
    """
    Retourne une liste de chaînes lisibles pour les bonus de caractéristiques.
    Ex: ["+2 Charisme", "+1 au choix (×2)"]
    """
    blocks = get_race_ability_bonuses(race_name, subrace_name, source=source)
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


def get_race_languages(race_name: str, subrace_name: Optional[str] = None, source: Optional[str] = None) -> list[str]:
    """Retourne la liste de langues lisibles."""
    entry = get_race_entry(race_name, source=source)
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


def get_race_skill_proficiencies(race_name: str, subrace_name: Optional[str] = None, source: Optional[str] = None) -> list[str]:
    """Retourne les maîtrises de compétences issues de la race."""
    entry = get_race_entry(race_name, source=source)
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


def _format_resist_immune(raw_list: list) -> list[str]:
    """Convertit les entrées brutes de résistance/immunité en texte lisible."""
    result = []
    for item in raw_list:
        if isinstance(item, str):
            result.append(item.title())
        elif isinstance(item, dict):
            if "choose" in item:
                choices = item["choose"].get("from", [])
                count = item["choose"].get("count", 1)
                if isinstance(choices, list):
                    lbl = "/".join(str(c).title() for c in choices)
                else:
                    lbl = str(choices).title()
                result.append(f"{count} au choix ({lbl})")
            elif "special" in item:
                result.append(str(item["special"]))
    return result


def get_race_resistance(race_name: str, subrace_name: Optional[str] = None, source: Optional[str] = None) -> list[str]:
    """Retourne les résistances aux dégâts."""
    entry = get_race_entry(race_name, source=source)
    res = list(entry.get("resist", []))
    if subrace_name:
        sr = get_subrace_entry(race_name, subrace_name)
        if sr:
            res += sr.get("resist", [])
    return _format_resist_immune(res)


def get_race_immunity(race_name: str, subrace_name: Optional[str] = None, source: Optional[str] = None) -> list[str]:
    """Retourne les immunités."""
    entry = get_race_entry(race_name, source=source)
    imm = list(entry.get("immune", []))
    if subrace_name:
        sr = get_subrace_entry(race_name, subrace_name)
        if sr:
            imm += sr.get("immune", [])
    return _format_resist_immune(imm)


def get_race_resist_choices(race_name: str, subrace_name: Optional[str] = None, source: Optional[str] = None) -> list[dict]:
    entry = get_race_entry(race_name, source=source)
    res = list(entry.get("resist", []))
    if subrace_name:
        sr = get_subrace_entry(race_name, subrace_name)
        if sr:
            res += sr.get("resist", [])
            
    choices = []
    for item in res:
        if isinstance(item, dict) and "choose" in item:
            from_list = item["choose"].get("from", [])
            count = item["choose"].get("count", 1)
            opts = [str(c).title() for c in from_list] if isinstance(from_list, list) else [str(from_list).title()]
            for i in range(count):
                lbl = "Résistance au choix" if count == 1 else f"Résistance au choix {i+1}"
                choices.append({"label": lbl, "options": opts})
    return choices

def get_race_immune_choices(race_name: str, subrace_name: Optional[str] = None, source: Optional[str] = None) -> list[dict]:
    entry = get_race_entry(race_name, source=source)
    imm = list(entry.get("immune", []))
    if subrace_name:
        sr = get_subrace_entry(race_name, subrace_name)
        if sr:
            imm += sr.get("immune", [])
            
    choices = []
    for item in imm:
        if isinstance(item, dict) and "choose" in item:
            from_list = item["choose"].get("from", [])
            count = item["choose"].get("count", 1)
            opts = [str(c).title() for c in from_list] if isinstance(from_list, list) else [str(from_list).title()]
            for i in range(count):
                lbl = "Immunité au choix" if count == 1 else f"Immunité au choix {i+1}"
                choices.append({"label": lbl, "options": opts})
    return choices


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
            for row in rows:  # On ne limite plus à 8 lignes pour permettre les 10 options de dragons
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

@lru_cache(maxsize=1)
def _get_dynamic_tools() -> list[str]:
    """Extrait dynamiquement les outils du PHB (book-phb.json)."""
    import os, json, re
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    book_path = os.path.join(base_dir, "book", "book-phb.json")
    
    # Fallback minimal de sécurité au cas où le fichier n'existe pas
    fallback_tools = [
        "Alchemist's Supplies", "Bagpipes", "Brewer's Supplies", "Calligrapher's Supplies", "Carpenter's Tools",
        "Cartographer's Tools", "Cobbler's Tools", "Cook's Utensils", "Dice Set", "Disguise Kit", "Dragonchess Set",
        "Drum", "Dulcimer", "Flute", "Forgery Kit", "Glassblower's Tools", "Herbalism Kit", "Horn", "Jeweler's Tools",
        "Leatherworker's Tools", "Lute", "Lyre", "Mason's Tools", "Navigator's Tools", "Painter's Supplies",
        "Pan Flute", "Playing Card Set", "Poisoner's Kit", "Potter's Tools", "Shawm", "Smith's Tools", "Thieves' Tools",
        "Three-Dragon Ante Set", "Tinker's Tools", "Vehicles (Land)", "Vehicles (Water)", "Viol", "Weaver's Tools",
        "Woodcarver's Tools"
    ]
    
    if not os.path.exists(book_path):
        return fallback_tools

    try:
        with open(book_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        # Cherche la section racine "Tools"
        def find_tools_section(node):
            if isinstance(node, dict):
                if node.get("name") == "Tools" and node.get("type") == "section":
                    return node
                for v in node.values():
                    res = find_tools_section(v)
                    if res: return res
            elif isinstance(node, list):
                for item in node:
                    res = find_tools_section(item)
                    if res: return res
            return None
            
        tools_section = find_tools_section(data)
        if not tools_section:
            return fallback_tools
            
        extracted = []
        
        # Parse l'intérieur de la section "Tools"
        def parse_entries(node):
            if isinstance(node, dict):
                # Récupération des catégories isolées (ex: Disguise Kit)
                if node.get("type") == "entries" and "name" in node:
                    name = node["name"]
                    if name not in ["Tools", "Artisan's Tools", "Gaming Set", "Musical Instrument"]:
                        extracted.append(name)
                        
                # Récupération des outils listés dans les tables (ex: Alchemist's supplies)
                if node.get("type") == "table":
                    for row in node.get("rows", []):
                        if row and isinstance(row, list):
                            cell = row[0]
                            if isinstance(cell, str):
                                # Nettoie les tags {@item ...} et les astérisques de note
                                clean = re.sub(r'\{@\w+ ([^}|]+)(?:\|[^}]*)?\}', r'\1', cell)
                                clean = clean.split('*')[0].strip().title()
                                if clean and clean.lower() not in ["item", "cost", "weight"]:
                                    extracted.append(clean)
                                    
                for v in node.values():
                    parse_entries(v)
            elif isinstance(node, list):
                for item in node:
                    parse_entries(item)
                    
        parse_entries(tools_section)
        
        if extracted:
            return sorted(list(set(extracted)))
            
    except Exception as e:
        print(f"[Tools Extraction Error] {e}")
        
    return fallback_tools

@lru_cache(maxsize=1)
def _get_dynamic_feats() -> list[str]:
    """Extrait dynamiquement les dons depuis data/feats.json."""
    import os, json
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    feats_path = os.path.join(base_dir, "data", "feats.json")
    
    # Fallback minimal de sécurité
    fallback_feats = [
        "Actor", "Alert", "Athlete", "Charger", "Crossbow Expert", "Dual Wielder", 
        "Dungeon Delver", "Durable", "Elemental Adept", "Grappler", "Great Weapon Master", 
        "Healer", "Heavily Armored", "Heavy Armor Master", "Inspiring Leader", "Keen Mind", 
        "Lightly Armored", "Linguist", "Lucky", "Mage Slayer", "Magic Initiate", 
        "Martial Adept", "Medium Armor Master", "Mobile", "Moderately Armored", "Mounted Combatant", 
        "Observant", "Polearm Master", "Resilient", "Ritual Caster", "Savage Attacker", 
        "Sentinel", "Sharpshooter", "Shield Master", "Skilled", "Skulker", "Spell Sniper", 
        "Tavern Brawler", "Tough", "War Caster", "Weapon Master"
    ]
    
    if not os.path.exists(feats_path):
        return fallback_feats

    try:
        with open(feats_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        extracted = []
        for feat in data.get("feat", []):
            name = feat.get("name")
            if name:
                extracted.append(name)
                
        if extracted:
            return sorted(list(set(extracted)))
            
    except Exception as e:
        print(f"[Feats Extraction Error] {e}")
        
    return fallback_feats

def extract_trait_choices(text: str) -> list[dict]:
    """
    Analyse le texte d'un trait et retourne une liste de choix requis.
    Chaque choix est un dict: {"label": "Nom du choix", "options": ["Opt1", "Opt2"]}
    """
    import re
    choices = []
    text_lower = text.lower()
    handled_intervals = []
    
    # 1. Choix de la caractéristique d'incantation ou modificateur
    if "intelligence, wisdom, or charisma" in text_lower and ("spellcasting" in text_lower or "modifier" in text_lower or "choose" in text_lower):
        choices.append({
            "label": "Caractéristique",
            "options": ["Intelligence", "Wisdom", "Charisma"]
        })
        
    # 2. Choix basés sur des listes explicites (ex: "cantrips of your choice: dancing lights...")
    keyword_regex = r"cantrip|spell|skill|language|weapon|tool|proficiency|proficiencie|damage type"
    # Capture le dernier mot-clé avant les deux-points pour éviter de fusionner avec un mot lointain
    m_lists = re.finditer(r"\b((?:" + keyword_regex + r")s?)\b((?:(?!\b(?:" + keyword_regex + r")s?\b)[^.:])*):\s*([^.]+)", text, re.IGNORECASE)
    
    found_list = False
    for m in m_lists:
        label_type = m.group(1).lower().rstrip('s') # Enlève le 's' du pluriel
        middle_text = m.group(2).lower()
        options_raw = m.group(3)
        
        # Ignorer les faux positifs très communs (ex: "proficiency bonus")
        if label_type.startswith("proficienc") and "bonus" in middle_text:
            continue
        
        # Validation stricte : vérifier que le texte avant les deux-points implique bien un choix
        colon_index = m.start() + m.group(0).find(':')
        context_before_colon = text[max(0, colon_index - 150):colon_index].lower()
        if not re.search(r"\b(choice|choose|choix|choisissez|select|one of|two of|three of)\b", context_before_colon, re.IGNORECASE):
            # C'est une liste descriptive (ex: capacités d'un Construct), on l'ignore.
            continue
        
        # Recherche dynamique du nombre de choix demandé (ex: "two of the following skills...")
        num_map = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
        count = 1
        
        # On fouille dans TOUT le contexte AVANT les deux-points
        m_num = re.findall(r"\b(one|a|an|two|three|four|five|six|\d+)\b", context_before_colon)
        if m_num:
            # On prend le dernier nombre mentionné avant les deux-points
            last_num_str = m_num[-1]
            count = int(last_num_str) if last_num_str.isdigit() else num_map.get(last_num_str, 1)
        
        # Séparation propre gérant les virgules, les "or/ou", ET les "and/et" (Oxford comma)
        raw_opts = re.split(r",\s*or\s+|,\s*and\s+|,\s*ou\s+|,\s*et\s+|,\s*|\s+or\s+|\s+and\s+|\s+ou\s+|\s+et\s+", options_raw)
        opts = [o.strip().title() for o in raw_opts if len(o.strip()) > 1 and not o.lower().startswith("when") and len(o.strip()) < 50]
        
        if opts:
            lbl_map = {
                "cantrip": "Sort mineur", "spell": "Sort", "skill": "Compétence",
                "language": "Langue", "weapon": "Arme", "tool": "Outil",
                "proficiencie": "Maîtrise", "proficiency": "Maîtrise",
                "damage type": "Type de dégâts"
            }
            label_base = lbl_map.get(label_type, label_type.capitalize())
            
            for i in range(count):
                lbl = f"{label_base} {i+1}" if count > 1 else label_base
                choices.append({
                    "label": lbl,
                    "options": opts
                })
            found_list = True
            handled_intervals.append((m.start(), m.end()))

    # 2.2 Choix avec options (a), (b), (c) explicites
    m_abc = re.search(r"(?:\(\s*a\s*\)|\ba\))\s+([\s\S]+?)\s*(?:,?\s*(?:or|ou)\s*)?(?:\(\s*b\s*\)|\bb\))\s+([\s\S]+?)(?:\s*(?:,?\s*(?:or|ou)\s*)?(?:\(\s*c\s*\)|\bc\))\s+([\s\S]+?))?(?:\.|$)", text, re.IGNORECASE)
    if m_abc and re.search(r"(choice|choix|choose|option)", text, re.IGNORECASE):
        opts = [o.strip() for o in m_abc.groups() if o]
        opts = [re.sub(r"\s+(?:or|ou)$", "", o, flags=re.IGNORECASE).strip().capitalize() for o in opts]
        
        # On limite la taille pour éviter de capturer d'énormes paragraphes par erreur
        if all(len(o) < 150 for o in opts):
            choices.append({
                "label": "Option globale",
                "options": opts
            })
            found_list = True
            handled_intervals.append((m_abc.start(), m_abc.end()))

    # 2.5 Choix ouverts implicites avec gestion de "ou" et analyse contextuelle de la quantité
    # On retire le nombre de la regex stricte pour analyser la phrase dynamiquement
    m_open = re.finditer(r"\b(language|skill|cantrip|tool|feat|weapon)s?(?:\s+proficienc(?:y|ies))?(?:\s+(and|or)\s+(?:[^\s]+\s+){0,5}(language|skill|cantrip|tool|feat|weapon)s?(?:\s+proficienc(?:y|ies))?)?\s+of\s+your\s+choice", text, re.IGNORECASE)
    
    std_options = {
        "language": ["Abyssal", "Celestial", "Common", "Deep Speech", "Draconic", "Dwarvish", "Elvish", "Giant", "Gnomish", "Goblin", "Halfling", "Infernal", "Orc", "Primordial", "Sylvan", "Undercommon"],
        "skill": ["Acrobatics", "Animal Handling", "Arcana", "Athletics", "Deception", "History", "Insight", "Intimidation", "Investigation", "Medicine", "Nature", "Perception", "Performance", "Persuasion", "Religion", "Sleight of Hand", "Stealth", "Survival"],
        "cantrip": ["Acid Splash", "Blade Ward", "Booming Blade", "Chill Touch", "Control Flames", "Create Bonfire", "Dancing Lights", "Eldritch Blast", "Fire Bolt", "Friends", "Frostbite", "Green-Flame Blade", "Guidance", "Gust", "Infestation", "Light", "Lightning Lure", "Mage Hand", "Magic Stone", "Mending", "Message", "Minor Illusion", "Mold Earth", "Poison Spray", "Prestidigitation", "Primal Savagery", "Produce Flame", "Ray of Frost", "Resistance", "Sacred Flame", "Shape Water", "Shillelagh", "Shocking Grasp", "Spare the Dying", "Sword Burst", "Thaumaturgy", "Thunderclap", "Toll the Dead", "True Strike", "Vicious Mockery", "Word of Radiance"],
        "tool": _get_dynamic_tools(),
        "feat": _get_dynamic_feats(),
        "weapon": ["Club", "Dagger", "Greatclub", "Handaxe", "Javelin", "Light Hammer", "Mace", "Quarterstaff", "Sickle", "Spear", "Crossbow, light", "Dart", "Shortbow", "Sling", "Battleaxe", "Flail", "Glaive", "Greataxe", "Greatsword", "Halberd", "Lance", "Longsword", "Maul", "Morningstar", "Pike", "Rapier", "Scimitar", "Shortsword", "Trident", "Warhammer", "Whip", "Blowgun", "Crossbow, hand", "Crossbow, heavy", "Longbow", "Net"]
    }
    
    num_map = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
    
    for m in m_open:
        # Ignore this match if it was already handled by an explicit list (Block 2)
        if any(start <= m.start() <= end for start, end in handled_intervals):
            continue

        choice_type_1 = m.group(1).lower()
        conjunction = m.group(2).lower() if m.group(2) else None
        choice_type_2 = m.group(3).lower() if m.group(3) else None
        
        # Extraction dynamique de la quantité basée sur la phrase précédente
        match_start = m.start()
        context_before = text[max(0, match_start - 150):match_start].lower()
        
        # Limiter l'analyse à la phrase courante pour ignorer les nombres des paragraphes précédents
        sentence_match = re.split(r'[.!?]', context_before)
        if sentence_match:
            context_before = sentence_match[-1]
            
        count = 1
        # On exclut "a/an" pour éviter les faux positifs d'articles, on cherche d'abord les nombres explicites
        m_num = re.findall(r"\b(one|two|three|four|five|six|\d+)\b", context_before)
        if m_num:
            vals = [int(n) if n.isdigit() else num_map.get(n, 1) for n in m_num]
            # S'il y a la notion de "chacun" (each) avec un nombre plus grand défini avant, on le récupère.
            if "each" in context_before or "total" in context_before:
                count = max(vals)
            else:
                count = vals[-1] # Sinon, on prend le dernier nombre lu
        elif re.search(r"\b(a|an)\b", context_before):
            count = 1
            
        opts1 = list(std_options.get(choice_type_1, []))
        label_base1 = {"language": "Langue", "skill": "Compétence", "cantrip": "Sort mineur", "tool": "Outil", "feat": "Don", "weapon": "Arme"}.get(choice_type_1, choice_type_1.capitalize())
        
        if not opts1:
            opts1 = ["Au choix"]

        if conjunction == "or" and choice_type_2:
            opts1 += list(std_options.get(choice_type_2, []))
            label_2 = {"language": "Langue", "skill": "Compétence", "cantrip": "Sort mineur", "tool": "Outil", "feat": "Don", "weapon": "Arme"}.get(choice_type_2, choice_type_2.capitalize())
            label_base1 = f"{label_base1} ou {label_2}"
            
        for i in range(count):
            lbl = f"{label_base1} {i+1}" if count > 1 else label_base1
            choices.append({
                "label": lbl,
                "options": opts1
            })
            found_list = True

        if conjunction == "and" and choice_type_2:
            middle_text = text[m.start(2):m.start(3)].lower()
            m_num2 = re.findall(r"\b(one|two|three|four|five|six|\d+)\b", middle_text)
            count2 = 1
            if m_num2:
                count2 = int(m_num2[-1]) if m_num2[-1].isdigit() else num_map.get(m_num2[-1], 1)
            elif "each" in context_before or "total" in context_before:
                count2 = count
                
            opts2 = list(std_options.get(choice_type_2, []))
            label_base2 = {"language": "Langue", "skill": "Compétence", "cantrip": "Sort mineur", "tool": "Outil", "feat": "Don", "weapon": "Arme"}.get(choice_type_2, choice_type_2.capitalize())
            
            if not opts2:
                opts2 = ["Au choix"]

            for i in range(count2):
                lbl = f"{label_base2} {i+1}" if count2 > 1 else label_base2
                choices.append({
                    "label": lbl,
                    "options": opts2
                })
            
    # 3. Détection de table (ex: Draconic Ancestry)
    found_table = False
    if " | " in text and re.search(r"(choice|choose|choix|choisissez)", text, re.IGNORECASE):
        lines = text.split("\n")
        table_rows = [line.strip() for line in lines if " | " in line]
        if len(table_rows) > 1:
            opts = []
            for row in table_rows[1:]:
                first_col = row.split(" | ")[0].strip()
                # Nettoyer les éventuelles puces
                first_col = re.sub(r"^[^a-zA-Z0-9]+", "", first_col).strip()
                if first_col and len(first_col) < 30:
                    opts.append(first_col)
            if opts and len(opts) > 1:
                header_col = table_rows[0].split(" | ")[0].strip()
                header_col = re.sub(r"^[^a-zA-Z0-9]+", "", header_col).strip()
                label = header_col if header_col else "Choix (Tableau)"
                choices.append({
                    "label": label,
                    "options": opts
                })
                found_table = True
            
    # 4. Fallback générique si rien n'a été trouvé mais qu'un choix est requis
    if not found_list and not found_table and not choices:
        # Exclut les tournures verbales comme "choose to...", "choose whether..."
        m_gen = re.search(r"(?:choice|choix|choose|choisissez)(?:\s+of)?\s*:?\s*(?!to\b|whether\b|if\b)([^.]+)", text, re.IGNORECASE)
        if m_gen:
            raw_opts = re.split(r",\s*or\s+|,\s*ou\s+|,\s*|\s+or\s+|\s+ou\s+", m_gen.group(1))
            opts = [o.strip().title() for o in raw_opts if len(o.strip()) > 2 and not o.lower().startswith("when")]
            # Filtre additionnel : si l'option détectée est trop longue (ex: phrase entière), ce n'est pas un vrai choix.
            opts = [o for o in opts if len(o) < 30]
            
            # Si on n'a qu'une seule option et pas de ":" pour indiquer une liste, c'est probablement un faux positif ("you can choose a target...")
            if opts and (len(opts) > 1 or ":" in m_gen.group(0)):
                choices.append({
                    "label": "Choix",
                    "options": opts
                })
                
    return choices

def get_race_traits(race_name: str, subrace_name: Optional[str] = None, source: Optional[str] = None) -> list[dict]:
    """
    Retourne la liste des traits raciaux.

    Chaque trait est un dict :
      {"name": str, "text": str, "source": str, "type": "race"|"subrace"}
    """
    entry = get_race_entry(race_name, source=source)
    src = entry.get("source", "?")
    traits: list[dict] = []

    for raw in entry.get("entries", []):
        if not isinstance(raw, dict):
            continue
        name = raw.get("name", "")
        if not name:
            continue
        text = _flatten_entries(raw.get("entries", []))
        traits.append({"name": name, "text": text, "source": src, "type": "race"})

    if subrace_name:
        sr = get_subrace_entry(race_name, subrace_name)
        if sr:
            sr_source = sr.get("source", src)
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

def get_race_fluff(race_name: str, source: str = "") -> str:
    """
    Retourne le texte de lore (fluff) d'une race depuis fluff-races.json.
    Retourne "" si indisponible.
    """
    try:
        data = _load_fluff_json()
    except Exception:
        return ""

    def _find_fluff(name: str, src: str = ""):
        if src:
            for item in data.get("raceFluff", []):
                if _normalize(item.get("name", "")) == _normalize(name) and _normalize(item.get("source", "")) == _normalize(src):
                    return item
        for item in data.get("raceFluff", []):
            if _normalize(item.get("name", "")) == _normalize(name):
                return item
        return None

    item = _find_fluff(race_name, source)
    if not item:
        return ""

    entries = item.get("entries", [])
    
    # Résolution des copies (ex: Aasimar (Protector) -> Aasimar)
    if not entries and "_copy" in item:
        copy_name = item["_copy"].get("name", "")
        if copy_name:
            copy_item = _find_fluff(copy_name)
            if copy_item:
                entries = copy_item.get("entries", [])
                
                # Gestion des ajouts (_mod) pour la copie
                _mod = item["_copy"].get("_mod", {})
                if "entries" in _mod:
                    mode = _mod["entries"].get("mode", "")
                    items = _mod["entries"].get("items", {})
                    if mode == "prependArr":
                        if isinstance(items, dict):
                            entries = [items] + entries
                        elif isinstance(items, list):
                            entries = items + entries

    if entries:
        return _flatten_entries(entries)[:4000]

    return ""


# ─── Résumé compact (pour prompt LLM) ────────────────────────────────────────

def get_race_prompt_block(race_name: str, subrace_name: Optional[str] = None, source: Optional[str] = None) -> str:
    """
    Génère un bloc de prompt compact décrivant les traits raciaux d'un personnage.
    Conçu pour être injecté dans les system_messages des agents.
    """
    if not race_name:
        return ""
    try:
        entry = get_race_entry(race_name, source=source)
    except Exception:
        return ""

    source = entry.get("source", "?")
    title = race_name
    if subrace_name:
        title += f" ({subrace_name})"

    lines = [f"## Race : {title} [{source}]"]

    # Vitesse
    speed = get_race_speed(race_name)
    walk_spd = speed.get("walk", 30)
    speed_parts = []
    for k, v in speed.items():
        val = walk_spd if v is True else v
        if k == "walk":
            speed_parts.append(f"{val} pi.")
        else:
            speed_parts.append(f"{k.title()} {val} pi.")
    lines.append(f"Vitesse : {', '.join(speed_parts)}")

    # Taille
    sizes = get_race_size(race_name)
    lines.append(f"Taille : {', '.join(sizes)}")

    # Vision
    dv = get_race_darkvision(race_name, subrace_name)
    if dv:
        lines.append(f"Vision dans le noir : {dv} pi.")

    # Bonus de stats
    bonuses = format_ability_bonuses(race_name, subrace_name, source=source)
    if bonuses:
        lines.append(f"Bonus de caractéristiques : {', '.join(bonuses)}")

    # Langues
    langs = get_race_languages(race_name, subrace_name, source=source)
    if langs:
        lines.append(f"Langues : {', '.join(langs)}")

    # Compétences
    skills = get_race_skill_proficiencies(race_name, subrace_name, source=source)
    if skills:
        lines.append(f"Compétences raciales : {', '.join(skills)}")

    # Résistances / immunités
    res = get_race_resistance(race_name, subrace_name, source=source)
    if res:
        lines.append(f"Résistances : {', '.join(res)}")
    imm = get_race_immunity(race_name, subrace_name, source=source)
    if imm:
        lines.append(f"Immunités : {', '.join(imm)}")

    # Traits — noms seulement pour rester compact
    traits = get_race_traits(race_name, subrace_name)
    if traits:
        trait_names = [t["name"] for t in traits]
        lines.append(f"Traits : {', '.join(trait_names)}")

    return "\n".join(lines) + "\n"
