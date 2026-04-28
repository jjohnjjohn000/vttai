"""
engine_mechanics.py — Mécaniques D&D 5e : stats personnages, jets de dés, actions.

Exporte :
  CHAR_MECHANICS          — dict de stats D&D 5e niveau 11 pour chaque PJ
  split_into_subactions   — décompose un bloc [ACTION] en sous-actions
  roll_attack_only        — Phase 1 : jet d'attaque uniquement (1d20)
  roll_damage_only        — Phase 2 : jets de dégâts confirmés
  execute_action_mechanics — dispatch principal (attaque/sort/compétence/mouvement)

Toutes les fonctions reçoivent char_mechanics et pending_smite en paramètre
explicite pour éviter l'état global.
"""

import re as _re

from state_manager import roll_dice, use_spell_slot
from engine_spell_mj import can_ritual_cast
from class_data import get_no_roll_feature, get_feature_details


# ─── Stats mécaniques D&D 5e 2014, niveau 11 ──────────────────────────────────
CHAR_MECHANICS: dict = {
    "Kaelen": {  # Paladin 11 — STR20 DEX14 CON16 INT10 WIS14 CHA18 — Prof+4
        "strength": 20, "str_mod": +5, "dexterity": 14, "dex_mod": +2,
        "constitution": 16, "con_mod": +3, "intelligence": 10, "int_mod": +0,
        "wisdom": 14, "wis_mod": +2, "charisma": 18, "cha_mod": +4,
        "spell_mod": +4,
        "atk_melee": +11, "atk_ranged": +7, "atk_spell": +9,
        "speed": 30,
        "dmg_melee": (2, 6, +8), "n_attacks": 2, "save_dc": 18,
        "extra_attack": True,
        "skills": {"athlétisme":+10,"religion":+5,"persuasion":+9,
                   "perspicacité":+7,"intimidation":+9,"perception":+7},
        "saves":  {"force":+10,"dextérité":+7,"constitution":+8,
                   "intelligence":+5,"sagesse":+7,"charisme":+9},
    },
    "Elara": {   # Mage 11 — STR8 DEX16 CON14 INT20 WIS14 CHA10 — Prof+4
        "strength": 8, "str_mod": -1, "dexterity": 16, "dex_mod": +3,
        "constitution": 14, "con_mod": +2, "intelligence": 20, "int_mod": +5,
        "wisdom": 14, "wis_mod": +2, "charisma": 10, "cha_mod": +0,
        "spell_mod": +5,
        "atk_melee": +3, "atk_ranged": +8, "atk_spell": +10,
        "speed": 30,
        "dmg_melee": (1, 4, -1), "n_attacks": 1, "save_dc": 18,
        "extra_attack": False,
        "skills": {"arcanes":+15,"histoire":+10,"investigation":+10,
                   "nature":+10,"religion":+10,"perception":+7,"perspicacité":+7},
        "saves":  {"force":-1,"dextérité":+8,"constitution":+7,
                   "intelligence":+10,"sagesse":+7,"charisme":+5},
    },
    "Thorne": {  # Voleur Assassin 11 — STR12 DEX20 CON14 INT16 WIS12 CHA14 — Prof+4
        "strength": 12, "str_mod": +1, "dexterity": 20, "dex_mod": +5,
        "constitution": 14, "con_mod": +2, "intelligence": 16, "int_mod": +3,
        "wisdom": 12, "wis_mod": +1, "charisma": 14, "cha_mod": +2,
        "spell_mod": +0,
        "atk_melee": +11, "atk_ranged": +11, "atk_spell": None,
        "speed": 30,
        "dmg_melee": (1, 6, +5), "dmg_sneak": (6, 6, 0),
        "n_attacks": 2, "save_dc": None,
        "extra_attack": False,
        "skills": {"discrétion":+15,"escamotage":+15,"tromperie":+12,
                   "perception":+11,"perspicacité":+6,"acrobaties":+10,
                   "investigation":+8,"athlétisme":+6,"intimidation":+7},
        "saves":  {"force":+6,"dextérité":+10,"constitution":+7,
                   "intelligence":+8,"sagesse":+6,"charisme":+7},
    },
    "Lyra": {    # Clerc Vie 11 — STR14 DEX12 CON14 INT12 WIS20 CHA16 — Prof+4
        "strength": 14, "str_mod": +2, "dexterity": 12, "dex_mod": +1,
        "constitution": 14, "con_mod": +2, "intelligence": 12, "int_mod": +1,
        "wisdom": 20, "wis_mod": +5, "charisma": 16, "cha_mod": +3,
        "spell_mod": +5,
        "atk_melee": +7, "atk_ranged": +6, "atk_spell": +10,
        "speed": 30,
        "dmg_melee": (1, 8, +2), "n_attacks": 1, "save_dc": 18,
        "extra_attack": False,
        "skills": {"médecine":+15,"perspicacité":+10,"religion":+6,
                   "persuasion":+8,"perception":+10,"histoire":+6},
        "saves":  {"force":+7,"dextérité":+6,"constitution":+7,
                   "intelligence":+6,"sagesse":+10,"charisme":+8},
    },
}


# ─── split_into_subactions ────────────────────────────────────────────────────

def split_into_subactions(type_label: str, intention: str,
                          regle: str, cible: str,
                          char_mechanics: dict | None = None,
                          char_name: str = "") -> list:
    """
    Retourne l'action déclarée.
    Plus de duplication automatique d'attaques : chaque bloc [ACTION] 
    correspond à UNE SEULE action ou attaque, comme demandé dans le prompt.
    """
    type_low   = (type_label or "").lower()
    intent_low = intention.lower()
    regle_low  = regle.lower()
    combined   = type_low + " " + intent_low + " " + regle_low

    # ── Court-circuit Mouvement ──
    # Si le Type est explicitement "Mouvement", on ne scan pas l'intention
    # pour éviter que des mots comme "attaquer" dans "pour pouvoir l'attaquer"
    # déclenchent un is_physical_attack = True.
    if "mouvement" in type_low:
        return [{
            "type_label": type_label or "Mouvement",
            "intention":  intention,
            "regle":      regle.strip() if regle else "",
            "cible":      cible,
            "single_attack": False,
        }]

    # ── Ready Action (Se Tenir Prêt) ──
    _READY_KW = ("ready", "se tenir prêt", "tenir prêt", "me tenir prêt",
                 "prépare une action", "préparer une action", "ready action",
                 "action préparée", "se prépare à")
    if any(k in combined for k in _READY_KW):
        return[{
            "type_label": type_label or "Action",
            "intention":  intention,
            "regle":      regle.strip(),
            "cible":      cible,
            "ready_action": True,
            "single_attack": False,
        }]

    # ── Détection Attaque Physique ──
    _GENERIC_ATK = (
        "attaque", "frappe", "coup", "tir", "poignarde", "tranche",
        "assaut", "perfore", "lacère", "abat", "sneak attack", "sournoise",
        "reckless", "téméraire", "deux armes", "dual wield",
        "corps-à-corps", "extra attack", "seconde attaque", "deuxième attaque",
    )
    _SPELL_DETECT = ("sort", "magie", "incant", "sacred flame", "flamme sacrée", 
                     "divine favor", "faveur divine", "bless", "bénédiction",
                     "spiritual weapon", "arme spirituelle", "marteau spirituel",
                     "flaming sphere", "sphère de feu", "bigby", "moonbeam",
                     "rayon de lune", "cloud of daggers", "nuage de dagues",
                     "soin", "soigne", "heal", "cure", "imposition", "lay on hands")
    _SMITE_SPELLS = ("wrathful smite", "courroux divin", "thunderous smite", 
                     "frappe tonnerre", "branding smite", "frappe lumière")
    
    # Utilisation des limites de mots (\b) pour éviter que "tir" ne matche dans "répartir"
    _is_spell = bool(_re.search(r'\b(?:' + '|'.join(_SPELL_DETECT + _SMITE_SPELLS) + r')', combined))
    _has_generic_atk = bool(_re.search(r'\b(?:' + '|'.join(_GENERIC_ATK) + r')', combined))
    
    # "divine smite" n'est pas un sort, c'est une feature ajoutée à une attaque
    _is_divine_smite = bool(_re.search(r'\b(?:divine smite|smite divin|châtiment divin|chatiment divin)', combined))
    
    # Identifier les actions qui sont manifestement des compétences/utilitaires et non des attaques
    _SKILL_OVERRIDE = ("se cacher", "cacher", "discrétion", "stealth", "aim", "steady aim", "visée", "viser", "jet de compétence", "skill check")
    _is_skill_action = bool(_re.search(r'\b(?:' + '|'.join(_SKILL_OVERRIDE) + r')', combined))
    
    _has_target = cible.lower().strip() not in ("", "none", "-", "n/a", "aucun", "aucune", "soi-même", "self", "personne", "moi-même", "moi meme", char_name.lower())
    _DODGE_KW = ("esquive", "dodge", "défensive", "defensive")
    _is_dodge = bool(_re.search(r'\b(?:' + '|'.join(_DODGE_KW) + r')', combined))
    _is_dash = bool(_re.search(r'\b(?:dash|foncer|sprint|course)', combined))
    _is_disengage = bool(_re.search(r'\b(?:disengage|désengager|desengager|désengagement|se désengager|se desengager)', combined))
    is_physical_attack = (_has_generic_atk or _is_divine_smite) and not _is_spell and not _is_dodge and not _is_dash and not _is_disengage and not _is_skill_action and _has_target

    # On nettoie le type_label pour enlever "1/2" ou "Extra Attack" s'il y en a, 
    # pour garder l'UI propre, bien que l'agent ne devrait plus les générer.
    clean_type = _re.sub(r'\s*\d+/\d+\s*', ' ', type_label).strip()

    return[{
        "type_label": clean_type or "Action",
        "intention":  intention,
        "regle":      regle.strip(),
        "cible":      cible,
        "single_attack": is_physical_attack,
    }]


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


