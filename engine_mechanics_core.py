"""
engine_mechanics_core.py — Dispatcher principal des actions
Partie 3/4 du module engine_mechanics.

Exporte :
  execute_action_mechanics — dispatch principal (attaque/sort/compétence/mouvement)
"""

import re as _re
from state_manager import roll_dice
from class_data import get_no_roll_feature, get_feature_details

# Import des sous-modules de résolution pour Spells et Move (définis dans le fichier 4)
from engine_mechanics_spells import execute_spell_action, execute_move_action


# ─── execute_action_mechanics ────────────────────────────────────────────────

def execute_action_mechanics(
    char_name: str, intention: str, regle: str,
    cible: str, mj_note: str,
    single_attack: bool, type_label: str,
    char_mechanics: dict,
    pending_smite: dict,
    pending_skill_narrators: set,
    app,
    extract_spell_name_fn,
    is_spell_prepared_fn,
    get_prepared_spell_names_fn,
) -> str:
    """
    Exécute directement les mécaniques D&D 5e en Python et retourne
    un résumé [RÉSULTAT SYSTÈME] à injecter dans le contexte de l'agent.
    """
    stats  = char_mechanics.get(char_name, {})
    r_low  = regle.lower()
    i_low  = intention.lower()
    t_low  = (type_label or "").lower()
    results = []
    narrative_hint = ""

    # Court-circuit : Type: Mouvement déclaré explicitement
    r_low_orig = r_low
    if "mouvement" in t_low:
        r_low = "mouvement "  # neutralise is_atk/is_spell, pas la direction

    if mj_note:
        results.append(f"Note MJ : {mj_note}")

    # ── COURT-CIRCUIT : Ready Action (Se Tenir Prêt) ──────────────────────────
    _READY_KW_EXEC = ("ready", "se tenir prêt", "tenir prêt", "me tenir prêt",
                      "prépare une action", "préparer une action", "ready action",
                      "action préparée", "se prépare à")
    _combined_ready = (t_low + " " + i_low + " " + r_low).lower()
    if any(k in _combined_ready for k in _READY_KW_EXEC):
        results.append(f"⏳ {char_name} — Se Tenir Prêt (Ready Action)")
        results.append(f"  Action préparée : {intention}")
        if cible and cible.lower() not in ("soi-même", "self", "-", ""):
            results.append(f"  Cible prévue : {cible}")
        results.append(f"  Déclencheur : en attente du trigger défini par le joueur.")
        results.append(f"  → Aucun jet de dés maintenant — les jets se feront quand le trigger se produit (coûtera la Réaction de {char_name}).")
        results.append(f"  ⚠ Rappel 5e : pas d'Extra Attack sur une action préparée — une seule attaque si le trigger se déclenche.")
        narrative_hint = (
            f"{char_name} prépare son action et reste en alerte. "
            f"Narre en 1-2 phrases comment {char_name} se tient prêt, "
            f"décrivant sa posture et sa vigilance. Pas de jets, pas de résultat."
        )
        return (
            f"[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ — {char_name}]\n"
            + "\n".join(results)
            + "\n\n[INSTRUCTION NARRATIVE]\n"
            + narrative_hint
        )

    # ── COURT-CIRCUIT : Capacités de classe sans jet de dés ───────────────────
    _no_roll = get_no_roll_feature(intention, regle)
    if _no_roll is not None:
        _cls, _feat_name, _narr_hint = _no_roll
        _feat_details = None
        try:
            _feat_details = get_feature_details(_cls, _feat_name)
        except Exception:
            pass
        results.append(f"✨ {char_name} — {_feat_name}")
        if _feat_details and _feat_details.get("text"):
            results.append(f"[Mécanique officielle]\n{_feat_details['text']}")
        else:
            results.append(f"  [Capacité de classe — aucun jet de dés requis]")
        if cible and cible.lower() not in ("soi-même", "self", "-", ""):
            results.append(f"  Cible : {cible}")
        narrative_hint = _narr_hint.format(name=char_name)
        return (
            f"[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ — {char_name}]\n"
            + "\n".join(results)
            + "\n\n[INSTRUCTION NARRATIVE]\n"
            + narrative_hint
        )

    # Helpers
    def _all_dice(text):
        text_mod = text
        if stats.get("spell_mod"):
            text_mod = _re.sub(r'\+\s*mod(?:ificateur|\.| )?(?:\s*de\s*sort)?', f"+{stats['spell_mod']}", text_mod, flags=_re.IGNORECASE)
            
        return[(int(m.group(1)), int(m.group(2)),
                 int(m.group(3).replace(" ","")) if m.group(3) else 0)
                for m in _re.finditer(r"(\d+)d(\d+)(?:\s*([+-]\s*\d+))?",
                                      text_mod, _re.IGNORECASE)]

    def _extract_dc(text):
        m = _re.search(r"\bDC\s*(\d+)", text, _re.IGNORECASE)
        return int(m.group(1)) if m else None

    def _extract_level(text):
        levels =[]
        for pat in (r"niv(?:eau)?\.?\s*(\d+)", r"niveau\s*(\d+)", r"\bniv(\d+)"):
            for m in _re.finditer(pat, text, _re.IGNORECASE):
                levels.append(int(m.group(1)))
        
        valid_levels = [l for l in levels if l <= 9]
        if valid_levels:
            return valid_levels[-1] 
        return None

    def _skill_bonus(text):
        t = text.lower()
        for table in (stats.get("skills",{}), stats.get("saves",{})):
            for k, v in table.items():
                if k in t:
                    return v
        return None

    def _total(res_str):
        m = _re.search(r"Total\s*=\s*(\d+)", res_str)
        return int(m.group(1)) if m else None

    def _first_roll(res_str):
        m = _re.search(r"Dés:\s*\[(\d+)", res_str)
        return int(m.group(1)) if m else None

    # Détection du type
    SPELL_KW = ("sort","magie","incant","boule","projectile","éclair","feu",
                "soin","soigne","heal","cure","guéri","restaure","parole",
                "contresort","dissipation","bannissement","désintégration",
                "lumière","ténèbres","sacré","nécro","évocation","abjuration",
                "divine favor", "faveur divine", "bless", "bénédiction",
                "spiritual weapon", "arme spirituelle", "marteau spirituel",
                "flaming sphere", "sphère de feu", "bigby", "moonbeam",
                "rayon de lune", "cloud of daggers", "nuage de dagues",
                "flamme","sacred","flame","toll the dead","glas des morts",
                "word of radiance","mot radieux","cantrip",
                "imposition", "lay on hands")
    ATK_KW   = ("attaque","frappe","coup","tir","tire","charge","poignarde",
                "tranche","abat","corps-à-corps","distance","assaut","offensive",
                "extra attack", "seconde attaque", "deuxième attaque")
    SKILL_KW = ("jet","check","compétence","sauvegarde","save","arcanes",
                "perception","investigation","discrétion","athlétisme",
                "acrobaties","médecine","histoire","nature","religion",
                "perspicacité","intimidation","tromperie","persuasion",
                "dressage","survie","escamotage","force","dextérité",
                "constitution","intelligence","sagesse","charisme")

    _SMITE_SPELLS = ("wrathful smite", "courroux divin", "thunderous smite",
                     "frappe tonnerre", "branding smite", "frappe lumière")
                     
    _DIVINE_SMITE = ("divine smite", "smite divin", "châtiment divin", "chatiment divin")

    is_move_action = "mouvement" in t_low
    is_spell = bool(_re.search(r'\b(?:' + '|'.join(SPELL_KW + _SMITE_SPELLS) + r')', r_low + " " + i_low)) and not is_move_action
    
    _SKILL_OVERRIDE = ("se cacher", "cacher", "discrétion", "stealth", "aim", "steady aim", "visée", "viser", "jet de compétence", "skill check")
    _is_skill_action = bool(_re.search(r'\b(?:' + '|'.join(_SKILL_OVERRIDE) + r')', r_low + " " + i_low))
    
    _has_target = cible.lower().strip() not in ("", "none", "-", "n/a", "aucun", "aucune", "soi-même", "self", "personne", "moi-même", "moi meme", char_name.lower())
    _DODGE_KW = ("esquive", "dodge", "défensive", "defensive")
    _is_dodge = bool(_re.search(r'\b(?:' + '|'.join(_DODGE_KW) + r')', r_low + " " + i_low))
    _is_dash = bool(_re.search(r'\b(?:dash|foncer|sprint|course)', r_low + " " + i_low))
    _DISENGAGE_KW = ("disengage", "désengager", "desengager", "désengagement", "se désengager", "se desengager")
    _is_disengage = bool(_re.search(r'\b(?:' + '|'.join(_DISENGAGE_KW) + r')', r_low + " " + i_low))
    
    is_atk   = bool(_re.search(r'\b(?:' + '|'.join(ATK_KW + _DIVINE_SMITE) + r')', r_low + " " + i_low)) and not is_spell and not is_move_action and not _is_dodge and not _is_dash and not _is_disengage and not _is_skill_action and _has_target
    is_skill = bool(_re.search(r'\b(?:' + '|'.join(SKILL_KW) + r')', r_low + " " + i_low)) and not is_atk and not is_spell and not is_move_action

    _DASH_KW = ("mouvement", "déplace", "deplace", "dash", "foncer", "sprint",
                "course", "avance", "recule", "approche", "fonce")

    # ── ATTAQUE ──────────────────────────────────────────────────────
    if is_atk:
        ranged = any(k in r_low or k in i_low
                     for k in ("distance","arc","arbalète","javelot","projectile"))
        m_bon = _re.search(r"bonus\s*([+-]\d+)", r_low)
        atk_bonus = (int(m_bon.group(1)) if m_bon
                     else stats.get("atk_ranged" if ranged else "atk_melee", +5))

        all_d  = _all_dice(regle)
        if single_attack:
            dmg_d = all_d[0] if all_d else None
            _m_atk = _re.search(
                r'(?:corps[- ]à[- ]corps|mêlée|melee|distance|ranged|attaque)[^,]*?([+-]\d+)',
                r_low
            )
            if _m_atk:
                atk_bonus = int(_m_atk.group(1))
        else:
            dmg_d = all_d[1] if len(all_d) >= 2 else None
        if dmg_d is None:
            dn, df, db = stats.get("dmg_melee", (1, 8, 0))
        else:
            dn, df, db = dmg_d

        n_atk  = 1 if single_attack else stats.get("n_attacks", 1)
        dc_val = _extract_dc(regle)

        results.append(f"⚔️ {char_name} — {intention} → {cible}")
        any_crit = False
        for i in range(1, n_atk + 1):
            atk_res = roll_dice(char_name, "1d20", atk_bonus)
            lbl = f"attaque {i}/{n_atk}" if n_atk > 1 else "attaque"
            results.append(f"  [{lbl}] {atk_res}")

            nat = _first_roll(atk_res)
            tot = _total(atk_res)

            if nat == 20:
                any_crit = True
                crit_res = roll_dice(char_name, f"{dn*2}d{df}", db)
                results.append(f"  🎯 CRITIQUE ! {crit_res}")
                continue
            if nat == 1:
                results.append(f"  💀 ÉCHEC CRITIQUE (nat.1) — attaque ratée.")
                continue

            if dc_val and tot is not None:
                hit = tot >= dc_val
                results.append(f"  → {'TOUCHÉ ✅' if hit else 'RATÉ ❌'} (CA {dc_val})")
            elif tot is not None:
                results.append(f"  → Total {tot} — MJ compare à la CA de {cible}")

            dmg_res = roll_dice(char_name, f"{dn}d{df}", db)
            results.append(f"  [dégâts] {dmg_res}")

        # Smite en attente → appliqué sur la première attaque confirmée
        if single_attack and char_name in pending_smite:
            _sm = pending_smite.pop(char_name)
            sm_d = _sm["dice"]
            if any_crit:
                import re as _re_smite
                _m = _re_smite.match(r"(\d+)d(\d+)", sm_d)
                if _m:
                    sm_d = f"{int(_m.group(1))*2}d{_m.group(2)}"
            sm_res = roll_dice(char_name, sm_d, 0)
            results.append(
                f"  [✨ {_sm['label']}] {sm_res}  "
                f"(dégâts {_sm['type']} supplémentaires)"
            )

        narrative_hint = (
            f"Le système vient d exécuter les jets d attaque. "
            f"Narre en 1-2 phrases vivantes comment {char_name} attaque {cible}. "
            f"Ne mentionne PAS les chiffres — décris l action, la violence, le mouvement."
        )

    # ── COMPÉTENCE / SAUVEGARDE ──────────────────────────────────────
    elif is_skill:
        bonus  = _skill_bonus(regle + " " + intention) or 0
        m_bon  = _re.search(r"([+-]\d+)", regle)
        if bonus == 0 and m_bon:
            bonus = int(m_bon.group(1))
        dc_val = _extract_dc(regle)

        res = roll_dice(char_name, "1d20", bonus)
        results.append(f"🎲 {char_name} — {regle}")
        results.append(f"  {res}")
        tot = _total(res)
        if dc_val and tot is not None:
            outcome = "RÉUSSITE ✅" if tot >= dc_val else "ÉCHEC ❌"
            results.append(f"  → DC {dc_val} : {outcome}")
        else:
            results.append(f"  → MJ annoncera la DC et l effet.")

        pending_skill_narrators.add(char_name)
        narrative_hint = (
            f"Le système a lancé le jet. "
            f"RÈGLE ABSOLUE : narre UNIQUEMENT l'effort physique ou mental de {char_name} "
            f"(la tension de ses muscles, sa concentration, le geste accompli). "
            f"TU NE DÉCRIS JAMAIS ce que tu trouves, découvres, perçois ou constates — "
            f"même si le résultat est élevé. La qualité des matériaux, l'état de la structure, "
            f"les propriétés magiques, les informations trouvées : TOUT cela appartient au MJ. "
            f"Exemple interdit : 'la pierre est de qualité' / 'je détecte une anomalie magique'. "
            f"Exemple correct : 'Mes doigts parcourent la surface. Quelque chose cloche ici.' "
            f"Attends que le MJ décrive le résultat."
        )

    # ── SORT ─────────────────────────────────────────────────────────
    elif is_spell:
        return execute_spell_action(
            char_name, intention, regle, cible, mj_note, type_label,
            char_mechanics, pending_smite, app,
            extract_spell_name_fn, is_spell_prepared_fn, get_prepared_spell_names_fn
        )
    
    # ── MOUVEMENT ────────────────────────────────────────────────────
    elif any(k in t_low or k in i_low or k in r_low_orig for k in _DASH_KW):
        return execute_move_action(
            char_name, intention, regle, cible, mj_note, type_label,
            char_mechanics, app
        )
    
    # ── AUTRE ────────────────────────────────────────────────────────
    else:
        results.append(f"⚙️ {char_name} — {intention}")
        results.append(f"  Mécanique : {regle} | Cible : {cible}")
        narrative_hint = (
            f"Narre en 1-2 phrases l action de {char_name} : {intention}. "
            f"Si des dés sont encore nécessaires, pose un nouveau [ACTION]."
        )

    return (
        f"[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ — {char_name}]\n"
        + "\n".join(results)
        + "\n\n[INSTRUCTION NARRATIVE]\n"
        + narrative_hint
    )