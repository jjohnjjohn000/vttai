"""
spell_data.py — Chargeur de sorts 5etools + widget SpellPickerDialog.

Charge tous les fichiers spells-*.json depuis le dossier ./spells/ (ou le
répertoire courant en fallback).  Les données fluff viennent de
fluff-spells-*.json dans le même dossier.

API publique :
  load_spells()                   → charge le cache (lazy, thread-safe)
  search_spells(query, n=12)      → liste de noms correspondants
  get_spell(name)                 → dict normalisé (ou None)
  format_spell_card(spell)        → str lisible pour le LLM / la fiche
  SpellPickerDialog(parent, cb)   → modal de sélection de sort

Format du dict retourné par get_spell() :
  {
      "name":        str,
      "level":       int,           # 0 = tour de magie
      "school":      str,           # nom complet FR
      "school_code": str,           # lettre 5etools
      "cast_time":   str,
      "range":       str,
      "components":  str,
      "duration":    str,
      "concentration": bool,
      "ritual":      bool,
      "description": str,           # texte brut nettoyé
      "source":      str,
  }
"""

import glob
import json
import os
import re
import tkinter as tk
from tkinter import scrolledtext
from typing import Callable

# ─── Répertoire des sorts ─────────────────────────────────────────────────────
_BASE_DIR   = os.path.dirname(__file__)
_SPELLS_DIR = os.path.join(_BASE_DIR, "spells")
if not os.path.isdir(_SPELLS_DIR):
    _SPELLS_DIR = _BASE_DIR   # fallback : même dossier que les scripts

# ─── Cache ────────────────────────────────────────────────────────────────────
_SPELL_DATA:  dict[str, dict] = {}   # name.lower() → dict normalisé
_SPELL_NAMES: list[str]       = []   # liste triée des noms (pour l'autocomplete)

# ─── Index des sources ────────────────────────────────────────────────────────
# sources.json : {SOURCE_CODE: {spell_name: {class:[...], classVariant:[...]}}}
# Chargé lazily par load_sources_index().
_SOURCES_INDEX: dict = {}      # source_code → {spell_name → {...}}
_SOURCES_FLAT:  dict = {}      # spell_name.lower() → source_code
_SOURCES_FILE   = os.path.join(_BASE_DIR, "sources.json")
_SOURCES_LOCK   = __import__("threading").Lock()

def load_sources_index() -> dict:
    """Charge sources.json une seule fois (thread-safe).
    Retourne {source_code: {spell_name: {class: [...]}}}."""
    global _SOURCES_INDEX, _SOURCES_FLAT
    if _SOURCES_INDEX:
        return _SOURCES_INDEX
    with _SOURCES_LOCK:
        if _SOURCES_INDEX:
            return _SOURCES_INDEX
        try:
            path = _SOURCES_FILE
            if not os.path.exists(path):
                path = os.path.join(_SPELLS_DIR, "sources.json")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    _SOURCES_INDEX = json.load(f)
                # Build flat reverse-index: spell_name.lower() → source_code
                for src_code, spells in _SOURCES_INDEX.items():
                    for spell_name in spells:
                        _SOURCES_FLAT[spell_name.lower()] = src_code
                print(f"[SpellData] sources.json chargé — {len(_SOURCES_INDEX)} sources, "
                      f"{len(_SOURCES_FLAT)} sorts indexés")
            else:
                print("[SpellData] sources.json introuvable")
        except Exception as e:
            print(f"[SpellData] Erreur chargement sources.json : {e}")
    return _SOURCES_INDEX


def get_source_for_spell(spell_name: str) -> str | None:
    """Retourne le code source (ex: 'PHB', 'XGE') d'un sort, ou None."""
    load_sources_index()
    return _SOURCES_FLAT.get(spell_name.strip().lower())


def get_class_spell_names(class_name: str) -> list[str]:
    """Retourne tous les noms de sorts disponibles pour une classe D&D,
    toutes sources confondues (PHB, XGE, TCE…).
    Lit sources.json dynamiquement — aucun sort hardcodé.
    """
    load_sources_index()
    result = []
    for src_code, spells in _SOURCES_INDEX.items():
        for spell_name, spell_data in spells.items():
            classes = spell_data.get("class", []) + spell_data.get("classVariant", [])
            if any(c.get("name", "").lower() == class_name.lower() for c in classes):
                if spell_name not in result:
                    result.append(spell_name)
    return sorted(result)

# ─── Correspondances école ────────────────────────────────────────────────────
_SCHOOL_FR = {
    "A": "Abjuration",
    "C": "Invocation",
    "D": "Divination",
    "E": "Enchantement",
    "V": "Évocation",
    "I": "Illusion",
    "N": "Nécromancie",
    "T": "Transmutation",
    "P": "Divination",
}
_SCHOOL_COLOR = {
    "Abjuration":   "#64b5f6",
    "Invocation":   "#81c784",
    "Divination":   "#ce93d8",
    "Enchantement": "#f06292",
    "Évocation":    "#ff8a65",
    "Illusion":     "#a5d6a7",
    "Nécromancie":  "#e57373",
    "Transmutation":"#ffcc80",
}
_SCHOOL_ICON = {
    "Abjuration":   "🛡",
    "Invocation":   "🌀",
    "Divination":   "👁",
    "Enchantement": "💞",
    "Évocation":    "🔥",
    "Illusion":     "✨",
    "Nécromancie":  "💀",
    "Transmutation":"⚗",
}

_TIME_FR = {
    "action":        "1 action",
    "bonus":         "1 action bonus",
    "reaction":      "1 réaction",
    "minute":        "min",
    "hour":          "h",
    "round":         "round",
}


