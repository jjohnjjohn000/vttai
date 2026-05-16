"""
engine_mechanics_rolls.py — Mécaniques de jets d'attaque et de dégâts
Partie 2/4 du module engine_mechanics.

Exporte :
  roll_attack_only        — Phase 1 : jet d'attaque uniquement (1d20)
  roll_damage_only        — Phase 2 : jets de dégâts confirmés
"""

import re as _re
from state_manager import roll_dice

# ─── roll_attack_only ─────────────────────────────────────────────────────────

def roll_attack_only(char_name: str, regle: str, intention: str,
                     cible: str, mj_note: str,
                     char_mechanics: dict) -> dict:
    """
    Phase 1 d'une attaque individuelle : lance UNIQUEMENT le 1d20.
    Retourne {atk_text, nat, total, is_crit, is_fumble, dn, df, db, atk_bonus}.
    """
    stats = char_mechanics.get(char_name, {})
    r_low = regle.lower()
    i_low = intention.lower()

    ranged = any(k in r_low or k in i_low
                 for k in ("distance","arc","arbalète","javelot","projectile"))
    _m_atk = _re.search(
        r'(?:corps[- ]à[- ]corps|mêlée|melee|distance|ranged|attaque|extra attack)[^,]*?([+-]\d+)',
        r_low
    )
    if _m_atk:
        atk_bonus = int(_m_atk.group(1))
    else:
        m_bon = _re.search(r"bonus\s*([+-]\d+)", r_low)
        atk_bonus = (int(m_bon.group(1)) if m_bon
                     else stats.get("atk_ranged" if ranged else "atk_melee", +5))

    # Dés de dégâts (extraits de la règle pour usage ultérieur)
    def _all_dice_local(text):
        text_mod = text
        if stats.get("spell_mod"):
            # Remplacement automatique de "+ mod." par le spell_mod du lanceur
            text_mod = _re.sub(r'\+\s*mod(?:ificateur|\.| )?(?:\s*de\s*sort)?', f"+{stats['spell_mod']}", text_mod, flags=_re.IGNORECASE)
            
        return[(int(m.group(1)), int(m.group(2)),
                 int(m.group(3).replace(" ","")) if m.group(3) else 0)
                for m in _re.finditer(r"(\d+)d(\d+)(?:\s*([+-]\s*\d+))?",
                                      text_mod, _re.IGNORECASE)]
    all_d = _all_dice_local(regle)
    dmg_d = all_d[0] if all_d else None
    if dmg_d is None:
        dn, df, db = stats.get("dmg_melee", (1, 8, 0))
    else:
        dn, df, db = dmg_d

    atk_res  = roll_dice(char_name, "1d20", atk_bonus)
    is_extra = any(k in r_low or k in i_low for k in ("extra attack", "seconde attaque", "deuxième attaque"))
    lbl = " attaque " if not is_extra else " porte une Extra Attack sur "
    lines    =[f"⚔️ {char_name}{lbl}{cible}"]
    if mj_note:
        lines.append(f"Note MJ : {mj_note}")
    lines.append(f"  [jet d'attaque] {atk_res}")

    nat      = None
    total    = None
    is_crit  = False
    is_fumble= False

    m_nat = _re.search(r"Dés:\s*\[(\d+)", atk_res)
    m_tot = _re.search(r"Total\s*=\s*(\d+)", atk_res)
    if m_nat: nat   = int(m_nat.group(1))
    if m_tot: total = int(m_tot.group(1))

    if nat == 20:
        is_crit = True
        lines.append("  🎯 COUP CRITIQUE — les dégâts seront doublés !")
    elif nat == 1:
        is_fumble = True
        lines.append("  💀 ÉCHEC CRITIQUE (nat.1) — attaque automatiquement ratée.")
    elif total is not None:
        lines.append(f"  → Total {total} — MJ compare à la CA de {cible}")

    return {
        "atk_text":  "\n".join(lines),
        "nat":       nat,
        "total":     total,
        "is_crit":   is_crit,
        "is_fumble": is_fumble,
        "dn": dn, "df": df, "db": db,
    }


# ─── roll_damage_only ─────────────────────────────────────────────────────────

def roll_damage_only(char_name: str, cible: str,
                     dn: int, df: int, db: int,
                     is_crit: bool, smite: dict | None,
                     mj_note: str,
                     char_mechanics: dict,
                     sneak_approved: bool = False) -> tuple:
    """
    Phase 2 d'une attaque : lance les dés de dégâts (+ smite si présent).
    Retourne (feedback_str, total_damage_int) pour l'hyperlien du chat.
    Le total additionne tous les composants (dégâts bruts + smite + sournoise).

    sneak_approved : si True, les dégâts de Sneak Attack sont inclus.
                     Le flag est positionné par la boîte de confirmation MJ
                     dans engine_receive.py.
    """
    import re as _re_dmg

    def _extract_total(res_str: str) -> int:
        m = _re_dmg.search(r'Total\s*=\s*(\d+)', res_str)
        return int(m.group(1)) if m else 0

    lines = [f"[RÉSULTAT SYSTÈME — DÉGÂTS CONFIRMÉS PAR MJ]",
             f"⚔️ {char_name} → {cible}"]
    if mj_note:
        lines.append(f"Note MJ : {mj_note}")

    grand_total = 0

    if is_crit:
        dmg_res = roll_dice(char_name, f"{dn*2}d{df}", db)
        lines.append(f"  [dégâts CRITIQUE] {dmg_res}")
    else:
        dmg_res = roll_dice(char_name, f"{dn}d{df}", db)
        lines.append(f"  [dégâts] {dmg_res}")
    grand_total += _extract_total(dmg_res)

    if smite:
        sm_d = smite["dice"]
        if is_crit:
            import re as _re_smite
            _m = _re_smite.match(r"(\d+)d(\d+)", sm_d)
            if _m:
                sm_d = f"{int(_m.group(1))*2}d{_m.group(2)}"
        sm_res = roll_dice(char_name, sm_d, 0)
        lines.append(
            f"  [✨ {smite['label']}] {sm_res}  "
            f"(dégâts {smite['type']} supplémentaires)"
        )
        grand_total += _extract_total(sm_res)

    # Sneak Attack : seulement si approuvé par le MJ via la boîte de confirmation
    if sneak_approved:
        stats = char_mechanics.get(char_name, {})
        sn, sf, sb = stats.get("dmg_sneak", (6, 6, 0))
        if is_crit:
            sn *= 2
        snk_res = roll_dice(char_name, f"{sn}d{sf}", sb)
        lines.append(f"  [🗡️ sournoise] {snk_res}")
        grand_total += _extract_total(snk_res)

    lines.append("")
    lines.append("[INSTRUCTION NARRATIVE]")
    lines.append(
        f"Le système vient d exécuter les dégâts. "
        f"Narre en 1-2 phrases vivantes l impact sur {cible}. "
        f"Ne mentionne PAS les chiffres."
    )
    return "\n".join(lines), grand_total