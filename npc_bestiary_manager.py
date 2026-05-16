"""
npc_bestiary_manager.py — Parser, mise en cache et recherche pour le bestiaire 5etools.
"""

import os
import json
import glob
import pickle
import copy as _copy_module
import re
import time

# ─── Répertoire du bestiary ───────────────────────────────────────────────────
_BESTIARY_DIR   = os.path.join(os.path.dirname(__file__), "bestiary")
_LEGENDARY_FILE = os.path.join(_BESTIARY_DIR, "legendarygroups.json")
_CACHE_FILE     = os.path.join(_BESTIARY_DIR, "bestiary_cache.pkl")

# ─── Cache des données du bestiary ───────────────────────────────────────────
_BESTIARY_DATA: dict[str, dict] = {}    # name.lower() → monster dict (résolu)
_FLUFF_DATA:    dict[str, dict] = {}    # name.lower() → fluff dict
_LEGENDARY_DATA: dict[str, dict] = {}  # name.lower() → legendary group dict
_BESTIARY_NAMES: list[str] = []        # liste triée pour l'autocomplétion

# ─── Résolution _copy / _versions (format 5etools) ───────────────────────────

def _apply_mod(base: dict, mod: dict) -> dict:
    """
    Applique un bloc _mod (format 5etools) à un dict de monstre de base.
    Supporte : appendArr, prependArr, replaceArr, removeArr, insertArr,
               replace (direct), et les overrides de champs scalaires.
    """
    result = _copy_module.deepcopy(base)
    for field, op in mod.items():
        if field == "_":
            # Opérations globales (addSpells, etc.) — ignorées pour l'affichage
            continue
        if not isinstance(op, dict) or "mode" not in op:
            # Override direct du champ
            result[field] = op
            continue
        mode = op["mode"]
        if mode == "appendArr":
            items = op.get("items", [])
            if field not in result:
                result[field] = []
            if isinstance(items, list):
                result[field].extend(items)
            else:
                result[field].append(items)
        elif mode == "prependArr":
            items = op.get("items", [])
            arr   = result.get(field, [])
            result[field] = (items if isinstance(items, list) else [items]) + arr
        elif mode == "replaceArr":
            replace_name = op.get("replace")
            new_item     = op.get("items")
            arr = result.get(field, [])
            result[field] = [
                new_item if (isinstance(x, dict) and x.get("name") == replace_name) else x
                for x in arr
            ]
        elif mode == "removeArr":
            names = op.get("names", [])
            if isinstance(names, str):
                names = [names]
            arr = result.get(field, [])
            result[field] = [
                x for x in arr
                if not (isinstance(x, dict) and x.get("name") in names)
            ]
        elif mode == "insertArr":
            items = op.get("items", [])
            idx   = op.get("index", 0)
            arr   = result.get(field, [])
            chunk = items if isinstance(items, list) else [items]
            result[field] = arr[:idx] + chunk + arr[idx:]
        elif mode == "replace":
            # { mode: "replace", replace: "OldName", items: {...} }
            replace_name = op.get("replace")
            new_item     = op.get("items")
            arr = result.get(field, [])
            result[field] = [
                new_item if (isinstance(x, dict) and x.get("name") == replace_name) else x
                for x in arr
            ]
        else:
            # Fallback : override direct
            result[field] = op
    return result


def _resolve_copy(raw: dict, index_by_key: dict, index_by_name: dict) -> dict:
    """
    Résout récursivement le champ _copy d'un monstre.
    index_by_key  : {(name.lower(), SOURCE) → dict}
    index_by_name : {name.lower() → dict}  (fallback toutes sources)
    """
    copy_ref = raw.get("_copy")
    if not copy_ref:
        return raw

    base_name   = copy_ref.get("name", "")
    base_source = copy_ref.get("source", "").upper()

    # Cherche le base d'abord par (nom, source), puis par nom seul
    base = (index_by_key.get((base_name.lower(), base_source))
            or index_by_name.get(base_name.lower()))

    if not base:
        print(f"[Bestiary] _copy non résolu : {base_name} ({base_source})")
        return raw  # Retourne tel quel si la base est introuvable

    # Résolution récursive de la base
    base = _resolve_copy(base, index_by_key, index_by_name)

    # Fusion : base + overrides du monstre enfant
    result = _copy_module.deepcopy(base)
    for k, v in raw.items():
        if k not in ("_copy", "_mod"):
            result[k] = v

    # Application des _mod
    if "_mod" in raw:
        result = _apply_mod(result, raw["_mod"])

    return result