# ─── Nettoyage texte 5etools ──────────────────────────────────────────────────

def _clean(text: str) -> str:
    """Supprime les tags {@type content} → content."""
    text = re.sub(r'\{@\w+\s*([^|}]*)[^}]*\}', r'\1', str(text))
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _flatten_entries(entries: list, depth: int = 0) -> str:
    """Aplatit récursivement une liste d'entries 5etools en texte."""
    parts = []
    indent = "  " * depth
    for e in entries:
        if isinstance(e, str):
            parts.append(indent + _clean(e))
        elif isinstance(e, dict):
            etype = e.get("type", "")
            name  = e.get("name", "")
            if etype in ("entries", "inset", "insetReadaloud"):
                sub = _flatten_entries(e.get("entries", []), depth)
                if name:
                    parts.append(f"{indent}▸ {_clean(name)}: {sub}")
                else:
                    parts.append(sub)
            elif etype == "list":
                for item in e.get("items", []):
                    if isinstance(item, str):
                        parts.append(f"{indent}• {_clean(item)}")
                    elif isinstance(item, dict):
                        sub = " ".join(
                            str(x) for x in item.get("entries", [])
                            if isinstance(x, str)
                        )
                        iname = item.get("name", "")
                        parts.append(f"{indent}• {_clean(iname)}: {_clean(sub)}"
                                     if iname else f"{indent}• {_clean(sub)}")
            elif etype == "table":
                caption = e.get("caption", "")
                if caption:
                    parts.append(f"{indent}[Table: {_clean(caption)}]")
            else:
                sub_entries = e.get("entries", [])
                if sub_entries:
                    parts.append(_flatten_entries(sub_entries, depth))
    return "\n".join(p for p in parts if p)


# ─── Formatage d'un sort ──────────────────────────────────────────────────────

def _fmt_time(time_list: list) -> str:
    if not time_list:
        return "?"
    t = time_list[0]
    n    = t.get("number", 1)
    unit = t.get("unit", "")
    cond = t.get("condition", "")
    base = _TIME_FR.get(unit, unit)
    if unit == "action" and n == 1:
        result = "1 action"
    elif unit == "bonus":
        result = "1 action bonus"
    elif unit == "reaction":
        result = "1 réaction"
    elif unit in ("minute", "hour", "round"):
        result = f"{n} {base}"
    else:
        result = f"{n} {base}"
    if cond:
        result += f" ({_clean(cond[:50])})"
    return result


def _fmt_range(rng: dict) -> str:
    if not rng:
        return "?"
    rtype = rng.get("type", "")
    if rtype == "special":
        return "Spéciale"
    if rtype == "point":
        dist = rng.get("distance", {})
        dtype = dist.get("type", "")
        amt   = dist.get("amount", "")
        if dtype == "self":   return "Personnelle"
        if dtype == "touch":  return "Contact"
        if dtype == "sight":  return "Ligne de mire"
        if dtype == "unlimited": return "Illimitée"
        unit = "m" if dtype in ("feet",) else dtype
        if dtype == "feet":
            metres = round(int(amt or 0) * 0.3)
            return f"{metres} m"
        return f"{amt} {unit}"
    if rtype in ("radius", "cone", "sphere", "cube", "line"):
        dist = rng.get("distance", {})
        amt  = dist.get("amount", "")
        metres = round(int(amt or 0) * 0.3) if dist.get("type") == "feet" else amt
        labels = {"radius": "Rayon", "cone": "Cône", "sphere": "Sphère",
                  "cube": "Cube", "line": "Ligne"}
        return f"Personnelle ({labels.get(rtype, rtype)} {metres} m)"
    return rtype.capitalize()


def _fmt_components(comp: dict) -> str:
    if not comp:
        return ""
    parts = []
    if comp.get("v"):  parts.append("V")
    if comp.get("s"):  parts.append("S")
    if comp.get("m"):
        mat = comp["m"]
        mat_str = mat if isinstance(mat, str) else mat.get("text", "")
        parts.append(f"M ({_clean(mat_str[:60])}{'…' if len(str(mat_str)) > 60 else ''})")
    if comp.get("r"):  parts.append("R")
    return ", ".join(parts)


def _fmt_duration(dur_list: list) -> tuple[str, bool]:
    """Retourne (texte durée, est_concentration)."""
    if not dur_list:
        return "?", False
    d = dur_list[0]
    dtype = d.get("type", "")
    conc  = d.get("concentration", False)
    if dtype == "instant":
        return "Instantanée", False
    if dtype == "permanent":
        return "Permanente", False
    if dtype == "special":
        return "Spéciale", conc
    if dtype == "timed":
        inner = d.get("duration", {})
        n    = inner.get("amount", 1)
        unit = inner.get("type", "")
        unit_fr = {"round": "round", "minute": "min", "hour": "h",
                   "day": "jour", "year": "an"}.get(unit, unit)
        txt = f"{n} {unit_fr}" + ("s" if n > 1 and unit_fr not in ("min", "h") else "")
        if conc:
            txt = f"Concentration, {txt}"
        return txt, conc
    return dtype, conc


