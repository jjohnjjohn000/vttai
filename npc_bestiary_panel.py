"""
npc_bestiary_panel.py — Gestionnaire de PNJs et bestiary D&D 5e.
──────────────────────────────────────────────────────────────────
Fonctionnalités :
  • GroupNPCPanel  : liste des PNJs du groupe dans la sidebar
      - Ajout / suppression / édition PV en ligne
      - 🎭 Bouton "Parler en tant que" : LLM génère la réplique du PNJ
      - 📋 Bouton raccourci pour ouvrir la fiche
  • MonsterSheetWindow : fiche complète du monstre (bestiary 5etools)
      - Sélecteur de monstre avec autocomplétion
      - Zone image NPC : charger, afficher, envoyer aux agents multimodaux
      - Panneau "Parler en tant que" : prompt MJ → LLM → réplique in-character
      - Jets COMPLETS : 6 stats, 6 sauvegardes (toutes), toutes compétences,
        initiative, toutes actions/attaques/DD

Structure PNJ dans campaign_state.json → group_npcs :
{
    "name": "Ismark",
    "color": "#a0c4ff",
    "bestiary_name": "Guard",
    "hp_current": 11,
    "notes": "Frère d'Ireena…",
    "image_path": "npc_images/Ismark.png"   ← optionnel
}
"""

import json
import copy as _copy_module
import glob
import os
import threading
import base64 as _b64
import tkinter as tk
from tkinter import scrolledtext, filedialog, messagebox
import re

# ─── Répertoire du bestiary ───────────────────────────────────────────────────
_BESTIARY_DIR   = os.path.join(os.path.dirname(__file__), "bestiary")
_LEGENDARY_FILE = os.path.join(_BESTIARY_DIR, "legendarygroups.json")

# ─── Mapping compétences → caractéristique de base ────────────────────────────
_SKILL_TO_STAT = {
    "athletics":      "str",
    "acrobatics":     "dex", "sleight of hand": "dex", "stealth":      "dex",
    "arcana":         "int", "history":         "int", "investigation":"int",
    "nature":         "int", "religion":        "int",
    "animal handling":"wis", "insight":         "wis", "medicine":     "wis",
    "perception":     "wis", "survival":        "wis",
    "deception":      "cha", "intimidation":    "cha", "performance":  "cha",
    "persuasion":     "cha",
}
_SKILL_FR = {
    "athletics":       "Athlétisme",    "acrobatics":      "Acrobaties",
    "sleight of hand": "Escamotage",    "stealth":         "Discrétion",
    "arcana":          "Arcanes",       "history":         "Histoire",
    "investigation":   "Investigation", "nature":          "Nature",
    "religion":        "Religion",      "animal handling": "Dressage",
    "insight":         "Perspicacité",  "medicine":        "Médecine",
    "perception":      "Perception",    "survival":        "Survie",
    "deception":       "Tromperie",     "intimidation":    "Intimidation",
    "performance":     "Représentation","persuasion":      "Persuasion",
}
_STAT_COLORS = {
    "str": "#e57373", "dex": "#81c784", "con": "#ffb74d",
    "int": "#64b5f6", "wis": "#ce93d8", "cha": "#f06292",
}

# ─── Utilitaires images NPC ───────────────────────────────────────────────────

def _npc_images_dir() -> str:
    """Retourne le dossier de stockage des images NPC (créé si absent)."""
    try:
        from app_config import get_campaign_name
        camp = get_campaign_name()
    except Exception:
        camp = "campagne"
    d = os.path.join("campagne", camp, "npc_images")
    os.makedirs(d, exist_ok=True)
    return d


def _npc_image_path(npc_name: str) -> str:
    """Chemin vers l'image PNG d'un PNJ."""
    safe = re.sub(r'[^\w\-]', '_', npc_name)
    return os.path.join(_npc_images_dir(), f"{safe}.png")


def load_npc_image_bytes(npc_name: str) -> bytes | None:
    """Charge les bytes de l'image NPC depuis le disque, ou None."""
    path = _npc_image_path(npc_name)
    try:
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
    except Exception:
        pass
    return None


def save_npc_image_bytes(npc_name: str, data: bytes):
    """Sauvegarde les bytes d'image PNG pour un PNJ."""
    try:
        with open(_npc_image_path(npc_name), "wb") as f:
            f.write(data)
    except Exception as e:
        print(f"[NPC Image] Erreur sauvegarde : {e}")


# ─── LLM : parler en tant que PNJ ────────────────────────────────────────────

def _build_npc_persona(npc_name: str, monster: dict | None) -> str:
    """Construit le system prompt de persona pour un PNJ."""
    if monster:
        m_type   = _fmt_type(monster.get("type", "créature"))
        size_map = {"T": "Très petit", "S": "Petit", "M": "Moyen",
                    "L": "Grand",      "H": "Très grand", "G": "Gigantesque"}
        align_map = {"L": "Loyal", "N": "Neutre", "C": "Chaotique",
                     "G": "Bon",   "E": "Mauvais", "A": "Quelconque", "U": "Sans alignement"}
        sizes     = [size_map.get(s, s) for s in monster.get("size", [])]
        align_raw = monster.get("alignment", [])
        align_txt = " ".join(align_map.get(a, a) for a in align_raw)
        langs     = monster.get("languages", [])
        langs_str = ", ".join(langs) if langs else "inconnu"

        traits_txt = ""
        for t in monster.get("trait", [])[:3]:
            traits_txt += f"\n- {t.get('name','?')} : {_fmt_entries(t.get('entries', []))[:120]}"

        cr = _fmt_cr(monster.get("cr", "?"))
        persona = (
            f"Tu incarnes {npc_name}, un(e) {' '.join(sizes)} {m_type}, {align_txt} "
            f"(FP {cr}). Langues : {langs_str}."
        )
        if traits_txt:
            persona += f"\nTraits notables :{traits_txt}"
    else:
        persona = f"Tu incarnes {npc_name}, un PNJ de l'univers de la campagne."

    persona += (
        "\n\nRègles absolues :"
        "\n• Parle TOUJOURS à la première personne, en français, dans le ton du personnage."
        "\n• 2-4 phrases maximum. Sois vivant, cohérent avec l'alignement et le type."
        "\n• N'explique jamais que tu es une IA. Ne casse jamais le 4e mur."
        "\n• Adapte le registre : un garde parle brièvement, un vampire avec morgue, etc."
    )
    return persona


def speak_as_npc(npc_name: str, monster: dict | None, prompt: str,
                 msg_queue, audio_queue=None, color: str = "#a5d6a7",
                 scene_context: str = ""):
    """
    Lance un thread daemon qui appelle le LLM pour générer une réplique du PNJ.
    Résultat envoyé dans msg_queue + audio_queue (si fourni).
    """
    def _run():
        try:
            import autogen as _ag
            from llm_config import build_llm_config, _default_model
            from app_config import get_chronicler_config

            chron = get_chronicler_config()
            cfg   = build_llm_config(
                chron.get("model", _default_model),
                temperature=chron.get("temperature", 0.75),
            )
            client  = _ag.OpenAIWrapper(config_list=cfg["config_list"])
            persona = _build_npc_persona(npc_name, monster)

            user_msg = prompt.strip()
            if scene_context.strip():
                user_msg = f"[Contexte de scène : {scene_context.strip()}]\n\n{user_msg}"

            response = client.create(messages=[
                {"role": "system", "content": persona},
                {"role": "user",   "content": user_msg or "Introduis-toi brièvement."},
            ])
            text = (response.choices[0].message.content or "").strip()
            if text:
                msg_queue.put({"sender": npc_name, "text": text, "color": color})
                if audio_queue:
                    audio_queue.put((text, npc_name))
        except Exception as e:
            msg_queue.put({
                "sender": f"⚠ PNJ",
                "text":   f"Erreur LLM pour {npc_name} : {e}",
                "color":  "#F44336",
            })

    threading.Thread(target=_run, daemon=True, name=f"npc-speak-{npc_name}").start()

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
            # Same as replaceArr but the field key IS the array name
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
    raw_monsters: list[dict] = []
    stat_files = sorted(glob.glob(os.path.join(_BESTIARY_DIR, "bestiary-*.json")))
    if not stat_files:
        print(f"[Bestiary] Aucun fichier bestiary-*.json trouvé dans {_BESTIARY_DIR}")
        return

    for path in stat_files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            batch = data.get("monster", [])
            raw_monsters.extend(batch)
            print(f"[Bestiary] Chargé {len(batch)} monstres depuis {os.path.basename(path)}")
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
    for m in raw_monsters:
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


def get_monster(name: str) -> dict | None:
    """Retourne le dict complet d'un monstre (ou None si introuvable)."""
    _load_bestiary()
    return _BESTIARY_DATA.get(name.lower())


def get_monster_fluff(name: str) -> dict | None:
    """Retourne le lore d'un monstre (ou None)."""
    _load_bestiary()
    return _FLUFF_DATA.get(name.lower())


def get_legendary_group(name: str) -> dict | None:
    """Retourne le groupe légendaire d'un monstre (ou None)."""
    _load_bestiary()
    return _LEGENDARY_DATA.get(name.lower())


# ─── Helpers de rendu ─────────────────────────────────────────────────────────

def _fmt_entries(entries) -> str:
    """Convertit la liste d'entrées JSON du bestiary en texte lisible."""
    if not entries:
        return ""
    parts = []
    for e in entries:
        if isinstance(e, str):
            # Nettoie les tags {@…}
            text = re.sub(r'\{@\w+\s*([^}]*)\}', r'\1', e)
            parts.append(text)
        elif isinstance(e, dict):
            if e.get("type") == "entries":
                name = e.get("name", "")
                sub  = _fmt_entries(e.get("entries", []))
                if name:
                    parts.append(f"► {name}: {sub}")
                else:
                    parts.append(sub)
            elif e.get("type") == "list":
                for item in e.get("items", []):
                    if isinstance(item, str):
                        text = re.sub(r'\{@\w+\s*([^}]*)\}', r'\1', item)
                        parts.append(f"  • {text}")
                    elif isinstance(item, dict):
                        name = item.get("name", "")
                        prefix = f"► {name}: " if name else ""
                        if "entry" in item:
                            t = _fmt_entries([item["entry"]])
                            parts.append(f"  • {prefix}{t}")
                        else:
                            t = _fmt_entries(item.get("entries", []))
                            parts.append(f"  • {prefix}{t}")
            else:
                t = _fmt_entries(e.get("entries", []))
                if t:
                    parts.append(t)
    return "\n".join(parts)


