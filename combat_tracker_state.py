"""
combat_tracker_state.py
───────────────────────
Fichier 1/10 : État global du combat et logique des prompts (règles IA).
"""

try:
    from state_manager import load_state as _sm_load_state
except ImportError:
    _sm_load_state = None

# ─── État de combat partagé avec main.py ──────────────────────────────────────
# Mis à jour à chaque changement de tour ; lu par run_autogen pour contraindre
# les agents hors-tour.
COMBAT_STATE: dict = {
    "active":            False,   # combat en cours ?
    "active_combatant":  None,    # nom du combatant dont c'est le tour (str|None)
    "round_num":         0,
    # Deux ressources hors-tour indépendantes, réinitialisées à chaque round :
    "reactions_used":    set(),   # PJ ayant utilisé leur réaction D&D 5e ce round
    "speech_used":       set(),   # PJ ayant utilisé leur parole hors-tour ce round
    # Flag [PAROLE_SPONTANEE] : indique que la prochaine prise de parole
    # d'un agent est sollicitée par le MJ et ne doit PAS consommer speech_used.
    # Mis à True dans engine_receive quand le MJ envoie [PAROLE_SPONTANEE],
    # remis à False dès que l'agent a répondu.
    "spontaneous_speech_pending": False,
    # Liste des sorts lancés pendant le tour actif pour vérifier la règle des actions bonus
    "turn_spells":[],
    # Objectif tactique du combat, saisi par le MJ au démarrage.
    # Ex: "Éliminer Chain Devil 1 et 2", "Protéger Mira jusqu'à la sortie", "Survivre 5 rounds"
    # Injecté dans le prompt de chaque agent (actif ET hors-tour) pour orienter leurs décisions.
    "combat_goal": "",
    # Historique très concis des événements récents du combat (max ~20 entrées)
    "combat_history": [],
}


def add_combat_history(text: str):
    """Ajoute une entrée courte narrativo-mécanique pour les prompts des agents."""
    ch = COMBAT_STATE.setdefault("combat_history", [])
    ch.append(text.strip())
    if len(ch) > 20:
        ch.pop(0)


def _is_fully_silenced(agent_name: str) -> bool:
    """Retourne True si l'agent a épuisé ses DEUX ressources hors-tour ce round."""
    return (agent_name in COMBAT_STATE["reactions_used"]
            and agent_name in COMBAT_STATE["speech_used"])


def mark_speech_used(agent_name: str):
    """
    Enregistre une prise de parole hors-tour pour agent_name.

    Si spontaneous_speech_pending est True, la parole a été sollicitée par
    le MJ via[PAROLE_SPONTANEE] : elle est GRATUITE et ne consomme PAS
    la ressource parole du round.  Le flag est réinitialisé dans tous les cas.
    """
    if COMBAT_STATE.get("spontaneous_speech_pending"):
        COMBAT_STATE["spontaneous_speech_pending"] = False
        return   # parole MJ-sollicitée → gratuite, pas de débit
    COMBAT_STATE["speech_used"].add(agent_name)
    COMBAT_STATE["spontaneous_speech_pending"] = False