def _normalize_spell(raw: dict) -> dict:
    """Convertit un sort brut 5etools en dict normalisé."""
    school_code = raw.get("school", "V")
    school      = _SCHOOL_FR.get(school_code, school_code)
    conc, ritual = False, False

    dur_txt, conc = _fmt_duration(raw.get("duration", []))
    ritual = bool(raw.get("meta", {}).get("ritual", False))

    entries_raw = raw.get("entries", [])
    entries_higher = raw.get("entriesHigherLevel", [])
    desc = _flatten_entries(entries_raw)
    if entries_higher:
        higher_txt = _flatten_entries(entries_higher)
        if higher_txt:
            desc += "\n▸ Aux niveaux supérieurs : " + higher_txt

    return {
        "name":          raw.get("name", ""),
        "level":         raw.get("level", 0),
        "school":        school,
        "school_code":   school_code,
        "cast_time":     _fmt_time(raw.get("time", [])),
        "cast_time_raw": raw.get("time", []),
        "range":         _fmt_range(raw.get("range", {})),
        "components":    _fmt_components(raw.get("components", {})),
        "duration":      dur_txt,
        "concentration": conc,
        "ritual":        ritual,
        "description":   desc,
        "source":        raw.get("source", "?"),
        "saving_throw":  raw.get("savingThrow", []),
        "damage_inflict": raw.get("damageInflict", []),
        "spell_attack":  raw.get("spellAttack", []),
        "entries":       entries_raw,
        "entries_higher": entries_higher,
    }


# ─── Chargement ──────────────────────────────────────────────────────────────

def load_spells():
    """Charge tous les fichiers spells-*.json détectés dans le dossier spells/.
    La liste des sources est pilotée par sources.json (aucun nom hardcodé).
    Thread-safe (double-check locking).
    """
    global _SPELL_DATA, _SPELL_NAMES
    if _SPELL_DATA:
        return

    # ── 1. Charger sources.json pour connaître les codes sources attendus ─────
    load_sources_index()
    source_codes = list(_SOURCES_INDEX.keys()) if _SOURCES_INDEX else []

    # ── 2. Construire la liste des fichiers à charger ─────────────────────────
    # Priorité : fichiers nommés d'après les codes sources connus,
    # puis glob fallback pour attraper les fichiers inconnus de sources.json.
    seen = set()
    files = []
    for code in source_codes:
        for d in (_SPELLS_DIR, _BASE_DIR):
            candidate = os.path.join(d, f"spells-{code.lower()}.json")
            if os.path.exists(candidate) and candidate not in seen:
                files.append(candidate)
                seen.add(candidate)
    # Glob fallback — attrape tout fichier spells-*.json non encore chargé
    for pat in (os.path.join(_SPELLS_DIR, "spells-*.json"),
                os.path.join(_BASE_DIR,   "spells-*.json")):
        for p in sorted(glob.glob(pat)):
            if p not in seen:
                files.append(p)
                seen.add(p)

    if not files:
        print(f"[SpellData] Aucun fichier spells-*.json trouvé dans {_SPELLS_DIR}")
        return

    total = 0
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for raw in data.get("spell", []):
                name = raw.get("name", "")
                if not name:
                    continue
                norm = _normalize_spell(raw)
                _SPELL_DATA[name.lower()] = norm
                total += 1
            print(f"[SpellData] Chargé {len(data.get('spell',[]))} sorts : {os.path.basename(path)}")
        except Exception as e:
            print(f"[SpellData] Erreur {path}: {e}")

    _SPELL_NAMES = sorted(_SPELL_DATA.keys())
    print(f"[SpellData] Total : {total} sorts en cache")


def search_spells(query: str, max_results: int = 14) -> list[str]:
    """Retourne jusqu'à max_results noms de sorts correspondant à la requête."""
    load_spells()
    if not query:
        return [v["name"] for v in list(_SPELL_DATA.values())[:max_results]]
    q = query.lower().strip()
    exact = [v["name"] for k, v in _SPELL_DATA.items() if k.startswith(q)]
    fuzzy = [v["name"] for k, v in _SPELL_DATA.items() if q in k and not k.startswith(q)]
    combined = exact + fuzzy
    # déduplication ordre-stable
    seen, out = set(), []
    for n in combined:
        if n not in seen:
            seen.add(n)
            out.append(n)
        if len(out) >= max_results:
            break
    return out


def get_spell(name: str) -> dict | None:
    """Retourne le dict normalisé d'un sort (None si introuvable)."""
    load_spells()
    return _SPELL_DATA.get(name.lower())


def format_spell_card(sp: dict) -> str:
    """Formate un sort en texte lisible pour le LLM ou l'affichage."""
    lvl_str = "Tour de magie" if sp["level"] == 0 else f"Niveau {sp['level']}"
    conc_str = " [Concentration]" if sp["concentration"] else ""
    ritual_str = " [Rituel]" if sp["ritual"] else ""
    lines = [
        f"{'─'*40}",
        f"✨ {sp['name']}  ({lvl_str} — {sp['school']}){conc_str}{ritual_str}",
        f"   Incantation : {sp['cast_time']}  |  Portée : {sp['range']}",
        f"   Composantes : {sp['components']}  |  Durée : {sp['duration']}",
        f"   Source      : {sp['source']}",
        f"{'─'*40}",
        sp["description"],
    ]
    return "\n".join(lines)


# ─── Extraction de l'expression de dégâts ────────────────────────────────────

# Mots-nombres EN + FR → int
_NUM_WORDS: dict[str, int] = {
    "one": 1,    "two": 2,    "three": 3, "four": 4,  "five": 5,
    "six": 6,    "seven": 7,  "eight": 8, "nine": 9,  "ten": 10,
    "un": 1,     "une": 1,    "deux": 2,  "trois": 3, "quatre": 4,
    "cinq": 5,   "sept": 7,   "huit": 8,  "neuf": 9,  "dix": 10,
}