def _expand_versions(base: dict) -> list[dict]:
    """
    Développe les _versions d'un monstre en entrées autonomes.
    Retourne une liste de dicts (sans le champ _versions).
    """
    versions = base.get("_versions", [])
    expanded = []
    for v in versions:
        if not isinstance(v, dict) or "name" not in v:
            continue
        result = _copy_module.deepcopy(base)
        result.pop("_versions", None)
        result["name"] = v["name"]
        # Overrides directs (champs non-underscore)
        for k, val in v.items():
            if not k.startswith("_") and k not in ("name", "variant"):
                result[k] = val
        # _mod
        if "_mod" in v:
            result = _apply_mod(result, v["_mod"])
        expanded.append(result)
    return expanded


def _load_bestiary():
    """
    Charge tous les fichiers bestiary-*.json du dossier bestiary/ en mémoire.
    - Résout les références _copy inter-fichiers
    - Étend les _versions en entrées autonomes
    - Charge également tous les fluff-bestiary-*.json
    - Appelé une seule fois (lazy).
    """
    global _BESTIARY_DATA, _FLUFF_DATA, _LEGENDARY_DATA, _BESTIARY_NAMES
    if _BESTIARY_DATA:
        return

    # ── Étape 1 : collecter TOUS les monstres bruts (toutes sources) ───────
    stat_files = sorted(glob.glob(os.path.join(_BESTIARY_DIR, "bestiary-*.json")))
    if not stat_files:
        print(f"[Bestiary] Aucun fichier bestiary-*.json trouvé dans {_BESTIARY_DIR}")
        return

    # Vérification du cache
    try:
        newest_mtime = max(os.path.getmtime(f) for f in stat_files)
        fluff_files = sorted(glob.glob(os.path.join(_BESTIARY_DIR, "fluff-bestiary-*.json")))
        if fluff_files:
            newest_mtime = max(newest_mtime, max(os.path.getmtime(f) for f in fluff_files))
        if os.path.exists(_LEGENDARY_FILE):
            newest_mtime = max(newest_mtime, os.path.getmtime(_LEGENDARY_FILE))

        if os.path.exists(_CACHE_FILE) and os.path.getmtime(_CACHE_FILE) >= newest_mtime:
            with open(_CACHE_FILE, "rb") as f:
                _BESTIARY_DATA, _FLUFF_DATA, _LEGENDARY_DATA, _BESTIARY_NAMES = pickle.load(f)
            print(f"[Bestiary] Chargé depuis le cache : {len(_BESTIARY_DATA)} entrées.")
            return
    except Exception as e:
        print(f"[Bestiary] Info: Impossible de lire le cache, chargement normal ({e})")

    raw_monsters: list[dict] = []
    for path in stat_files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            batch = data.get("monster", [])
            raw_monsters.extend(batch)
            print(f"[Bestiary] Chargé {len(batch)} monstres depuis {os.path.basename(path)}")
            time.sleep(0.01)  # Force GIL yield to Tkinter mainloop
        except Exception as e:
            print(f"[Bestiary] Erreur lecture {path}: {e}")

    # ── Étape 2 : construire les index bruts (avant résolution) ────────────
    raw_by_key:  dict[tuple, dict] = {}   # (name.lower(), SOURCE) → raw dict
    raw_by_name: dict[str, dict]   = {}   # name.lower() → raw dict (dernier vu)

    for m in raw_monsters:
        name   = m.get("name", "")
        source = m.get("source", "").upper()
        raw_by_key[(name.lower(), source)] = m
        raw_by_name[name.lower()] = m  # écrase ; MM prioritaire si chargé en premier

    # ── Étape 3 : résoudre _copy et étendre _versions ──────────────────────
    for i, m in enumerate(raw_monsters):
        if i % 100 == 0:
            time.sleep(0.01)  # Force GIL yield during heavy parsing

        resolved = _resolve_copy(m, raw_by_key, raw_by_name)
        name_key = resolved.get("name", "").lower()
        _BESTIARY_DATA[name_key] = resolved

        # Étend les _versions en entrées autonomes
        for variant in _expand_versions(resolved):
            v_key = variant.get("name", "").lower()
            _BESTIARY_DATA[v_key] = variant

    _BESTIARY_NAMES = sorted(_BESTIARY_DATA.keys())
    print(f"[Bestiary] {len(_BESTIARY_DATA)} entrées totales après résolution.")

    # ── Étape 4 : charger le fluff (lore) ──────────────────────────────────
    fluff_files = sorted(glob.glob(os.path.join(_BESTIARY_DIR, "fluff-bestiary-*.json")))
    for path in fluff_files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for m in data.get("monsterFluff", []):
                key = m.get("name", "").lower()
                _FLUFF_DATA[key] = m
            time.sleep(0.01)  # Force GIL yield
        except Exception as e:
            print(f"[Bestiary] Erreur lecture fluff {path}: {e}")

    # ── Étape 5 : groupes légendaires ──────────────────────────────────────
    try:
        with open(_LEGENDARY_FILE, encoding="utf-8") as f:
            raw_leg = json.load(f)
        for g in raw_leg.get("legendaryGroup", []):
            key = g.get("name", "").lower()
            _LEGENDARY_DATA[key] = g
    except Exception as e:
        print(f"[Bestiary] Impossible de charger {_LEGENDARY_FILE}: {e}")

    # ── Sauvegarde du cache ────────────────────────────────────────────────
    try:
        with open(_CACHE_FILE, "wb") as f:
            pickle.dump((_BESTIARY_DATA, _FLUFF_DATA, _LEGENDARY_DATA, _BESTIARY_NAMES), f)
    except Exception as e:
        print(f"[Bestiary] Erreur sauvegarde cache : {e}")