def get_combat_prompt(agent_name: str) -> str:
    """
    Retourne le bloc de règles de combat à injecter dans le system_message
    de l'agent selon l'état courant du combat.
    Appelé depuis main.py à chaque changement de tour.
    """
    cs = COMBAT_STATE
    if not cs["active"]:
        return ""

    active   = cs["active_combatant"] or "?"
    rnd      = cs["round_num"]
    reacted  = agent_name in cs["reactions_used"]
    spoken   = agent_name in cs["speech_used"]

    # Bloc objectif — injecté dans toutes les branches si défini
    _goal     = cs.get("combat_goal", "").strip()
    _goal_block = (
        f"\n🎯 OBJECTIF DU COMBAT : {_goal}\n"
        "   Toutes tes décisions tactiques doivent servir cet objectif en priorité.\n"
        if _goal else ""
    )

    # ── Santé du groupe ───────────────────────────────────────────────
    group_hp_lines = []
    if _sm_load_state is not None:
        try:
            _st = _sm_load_state()
            for _n, _d in _st.get("characters", {}).items():
                if _d.get("active", True):
                    _hp = _d.get("hp", 0)
                    _max = max(1, _d.get("max_hp", 1))
                    group_hp_lines.append(f"  • {_n} : {int((_hp / _max) * 100)}% PV")
        except Exception:
            pass
    if group_hp_lines:
        _goal_block += "\n❤️ ÉTAT DE SANTÉ DES HÉROS :\n" + "\n".join(group_hp_lines) + "\n"

    # ── Historique du combat ──────────────────────────────────────────────────
    _history = cs.get("combat_history", [])
    if _history:
        lines = "\n".join(_history)
        _goal_block += f"\n📜 HISTORIQUE RÉCENT DU COMBAT :\n{lines}\n"

    # ── Tour actif (NOUVEAU FORMAT STRICT) ───────────────────────────────────
    if agent_name == active:
        # 1. Vérifier les ressources consommées
        _tr = COMBAT_STATE.get("turn_res", {}).get(agent_name, {})
        has_action = _tr.get("action", True)
        has_bonus = _tr.get("bonus", True)

        # 2. Récupérer la classe et les sorts
        _char_class = ""
        _slots = {}
        _prepared =[]
        if _sm_load_state is not None:
            try:
                _st = _sm_load_state()
                _char_data = _st.get("characters", {}).get(agent_name, {})
                _char_class = _char_data.get("class", "").lower()
                _slots = _char_data.get("spell_slots", {})
                _prepared = list(_char_data.get("spells_prepared",[]))
                _sub_c = _char_data.get("subclass", "")
                _c_lvl = _char_data.get("level", 1)
                
                if _char_class and _sub_c:
                    try:
                        from class_data import get_subclass_spells
                        for x in get_subclass_spells(_char_class, _sub_c, _c_lvl):
                            if x not in _prepared:
                                _prepared.append(x)
                    except Exception:
                        pass
            except Exception:
                pass

        # 3. Trier les sorts disponibles par type d'action
        action_spells = []
        bonus_spells =[]
        try:
            from spell_data import get_spell
            avail_levels =[lvl for lvl, count in _slots.items() if int(count) > 0]
            for s_name in _prepared:
                sp = get_spell(s_name)
                if not sp: continue
                
                time_raw = sp.get("cast_time_raw", [])
                unit = str(time_raw[0].get("unit", "")).lower() if time_raw else "action"
                s_lvl = int(sp.get("level", 0))
                
                is_castable = (s_lvl == 0) or any(int(l) >= s_lvl for l in avail_levels)
                if is_castable:
                    if "action" in unit and "bonus" not in unit:
                        action_spells.append(s_name)
                    elif "bonus" in unit:
                        bonus_spells.append(s_name)
        except Exception:
            pass

        # 4. Construire le prompt strict
        lines =[
            f"RONDE DE COMBAT {rnd} C'EST TON TOUR",
            "Utilise uniquement les ressources listées ci-dessous",
            "Actions:"
        ]

        # -- Détection contexte (Cibles à portée & Conditions) --
        has_melee_target = True  # Vrai par défaut si pas de carte active
        is_grappled = False
        
        try:
            import __main__
            app_inst = getattr(__main__, "app", None)
            if app_inst:
                # 1. Vérification condition Agrippé (Escape)
                ct = getattr(app_inst, "_combat_tracker", None)
                if ct and hasattr(ct, "combatants"):
                    for c in ct.combatants:
                        if c.name == agent_name:
                            conds =[k.lower() for k in getattr(c, "conditions", {}).keys()]
                            if any("agrip" in x or "grap" in x or "entrav" in x or "restr" in x for x in conds):
                                is_grappled = True
                            break
                            
                # 2. Vérification distances au corps-à-corps (Melee/Grapple/Shove)
                cw = getattr(app_inst, "_combat_map_win", None)
                tokens = getattr(cw, "tokens",[]) if cw else app_inst._win_state.get("combat_map_data", {}).get("tokens",[])
                
                if tokens:
                    my_col, my_row = None, None
                    for t in tokens:
                        if t.get("name") == agent_name:
                            my_col, my_row = t.get("col"), t.get("row")
                            break
                            
                    if my_col is not None and my_row is not None:
                        has_melee_target = False
                        for t in tokens:
                            if t.get("name") != agent_name:
                                t_col, t_row = t.get("col"), t.get("row")
                                if t_col is not None and t_row is not None:
                                    # Tolérance de 2 cases (10ft) pour armes à allonge et monstres larges
                                    if abs(t_col - my_col) <= 2 and abs(t_row - my_row) <= 2:
                                        has_melee_target = True
                                        break
        except Exception:
            pass

        # ── Pré-calcul ba_list (nécessaire pour la détection d'épuisement) ──────
        ba_list = []
        _has_cunning = False
        if has_bonus:
            if _char_class == "rogue":
                ba_list.append("Cunning Action (Dash, Disengage, Hide)")
                _has_cunning = True
            if bonus_spells:
                ba_list.append(f"[{', '.join(bonus_spells)}]")

        # 5. Extra attack + vitesse de base
        n_atk = 1
        base_speed = 30
        try:
            from engine_mechanics import CHAR_MECHANICS
            n_atk = CHAR_MECHANICS.get(agent_name, {}).get("n_attacks", 1)
            base_speed = CHAR_MECHANICS.get(agent_name, {}).get("speed", 30)
        except Exception:
            pass

        # 6. Mouvement restant (en pieds)
        rem_mov = _tr.get("movement", base_speed)

        # ── Détection épuisement total → fin de tour automatique ─────────────
        _no_action  = not has_action
        _no_bonus   = not ba_list          # slot dispo mais rien à faire = inutile
        _no_move    = (rem_mov == 0)

        if _no_action and _no_bonus and _no_move:
            return (
                _goal_block
                + f"\n\n⚔️ RONDE {rnd} — {agent_name} — TOUTES RESSOURCES ÉPUISÉES\n"
                "🚫 Plus d'action, plus d'action bonus, plus de mouvement.\n"
                "✅ Fin de tour automatique.\n\n"
                "[ACTION]\n"
                "Type      : Fin de tour\n"
                f"Intention : {agent_name} a épuisé toutes ses ressources (action, action bonus, mouvement).\n"
                "Règle 5e  : Fin de tour automatique — aucune action envisageable.\n"
                "Cible     : —"
            )

        # ── Ajout des lignes au prompt normal ────────────────────────────────
        if has_action:
            act_list = []
            if has_melee_target:
                act_list.append("Melee")
            act_list.append("ranged attack")
            if has_melee_target:
                act_list.extend(["Grapple", "Shove"])

            if action_spells:
                act_list.append(f"[{', '.join(action_spells)}]")

            # Si Cunning Action est dispo, Dash/Disengage/Hide sont déjà
            # en Bonus Action → ne pas les lister en Action normale.
            if _has_cunning:
                act_list.append("Dodge")
            else:
                act_list.extend(["Dash", "Disengage", "Dodge"])

            if is_grappled:
                act_list.append("Escape")

            if _has_cunning:
                act_list.extend(["Help", "Equip a shield, Unequip a shield", "Ready"])
            else:
                act_list.extend(["Help", "Equip a shield, Unequip a shield", "Hide", "Ready"])

            specials = []
            if _char_class == "paladin": specials.extend(["Lay on Hands", "Turn the Unholy", "Channel Divinity"])
            elif _char_class == "cleric": specials.extend(["Turn Undead", "Channel Divinity"])
            elif _char_class == "rogue": specials.append("Sneak Attack")

            if specials:
                act_list.append(f"[{', '.join(specials)}]")

            lines.append(f"ACTION (1 par tour): {', '.join(act_list)}")
        else:
            lines.append("ACTION: 🚫 DÉJÀ UTILISÉE (Tu n'as plus d'action normale disponible ce tour, utilise seulement action bonus ou mouvement)")

        if ba_list:
            lines.append(f"BONUS ACTION (1 par tour): {', '.join(ba_list)}")

        # FIX : vérifier que le personnage a RÉELLEMENT la capacité Extra Attack,
        # pas seulement n_attacks > 1 (qui peut être > 1 pour d'autres raisons).
        # FIX 2 : cacher si l'Extra Attack a déjà été utilisée ce tour.
        try:
            _has_ea_check = CHAR_MECHANICS.get(agent_name, {}).get("extra_attack", False)
        except Exception:
            _has_ea_check = False
        _ea_used_check = COMBAT_STATE.get("turn_res", {}).get(agent_name, {}).get("extra_attack_used", False)
        if n_atk > 1 and has_action and _has_ea_check and not _ea_used_check:
            lines.append("Special: EXTRA ATTACK (martial classes) AFTER ATTACK ACTION")

        # ── Extra Attack disponible après la 1re attaque d'action ────────
        _ea_avail = COMBAT_STATE.get("turn_res", {}).get(agent_name, {}).get("extra_attack_available", False)
        try:
            from engine_mechanics import CHAR_MECHANICS as _CM_ea
            _has_ea = _CM_ea.get(agent_name, {}).get("extra_attack", False)
        except Exception:
            _has_ea = False
        if _has_ea and _ea_avail and not has_action:
            lines.append(
                "\n⚔️ EXTRA ATTACK DISPONIBLE — Tu as attaqué avec ton Action ce tour."
                "\n   Tu peux faire UNE attaque supplémentaire (fait partie de ton Action, PAS une Action Bonus)."
                "\n   Déclare-la avec [ACTION] Type: Extra Attack."
            )

        lines.append(f"MOUVEMENT RESTANT: {rem_mov} ft ({rem_mov // 5} cases) — 1 case = 5 ft")

        lines.append("")
        lines.append("Déclare UN choix mécanique en utilisant exactement UN bloc [ACTION].")

        # ── Rappel concentration ─────────────────────────────────────────
        try:
            import __main__ as _m_conc
            _app_conc = getattr(_m_conc, "app", None)
            if _app_conc:
                _ct_conc = (
                    getattr(_app_conc, "_combat_tracker_win", None)
                    or getattr(_app_conc, "_combat_tracker", None)
                )
                if _ct_conc and hasattr(_ct_conc, "combatants"):
                    for _cc in _ct_conc.combatants:
                        if _cc.name == agent_name and _cc.concentration and _cc.conc_spell:
                            lines.append(
                                f"\n🔮 TU ES CONCENTRÉ SUR : {_cc.conc_spell} "
                                f"({_cc.conc_rounds_left} tour(s) restant(s)).\n"
                                f"   ⚠️ Lancer un autre sort à concentration "
                                f"mettra AUTOMATIQUEMENT fin à {_cc.conc_spell}."
                            )
                            break
        except Exception:
            pass

        # ── Rappel armes / invocations spectrales actives ────────────────
        # Si un token spectral appartenant à cet agent est sur la carte,
        # rappeler les contraintes mécaniques exactes (mouvement, attaque).
        _SPECTRAL_RULES = {
            "Arme":   ("Spiritual Weapon",   "20 ft (4 cases) par Action Bonus",  "1d8+mod (force) — jet d'attaque de sort +{atk}"),
            "Sphère": ("Flaming Sphere",     "30 ft (6 cases) par Action Bonus",  "2d6 feu — JS Dex DC {dc} ou dégâts à l'entrée/sortie"),
            "Main":   ("Bigby's Hand",       "60 ft (12 cases) par Action Bonus", "Varie selon la commande choisie"),
            "Rayon":  ("Moonbeam",           "60 ft (12 cases) par Action",       "2d10 radiant — JS Con DC {dc}"),
            "Dagues": ("Cloud of Daggers",   "Immobile",                          "4d4 tranchant à l'entrée dans la zone"),
        }
        try:
            import __main__ as _m_sp
            _app_sp = getattr(_m_sp, "app", None)
            if _app_sp:
                _cmap_sp = getattr(_app_sp, "_combat_map_win", None)
                if _cmap_sp and hasattr(_cmap_sp, "tokens"):
                    _spectral_lines = []
                    for _tok in _cmap_sp.tokens:
                        _tname = _tok.get("name", "")
                        for _prefix, (_spell, _mv, _dmg) in _SPECTRAL_RULES.items():
                            if _tname == f"{_prefix} ({agent_name})":
                                _tc = int(round(_tok.get("col", 0))) + 1
                                _tr = int(round(_tok.get("row", 0))) + 1
                                try:
                                    from engine_mechanics import CHAR_MECHANICS as _CM_sp
                                except Exception:
                                    _CM_sp = {}
                                _atk = _CM_sp.get(agent_name, {}).get("atk_spell", "?")
                                _dc  = _CM_sp.get(agent_name, {}).get("save_dc", "?")
                                _dmg_fmt = _dmg.replace("{atk}", str(_atk)).replace("{dc}", str(_dc))
                                _spectral_lines.append(
                                    f"\n✨ {_spell} ACTIVE — Position : Col {_tc}, Lig {_tr}"
                                    f"\n   Déplacement max : {_mv} (Action Bonus — ne coûte PAS de slot)"
                                    f"\n   Attaque/Effet   : {_dmg_fmt}"
                                    f"\n   ⚠️ Déclare le déplacement ET l'attaque dans UN SEUL bloc [ACTION] de type Action Bonus."
                                )
                                break
                    if _spectral_lines:
                        lines.append("".join(_spectral_lines))
        except Exception:
            pass
        # ─────────────────────────────────────────────────────────────────

        return _goal_block + "\n\n" + "\n".join(lines)


    # ── Hors-tour : les deux ressources épuisées → silence total ────────────
    if reacted and spoken:
        return (
            _goal_block
            + f"\n\n⚔️ ═══ COMBAT — ROUND {rnd} — HORS-TOUR — TOUTES RESSOURCES ÉPUISÉES ═══\n"
            f"C'est le tour de {active}. Tu as déjà utilisé ta réaction ET ta parole ce round.\n"
            "🚫 TU NE PEUX PLUS RIEN FAIRE jusqu'à ton prochain tour.\n"
            "🚫 Interdit : attaquer, lancer un sort, te déplacer, parler, commenter.\n"
            "✅ Exception : si le MJ te demande explicitement un jet (dégâts, attaque, sauvegarde…),\n"
            "   exécute roll_dice immédiatement — cela ne coûte aucune ressource.\n"
            "✅ Sinon, seule réponse autorisée : le mot-clé exact [SILENCE] — rien d'autre."
        )

    # ── Hors-tour : réaction utilisée, parole encore disponible ─────────────
    if reacted and not spoken:
        return (
            _goal_block
            + f"\n\n⚔️ ═══ COMBAT — ROUND {rnd} — HORS-TOUR — RÉACTION UTILISÉE ═══\n"
            f"C'est le tour de {active}. Tu as déjà utilisé ta réaction ce round.\n"
            "\n"
            "✅ Il te reste UNE parole possible — seulement si :\n"
            "  • Tu révèles une information tactique CRITIQUE (danger immédiat, piège)\n"
            "  • Tu réponds à une question directe d'un allié\n"
            "  Sinon →[SILENCE]\n"
            "✅ Si le MJ te demande un jet (dégâts, attaque, sauvegarde…) : exécute roll_dice\n"
            "   immédiatement — cela ne coûte aucune ressource.\n"
            "\n"
            "🚫 INTERDIT : toute action physique, mouvement, sort, commentaire.\n"
            "Si tu parles, une seule phrase (max 10 mots). Après : [SILENCE]."
        )

    # ── Hors-tour : parole utilisée, réaction encore disponible ─────────────
    if spoken and not reacted:
        return (
            _goal_block
            + f"\n\n⚔️ ═══ COMBAT — ROUND {rnd} — HORS-TOUR — PAROLE UTILISÉE ═══\n"
            f"C'est le tour de {active}. Tu as déjà parlé ce round.\n"
            "\n"
            "✅ Il te reste UNE réaction D&D 5e — seulement si un déclencheur mécanique précis se produit :\n"
            "  • Attaque d'opportunité (ennemi quitte ta portée)\n"
            "  • Sort Bouclier (tu es attaqué)\n"
            "  • Riposte ou Pas de côté (si tu possèdes cette capacité)\n"
            "  Sans déclencheur réel → [SILENCE]\n"
            "✅ Si le MJ te demande un jet (dégâts, attaque, sauvegarde…) : exécute roll_dice\n"
            "   immédiatement — cela ne coûte aucune ressource.\n"
            "\n"
            "🚫 INTERDIT : toute action normale, mouvement, parole supplémentaire.\n"
            "Après la réaction (ou si pas de déclencheur) : [SILENCE]."
        )

    # ── Hors-tour : les deux ressources disponibles ──────────────────────────
    return (
        _goal_block
        + f"\n\n⚔️ ═══ COMBAT — ROUND {rnd} — HORS-TOUR ═══\n"
        f"C'est le tour de {active}. Ce n'est PAS ton tour.\n"
        "\n"
        "Tu disposes de DEUX ressources limitées et indépendantes ce round :\n"
        "\n"
        "🔵 RÉACTION (1 par round) — uniquement si un déclencheur mécanique précis se produit :\n"
        "  • Attaque d'opportunité, sort Bouclier, Riposte, Pas de côté…\n"
        "  Sans déclencheur réel → pas de réaction.\n"
        "\n"
        "🟡 PAROLE (1 par round) — uniquement si l'une de ces conditions est remplie :\n"
        "  • Tu révèles une information CRUCIALE que les alliés ne peuvent pas deviner\n"
        "  • Tu réponds à une question directe d'un allié\n"
        "  Une seule phrase, max 10 mots. Le bavardage tactique est interdit.\n"
        "\n"
        "✅ JETS DEMANDÉS PAR LE MJ — toujours autorisés, quelle que soit ta situation :\n"
        "  Si le MJ te demande un jet (dégâts, attaque, sauvegarde, initiative…),\n"
        "  exécute roll_dice immédiatement. Cela ne consomme ni réaction ni parole.\n"
        "\n"
        "🚫 INTERDIT hors-tour, sans exception :\n"
        "  • Se déplacer, attaquer hors réaction, lancer un sort hors réaction\n"
        "  • Action bonus, objet, compétence\n"
        "  • Commenter l'action, donner des conseils, décrire une posture\n"
        "\n"
        "Si aucune condition ne justifie d'agir → réponds [SILENCE].\n"
        "Après chaque ressource utilisée, réponds[SILENCE] pour les tours suivants."
    )