def _fmt_damage_list(entries: list, key: str) -> str:
    """
    Formate une liste resist/immune du format 5etools.
    Chaque item peut être :
      - str                    → affiché tel quel
      - {"resist": [...], "note": "...", ...}  → liste + note
      - {"immune": [...], "note": "...", ...}  → idem
      - {"special": "..."}     → affiché tel quel
    """
    parts = []
    for item in entries:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, list):
            # Liste imbriquée directe — aplatit récursivement
            parts.append(_fmt_damage_list(item, key))
        elif isinstance(item, dict):
            if "special" in item:
                parts.append(item["special"])
            else:
                sub = item.get(key, [])
                # sub peut être une liste de strings ou de dicts
                sub_str = _fmt_damage_list(sub, key) if sub else ""
                note = item.get("note", "")
                pre  = item.get("preNote", "")
                chunk = sub_str
                if pre:
                    chunk = f"{pre} {chunk}".strip()
                if note:
                    chunk = f"{chunk} ({note})"
                if chunk:
                    parts.append(chunk)
        else:
            parts.append(str(item))
    return ", ".join(p for p in parts if p)


def _fmt_condition_list(entries: list) -> str:
    """
    Formate une liste conditionImmune.
    Items peuvent être str ou dict avec "condition".
    """
    parts = []
    for item in entries:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, list):
            parts.append(_fmt_condition_list(item))
        elif isinstance(item, dict):
            cond = item.get("condition", "")
            note = item.get("note", "")
            chunk = cond or str(item)
            if note:
                chunk = f"{chunk} ({note})"
            parts.append(chunk)
        else:
            parts.append(str(item))
    return ", ".join(p for p in parts if p)

def _fmt_action_list(actions: list) -> str:
    if not actions:
        return "(aucune)"
    lines =[]
    for a in actions:
        name = a.get("name", "?")
        desc = _fmt_entries(a.get("entries", []))
        lines.append(f"▸ {name}\n  {desc}")
    return "\n\n".join(lines)


def _fmt_cr(cr) -> str:
    if isinstance(cr, dict):
        return str(cr.get("cr", "?"))
    return str(cr)


def _fmt_type(t) -> str:
    if isinstance(t, dict):
        base = t.get("type", "?")
        tags = t.get("tags", [])
        if tags:
            return f"{base} ({', '.join(tags)})"
        return base
    return str(t)


def _fmt_ac(ac_list) -> str:
    if not ac_list:
        return "?"
    a = ac_list[0]
    if isinstance(a, int):
        return str(a)
    if isinstance(a, dict):
        val  = str(a.get("ac", "?"))
        frm  = a.get("from", [])
        cond = a.get("condition", "")
        extra = ", ".join(frm)
        if cond:
            extra = f"{extra} {cond}".strip()
        return f"{val} ({extra})" if extra else val
    return str(a)


def _fmt_speed(speed: dict) -> str:
    parts = []
    for k, v in speed.items():
        if k == "walk":
            parts.insert(0, f"{v} ft.")
        else:
            parts.append(f"{k} {v} ft.")
    return ", ".join(parts)


def _ability_mod(score: int) -> str:
    mod = (score - 10) // 2
    return f"{score} ({mod:+d})"


# ─── Fenêtre fiche monstre ─────────────────────────────────────────────────────