# Noms de projectiles EN + FR — avec pluriel optionnel (dart / darts, missile / missiles…)
_PROJ_NOUN_RE = re.compile(
    r'\b(dart|missile|ray|bolt|beam|shard|arrow|orb|mote|needle|'
    r'fléchette|projectile|flèche|rayon|trait|éclat)s?\b',
    re.IGNORECASE,
)

# Expression de dés : 2d6, 1d4+1, 8d6 …
_DICE_EXPR_RE = re.compile(r'(\d+)d(\d+)(?:[+\-](\d+))?', re.IGNORECASE)

# Ordinals anglais courants → int
_ORDINAL_RE = re.compile(r'(\d+)(?:st|nd|rd|th)', re.IGNORECASE)


def _parse_number(token: str) -> int | None:
    """Mot ou chiffre → int, None si échec."""
    t = token.strip().lower()
    if t.isdigit():
        return int(t)
    return _NUM_WORDS.get(t)


def _ordinal_to_int(text: str) -> int | None:
    """'1st' / '2nd' / '3rd' / '4th' … → int, None si échec."""
    m = _ORDINAL_RE.search(text)
    return int(m.group(1)) if m else None


def get_spell_damage_expr(spell_name: str, spell_level: int) -> str | None:
    """
    Retourne l'expression de dés de dégâts complète d'un sort
    au niveau d'emplacement donné, lue depuis les données 5etools.

    Deux cas traités :

    ① Sort à projectiles multiples — ex. Magic Missile
      Description contient "N [fléchettes/missiles/…] … chaque … XdY+Z dégâts"
      Montée en niveau : "one more missile for each slot level above Nth"
      → niveau 1 : "3d4+3"   niveau 2 : "4d4+4"   etc.

    ② Sort à dégâts directs — ex. Boule de Feu / Fireball
      Description contient "XdY [fire] damage"
      Montée en niveau : "1d6 for each slot level above 3rd"
      → niveau 3 : "8d6"   niveau 4 : "9d6"   etc.

    Retourne None si aucune expression de dés n'est trouvable.
    """
    sp = get_spell(spell_name)
    if sp is None:
        return None

    desc         = sp.get("description", "")
    entries_raw  = sp.get("entries_higher", [])
    higher_txt   = _flatten_entries(entries_raw) if entries_raw else ""
    base_level   = int(sp.get("level", 1))

    # ── 1. Trouver la première expression de dés dans la description ──────────
    dice_m = _DICE_EXPR_RE.search(desc)
    if not dice_m:
        return None

    base_n      = int(dice_m.group(1))          # ex. 1  (dés par projectile)
    dice_sides  = int(dice_m.group(2))           # ex. 4
    dice_bonus  = int(dice_m.group(3)) if dice_m.group(3) else 0  # ex. 1

    # ── 2. Sort multi-projectiles ? ───────────────────────────────────────────
    # Heuristique : chercher "N [adjectif?] <projectile>" AVANT la 1ère formule
    # de dés (i.e. la description parle d'abord du nombre puis des dégâts)
    _num_alts = "|".join(
        list(_NUM_WORDS.keys()) + [r"\d+"]
    )
    proj_count_re = re.compile(
        rf'(?P<nb>{_num_alts})'
        r'(?:\s+\w+)?'          # adjectif optionnel ("glowing", "magical"…)
        r'\s+' + _PROJ_NOUN_RE.pattern,
        re.IGNORECASE,
    )
    proj_m = proj_count_re.search(desc[: dice_m.start()])

    # Vérifier aussi "each [noun] deal" (confirme le modèle multi-proj)
    each_re = re.compile(
        r'\beach\s+' + _PROJ_NOUN_RE.pattern + r'.{0,60}?deal',
        re.IGNORECASE | re.DOTALL,
    )
    is_multi = proj_m is not None or each_re.search(desc) is not None

    if is_multi and proj_m:
        base_count = _parse_number(proj_m.group("nb")) or 1

        # ── Montée en niveau : projectiles supplémentaires ────────────────────
        # Cherche : "one more <noun> for each slot level above Nth"
        #        ou : "one additional <noun> for each slot level above Nth"
        scale_re = re.compile(
            r'one\s+(?:more|additional)\s+\w+\s+for\s+each\s+slot\s+level\s+above\s+'
            r'(?P<above>\d+(?:st|nd|rd|th)?)',
            re.IGNORECASE,
        )
        scale_m = scale_re.search(higher_txt)
        extra_count = 0
        if scale_m:
            above_level = _ordinal_to_int(scale_m.group("above")) or base_level
            extra_count = max(0, spell_level - above_level)

        total_count = base_count + extra_count
        total_n     = total_count * base_n
        total_bonus = total_count * dice_bonus

        if total_bonus > 0:
            return f"{total_n}d{dice_sides}+{total_bonus}"
        return f"{total_n}d{dice_sides}"

    # ── 3. Sort à dégâts directs — montée en niveau ───────────────────────────
    # Cherche : "Xd6 for each slot level above Yth"
    extra_n = 0
    if higher_txt:
        scale_direct_re = re.compile(
            r'(\d+)d(\d+)\s+for\s+each\s+slot\s+level\s+above\s+'
            r'(?P<above>\d+(?:st|nd|rd|th)?)',
            re.IGNORECASE,
        )
        sd_m = scale_direct_re.search(higher_txt)
        if sd_m:
            scale_n     = int(sd_m.group(1))
            scale_sides = int(sd_m.group(2))
            above_level = _ordinal_to_int(sd_m.group("above")) or base_level
            levels_up   = max(0, spell_level - above_level)
            if scale_sides == dice_sides:   # même type de dé
                extra_n = levels_up * scale_n

    total_n = base_n + extra_n
    if dice_bonus > 0:
        return f"{total_n}d{dice_sides}+{dice_bonus}"
    return f"{total_n}d{dice_sides}"


