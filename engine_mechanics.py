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
        "atk_melee": +11, "atk_ranged": +7, "atk_spell": +9,
        "dmg_melee": (2, 6, +8), "n_attacks": 2, "save_dc": 18,
        "skills": {"athlétisme":+10,"religion":+5,"persuasion":+9,
                   "perspicacité":+7,"intimidation":+9,"perception":+7},
        "saves":  {"force":+10,"dextérité":+7,"constitution":+8,
                   "intelligence":+5,"sagesse":+7,"charisme":+9},
    },
    "Elara": {   # Mage 11 — STR8 DEX16 CON14 INT20 WIS14 CHA10 — Prof+4
        "atk_melee": +3, "atk_ranged": +8, "atk_spell": +10,
        "dmg_melee": (1, 4, -1), "n_attacks": 1, "save_dc": 18,
        "skills": {"arcanes":+15,"histoire":+10,"investigation":+10,
                   "nature":+10,"religion":+10,"perception":+7,"perspicacité":+7},
        "saves":  {"force":-1,"dextérité":+8,"constitution":+7,
                   "intelligence":+10,"sagesse":+7,"charisme":+5},
    },
    "Thorne": {  # Voleur Assassin 11 — STR12 DEX20 CON14 INT16 WIS12 CHA14 — Prof+4
        "atk_melee": +11, "atk_ranged": +11, "atk_spell": None,
        "dmg_melee": (1, 6, +5), "dmg_sneak": (8, 6, 0),
        "n_attacks": 2, "save_dc": None,
        "skills": {"discrétion":+15,"escamotage":+15,"tromperie":+12,
                   "perception":+11,"perspicacité":+6,"acrobaties":+10,
                   "investigation":+8,"athlétisme":+6,"intimidation":+7},
        "saves":  {"force":+6,"dextérité":+10,"constitution":+7,
                   "intelligence":+8,"sagesse":+6,"charisme":+7},
    },
    "Lyra": {    # Clerc Vie 11 — STR14 DEX12 CON14 INT12 WIS20 CHA16 — Prof+4
        "atk_melee": +7, "atk_ranged": +6, "atk_spell": +10,
        "dmg_melee": (1, 8, +2), "n_attacks": 1, "save_dc": 18,
        "skills": {"médecine":+15,"perspicacité":+10,"religion":+6,
                   "persuasion":+8,"perception":+10,"histoire":+6},
        "saves":  {"force":+7,"dextérité":+6,"constitution":+7,
                   "intelligence":+6,"sagesse":+10,"charisme":+8},
    },
}


# ─── split_into_subactions ────────────────────────────────────────────────────

