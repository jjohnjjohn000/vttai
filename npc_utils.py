"""
npc_utils.py — Utilitaires, formatteurs et intégration LLM pour les PNJs.
"""

import os
import re
import threading
import base64 as _b64

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
    """
    parts = []
    for item in entries:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, list):
            parts.append(_fmt_damage_list(item, key))
        elif isinstance(item, dict):
            if "special" in item:
                parts.append(item["special"])
            else:
                sub = item.get(key, [])
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
                msg_queue.put({
                    "action": "npc_speak",
                    "sender": npc_name,
                    "text": text,
                    "color": color
                })
                if audio_queue:
                    audio_queue.put((text, npc_name))
        except Exception as e:
            msg_queue.put({
                "sender": f"⚠ PNJ",
                "text":   f"Erreur LLM pour {npc_name} : {e}",
                "color":  "#F44336",
            })

    threading.Thread(target=_run, daemon=True, name=f"npc-speak-{npc_name}").start()