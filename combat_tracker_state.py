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
}


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

    Deux ressources hors-tour INDÉPENDANTES par round :
      • Réaction   — déclenchée mécaniquement (Attaque d'opportunité, Bouclier…)
      • Parole     — une phrase courte si l'information est VRAIMENT importante
    Chacune ne vaut que si elle apporte une information cruciale ou répond
    à une question directe. Le bavardage tactique est interdit.
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

    # ── Tour actif ───────────────────────────────────────────────────────────
    if agent_name == active:
        # ── Snapshot emplacements de sorts ───────────────────────────────────
        _slot_block = ""
        if _sm_load_state is not None:
            try:
                _st    = _sm_load_state()
                _slots = (
                    _st.get("characters", {})
                       .get(agent_name, {})
                       .get("spell_slots", {})
                )
                if _slots:
                    _slot_lines =[]
                    for _lvl in sorted(_slots.keys(), key=lambda x: int(x)):
                        _n = _slots[_lvl]
                        _icon = "✅" if _n > 0 else "❌"
                        _slot_lines.append(f"    Niv.{_lvl} : {_icon} {_n} slot(s)")
                    _slot_block = (
                        "  📖 EMPLACEMENTS DE SORTS (état actuel) :\n"
                        + "\n".join(_slot_lines) + "\n"
                        "  ⚠ Ne tente JAMAIS un sort dont le slot est à ❌ 0 —\n"
                        "    si tu veux le lancer, choisis un niveau avec ✅ slots dispo (upcast),\n"
                        "    ou opte pour un tour de magie / action physique.\n"
                    )
            except Exception:
                pass

        # Rappels spécifiques par personnage
        _char_hints = {
            "Kaelen": (
                "  🗡 EXTRA ATTACK : ton Action te donne 2 attaques, mais tu DOIS les déclarer SÉPARÉMENT (1 seule attaque par message).\n"
                "  ✦ CHÂTIMENT DIVIN : Ajoute simplement '| Divine Smite niv.X si touche' dans la Règle 5e de ton attaque.\n"
                "  ◈ SORTS (Action Bonus) : Si tu veux lancer un sort de châtiment ou Faveur Divine, fais-le dans un bloc[ACTION] séparé AVANT ton attaque.\n"
            ),
            "Elara": (
                "  🔮 ACTION : choisis le sort le plus efficace pour la situation.\n"
                "  ◈ CONCENTRATION : vérifie si un sort actif tourne déjà avant d'en lancer un nouveau.\n"
                "  ◈ ACTION BONUS : sort bonus action si disponible (ex. Misty Step pour te repositionner).\n"
            ),
            "Thorne": (
                "  🗡 ACTION : 1 attaque + SNEAK ATTACK (8d6) si avantage ou allié adjacent.\n"
                "  ◈ CUNNING ACTION : utilise ton Action Bonus pour [Foncer] (Dash), te Désengager ou te Cacher.\n"
                "  ⚡ Priorité : Hide → avantage assuré sur la prochaine attaque + Sneak Attack garanti.\n"
            ),
            "Lyra": (
                "  ✦ ACTION : sort de soin/attaque ou Esquive si en danger.\n"
                "  ◈ ARME SPIRITUELLE : si invoquée, attaque bonus gratuite chaque tour (ne pas oublier !).\n"
                "  ◈ CHANNEL DIVINITY disponible si non utilisé ce repos court.\n"
            ),
        }
        hint = _char_hints.get(agent_name, "")
        
        # Récupération de la vitesse de base du personnage (30 ft par défaut)
        _speed = 30
        try:
            from engine_mechanics import CHAR_MECHANICS
            _speed = CHAR_MECHANICS.get(agent_name, {}).get("speed", 30)
        except ImportError:
            pass

        # Récupération des ressources restantes
        _tr = COMBAT_STATE.get("turn_res", {}).get(agent_name, {})
        _act_str = "✅ Disponible" if _tr.get("action", True) else "❌ Épuisée"
        _bon_str = "✅ Disponible" if _tr.get("bonus", True) else "❌ Épuisée"
        _mv_rem  = _tr.get("movement_ft", _speed)
        _mv_str  = f"✅ {_mv_rem} ft restants" if _mv_rem > 0 else "❌ Épuisé"
        _re_str  = "❌ Épuisée" if agent_name in COMBAT_STATE.get("reactions_used", set()) else "✅ Disponible"

        _res_block = (
            f"  [RESSOURCES ACTUELLES RESTANTES POUR {agent_name}]\n"
            f"  • Action       : {_act_str}\n"
            f"  • Action Bonus : {_bon_str}\n"
            f"  • Déplacement  : {_mv_str} (Vitesse de base: {_speed} ft)\n"
            f"  • Réaction     : {_re_str}\n\n"
        )

        return (
            _goal_block
            + f"\n\n⚔️ ═══ COMBAT — ROUND {rnd} — C'EST TON TOUR ═══\n"
            "Utilise TON ÉCONOMIE D'ACTION COMPLÈTE de façon AUTONOME :\n\n"
            f"{_res_block}"
            f"{hint}"
            f"{_slot_block}"
            f"  🏃 MOUVEMENT — LIMITE STRICTE :\n"
            f"    ⛔ Tu as {_speed} ft ({_speed//5} cases) de déplacement par tour. PAS PLUS.\n"
            f"    • Si tu veux aller plus loin, utilise ton Action pour Foncer (Dash) → {_speed*2} ft max.\n"
            f"    • Les ft restants sont indiqués plus haut — ne les dépasse JAMAIS.\n"
            f"    • Exemple INTERDIT : déclarer 95 ft ou 50 ft avec {_speed} ft de vitesse.\n"
            f"    • Exemple VALIDE  : déclarer {_speed} ft ({_speed//5} cases) vers le nord.\n\n"
            "  ⚔️ AVANT D'ATTAQUER EN MÊLÉE :\n"
            "    Lis la section 📏 DISTANCES ci-dessous. Si l'ennemi est marqué 🏹 (portée distance),\n"
            "    tu DOIS te déplacer pour te mettre à portée (≤5ft) AVANT d'attaquer.\n"
            "    ⛔ Attaque corps-à-corps hors portée = REJETÉE automatiquement.\n\n"
            "RÈGLE ABSOLUE — NARRATION D'ABORD :\n"
            "1. Narre d'abord ce que ton personnage fait, dit ou ressent (roleplay).\n"
            "2. Puis déclare chaque action mécanique dans un bloc [ACTION].\n"
            "3. N'appelle AUCUN outil directement (update_hp, roll_dice, etc.) — "
            "le MJ les exécute lui-même après validation dans le chat.\n"
            "4. Ne modifie jamais tes PV, slots ou état toi-même.\n"
            "5. Pour les sorts : vérifie la liste 📋 ci-dessus et indique un niveau ✅ disponible.\n\n"
            "⚠ Ne laisse JAMAIS ton Action inutilisée — au minimum : Esquive (Dodge) ou Aide (Help).\n"
            "⚠ N'attends PAS que le MJ te liste tes options — c'est TON tour, décide.\n\n"
            "FORMAT STRICT — DÉCLARE UNE SEULE ACTION À LA FOIS :\n"
            "Termine ton message par UN SEUL bloc [ACTION]. N'essaie PAS de faire toute ton économie d'action d'un coup.\n\n"
            "  [ACTION]\n"
            "  Type      : <Action / Extra Attack / Action Bonus / Réaction / Mouvement / Fin de tour>\n"
            "  Intention : <ce que ton personnage fait, en une phrase claire>\n"
            "  Règle 5e  : <mécanique exacte : attaque + bonus + dégâts, sort + niveau, etc.>\n"
            "  Cible     : <sur qui ou quoi> (Optionnel)\n\n"
            "Si tu as Attaque Supplémentaire (Extra Attack) ou te bats à deux armes :\n"
            "  Tu DOIS déclarer ta première attaque, attendre le résultat du MJ, puis déclarer ta seconde attaque dans un NOUVEAU message.\n"
            "  Ne groupe JAMAIS plusieurs attaques dans le même bloc.\n\n"
            "Joue avec intensité et concision."
        )

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
            "  Sinon → [SILENCE]\n"
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
        "Après chaque ressource utilisée, réponds [SILENCE] pour les tours suivants."
    )