def split_into_subactions(type_label: str, intention: str,
                          regle: str, cible: str,
                          char_mechanics: dict | None = None) -> list:
    """
    Décompose un bloc [ACTION] en sous-actions individuelles.

    • Extra Attack (Attaque × N) → une carte de confirmation par attaque.
    • Bloc attaque + smite combiné → single_attack=True (flow Phase 1/2/3).
    • Toute autre attaque détectée   → N cartes single_attack=True (N depuis char_mechanics).
    • Tout autre bloc → une seule carte.

    Retourne une liste de dict {type_label, intention, regle, cible}.
    """
    type_low   = (type_label or "").lower()
    intent_low = intention.lower()
    regle_low  = regle.lower()
    combined   = type_low + " " + intent_low + " " + regle_low

    # ── Ready Action (Se Tenir Prêt) — pas d'Extra Attack, pas de jets ──
    _READY_KW = ("ready", "se tenir prêt", "tenir prêt", "me tenir prêt",
                 "prépare une action", "préparer une action", "ready action",
                 "action préparée", "se prépare à")
    _is_ready = any(k in combined for k in _READY_KW)
    if _is_ready:
        return [{
            "type_label": type_label or "Action",
            "intention":  intention,
            "regle":      regle.strip(),
            "cible":      cible,
            "ready_action": True,
        }]

    # ── Détection Extra Attack (format structuré ou langage naturel) ──
    is_extra = (
        "extra attack" in combined
        or bool(_re.search(r'(?:attaque|frappe|coup|tir)[s]?\s*[×x]\s*\d+', combined))
        or bool(_re.search(r'\d+\s*(?:attaques?|frappes?|coups?|tirs?)', combined))
        or "deux fois" in intent_low
        or "deux attaques" in intent_low
        or "two attacks" in combined
        or "deux frappes" in intent_low
        or "deux coups" in intent_low
        or "deux tirs" in intent_low
    )

    if is_extra:
        # Extraire le nombre d'attaques attendu (de base 2, ou défini dans les stats)
        expected_n = 2
        if char_mechanics and char_mechanics.get("n_attacks", 1) > 1:
            expected_n = char_mechanics.get("n_attacks", 2)
            
        _n_m = (
            _re.search(r'(?:attaque|frappe|coup|tir)[s]?\s*[×x]\s*(\d+)', combined)
            or _re.search(r'(\d+)\s*(?:fois|attaques?|frappes?|coups?|tirs?)', intent_low)
        )
        if _n_m:
            expected_n = max(expected_n, int(_n_m.group(1)))
        elif "deux" in combined or "2" in type_low:
            expected_n = max(expected_n, 2)

        # Cas 1 : lignes "Attaque N : détail" dans le champ règle (séparées par \n ou |)
        parts = _re.split(r'\||\n', regle)
        lines =[]
        for p in parts:
            m = _re.search(r'attaque\s*(\d+)\s*:\s*(.*)', p, _re.IGNORECASE)
            if m:
                lines.append((m.group(1), m.group(2).strip()))
                
        if lines:
            # Si le LLM n'a formaté qu'une seule attaque mais qu'on en attend 2+ (oubli fréquent)
            if len(lines) < expected_n:
                last_detail = lines[-1][1]
                while len(lines) < expected_n:
                    lines.append((str(len(lines)+1), last_detail))
            
            total = len(lines)
            return[
                {
                    "type_label":    f"Action — Attaque {i+1}/{total} (Extra Attack)",
                    "intention":     intention,
                    "regle":         detail,
                    "cible":         cible,
                    "single_attack": True,
                }
                for i, (_, detail) in enumerate(lines)
            ]

        # Cas 2 : pas de lignes structurées
        regle_clean = regle.strip()
        return[
            {
                "type_label":    f"Action — Attaque {i+1}/{expected_n} (Extra Attack)",
                "intention":     intention,
                "regle":         regle_clean,
                "cible":         cible,
                "single_attack": True,
            }
            for i in range(expected_n)
        ]

    # ── Bloc attaque + smite combiné dans un seul [ACTION] ──────────
    _SMITE_DETECT = ("smite", "châtiment", "chatiment", "courroux divin",
                     "frappe tonnerre", "frappe lumière", "branding smite",
                     "divine smite", "smite divin")
    _ATK_DETECT   = ("attaque", "frappe", "coup", "tir", "corps-à-corps",
                     "poignarde", "tranche", "assaut")
    _has_smite = any(k in combined for k in _SMITE_DETECT)
    _has_atk   = any(k in combined for k in _ATK_DETECT)
    if _has_smite and _has_atk:
        return [{
            "type_label":    type_label or "Action",
            "intention":     intention,
            "regle":         regle.strip(),
            "cible":         cible,
            "single_attack": True,
        }]

    # ── Attaque simple (ou Sneak Attack, Reckless Attack, etc.) ──────────────
    # Toute action qui contient un indicateur d'attaque passe en Phase 1/2/3.
    # On crée N sous-actions distinctes selon char_mechanics["n_attacks"].
    _GENERIC_ATK = (
        "attaque", "frappe", "coup", "tir", "poignarde", "tranche",
        "assaut", "perfore", "lacère", "abat", "sneak attack", "sournoise",
        "reckless", "téméraire", "deux armes", "dual wield",
    )
    _has_generic_atk = any(k in combined for k in _GENERIC_ATK)

    # Filtre pour éviter de multiplier les sorts (ex: "Je lance un sort d'attaque")
    _SPELL_DETECT = ("sort", "magie", "incant", "sacred flame", "flamme sacrée")
    _is_spell_atk = any(k in combined for k in _SPELL_DETECT)

    if _has_generic_atk:
        _n_atk = 1
        if not _is_spell_atk:
            if char_mechanics:
                # char_mechanics peut être le dict global (clé = char_name) ou le dict du perso
                _stats = char_mechanics if "n_attacks" in char_mechanics else {}
                _n_atk = _stats.get("n_attacks", 1)
            # Sécurité : si le texte mentionne explicitement un nombre
            _n_m2 = _re.search(r'\b(\d)\s*(?:attaques?|frappes?|coups?)\b', combined)
            if _n_m2:
                _n_atk = max(_n_atk, int(_n_m2.group(1)))
                
        if _n_atk <= 1:
            return[{
                "type_label":    type_label or "Action",
                "intention":     intention,
                "regle":         regle.strip(),
                "cible":         cible,
                "single_attack": True,
            }]
        else:
            return[
                {
                    "type_label":    f"Action — Attaque {i+1}/{_n_atk}",
                    "intention":     intention,
                    "regle":         regle.strip(),
                    "cible":         cible,
                    "single_attack": True,
                }
                for i in range(_n_atk)
            ]

    return [{
        "type_label": type_label or "Action",
        "intention":  intention,
        "regle":      regle.strip(),
        "cible":      cible,
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
        r'(?:corps[- ]à[- ]corps|mêlée|melee|distance|ranged|attaque)[^,]*?([+-]\d+)',
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
        return [(int(m.group(1)), int(m.group(2)),
                 int(m.group(3).replace(" ","")) if m.group(3) else 0)
                for m in _re.finditer(r"(\d+)d(\d+)(?:\s*([+-]\s*\d+))?",
                                      text, _re.IGNORECASE)]
    all_d = _all_dice_local(regle)
    dmg_d = all_d[0] if all_d else None
    if dmg_d is None:
        dn, df, db = stats.get("dmg_melee", (1, 8, 0))
    else:
        dn, df, db = dmg_d

    atk_res  = roll_dice(char_name, "1d20", atk_bonus)
    lines    = [f"⚔️ {char_name} attaque {cible}"]
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
                     char_mechanics: dict) -> tuple:
    """
    Phase 2 d'une attaque : lance les dés de dégâts (+ smite si présent).
    Retourne (feedback_str, total_damage_int) pour l'hyperlien du chat.
    Le total additionne tous les composants (dégâts bruts + smite + sournoise).
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
        sm_res = roll_dice(char_name, smite["dice"], 0)
        lines.append(
            f"  [✨ {smite['label']}] {sm_res}  "
            f"(dégâts {smite['type']} supplémentaires)"
        )
        grand_total += _extract_total(sm_res)

    if char_name == "Thorne":
        sn, sf, sb = char_mechanics.get("Thorne", {}).get("dmg_sneak", (8, 6, 0))
        snk_res = roll_dice("Thorne", f"{sn}d{sf}", sb)
        lines.append(f"  [sournoise] {snk_res}  ← si avantage/allié adjacent")
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
            "[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ]\n"
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
            results.append(f"  [Mécanique officielle]\n{_feat_details['text']}")
        else:
            results.append(f"  [Capacité de classe — aucun jet de dés requis]")
        if cible and cible.lower() not in ("soi-même", "self", "-", ""):
            results.append(f"  Cible : {cible}")
        narrative_hint = _narr_hint.format(name=char_name)
        return (
            "[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ]\n"
            + "\n".join(results)
            + "\n\n[INSTRUCTION NARRATIVE]\n"
            + narrative_hint
        )

    # Helpers
    def _all_dice(text):
        return [(int(m.group(1)), int(m.group(2)),
                 int(m.group(3).replace(" ","")) if m.group(3) else 0)
                for m in _re.finditer(r"(\d+)d(\d+)(?:\s*([+-]\s*\d+))?",
                                      text, _re.IGNORECASE)]

    def _extract_dc(text):
        m = _re.search(r"\bDC\s*(\d+)", text, _re.IGNORECASE)
        return int(m.group(1)) if m else None

    def _extract_level(text):
        for pat in (r"niv(?:eau)?\.?\s*(\d+)", r"niveau\s*(\d+)", r"\bniv(\d+)"):
            m = _re.search(pat, text, _re.IGNORECASE)
            if m: return int(m.group(1))
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
                "lumière","ténèbres","sacré","nécro","évocation","abjuration")
    ATK_KW   = ("attaque","frappe","coup","tir","tire","charge","poignarde",
                "tranche","abat","corps-à-corps","distance","assaut","offensive")
    SKILL_KW = ("jet","check","compétence","sauvegarde","save","arcanes",
                "perception","investigation","discrétion","athlétisme",
                "acrobaties","médecine","histoire","nature","religion",
                "perspicacité","intimidation","tromperie","persuasion",
                "dressage","survie","escamotage","force","dextérité",
                "constitution","intelligence","sagesse","charisme")

    # Mots-clés smite — augmentent une attaque, PAS des sorts indépendants.
    _SMITE_BOOST_KW = ("divine smite", "smite divin", "châtiment divin", "chatiment divin",
                       "wrathful smite", "courroux divin", "thunderous smite",
                       "frappe tonnerre", "branding smite", "frappe lumière")
    _is_smite_boost = any(k in r_low or k in i_low for k in _SMITE_BOOST_KW)

    is_spell = any(k in r_low or k in i_low for k in SPELL_KW) and not _is_smite_boost
    # Garde mouvement : si type_label est explicitement "Mouvement", aucun jet de compétence
    # ne doit être déclenché — le LLM met parfois "analyser / détecter" dans l'intention
    # d'un déplacement, ce qui ferait firer is_skill avant la branche mouvement.
    # Défini AVANT is_atk pour éviter "referenced before assignment".
    is_move_action = "mouvement" in t_low
    is_atk   = (any(k in r_low or k in i_low for k in ATK_KW) or _is_smite_boost) and not is_spell and not is_move_action
    is_skill = (any(k in r_low or k in i_low for k in SKILL_KW)
                and not is_atk and not is_spell and not is_move_action)

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
        for i in range(1, n_atk + 1):
            atk_res = roll_dice(char_name, "1d20", atk_bonus)
            lbl = f"attaque {i}/{n_atk}" if n_atk > 1 else "attaque"
            results.append(f"  [{lbl}] {atk_res}")

            nat = _first_roll(atk_res)
            tot = _total(atk_res)

            if nat == 20:
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
            sm_res = roll_dice(char_name, _sm["dice"], 0)
            results.append(
                f"  [✨ {_sm['label']}] {sm_res}  "
                f"(dégâts {_sm['type']} supplémentaires)"
            )

        # Attaque sournoise Thorne
        if char_name == "Thorne":
            sn, sf, sb = stats.get("dmg_sneak", (8, 6, 0))
            snk_res = roll_dice("Thorne", f"{sn}d{sf}", sb)
            results.append(f"  [sournoise] {snk_res}  ← si avantage/allié adjacent")

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
                                  "restaure","parole curative"))
        is_atk_roll = (any(k in r_low for k in ("jet d attaque de sort",
                                                  "attaque de sort"))
                       or (not is_heal and "rayon" in r_low
                           and not _re.search(r"rayon\s+de\s+\d+", r_low)))
        dc_val    = _extract_dc(regle)

        # Vérification liste de sorts préparés
        _spell_name_candidate = extract_spell_name_fn(intention, char_name) if extract_spell_name_fn else ""
        if not is_cantrip and _spell_name_candidate:
            if not is_spell_prepared_fn(char_name, _spell_name_candidate):
                _avail = get_prepared_spell_names_fn(char_name)
                _avail_str = ", ".join(_avail) if _avail else "aucun sort préparé trouvé"
                _no_prep_msg = (
                    f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE]\n"
                    f"« {_spell_name_candidate} » n'est pas dans la liste de sorts "
                    f"préparés de {char_name}. Ce sort ne peut pas être lancé aujourd'hui.\n\n"
                    f"[SORTS AUTORISÉS POUR {char_name.upper()}]\n"
                    f"{_avail_str}\n\n"
                    f"[INSTRUCTION]\n"
                    f"Choisis UNIQUEMENT parmi les sorts listés ci-dessus. "
                    f"Déclare une nouvelle action avec [ACTION]."
                )
                return "[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE]\n" + _no_prep_msg

        # Injection des mécaniques depuis spell_data.py
        _sp_data = None
        if _spell_name_candidate:
            try:
                from spell_data import get_spell as _get_spell
                _sp_data = _get_spell(_spell_name_candidate)
            except Exception:
                pass

        if _sp_data:
            # Jet d'attaque ? — jamais pour un sort de soin.
            if _sp_data.get("spell_attack") and not is_atk_roll and not is_heal:
                is_atk_roll = True

            # Sauvegarde ?
            _save = _sp_data.get("saving_throw", [])
            _dc_stat = stats.get("save_dc")
            if _save and not dc_val and _dc_stat:
                dc_val = _dc_stat  # On assigne la stat de DC du perso
                # On force la mention du jet de sauvegarde dans results plus tard

            # Dégâts/Soins dynamiques depuis le tag {@damage XdY} ou {@dice XdY}
            if not _all_dice(regle):
                import json as _json_parser
                _entries_str = _json_parser.dumps(_sp_data.get("entries", []))
                _dmg_matches = _re.findall(r"\{@(damage|dice)\s+(\d+d\d+)[^}]*\}", _entries_str)
                if _dmg_matches:
                    _base_dice = _dmg_matches[0][1]
                    _base_lvl = _sp_data.get("level", 0)
                    if lvl and lvl > _base_lvl and _sp_data.get("entries_higher"):
                        _higher_str = _json_parser.dumps(_sp_data["entries_higher"])
                        _scale_m = _re.search(r"\{@scaledamage\s+[^|]+\|[^|]+\|(\d+d\d+)\}", _higher_str)
                        if _scale_m:
                            _diff = lvl - _base_lvl
                            _scale_dice = _scale_m.group(1)
                            _sm_m = _re.match(r"(\d+)d(\d+)", _scale_dice)
                            if _sm_m:
                                _ext_dn = int(_sm_m.group(1)) * _diff
                                _ext_df = _sm_m.group(2)
                                _base_m = _re.match(r"(\d+)d(\d+)", _base_dice)
                                if _base_m and _base_m.group(2) == _ext_df:
                                    _new_dn = int(_base_m.group(1)) + _ext_dn
                                    _base_dice = f"{_new_dn}d{_ext_df}"
                                else:
                                    regle += f" + {_ext_dn}d{_ext_df}"
                    regle += f" {_base_dice} "

        results.append(f"✨ {char_name} — {intention.strip()} (niv.{lvl or 0}) → {cible}")

        # ── Smite spells → détection EN PREMIER, AVANT consommation de slot ──
        _SMITE_TABLE = {
            "wrathful smite":   ("1d6",  "psychique",  "Wrathful Smite"),
            "courroux divin":   ("1d6",  "psychique",  "Wrathful Smite"),
            "thunderous smite": ("2d6",  "tonnerre",   "Thunderous Smite"),
            "frappe tonnerre":  ("2d6",  "tonnerre",   "Thunderous Smite"),
            "branding smite":   ("2d6",  "radiant",    "Branding Smite"),
            "frappe lumière":   ("2d6",  "radiant",    "Branding Smite"),
            "divine smite":     (None,   "radiant",    "Divine Smite"),
            "smite divin":      (None,   "radiant",    "Divine Smite"),
            "châtiment divin":  (None,   "radiant",    "Divine Smite"),
            "chatiment divin":  (None,   "radiant",    "Divine Smite"),
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
                "[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ]\n"
                + "\n".join(results)
                + "\n\n[INSTRUCTION NARRATIVE]\n"
                + narrative_hint
            )

        # Slot (uniquement pour les sorts NON-smite)
        if not is_cantrip and lvl:
            # ── Bypass rituel : pas de slot consommé ──
            _spell_for_ritual = extract_spell_name_fn(intention, char_name) if extract_spell_name_fn else ""
            if _spell_for_ritual and can_ritual_cast(char_name, _spell_for_ritual):
                results.append(
                    f"  [🕯️ RITUEL] {_spell_for_ritual} lancé en rituel "
                    f"(+10 min d'incantation, aucun slot consommé)"
                )
            else:
                slot_res = use_spell_slot(char_name, str(lvl))
                results.append(f"  [slot niv.{lvl}] {slot_res}")
                if "ÉCHEC" in slot_res:
                    narrative_hint = (
                        f"{char_name} n a plus de slot de niveau {lvl}. "
                        f"Narre en 1 phrase qu il réalise qu il est à court d énergie magique."
                    )
                    return ("[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ]\n"
                            + "\n".join(results)
                            + "\n\n[INSTRUCTION NARRATIVE]\n" + narrative_hint)

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
                "[RÉSULTAT SYSTÈME — ATTAQUE DE SORT]\n"
                + "\n".join(results)
                + "\n\n[INSTRUCTION NARRATIVE]\n"
                + narrative_hint
            )

        # ── Projectile Magique (touche automatiquement) ───────────────────
        _MM_KW = ("projectile magique", "magic missile", "projectiles magiques")
        _is_magic_missile = any(k in r_low or k in i_low for k in _MM_KW)
        if _is_magic_missile:
            _mm_lvl   = lvl if lvl and lvl >= 1 else 1
            _mm_darts = 3 + max(0, _mm_lvl - 1)   # 3 au niv.1, +1/niveau sup.
            results.append(
                f"  [Projectile Magique — niv.{_mm_lvl}] "
                f"{_mm_darts} projectile(s) — touche(nt) automatiquement"
            )
            _mm_totals = []
            for _i in range(1, _mm_darts + 1):
                _dart_res = roll_dice(char_name, "1d4", 1)   # 1d4+1 force
                _dart_m   = _re.search(r"Total\s*=\s*(\d+)", _dart_res)
                _dart_tot = int(_dart_m.group(1)) if _dart_m else 0
                results.append(f"  [projectile {_i}] {_dart_res}  (dégâts de force)")
                _mm_totals.append(_dart_tot)
            _mm_grand_total = sum(t for t in _mm_totals if isinstance(t, int))
            _cible_note = (
                "répartis librement entre les cibles"
                if ("," in cible or " et " in cible)
                else cible
            )
            results.append(f"  → Total dégâts de force : {_mm_grand_total} ({_cible_note})")
            narrative_hint = (
                f"Le système a lancé les {_mm_darts} projectile(s) de force. "
                f"Narre en 1-2 phrases comment les flèches de lumière dorée fusent "
                f"inévitablement vers {cible} et l'impact (total {_mm_grand_total} dégâts de force). "
                f"Ne mentionne pas les chiffres individuels des dés."
            )
            return (
                "[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ]\n"
                + "\n".join(results)
                + "\n\n[INSTRUCTION NARRATIVE]\n"
                + narrative_hint
            )

        # Dés de dégâts / soin
        all_d = _all_dice(regle)
        _dmg_total_save = 0   # total brut pour la boite sauvegarde
        if all_d:
            dn2, df2, db2 = all_d[0]
            verb = "soin" if is_heal else "dégâts"
            res  = roll_dice(char_name, f"{dn2}d{df2}", db2)
            results.append(f"  [{verb}] {res}")
            if is_heal:
                m_tot_h  = _re.search(r"Total\s*=\s*(\d+)", res)
                heal_amt = int(m_tot_h.group(1)) if m_tot_h else 0
                _HEAL_NAMES = ["Kaelen", "Elara", "Thorne", "Lyra"]
                try:
                    from state_manager import load_state as _ls_heal
                    _HEAL_NAMES = list(_ls_heal().get("characters", {}).keys()) or _HEAL_NAMES
                except Exception:
                    pass
                targets = [n for n in _HEAL_NAMES if n.lower() in cible.lower()]
                if not targets:
                    targets = [char_name]
                for tgt in targets:
                    from state_manager import update_hp as _uhp
                    hp_res = _uhp(tgt, heal_amt)
                    results.append(f"  [PV] {hp_res}")
                try:
                    if app._combat_tracker is not None:
                        app.root.after(0, app._combat_tracker.sync_pc_hp_from_state)
                except Exception:
                    pass
            elif dc_val:
                # Extraire le total pour la boîte de confirmation MJ
                _m_tot_sv = _re.search(r"Total\s*=\s*(\d+)", res)
                _dmg_total_save = int(_m_tot_sv.group(1)) if _m_tot_sv else 0

        # ── Jet de sauvegarde avec cible → boite de confirmation MJ ──────────
        if dc_val and not is_atk_roll and not is_heal:
            _save_stat = _save[0].upper() if (_sp_data and _sp_data.get("saving_throw")) else ""
            _save_hint = f" {_save_stat}" if _save_stat else ""
            results.append(
                f"  → Cibles : jet de sauvegarde{_save_hint} DC {dc_val}."
            )
            if _dmg_total_save:
                results.append(
                    f"  [Dégâts roulés : {_dmg_total_save} — "
                    f"pleins si raté, divisés par 2 si réussi]"
                )
            else:
                results.append(
                    f"  [Aucun dégât — effets actifs uniquement si raté]"
                )
            # Annoter le total pour que engine_receive puisse extraire la valeur
            results.append(f"  [__save_dmg_total__:{_dmg_total_save}]")
            narrative_hint = (
                f"Le MJ va confirmer le résultat du jet de sauvegarde. "
                f"Attends la confirmation avant de narrer."
            )
            return (
                "[RÉSULTAT SYSTÈME — JET DE SAUVEGARDE]\n"
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
                "[RÉSULTAT SYSTÈME — SOIN]\n"
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
    elif "mouvement" in t_low or "mouvement" in i_low or "mouvement" in r_low_orig:
        MOVE_KW = ("mouvement", "déplace", "deplace", "repositionne",
                   "avance", "recule", "cours", "marche", "approche",
                   "éloigne", "eloigne", "dash", "sprint", "charge",
                   "vers le nord", "vers le sud", "vers l est", "vers l ouest",
                   "vers le", "cases vers", "metres vers", "mètres vers",
                   "se deplace", "se déplace")
        is_move = any(k in r_low_orig or k in i_low for k in MOVE_KW) or "mouvement" in t_low

        if is_move:
            # Récupérer la position courante du token
            # Priorité 1 : fenêtre live (_combat_map_win.tokens) — toujours à jour
            # Priorité 2 : _win_state["combat_map_data"] — fallback si fenêtre fermée
            _cur_col, _cur_row = 0, 0
            _found_in_live = False
            try:
                _cmap_win = getattr(app, "_combat_map_win", None)
                if _cmap_win is not None:
                    for _tok in getattr(_cmap_win, "tokens", []):
                        if _tok.get("name") == char_name:
                            _cur_col = int(round(_tok.get("col", 0)))
                            _cur_row = int(round(_tok.get("row", 0)))
                            _found_in_live = True
                            break
            except Exception:
                pass
            if not _found_in_live:
                try:
                    _map_data = app._win_state.get("combat_map_data", {})
                    for _tok in _map_data.get("tokens", []):
                        if _tok.get("name") == char_name:
                            _cur_col = int(round(_tok.get("col", 0)))
                            _cur_row = int(round(_tok.get("row", 0)))
                            break
                except Exception:
                    pass

            _combined_mv = r_low_orig + " " + i_low + " " + cible.lower()
            _new_col, _new_row = _cur_col, _cur_row

            # FIX : priorité explicite "N cases / N m" dans le champ Règle 5e
            # AVANT la recherche de coordonnées absolues.
            # Problème original : si l'agent écrit "6 cases (9 m)" dans Règle 5e
            # ET "Col 100, Lig 44" dans Cible, le regex abs matchait dans _combined_mv
            # et ignorait la distance déclarée → déplacement × 5 incorrect.
            _m_cases_regle = _re.search(r'(\d+)\s*cases?', r_low_orig, _re.IGNORECASE)
            _m_met_regle   = _re.search(
                r'(\d+(?:[.,]\d+)?)\s*m(?:ètres?|etres?|\.|\b)', r_low_orig
            )
            _has_relative_dist = bool(_m_cases_regle or _m_met_regle)

            # 1. Coordonnées absolues : "col X, lig Y"
            # Uniquement si le champ Règle 5e ne contient PAS déjà une distance relative.
            # On cherche dans r_low_orig seulement (pas dans cible) pour éviter qu'un
            # "Col X, Lig Y" mis dans Cible n'écrase la distance en cases.
            _m_abs = None
            if not _has_relative_dist:
                _m_abs = _re.search(
                    r'col(?:onne)?\s*(\d+)[,\s]+(?:lig(?:ne)?|rang(?:ée?)?)\s*(\d+)',
                    r_low_orig, _re.IGNORECASE
                )
            if _m_abs:
                _new_col = int(_m_abs.group(1)) - 1   # 1-based → 0-based
                _new_row = int(_m_abs.group(2)) - 1
            else:
                # 2. Distance + direction
                if _m_cases_regle:
                    _dist = int(_m_cases_regle.group(1))
                elif _m_met_regle:
                    _dist = max(1, round(float(_m_met_regle.group(1).replace(",", ".")) / 1.5))
                else:
                    # Fallback : chercher dans le message complet
                    _m_cases = _re.search(r'(\d+)\s*cases?', _combined_mv)
                    _m_met   = _re.search(
                        r'(\d+(?:[.,]\d+)?)\s*m(?:ètres?|etres?|\.|\b)', _combined_mv
                    )
                    if _m_cases:
                        _dist = int(_m_cases.group(1))
                    elif _m_met:
                        _dist = max(1, round(float(_m_met.group(1).replace(",", ".")) / 1.5))
                    else:
                        _dist = 6  # 30 ft par défaut

                # Directions composées (tiret obligatoire) puis cardinales simples
                _DIR_EXACT = [
                    ("nord-est",   ( 1, -1)), ("nord-ouest", (-1, -1)),
                    ("sud-est",    ( 1,  1)), ("sud-ouest",  (-1,  1)),
                    ("north-east", ( 1, -1)), ("north-west", (-1, -1)),
                    ("south-east", ( 1,  1)), ("south-west", (-1,  1)),
                ]
                _DIR_WORD = [
                    ("nord",  ( 0, -1)), ("north", ( 0, -1)),
                    ("sud",   ( 0,  1)), ("south", ( 0,  1)),
                    ("est",   ( 1,  0)), ("east",  ( 1,  0)),
                    ("ouest", (-1,  0)), ("west",  (-1,  0)),
                ]
                _dcol, _drow = 0, 0
                _dir_search = i_low + " " + cible.lower() + " " + r_low_orig
                for _kd, (_dc, _dr) in _DIR_EXACT:
                    if _kd in _dir_search:
                        _dcol, _drow = _dc, _dr
                        break
                if _dcol == 0 and _drow == 0:
                    for _kd, (_dc, _dr) in _DIR_WORD:
                        if _re.search(r'\b' + _kd + r'\b', _dir_search):
                            _dcol, _drow = _dc, _dr
                            break

                # 3. Vers un autre token
                if _dcol == 0 and _drow == 0:
                    try:
                        _cmap_win2 = getattr(app, "_combat_map_win", None)
                        _map_tokens = (
                            getattr(_cmap_win2, "tokens", []) if _cmap_win2 is not None
                            else app._win_state.get("combat_map_data", {}).get("tokens", [])
                        )
                        for _other in _map_tokens:
                            _oname = _other.get("name", "").lower()
                            if (_oname and _oname in _combined_mv
                                    and _other.get("name") != char_name):
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

                # 4. Destination non résoluble → refus propre
                if _dcol == 0 and _drow == 0:
                    narrative_hint = (
                        f"Destination non déterminée automatiquement pour {char_name}. "
                        f"MJ : précise la destination avec 'Col X, Lig Y' ou une direction "
                        f"cardinale (nord/sud/est/ouest) pour déplacer le token manuellement."
                    )
                    results.append(f"🏃 {char_name} — {intention}")
                    results.append(f"  Position actuelle : Col {_cur_col+1}, Lig {_cur_row+1}")
                    results.append(f"  ⚠ Destination '{cible}' non résoluble — token non déplacé.")
                    results.append(f"  → Précise : Col X, Lig Y  OU  direction (nord/sud/est/ouest)")
                    return (
                        "[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ]\n"
                        + "\n".join(results)
                        + "\n\n[INSTRUCTION NARRATIVE]\n"
                        + narrative_hint
                    )

                _new_col = _cur_col + _dcol * _dist
                _new_row = _cur_row + _drow * _dist

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
            results.append(f"🏃 {char_name} — {intention}")
            results.append(f"  Position actuelle : Col {_cur_col+1}, Lig {_cur_row+1}")
            results.append(f"  Destination       : Col {_new_col+1}, Lig {_new_row+1}")
            results.append(f"  Distance          : {_dist_actual} cases ({_dist_m:.1f} m)")
            results.append(f"[MOVE_TOKEN:{char_name}:{_new_col}:{_new_row}]")
            narrative_hint = (
                f"Le système a calculé le déplacement. "
                f"Narre en 1 phrase le mouvement de {char_name} : {intention}. "
                f"Décris la façon dont il se déplace, son attitude, pas les coordonnées."
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
        "[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ]\n"
        + "\n".join(results)
        + "\n\n[INSTRUCTION NARRATIVE]\n"
        + narrative_hint
    )