# ─── execute_action_mechanics ────────────────────────────────────────────────

def execute_action_mechanics(
    char_name: str, intention: str, regle: str,
    cible: str, mj_note: str,
    single_attack: bool, type_label: str,
    char_mechanics: dict,
    pending_smite: dict,
    pending_skill_narrators: set,
    app,                          # DnDApp instance (pour la carte de combat)
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
    # En D&D 5e, préparer une action NE déclenche aucun jet de dés.
    # Les jets se font UNIQUEMENT quand le trigger se produit (via Réaction).
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
    # Vérifier EN PREMIER — avant toute classification is_spell/is_skill.
    # Ces capacités ont une mécanique fixe décrite dans les JSON de classe.
    # La description est lue depuis class/<class>.json (format 5etools) via class_data.
    _no_roll = get_no_roll_feature(intention, regle)
    if _no_roll is not None:
        _cls, _feat_name, _narr_hint = _no_roll
        # Charger la description officielle depuis le JSON de classe
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
            # Remplacement automatique de "+ mod." par le spell_mod du lanceur
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
        
        # Filtre absolu : en D&D 5e, les sorts s'arrêtent au niveau 9.
        # Cela empêche le système d'attraper le "11" de "Clerc niv 11".
        valid_levels = [l for l in levels if l <= 9]
        if valid_levels:
            # S'il y a plusieurs niveaux (ex: Sort niv 1 upcast niv 3), on prend le dernier
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

        # Attaque sournoise — géré par la boîte de confirmation MJ dans
        # engine_receive.py (flow Phase 1/2/3). Ici (flow legacy non-combat)
        # on ne roule plus automatiquement la sournoise.

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
        lvl       = _extract_level(regle) or _extract_level(intention)
        is_cantrip = lvl is None or lvl == 0
        is_heal   = any(k in r_low or k in i_low
                        for k in ("soin","soigne","heal","cure","guéri",
                                  "restaure","parole curative","imposition","lay on hands"))
        is_atk_roll = (any(k in r_low for k in ("jet d attaque de sort",
                                                  "attaque de sort"))
                       or (not is_heal and "rayon" in r_low
                           and not _re.search(r"rayon\s+de\s+\d+", r_low)))
        dc_val    = _extract_dc(regle)

        # Vérification liste de sorts préparés
        _combined_text = f"{intention} {regle}".strip()
        
        _CLASS_FEATURES = ("imposition", "lay on hands", "second wind", "second souffle", "potion", "conduit divin", "channel divinity")
        _is_class_feature = any(k in r_low or k in i_low for k in _CLASS_FEATURES)
        
        _spell_name_candidate = "" if _is_class_feature else (extract_spell_name_fn(_combined_text, char_name) if extract_spell_name_fn else "")
        
        if not is_cantrip and _spell_name_candidate:
            if not is_spell_prepared_fn(char_name, _spell_name_candidate):
                _avail = get_prepared_spell_names_fn(char_name)
                _avail_str = ", ".join(_avail) if _avail else "aucun sort préparé trouvé"
                _no_prep_msg = (
                    f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE — {char_name}]\n"
                    f"« {_spell_name_candidate} » n'est pas dans la liste de sorts "
                    f"préparés de {char_name}. Ce sort ne peut pas être lancé aujourd'hui.\n\n"
                    f"[SORTS AUTORISÉS POUR {char_name.upper()}]\n"
                    f"{_avail_str}\n\n"
                    f"[INSTRUCTION]\n"
                    f"Choisis UNIQUEMENT parmi les sorts listés ci-dessus. "
                    f"Déclare une nouvelle action avec [ACTION]."
                )
                return _no_prep_msg

        # Injection des mécaniques depuis spell_data.py
        _sp_data = None
        if _spell_name_candidate:
            try:
                from spell_data import get_spell as _get_spell
                _sp_data = _get_spell(_spell_name_candidate)
            except Exception:
                pass

        # Fallback : si le nom FR retourné par le LLM n'est pas dans la DB (anglaise),
        # chercher via les mots-clés de l intention/règle dans le catalogue de sorts.
        if _sp_data is None and is_spell and not _is_class_feature:
            try:
                from spell_data import search_spells as _ss_fb, get_spell as _get_spell
                _i_r_fb = (intention + " " + regle).lower()
                _STOP_FB = {"lance", "lancer", "utilise", "sorts", "avec", "pour",
                            "dans", "contre", "vers", "cible", "niveau", "niveaux",
                            "sort", "magie", "spell", "cast", "magic", "bonus"}
                for _w in _re.split(r"[\s\-,;:!?]+", _i_r_fb):
                    if len(_w) >= 5 and _w not in _STOP_FB:
                        _hits = _ss_fb(_w, max_results=1)
                        if _hits:
                            _fb_sp = _get_spell(_hits[0])
                            if _fb_sp:
                                _spell_name_candidate = _hits[0]
                                _sp_data = _fb_sp
                                break
            except Exception:
                pass

        if _sp_data:
            # Jet d'attaque ? — jamais pour un sort de soin.
            if _sp_data.get("spell_attack") and not is_atk_roll and not is_heal:
                is_atk_roll = True
            # La DB de sorts fait autorité sur la détection textuelle.
            # Si le sort n'a PAS de jet d'attaque dans ses données (ex : Projectile
            # Magique), on désactive is_atk_roll même si le texte de la règle
            # contient "attaque de sort". Couvre tout sort auto-hit sans hardcoder
            # les noms.
            elif not _sp_data.get("spell_attack") and not is_heal:
                is_atk_roll = False

            # Sauvegarde ?
            _save = _sp_data.get("saving_throw", [])
            _dc_stat = stats.get("save_dc")
            if _save and not dc_val and _dc_stat:
                dc_val = _dc_stat  # On assigne la stat de DC du perso
                # On force la mention du jet de sauvegarde dans results plus tard

            # Dégâts/Soins dynamiques depuis le tag {@damage XdY} ou {@dice XdY}
            if not _all_dice(regle):
                import json as _json_parser
                _entries_str = _json_parser.dumps(_sp_data.get("entries",[]))
                _dmg_matches = _re.findall(r"\{@(damage|dice)\s+([^}]+)\}", _entries_str)
                if _dmg_matches:
                    _base_dice = _dmg_matches[0][1]
                    _base_lvl = _sp_data.get("level", 0)
                    if lvl and lvl > _base_lvl and _sp_data.get("entries_higher"):
                        _higher_str = _json_parser.dumps(_sp_data["entries_higher"])
                        # FIX : Ajout de (?:damage|dice) car les soins utilisent @scaledice
                        _scale_m = _re.search(r"\{@scale(?:damage|dice)\s+[^|]+\|[^|]+\|(\d+d\d+)\}", _higher_str)
                        if _scale_m:
                            _diff = lvl - _base_lvl
                            _scale_dice = _scale_m.group(1)
                            _sm_m = _re.match(r"(\d+)d(\d+)", _scale_dice)
                            if _sm_m:
                                _ext_dn = int(_sm_m.group(1)) * _diff
                                _ext_df = _sm_m.group(2)
                                _base_m = _re.match(r"(\d+)d(\d+)(.*)", _base_dice)
                                if _base_m and _base_m.group(2) == _ext_df:
                                    _new_dn = int(_base_m.group(1)) + _ext_dn
                                    _base_dice = f"{_new_dn}d{_ext_df}{_base_m.group(3)}"
                                else:
                                    regle += f" + {_ext_dn}d{_ext_df}"
                    regle += f" {_base_dice} "
                    
                    # FIX : Ajout automatique du modificateur si mentionné dans le sort
                    _low_entries = _entries_str.lower()
                    if "spellcasting ability modifier" in _low_entries or "modificateur" in _low_entries or "modifier" in _low_entries:
                        regle += "+ mod"

        results.append(f"✨ {char_name} — {intention.strip()} (niv.{lvl or 0}) → {cible}")

        # ── Smite spells → détection EN PREMIER, AVANT consommation de slot ──
        _SMITE_TABLE = {
            "wrathful smite":   ("1d6",  "psychique",  "Wrathful Smite"),
            "courroux divin":   ("1d6",  "psychique",  "Wrathful Smite"),
            "thunderous smite": ("2d6",  "tonnerre",   "Thunderous Smite"),
            "frappe tonnerre":  ("2d6",  "tonnerre",   "Thunderous Smite"),
            "branding smite":   ("2d6",  "radiant",    "Branding Smite"),
            "frappe lumière":   ("2d6",  "radiant",    "Branding Smite"),
        }
        _smite_match = next(
            ((dice, typ, lbl)
             for kw, (dice, typ, lbl) in _SMITE_TABLE.items()
             if kw in r_low or kw in i_low),
            None
        )
        if _smite_match:
            _sm_dice, _sm_type, _sm_label = _smite_match
            _sm_lvl = lvl or 1
            if _sm_dice is None:
                _sm_dice = f"{_sm_lvl + 1}d8"
            pending_smite[char_name] = {
                "dice":       _sm_dice,
                "type":       _sm_type,
                "label":      _sm_label,
                "slot_level": _sm_lvl,
            }
            results.append(
                f"  [✨ {_sm_label}] En attente — {_sm_dice} dégâts {_sm_type} "
                f"s'ajouteront sur la prochaine attaque de {char_name} SI elle touche. "
                f"(slot niv.{_sm_lvl} sera consommé uniquement sur toucher)"
            )
            narrative_hint = (
                f"Le sort {_sm_label} est prêt. "
                f"Narre en 1 phrase : la lueur sacrée qui enveloppe l'arme de {char_name}, "
                f"prête à se décharger sur le prochain coup."
            )
            return (
                f"[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ — {char_name}]\n"
                + "\n".join(results)
                + "\n\n[INSTRUCTION NARRATIVE]\n"
                + narrative_hint
            )

        # ── PRÉ-DÉTECTION INVOCATION SPECTRALE (Pour éviter la double-consommation) ──
        _spell_check_str = f"{_spell_name_candidate} {regle} {intention}".lower()
        _SPECTRAL_SPAWNS = {
            "spiritual weapon": {"name": "Arme", "src": "Spiritual_Weapon", "size": 1, "aura": 0, "color": ""},
            "arme spirituelle": {"name": "Arme", "src": "Spiritual_Weapon", "size": 1, "aura": 0, "color": ""},
            "marteau spirituel":{"name": "Arme", "src": "Spiritual_Weapon", "size": 1, "aura": 0, "color": ""},
            "flaming sphere":   {"name": "Sphère", "src": "Flaming_Sphere", "size": 1, "aura": 5, "color": "#ff6600"},
            "sphère de feu":    {"name": "Sphère", "src": "Flaming_Sphere", "size": 1, "aura": 5, "color": "#ff6600"},
            "bigby's hand":     {"name": "Main", "src": "Bigbys_Hand", "size": 2, "aura": 0, "color": ""},
            "main de bigby":    {"name": "Main", "src": "Bigbys_Hand", "size": 2, "aura": 0, "color": ""},
            "moonbeam":         {"name": "Rayon", "src": "Moonbeam", "size": 1, "aura": 5, "color": "#e0e0ff"},
            "rayon de lune":    {"name": "Rayon", "src": "Moonbeam", "size": 1, "aura": 5, "color": "#e0e0ff"},
            "cloud of daggers": {"name": "Dagues", "src": "Cloud_of_Daggers", "size": 1, "aura": 0, "color": ""},
            "nuage de dagues":  {"name": "Dagues", "src": "Cloud_of_Daggers", "size": 1, "aura": 0, "color": ""},
        }
        
        _match = next((v for k, v in _SPECTRAL_SPAWNS.items() if k in _spell_check_str), None)
        _cmap_win = getattr(app, "_combat_map_win", None)
        _spectral_exists = False
        _sum_name = f"{_match['name']} ({char_name})" if _match else ""

        if _cmap_win and _match:
            _spectral_exists = any(t.get("name") == _sum_name for t in _cmap_win.tokens)

        # ── Court-circuit : arme spectrale déjà présente = Action Bonus gratuite ──
        # Le slot est consommé UNIQUEMENT à l'invocation initiale.
        # Les tours suivants, l'arme attaque via une Action Bonus sans aucun slot.
        #
        # FIX : distinguer "déplacement de l'arme" vs "attaque avec l'arme".
        # En D&D 5e, le déplacement et l'attaque de l'arme spirituelle sont deux
        # actions séparées — le déplacement ne doit PAS déclencher une attaque auto.
        # Si l'action contient des mots de mouvement (avec ou sans mots d'attaque),
        # on valide uniquement le déplacement et on instruit l'agent d'attaquer séparément.
        _SW_MOVE_KW = ("déplace", "deplace", "move", "repositionne",
                       "rapproche", "avance", "recule", "bouge", "mouvement")
        _SW_ATK_KW  = ("attaque", "attack", "frappe", "frapper", "assaut")
        _sw_has_move = (
            is_move_action  # type_label explicitement "Mouvement"
            or any(k in i_low or k in r_low for k in _SW_MOVE_KW)
        )
        _sw_has_atk = any(k in i_low or k in r_low for k in _SW_ATK_KW)

        # Cas 1a : déplacement de l'arme SANS attaque
        # → Action Gratuite (ne consomme PAS l'Action Bonus)
        if _spectral_exists and _match and _sw_has_move and not _sw_has_atk:
            results.append(f"  [✨ {_sum_name}] Déplacée vers {cible}.")
            results.append(
                "  → Déplacement libre (Action Gratuite) — Action Bonus non consommée.\n"
                "  Pour attaquer, déclare un [ACTION] Type: Action Bonus "
                "/ Intention: Attaquer avec l'arme spirituelle / Cible: <ennemi>."
            )
            narrative_hint = (
                f"L'arme spectrale de {char_name} se déplace vers {cible}. "
                f"Narre en 1 phrase uniquement le déplacement de l'arme. "
                f"{char_name} peut encore utiliser son Action Bonus pour attaquer."
            )
            return (
                f"[RÉSULTAT SYSTÈME — DÉPLACEMENT LIBRE ARME SPECTRALE — {char_name}]\n"
                f"✅ Déplacement confirmé. Action Gratuite — Action Bonus NON consommée.\n"
                + "\n".join(results)
                + "\n\n[INSTRUCTION NARRATIVE]\n"
                + narrative_hint
            )

        # Cas 1b : déplacement + attaque dans le même bloc
        # → Déplacement : Action Gratuite / Attaque : Action Bonus (une seule consommation)
        elif _spectral_exists and _match and _sw_has_move and _sw_has_atk:
            results.append(
                f"  [✨ {_sum_name}] Déplacement vers {cible} (Action Gratuite) + Attaque (Action Bonus)."
            )
            _atk_spell  = stats.get("atk_spell", +5)
            _atk_res    = roll_dice(char_name, "1d20", _atk_spell)
            _sw_all_d   = _all_dice(regle)
            if _sw_all_d:
                _sw_dn, _sw_df, _sw_db = _sw_all_d[0]
            else:
                _sw_dn, _sw_df, _sw_db = 1, 8, max(0, _atk_spell - 4)
            _dmg_res = roll_dice(char_name, f"{_sw_dn}d{_sw_df}", _sw_db)
            results.append(f"  [jet d'attaque de sort] {_atk_res}")
            results.append(f"  [dégâts si touche] {_dmg_res}  (force)")
            results.append(f"  → MJ : confirmer Touché ou Raté")
            narrative_hint = (
                f"L'arme spectrale de {char_name} fonce vers {cible} et frappe. "
                f"Narre en 1-2 phrases le déplacement et l'attaque de l'arme. "
                f"Ne mentionne pas les chiffres."
            )
            return (
                f"[RÉSULTAT SYSTÈME — ATTAQUE ARME SPECTRALE — {char_name}]\n"
                f"⚠ AUCUN SLOT D'EMPLACEMENT REQUIS — L'ARME EST DÉJÀ INVOQUÉE.\n"
                f"Déplacement : Action Gratuite. Attaque : Action Bonus (1 seule consommation).\n"
                + "\n".join(results)
                + "\n\n[INSTRUCTION NARRATIVE]\n"
                + narrative_hint
            )

        # Cas 2 : attaque pure avec l'arme spectrale (aucun mot de mouvement détecté)
        # → comportement original : résoudre l'attaque immédiatement
        elif _spectral_exists and _match:
            _atk_spell  = stats.get("atk_spell", +5)
            _atk_res    = roll_dice(char_name, "1d20", _atk_spell)
            # Dégâts : extraire depuis la règle si présent, sinon 1d8 + mod de sort
            _sw_all_d   = _all_dice(regle)
            if _sw_all_d:
                _sw_dn, _sw_df, _sw_db = _sw_all_d[0]
            else:
                # Spiritual Weapon : 1d8 + mod de sort (atk_spell - prof niv.11 = +4)
                _sw_dn, _sw_df, _sw_db = 1, 8, max(0, _atk_spell - 4)
            _dmg_res = roll_dice(char_name, f"{_sw_dn}d{_sw_df}", _sw_db)
            results.append(
                f"  [✨ {_sum_name}] Active sur la carte — "
                f"Action Bonus d'attaque (AUCUN SLOT REQUIS)"
            )
            results.append(f"  [jet d'attaque de sort] {_atk_res}")
            results.append(f"  [dégâts si touche] {_dmg_res}  (force)")
            results.append(f"  → MJ : confirmer Touché ou Raté")
            narrative_hint = (
                f"L'arme spectrale de {char_name} est déjà présente sur le champ de bataille. "
                f"Narre en 1 phrase l'attaque de l'arme sur {cible}. "
                f"Ne mentionne pas les chiffres."
            )
            return (
                f"[RÉSULTAT SYSTÈME — ATTAQUE ARME SPECTRALE — {char_name}]\n"
                f"⚠ AUCUN SLOT D'EMPLACEMENT REQUIS — L'ARME EST DÉJÀ INVOQUÉE.\n"
                f"Cette action est une Action Bonus d'attaque, pas un nouveau lancer de sort.\n"
                + "\n".join(results)
                + "\n\n[INSTRUCTION NARRATIVE]\n"
                + narrative_hint
            )

        # Slot (uniquement pour les sorts NON-smite)
        if not is_cantrip and lvl:
            # ── Bypass rituel : pas de slot consommé ──
            _combined_text = f"{intention} {regle}".strip()
            _spell_for_ritual = extract_spell_name_fn(_combined_text, char_name) if extract_spell_name_fn else ""
            if _spell_for_ritual and can_ritual_cast(char_name, _spell_for_ritual):
                results.append(
                    f"[🕯️ RITUEL] {_spell_for_ritual} lancé en rituel "
                    f"(+10 min d'incantation, aucun slot consommé)"
                )
            elif _spectral_exists:
                # L'invocation est déjà sur la carte ! (C'est une attaque, pas une incantation)
                results.append(f"  [✨ {_match['name']}] Déjà active sur la carte — pas de nouveau slot requis.")
            else:
                # SUPPRESSION TOTALE DE LA DOUBLE-DÉDUCTION :
                # On supprime l'appel à use_spell_slot() pour TOUS les sorts.
                # Le lanceur (l'agent ou le joueur) gère déjà son propre emplacement en amont.
                results.append(f"  [slot niv.{lvl}] Validé (consommation gérée en amont par le lanceur).")

        # ── INVOCATIONS AUTOMATIQUES SUR LA CARTE ──
        if _match:
            try:
                if _cmap_win is not None:
                    sum_src = _match['src']
                    size = float(_match['size'])
                    
                    _c_col, _c_row = 0, 0
                    _t_col, _t_row = None, None
                    
                    # 1. Chercher si la cible contient des coordonnées "Col X, Lig Y"
                    import re as _summon_re
                    _m_coord = _summon_re.search(r'col(?:onne)?\s*(\d+)[,\s]+(?:lig(?:ne)?|rang(?:ée?)?)\s*(\d+)', cible + " " + intention, _summon_re.IGNORECASE)
                    if _m_coord:
                        _t_col = int(_m_coord.group(1)) - 1
                        _t_row = int(_m_coord.group(2)) - 1
                    
                    for _tok in _cmap_win.tokens:
                        if _tok.get("name") == char_name:
                            _c_col, _c_row = int(round(_tok.get("col", 0))), int(round(_tok.get("row", 0)))
                        # 2. Chercher un token cible si pas de coordonnées exactes
                        if _t_col is None and cible and _tok.get("name", "").lower() == cible.lower():
                            _t_col, _t_row = int(round(_tok.get("col", 0))), int(round(_tok.get("row", 0)))

                    # Placer sur la cible exacte si coordonnée manuelle, sinon case libre proche cible/lanceur
                    if _m_coord:
                        _n_col, _n_row = _t_col, _t_row
                    else:
                        _ref_col, _ref_row = (_t_col, _t_row) if _t_col is not None else (_c_col, _c_row)
                        _n_col, _n_row = _cmap_win._nearest_free_cell(_ref_col, _ref_row, from_col=_c_col, from_row=_c_row)
                        
                    # Déléguer le dessin au fil d'exécution principal (UI Thread) pour éviter le crash Tkinter
                    def _spawn_on_main_thread():
                        _existing = next((t for t in _cmap_win.tokens if t.get("name") == _sum_name), None)
                        if _existing:
                            _existing["col"], _existing["row"] = _n_col, _n_row
                            _cmap_win._redraw_one_token(_existing)
                        else:
                            wpn_tok = {
                                "name": _sum_name,
                                "type": "spectral",
                                "size": size,
                                "col": _n_col, "row": _n_row,
                                "hp": -1, "max_hp": -1,
                                "source_name": sum_src,
                                "alignment": "ally",
                                "aura_radius": _match['aura'],
                                "aura_color": _match['color']
                            }
                            _cmap_win.tokens.append(wpn_tok)
                            _cmap_win._redraw_one_token(wpn_tok)
                        app._save_state()

                    if hasattr(app, "root"):
                        app.root.after(0, _spawn_on_main_thread)
                        
                    if _spectral_exists:
                        results.append(f"[✨ Invocation] {_sum_name} se déplace en Col {_n_col+1}, Lig {_n_row+1}.")
                    else:
                        results.append(f"  [✨ Invocation] {_sum_name} apparaît en Col {_n_col+1}, Lig {_n_row+1}.")
            except Exception as e:
                print(f"[Engine] Erreur spawn invocation : {e}")

        # Jet d'attaque de sort → pré-roller les dégâts
        # Header distinct pour que le calling code utilise mode="attack"
        # Guard : les sorts de soin ne passent JAMAIS par le chemin attaque.
        if is_atk_roll and not is_heal:
            atk_spell = stats.get("atk_spell", +5)
            atk_res = roll_dice(char_name, "1d20", atk_spell)
            results.append(f"  [attaque sort] {atk_res}")

            # Table de dégâts de cantrips : (dés, bonus, type_dégât)
            _CANTRIP_DMG = {
                "rayon de givre":     ("1d8",  0, "froid"),
                "ray of frost":       ("1d8",  0, "froid"),
                "flamme sacrée":      ("2d8",  0, "radiant"),
                "sacred flame":       ("2d8",  0, "radiant"),
                "bourrasque":         ("1d8",  0, "tonnerre"),
                "dard du feu":        ("1d10", 0, "feu"),
                "fire bolt":          ("1d10", 0, "feu"),
                "contact glacial":    ("1d8",  0, "nécrotique"),
                "chill touch":        ("1d8",  0, "nécrotique"),
                "éclair de sorcière": ("1d10", 0, "foudre"),
                "eldritch blast":     ("1d10", 0, "force"),
                "trait de feu":       ("1d10", 0, "feu"),
                "rayon empoisonné":   ("1d4",  0, "poison"),
                "poison spray":       ("1d12", 0, "poison"),
            }
            all_dmg = _all_dice(regle)
            if all_dmg:
                _dn, _df, _db = all_dmg[0]
                _dmg_type = "magique"
            else:
                _cantrip_key = next(
                    (k for k in _CANTRIP_DMG if k in r_low or k in i_low), None
                )
                if _cantrip_key:
                    _dice_str, _db, _dmg_type = _CANTRIP_DMG[_cantrip_key]
                    _dm = _re.match(r"(\d+)d(\d+)", _dice_str)
                    _dn, _df = (int(_dm.group(1)), int(_dm.group(2))) if _dm else (1, 8)
                else:
                    _dn, _df, _db, _dmg_type = 1, 8, 0, "magique"

            dmg_res = roll_dice(char_name, f"{_dn}d{_df}", _db)
            results.append(f"  [dégâts si touche] {dmg_res}  ({_dmg_type})")
            results.append(f"  → MJ : confirmer Touché ou Raté")
            narrative_hint = (
                f"Le système a résolu l'attaque de sort. "
                f"Si touché : narre en 1-2 phrases l'impact du sort sur {cible}. "
                f"Si raté : narre l'esquive ou la résistance. Ne mentionne pas les chiffres."
            )
            return (
                f"[RÉSULTAT SYSTÈME — ATTAQUE DE SORT — {char_name}]\n"
                + "\n".join(results)
                + "\n\n[INSTRUCTION NARRATIVE]\n"
                + narrative_hint
            )

        # ── Sort à touche automatique (pas de jet d'attaque, pas de sauvegarde) ──
        # Détecté par les propriétés du sort dans la DB — aucun nom hardcodé.
        # Couvre Projectile Magique et tout sort auto-hit futur.
        _deals_damage = False
        if _sp_data:
            import json as _json_parser
            _entries_str = _json_parser.dumps(_sp_data.get("entries", []))
            _deals_damage = (
                bool(_sp_data.get("damage_inflict"))
                or "{@damage " in _entries_str
                or "{@dice " in _entries_str
            )
        if not _deals_damage and _all_dice(regle):
            _deals_damage = True

        _is_auto_hit = (
            _sp_data is not None
            and not _sp_data.get("spell_attack")
            and not _sp_data.get("saving_throw")
            and not is_heal
            and not dc_val
            and _deals_damage
        )
        if _is_auto_hit:
            # La DB fait autorité : pas de jet d attaque pour ce sort.
            is_atk_roll = False
            from spell_data import (
                get_spell_damage_expr as _gde,
                get_spell_projectile_count as _gpc,
            )
            _ah_lvl   = lvl if lvl and lvl >= 1 else (_sp_data.get("level", 1) or 1)
            _proj     = _gpc(_spell_name_candidate, _ah_lvl)
            _total_expr = _gde(_spell_name_candidate, _ah_lvl)

            # Fallback : utiliser les dés extraits de la règle si la DB est muette
            if not _total_expr:
                _ah_all = _all_dice(regle)
                if _ah_all:
                    _dn0, _df0, _db0 = _ah_all[0]
                    _total_expr = f"{_dn0}d{_df0}+{_db0}" if _db0 else f"{_dn0}d{_df0}"

            _dmg_type     = (_sp_data.get("damage_inflict") or ["force"])[0]
            _spell_display = _spell_name_candidate or "Sort"

            results.append(
                f"  [{_spell_display} — niv.{_ah_lvl}] "
                f"{_proj} instance(s) — touche(nt) automatiquement"
            )

            _totals_ah: list[int] = []
            if _total_expr:
                _m_te = _re.match(r'(\d+)d(\d+)(?:\+(\d+))?', _total_expr)
                if _proj > 1 and _m_te:
                    # Plusieurs projectiles : on divise l'expression totale par le
                    # nombre de projectiles pour obtenir les dés par instance.
                    # Ex : 3d4+3 / 3 → 1d4+1 par fléchette.
                    _dn_p = max(1, int(_m_te.group(1)) // _proj)
                    _df_p = int(_m_te.group(2))
                    _db_p = int(_m_te.group(3) or 0) // _proj
                    for _i in range(1, _proj + 1):
                        _dr = roll_dice(char_name, f"{_dn_p}d{_df_p}", _db_p)
                        _dm = _re.search(r"Total\s*=\s*(\d+)", _dr)
                        _totals_ah.append(int(_dm.group(1)) if _dm else 0)
                        results.append(f"  [instance {_i}] {_dr}  ({_dmg_type})")
                else:
                    # Un seul lancer (ou expression non parsable → lancer brut)
                    if _m_te:
                        _dn_s = int(_m_te.group(1))
                        _df_s = int(_m_te.group(2))
                        _db_s = int(_m_te.group(3) or 0)
                        _dr = roll_dice(char_name, f"{_dn_s}d{_df_s}", _db_s)
                    else:
                        _dr = roll_dice(char_name, _total_expr, 0)
                    _dm = _re.search(r"Total\s*=\s*(\d+)", _dr)
                    _totals_ah.append(int(_dm.group(1)) if _dm else 0)
                    results.append(f"  [dégâts] {_dr}  ({_dmg_type})")

            _grand_total = sum(_totals_ah)
            _cible_note = (
                "répartis librement entre les cibles"
                if ("," in cible or " et " in cible)
                else cible
            )
            results.append(
                f"  → Total dégâts {_dmg_type} : {_grand_total} ({_cible_note})"
            )
            narrative_hint = (
                f"Le sort a été résolu automatiquement "
                f"({_proj} instance(s), {_grand_total} dégâts {_dmg_type}). "
                f"Narre en 1-2 phrases l'impact inévitable sur {cible}. "
                f"Ne mentionne pas les chiffres."
            )
            return (
                f"[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ — {char_name}]\n"
                + "\n".join(results)
                + "\n\n[INSTRUCTION NARRATIVE]\n"
                + narrative_hint
            )

        # Dés de dégâts / soin
        all_d = _all_dice(regle)
        _dmg_total_save = 0   # total brut pour la boite sauvegarde
        heal_amt = 0          # Initialisation sécurisée
        
        if all_d:
            dn2, df2, db2 = all_d[0]
            verb = "soin" if is_heal else "dégâts"
            res  = roll_dice(char_name, f"{dn2}d{df2}", db2)
            results.append(f"  [{verb}] {res}")
            if is_heal:
                m_tot_h  = _re.search(r"Total\s*=\s*(\d+)", res)
                heal_amt = int(m_tot_h.group(1)) if m_tot_h else 0
            elif dc_val:
                # Extraire le total pour la boîte de confirmation MJ
                _m_tot_sv = _re.search(r"Total\s*=\s*(\d+)", res)
                _dmg_total_save = int(_m_tot_sv.group(1)) if _m_tot_sv else 0

        elif is_heal:
            # Soin sans dés (ex: Imposition des mains)
            _combined_text = regle + " " + intention
            _m_flat = _re.search(r"(\d+)\s*(?:pv|hp|points|de|d'imposition|chacun)", _combined_text, _re.IGNORECASE)
            heal_amt = int(_m_flat.group(1)) if _m_flat else 0
            
            _intent_low = _combined_text.lower()
            if heal_amt == 0 and ("imposition" in _intent_low or "lay on hands" in _intent_low):
                _nums = _re.findall(r"\b(\d+)\b", _combined_text)
                _valid_nums =[int(n) for n in _nums if int(n) > 0 and int(n) <= 100]
                if _valid_nums:
                    # On prend le premier nombre (évite de prendre la jauge max par erreur)
                    heal_amt = _valid_nums[0]

        # ── Rétrogradation des faux soins (ex: demander à être soigné en RP) ──
        if is_heal and not all_d and heal_amt <= 0:
            is_heal = False

        # Préparation de l'affichage des soins (l'application réelle se fait lors du clic MJ)
        if is_heal and heal_amt > 0:
            _HEAL_NAMES =["Kaelen", "Elara", "Thorne", "Lyra"]
            try:
                from state_manager import load_state as _ls_heal
                _HEAL_NAMES = list(_ls_heal().get("characters", {}).keys()) or _HEAL_NAMES
            except Exception:
                pass
            targets =[n for n in _HEAL_NAMES if n.lower() in cible.lower()]
            if not targets:
                targets =[cible if cible.strip() not in ("-", "aucun", "aucune", "") else char_name]

            _intent_low = (regle + " " + intention).lower()
            _is_loh = "imposition" in _intent_low or "lay on hands" in _intent_low
            _curr_loh = 0
            
            if _is_loh:
                try:
                    from state_manager import load_state as _ls_loh
                    _st_loh = _ls_loh()
                    _feats_loh = _st_loh.get("characters", {}).get(char_name, {}).get("features", {})
                    _curr_loh = _feats_loh.get("lay_on_hands", 0)
                except Exception as e:
                    print(f"[Lay on Hands Error] {e}")

            # ── CORRECTION DU BUG MATHÉMATIQUE (Valeur globale vs Valeur par cible) ──
            if not all_d and len(targets) > 1:
                _all_nums =[int(n) for n in _re.findall(r"\b(\d+)\b", regle + " " + intention) if int(n) > 0]
                # Si heal_amt est le total (ex: 30) et que la part individuelle (15) est aussi dans le texte
                if heal_amt in _all_nums and (heal_amt // len(targets)) in _all_nums:
                    heal_amt = heal_amt // len(targets)
                # Ou si le texte dit explicitement que les PV sont répartis
                elif any(kw in _intent_low for kw in ("partagé", "réparti", "reparti", "total", "divisé", "divise")):
                    heal_amt = heal_amt // len(targets)
                # Fallback ultime pour Imposition : si la valeur globale dépasse la jauge mais passe si on la divise
                elif _is_loh and (heal_amt * len(targets)) > _curr_loh >= heal_amt:
                    heal_amt = heal_amt // len(targets)

            # Restauration de la ligne nécessaire pour que chat_mixin extraie le montant fixe
            if not all_d:
                results.append(f"[soin] Total = {heal_amt} (montant fixe)")

            if _is_loh:
                _total_cost = heal_amt * len(targets)
                if _curr_loh >= _total_cost:
                    results.append(f"[Imposition des mains] -{_total_cost} points demandés (reste {_curr_loh - _total_cost} après confirmation)")
                else:
                    results.append(f"  [Attention] Pas assez de points Lay on Hands ({_curr_loh} vs {_total_cost} demandés) !")

            for tgt in targets:
                results.append(f"  [PV] En attente de confirmation MJ pour soigner {tgt} de {heal_amt} PV.")

        # ── Jet de sauvegarde avec cible → boite de confirmation MJ ──────────
        if dc_val and not is_atk_roll and not is_heal:
            _save_stat = _save[0].upper() if (_sp_data and _sp_data.get("saving_throw")) else ""
            _save_hint = f" {_save_stat}" if _save_stat else ""
            results.append(
                f"  → Cibles : jet de sauvegarde{_save_hint} DC {dc_val}."
            )
            if _dmg_total_save:
                results.append(
                    f"[Dégâts roulés : {_dmg_total_save} — "
                    f"pleins si raté, divisés par 2 si réussi]"
                )
            else:
                results.append(
                    f"[Aucun dégât — effets actifs uniquement si raté]"
                )
            # Annoter le total pour que engine_receive puisse extraire la valeur
            results.append(f"[__save_dmg_total__:{_dmg_total_save}]")
            narrative_hint = (
                f"Le MJ va confirmer le résultat du jet de sauvegarde. "
                f"Attends la confirmation avant de narrer."
            )
            return (
                f"[RÉSULTAT SYSTÈME — JET DE SAUVEGARDE — {char_name}]\n"
                + "\n".join(results)
                + "\n\n[INSTRUCTION NARRATIVE]\n"
                + narrative_hint
            )

        if is_heal:
            narrative_hint = (
                f"Le système a lancé les dés de soin. "
                f"Narre en 1-2 phrases comment {char_name} canalise l énergie divine "
                f"pour soigner {cible}. Ne mentionne pas les chiffres bruts."
            )
            return (
                f"[RÉSULTAT SYSTÈME — SOIN — {char_name}]\n"
                + "\n".join(results)
                + "\n\n[INSTRUCTION NARRATIVE]\n"
                + narrative_hint
            )

        narrative_hint = (
            f"Le système a exécuté la mécanique du sort. "
            f"Narre en 1-2 phrases comment {char_name} incante et l effet visible sur {cible}. "
            f"Ne mentionne pas les chiffres bruts."
        )

    # ── MOUVEMENT ────────────────────────────────────────────────────
    elif any(k in t_low or k in i_low or k in r_low_orig for k in _DASH_KW):
        MOVE_KW = ("mouvement", "déplace", "deplace", "repositionne",
                   "avance", "recule", "cours", "marche", "approche",
                   "éloigne", "eloigne", "dash", "sprint", "charge",
                   "vers le nord", "vers le sud", "vers l est", "vers l ouest",
                   "vers le", "cases vers", "metres vers", "mètres vers",
                   "se deplace", "se déplace")
        is_move = any(k in r_low_orig or k in i_low for k in MOVE_KW) or "mouvement" in t_low

        if is_move:
            target_token_name = char_name
            # ── Détection d'invocation à déplacer (ex: "Je déplace mon arme...") ──
            _move_intent = i_low + " " + r_low_orig + " " + cible.lower()
            if any(w in _move_intent for w in ("arme", "weapon", "marteau", "hammer", "sphère", "sphere", "main", "hand", "rayon", "moonbeam", "beam", "dague", "dagger", "nuage", "cloud", "invocation", "summon")):
                _summons = {
                    "arme": f"Arme ({char_name})", "weapon": f"Arme ({char_name})",
                    "marteau": f"Arme ({char_name})", "hammer": f"Arme ({char_name})",
                    "sphère": f"Sphère ({char_name})", "sphere": f"Sphère ({char_name})",
                    "main": f"Main ({char_name})", "hand": f"Main ({char_name})",
                    "rayon": f"Rayon ({char_name})", "moonbeam": f"Rayon ({char_name})", "beam": f"Rayon ({char_name})",
                    "dague": f"Dagues ({char_name})", "dagger": f"Dagues ({char_name})", "nuage": f"Dagues ({char_name})", "cloud": f"Dagues ({char_name})"
                }
                _map_tokens =[]
                try:
                    _cw = getattr(app, "_combat_map_win", None)
                    _map_tokens = _cw.tokens if _cw else app._win_state.get("combat_map_data", {}).get("tokens",[])
                except: pass
                
                # Vérifie si le joueur possède un de ces tokens sur la map et s'il en a mentionné le mot-clé
                for kw, s_name in _summons.items():
                    if kw in _move_intent and any(t.get("name") == s_name for t in _map_tokens):
                        target_token_name = s_name
                        break

            # Récupérer la position courante du token (joueur ou invocation)
            # Priorité 1 : fenêtre live (_combat_map_win.tokens) — toujours à jour
            # Priorité 2 : _win_state["combat_map_data"] — fallback si fenêtre fermée
            _cur_col, _cur_row = 0, 0
            _found_in_live = False
            try:
                _cmap_win = getattr(app, "_combat_map_win", None)
                if _cmap_win is not None:
                    for _tok in getattr(_cmap_win, "tokens",[]):
                        if _tok.get("name") == target_token_name:
                            _cur_col = int(round(_tok.get("col", 0)))
                            _cur_row = int(round(_tok.get("row", 0)))
                            _found_in_live = True
                            break
            except Exception:
                pass
            if not _found_in_live:
                try:
                    _map_data = app._win_state.get("combat_map_data", {})
                    for _tok in _map_data.get("tokens",[]):
                        if _tok.get("name") == target_token_name:
                            _cur_col = int(round(_tok.get("col", 0)))
                            _cur_row = int(round(_tok.get("row", 0)))
                            break
                except Exception:
                    pass

            _combined_mv = r_low_orig + " " + i_low + " " + cible.lower()
            _new_col, _new_row = _cur_col, _cur_row

            _m_cases_regle = _re.search(r'(\d+)\s*cases?', r_low_orig, _re.IGNORECASE)
            _m_met_regle   = _re.search(
                r'(\d+(?:[.,]\d+)?)\s*m(?:ètres?|etres?|\.|\b)', r_low_orig
            )

            # 1. Coordonnées explicites (ex: MJ drag preview ou Cible pure)
            _m_exact_cible = _re.match(r'^col(?:onne)?\s*(\d+)[,\s]+(?:lig(?:ne)?|rang(?:ée?)?)\s*(\d+)$', cible.strip(), _re.IGNORECASE)
            
            # 2. Coordonnées absolues dans Règle 5e (l'agent précise sa destination)
            _m_abs = _re.search(
                r'col(?:onne)?\s*(\d+)[,\s]+(?:lig(?:ne)?|rang(?:ée?)?)\s*(\d+)',
                r_low_orig, _re.IGNORECASE
            )

            if _m_exact_cible:
                _new_col = int(_m_exact_cible.group(1)) - 1
                _new_row = int(_m_exact_cible.group(2)) - 1
            elif _m_abs:
                _new_col = int(_m_abs.group(1)) - 1
                _new_row = int(_m_abs.group(2)) - 1
            else:
                # Extraction de la distance : ajout des "ft"
                _m_cases = _re.search(r'(\d+)\s*cases?', _combined_mv)
                _m_ft    = _re.search(r'(\d+)\s*ft', _combined_mv)
                _m_met   = _re.search(r'(\d+(?:[.,]\d+)?)\s*m(?:ètres?|etres?|\b)', _combined_mv)
                
                if _m_cases: _dist = int(_m_cases.group(1))
                elif _m_ft: _dist = max(1, round(int(_m_ft.group(1)) / 5.0))
                elif _m_met: _dist = max(1, round(float(_m_met.group(1).replace(",", ".")) / 1.5))
                else: _dist = 6  # 30 ft par défaut

                _dcol, _drow = 0, 0

                # A. Priorité absolue : Cible d'un token (ex: VexSira)
                try:
                    _cmap_win2 = getattr(app, "_combat_map_win", None)
                    _map_tokens = (
                        getattr(_cmap_win2, "tokens",[]) if _cmap_win2 is not None
                        else app._win_state.get("combat_map_data", {}).get("tokens",[])
                    )
                    for _other in _map_tokens:
                        _oname = _other.get("name", "").lower()
                        if (_oname and _oname in _combined_mv and _other.get("name") != char_name):
                            _oc = int(round(_other.get("col", 0)))
                            _or = int(round(_other.get("row", 0)))
                            _raw_dc = _oc - _cur_col
                            _raw_dr = _or - _cur_row
                            _mag    = max(abs(_raw_dc), abs(_raw_dr)) or 1
                            _dcol   = round(_raw_dc / _mag)
                            _drow   = round(_raw_dr / _mag)
                            break
                except Exception:
                    pass

                # B. Directions composées
                if _dcol == 0 and _drow == 0:
                    _DIR_EXACT =[
                        ("nord-est",   ( 1, -1)), ("nord-ouest", (-1, -1)),
                        ("sud-est",    ( 1,  1)), ("sud-ouest",  (-1,  1)),
                        ("north-east", ( 1, -1)), ("north-west", (-1, -1)),
                        ("south-east", ( 1,  1)), ("south-west", (-1,  1)),
                    ]
                    for _kd, (_dc, _dr) in _DIR_EXACT:
                        if _kd in _combined_mv:
                            _dcol, _drow = _dc, _dr
                            break

                # C. Directions cardinales simples
                if _dcol == 0 and _drow == 0:
                    _DIR_WORD =[
                        ("nord",  ( 0, -1)), ("north", ( 0, -1)),
                        ("sud",   ( 0,  1)), ("south", ( 0,  1)),
                        ("est",   ( 1,  0)), ("east",  ( 1,  0)),
                        ("ouest", (-1,  0)), ("west",  (-1,  0)),
                    ]
                    for _kd, (_dc, _dr) in _DIR_WORD:
                        if _kd == "est" and not _re.search(r"(vers l'|à l'|direction )\b" + _kd + r"\b", _combined_mv):
                            continue # Ignore le verbe "est" !
                        if _re.search(r'\b' + _kd + r'\b', _combined_mv):
                            _dcol, _drow = _dc, _dr
                            break

                _new_col = _cur_col + _dcol * _dist
                _new_row = _cur_row + _drow * _dist

                # Compute Chebyshev distance (D&D diagonal movement)
                _chebyshev = max(abs(_new_col - _cur_col), abs(_new_row - _cur_row))
                _ft_requested = _chebyshev * 5
                # Get remaining movement from COMBAT_STATE
                try:
                    from combat_tracker_state import COMBAT_STATE as _CS2
                    _rem_mv = _CS2.get("turn_res", {}).get(char_name, {}).get("movement", stats.get("speed", 30))
                except Exception:
                    _rem_mv = stats.get("speed", 30)

                if _ft_requested > _rem_mv:
                    # Scale back: move only as many cases as we have budget for
                    _allowed_cases = _rem_mv // 5
                    if _chebyshev > 0:
                        _ratio = _allowed_cases / _chebyshev
                        _new_col = _cur_col + round((_new_col - _cur_col) * _ratio)
                        _new_row = _cur_row + round((_new_row - _cur_row) * _ratio)

                # ── NEW: stop adjacent to enemy token (don't stack on same square) ────────
                # If the destination square is occupied by a hostile token, back off 1 case
                try:
                    _cmap = getattr(app, "_combat_map_win", None)
                    _all_toks = getattr(_cmap, "tokens", []) if _cmap else app._win_state.get("combat_map_data", {}).get("tokens", [])
                    for _ot in _all_toks:
                        if (int(round(_ot.get("col", 0))) == _new_col
                                and int(round(_ot.get("row", 0))) == _new_row
                                and _ot.get("name") != char_name):
                            # Retreat by 1 case toward origin
                            _dc = _new_col - _cur_col
                            _dr = _new_row - _cur_row
                            _mag = max(abs(_dc), abs(_dr), 1)
                            _new_col -= round(_dc / _mag)
                            _new_row -= round(_dr / _mag)
                            break
                except Exception:
                    pass

            # Clamp à la grille
            # Priorité : fenêtre live → _win_state → défauts larges
            try:
                _cmap_win3 = getattr(app, "_combat_map_win", None)
                if _cmap_win3 is not None:
                    _grid_cols = getattr(_cmap_win3, "cols", None) or app._win_state.get("combat_map_data", {}).get("cols", 200)
                    _grid_rows = getattr(_cmap_win3, "rows", None) or app._win_state.get("combat_map_data", {}).get("rows", 200)
                else:
                    _grid_cols = app._win_state.get("combat_map_data", {}).get("cols", 200)
                    _grid_rows = app._win_state.get("combat_map_data", {}).get("rows", 200)
            except Exception:
                _grid_cols, _grid_rows = 200, 200
            _new_col = max(0, min(_grid_cols - 1, _new_col))
            _new_row = max(0, min(_grid_rows - 1, _new_row))

            _dist_actual = max(abs(_new_col - _cur_col), abs(_new_row - _cur_row))
            _dist_m = _dist_actual * 1.5
            _dist_ft = _dist_actual * 5

            # ── Déduction de la vitesse dans l'état de combat ──
            _rem_mov_str = ""
            if target_token_name == char_name:  # Ne pas déduire si c'est une arme spirituelle qui bouge
                try:
                    from combat_tracker_state import COMBAT_STATE as _CS
                    if _CS.get("active") and _CS.get("active_combatant") == char_name:
                        _tr = _CS.setdefault("turn_res", {}).setdefault(char_name, {})
                        _base_speed = stats.get("speed", 30)
                        _cur_mov = _tr.get("movement", _base_speed)
                        _tr["movement"] = max(0, _cur_mov - _dist_ft)
                        _rem_mov_str = f"\n  Vitesse restante  : {_tr['movement']} ft"
                except Exception:
                    pass
            # ───────────────────────────────────────────────────

            results.append(f"🏃 {target_token_name} — {intention}")
            results.append(f"  Position actuelle : Col {_cur_col+1}, Lig {_cur_row+1}")
            results.append(f"  Destination       : Col {_new_col+1}, Lig {_new_row+1}")
            results.append(f"  Distance          : {_dist_actual} cases ({_dist_ft} ft / {_dist_m:.1f} m){_rem_mov_str}")
            results.append(f"[MOVE_TOKEN:{target_token_name}:{_new_col}:{_new_row}]")

            # ── POST-MOUVEMENT : Rappel portée mêlée ──────────────────────────
            # Calcule la distance Chebyshev entre la nouvelle position et chaque
            # token ennemi (non-PJ, non-allié) pour prévenir l'agent de façon
            # explicite s'il est — ou non — à portée d'attaque au corps-à-corps.
            _melee_reminder = ""
            try:
                _cmap_post = getattr(app, "_combat_map_win", None)
                _all_toks_post = (
                    getattr(_cmap_post, "tokens", []) if _cmap_post is not None
                    else app._win_state.get("combat_map_data", {}).get("tokens", [])
                )
                _pc_names = set(char_mechanics.keys())
                _in_melee = []
                _nearest_name = None
                _nearest_dist = 9999
                for _pt in _all_toks_post:
                    _pn = _pt.get("name", "")
                    if not _pn or _pn == target_token_name:
                        continue
                    # Ignore allied / PC tokens
                    if _pn in _pc_names or _pt.get("alignment") == "ally":
                        continue
                    _pc2 = int(round(_pt.get("col", 0)))
                    _pr2 = int(round(_pt.get("row", 0)))
                    _cheb = max(abs(_pc2 - _new_col), abs(_pr2 - _new_row))
                    if _cheb <= 1:
                        _in_melee.append(_pn)
                    elif _cheb < _nearest_dist:
                        _nearest_dist = _cheb
                        _nearest_name = _pn
                if _in_melee:
                    _names_str = ", ".join(_in_melee)
                    _melee_reminder = (
                        f"\n⚔️ PORTÉE MÊLÉE : {target_token_name} EST à portée de mêlée de "
                        f"{_names_str} — une attaque corps-à-corps est possible ce tour."
                    )
                elif _nearest_name:
                    _dist_ft_near = _nearest_dist * 5
                    _melee_reminder = (
                        f"\n⚠️ PORTÉE MÊLÉE : {target_token_name} N'EST PAS encore à portée de mêlée. "
                        f"Ennemi le plus proche : {_nearest_name} "
                        f"({_nearest_dist} case{'s' if _nearest_dist > 1 else ''} / {_dist_ft_near} ft). "
                        f"Une attaque corps-à-corps n'est PAS possible depuis cette position."
                    )
                else:
                    _melee_reminder = (
                        f"\n⚠️ PORTÉE MÊLÉE : Aucun ennemi détecté à portée de mêlée."
                    )
            except Exception:
                pass
            if _melee_reminder:
                results.append(_melee_reminder)
            # ──────────────────────────────────────────────────────────────────

            narrative_hint = (
                f"Le système a calculé le déplacement. "
                f"Narre en 1 phrase le mouvement de {target_token_name} : {intention}. "
                f"Décris la façon dont il se déplace, son attitude, pas les coordonnées. "
                f"Vérifie le rappel PORTÉE MÊLÉE ci-dessus avant de proposer ou décrire "
                f"toute attaque au corps-à-corps."
            )
        else:
            results.append(f"⚙️ {char_name} — {intention}")
            results.append(f"  Mécanique : {regle} | Cible : {cible}")
            narrative_hint = (
                f"Narre en 1-2 phrases l action de {char_name} : {intention}. "
                f"Si des dés sont encore nécessaires, pose un nouveau [ACTION]."
            )

    else:
        # Autre action non couverte
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