def get_spell_projectile_count(spell_name: str, spell_level: int) -> int:
    """
    Retourne le nombre de projectiles distincts pour un sort multi-projectile
    au niveau donné (utile pour afficher les jets individuels).
    Retourne 1 pour les sorts non multi-projectiles.

    Ex. : Magic Missile niveau 1 → 3,  niveau 2 → 4
    """
    sp = get_spell(spell_name)
    if sp is None:
        return 1

    desc         = sp.get("description", "")
    entries_raw  = sp.get("entries_higher", [])
    higher_txt   = _flatten_entries(entries_raw) if entries_raw else ""
    base_level   = int(sp.get("level", 1))

    dice_m = _DICE_EXPR_RE.search(desc)
    if not dice_m:
        return 1

    _num_alts = "|".join(list(_NUM_WORDS.keys()) + [r"\d+"])
    proj_count_re = re.compile(
        rf'(?P<nb>{_num_alts})(?:\s+\w+)?\s+' + _PROJ_NOUN_RE.pattern,
        re.IGNORECASE,
    )
    proj_m = proj_count_re.search(desc[: dice_m.start()])
    if not proj_m:
        return 1

    base_count = _parse_number(proj_m.group("nb")) or 1

    scale_re = re.compile(
        r'one\s+(?:more|additional)\s+\w+\s+for\s+each\s+slot\s+level\s+above\s+'
        r'(?P<above>\d+(?:st|nd|rd|th)?)',
        re.IGNORECASE,
    )
    scale_m = scale_re.search(higher_txt)
    extra = 0
    if scale_m:
        above_level = _ordinal_to_int(scale_m.group("above")) or base_level
        extra = max(0, spell_level - above_level)

    return base_count + extra


# ─── Widget SpellPickerDialog ─────────────────────────────────────────────────