class MonsterSheetWindow:
    """
    Fenêtre Toplevel affichant la fiche complète d'un monstre D&D 5e.
    Peut être ouverte avec un monstre pré-sélectionné ou vide (avec recherche).
    """

    BG      = "#0d1117"
    BG2     = "#161b22"
    BG3     = "#1e2430"
    FG      = "#e0e0e0"
    FG_DIM  = "#666677"
    FG_MID  = "#aaaaaa"
    ACCENT  = "#e57373"       # rouge sang
    GOLD    = "#ffd54f"
    GREEN   = "#81c784"
    BLUE    = "#64b5f6"
    PURPLE  = "#ce93d8"

    def __init__(self, root, npc_name: str, bestiary_name: str | None = None,
                 on_select_callback=None, win_state: dict = None, track_fn=None,
                 chat_queue=None, audio_queue=None, npc_color: str = "#e0e0e0",
                 get_scene_fn=None):
        """
        root              : fenêtre parente Tk
        npc_name          : nom du PNJ (pour le titre)
        bestiary_name     : nom dans le bestiary (si déjà sélectionné)
        on_select_callback(bestiary_name) : appelé quand le MJ sélectionne un monstre
        win_state         : dict de persistance de géométrie
        track_fn          : fonction _track_window de DnDApp
        chat_queue        : queue.Queue — jets et répliques envoyés dans le chat
        audio_queue       : queue.Queue — texte TTS (optionnel)
        npc_color         : couleur hex du PNJ dans le chat
        get_scene_fn      : callable → str (contexte de scène, optionnel)
        """
        self.root = root
        self.npc_name = npc_name
        self.on_select_callback = on_select_callback
        self.chat_queue  = chat_queue
        self.audio_queue = audio_queue
        self.npc_color   = npc_color
        self.get_scene_fn = get_scene_fn
        self._current_monster: dict | None = None
        self._img_tk = None       # référence PhotoImage anti-GC
        self._img_bytes: bytes | None = load_npc_image_bytes(npc_name)

        _load_bestiary()

        win = tk.Toplevel(root)
        win.title(f"📋 {npc_name}" + (f" — {bestiary_name}" if bestiary_name else ""))
        win.configure(bg=self.BG)
        win.resizable(True, True)
        win.minsize(580, 640)
        win.geometry("660x840")
        self.win = win

        if track_fn:
            track_fn(f"monster_{npc_name}", win)

        # ── Layout principal ─────────────────────────────────────────────────
        # 1. Barre de recherche (fixe)
        search_bar = tk.Frame(win, bg=self.BG2, pady=6)
        search_bar.pack(fill=tk.X, padx=0, pady=0)

        tk.Label(search_bar, text="Monstre :", bg=self.BG2, fg=self.FG_MID,
                 font=("Arial", 9)).pack(side=tk.LEFT, padx=(10, 4))

        self._search_var = tk.StringVar(value=bestiary_name or "")
        search_entry = tk.Entry(search_bar, textvariable=self._search_var,
                                bg=self.BG3, fg=self.FG, font=("Consolas", 10),
                                insertbackground=self.FG, relief="flat", width=28)
        search_entry.pack(side=tk.LEFT, padx=(0, 6), ipady=4)
        search_entry.bind("<KeyRelease>", self._on_search_key)
        search_entry.bind("<Return>",     self._on_search_confirm)

        self._select_btn = tk.Button(
            search_bar, text="Selectionner",
            bg="#1a3a1a", fg=self.GREEN,
            font=("Arial", 9, "bold"), relief="flat", padx=8,
            command=self._confirm_selection
        )
        self._select_btn.pack(side=tk.RIGHT, padx=8)

        # Dropdown de suggestions
        self._suggest_frame = tk.Frame(win, bg=self.BG2, relief="flat", bd=1)
        self._suggest_labels: list[tk.Label] = []
        self._suggest_visible = False

        # 2. Zone fixe : image NPC + parler en tant que
        self._fixed_top = tk.Frame(win, bg=self.BG2)
        self._fixed_top.pack(fill=tk.X)
        self._build_image_panel(self._fixed_top)
        self._speak_frame = tk.Frame(self._fixed_top, bg="#0e1a10")
        self._speak_frame.pack(fill=tk.X)
        self._build_speak_as_content(self._speak_frame, monster=None)

        # 3. Corps scrollable de la fiche
        body_outer = tk.Frame(win, bg=self.BG)
        body_outer.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        self._canvas = tk.Canvas(body_outer, bg=self.BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(body_outer, orient="vertical", command=self._canvas.yview)
        self._inner = tk.Frame(self._canvas, bg=self.BG)
        self._inner.bind("<Configure>",
                         lambda e: self._canvas.configure(
                             scrollregion=self._canvas.bbox("all")))
        self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._canvas.configure(yscrollcommand=scrollbar.set)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # La molette sera liée récursivement après le chargement du PNJ

        # Affiche la fiche si un monstre est déjà connu
        if bestiary_name:
            self._show_monster(bestiary_name)
        else:
            self._show_empty()

    # ── Panneau image NPC ─────────────────────────────────────────────────────

    def _build_image_panel(self, parent):
        """Zone image NPC : charger, afficher, envoyer aux agents multimodaux."""
        frame = tk.Frame(parent, bg=self.BG2, pady=4)
        frame.pack(fill=tk.X, padx=0)

        # -- Miniature
        self._img_label = tk.Label(frame, bg=self.BG2, cursor="hand2",
                                   text="📷 Aucune image",
                                   fg=self.FG_DIM, font=("Consolas", 8))
        self._img_label.pack(side=tk.LEFT, padx=8)
        self._img_label.bind("<Button-1>", lambda e: self._browse_image())
        self._refresh_image_thumbnail()

        btn_col = tk.Frame(frame, bg=self.BG2)
        btn_col.pack(side=tk.LEFT, fill=tk.Y, padx=4)

        def _btn(txt, bg, fg, cmd):
            tk.Button(btn_col, text=txt, bg=bg, fg=fg, relief="flat",
                      font=("Arial", 8, "bold"), padx=6, pady=2,
                      command=cmd).pack(fill=tk.X, pady=1)

        _btn("📂 Charger image", "#1a2030", self.BLUE,  self._browse_image)
        _btn("✕ Supprimer",     "#200a0a", "#e57373",   self._clear_image)
        _btn("📡 Envoyer aux agents", "#0a2010", self.GREEN, self._send_image_to_agents)

        tk.Frame(parent, bg="#2a2a3a", height=1).pack(fill=tk.X)

    def _refresh_image_thumbnail(self):
        """Affiche ou met à jour la miniature dans l'image label."""
        lbl = getattr(self, "_img_label", None)
        if lbl is None:
            return
        data = self._img_bytes
        if not data:
            lbl.config(image="", text="📷 Aucune image", fg=self.FG_DIM, width=10)
            self._img_tk = None
            return
        try:
            from PIL import Image, ImageTk
            import io
            img = Image.open(io.BytesIO(data)).convert("RGBA")
            img.thumbnail((100, 100))
            self._img_tk = ImageTk.PhotoImage(img)
            lbl.config(image=self._img_tk, text="", width=100, height=100)
        except Exception:
            lbl.config(image="", text=f"🖼 image ({len(data)//1024}KB)", fg=self.GREEN)

    def _browse_image(self):
        """Ouvre un sélecteur de fichier pour charger une image NPC."""
        path = filedialog.askopenfilename(
            parent=self.win,
            title=f"Image pour {self.npc_name}",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.gif"), ("Tous", "*.*")],
        )
        if not path:
            return
        try:
            from PIL import Image
            import io
            img = Image.open(path).convert("RGBA")
            img.thumbnail((512, 512))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            self._img_bytes = buf.getvalue()
            save_npc_image_bytes(self.npc_name, self._img_bytes)
            self._refresh_image_thumbnail()
        except Exception as e:
            messagebox.showerror("Image", f"Impossible de charger l'image : {e}",
                                 parent=self.win)

    def _clear_image(self):
        self._img_bytes = None
        path = _npc_image_path(self.npc_name)
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        self._refresh_image_thumbnail()

    def _send_image_to_agents(self):
        """Envoie l'image NPC à tous les agents multimodaux (Gemini)."""
        if not self._img_bytes:
            messagebox.showinfo("Image", "Aucune image définie pour ce PNJ.",
                                parent=self.win)
            return
        if not self.chat_queue:
            return
        b64      = _b64.b64encode(self._img_bytes).decode()
        npc_name = self.npc_name
        msg_queue = self.chat_queue
        audio_q   = self.audio_queue

        self.chat_queue.put({
            "sender": "🖼️ Système",
            "text":   f"📸 Image de {npc_name} envoyée aux agents multimodaux.",
            "color":  "#81c784",
        })

        def _run():
            try:
                import autogen as _ag
                from app_config import get_agent_config
                from llm_config import build_llm_config

                monster = self._current_monster
                m_type  = _fmt_type(monster.get("type", "personnage")) if monster else "personnage"

                # On envoie à tous les agents Gemini (multimodaux)
                for agent_name in ["Kaelen", "Elara", "Thorne", "Lyra"]:
                    acfg  = get_agent_config(agent_name)
                    model = acfg.get("model", "")
                    if not model.startswith("gemini-"):
                        continue
                    llm_cfg = build_llm_config(model, temperature=acfg.get("temperature", 0.7))
                    client  = _ag.OpenAIWrapper(config_list=llm_cfg["config_list"])

                    prompt = (
                        f"[IMAGE NPC — CONTEXTE PRIVÉ]\n"
                        f"Le MJ te montre une illustration de {npc_name} ({m_type}).\n"
                        f"En 1-2 phrases courtes de roleplay, décris la première impression "
                        f"que {agent_name} ressent en apercevant ce personnage. "
                        f"Reste dans le personnage. Ne pose pas de question."
                    )
                    try:
                        resp = client.create(messages=[{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {
                                    "url": f"data:image/png;base64,{b64}"
                                }},
                            ]
                        }])
                        text = (resp.choices[0].message.content or "").strip()
                        if text:
                            msg_queue.put({"sender": agent_name, "text": text,
                                           "color": "#e0e0e0"})
                            if audio_q:
                                audio_q.put((text, agent_name))
                    except Exception as e:
                        msg_queue.put({"sender": "⚠ Image", "text": str(e),
                                       "color": "#F44336"})
            except Exception as e:
                msg_queue.put({"sender": "⚠ Image", "text": str(e), "color": "#F44336"})

        threading.Thread(target=_run, daemon=True).start()

    # ── Panneau "Parler en tant que" ──────────────────────────────────────────

    def _build_speak_as_panel(self, parent, monster: dict | None):
        """Construit le panneau dans un container donné (legacy compat — délègue)."""
        self._build_speak_as_content(parent, monster)

    def _build_speak_as_content(self, container: tk.Frame, monster: dict | None):
        """Construit ou reconstruit le contenu du panneau 'Parler en tant que'."""
        for w in container.winfo_children():
            w.destroy()

        container.configure(bg="#0e1a10")

        tk.Label(container, text=f"Parler en tant que {self.npc_name} :",
                 bg="#0e1a10", fg="#a5d6a7", font=("Arial", 9, "bold")
                 ).pack(anchor="w", padx=10, pady=(4, 2))

        row = tk.Frame(container, bg="#0e1a10")
        row.pack(fill=tk.X, padx=10, pady=(0, 6))

        self._speak_var = tk.StringVar()
        entry = tk.Entry(row, textvariable=self._speak_var,
                         bg="#0d1f0d", fg="white", font=("Consolas", 10),
                         insertbackground="white", relief="flat")
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5, padx=(0, 6))
        entry.insert(0, "Que dites-vous ?")
        entry.bind("<FocusIn>",
                   lambda e: (self._speak_var.get() == "Que dites-vous ?"
                              and (self._speak_var.set(""), None)))
        entry.bind("<Return>", lambda e, m=monster: self._do_speak(m))

        self._scene_ctx_var = tk.BooleanVar(value=True)
        tk.Checkbutton(row, text="Scène", variable=self._scene_ctx_var,
                       bg="#0e1a10", fg="#7aad7a", selectcolor="#0e1a10",
                       activebackground="#0e1a10", font=("Arial", 8)
                       ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(row, text="Generer", bg="#1a3a1a", fg="#81c784",
                  font=("Arial", 9, "bold"), relief="flat", padx=10, pady=4,
                  command=lambda m=monster: self._do_speak(m)
                  ).pack(side=tk.LEFT)

        if monster:
            sub = (f"{_fmt_type(monster.get('type','?'))}  "
                   f"FP {_fmt_cr(monster.get('cr','?'))}  "
                   f"Align.: {' '.join(monster.get('alignment', []))}")
            tk.Label(container, text=sub, bg="#0e1a10", fg="#3a5a3a",
                     font=("Consolas", 7)).pack(anchor="w", padx=10, pady=(0, 4))

        tk.Frame(container, bg="#2a3a2a", height=1).pack(fill=tk.X)

    def _refresh_speak_panel(self, monster: dict | None):
        """Met à jour le panneau 'Parler en tant que' quand le monstre change."""
        if hasattr(self, "_speak_frame") and self._speak_frame.winfo_exists():
            self._build_speak_as_content(self._speak_frame, monster)

    def _do_speak(self, monster: dict | None):
        """Déclenche la génération de réplique NPC via LLM."""
        if not self.chat_queue:
            return
        prompt = self._speak_var.get().strip()
        if not prompt or prompt == "Que dites-vous ?":
            prompt = ""

        scene = ""
        if self._scene_ctx_var.get() and self.get_scene_fn:
            try:
                scene = self.get_scene_fn()
            except Exception:
                pass

        self.chat_queue.put({
            "sender": "🎭 Système",
            "text":   f"{self.npc_name} prend la parole…",
            "color":  "#555566",
        })
        speak_as_npc(
            self.npc_name, monster, prompt,
            self.chat_queue, self.audio_queue,
            color=self.npc_color, scene_context=scene,
        )

    # ── Recherche ─────────────────────────────────────────────────────────────


    def _on_search_key(self, event=None):
        q = self._search_var.get().strip()
        suggestions = search_monsters(q, 10)
        self._show_suggestions(suggestions)

    def _on_search_confirm(self, event=None):
        q = self._search_var.get().strip()
        if not q:
            return
        # Essai direct
        m = get_monster(q)
        if m:
            self._hide_suggestions()
            self._show_monster(m["name"])
        else:
            # Prend la première suggestion
            suggestions = search_monsters(q, 1)
            if suggestions:
                self._search_var.set(suggestions[0])
                self._hide_suggestions()
                self._show_monster(suggestions[0])

    def _show_suggestions(self, names: list[str]):
        self._hide_suggestions()
        if not names:
            return
        x = self._canvas.winfo_x()
        y = 42  # sous la barre de recherche

        self._suggest_frame.place(x=10, y=y, width=280)
        self._suggest_frame.lift()

        for name in names:
            lbl = tk.Label(self._suggest_frame, text=name, bg=self.BG2,
                           fg=self.FG, font=("Consolas", 9),
                           anchor="w", padx=8, pady=3, cursor="hand2")
            lbl.pack(fill=tk.X)
            lbl.bind("<Button-1>", lambda e, n=name: self._pick_suggestion(n))
            lbl.bind("<Enter>",    lambda e, l=lbl: l.config(bg=self.BG3))
            lbl.bind("<Leave>",    lambda e, l=lbl: l.config(bg=self.BG2))
            self._suggest_labels.append(lbl)
        self._suggest_visible = True

    def _hide_suggestions(self):
        for lbl in self._suggest_labels:
            lbl.destroy()
        self._suggest_labels.clear()
        self._suggest_frame.place_forget()
        self._suggest_visible = False

    def _pick_suggestion(self, name: str):
        self._search_var.set(name)
        self._hide_suggestions()
        self._show_monster(name)

    def _confirm_selection(self):
        """Valide le monstre actuellement affiché et appelle le callback."""
        name = self._search_var.get().strip()
        if name and get_monster(name):
            if self.on_select_callback:
                self.on_select_callback(name)
            self.win.title(f"📋 {self.npc_name} — {name}")

    # ── Rendu de la fiche ─────────────────────────────────────────────────────

    def _clear_body(self):
        for w in self._inner.winfo_children():
            w.destroy()

    def _show_empty(self):
        self._clear_body()
        self._current_monster = None
        self._refresh_speak_panel(None)
        tk.Label(self._inner, text="Recherchez un monstre ci-dessus",
                 bg=self.BG, fg=self.FG_DIM, font=("Consolas", 10, "italic"),
                 pady=30).pack()
        tk.Label(self._inner, text="Tapez un nom et appuyez sur Entree",
                 bg=self.BG, fg=self.FG_DIM, font=("Consolas", 9)).pack()

    def _sep(self, color="#2a2a3a", height=1, pady=4):
        tk.Frame(self._inner, bg=color, height=height).pack(
            fill=tk.X, padx=8, pady=pady)

    def _section(self, title: str, color=None):
        color = color or self.GOLD
        tk.Label(self._inner, text=title.upper(), bg=self.BG, fg=color,
                 font=("Arial", 8, "bold"), anchor="w",
                 pady=3, padx=10).pack(fill=tk.X)

    def _row(self, label: str, value: str, label_color=None, value_color=None):
        label_color = label_color or self.FG_DIM
        value_color = value_color or self.FG
        row = tk.Frame(self._inner, bg=self.BG)
        row.pack(fill=tk.X, padx=10, pady=1)
        tk.Label(row, text=label, bg=self.BG, fg=label_color,
                 font=("Arial", 8), width=14, anchor="w").pack(side=tk.LEFT)
        tk.Label(row, text=value, bg=self.BG, fg=value_color,
                 font=("Consolas", 9), anchor="w", wraplength=360,
                 justify=tk.LEFT).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _text_block(self, content: str, color=None, font=None):
        color = color or self.FG_MID
        font  = font  or ("Consolas", 9)
        txt = tk.Text(self._inner, bg=self.BG2, fg=color, font=font,
                      relief="flat", wrap=tk.WORD, height=1,
                      padx=10, pady=6, state=tk.NORMAL,
                      highlightthickness=0, borderwidth=0)
        txt.insert("1.0", content)
        txt.config(state=tk.DISABLED)
        # Ajuste la hauteur automatiquement
        lines = content.count("\n") + 1
        estimated = max(2, min(lines + 1, 20))
        txt.config(height=estimated)
        txt.pack(fill=tk.X, padx=8, pady=2)
        # _bind_mouse_scroll se chargera de la molette pour ce widget

    # ── Dés & parsing actions ────────────────────────────────────────────────

    def _roll_dice(self, expr: str) -> tuple[int, str]:
        """Lance une expression de dés (ex: '2d6+5') → (total, détail)."""
        import random as _random
        import re as _re
        expr = expr.strip()
        total = 0
        detail_parts = []
        # Traite chaque terme : NdX, +N, -N
        for term in _re.finditer(r'([+-]?\s*\d*d\d+|[+-]?\s*\d+)', expr):
            t = term.group(0).replace(' ', '')
            if 'd' in t:
                sign = -1 if t.startswith('-') else 1
                t2 = t.lstrip('+-')
                parts = t2.split('d')
                n = int(parts[0]) if parts[0] else 1
                sides = int(parts[1])
                rolls = [_random.randint(1, sides) for _ in range(n)]
                s = sum(rolls)
                total += sign * s
                detail_parts.append(f"[{','.join(str(r) for r in rolls)}]")
            else:
                val = int(t.replace(' ',''))
                total += val
                detail_parts.append(str(val))
        return total, '+'.join(detail_parts).replace('+-', '-')

    def _parse_action_rolls(self, entries: list) -> dict:
        """
        Extrait depuis les entries d'une action :
          hit      : int | None  (bonus d'attaque, tag {@hit N})
          damages  : list[(expr, type)]  (tags {@damage NdX+Y}, {@scaledice...})
          dc       : int | None   ({@dc N})
          dc_save  : str | None   ({@skill X} ou inféré depuis le texte)
          desc     : str  (texte nettoyé)
        """
        import re as _re
        full_text = _fmt_entries(entries)
        raw_text  = "\n".join(e if isinstance(e, str) else "" for e in entries
                              if isinstance(e, str))

        hit_m  = _re.search(r'\{@hit\s+(-?\d+)\}', raw_text)
        dc_m   = _re.search(r'\{@dc\s+(\d+)\}', raw_text)

        dmg_tags = _re.findall(r'\{@damage\s+([^}]+)\}', raw_text)
        type_tags = _re.findall(r'\{@damage\s+[^}]+\}\s*([a-zA-Zéâ]+(?:\s+[a-zA-Zéâ]+)?)',
                                raw_text)

        # Cherche aussi les types de dégâts depuis le texte nettoyé
        dmg_types_raw = _re.findall(
            r'(\d+d\d+(?:[+-]\d+)?)\s+(?:de\s+)?([a-zA-Zé]+(?:\s+et\s+[a-zA-Zé]+)?)\s*(?:dégâts|damage)',
            full_text, _re.IGNORECASE)

        damages = []
        for i, expr in enumerate(dmg_tags):
            t = type_tags[i] if i < len(type_tags) else ""
            damages.append((expr.strip(), t.strip()))
        if not damages and dmg_types_raw:
            for expr, typ in dmg_types_raw:
                damages.append((expr, typ))

        # Jet de sauvegarde associé
        save_m = _re.search(
            r'\{@dc\s+\d+\}[^{]*\{@skill\s+([^}]+)\}|'
            r'jet\s+de\s+sauvegarde\s+(?:de\s+)?(\w+)|'
            r'(\w+)\s+saving\s+throw',
            raw_text, _re.IGNORECASE)
        dc_save = None
        if save_m:
            dc_save = (save_m.group(1) or save_m.group(2) or save_m.group(3) or "").strip()

        return {
            "hit":     int(hit_m.group(1)) if hit_m else None,
            "dc":      int(dc_m.group(1))  if dc_m  else None,
            "dc_save": dc_save,
            "damages": damages,
            "desc":    full_text,
        }

    def _send_to_chat(self, text: str, color: str = "#f0d060"):
        """Envoie un message dans le chat principal."""
        if self.chat_queue:
            self.chat_queue.put({"sender": f"⚔ {self.npc_name}", "text": text, "color": color})

    def _action_roll_widget(self, parent, action_name: str, rolls: dict,
                            monster: dict, row_bg: str, recharge_val: int = None):
        """
        Construit le bloc interactif sous une action :
        [Attaque] [Dégât X] [DD N — Sauvegarde] [♻ Recharge]
        """
        if not rolls["hit"] and not rolls["dc"] and not rolls["damages"] and recharge_val is None:
            return  # Rien à lancer

        btn_frame = tk.Frame(parent, bg=row_bg)
        btn_frame.pack(anchor="w", padx=20, pady=(2, 6))

        from state_manager import get_npc_cooldown, set_npc_cooldown
        on_cooldown = False
        if recharge_val is not None:
            on_cooldown = get_npc_cooldown(self.npc_name, action_name)

        def _consume_if_needed(name=action_name):
            if recharge_val is not None and not get_npc_cooldown(self.npc_name, name):
                set_npc_cooldown(self.npc_name, name, True)
                if self._current_monster:
                    self.root.after(50, lambda: self._show_monster(self._current_monster["name"]))

        def _btn(text, bg, fg, cmd):
            if on_cooldown and not text.startswith("♻"):
                # Style désactivé visuellement (grisé) mais toujours cliquable par sécurité
                bg = "#2a2a2a"
                fg = "#666666"
                text = f"[En Recharge] {text}"

            tk.Button(btn_frame, text=text, bg=bg, fg=fg,
                      font=("Consolas", 8, "bold"), relief="flat",
                      padx=6, pady=2, cursor="hand2",
                      command=cmd).pack(side=tk.LEFT, padx=(0, 4))

        # ── Bouton Recharge / Statut ──────────────────────────────────────
        if recharge_val is not None:
            if on_cooldown:
                def _roll_recharge(r=recharge_val, name=action_name):
                    import random as _rnd
                    d6 = _rnd.randint(1, 6)
                    if d6 >= r:
                        res_txt = "🟢 **Réussi !** L'action est rechargée."
                        color = "#81c784"
                        set_npc_cooldown(self.npc_name, name, False)
                    else:
                        res_txt = "🔴 **Échec.** Doit encore recharger."
                        color = "#e57373"
                    msg = f"**{name}** — Jet de Recharge (Recharge {r}-6)\n  d6({d6}) : {res_txt}"
                    self._send_to_chat(msg, color)
                    if self._current_monster:
                        self.root.after(50, lambda: self._show_monster(self._current_monster["name"]))
                
                _btn(f"♻ Tenter Recharge {recharge_val}+", "#302607", "#ffd54f", _roll_recharge)
            else:
                def _mark_used(name=action_name):
                    set_npc_cooldown(self.npc_name, name, True)
                    if self._current_monster:
                        self.root.after(50, lambda: self._show_monster(self._current_monster["name"]))
                _btn("🟢 Action Prête (marquer utilisée)", "#1a351a", "#81c784", _mark_used)

        # ── Bouton Attaque ──────────────────────────────────────────────
        if rolls["hit"] is not None:
            bonus = rolls["hit"]
            sign  = "+" if bonus >= 0 else ""

            def _roll_attack(b=bonus, name=action_name):
                _consume_if_needed(name)
                import random as _rnd
                d20  = _rnd.randint(1, 20)
                tot  = d20 + b
                sign2 = "+" if b >= 0 else ""
                crit = " 🎯 CRITIQUE!" if d20 == 20 else (" ☠ FUMBLE" if d20 == 1 else "")
                msg = f"**{name}** — Attaque\n  d20({d20}) {sign2}{b} = **{tot}**{crit}"
                self._send_to_chat(msg, "#e57373")

            _btn(f"Attaque {sign}{bonus}", "#3a1010", "#e57373", _roll_attack)

        # ── Bouton(s) Dégâts ────────────────────────────────────────────
        for i, (expr, dmg_type) in enumerate(rolls["damages"]):
            lbl_type = f" {dmg_type}" if dmg_type else ""
            btn_text = f"Dégâts{lbl_type} ({expr})" if i == 0 else f"+ {expr}{lbl_type}"

            def _roll_damage(e=expr, t=dmg_type, name=action_name):
                _consume_if_needed(name)
                total, detail = self._roll_dice(e)
                type_str = f" {t}" if t else ""
                msg = f"**{name}** — Dégâts{type_str}\n  {e} → {detail} = **{total}**"
                self._send_to_chat(msg, "#ffb74d")

            _btn(btn_text, "#2a1800", "#ffb74d", _roll_damage)

        # ── Bouton DD / Sauvegarde ──────────────────────────────────────
        if rolls["dc"] is not None:
            save_lbl = rolls["dc_save"].upper() if rolls["dc_save"] else "SAU"
            dc_val   = rolls["dc"]

            def _show_dc(dc=dc_val, sv=save_lbl, name=action_name):
                _consume_if_needed(name)
                msg = f"**{name}** — Jet de sauvegarde\n  DD {dc} ({sv}) — les cibles doivent réussir !"
                self._send_to_chat(msg, "#64b5f6")

            _btn(f"DD {dc_val} — {save_lbl}", "#0a1a30", "#64b5f6", _show_dc)

    def _skill_roll_widget(self, parent, monster: dict, row_bg: str):
        """
        Bloc interactif complet :
          • Initiative
          • 6 caractéristiques brutes
          • 6 sauvegardes (avec bonus proficiency si présent dans fiche, sinon stat seule)
          • Toutes les compétences (proficiency si dans fiche, sinon stat de base)
        """
        import random as _rnd

        STAT_MAP_FR = {
            "str": ("FOR", "#e57373"), "dex": ("DEX", "#81c784"),
            "con": ("CON", "#ffb74d"), "int": ("INT", "#64b5f6"),
            "wis": ("SAG", "#ce93d8"), "cha": ("CHA", "#f06292"),
        }
        SAVE_FR = {
            "str": "FOR", "dex": "DEX", "con": "CON",
            "int": "INT", "wis": "SAG", "cha": "CHA",
        }

        outer = tk.Frame(parent, bg=row_bg)
        outer.pack(fill=tk.X, padx=8, pady=(0, 8))

        def _section_lbl(txt):
            tk.Label(outer, text=txt, bg=row_bg, fg=self.FG_DIM,
                     font=("Consolas", 7, "bold")).pack(anchor="w", padx=2, pady=(6, 1))

        def _btn_wrap():
            f = tk.Frame(outer, bg=row_bg)
            f.pack(fill=tk.X)
            return f

        def _qbtn(wrap, text, bg, fg, cmd):
            b = tk.Button(wrap, text=text, bg=bg, fg=fg,
                          font=("Consolas", 7, "bold"), relief="flat",
                          padx=5, pady=2, cursor="hand2", command=cmd)
            b.pack(side=tk.LEFT, padx=2, pady=1)

        def _roll_d20(bonus: int, label: str, color: str):
            d20  = _rnd.randint(1, 20)
            tot  = d20 + bonus
            sign = "+" if bonus >= 0 else ""
            crit = " 🎯 CRITIQUE!" if d20 == 20 else (" ☠ FUMBLE" if d20 == 1 else "")
            msg  = f"**{label}** : d20({d20}){sign}{bonus} = **{tot}**{crit}"
            self._send_to_chat(msg, color)

        # ── Initiative ───────────────────────────────────────────────────────
        _section_lbl("INITIATIVE")
        w = _btn_wrap()
        dex_val = monster.get("dex", 10)
        dex_mod = (dex_val - 10) // 2
        sign    = "+" if dex_mod >= 0 else ""
        _qbtn(w, f"Initiative {sign}{dex_mod}", "#101820", "#81c784",
              lambda m=dex_mod: _roll_d20(m, "Initiative", "#81c784"))

        # ── Caractéristiques ─────────────────────────────────────────────────
        _section_lbl("CARACTÉRISTIQUES")
        w = _btn_wrap()
        for key, (label, color) in STAT_MAP_FR.items():
            val = monster.get(key, 10)
            mod = (val - 10) // 2
            s   = "+" if mod >= 0 else ""
            _qbtn(w, f"{label} {s}{mod}", "#1a1a2a", color,
                  lambda m=mod, l=f"Jet de {label}", c=color: _roll_d20(m, l, c))

        # ── Sauvegardes (TOUTES, avec proficiency si dispo) ──────────────────
        _section_lbl("SAUVEGARDES")
        w = _btn_wrap()
        saves_dict = monster.get("save", {})
        for key, (label, color) in STAT_MAP_FR.items():
            if key in saves_dict:
                # Proficiency explicite dans la fiche
                import re as _re4
                m4 = _re4.search(r'([+-]?\d+)', str(saves_dict[key]))
                bonus = int(m4.group(1)) if m4 else 0
                star  = "★"
            else:
                # Pas de proficiency — bonus = mod de stat seul
                bonus = (monster.get(key, 10) - 10) // 2
                star  = ""
            sign = "+" if bonus >= 0 else ""
            fr   = SAVE_FR.get(key, key.upper())
            _qbtn(w, f"Sauv.{fr}{star} {sign}{bonus}", "#0a1a0a", color,
                  lambda b=bonus, l=f"Sauvegarde {fr}", c=color: _roll_d20(b, l, c))

        # ── Compétences (TOUTES, avec proficiency si dispo) ──────────────────
        _section_lbl("COMPÉTENCES")
        skills_dict = monster.get("skill", {})

        # On regroupe les compétences par stat pour garder l'ordre logique
        import re as _re5
        for stat_key in ("str", "dex", "int", "wis", "cha"):
            color = STAT_MAP_FR[stat_key][1]
            stat_val = monster.get(stat_key, 10)
            stat_mod = (stat_val - 10) // 2
            row_skills = [(k, v) for k, v in _SKILL_TO_STAT.items() if v == stat_key]
            if not row_skills:
                continue
            w = _btn_wrap()
            for skill_en, _ in sorted(row_skills, key=lambda x: x[0]):
                fr_name = _SKILL_FR.get(skill_en, skill_en.capitalize())
                # Cherche le bonus dans la fiche (5etools stocke "perception": "+5" etc.)
                match_key = next(
                    (k for k in skills_dict
                     if k.lower().replace(" ", "") == skill_en.replace(" ", "")), None
                )
                if match_key:
                    m5 = _re5.search(r'([+-]?\d+)', str(skills_dict[match_key]))
                    bonus = int(m5.group(1)) if m5 else stat_mod
                    star  = "★"
                else:
                    bonus = stat_mod
                    star  = ""
                sign = "+" if bonus >= 0 else ""
                short_fr = fr_name[:10]
                _qbtn(w, f"{short_fr}{star} {sign}{bonus}", "#0d0d1a", color,
                      lambda b=bonus, l=fr_name, c=color: _roll_d20(b, l, c))



    def _action_block(self, parent, action: dict, monster: dict,
                      name_color: str, row_bg: str):
        """Rend une action complète : titre + description + boutons de lancer."""
        raw_name = action.get("name", "?")
        
        recharge_val = None
        import re as _re
        
        m_tag = _re.search(r'\{@recharge\s+(\d+)\}', raw_name)
        if m_tag:
            recharge_val = int(m_tag.group(1))
            a_name = _re.sub(r'\s*\{@recharge\s+\d+\}', f' (Recharge {recharge_val}-6)', raw_name)
        else:
            m_text = _re.search(r'\(Recharge\s+(\d+)(?:-\d+)?\)', raw_name, _re.IGNORECASE)
            if m_text:
                recharge_val = int(m_text.group(1))
            a_name = raw_name

        entries = action.get("entries", [])
        a_desc  = _fmt_entries(entries)
        rolls   = self._parse_action_rolls(entries)

        tk.Label(parent, text=f"▸ {a_name}", bg=self.BG, fg=name_color,
                 font=("Consolas", 9, "bold"), anchor="w", padx=10,
                 pady=2).pack(fill=tk.X)
        if a_desc:
            tk.Label(parent, text=a_desc, bg=self.BG, fg=self.FG_MID,
                     font=("Consolas", 9), anchor="w", padx=20,
                     wraplength=520, justify=tk.LEFT).pack(fill=tk.X)
        self._action_roll_widget(parent, a_name, rolls, monster, self.BG, recharge_val=recharge_val)

    # ── Spellcasting ─────────────────────────────────────────────────────────

    @staticmethod
    def _clean_spell_name(raw: str) -> str:
        """Extrait le nom lisible d'un tag {@spell nom} ou retourne la chaîne brute."""
        import re as _re
        m = _re.search(r'\{@spell\s+([^|}]+)', raw)
        return m.group(1).strip() if m else raw.strip()

    def _spellcasting_block(self, parent, sc: dict, monster: dict):
        """
        Rend un bloc spellcasting complet (standard ou inné) :
          • En-tête descriptif avec boutons DD / Attaque de sort
          • Cantrips (niveau 0, à volonté)
          • Emplacements par niveau (1-9)
          • Sorts inné : à volonté, X/jour
        """
        import re as _re

        SPELL_LEVEL_FR = {
            "0": "Sorts mineurs",
            "1": "Niveau 1", "2": "Niveau 2", "3": "Niveau 3",
            "4": "Niveau 4", "5": "Niveau 5", "6": "Niveau 6",
            "7": "Niveau 7", "8": "Niveau 8", "9": "Niveau 9",
        }
        ABILITY_FR = {
            "int": "INT", "wis": "SAG", "cha": "CHA",
            "str": "FOR", "dex": "DEX", "con": "CON",
        }
        SPELL_BG   = "#0d0d1f"
        SPELL_FG   = "#b39ddb"
        CANTRIP_FG = "#9b8fc7"
        SLOT_FG    = "#ce93d8"
        INNATE_FG  = "#80cbc4"
        HDR_FG     = "#e0d0ff"

        sc_name = sc.get("name", "Spellcasting")
        ability = sc.get("ability", "int")
        header_entries = sc.get("headerEntries", [])
        header_text = _fmt_entries(header_entries)

        # Extraire DC et bonus d'attaque depuis le header
        dc_m   = _re.search(r'\{@dc\s+(\d+)\}',  header_text + " ".join(str(e) for e in header_entries))
        hit_m  = _re.search(r'\{@hit\s+(-?\d+)\}', " ".join(str(e) for e in header_entries))
        spell_dc  = int(dc_m.group(1))  if dc_m  else None
        spell_hit = int(hit_m.group(1)) if hit_m else None

        # ── Titre de section ──────────────────────────────────────────────────
        title_row = tk.Frame(parent, bg=SPELL_BG)
        title_row.pack(fill=tk.X, padx=8, pady=(6, 0))

        tk.Label(title_row, text=f"✨ {sc_name}",
                 bg=SPELL_BG, fg=SPELL_FG,
                 font=("Consolas", 9, "bold"), anchor="w").pack(side=tk.LEFT, padx=4)

        ability_lbl = ABILITY_FR.get(ability, ability.upper())
        tk.Label(title_row, text=f"({ability_lbl})",
                 bg=SPELL_BG, fg=self.FG_DIM,
                 font=("Consolas", 8)).pack(side=tk.LEFT, padx=2)

        # Boutons DD et Attaque de sort
        if spell_dc is not None or spell_hit is not None:
            btn_row = tk.Frame(title_row, bg=SPELL_BG)
            btn_row.pack(side=tk.RIGHT, padx=4)

            if spell_dc is not None:
                dc_val = spell_dc
                def _show_dc(dc=dc_val, ab=ability_lbl, name=sc_name):
                    self._send_to_chat(
                        f"✨ **{name}** — DD de sauvegarde\n  DD {dc} ({ab}) — les cibles doivent réussir !",
                        "#9b8fc7")
                tk.Button(btn_row, text=f"DD {spell_dc}",
                          bg="#1a0d2e", fg="#ce93d8",
                          font=("Consolas", 8, "bold"), relief="flat",
                          padx=6, pady=2, cursor="hand2",
                          command=_show_dc).pack(side=tk.LEFT, padx=(0, 4))

            if spell_hit is not None:
                hit_val = spell_hit
                sign    = "+" if hit_val >= 0 else ""
                def _roll_spell_attack(b=hit_val, name=sc_name):
                    import random as _rnd
                    d20  = _rnd.randint(1, 20)
                    tot  = d20 + b
                    s    = "+" if b >= 0 else ""
                    crit = " 🎯 CRITIQUE!" if d20 == 20 else (" ☠ FUMBLE" if d20 == 1 else "")
                    self._send_to_chat(
                        f"✨ **{name}** — Attaque de sort\n  d20({d20}){s}{b} = **{tot}**{crit}",
                        "#ce93d8")
                tk.Button(btn_row, text=f"Attaque {sign}{hit_val}",
                          bg="#1a0d2e", fg="#b39ddb",
                          font=("Consolas", 8, "bold"), relief="flat",
                          padx=6, pady=2, cursor="hand2",
                          command=_roll_spell_attack).pack(side=tk.LEFT)

        # ── Description / header ──────────────────────────────────────────────
        clean_header = _re.sub(r'\{@(?:dc|hit)\s+[^}]+\}',
                               lambda mo: (f"DD {mo.group(0)[4:-1]}" if "@dc" in mo.group(0)
                                           else f"+{mo.group(0)[5:-1]}"),
                               header_text)
        clean_header = _re.sub(r'\{@[^}]+\}', '', clean_header).strip()
        if clean_header:
            tk.Label(parent, text=clean_header,
                     bg=SPELL_BG, fg=self.FG_DIM,
                     font=("Consolas", 8, "italic"), anchor="w",
                     padx=14, wraplength=520, justify=tk.LEFT).pack(fill=tk.X)

        def _spell_btn(wrap, spell_raw: str, fg: str):
            """
            Groupe sort : [Nom du sort ──────────][▶]
              • Clic sur le nom  → ouvre SpellSheetWindow (fiche lecture seule)
              • Clic sur ▶       → envoie "Lancé : X" dans le chat
            """
            name = self._clean_spell_name(spell_raw)

            # Conteneur groupé (nom + bouton cast collés)
            grp = tk.Frame(wrap, bg="#130d24", bd=0, highlightthickness=0)
            grp.pack(side=tk.LEFT, padx=2, pady=1)

            def _open_sheet(n=name):
                try:
                    from spell_data import SpellSheetWindow, get_spell
                    sp = get_spell(n)
                    if sp:
                        SpellSheetWindow(self.win, sp)
                    else:
                        self._send_to_chat(f"✨ {n}  (fiche de sort introuvable)", "#b39ddb")
                except ImportError:
                    self._send_to_chat(f"✨ {n}", "#b39ddb")

            def _cast(n=name):
                self._send_to_chat(f"✨ **Lancé :** {n}", "#b39ddb")

            # ── Bouton nom → fiche ────────────────────────────────────────────
            tk.Button(grp, text=name,
                      bg="#130d24", fg=fg,
                      font=("Consolas", 8), relief="flat",
                      padx=5, pady=1, cursor="hand2",
                      command=_open_sheet).pack(side=tk.LEFT)

            # ── Petit bouton carré → lancer ───────────────────────────────────
            tk.Button(grp, text="▶",
                      bg="#1a0f30", fg="#9b8fc7",
                      font=("Consolas", 7, "bold"), relief="flat",
                      padx=3, pady=1, cursor="hand2",
                      command=_cast).pack(side=tk.LEFT)

        # ── Emplacements de sorts (standard) ─────────────────────────────────
        spells_by_level = sc.get("spells", {})
        for lvl_key in sorted(spells_by_level.keys(), key=lambda x: int(x)):
            lvl_data  = spells_by_level[lvl_key]
            spell_list = lvl_data.get("spells", [])
            if not spell_list:
                continue
            slots = lvl_data.get("slots")
            lvl_int = int(lvl_key)

            # Label de niveau
            row_lbl = tk.Frame(parent, bg=SPELL_BG)
            row_lbl.pack(fill=tk.X, padx=14, pady=(4, 0))

            lvl_fr = SPELL_LEVEL_FR.get(lvl_key, f"Niveau {lvl_key}")
            fg_lbl = CANTRIP_FG if lvl_int == 0 else SLOT_FG
            lbl_txt = lvl_fr
            if slots is not None:
                lbl_txt += f"  ({slots} emplacement{'s' if slots > 1 else ''})"

            tk.Label(row_lbl, text=lbl_txt,
                     bg=SPELL_BG, fg=fg_lbl,
                     font=("Arial", 8, "bold"), anchor="w").pack(side=tk.LEFT)

            # Emplacements visuels (petits carrés)
            if slots:
                for _ in range(min(slots, 9)):
                    tk.Label(row_lbl, text="□",
                             bg=SPELL_BG, fg=SLOT_FG,
                             font=("Consolas", 9)).pack(side=tk.LEFT, padx=1)

            # Boutons des sorts
            spell_wrap = tk.Frame(parent, bg=SPELL_BG)
            spell_wrap.pack(fill=tk.X, padx=24, pady=(1, 2))
            for sp in spell_list:
                _spell_btn(spell_wrap, sp, fg_lbl)

        # ── Sorts inné : à volonté (will) ─────────────────────────────────────
        will_spells = sc.get("will", [])
        if will_spells:
            row_lbl = tk.Frame(parent, bg=SPELL_BG)
            row_lbl.pack(fill=tk.X, padx=14, pady=(4, 0))
            tk.Label(row_lbl, text="À volonté",
                     bg=SPELL_BG, fg=INNATE_FG,
                     font=("Arial", 8, "bold"), anchor="w").pack(side=tk.LEFT)
            spell_wrap = tk.Frame(parent, bg=SPELL_BG)
            spell_wrap.pack(fill=tk.X, padx=24, pady=(1, 2))
            for sp in will_spells:
                _spell_btn(spell_wrap, sp, INNATE_FG)

        # ── Sorts innés : X/jour ───────────────────────────────────────────────
        daily = sc.get("daily", {})
        DAILY_LABELS = {
            "1": "1/jour", "1e": "1/jour chacun",
            "2": "2/jour", "2e": "2/jour chacun",
            "3": "3/jour", "3e": "3/jour chacun",
            "4": "4/jour", "4e": "4/jour chacun",
        }
        for freq_key in sorted(daily.keys()):
            freq_spells = daily[freq_key]
            if not freq_spells:
                continue
            freq_lbl = DAILY_LABELS.get(freq_key, f"{freq_key}/jour")
            row_lbl = tk.Frame(parent, bg=SPELL_BG)
            row_lbl.pack(fill=tk.X, padx=14, pady=(4, 0))
            tk.Label(row_lbl, text=freq_lbl,
                     bg=SPELL_BG, fg=INNATE_FG,
                     font=("Arial", 8, "bold"), anchor="w").pack(side=tk.LEFT)
            spell_wrap = tk.Frame(parent, bg=SPELL_BG)
            spell_wrap.pack(fill=tk.X, padx=24, pady=(1, 2))
            for sp in freq_spells:
                _spell_btn(spell_wrap, sp, INNATE_FG)

        # Ligne de séparation finale
        tk.Frame(parent, bg="#2a1a4a", height=1).pack(fill=tk.X, padx=8, pady=(6, 0))

    def _show_monster(self, name: str):
        m = get_monster(name)
        if not m:
            self._show_empty()
            return

        self._current_monster = m
        self._clear_body()
        self._hide_suggestions()
        self.win.title(f"[{self.npc_name}] {name}")

        # Mettre à jour le panneau "Parler en tant que" avec le nouveau monstre
        self._refresh_speak_panel(m)

        # ── EN-TÊTE ─────────────────────────────────────────────────────────
        hdr = tk.Frame(self._inner, bg="#1a0808", pady=8)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text=m.get("name", "?"), bg="#1a0808", fg=self.ACCENT,
                 font=("Arial", 16, "bold"), anchor="w", padx=12).pack(side=tk.LEFT)
        cr_txt = f"FP {_fmt_cr(m.get('cr', '?'))}"
        tk.Label(hdr, text=cr_txt, bg="#1a0808", fg=self.GOLD,
                 font=("Consolas", 11, "bold"), anchor="e", padx=12).pack(side=tk.RIGHT)

        # Taille · Type · Alignement
        size_map = {"T": "Très petit", "S": "Petit", "M": "Moyen",
                    "L": "Grand", "H": "Très grand", "G": "Gigantesque"}
        align_map = {"L": "Loyal", "N": "Neutre", "C": "Chaotique",
                     "G": "Bon", "E": "Mauvais", "A": "Quelconque",
                     "U": "Sans alignement"}
        sizes = [size_map.get(s, s) for s in m.get("size", [])]
        type_txt = _fmt_type(m.get("type", "?"))
        align_raw = m.get("alignment", [])
        align_txt = " ".join(align_map.get(a, a) for a in align_raw)

        tk.Label(self._inner, text=f"{' / '.join(sizes)} {type_txt}, {align_txt}",
                 bg=self.BG, fg=self.FG_MID, font=("Arial", 9, "italic"),
                 anchor="w", padx=10).pack(fill=tk.X, pady=(4, 0))

        self._sep(color="#5a1a1a", height=2, pady=4)

        # ── STATS DÉFENSIVES ────────────────────────────────────────────────
        hp = m.get("hp", {})
        hp_txt = f"{hp.get('average','?')} ({hp.get('formula','?')})"
        self._row("Classe d'Armure", _fmt_ac(m.get("ac", [])), value_color=self.GREEN)
        self._row("Points de Vie",   hp_txt,                    value_color=self.GREEN)
        self._row("Vitesse",         _fmt_speed(m.get("speed", {})))

        self._sep()

        # ── CARACTÉRISTIQUES ────────────────────────────────────────────────
        self._section("Caractéristiques", self.GOLD)
        stats_frame = tk.Frame(self._inner, bg=self.BG2, pady=6)
        stats_frame.pack(fill=tk.X, padx=8, pady=4)

        STAT_LABELS = [("FOR", "str"), ("DEX", "dex"), ("CON", "con"),
                       ("INT", "int"), ("SAG", "wis"), ("CHA", "cha")]
        STAT_COLORS = {"FOR": "#e57373", "DEX": "#81c784", "CON": "#ffb74d",
                       "INT": "#64b5f6", "SAG": "#ce93d8", "CHA": "#f06292"}

        for i, (label, key) in enumerate(STAT_LABELS):
            col = tk.Frame(stats_frame, bg=self.BG2)
            col.grid(row=0, column=i, padx=6, pady=2, sticky="n")
            c = STAT_COLORS.get(label, self.FG)
            tk.Label(col, text=label, bg=self.BG2, fg=c,
                     font=("Arial", 8, "bold")).pack()
            val = m.get(key, 10)
            mod = (val - 10) // 2
            tk.Label(col, text=str(val), bg=self.BG2, fg=self.FG,
                     font=("Consolas", 11, "bold")).pack()
            tk.Label(col, text=f"({mod:+d})", bg=self.BG2, fg=self.FG_MID,
                     font=("Consolas", 8)).pack()
        for i in range(6):
            stats_frame.columnconfigure(i, weight=1)

        self._sep()

        # ── SAUVEGARDES & COMPÉTENCES ────────────────────────────────────────
        saves = m.get("save", {})
        if saves:
            self._row("Jets de sauvegarde",
                      "  ".join(f"{k.upper()} {v}" for k, v in saves.items()),
                      value_color=self.BLUE)

        skills = m.get("skill", {})
        if skills:
            self._row("Compétences",
                      "  ".join(f"{k.capitalize()} {v}" for k, v in skills.items()),
                      value_color=self.BLUE)

        # Résistances / Immunités
        dr = m.get("resist", [])
        di = m.get("immune", [])
        ci = m.get("conditionImmune", [])
        senses = m.get("senses", [])
        passive = m.get("passive", "?")
        langs = m.get("languages", [])

        if dr:
            self._row("Résistances",     _fmt_damage_list(dr, "resist"), value_color="#ffb74d")
        if di:
            self._row("Immunités dégâts", _fmt_damage_list(di, "immune"), value_color="#e57373")
        if ci:
            self._row("Immunités états",  _fmt_condition_list(ci),        value_color="#e57373")
        if senses:
            self._row("Sens", ", ".join(senses) + f", Perception passive {passive}")
        if langs:
            self._row("Langues", ", ".join(langs))

        self._sep()

        # ── JETS RAPIDES (stats / sauvegardes / compétences) ──────────────────
        self._sep(color="#1a1a2a")
        self._section("Jets Rapides", "#888899")
        self._skill_roll_widget(self._inner, m, self.BG)
        self._sep(color="#1a1a2a")

        # ── TRAITS ──────────────────────────────────────────────────────────
        traits = m.get("trait", [])
        if traits:
            self._section("Traits", self.PURPLE)
            for t in traits:
                self._action_block(self._inner, t, m, self.PURPLE, self.BG)
            self._sep()

        # ── SORTS (SPELLCASTING) ─────────────────────────────────────────────
        spellcasting = m.get("spellcasting", [])
        if spellcasting:
            self._section("Sorts", "#b39ddb")
            spell_outer = tk.Frame(self._inner, bg="#0d0d1f")
            spell_outer.pack(fill=tk.X, padx=4, pady=(0, 4))
            for sc in spellcasting:
                self._spellcasting_block(spell_outer, sc, m)
            self._sep(color="#2a1a4a")

        # ── ACTIONS ─────────────────────────────────────────────────────────
        actions = m.get("action", [])
        if actions:
            self._section("Actions", self.ACCENT)
            for a in actions:
                self._action_block(self._inner, a, m, self.ACCENT, self.BG)
            self._sep()

        # ── ACTIONS BONUS ────────────────────────────────────────────────────
        bonus = m.get("bonus_action", m.get("bonusAction", []))
        if bonus:
            self._section("Actions Bonus", "#ffb74d")
            for a in bonus:
                self._action_block(self._inner, a, m, "#ffb74d", self.BG)
            self._sep()

        # ── RÉACTIONS ────────────────────────────────────────────────────────
        reactions = m.get("reaction", [])
        if reactions:
            self._section("Réactions", self.BLUE)
            for r in reactions:
                self._action_block(self._inner, r, m, self.BLUE, self.BG)
            self._sep()

        # ── ACTIONS LÉGENDAIRES ───────────────────────────────────────────────
        legendary = m.get("legendary", [])
        leg_group_name = m.get("legendaryGroup", {})
        if isinstance(leg_group_name, dict):
            leg_group_name = leg_group_name.get("name", "")

        if legendary or leg_group_name:
            self._section("Actions Légendaires", self.GOLD)
            lg = get_legendary_group(leg_group_name) if leg_group_name else None
            if lg:
                intro = _fmt_entries(lg.get("lairActions", lg.get("regional", [])))
                if intro:
                    tk.Label(self._inner, text=intro, bg=self.BG, fg=self.FG_DIM,
                             font=("Consolas", 8, "italic"), anchor="w", padx=10,
                             wraplength=540, justify=tk.LEFT, pady=3).pack(fill=tk.X)
            for la in legendary:
                self._action_block(self._inner, la, m, self.GOLD, self.BG)
            self._sep()

        # ── LORE / FLUFF ──────────────────────────────────────────────────────
        fluff = get_monster_fluff(name)
        if fluff:
            fluff_text = _fmt_entries(fluff.get("entries", []))
            if fluff_text and fluff_text.strip():
                self._section("Lore", self.FG_DIM)
                self._text_block(fluff_text[:1200] + ("…" if len(fluff_text) > 1200 else ""),
                                 color=self.FG_DIM)

        # ── Pad bas ──────────────────────────────────────────────────────────
        tk.Frame(self._inner, bg=self.BG, height=20).pack()
        self._canvas.yview_moveto(0)
        self._bind_mouse_scroll(self._inner)

    def _bind_mouse_scroll(self, parent):
        """Bind les événements de défilement de façon récursive (Linux + Win/Mac)."""
        def _on_mousewheel(event):
            # Pour Windows/Mac (delta) et Linux (num 4/5)
            if event.num == 4 or getattr(event, "delta", 0) > 0:
                self._canvas.yview_scroll(-1, "units")
            elif event.num == 5 or getattr(event, "delta", 0) < 0:
                self._canvas.yview_scroll(1, "units")

        def _recursive_bind(w):
            w.bind("<MouseWheel>", _on_mousewheel)
            w.bind("<Button-4>", _on_mousewheel)
            w.bind("<Button-5>", _on_mousewheel)
            for c in w.winfo_children():
                # On évite de binder si le widget gère lui-même un scroll natif sensible
                _recursive_bind(c)

        _recursive_bind(parent)