def search_monsters(query: str, max_results: int = 12) -> list[str]:
    """Retourne les noms originaux de monstres correspondant à la recherche."""
    _load_bestiary()
    q = query.lower().strip()
    if not q:
        return [_BESTIARY_DATA[k]["name"] for k in _BESTIARY_NAMES[:max_results]]
    exact  = [k for k in _BESTIARY_NAMES if k == q]
    starts = [k for k in _BESTIARY_NAMES if k.startswith(q) and k != q]
    contains = [k for k in _BESTIARY_NAMES if q in k and not k.startswith(q)]
    results = (exact + starts + contains)[:max_results]
    return [_BESTIARY_DATA[k]["name"] for k in results]


def get_monster(name: str, apply_upgrades: bool = True) -> dict | None:
    """Retourne le dict complet d'un monstre (ou None si introuvable)."""
    _load_bestiary()
    m = _BESTIARY_DATA.get(name.lower())
    if not m:
        return None

    if apply_upgrades:
        try:
            from state_manager import get_monster_upgrade
            lvl = get_monster_upgrade(name)
            if lvl != 0:
                import copy
                m = copy.deepcopy(m)
                _apply_monster_upgrade(m, lvl)
        except Exception:
            pass

    return m

def _apply_monster_upgrade(m: dict, lvl: int):
    # 1. Caractéristiques de base
    for stat in ["str", "dex", "con", "int", "wis", "cha"]:
        if stat in m:
            m[stat] = max(1, m[stat] + (lvl * 2))

    # 2. Points de vie (moyenne)
    if "hp" in m and isinstance(m["hp"], dict):
        if lvl >= 0:
            m["hp"]["average"] = int(m["hp"].get("average", 10) * (1 + lvl))
        else:
            m["hp"]["average"] = max(1, int(m["hp"].get("average", 10) / (1 + abs(lvl))))

    # 3. Classe d'Armure (CA)
    if "ac" in m and isinstance(m["ac"], list):
        for i, ac_item in enumerate(m["ac"]):
            if isinstance(ac_item, int):
                m["ac"][i] = max(1, ac_item + lvl)
            elif isinstance(ac_item, dict) and "ac" in ac_item:
                ac_item["ac"] = max(1, ac_item["ac"] + lvl)

    # 4. Jets de Sauvegarde et Compétences
    if "save" in m and isinstance(m["save"], dict):
        for k, v in m["save"].items():
            if isinstance(v, str):
                try: m["save"][k] = f"{int(v) + lvl:+d}"
                except Exception: pass
                
    if "skill" in m and isinstance(m["skill"], dict):
        for k, v in m["skill"].items():
            if isinstance(v, str):
                try: m["skill"][k] = f"{int(v) + lvl:+d}"
                except Exception: pass

    # 5. Remplacements textuels automatiques (Attaques, DCs, Dégâts)
    def _upgrade_text(text: str) -> str:
        # {@hit X} -> Modifie le bonus d'attaque
        text = re.sub(r'\{@hit\s+(-?\d+)\}', lambda match: f"{{@hit {int(match.group(1)) + lvl}}}", text)
        
        # {@dc X} -> Modifie le jet de sauvegarde (Save DC)
        text = re.sub(r'\{@dc\s+(\d+)\}', lambda match: f"{{@dc {int(match.group(1)) + lvl}}}", text)
        
        # {@damage NdX + Y} ou {@damage NdX - Y} -> Modifie les bonus de dégâts
        def _dmg_repl(match):
            dice, sign, bonus = match.group(1), match.group(2), int(match.group(3))
            new_bonus = bonus + lvl if sign == '+' else bonus - lvl
            if new_bonus > 0: return f"{{@damage {dice} + {new_bonus}}}"
            elif new_bonus < 0: return f"{{@damage {dice} - {abs(new_bonus)}}}"
            else: return f"{{@damage {dice}}}"
        text = re.sub(r'\{@damage\s+(\d+d\d+)\s*([+-])\s*(\d+)\}', _dmg_repl, text)
        
        # {@damage NdX} (sans bonus de base)
        def _dmg_base_repl(match):
            dice = match.group(1)
            if lvl > 0: return f"{{@damage {dice} + {lvl}}}"
            elif lvl < 0: return f"{{@damage {dice} - {abs(lvl)}}}"
            return match.group(0)
        text = re.sub(r'\{@damage\s+(\d+d\d+)\}', _dmg_base_repl, text)

        # {@damage FLAT} (dégâts bruts, sans dés)
        def _dmg_flat_repl(match):
            val = int(match.group(1))
            return f"{{@damage {max(1, val + lvl)}}}"
        text = re.sub(r'\{@damage\s+(\d+)\}', _dmg_flat_repl, text)

        # Moyennes des dégâts affichées avant les tags : ex. "10 ({@damage..."
        def _avg_repl(match):
            old_avg = int(match.group(1))
            return f"{max(1, old_avg + lvl)} ({{@damage"
        text = re.sub(r'\b(\d+)\s*\(\{@damage', _avg_repl, text)

        return text

    # Parcours de tous les blocs d'actions du monstre pour appliquer les regex
    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str): node[k] = _upgrade_text(v)
                elif isinstance(v, (dict, list)): _walk(v)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                if isinstance(v, str): node[i] = _upgrade_text(v)
                elif isinstance(v, (dict, list)): _walk(v)

    for block in ["action", "bonus_action", "reaction", "trait", "legendary", "spellcasting"]:
        if block in m:
            _walk(m[block])


def get_monster_fluff(name: str) -> dict | None:
    """Retourne le lore d'un monstre (ou None)."""
    _load_bestiary()
    return _FLUFF_DATA.get(name.lower())


def get_legendary_group(name: str) -> dict | None:
    """Retourne le groupe légendaire d'un monstre (ou None)."""
    _load_bestiary()
    return _LEGENDARY_DATA.get(name.lower())