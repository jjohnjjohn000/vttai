import json
import os
from functools import lru_cache

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

@lru_cache(maxsize=1)
def _load_backgrounds() -> dict:
    path = os.path.join(_DATA_DIR, "backgrounds.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_available_backgrounds() -> list[str]:
    """Retourne la liste triée des noms d'historiques disponibles."""
    try:
        data = _load_backgrounds()
        return sorted(list(set(bg.get("name", "") for bg in data.get("background", []) if bg.get("name"))))
    except Exception:
        return []


def get_background_entry(bg_name: str) -> dict:
    """Récupère l'entrée brute d'un historique (résout les références _copy)."""
    data = _load_backgrounds()
    bg_list = data.get("background", [])
    
    def _find(name, source=None):
        for b in bg_list:
            if b.get("name", "").lower() == name.lower():
                if source and b.get("source") != source:
                    continue
                return b
        return {}

    entry = _find(bg_name)
    if not entry:
        return {}
        
    if "_copy" in entry:
        copy_info = entry["_copy"]
        base_entry = _find(copy_info.get("name"), copy_info.get("source"))
        if base_entry:
            import copy
            merged = copy.deepcopy(base_entry)
            merged.update(entry)
            return merged
            
    return entry


def get_background_fixed_proficiencies(bg_name: str) -> dict:
    """Retourne les maîtrises fixes (non au choix) d'un historique."""
    entry = get_background_entry(bg_name)
    skills = []
    tools = []
    langs = []
    
    for sp in entry.get("skillProficiencies", []):
        for k, v in sp.items():
            if k not in ["choose", "any"] and v is True:
                skills.append(k.title())
                
    for tp in entry.get("toolProficiencies", []):
        for k, v in tp.items():
            if k not in ["choose", "anyArtisansTool", "anyMusicalInstrument", "anyGamingSet", "anyTool"] and v is True:
                tools.append(k.title())
                
    for lp in entry.get("languageProficiencies", []):
        for k, v in lp.items():
            if k not in ["choose", "anyStandard", "any", "anyExotic", "anyLanguage"] and v is True:
                langs.append(k.title())
                
    return {"skills": sorted(skills), "tools": sorted(tools), "languages": sorted(langs)}


def get_background_proficiency_choices(bg_name: str) -> list[dict]:
    """Analyse et retourne les choix de maîtrises requis par l'historique."""
    entry = get_background_entry(bg_name)
    choices = []
    
    # ── Choix de Compétences ──
    for sp in entry.get("skillProficiencies", []):
        if "choose" in sp:
            from_list = sp["choose"].get("from", [])
            count = sp["choose"].get("count", 1)
            opts = sorted([s.title() for s in from_list])
            for i in range(count):
                lbl = f"Compétence {i+1}" if count > 1 else "Compétence"
                choices.append({"label": lbl, "options": opts})
        for k, v in sp.items():
            if k == "any":
                count = v if isinstance(v, int) else 1
                opts = ["Acrobatics", "Animal Handling", "Arcana", "Athletics", "Deception", "History", "Insight", "Intimidation", "Investigation", "Medicine", "Nature", "Perception", "Performance", "Persuasion", "Religion", "Sleight of Hand", "Stealth", "Survival"]
                for i in range(count):
                    choices.append({"label": f"Compétence libre {i+1}" if count > 1 else "Compétence libre", "options": opts})

    # ── Choix d'Outils ──
    for tp in entry.get("toolProficiencies", []):
        if "choose" in tp:
            from_list = tp["choose"].get("from", [])
            count = tp["choose"].get("count", 1)
            opts = []
            for item in from_list:
                if item in ["anyArtisansTool", "artisan's tools"]:
                    opts.extend(["Alchemist's Supplies", "Brewer's Supplies", "Calligrapher's Supplies", "Carpenter's Tools", "Cartographer's Tools", "Cobbler's Tools", "Cook's Utensils", "Glassblower's Tools", "Jeweler's Tools", "Leatherworker's Tools", "Mason's Tools", "Painter's Supplies", "Potter's Tools", "Smith's Tools", "Tinker's Tools", "Weaver's Tools", "Woodcarver's Tools"])
                elif item in ["anyMusicalInstrument", "musical instrument"]:
                    opts.extend(["Bagpipes", "Drum", "Dulcimer", "Flute", "Lute", "Lyre", "Horn", "Pan Flute", "Shawm", "Viol"])
                elif item in ["anyGamingSet", "gaming set"]:
                    opts.extend(["Dice Set", "Dragonchess Set", "Playing Card Set", "Three-Dragon Ante Set"])
                elif item == "vehicles (land)":
                    opts.append("Vehicles (Land)")
                elif item == "vehicles (water)":
                    opts.append("Vehicles (Water)")
                else:
                    opts.append(item.title())
            opts = sorted(list(set(opts)))
            for i in range(count):
                lbl = f"Outil {i+1}" if count > 1 else "Outil"
                choices.append({"label": lbl, "options": opts})
                
        for k, v in tp.items():
            if k in ["anyArtisansTool", "artisan's tools"]:
                opts = sorted(["Alchemist's Supplies", "Brewer's Supplies", "Calligrapher's Supplies", "Carpenter's Tools", "Cartographer's Tools", "Cobbler's Tools", "Cook's Utensils", "Glassblower's Tools", "Jeweler's Tools", "Leatherworker's Tools", "Mason's Tools", "Painter's Supplies", "Potter's Tools", "Smith's Tools", "Tinker's Tools", "Weaver's Tools", "Woodcarver's Tools"])
                for i in range(v if isinstance(v, int) else 1):
                    lbl = f"Outil d'artisan {i+1}" if (v if isinstance(v, int) else 1) > 1 else "Outil d'artisan"
                    choices.append({"label": lbl, "options": opts})
            elif k in ["anyMusicalInstrument", "musical instrument"]:
                opts = sorted(["Bagpipes", "Drum", "Dulcimer", "Flute", "Lute", "Lyre", "Horn", "Pan Flute", "Shawm", "Viol"])
                for i in range(v if isinstance(v, int) else 1):
                    lbl = f"Instrument {i+1}" if (v if isinstance(v, int) else 1) > 1 else "Instrument"
                    choices.append({"label": lbl, "options": opts})
            elif k in ["anyGamingSet", "gaming set"]:
                opts = sorted(["Dice Set", "Dragonchess Set", "Playing Card Set", "Three-Dragon Ante Set"])
                for i in range(v if isinstance(v, int) else 1):
                    lbl = f"Jeu {i+1}" if (v if isinstance(v, int) else 1) > 1 else "Jeu"
                    choices.append({"label": lbl, "options": opts})

    # ── Choix de Langues ──
    for lp in entry.get("languageProficiencies", []):
        if "choose" in lp:
            from_list = lp["choose"].get("from", [])
            count = lp["choose"].get("count", 1)
            opts = sorted([l.title() for l in from_list])
            for i in range(count):
                lbl = f"Langue {i+1}" if count > 1 else "Langue"
                choices.append({"label": lbl, "options": opts})
        for k, v in lp.items():
            if k in ["anyStandard", "any", "anyExotic", "anyLanguage"]:
                count = v if isinstance(v, int) else 1
                opts = sorted(["Abyssal", "Celestial", "Common", "Deep Speech", "Draconic", "Dwarvish", "Elvish", "Giant", "Gnomish", "Goblin", "Halfling", "Infernal", "Orc", "Primordial", "Sylvan", "Undercommon"])
                for i in range(count):
                    lbl = f"Langue libre {i+1}" if count > 1 else "Langue libre"
                    choices.append({"label": lbl, "options": opts})

    return choices


def get_background_features(bg_name: str) -> list[dict]:
    """Retourne les capacités spécifiques de l'historique."""
    entry = get_background_entry(bg_name)
    features = []
    
    try:
        from race_data import _flatten_entries
    except ImportError:
        def _flatten_entries(entries, depth=0): return str(entries)
        
    for e in entry.get("entries", []):
        if isinstance(e, dict) and e.get("name", "").startswith("Feature:"):
            text = _flatten_entries(e.get("entries", []))
            features.append({"name": e["name"], "text": text})
            
    return features