# ─── Panel PNJs du groupe (intégré dans la sidebar de DnDApp) ─────────────────

class GroupNPCPanel:
    """
    Panneau latéral listant les PNJs actuellement dans le groupe.
    Cliquer sur un nom ouvre MonsterSheetWindow.
    Géré dans state_manager via get_group_npcs / save_group_npcs.
    """

    BG     = "#0d1a0d"
    BG2    = "#0f1f0f"
    FG     = "#a5d6a7"
    FG_DIM = "#3a5a3a"
    ACCENT = "#4CAF50"

    def __init__(self, parent_frame: tk.Frame, root, win_state: dict,
                 save_win_state_fn, track_fn, msg_queue, audio_queue=None,
                 get_scene_fn=None):
        self.root             = root
        self._win_state       = win_state
        self._save_ws         = save_win_state_fn
        self._track           = track_fn
        self._msg_queue       = msg_queue
        self._audio_queue     = audio_queue
        self._get_scene_fn    = get_scene_fn
        self._open_sheets: dict[str, MonsterSheetWindow] = {}

        # Import ici pour éviter une dépendance circulaire
        from state_manager import get_group_npcs, save_group_npcs
        self._get_npcs  = get_group_npcs
        self._save_npcs = save_group_npcs

        # ── Conteneur principal ───────────────────────────────────────────────
        self._frame = tk.Frame(parent_frame, bg=self.BG)
        self._frame.pack(fill=tk.X, padx=8, pady=(0, 4))

        # En-tête
        hdr = tk.Frame(self._frame, bg=self.BG)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="👥 PNJs DU GROUPE", bg=self.BG, fg=self.FG,
                 font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=6, pady=(6, 2))
        tk.Button(hdr, text="＋", bg=self.BG, fg=self.ACCENT,
                  font=("Arial", 10, "bold"), relief="flat",
                  command=self._add_npc).pack(side=tk.RIGHT, padx=4, pady=2)

        # Zone de la liste
        self._list_frame = tk.Frame(self._frame, bg=self.BG)
        self._list_frame.pack(fill=tk.X)

        self._refresh()

    def _refresh(self):
        """Reconstruit la liste des PNJs du groupe."""
        for w in self._list_frame.winfo_children():
            w.destroy()

        npcs = self._get_npcs()
        if not npcs:
            tk.Label(self._list_frame, text="Aucun PNJ dans le groupe",
                     bg=self.BG, fg=self.FG_DIM,
                     font=("Consolas", 8, "italic"),
                     anchor="w", padx=8, pady=4).pack(fill=tk.X)
            return

        for i, npc in enumerate(npcs):
            name      = npc.get("name", "?")
            bestiary  = npc.get("bestiary_name", "")
            color     = npc.get("color", self.FG)
            hp_cur    = npc.get("hp_current")
            row_bg    = "#0d1a0d" if i % 2 == 0 else "#0f220f"

            row = tk.Frame(self._list_frame, bg=row_bg)
            row.pack(fill=tk.X, pady=1)

            # ── Indicateur monstre associé ────────────────────────────────────
            icon = "📋" if bestiary else "❓"
            tk.Label(row, text=icon, bg=row_bg,
                     fg=color if bestiary else self.FG_DIM,
                     font=("TkDefaultFont", 9)).pack(side=tk.LEFT, padx=(4, 1), pady=3)

            # ── Nom cliquable → ouvre la fiche ────────────────────────────────
            name_lbl = tk.Label(row, text=name, bg=row_bg, fg=color,
                                font=("Consolas", 9, "bold"), anchor="w",
                                cursor="hand2")
            name_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=3)
            for w2 in (row, name_lbl):
                w2.bind("<Button-1>",
                        lambda e, n=name, b=bestiary: self._open_sheet(n, b))

            # ── PV cliquables → édition inline ────────────────────────────────
            if hp_cur is not None:
                m_data = get_monster(bestiary) if bestiary else None
                hp_max = m_data.get("hp", {}).get("average", "?") if m_data else "?"
                hp_color = (
                    "#81c784" if (isinstance(hp_max, int) and hp_cur > hp_max * 0.5)
                    else "#FF9800" if (isinstance(hp_max, int) and hp_cur > hp_max * 0.25)
                    else "#e57373"
                )
                hp_lbl = tk.Label(row, text=f"❤ {hp_cur}/{hp_max}",
                                  bg=row_bg, fg=hp_color,
                                  font=("Consolas", 8), cursor="hand2")
                hp_lbl.pack(side=tk.RIGHT, padx=(0, 2))
                hp_lbl.bind("<Button-1>",
                            lambda e, n=name, idx=i: self._edit_hp_dialog(n, idx))

            # ── Bouton 🎭 Parler en tant que ──────────────────────────────────
            speak_btn = tk.Button(
                row, text="🎭", bg=row_bg, fg="#9b8fc7",
                font=("Arial", 9), relief="flat", padx=3, pady=1,
                cursor="hand2",
                command=lambda n=name, b=bestiary, c=color:
                    self._speak_as_dialog(n, b, c)
            )
            speak_btn.pack(side=tk.RIGHT, padx=1)

            # ── Bouton 📋 Fiche rapide ────────────────────────────────────────
            sheet_btn = tk.Button(
                row, text="📋", bg=row_bg, fg=self.FG_DIM,
                font=("Arial", 9), relief="flat", padx=3, pady=1,
                cursor="hand2",
                command=lambda n=name, b=bestiary: self._open_sheet(n, b)
            )
            sheet_btn.pack(side=tk.RIGHT, padx=1)

            # ── Bouton supprimer ──────────────────────────────────────────────
            tk.Button(row, text="✕", bg=row_bg, fg="#553333",
                      font=("Arial", 7), relief="flat", padx=2,
                      cursor="hand2",
                      command=lambda idx=i: self._remove_npc(idx)
                      ).pack(side=tk.RIGHT, padx=2)

    def _open_sheet(self, npc_name: str, bestiary_name: str | None):
        """Ouvre (ou ramène) la fiche de monstre pour ce PNJ."""
        existing = self._open_sheets.get(npc_name)
        if existing:
            try:
                existing.win.deiconify()
                existing.win.lift()
                return
            except Exception:
                pass

        # Couleur du PNJ
        npcs = self._get_npcs()
        npc_color = next(
            (n.get("color", "#e0e0e0") for n in npcs if n.get("name") == npc_name),
            "#e0e0e0"
        )

        def _on_select(new_bestiary: str):
            """Callback quand le MJ sélectionne un monstre dans la fiche."""
            npcs = self._get_npcs()
            for npc in npcs:
                if npc.get("name") == npc_name:
                    npc["bestiary_name"] = new_bestiary
                    # Initialise les PV au max du monstre
                    m = get_monster(new_bestiary)
                    if m and npc.get("hp_current") is None:
                        npc["hp_current"] = m.get("hp", {}).get("average")
                    break
            self._save_npcs(npcs)
            self._refresh()
            self._msg_queue.put({
                "sender": "📋 PNJ",
                "text":   f"{npc_name} → fiche de monstre : {new_bestiary}",
                "color":  "#a5d6a7"
            })

        sheet = MonsterSheetWindow(
            self.root, npc_name, bestiary_name,
            on_select_callback=_on_select,
            win_state=self._win_state,
            track_fn=self._track,
            chat_queue=self._msg_queue,
            audio_queue=self._audio_queue,
            npc_color=npc_color,
            get_scene_fn=self._get_scene_fn,
        )
        self._open_sheets[npc_name] = sheet

        def _on_close():
            self._open_sheets.pop(npc_name, None)
            try:
                sheet.win.destroy()
            except Exception:
                pass

        sheet.win.protocol("WM_DELETE_WINDOW", _on_close)

    # ─── Dialogue "Parler en tant que" ────────────────────────────────────────

    def _speak_as_dialog(self, npc_name: str, bestiary_name: str | None,
                         npc_color: str):
        """Fenêtre rapide : MJ tape un prompt → LLM génère la réplique du PNJ."""
        monster = get_monster(bestiary_name) if bestiary_name else None

        dlg = tk.Toplevel(self.root)
        dlg.title(f"🎭 Parler en tant que {npc_name}")
        dlg.configure(bg="#0e1a10")
        dlg.geometry("420x220")
        dlg.resizable(False, False)
        dlg.wait_visibility()
        dlg.grab_set()

        tk.Label(dlg, text=f"🎭 {npc_name} prend la parole",
                 bg="#0e1a10", fg="#a5d6a7",
                 font=("Arial", 11, "bold")).pack(pady=(14, 4))

        if monster:
            sub = f"{_fmt_type(monster.get('type','?'))}  •  FP {_fmt_cr(monster.get('cr','?'))}"
            tk.Label(dlg, text=sub, bg="#0e1a10", fg="#4a7a4a",
                     font=("Consolas", 8)).pack()

        tk.Label(dlg, text="Contexte / question (optionnel) :",
                 bg="#0e1a10", fg="#888", font=("Arial", 8)
                 ).pack(anchor="w", padx=14, pady=(10, 2))

        prompt_var = tk.StringVar()
        entry = tk.Entry(dlg, textvariable=prompt_var,
                         bg="#0d1f0d", fg="white", font=("Consolas", 10),
                         insertbackground="white", relief="flat")
        entry.pack(fill=tk.X, padx=14, ipady=6)
        entry.focus_set()

        scene_var = tk.BooleanVar(value=True)
        tk.Checkbutton(dlg, text="Inclure le contexte de scène",
                       variable=scene_var, bg="#0e1a10", fg="#7aad7a",
                       selectcolor="#0e1a10", activebackground="#0e1a10",
                       font=("Arial", 8)).pack(anchor="w", padx=14, pady=4)

        def _send():
            prompt = prompt_var.get().strip()
            scene  = ""
            if scene_var.get() and self._get_scene_fn:
                try:
                    scene = self._get_scene_fn()
                except Exception:
                    pass
            self._msg_queue.put({
                "sender": "🎭 Système",
                "text":   f"{npc_name} prend la parole…",
                "color":  "#555566",
            })
            speak_as_npc(
                npc_name, monster, prompt,
                self._msg_queue, self._audio_queue,
                color=npc_color, scene_context=scene,
            )
            dlg.destroy()

        entry.bind("<Return>", lambda e: _send())
        tk.Button(dlg, text="Générer la réplique", bg="#1a3a1a", fg="#81c784",
                  font=("Arial", 10, "bold"), relief="flat", pady=6,
                  command=_send).pack(fill=tk.X, padx=14, pady=(4, 14))

    # ─── Édition HP inline ────────────────────────────────────────────────────

    def _edit_hp_dialog(self, npc_name: str, idx: int):
        """Mini-dialog pour modifier les PV d'un PNJ directement."""
        npcs = self._get_npcs()
        if idx >= len(npcs):
            return
        npc     = npcs[idx]
        hp_cur  = npc.get("hp_current", 0)
        bestiary = npc.get("bestiary_name", "")
        m_data  = get_monster(bestiary) if bestiary else None
        hp_max  = m_data.get("hp", {}).get("average", "?") if m_data else "?"

        dlg = tk.Toplevel(self.root)
        dlg.title(f"❤ PV — {npc_name}")
        dlg.configure(bg="#1a0d0d")
        dlg.geometry("280x150")
        dlg.resizable(False, False)
        dlg.wait_visibility()
        dlg.grab_set()

        tk.Label(dlg, text=f"PV actuels de {npc_name}",
                 bg="#1a0d0d", fg="#e57373", font=("Arial", 10, "bold")
                 ).pack(pady=(12, 4))
        tk.Label(dlg, text=f"(max : {hp_max})",
                 bg="#1a0d0d", fg="#888", font=("Consolas", 8)).pack()

        hp_var = tk.StringVar(value=str(hp_cur))
        entry  = tk.Entry(dlg, textvariable=hp_var, bg="#2a0d0d", fg="white",
                          font=("Consolas", 14, "bold"), justify="center",
                          insertbackground="white", relief="flat", width=8)
        entry.pack(pady=8, ipady=6)
        entry.select_range(0, tk.END)
        entry.focus_set()

        def _save_hp():
            try:
                new_hp = int(hp_var.get())
                npcs[idx]["hp_current"] = new_hp
                self._save_npcs(npcs)
                self._refresh()
                dlg.destroy()
            except ValueError:
                entry.config(bg="#3a0000")

        entry.bind("<Return>", lambda e: _save_hp())
        tk.Button(dlg, text="Appliquer", bg="#2a1010", fg="#e57373",
                  font=("Arial", 9, "bold"), relief="flat",
                  command=_save_hp).pack()

    def _add_npc(self):
        """Ouvre une mini-fenêtre pour ajouter un PNJ au groupe."""
        dialog = tk.Toplevel(self.root)
        dialog.title("＋ Ajouter un PNJ au groupe")
        dialog.geometry("400x310")
        dialog.configure(bg="#0d1117")
        dialog.resizable(False, True)
        dialog.wait_visibility()
        dialog.grab_set()

        tk.Label(dialog, text="Nom du PNJ :", bg="#0d1117", fg="#a5d6a7",
                 font=("Arial", 10, "bold")).pack(anchor="w", padx=14, pady=(14, 2))
        name_var = tk.StringVar()
        tk.Entry(dialog, textvariable=name_var, bg="#161b22", fg="white",
                 font=("Consolas", 11), insertbackground="white",
                 relief="flat").pack(fill=tk.X, padx=14, ipady=5)

        tk.Label(dialog, text="Couleur (hex, ex: #a5d6a7) :", bg="#0d1117", fg="#888",
                 font=("Arial", 8)).pack(anchor="w", padx=14, pady=(8, 2))
        color_var = tk.StringVar(value="#a5d6a7")
        tk.Entry(dialog, textvariable=color_var, bg="#161b22", fg="white",
                 font=("Consolas", 10), insertbackground="white",
                 relief="flat", width=14).pack(anchor="w", padx=14, ipady=3)

        # ── Section Sorts ─────────────────────────────────────────────────────
        tk.Frame(dialog, bg="#2a3040", height=1).pack(fill=tk.X, padx=10, pady=(12, 4))

        spell_hdr = tk.Frame(dialog, bg="#0d1117")
        spell_hdr.pack(fill=tk.X, padx=14)
        tk.Label(spell_hdr, text="✨ Sorts du PNJ :", bg="#0d1117", fg="#9b8fc7",
                 font=("Arial", 9, "bold")).pack(side=tk.LEFT)

        _npc_spells: list = []   # sorts choisis pour ce PNJ

        def _open_spell_picker():
            try:
                from spell_data import SpellPickerDialog
            except ImportError:
                return
            def _on_pick(sp: dict):
                if not any(s["name"] == sp["name"] for s in _npc_spells):
                    _npc_spells.append(sp)
                _refresh_spell_lbl()
            SpellPickerDialog(dialog, _on_pick,
                              title="✨ Sorts — " + (name_var.get() or "PNJ"))

        def _clear_spells():
            _npc_spells.clear()
            _refresh_spell_lbl()

        def _refresh_spell_lbl():
            if _npc_spells:
                txt = "  ".join(
                    f"✨ {s['name']} (Niv {'TM' if s.get('level',1)==0 else s.get('level',1)})"
                    for s in _npc_spells
                )
                spells_lbl.config(text=txt, fg="#a855f7")
            else:
                spells_lbl.config(text="(aucun sort)", fg="#444466")

        btn_row = tk.Frame(dialog, bg="#0d1117")
        btn_row.pack(fill=tk.X, padx=14, pady=(2, 2))
        tk.Button(btn_row, text="＋ Ajouter sort",
                  bg="#1a103a", fg="#9b8fc7",
                  font=("Arial", 8, "bold"), relief="flat",
                  padx=8, pady=2,
                  command=_open_spell_picker).pack(side=tk.LEFT)
        tk.Button(btn_row, text="✕ Vider",
                  bg="#1a0808", fg="#885555",
                  font=("Arial", 7), relief="flat",
                  padx=4, pady=2,
                  command=_clear_spells).pack(side=tk.LEFT, padx=6)

        spells_lbl = tk.Label(dialog, text="(aucun sort)",
                              bg="#0d1117", fg="#444466",
                              font=("Consolas", 7, "italic"), anchor="w",
                              wraplength=370, justify=tk.LEFT)
        spells_lbl.pack(fill=tk.X, padx=14, pady=(2, 8))

        def _save():
            name = name_var.get().strip()
            if not name:
                return
            npcs = self._get_npcs()
            entry = {
                "name":  name,
                "color": color_var.get().strip() or "#a5d6a7",
                "bestiary_name": None,
                "hp_current": None,
                "notes": "",
            }
            if _npc_spells:
                entry["spells"] = list(_npc_spells)
            npcs.append(entry)
            self._save_npcs(npcs)
            self._refresh()
            dialog.destroy()

        tk.Button(dialog, text="✅ Ajouter", bg="#1a3a1a", fg="#81c784",
                  font=("Arial", 10, "bold"), relief="flat",
                  command=_save).pack(pady=10)

    def _remove_npc(self, idx: int):
        npcs = self._get_npcs()
        if 0 <= idx < len(npcs):
            npcs.pop(idx)
            self._save_npcs(npcs)
            self._refresh()