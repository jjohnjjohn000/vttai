"""
Proxy module to keep backward compatibility.
The actual implementation has been split into the `combat_map` package.
"""
import os

from combat_map_constants import *
from combat_map_constants import _sep, _darken_rgb, _darken_rgb_tuple, _compress_ranges
from combat_map_window import CombatMapWindow


# ─── Point d'entrée ───────────────────────────────────────────────────────────

def open_combat_map(parent, win_state, save_fn, track_fn,
                    msg_queue=None, inject_fn=None, update_sys_prompt_fn=None, app=None):
    return CombatMapWindow(parent, win_state=win_state,
                           save_fn=save_fn, track_fn=track_fn,
                           msg_queue=msg_queue, inject_fn=inject_fn,
                           update_sys_prompt_fn=update_sys_prompt_fn, app=app)


# ─── Export textuel de la carte pour les agents LLM ──────────────────────────

def get_map_prompt(win_state: dict, for_hero: str = "", in_combat: bool = True) -> str:
    """
    Génère une description textuelle de la carte de combat active.
    Lit depuis le fichier JSON de la carte active (nouveau système multi-cartes).
    Rétro-compatible avec l'ancien win_state["combat_map_data"].
    """
    data = {}
    try:
        active_name = win_state.get("active_map_name", "")
        if active_name:
            import json
            try:
                from app_config import get_campaign_name
                camp_name = get_campaign_name()
            except Exception:
                camp_name = "campagne"
            camp_name = "".join(
                c for c in camp_name if c.isalnum() or c in (" ", "-", "_")
            ).strip() or "campagne"
            safe_name = "".join(
                c for c in active_name if c.isalnum() or c in (" ", "-", "_")
            ).strip() or "carte"
            map_path = os.path.join("campagne", camp_name, "maps", f"{safe_name}.json")
            if os.path.exists(map_path):
                with open(map_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
    except Exception as e:
        print(f"[get_map_prompt] Erreur lecture carte : {e}")

    if not data:
        data = win_state.get("combat_map_data", {})

    tokens = data.get("tokens", [])
    if not tokens:
        return ""

    cols = data.get("cols", 30)
    rows = data.get("rows", 20)

    _fog_arr = None
    _fog_w   = 0
    _fog_h   = 0
    try:
        fog_b64 = (data.get("fog_b64") or data.get("fog") or
                   data.get("fog_mask_b64") or "")
        if fog_b64:
            import io
            import base64 as _b64
            import numpy as _np
            from PIL import Image as _PILImage
            fog_bytes = _b64.b64decode(fog_b64)
            fog_img   = _PILImage.open(io.BytesIO(fog_bytes)).convert("L")
            _fog_arr  = _np.array(fog_img, dtype=_np.uint8)
            _fog_h, _fog_w = _fog_arr.shape
    except Exception as _fog_err:
        print(f"[get_map_prompt] Impossible de lire le fog mask : {_fog_err}")

    def _is_revealed(tok) -> bool:
        if _fog_arr is None or _fog_w == 0 or _fog_h == 0:
            return True
        c  = int(round(tok.get("col", 0)))
        r  = int(round(tok.get("row", 0)))
        px = min(int((c + 0.5) * _fog_w / cols), _fog_w - 1) if cols > 0 else 0
        py = min(int((r + 0.5) * _fog_h / rows), _fog_h - 1) if rows > 0 else 0
        return _fog_arr[py, px] <= 127

    def _is_ally(t):
        a = t.get("alignment", "")
        if a == "ally":    return True
        if a == "hostile": return False
        return t.get("type") == "hero"

    def _is_hostile(t):
        a = t.get("alignment", "")
        if a == "hostile": return True
        if a == "ally":    return False
        return t.get("type") == "monster"

    allies   = [t for t in tokens if _is_ally(t)]
    enemies  = [t for t in tokens if _is_hostile(t) and _is_revealed(t)]
    neutrals = [t for t in tokens if t.get("alignment") == "neutral"
                and t.get("type") != "trap" and _is_revealed(t)]
    traps    = [t for t in tokens if t.get("type") == "trap" and _is_revealed(t)]
    notes    = data.get("notes", [])

    def _coord(t):
        return int(round(t.get("col", 0))), int(round(t.get("row", 0)))

    def _label(t):
        base = t.get("name") or t.get("type", "?")
        alt  = int(t.get("altitude_ft", 0))
        return f"{base} [▲{alt}ft]" if alt > 0 else base

    import math as _math

    def _dist_horiz_ft(t1, t2) -> float:
        c1, r1 = _coord(t1)
        c2, r2 = _coord(t2)
        s1 = max(1, int(float(t1.get("size", 1))))
        s2 = max(1, int(float(t2.get("size", 1))))
        
        def _dist1d(a, a_sz, b, b_sz):
            a_end = a + a_sz - 1
            b_end = b + b_sz - 1
            if a_end < b: return b - a_end
            if b_end < a: return a - b_end
            return 0
            
        return max(_dist1d(c1, s1, c2, s2), _dist1d(r1, s1, r2, s2)) * 5.0

    def _dist3d_ft(t1, t2) -> float:
        horiz = _dist_horiz_ft(t1, t2)
        dalt  = abs(int(t1.get("altitude_ft", 0)) - int(t2.get("altitude_ft", 0)))
        return max(float(horiz), float(dalt))

    def _reach_verdict(t1, t2) -> str:
        d3d = _dist3d_ft(t1, t2)
        if not in_combat:
            return f"{d3d:.0f}ft"
        return "mêlée ✅ (≤5ft 3D)" if d3d <= 5.0 else f"portée distance 🏹 ({d3d:.0f}ft 3D)"

    if in_combat:
        lines = [
            f"\n\n🗺️ ═══ CARTE DE COMBAT ({cols}×{rows} cases — 1 case = 5ft) ═══",
            "  • L'axe des Colonnes (Col) va de GAUCHE (1) vers la DROITE (est).",
            "  • L'axe des Rangées/Lignes (Lig) va du HAUT (1) vers le BAS (sud).",
            "  • Les distances intègrent l'ALTITUDE (règle 1-1-1) : dist_3D = max(horiz, Δalt).",
            "  • Portée de mêlée : ≤5ft en 3D.",
            "  • Un token en vol ne peut être attaqué en mêlée que si la dist 3D ≤ 5ft.",
            "  • 🌫️ Seuls les ennemis sur des cases RÉVÉLÉES sont listés ci-dessous.",
            "      Des ennemis cachés dans le brouillard de guerre peuvent exister.",
        ]
    else:
        lines = [
            f"\n\n🗺️ ═══ CARTE — POSITIONS ({cols}×{rows} cases — 1 case = 5ft) ═══",
            "  • L'axe des Colonnes (Col) va de GAUCHE (1) vers la DROITE (est).",
            "  • L'axe des Rangées/Lignes (Lig) va du HAUT (1) vers le BAS (sud).",
        ]

    if allies:
        lines.append("\n🔵 ALLIÉS — positions :")
        for h in allies:
            c, r  = _coord(h)
            alt   = int(h.get("altitude_ft", 0))
            tag   = " (PNJ)" if h.get("type") != "hero" else ""
            alt_s = (f"  ✈ EN VOL — altitude : {alt}ft ({alt//5} cases au-dessus du sol)"
                     if alt > 0 else "  [au sol]")
            lines.append(f"  • {_label(h)}{tag} → Col {c+1}, Lig {r+1}{alt_s}")

    if enemies:
        lines.append("\n🔴 ENNEMIS — positions :")
        for m in enemies:
            c, r  = _coord(m)
            alt   = int(m.get("altitude_ft", 0))
            alt_s = (f"  ✈ EN VOL — altitude : {alt}ft ({alt//5} cases au-dessus du sol)"
                     if alt > 0 else "  [au sol]")
            lines.append(f"  • {_label(m)} → Col {c+1}, Lig {r+1}{alt_s}")

    if neutrals:
        lines.append("\n🟡 NEUTRES — positions :")
        for n in neutrals:
            c, r  = _coord(n)
            alt   = int(n.get("altitude_ft", 0))
            alt_s = f"  ✈ EN VOL — altitude : {alt}ft" if alt > 0 else "  [au sol]"
            lines.append(f"  • {_label(n)} → Col {c+1}, Lig {r+1}{alt_s}")

    if traps:
        lines.append("\n⚠️ PIÈGES / ZONES :")
        for tr in traps:
            c, r = _coord(tr)
            lines.append(f"  • {_label(tr)} → Col {c+1}, Lig {r+1}")

    if allies and enemies:
        if for_hero:
            hero_token = next(
                (h for h in allies if (_label(h).split(' [')[0]).lower() == for_hero.lower()
                 or h.get('name', '').lower() == for_hero.lower()), None)
            if hero_token:
                h_alt = int(hero_token.get("altitude_ft", 0))
                lines.append("\n📏 TES DISTANCES → ENNEMIS (distances 3D — altitude incluse) :")
                for m in sorted(enemies, key=lambda m: _dist3d_ft(hero_token, m))[:6]:
                    horiz     = _dist_horiz_ft(hero_token, m)
                    dalt      = abs(h_alt - int(m.get("altitude_ft", 0)))
                    d3d       = _dist3d_ft(hero_token, m)
                    breakdown = (f"{horiz:.0f}ft horiz, même altitude" if dalt == 0
                                 else f"max({horiz:.0f}ft ↔, {dalt}ft ↕) = {d3d:.0f}ft 3D")
                    lines.append(f"  → {_label(m)} : {breakdown} — {_reach_verdict(hero_token, m)}")
        else:
            lines.append("\n📏 DISTANCES HÉROS → ENNEMIS (distances 3D — altitude incluse) :")
            for h in allies:
                h_alt = int(h.get("altitude_ft", 0))
                lines.append(f"  ── {_label(h)} ({'vol' if h_alt else 'sol'}) ──")
                for m in sorted(enemies, key=lambda m: _dist3d_ft(h, m))[:4]:
                    horiz     = _dist_horiz_ft(h, m)
                    dalt      = abs(h_alt - int(m.get("altitude_ft", 0)))
                    d3d       = _dist3d_ft(h, m)
                    breakdown = (f"{horiz:.0f}ft horiz, même altitude" if dalt == 0
                                 else f"max({horiz:.0f}ft ↔, {dalt}ft ↕) = {d3d:.0f}ft 3D")
                    lines.append(f"    → {_label(m)} : {breakdown} — {_reach_verdict(h, m)}")

    if len(allies) >= 2:
        if for_hero:
            hero_token = next(
                (h for h in allies if (_label(h).split(' [')[0]).lower() == for_hero.lower()
                 or h.get('name', '').lower() == for_hero.lower()), None)
            if hero_token:
                other_allies = [h for h in allies if h is not hero_token]
                if other_allies:
                    lines.append("\n🤝 TES DISTANCES → ALLIÉS (3D) :")
                    for ally in sorted(other_allies, key=lambda a: _dist3d_ft(hero_token, a)):
                        horiz     = _dist_horiz_ft(hero_token, ally)
                        dalt      = abs(int(hero_token.get("altitude_ft", 0)) - int(ally.get("altitude_ft", 0)))
                        d3d       = _dist3d_ft(hero_token, ally)
                        breakdown = (f"{horiz:.0f}ft" if dalt == 0
                                     else f"max({horiz:.0f}ft ↔, {dalt}ft ↕) = {d3d:.0f}ft 3D")
                        lines.append(f"  → {_label(ally)} : {breakdown} — {_reach_verdict(hero_token, ally)}")
        else:
            lines.append("\n🤝 DISTANCES ENTRE ALLIÉS (3D) :")
            for i, h1 in enumerate(allies):
                for h2 in allies[i + 1:]:
                    horiz     = _dist_horiz_ft(h1, h2)
                    dalt      = abs(int(h1.get("altitude_ft", 0)) - int(h2.get("altitude_ft", 0)))
                    d3d       = _dist3d_ft(h1, h2)
                    breakdown = (f"{horiz:.0f}ft" if dalt == 0
                                 else f"max({horiz:.0f}ft ↔, {dalt}ft ↕) = {d3d:.0f}ft 3D")
                    lines.append(f"  • {_label(h1)} ↔ {_label(h2)} : {breakdown} — {_reach_verdict(h1, h2)}")

    if notes:
        note_texts = [n.get("text", "").strip() for n in notes if n.get("text", "").strip()]
        if note_texts:
            lines.append("\n📌 NOTES SUR LA CARTE :")
            for nt in note_texts[:6]:
                lines.append(f"  • {nt}")

    doors = data.get("doors", [])
    if doors:
        lines.append("\n🚪 PORTES — état réel (priorité absolue sur l'image de fond) :")
        lines.append("  ⚠ L'image peut montrer un état différent — ces données font foi.")
        for d in doors:
            state    = "OUVERTE" if d.get("open") else "FERMÉE"
            label    = f" ({d['label']})" if d.get("label") else ""
            override = ("l'image montre une porte fermée — elle est en réalité OUVERTE"
                        if d.get("open")
                        else "l'image montre une porte ouverte — elle est en réalité FERMÉE")
            lines.append(f"  • Col {d['col']+1}, Lig {d['row']+1}{label} : {state} — {override}")

    obstacles = data.get("obstacles", [])
    if obstacles:
        lines.append("\n🧱 OBSTACLES / ZONES BLOQUÉES :")
        lines.append("  ⚠ Ces zones sont physiquement bloquées — mouvement et ligne de vue impossibles.")
        for obs in obstacles:
            pts       = obs.get("pts", [])
            label     = obs.get("label", "")
            label_txt = f" « {label} »" if label else ""
            if pts:
                min_c = int(min(p[0] for p in pts) / 44)
                max_c = int(max(p[0] for p in pts) / 44)
                min_r = int(min(p[1] for p in pts) / 44)
                max_r = int(max(p[1] for p in pts) / 44)
                lines.append(
                    f"  • Obstacle{label_txt} — cases Col {min_c+1}–{max_c+1}, "
                    f"Lig {min_r+1}–{max_r+1} : PASSAGE BLOQUÉ")

    if in_combat:
        lines.append("\nUtilise ces positions pour décider de ton mouvement et de ta portée d'attaque.")
    else:
        lines.append("\nUtilise ces positions pour te déplacer et interagir avec les personnes proches.")
    return "\n".join(lines)