class SpellPickerDialog:
    """
    Fenêtre modale de sélection de sort.

    Usage :
        SpellPickerDialog(
            parent = self.win,
            on_select = lambda sp: print(sp["name"]),
            title = "✨ Choisir un sort",
            initial_query = "",     # pré-rempli si souhaité
        )
    """

    # Palette cohérente avec le reste de l'app
    BG      = "#0b0d12"
    PANEL   = "#111520"
    BORDER  = "#2a3040"
    GOLD    = "#c8a820"
    FG      = "#dde0e8"
    FG_DIM  = "#8899aa"
    ENTRY   = "#1a1f2e"
    SEL     = "#1e2a4a"
    BLUE    = "#3498db"
    GREEN   = "#2ecc71"

    def __init__(self, parent, on_select: Callable[[dict], None],
                 title: str = "✨ Choisir un sort",
                 initial_query: str = ""):
        self._cb = on_select
        self._selected: dict | None = None

        load_spells()  # no-op si déjà chargé

        # ── Fenêtre ───────────────────────────────────────────────────────────
        self.win = tk.Toplevel(parent)
        self.win.title(title)
        self.win.geometry("860x580")
        self.win.configure(bg=self.BG)
        self.win.resizable(True, True)
        self.win.minsize(600, 400)
        self.win.grab_set()
        self.win.focus_set()

        # ── Barre de recherche ────────────────────────────────────────────────
        top = tk.Frame(self.win, bg="#080a10", pady=8)
        top.pack(fill=tk.X)

        tk.Label(top, text="🔍", bg="#080a10", fg=self.GOLD,
                 font=("TkDefaultFont", 11)).pack(side=tk.LEFT, padx=(12, 4))

        self._search_var = tk.StringVar(value=initial_query)
        self._search_entry = tk.Entry(
            top, textvariable=self._search_var,
            bg=self.ENTRY, fg=self.FG,
            font=("Consolas", 11), insertbackground=self.FG,
            relief="flat"
        )
        self._search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True,
                                padx=(0, 8), ipady=4)

        # Filtre niveau
        tk.Label(top, text="Niv :", bg="#080a10", fg=self.FG_DIM,
                 font=("Consolas", 9)).pack(side=tk.LEFT)
        self._lvl_var = tk.StringVar(value="Tous")
        lvl_opts = ["Tous", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]
        lvl_om = tk.OptionMenu(top, self._lvl_var, *lvl_opts)
        lvl_om.config(bg=self.ENTRY, fg=self.FG, font=("Consolas", 9),
                      relief="flat", highlightthickness=0, width=4)
        lvl_om["menu"].config(bg=self.ENTRY, fg=self.FG)
        lvl_om.pack(side=tk.LEFT, padx=(2, 6))

        # Filtre école
        tk.Label(top, text="École :", bg="#080a10", fg=self.FG_DIM,
                 font=("Consolas", 9)).pack(side=tk.LEFT)
        self._school_var = tk.StringVar(value="Toutes")
        school_opts = ["Toutes"] + sorted(_SCHOOL_FR.values())
        school_om = tk.OptionMenu(top, self._school_var, *school_opts)
        school_om.config(bg=self.ENTRY, fg=self.FG, font=("Consolas", 9),
                         relief="flat", highlightthickness=0, width=14)
        school_om["menu"].config(bg=self.ENTRY, fg=self.FG)
        school_om.pack(side=tk.LEFT, padx=(2, 12))

        # ── Corps principal ───────────────────────────────────────────────────
        body = tk.Frame(self.win, bg=self.BG)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 0))

        # Liste (gauche)
        list_frame = tk.Frame(body, bg=self.PANEL, width=260)
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, padx=(0, 6))
        list_frame.pack_propagate(False)

        tk.Label(list_frame, text="SORTS", bg=self.PANEL, fg=self.GOLD,
                 font=("Consolas", 8, "bold")).pack(anchor="w", padx=8, pady=(6, 2))
        self._count_lbl = tk.Label(list_frame, text="", bg=self.PANEL,
                                   fg=self.FG_DIM, font=("Consolas", 7))
        self._count_lbl.pack(anchor="w", padx=8)

        list_outer = tk.Frame(list_frame, bg=self.BG)
        list_outer.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._list_canvas = tk.Canvas(list_outer, bg=self.BG, highlightthickness=0)
        sb = tk.Scrollbar(list_outer, orient="vertical",
                          command=self._list_canvas.yview)
        self._list_inner = tk.Frame(self._list_canvas, bg=self.BG)

        def _poll_list():
            try:
                if not self._list_inner.winfo_exists(): return
                self._list_canvas.configure(
                    scrollregion=self._list_canvas.bbox("all"))
                self._list_inner.after(300, _poll_list)
            except Exception:
                pass
        self._list_inner.after(200, _poll_list)
        self._list_canvas.create_window((0, 0), window=self._list_inner, anchor="nw")
        self._list_canvas.configure(yscrollcommand=sb.set)
        self._list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._list_canvas.bind("<MouseWheel>",
            lambda e: self._list_canvas.yview_scroll(-1*(e.delta//120), "units"))
        self._list_canvas.bind("<Button-4>",
            lambda e: self._list_canvas.yview_scroll(-1, "units"))
        self._list_canvas.bind("<Button-5>",
            lambda e: self._list_canvas.yview_scroll(1, "units"))

        # Détail (droite)
        detail_outer = tk.Frame(body, bg=self.PANEL)
        detail_outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._detail_box = scrolledtext.ScrolledText(
            detail_outer, bg="#090c15", fg=self.FG,
            font=("Consolas", 9), state=tk.DISABLED,
            wrap=tk.WORD, relief="flat"
        )
        self._detail_box.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        # Tags couleur pour le détail
        for tag, color in [
            ("name",  self.GOLD),
            ("level", "#aad4ff"),
            ("school","#c8b8ff"),
            ("meta",  self.FG_DIM),
            ("desc",  self.FG),
            ("sep",   self.BORDER),
        ]:
            self._detail_box.tag_config(tag, foreground=color)
        self._detail_box.tag_config("bold", font=("Consolas", 10, "bold"))
        self._detail_box.tag_config("name_bold",
                                    foreground=self.GOLD,
                                    font=("Consolas", 12, "bold"))

        # ── Barre bas ─────────────────────────────────────────────────────────
        bot = tk.Frame(self.win, bg="#080a10", pady=8)
        bot.pack(fill=tk.X, side=tk.BOTTOM)

        self._sel_label = tk.Label(bot, text="Aucun sort sélectionné",
                                   bg="#080a10", fg=self.FG_DIM,
                                   font=("Consolas", 9, "italic"))
        self._sel_label.pack(side=tk.LEFT, padx=14)

        tk.Button(bot, text="✕ Annuler",
                  bg="#1a0a0a", fg="#cc5555",
                  font=("Consolas", 9, "bold"), relief="flat",
                  padx=10, pady=4,
                  command=self.win.destroy).pack(side=tk.RIGHT, padx=6)

        self._btn_select = tk.Button(
            bot, text="✅ Sélectionner ce sort",
            bg="#0a2a0a", fg=self.GREEN,
            font=("Consolas", 10, "bold"), relief="flat",
            padx=14, pady=4, state=tk.DISABLED,
            command=self._confirm
        )
        self._btn_select.pack(side=tk.RIGHT, padx=6)

        # ── Bindings ─────────────────────────────────────────────────────────
        self._search_var.trace_add("write", lambda *_: self._refresh_list())
        self._lvl_var.trace_add("write", lambda *_: self._refresh_list())
        self._school_var.trace_add("write", lambda *_: self._refresh_list())
        self._search_entry.bind("<Return>", lambda e: self._pick_first())
        self._search_entry.focus_set()

        self._spell_labels: list[tk.Label] = []
        self._highlight_idx = -1

        self._refresh_list()

    # ── Liste ─────────────────────────────────────────────────────────────────

    def _filtered_spells(self) -> list[str]:
        """Retourne les noms de sorts filtrés selon les critères courants."""
        query  = self._search_var.get().strip().lower()
        lvl    = self._lvl_var.get()
        school = self._school_var.get()

        # Récupère les correspondances texte
        if query:
            candidates = search_spells(query, max_results=200)
        else:
            candidates = [v["name"] for v in _SPELL_DATA.values()]

        # Filtre niveau
        if lvl != "Tous":
            lvl_int = int(lvl)
            candidates = [n for n in candidates
                          if _SPELL_DATA.get(n.lower(), {}).get("level") == lvl_int]

        # Filtre école
        if school != "Toutes":
            candidates = [n for n in candidates
                          if _SPELL_DATA.get(n.lower(), {}).get("school") == school]

        return candidates[:120]   # limite UI

    def _refresh_list(self):
        """Reconstruit la liste des sorts filtrés."""
        for w in self._list_inner.winfo_children():
            w.destroy()
        self._spell_labels.clear()
        self._highlight_idx = -1

        names = self._filtered_spells()
        self._count_lbl.config(text=f"{len(names)} sort(s)")

        for name in names:
            sp = _SPELL_DATA.get(name.lower())
            if not sp:
                continue
            bg = self.BG
            lvl_tag = "■ " + ("T.M." if sp["level"] == 0 else f"Niv {sp['level']}")
            school_color = _SCHOOL_COLOR.get(sp["school"], "#aaaaaa")
            icon = _SCHOOL_ICON.get(sp["school"], "✨")

            row = tk.Frame(self._list_inner, bg=bg, cursor="hand2")
            row.pack(fill=tk.X, pady=0)

            tk.Label(row, text=icon, bg=bg, fg=school_color,
                     font=("TkDefaultFont", 9), width=2).pack(side=tk.LEFT,
                                                               padx=(4, 2), pady=2)
            name_lbl = tk.Label(row, text=name, bg=bg, fg=self.FG,
                                 font=("Consolas", 9), anchor="w")
            name_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=2)
            tk.Label(row, text=lvl_tag, bg=bg, fg=self.FG_DIM,
                     font=("Consolas", 7), anchor="e").pack(side=tk.RIGHT,
                                                             padx=(2, 6), pady=2)

            for widget in (row, name_lbl):
                widget.bind("<Enter>", lambda e, r=row: r.config(bg=self.SEL))
                widget.bind("<Leave>",
                    lambda e, r=row, i=len(self._spell_labels):
                        r.config(bg=self.SEL if i == self._highlight_idx else self.BG))
                widget.bind("<Button-1>",
                    lambda e, n=name: self._show_spell(n))
                widget.bind("<Double-Button-1>",
                    lambda e, n=name: (self._show_spell(n), self._confirm()))

            self._spell_labels.append(row)

    # ── Détail ────────────────────────────────────────────────────────────────

    def _show_spell(self, name: str):
        sp = _SPELL_DATA.get(name.lower())
        if not sp:
            return
        self._selected = sp

        # Reset surlignage
        for lbl in self._spell_labels:
            lbl.config(bg=self.BG)

        # Recherche et surligne la row correspondante
        for row in self._list_inner.winfo_children():
            children = row.winfo_children()
            for ch in children:
                if isinstance(ch, tk.Label) and ch.cget("text") == name:
                    row.config(bg=self.SEL)
                    break

        # Affiche le détail
        self._detail_box.config(state=tk.NORMAL)
        self._detail_box.delete("1.0", tk.END)

        lvl_str = "Tour de magie" if sp["level"] == 0 else f"Niveau {sp['level']}"
        badges = []
        if sp["concentration"]: badges.append("[Conc.]")
        if sp["ritual"]:        badges.append("[Rituel]")
        badge_str = "  " + "  ".join(badges) if badges else ""

        school_color = _SCHOOL_COLOR.get(sp["school"], "#aaaaaa")
        icon = _SCHOOL_ICON.get(sp["school"], "✨")

        self._detail_box.insert(tk.END, f"\n  {sp['name']}\n", "name_bold")
        self._detail_box.insert(tk.END,
            f"  {icon} {lvl_str}  —  {sp['school']}{badge_str}\n\n", "school")

        meta_lines = [
            f"  🕐 Incantation : {sp['cast_time']}",
            f"  🎯 Portée      : {sp['range']}",
            f"  🔤 Composantes : {sp['components']}",
            f"  ⏱ Durée        : {sp['duration']}",
            f"  📖 Source       : {sp['source']}",
        ]
        for line in meta_lines:
            self._detail_box.insert(tk.END, line + "\n", "meta")

        self._detail_box.insert(tk.END, "\n" + "─" * 42 + "\n", "sep")
        self._detail_box.insert(tk.END, "\n", "")
        self._detail_box.insert(tk.END, sp["description"], "desc")
        self._detail_box.insert(tk.END, "\n", "")

        self._detail_box.config(state=tk.DISABLED)

        # Active le bouton
        self._btn_select.config(state=tk.NORMAL)
        self._sel_label.config(
            text=f"✨ {sp['name']}  ({lvl_str})",
            fg=self.GOLD
        )

    def _pick_first(self):
        """Sélectionne le premier sort de la liste si aucun n'est cliqué."""
        names = self._filtered_spells()
        if names:
            self._show_spell(names[0])

    # ── Confirmation ──────────────────────────────────────────────────────────

    def _confirm(self):
        if self._selected:
            self._cb(self._selected)
            self.win.destroy()


# ─── Regex cachée de tous les noms de sorts ───────────────────────────────────

_spell_pattern: re.Pattern | None = None

def get_spell_pattern() -> re.Pattern | None:
    """
    Retourne une regex compilée qui matche n'importe quel nom de sort connu.
    Construite une seule fois après load_spells(), puis mise en cache.
    Les noms sont triés par longueur décroissante pour favoriser les matches longs.
    """
    global _spell_pattern
    if _spell_pattern is not None:
        return _spell_pattern
    load_spells()
    if not _SPELL_DATA:
        return None
    # Noms originaux (casse préservée) triés par longueur desc
    names_sorted = sorted(
        (v["name"] for v in _SPELL_DATA.values()),
        key=len, reverse=True
    )
    parts = [re.escape(n) for n in names_sorted]
    _spell_pattern = re.compile(
        r"(?<![A-Za-zÀ-ÿ])(" + "|".join(parts) + r")(?![A-Za-zÀ-ÿ])",
        re.IGNORECASE
    )
    return _spell_pattern


# ─── SpellSheetWindow — fiche sort autonome (lecture seule) ───────────────────

class SpellSheetWindow:
    """
    Fenêtre Toplevel autonome affichant la fiche complète d'un sort.
    Non-modale : plusieurs fiches peuvent être ouvertes simultanément.

    Usage :
        SpellSheetWindow(parent_tk_widget, spell_dict)
        SpellSheetWindow(parent_tk_widget, "Fireball")   # par nom
    """

    BG      = "#0b0d12"
    PANEL   = "#090c15"
    BORDER  = "#2a3040"
    GOLD    = "#c8a820"
    FG      = "#dde0e8"
    FG_DIM  = "#8899aa"

    def __init__(self, parent, spell):
        # Accepte un dict normalisé ou un nom de sort
        if isinstance(spell, str):
            load_spells()
            spell = _SPELL_DATA.get(spell.lower())
        if not spell:
            return

        self.sp = spell

        win = tk.Toplevel(parent)
        school_color = _SCHOOL_COLOR.get(spell["school"], "#aaaaaa")
        icon         = _SCHOOL_ICON.get(spell["school"], "✨")
        lvl_str      = "Tour de magie" if spell["level"] == 0 else f"Niveau {spell['level']}"

        win.title(f"{icon} {spell['name']}  —  {lvl_str}")
        win.geometry("560x480")
        win.configure(bg=self.BG)
        win.resizable(True, True)
        win.minsize(400, 300)

        # ── En-tête coloré ────────────────────────────────────────────────────
        hdr = tk.Frame(win, bg=_darken_color(school_color, 0.15), pady=10)
        hdr.pack(fill=tk.X)

        tk.Label(
            hdr, text=f"{icon}  {spell['name']}",
            bg=_darken_color(school_color, 0.15),
            fg=school_color,
            font=("Consolas", 15, "bold"),
        ).pack(side=tk.LEFT, padx=16)

        badges = []
        if spell["concentration"]: badges.append("Concentration")
        if spell["ritual"]:        badges.append("Rituel")
        badge_txt = "  |  ".join([f"{lvl_str}", spell["school"]] + badges)
        tk.Label(
            hdr, text=badge_txt,
            bg=_darken_color(school_color, 0.15),
            fg=self.FG_DIM,
            font=("Consolas", 9),
        ).pack(side=tk.LEFT, padx=(0, 12))

        # ── Métadonnées ───────────────────────────────────────────────────────
        meta_frame = tk.Frame(win, bg="#0d1018", pady=6)
        meta_frame.pack(fill=tk.X, padx=0)

        meta_pairs = [
            ("Incantation", spell["cast_time"]),
            ("Portée",      spell["range"]),
            ("Composantes", spell["components"]),
            ("Durée",       spell["duration"]),
            ("Source",      spell["source"]),
        ]
        for i, (label, value) in enumerate(meta_pairs):
            col = tk.Frame(meta_frame, bg="#0d1018")
            col.pack(side=tk.LEFT, padx=(16 if i == 0 else 12, 0))
            tk.Label(col, text=label.upper(), bg="#0d1018", fg=self.FG_DIM,
                     font=("Consolas", 7, "bold")).pack(anchor="w")
            tk.Label(col, text=value, bg="#0d1018", fg=self.GOLD,
                     font=("Consolas", 9)).pack(anchor="w")

        # ── Séparateur ────────────────────────────────────────────────────────
        tk.Frame(win, bg=self.BORDER, height=1).pack(fill=tk.X, padx=8, pady=(4, 0))

        # ── Description scrollable ────────────────────────────────────────────
        txt = scrolledtext.ScrolledText(
            win, bg=self.PANEL, fg=self.FG,
            font=("Consolas", 10), wrap=tk.WORD,
            relief="flat", state=tk.NORMAL,
            padx=14, pady=10,
        )
        txt.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        txt.tag_config("desc",  foreground=self.FG,    font=("Consolas", 10))
        txt.tag_config("emph",  foreground=self.GOLD,  font=("Consolas", 10, "bold"))
        txt.tag_config("dim",   foreground=self.FG_DIM,font=("Consolas", 9, "italic"))

        # Mise en valeur des formules de dés et des mots en majuscules
        desc = spell["description"]
        _dice_re  = re.compile(r'\b\d+d\d+(?:[+\-]\d+)?\b')
        _upper_re = re.compile(r'\b[A-Z][A-Z]{2,}\b')

        pos = 0
        for m in sorted(
            list(_dice_re.finditer(desc)) + list(_upper_re.finditer(desc)),
            key=lambda x: x.start()
        ):
            if m.start() < pos:
                continue
            if m.start() > pos:
                txt.insert(tk.END, desc[pos:m.start()], "desc")
            txt.insert(tk.END, m.group(0), "emph")
            pos = m.end()
        if pos < len(desc):
            txt.insert(tk.END, desc[pos:], "desc")

        txt.config(state=tk.DISABLED)

        # ── Bouton fermer ─────────────────────────────────────────────────────
        bot = tk.Frame(win, bg="#080a10", pady=6)
        bot.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Button(
            bot, text="Fermer",
            bg="#1a0a0a", fg="#cc5555",
            font=("Consolas", 9, "bold"), relief="flat",
            padx=12, pady=4, cursor="hand2",
            command=win.destroy,
        ).pack(side=tk.RIGHT, padx=10)

        win.focus_set()


def _darken_color(hex_color: str, factor: float) -> str:
    """Assombrit une couleur hex (factor entre 0 et 1)."""
    try:
        h = hex_color.lstrip("#")
        if len(h) == 6:
            r = int(int(h[0:2], 16) * (1 - factor))
            g = int(int(h[2:4], 16) * (1 - factor))
            b = int(int(h[4:6], 16) * (1 - factor))
            return f"#{max(0,r):02x}{max(0,g):02x}{max(0,b):02x}"
    except Exception:
        pass
    return hex_color