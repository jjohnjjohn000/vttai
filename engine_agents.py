"""
engine_agents.py — Création des agents AutoGen, règles D&D, outils, speaker selector.

Exporte :
  build_regle_outils(combat_mode)  — règles communes à tous les PJ, deux modes :
                                       combat_mode=False → HORS COMBAT (défaut au démarrage)
                                       combat_mode=True  → EN COMBAT   (appelé par _update_agent_combat_prompts)
  build_agents_and_tools()         — crée tous les agents, enregistre les outils, retourne un dict
  combat_speaker_selector()        — sélecteur de speaker déterministe (fonc. standalone)
  make_thinking_wrapper()          — wrapping generate_reply pour bulle de pensée + interruption

Usage dans _update_agent_combat_prompts() :
  from engine_agents import build_regle_outils
  _regle = build_regle_outils(combat_mode=COMBAT_STATE["active"])
  # Remplacer le préfixe de règles dans agent.system_message
"""

import threading as _threading_mod
import types as _types

from llm_config    import build_llm_config, _default_model, StopLLMRequested, _SSL_LOCK
from class_data    import get_combat_prompt as _get_combat_prompt
from app_config    import get_agent_config, get_memories_config
from state_manager import (
    get_scene_prompt, get_active_quests_prompt, get_memories_prompt_compact,
    get_calendar_prompt, get_session_logs_prompt, get_spells_prompt,
    get_inventory_prompt, use_spell_slot, update_hp, add_temp_hp,
    add_item_to_inventory, remove_item_from_inventory, update_currency,
    load_state, get_active_characters, roll_dice as _roll_dice_orig,
)
from combat_tracker import COMBAT_STATE, _is_fully_silenced
from agent_logger   import log_llm_model_used, set_agent_configured_model


# ─── SSL hardening global (anti-segfault OpenSSL multithreading) ──────────────
# Deux causes de segfault OpenSSL en contexte multithread :
#
#  1. session tickets TLS (RFC 5077) : reprise de session via un SSL_CTX partagé
#     → corruption mémoire si deux threads utilisent le même contexte.
#     Fix : OP_NO_TICKET désactive les tickets, forçant une handshake complète.
#
#  2. ssl.create_default_context() appelle ctx.load_default_certs() en interne,
#     qui exécute SSL_CTX_set_default_verify_paths() — non thread-safe sous
#     Python 3.10 / OpenSSL 3.x quand plusieurs threads l'appellent en parallèle.
#     Fix : sérialiser tous les appels via _SSL_CREATE_LOCK.
#
# Le patch est appliqué une seule fois au chargement du module (idempotent).
try:
    import ssl as _ssl_patch

    _SSL_CREATE_LOCK = _threading_mod.Lock()   # sérialiseur création contexte SSL

    _orig_create_default_ctx = _ssl_patch.create_default_context

    def _safe_create_default_ctx(*args, **kwargs):
        with _SSL_CREATE_LOCK:
            ctx = _orig_create_default_ctx(*args, **kwargs)
        ctx.options |= getattr(_ssl_patch, "OP_NO_TICKET", 0)
        return ctx

    _ssl_patch.create_default_context = _safe_create_default_ctx
    del _safe_create_default_ctx   # garder le namespace propre
except Exception:
    pass   # ne jamais bloquer le démarrage pour une optimisation SSL
# ──────────────────────────────────────────────────────────────────────────────


# ─── Règles anti-hallucination communes à tous les joueurs ───────────────────

# Bloc [ACTION] canonique — format unique partagé par les deux modes
_ACTION_FORMAT = (
    "  ⛔ STRICTEMENT UNE SEULE ACTION PAR MESSAGE :\n"
    "    • Ne déclare JAMAIS ton déplacement ET ton attaque en même temps.\n"
    "    • Si tu as plusieurs attaques (Extra Attack / combat à deux armes), déclare-les TOUTES dans le MÊME bloc[ACTION].\n"
    "    • Fais 1 Déplacement OR 1 Action (Attaque/Sort) OR 1 Action Bonus, attends le résultat, puis fais la suite.\n"
    "    • Ne mets JAMAIS [FIN_DE_TOUR] dans le même message qu'un bloc [ACTION].\n"
    "    • Quand tu n'as plus rien à faire, ton action OBLIGATOIRE est de terminer en envoyant UNIQUEMENT : [FIN_DE_TOUR].\n\n"
    "  [ACTION]\n"
    "  Type      : <Action / Action Bonus / Réaction / Mouvement / Fin de tour>\n"
    "  Intention : <ce que ton personnage fait, en une phrase claire>\n"
    "  Règle 5e  : <mécanique exacte : sort + niveau, attaque + bonus + dégâts, etc.>\n"
    "  Cible     : <sur qui ou quoi>\n\n"
)

# Version allégée du bloc [ACTION] pour le mode HORS COMBAT.
# Ne mentionne PAS Fin de tour ni [FIN_DE_TOUR] — ces concepts
# n'existent qu'en combat et confondent les agents sinon.
_ACTION_FORMAT_HORS_COMBAT = (
    "  ⛔ UNE SEULE ACTION PAR MESSAGE :\n"
    "    • Fais 1 Déplacement OU 1 Action OU 1 Action Bonus — jamais plusieurs à la fois.\n\n"
    "  [ACTION]\n"
    "  Type      : <Action / Action Bonus / Réaction / Mouvement>\n"
    "  Intention : <ce que ton personnage fait, en une phrase claire>\n"
    "  Règle 5e  : <mécanique exacte : sort + niveau, compétence + bonus, etc.>\n"
    "  Cible     : <sur qui ou quoi>\n\n"
    "  ⛔ INTERDIT HORS COMBAT : le Type 'Fin de tour' n'existe PAS en dehors du combat.\n"
    "     Ne l'écris jamais hors combat — le système l'ignorera et te corrigera.\n\n"
)

_ACTION_MOUVEMENT_FORMAT = (
    "  [ACTION]\n"
    "  Type      : Mouvement\n"
    "  Intention : <description narrative du déplacement>\n"
    "  Règle 5e  : <N cases (M m)> vers <nord/sud/est/ouest/nord-est…>\n"
    "              OU vers Col X, Lig Y  OU vers <nom d un allié/ennemi>\n"
    "  Cible     : <destination>\n"
)

# Règles immuables communes aux deux modes
_REGLES_COMMUNES = (
    "\n\n═══════════════════════════════════════════"
    "\nRÈGLES ABSOLUES — LIRE ET APPLIQUER À CHAQUE MESSAGE"
    "\n═══════════════════════════════════════════"
    "\n\n⛔ RÈGLE N°1 — ABSOLUE ET SANS EXCEPTION : TU N'ES PAS LE MJ"
    "\nTu joues UNIQUEMENT ton personnage. Tu n'as aucune autorité sur :"
    "\n  • Les PNJ (Van Richten, Ezmerelda, Ismark, Ireena, tout PNJ sans exception)"
    "\n  • Leurs gestes, expressions, paroles, pensées, réactions, déplacements"
    "\n  • L'environnement, les objets, les sons, les odeurs, la météo"
    "\n  • Ce qui se passe dans le monde autour de toi"
    "\nEXEMPLES INTERDITS ABSOLUS :"
    "\n  ✗ 'Van Richten ajuste son appareil...'"
    "\n  ✗ 'Ezmerelda se retourne vers moi...'"
    "\n  ✗ 'Le mur vibre sous l'effet de...'"
    "\n  ✗ 'On entend un grondement au loin...'"
    "\nEXEMPLES CORRECTS :"
    "\n  ✓ 'Je pose une main sur mon symbole sacré.'"
    "\n  ✓ 'Mes yeux scrutent la faille.'"
    "\n  ✓ 'Van Richten, qu'indique l'appareil ?' — et tu t'arrêtes là."
    "\nSi tu décris un PNJ ou l'environnement, ton message sera rejeté.\n"
    "\n▶ CONTRAT SYSTÈME :"
    "\n  1. Le SYSTÈME exécute les dés — tu ne lances rien toi-même."
    "\n  2. Tu reçois un [RÉSULTAT SYSTÈME] avec les valeurs exactes."
    "\n  3. Après un [RÉSULTAT SYSTÈME], tu narres UNIQUEMENT l'EFFORT physique ou mental"
    "\n     de ton personnage (la tension de ses muscles, sa concentration, sa sensation)."
    "\n     TU NE DÉCRIS JAMAIS CE QUE TU TROUVES, DÉCOUVRES OU PERÇOIS DANS L'ENVIRONNEMENT."
    "\n     C'est le MJ seul qui décrit ce qui existe dans le monde."
    "\n     Exemple INTERDIT : 'Je trouve une brique sur pivot dissimulée par la suie.'"
    "\n     Exemple CORRECT  : 'Mes doigts s'arrêtent. Quelque chose cloche ici.'"
    "\n  4. NE JAMAIS appeler roll_dice, use_spell_slot, update_hp, add_temp_hp de ta propre initiative."
    "\n     EXCEPTION : si tu reçois une [DIRECTIVE SYSTÈME — JET] ou [DIRECTIVE SYSTÈME — DÉGÂTS]"
    "\n     avec ton nom, tu DOIS appeler l'outil indiqué IMMÉDIATEMENT, AVANT tout texte."
    "\n  5. NE JAMAIS inventer un résultat différent de celui donné par le système.\n"
    "\n▶ SORTS — RÈGLE ABSOLUE"
    "\nPour lancer un sort, tu DOIS utiliser ce tag exact APRÈS ton roleplay :"
    "\n  [SORT: Nom du sort | Niveau: X | Cible: nom ou description]"
    "\nExemple : [SORT: Soins | Niveau: 3 | Cible: Kaelen]"
    "\nCe tag déclenche automatiquement la boîte de confirmation du MJ."
    "\nTu N'APPELLES JAMAIS use_spell_slot directement — le système s'en charge après confirmation."
    "\nSi tu n'as plus de slot au niveau voulu, le système te le signalera — choisis un niveau inférieur.\n"
    "\n▶ DÉGÂTS REÇUS"
    "\nQuand le MJ annonce que tu prends des dégâts, le SYSTÈME met tes PV à jour."
    "\nTon seul rôle : narrer en 1-2 phrases comment ton personnage encaisse le coup."
    "\nPas de chiffres — décris la douleur, le choc, ta posture. Reste dans l'action.\n"
    "\n▶ PNJ — RÈGLE ABSOLUE EN DEUX PARTIES"
    "\n1. Tu ne DÉCRIS JAMAIS les actions, expressions ou réactions d'un PNJ"
    " (il soupire, il répond, il échange un regard…) — seul le MJ décrit les PNJ."
    "\n2. Tu ne INVENTES JAMAIS leurs paroles. Si tu t'adresses à un PNJ"
    " (ex: 'Gil, de combien de temps aurais-tu besoin ?'), tu ARRÊTES"
    " IMMÉDIATEMENT après la question. Une seule phrase d'adresse maximum."
    " Tu n'élabores PAS, tu n'anticipes PAS leur réponse, tu n'imagines PAS"
    " leurs besoins. Tu poses la question et tu te tais — c'est au MJ de répondre."
    "\n\n▶ MONDE & UNICITÉ — RÈGLE ABSOLUE"
    "\nTu n'existes QUE dans ta tête et ton corps. Le monde extérieur a ses propres règles et appartient au MJ."
    "\nNe décide JAMAIS du résultat de tes actes (succès ou échec), Alexis seul le valide."
    "\nN'invente JAMAIS : un objet, une texture, une odeur, un mécanisme, un passage,"
    "\nune inscription, une créature, une réaction de PNJ — rien de ce qui existe hors"
    "\nde toi. Si ton jet de dés réussit, dis ce que TON CORPS ressent (une anomalie,"
    "\nun doute, une intuition) — PAS ce que tu trouves. Attends qu'Alexis décrive."
    "\nNe répète jamais une question ou idée déjà exprimée — apporte un angle nouveau."
    "\nEnfin, tu connais bien la vallée de Barovie à présent, mais son aberration continue de heurter ta nature."
    "\n\n▶ IDENTITÉ — RÈGLE ABSOLUE"
    "\nTu es UN SEUL personnage. Tu connais ton propre nom."
    "\nINTERDIT ABSOLU :"
    "\n  ✗ Attribuer à toi-même les paroles d'un autre personnage"
    "\n  ✗ Dire 'Excellente question, [TON PROPRE NOM]' — tu ne te félicites pas toi-même"
    "\n  ✗ Parler à la troisième personne de toi-même"
    "\n  ✗ Confondre ce que TU as dit avec ce qu'un autre a dit"
    "\nSi le message précédent vient d'Elara, c'est Elara qui a parlé — pas toi."
    "\nSi le message précédent vient de Kaelen, c'est Kaelen — pas toi."
    "\nLis attentivement le nom de l'auteur de chaque message avant de répondre.\n"
    "\n\n▶ INTERDICTION DE COPIE — RÈGLE ABSOLUE"
    "\nNe reproduis JAMAIS, même partiellement, le contenu du message précédent."
    "\nSi un autre personnage vient de dire ou faire quelque chose, tu ne le répètes pas,"
    "\nne le paraphrases pas, ne le reformules pas. Chaque personnage a sa propre voix,"
    "\nses propres actes."
    "\n\n▶ [SILENCE] — USAGE TRÈS RESTREINT"
    "\n[SILENCE] n'est autorisé QUE si tu es physiquement incapable de parler"
    "\n(inconscient, bâillonné) ou si parler trahirait immédiatement ta position tactique."
    "\nDans TOUS les autres cas, contribue quelque chose — même une seule phrase :"
    "\n  une pensée interne, une réaction émotionnelle, un doute, une question au MJ."
    "\nL'hésitation EST du jeu. [SILENCE] ne l'est presque jamais."
    "\n\n▶ FORMAT & LONGUEUR — RÈGLE ABSOLUE"
    "\nChaque message = 1 réplique dialoguée (1-2 phrases MAX) + 1 bloc [ACTION] si nécessaire."
    "\nINTERDIT ABSOLU :"
    "\n  • Pas de blocs de mise en scène entre parenthèses (Lyra s'approche..., Kaelen observe...)."
    "\n    Tes gestes et postures peuvent figurer dans ta réplique, pas en paragraphe séparé."
    "\n  • Pas de plusieurs questions dans un même message. UNE seule question si tu en poses."
    "\n  • Pas de tirade, pas de monologue, pas de discours en plusieurs paragraphes."
    "\n  • Pas de résumé de ce qu'un autre vient de dire avant de répondre."
    "\nSi tu veux décrire ton attitude : glisse-la dans ta réplique en une incise courte."
    "\nExemple INTERDIT : (Kaelen se tourne lentement.) « Question ? »"
    "\nExemple CORRECT  : « Question ? » — sa voix porte dans le hall."
    "\n═══════════════════════════════════════════\n"
)


def build_regle_outils(combat_mode: bool = False) -> str:
    """
    Retourne le bloc de règles absolues injecté dans le system_message de chaque PJ.

    combat_mode=False  → règles HORS COMBAT (exploration, roleplay, dialogue)
    combat_mode=True   → règles EN COMBAT   (actions obligatoires, initiative, tactique)

    Appelé avec combat_mode=False au démarrage (build_agents_and_tools).
    Appelé avec combat_mode=True  par _update_agent_combat_prompts() dès que
    COMBAT_STATE["active"] passe à True, puis avec False à la fin du combat.
    """
    if combat_mode:
        return _build_regle_en_combat()
    else:
        return _build_regle_hors_combat()


def _build_regle_hors_combat() -> str:
    return (
        _REGLES_COMMUNES
        # ── Section spécifique HORS COMBAT ──────────────────────────────────
        + "\n▶ HORS COMBAT — MODE ACTIF"
        "\nTu joues ton rôle : roleplay, dialogue, exploration, réflexion."
        "\nAgis et déclare tes actions de façon autonome — n'attends pas qu'on te liste les choix."
        "\nNe déclare PAS d'action d'attaque, ne lance PAS de dés, ne prends PAS d'initiative de combat"
        "\nsauf si le MJ l'indique explicitement.\n"
        "\n▶ ACTIONS MÉCANIQUES"
        "\nSi le MJ te demande une action mécanique, ou si tu crois pertinent de le demander, termine ton message par :\n\n"
        + _ACTION_FORMAT_HORS_COMBAT
        + "\n▶ MOUVEMENT SUR LA CARTE — HORS COMBAT"
        "\nLe bloc [ACTION] Type: Mouvement est réservé aux déplacements de 6 cases (9 m / 30 ft) ou plus."
        "\nEn dessous de ce seuil, décris simplement ton mouvement en roleplay — le système l'ignorera."
        "\nExemples INTERDITS (trop courts) : faire un pas vers quelqu'un, se retourner,"
        "\nse rapprocher légèrement pour entendre, ajuster sa position."
        "\nExemples VALIDES (≥ 6 cases) : traverser une salle, rejoindre un autre groupe,"
        "\nquitter une zone, s'éloigner délibérément du groupe.\n\n"
        + _ACTION_MOUVEMENT_FORMAT
    )


def _build_regle_en_combat() -> str:
    return (
        _REGLES_COMMUNES
        # ── Section spécifique EN COMBAT ─────────────────────────────────────
        + "\n▶ COMBAT EN COURS — RÈGLES D'INITIATIVE"
        "\nChaque round tu disposes de :"
        "\n  • 1 Action       (attaque, sort, Dash, Disengage, Ready/Se tenir prêt, Help, Hide, etc.)"
        "\n  • 1 Action Bonus (si ta classe ou un sort t'en donne une)"
        "\n  • 1 Déplacement  (≤ ta vitesse — peut être fractionné avant/après l'action)"
        "\n  • 1 Réaction     (par round, uniquement hors de ton tour)\n"
        "\n▶ SE TENIR PRÊT (READY ACTION) — RÈGLE STRICTE"
        "\nPréparer une action (ex: préparer un sort pour plus tard, préparer une attaque)"
        "\nCOÛTE TON ACTION NORMALE. Cela ne peut JAMAIS être fait avec une Action Bonus."
        "\nLe déclenchement ultérieur de cette action préparée coûtera ta Réaction.\n"
        "\n▶ RÈGLE FONDAMENTALE — UNE ACTION À LA FOIS"
        "\nDéclare UN SEUL bloc [ACTION] par message — jamais plusieurs à la fois."
        "\nINTERDIT ABSOLU : combiner un tag [SORT: ...] avec un bloc [ACTION] dans le même message."
        "\n  ✗ Exemple interdit : [SORT: Sacred Flame | ...] suivi de [ACTION] Type: Mouvement"
        "\n  ✓ Message 1 : roleplay + [SORT: Sacred Flame | Niveau: 0 | Cible: Diable]"
        "\n  ✓ Message 2 (si ressources restantes) : [ACTION] Type: Mouvement …"
        "\nAprès chaque action confirmée, le système t'envoie un [TOUR EN COURS]"
        "\nqui liste tes ressources restantes. Tu déclares alors ta prochaine action."
        "\nQuand tu n'as plus rien à faire, envoie simplement [FIN_DE_TOUR]."
        "\nINTERDIT : écrire plusieurs blocs [ACTION] dans le même message."
        "\nINTERDIT : déclarer une action dont le champ Règle 5e est vide.\n"
        "\n▶ ACTIONS EN COMBAT — FORMAT OBLIGATOIRE\n\n"
        + _ACTION_FORMAT
        + "\n▶ MOUVEMENT SUR LA CARTE — EN COMBAT"
        "\n⛔ RÈGLE FONDAMENTALE : ta distance de déplacement par tour est STRICTEMENT LIMITÉE"
        "\n   à ta vitesse de base (généralement 30 ft = 6 cases). Tu ne peux PAS te déplacer plus loin."
        "\n   • SANS Dash : max 30 ft (6 cases)"
        "\n   • AVEC Dash (coûte ton Action) : max 60 ft (12 cases)"
        "\n   Tout mouvement dépassant cette limite sera AUTOMATIQUEMENT REJETÉ par le système."
        "\n   Consulte toujours le [TOUR EN COURS] pour connaître tes ft restants."
        "\nTout déplacement, même d'une seule case, DOIT être déclaré via [ACTION] Type: Mouvement."
        "\nLe système mettra ton token à jour automatiquement.\n\n"
        "\n⚔️ PORTÉE DE MÊLÉE — VÉRIFIE AVANT D'ATTAQUER"
        "\n   Consulte la section 📏 DISTANCES HÉROS → ENNEMIS dans ton prompt."
        "\n   • Mêlée standard : tu dois être à ≤ 5 ft (1 case adjacente) de l'ennemi. Au-delà → IMPOSSIBLE."
        "\n   • Arme à allonge (Reach) : ≤ 10 ft."
        "\n   • Si l'ennemi est marqué 🏹 (portée distance) → tu dois D'ABORD te DÉPLACER"
        "\n     avec [ACTION] Type: Mouvement pour te mettre à portée, AVANT d'attaquer."
        "\n   ⛔ INTERDICTION : déclarer une attaque corps-à-corps sur un ennemi hors portée.\n\n"
        + _ACTION_MOUVEMENT_FORMAT
    )


# ─── Wrapper tolérant pour roll_dice ─────────────────────────────────────────

def _build_roll_dice_safe():
    """Construit roll_dice_safe avec fallback dice_notation."""
    import re as _re_dice

    def roll_dice_safe(character_name: str,
                       dice_type: str = "",
                       bonus: int = 0,
                       dice_notation: str = "") -> str:
        """
        Lance des dés pour character_name.
        Paramètres :
          character_name : nom du personnage (ex: "Kaelen")
          dice_type      : formule sans bonus (ex: "2d6")
          bonus          : modificateur entier (ex: 5)
        Le paramètre dice_notation="2d6+5" est aussi accepté en fallback.
        """
        if dice_notation and not dice_type:
            _m = _re_dice.match(r"(\d+d\d+)\s*([+-]\s*\d+)?", dice_notation.strip())
            if _m:
                dice_type = _m.group(1)
                bonus = int((_m.group(2) or "0").replace(" ", "")) if _m.group(2) else 0
        if dice_type and ('+' in dice_type or (dice_type.count('-') > 0 and 'd' in dice_type)):
            _m2 = _re_dice.match(r"(\d+d\d+)\s*([+-]\s*\d+)?", dice_type.strip())
            if _m2:
                bonus     = int((_m2.group(2) or "0").replace(" ", "")) if _m2.group(2) else bonus
                dice_type = _m2.group(1)
        if not dice_type:
            return "Erreur : dice_type manquant. Exemple : dice_type='2d6', bonus=5"
        return _roll_dice_orig(character_name, dice_type, int(bonus))

    return roll_dice_safe


def _log_full_prompt(agent, sender, messages):
    import os
    import time
    
    log_dir = "logs/prompts"
    os.makedirs(log_dir, exist_ok=True)
    
    msgs = messages if messages is not None else agent.chat_messages.get(sender, [])
    
    try:
        sys_msg = agent.system_message
    except Exception:
        # Fallback pour récupérer le system_message interne d'AutoGen
        sys_msg = str(getattr(agent, "_oai_system_message", ""))
    
    prompt_text = f"=== SYSTEM MESSAGE ({agent.name}) ===\n{sys_msg}\n\n=== CHAT HISTORY ===\n"
    for m in msgs:
        prompt_text += f"[{m.get('name', m.get('role', 'unknown'))}]: {m.get('content', '')}\n\n"
        
    filename = f"{log_dir}/prompt_{agent.name}_{int(time.time()*1000)}.txt"
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(prompt_text)
    except Exception:
        pass
        
    # Ne garder que les 5 logs les plus récents (globalement)
    all_logs = sorted([os.path.join(log_dir, x) for x in os.listdir(log_dir) if x.endswith(".txt")], 
                      key=os.path.getmtime)
    while len(all_logs) > 5:
        oldest = all_logs.pop(0)
        try:
            os.remove(oldest)
        except Exception:
            pass


def _close_agent_connections(agent):
    """
    Ferme proprement les connexions httpx/httpcore de l'agent après un appel LLM.

    But : empêcher qu'un thread daemon abandonné (après StopLLMRequested) continue
    de lire sur un socket SSL partagé avec le prochain appel — ce qui provoque
    une corruption mémoire et un segfault OpenSSL.

    On itère sur tous les clients httpx connus de l'OpenAIWrapper d'AutoGen et on
    appelle close() pour fermer les connexions persistantes (keep-alive).
    L'opération est entièrement silencieuse : si le client n'existe pas ou que
    close() échoue, on ignore l'erreur.
    """
    try:
        clients = getattr(agent.client, "_clients", None) or {}
        for c in clients.values():
            try:
                c.close()
            except Exception:
                pass
    except Exception:
        pass


# ─── Filtre des messages de tour privés ──────────────────────────────────────

def _filter_turn_private_messages(msgs: list, agent_name: str) -> list:
    """
    Retire du contexte de l'agent les messages de gestion de tour qui
    appartiennent à d'autres personnages.

    Ces messages sont injectés dans le GroupChat (partagé par tous les agents)
    par engine_receive.py, mais leur contenu est exclusivement destiné au
    personnage dont c'est le tour. Les autres agents n'en ont pas besoin et
    les voir pollue leur raisonnement tactique.

    FILTRÉS (quand le destinataire n'est PAS agent_name) :
      • [TOUR EN COURS — AutrePerso] … ressources/directives de tour
      • [MJ → AutrePerso] ❌ …          action refusée / directive privée
      • Tu as encore des actions disponibles. Continue ton tour, AutrePerso.

    CONSERVÉS pour tous les agents :
      • Narrations / roleplay de chaque personnage
      • [RÉSULTAT SYSTÈME — ATTAQUE/SOIN/SORT…] — résultats observables par tous
      • Messages MJ normaux (contexte, description, questions)
      • Tout message adressé à agent_name lui-même
    """
    import re as _re_f
    _n = _re_f.escape(agent_name)

    # Chaque alternative capture un type de message de gestion de tour
    # appartenant à un personnage AUTRE que agent_name.
    # La partie (?!{_n}…) est un lookahead négatif : si le nom qui suit
    # est celui de l'agent courant, le message est conservé.
    _private_re = _re_f.compile(
        # Statut des ressources de tour d'un autre personnage
        r'\[TOUR EN COURS\s*[—\-]\s*(?!' + _n + r'[\s\]])'
        # Refus d'action ou directive interne dirigée vers un autre personnage
        r'|\[MJ\s*[→>]\s*(?!' + _n + r'[\]\s,»])'
        # Message d'auto-continue dirigé vers un autre personnage
        r'|Tu as encore des actions disponibles\. Continue ton tour,\s*(?!' + _n + r'[\s\.,])',
        _re_f.IGNORECASE,
    )
    
    _action_block_re = _re_f.compile(
        r'\s*\[ACTION\].*?(?=\[ACTION\]|\Z)', 
        _re_f.IGNORECASE | _re_f.DOTALL
    )

    filtered_msgs = []
    for m in msgs:
        content = str(m.get("content", ""))
        sender = str(m.get("name", ""))
        
        # 1. Retirer complètement le message s'il correspond aux regex privées (MJ -> autre)
        if _private_re.search(content):
            continue
            
        # 2. Si le message vient d'un AUTRE joueur et contient un bloc [ACTION], on retire le bloc
        # pour ne garder que la narration / roleplay.
        if sender and sender != agent_name and sender not in ("Alexis_Le_MJ", "MJ"):
            content = _action_block_re.sub('', content).strip()
            
        if content:
            new_m = dict(m)
            new_m["content"] = content
            filtered_msgs.append(new_m)
            
    return filtered_msgs


# ─── Thinking wrapper (bulle de pensée + interruption fiable) ─────────────────

def make_thinking_wrapper(agent, name: str, app_ref):
    """
    Deux responsabilités :
      1. Bulle de pensée : set_thinking(True/False) autour de generate_reply.
      2. Interruption fiable : l'appel LLM réel tourne dans un sous-thread daemon.
         Le thread autogen sonde _stop_event toutes les 50 ms.
         Dès que _stop_event est levé, StopLLMRequested est lancé dans le
         thread autogen IMMÉDIATEMENT — même si le sous-thread est encore
         bloqué dans un appel C (HTTP/gRPC). Ce sous-thread finit sa
         requête en tâche de fond (daemon → pas de fuite à l'arrêt de l'app).

    Fix segfault SSL (3 niveaux) :
      A. _SSL_LOCK englobe tout le corps de _llm_call (pas seulement _orig_gr),
         y compris la fermeture des connexions httpx — garantissant qu'aucun
         socket SSL ne reste ouvert quand le verrou est relâché.
      B. _close_agent_connections() est appelé DANS le verrou avant release,
         pour que le prochain thread ne récupère pas une connexion corrompue.
      C. En cas d'interruption (StopLLMRequested côté thread principal),
         on tente également de fermer les connexions depuis le thread principal
         (best-effort) pour accélérer la mort du thread daemon.
    """
    _orig_gr = agent.generate_reply.__func__

    def _wrapped(self_agent, messages=None, sender=None, **kwargs):
        # ── Guard race condition : personnage retiré de la scène mid-session ──
        # Le speaker selector peut avoir choisi cet agent AVANT que
        # _sync_groupchat_agents() ne mette à jour groupchat.agents depuis
        # le thread UI. On vérifie l'état actif ici, au dernier moment,
        # pour bloquer l'appel LLM sans crasher la boucle AutoGen.
        try:
            from state_manager import is_character_active as _is_active
            if not _is_active(name):
                return None   # None = silence ; AutoGen retourne au MJ
        except Exception:
            pass
        # ─────────────────────────────────────────────────────────────────────

        face = app_ref.face_windows.get(name)
        if face:
            try:
                face.set_thinking(True)
            except Exception:
                pass

        app_ref._stop_event.clear()

        result    = [None]
        exc_box   = [None]
        done_evt  = _threading_mod.Event()

        def _llm_call():
            # FIX A+B : _SSL_LOCK englobe tout le corps de _llm_call.
            # On acquiert le verrou en premier, AVANT tout I/O SSL, et on ne
            # le relâche qu'après avoir fermé les connexions httpx.
            # Cela garantit qu'aucun socket ne peut être partagé entre deux
            # threads simultanément, éliminant la race condition OpenSSL.
            with _SSL_LOCK:
                try:
                    try:
                        # Logger les messages filtrés — c'est ce que le LLM reçoit réellement
                        _msgs_to_log = (
                            _filter_turn_private_messages(messages, name)
                            if messages is not None
                            else messages
                        )
                        _log_full_prompt(self_agent, sender, _msgs_to_log)
                    except Exception:
                        pass

                    _usage_before = dict(
                        getattr(self_agent.client, "actual_usage_summary", None) or {}
                    )

                    # Reset du sticky-fallback d'AutoGen
                    try:
                        self_agent.client._last_config_idx = 0
                    except Exception:
                        pass

                    # Filtrer les kwargs internes (__*) que generate_reply() n'accepte pas
                    _safe_kwargs = {k: v for k, v in kwargs.items() if not k.startswith("__")}

                    # ── Cloisonnement des messages de tour ────────────────────
                    # Les messages [TOUR EN COURS — X], [MJ → X] et auto-continue
                    # sont injectés dans le GroupChat partagé par tous les agents.
                    # On les retire ici pour que chaque agent ne voie que les
                    # messages qui le concernent — les résultats d'actions
                    # observables (ATTAQUE, SOIN, SORT…) restent visibles par tous.
                    _msgs_for_llm = (
                        _filter_turn_private_messages(messages, name)
                        if messages is not None
                        else messages
                    )

                    result[0] = _orig_gr(
                        self_agent, messages=_msgs_for_llm, sender=sender, **_safe_kwargs
                    )

                    # Log du modèle ayant effectivement répondu
                    try:
                        _usage_after = getattr(self_agent.client, "actual_usage_summary", None) or {}
                        _new = [
                            m for m in _usage_after
                            if m != "total_cost"
                            and _usage_after[m] != _usage_before.get(m)
                        ]
                        actual = _new[0] if _new else None
                        if actual:
                            _cs = load_state().get("characters", {}).get(name, {})
                            configured = (_cs.get("llm", "")
                                          or get_agent_config(name).get("model", "")
                                          or "")
                            log_llm_model_used(name, actual, configured)
                    except Exception:
                        pass

                except StopLLMRequested:
                    exc_box[0] = StopLLMRequested()
                except BaseException as _e:
                    exc_box[0] = _e
                finally:
                    # FIX B : fermer les connexions httpx DANS le verrou, avant
                    # de le relâcher. Le prochain thread ne récupèrera donc jamais
                    # un socket SSL encore actif depuis un appel précédent.
                    _close_agent_connections(self_agent)
                    done_evt.set()

        llm_thread = _threading_mod.Thread(target=_llm_call, daemon=True,
                                           name=f"llm-call-{name}")
        llm_thread.start()

        while not done_evt.wait(timeout=0.05):
            if app_ref._stop_event.is_set():
                app_ref._stop_event.clear()
                if face:
                    try:
                        face.set_thinking(False)
                    except Exception:
                        pass
                # FIX C : best-effort close depuis le thread principal pour
                # accélérer la mort du thread daemon abandonné. Le daemon
                # peut déjà tenir _SSL_LOCK — on ne bloque pas ici.
                try:
                    _close_agent_connections(self_agent)
                except Exception:
                    pass
                raise StopLLMRequested()

        if face:
            try:
                face.set_thinking(False)
            except Exception:
                pass

        if exc_box[0] is not None:
            _e = exc_box[0]
            _err_str = str(_e)
            _status_code = getattr(_e, "status_code", None)
            from agent_logger import log_llm_end as _log_end

            # 400 BadRequestError — tool_use_failed
            if type(_e).__name__ == "BadRequestError" and "tool_use_failed" in _err_str:
                if not kwargs.get("__is_fallback_retry"):
                    _log_end(name, error="BadRequestError (400): tool_use_failed (Tentative de Récupération)")
                    app_ref.msg_queue.put({"sender": "⚙️ Système", "color": "#ff9800",
                        "text": f"Agent {name} : erreur de formatage (tool_use_failed). Tentative de récupération avec gemini-2.0-flash-exp..."})
                    
                    _old_client = getattr(self_agent, "client", None)
                    if _old_client is not None:
                        try:
                            from llm_config import build_llm_config
                            import autogen
                            # FIX 4 : "gemini-3.1-flash-preview" n'existe pas → 404 garanti.
                            # Remplacé par "gemini-2.0-flash-exp" (modèle réel avec tool use).
                            _fallback_cfg = build_llm_config("gemini-2.0-flash-exp", temperature=0.0)
                            self_agent.client = autogen.OpenAIWrapper(config_list=_fallback_cfg["config_list"])
                            
                            _fallback_notice = (
                                "[RÈGLE SYSTÈME TEMPORAIRE — RÉCUPÉRATION D'ERREUR]\n"
                                "Ta précédente tentative de réponse a échoué car tu as mal formaté "
                                "l'appel d'outil ou as utilisé un outil inexistant (tool_use_failed).\n"
                                "Analyse tes intentions, corrige ton formatage, et réponds de nouveau correctement."
                            )
                            _fallback_messages = list(messages) if messages else []
                            _fallback_messages.append({"role": "system", "content": _fallback_notice, "name": "Systeme"})
                            
                            kwargs_copy = dict(kwargs)
                            kwargs_copy["__is_fallback_retry"] = True
                            
                            # Relance de l'appel via la fonction wrapper
                            return _wrapped(self_agent, messages=_fallback_messages, sender=sender, **kwargs_copy)
                        except Exception as _fe:
                            print(f"[Fallback Error] {_fe}")
                        finally:
                            self_agent.client = _old_client

                # Échec du fallback ou deuxième erreur 400 consécutive
                _log_end(name, error="BadRequestError (400): tool_use_failed (Échec définitif)")
                app_ref.msg_queue.put({"sender": "⚙️ Système", "color": "#cc4422",
                    "text": "Agent " + name + " : demande invalide persistante (tool_use_failed). Réplique ignorée."})
                return "[Erreur systeme: capacite invalide (400).]"

            # 404 NotFoundError — modele introuvable ou sans tool use
            if "404" in _err_str or _status_code == 404:
                # Priorité : llm_session_override > llm > app_config (même logique que _cfg())
                try:
                    _cs = load_state().get("characters", {}).get(name, {})
                    _cs_model = _cs.get("llm_session_override", "") or _cs.get("llm", "")
                except Exception:
                    _cs_model = ""
                _actual_model = _cs_model or get_agent_config(name).get("model", "?")
                _log_end(name, error="404 - modele introuvable ou sans tool use: " + _actual_model)

                # Conseils spécifiques selon le fournisseur
                if _actual_model.startswith("openrouter/"):
                    _slug = _actual_model[len("openrouter/"):]
                    _tips = [
                        "  - Slug incorrect : verifiez sur openrouter.ai/models (ex: deepseek/deepseek-chat)",
                        "  - Le modele ne supporte pas les function calls (tool use)",
                        "  - Modele desactive ou retire de l'offre OpenRouter",
                        "  - Syntaxe attendue : openrouter/<provider>/<model-id>",
                    ]
                    try:
                        _cs2 = load_state().get("characters", {}).get(name, {})
                        if _cs2.get("llm_session_override", ""):
                            _source = "UI session override"
                        elif _cs2.get("llm", ""):
                            _source = "campaign_state.json (llm)"
                        else:
                            _source = "app_config.json"
                    except Exception:
                        _source = "app_config.json"
                    _txt404 = [
                        "Modele introuvable ou sans support tool use pour " + name + ".",
                        "Modele tente    : " + _actual_model,
                        "Slug OpenRouter : " + _slug,
                        "Source config   : " + _source,
                        "",
                        "Causes possibles :",
                    ] + _tips
                else:
                    try:
                        _cs2 = load_state().get("characters", {}).get(name, {})
                        if _cs2.get("llm_session_override", ""):
                            _source = "UI session override"
                        elif _cs2.get("llm", ""):
                            _source = "campaign_state.json (llm)"
                        else:
                            _source = "app_config.json"
                    except Exception:
                        _source = "app_config.json"
                    _txt404 = [
                        "Modele introuvable ou sans support tool use pour " + name + ".",
                        "Modele configure : " + _actual_model,
                        "Source config    : " + _source,
                        "",
                        "Causes possibles :",
                        "  - Nom de modele incorrect (verifiez sur openrouter.ai/models)",
                        "  - Le modele ne supporte pas les function calls",
                        "  - Prefixe openrouter/ manquant dans campaign_state.json",
                    ]
                app_ref.msg_queue.put({"sender": "OpenRouter 404", "color": "#F44336",
                    "text": chr(10).join(_txt404)})
                return "[" + name + " est silencieux - modele OpenRouter incompatible (404).]"

            # 402 Payment Required — credits insuffisants
            if "402" in _err_str or _status_code == 402:
                _log_end(name, error="402 - credits insuffisants")
                try:
                    from llm_config import fetch_openrouter_key_status, format_openrouter_status
                    _kdata = fetch_openrouter_key_status()
                    _kstatus = format_openrouter_status(_kdata) if _kdata else "(impossible de recuperer le solde)"
                except Exception:
                    _kstatus = "(impossible de recuperer le solde)"
                _txt402 = ["Credits insuffisants pour " + name + ".",
                           _kstatus, "",
                           "Ajoutez des credits : https://openrouter.ai/settings/credits"]
                app_ref.msg_queue.put({"sender": "OpenRouter 402", "color": "#F44336",
                    "text": chr(10).join(_txt402)})
                return "[" + name + " est silencieux - credits OpenRouter insuffisants (402).]"

            raise _e
        return result[0]

    return _types.MethodType(_wrapped, agent)


# ─── Speaker selector ─────────────────────────────────────────────────────────

def combat_speaker_selector(last_speaker, groupchat):
    """
    Sélecteur de speaker entièrement déterministe — ne retourne JAMAIS "auto".

    Stratégie basée sur l'intention du MJ (par ordre de priorité) :
      1. Noms explicites : le MJ mentionne un ou plusieurs PJ par nom
         → seuls ces PJ répondent, dans l'ordre d'apparition dans le message.
      2. Question de groupe : pas de nom mentionné mais présence d'un '?'
         ou d'un marqueur de groupe
         → tous les PJ actifs répondent, chacun une seule fois.
      3. Narration / pas de question : pas de nom, pas de '?'
         → un seul PJ réagit (rotation simple).
      4. Un PJ vient de parler → retour au MJ.
    """
    import re as _re_sel
    import random

    def _pick_least_recent(choices):
        if not choices: return None
        if len(choices) == 1: return choices[0]
        recent = []
        for msg in reversed(groupchat.messages):
            name = msg.get("name")
            if name in _ALL_PLAYERS and name not in recent:
                recent.append(name)
            if name == "Alexis_Le_MJ":
                content = str(msg.get("content", "")).strip()
                m = _re_sel.match(r'^\[(\w+),\s*s\'adressant au groupe\]', content, _re_sel.IGNORECASE)
                if m:
                    rname = m.group(1)
                    if rname in _ALL_PLAYERS and rname not in recent:
                        recent.append(rname)
        never_spoken = [c for c in choices if c.name not in recent]
        if never_spoken: return random.choice(never_spoken)
        for name in reversed(recent):
            cand = next((c for c in choices if c.name == name), None)
            if cand: return cand
        return random.choice(choices)

    _ALL_PLAYERS = ["Kaelen", "Elara", "Thorne", "Lyra"]
    _GROUP_MARKERS = ("tout le monde", "vous tous", "le groupe", "chacun",
                      "l'équipe", "vous avez", "que faites-vous",
                      "vos réactions", "qu'en pensez-vous")

    _players_in_gc = [a for a in groupchat.agents if a.name in _ALL_PLAYERS]
    _player_names_in_gc = {a.name for a in _players_in_gc}

    def _eligible_agents():
        if not COMBAT_STATE["active"]:
            return list(groupchat.agents)
        else:
            candidates = [
                a for a in groupchat.agents
                if not _is_fully_silenced(a.name) or a.name not in _ALL_PLAYERS
            ]
            if not candidates:
                candidates = [a for a in groupchat.agents if a.name == "Alexis_Le_MJ"]
            return candidates

    eligible = _eligible_agents()
    if not eligible:
        mj = next((a for a in groupchat.agents if a.name == "Alexis_Le_MJ"), None)
        return mj or groupchat.agents[0]

    eligible_names  = {a.name for a in eligible}
    last_name       = last_speaker.name if last_speaker else ""
    mj_agent_ref    = next((a for a in eligible if a.name == "Alexis_Le_MJ"), None)

    def _find_last_mj_msg():
        for i in range(len(groupchat.messages) - 1, -1, -1):
            if groupchat.messages[i].get("name") == "Alexis_Le_MJ":
                return i, str(groupchat.messages[i].get("content", ""))
        return None, ""

    def _responded_since(mj_idx):
        responded = set()
        for msg in groupchat.messages[mj_idx + 1:]:
            if msg.get("name") in _ALL_PLAYERS:
                responded.add(msg.get("name"))
        return responded

    def _next_pending(target_list, responded):
        for name in target_list:
            if name not in responded and name in eligible_names:
                return next((a for a in eligible if a.name == name), None)
        return None

    last_mj_idx, last_mj_content = _find_last_mj_msg()

    if last_mj_idx is not None:
        _stripped = last_mj_content.strip()

        # [PAROLE_SPONTANEE] → un seul PJ parle puis retour au MJ
        if _stripped == "[PAROLE_SPONTANEE]":
            _ps_responded = _responded_since(last_mj_idx)
            if _ps_responded:
                return mj_agent_ref or eligible[0]
            players_eligible = [a for a in eligible if a.name in _ALL_PLAYERS]
            if players_eligible:
                return _pick_least_recent(players_eligible)

        # Message relayé du groupe → le PJ vient virtuellement de parler, retour au MJ
        _relay_match = _re_sel.match(r'^\[(\w+),\s*s\'adressant au groupe\]', _stripped, _re_sel.IGNORECASE)
        if _relay_match:
            rname = _relay_match.group(1)
            if rname in _ALL_PLAYERS:
                if mj_agent_ref:
                    return mj_agent_ref

        # Résultat d'outil : MJ reprend la main
        _is_tool_result = (
            _stripped.startswith("[RÉSULTAT SYSTÈME")
            or _stripped.startswith("Error: Function")
            or "Function" in _stripped and "not found" in _stripped
        )
        if _is_tool_result:
            return mj_agent_ref or eligible[0]

        content_low = last_mj_content.lower()

        # Détection réponse PNJ → re-router vers le dernier PJ questionneur
        try:
            _sel_state = load_state()
            _PNJ_NAMES_SEL = list({
                n["name"]
                for src in ("npcs", "group_npcs")
                for n in _sel_state.get(src, [])
                if n.get("name")
            })
        except Exception:
            _PNJ_NAMES_SEL = []

        _pnj_reply_re = _re_sel.compile(
            r'(?:^|\n)\s*(?:' + '|'.join(_re_sel.escape(n) for n in _PNJ_NAMES_SEL) + r')\s*(?::|—|-)',
            _re_sel.IGNORECASE
        )
        if _pnj_reply_re.search(last_mj_content):
            _already_resp_pnj = _responded_since(last_mj_idx)
            _last_pc_before_mj = None
            for _rmsg in reversed(groupchat.messages[:last_mj_idx]):
                if _rmsg.get("name") in _ALL_PLAYERS:
                    _last_pc_before_mj = _rmsg.get("name")
                    break
            if (_last_pc_before_mj
                    and _last_pc_before_mj in eligible_names
                    and _last_pc_before_mj not in _already_resp_pnj):
                return next(
                    (a for a in eligible if a.name == _last_pc_before_mj),
                    mj_agent_ref or eligible[0]
                )
            if mj_agent_ref:
                return mj_agent_ref

        # Cas 1 — noms explicites dans le message du MJ
        mentioned = [
            name for name in _ALL_PLAYERS
            if name.lower() in content_low
            and name in _player_names_in_gc
        ]

        # Cas 2 — question de groupe
        if not mentioned:
            is_group_question = (
                "?" in last_mj_content
                or any(m in content_low for m in _GROUP_MARKERS)
            )
            if is_group_question:
                mentioned = [n for n in _ALL_PLAYERS if n in _player_names_in_gc]

        if mentioned:
            responded = _responded_since(last_mj_idx)
            pending   = _next_pending(mentioned, responded)
            if pending:
                return pending
            if mj_agent_ref:
                return mj_agent_ref

    # Un PJ vient de parler → MJ
    if last_name in _ALL_PLAYERS:
        if mj_agent_ref:
            return mj_agent_ref

    # MJ vient de parler sans cibler → Cas 3 : un seul PJ réagit (rotation)
    if last_name == "Alexis_Le_MJ":
        players_eligible = [a for a in eligible if a.name in _ALL_PLAYERS]
        if players_eligible:
            responded = _responded_since(last_mj_idx) if last_mj_idx is not None else set()
            not_yet = [a for a in players_eligible if a.name not in responded]
            if not_yet:
                return _pick_least_recent(not_yet)
            return _pick_least_recent(players_eligible)
        return mj_agent_ref or eligible[0]

    # Fallback ultime : choix parmi les PJ éligibles qui ne viennent pas de parler
    players_eligible = [a for a in eligible if a.name in _ALL_PLAYERS]
    if players_eligible:
        candidates = [a for a in players_eligible if a.name != last_name]
        return _pick_least_recent(candidates if candidates else players_eligible)

    return eligible[0]


# ─── build_agents_and_tools ──────────────────────────────────────────────────

def build_agents_and_tools(autogen, cfg_fn, app) -> dict:
    """
    Crée tous les agents AutoGen et enregistre les outils.

    Paramètres :
      autogen : module autogen importé (lazy import depuis run_autogen)
      cfg_fn  : callable(char_name) → llm_config dict
      app     : instance DnDApp (pour CHAR_COLORS et face_windows)

    Retourne un dict :
      {
        "mj":             mj_agent,
        "kaelen":         kaelen_agent,
        "elara":          elara_agent,
        "thorne":         thorne_agent,
        "lyra":           lyra_agent,
        "agents":         {"Kaelen": ..., "Elara": ..., "Thorne": ..., "Lyra": ...},
        "all_player":     idem,
      }
    """
    _regle = build_regle_outils()
    _mem_min = get_memories_config().get("compact_importance_min", 2)

    # ── MJ ───────────────────────────────────────────────────────────────────
    import types as _t
    mj_agent = autogen.UserProxyAgent(
        name="Alexis_Le_MJ",
        system_message="Tu es Alexis, le Maître du Jeu suprême. Tu as l'autorité absolue sur le monde et les règles de D&D 5e.",
        human_input_mode="ALWAYS",
        code_execution_config=False,
    )

    def gui_get_human_input(self_agent, prompt: str, **kwargs) -> str:
        if app._pending_combat_trigger is not None:
            trigger = app._pending_combat_trigger
            app._pending_combat_trigger = None
            return trigger
        # Auto-exécution du roll_dice quand un [DIRECTIVE SYSTÈME — JET] est en attente.
        # Retourner "" dit à autogen "pas de saisie humaine, exécute l'outil directement".
        if getattr(app, "_pending_auto_roll", False):
            app._pending_auto_roll = False
            return ""
        app.msg_queue.put({"sender": "Système", "text": "En attente de votre action (Texte ou 🎤)...", "color": "#888888"})
        app._set_waiting_for_mj(True)
        result = app.wait_for_input()
        app._set_waiting_for_mj(False)
        return result

    mj_agent.get_human_input = _t.MethodType(gui_get_human_input, mj_agent)

    # ── Kaelen ───────────────────────────────────────────────────────────────
    kaelen_agent = autogen.AssistantAgent(
        name="Kaelen",
        system_message=(
            _regle +
            "Tu es Kaelen, un Paladin Humain de niveau 11, hanté par un serment passé.\n"
            "PERSONNALITÉ : Tu es économe en mots, fier et grave. Tes préoccupations sont toujours liées "
            "à l'honneur, aux serments, à qui mérite protection et à ce qui constitue une cause juste. "
            "Quand tu interviens, c'est pour évaluer la valeur morale de la mission ou jurer ta protection. "
            "Tu n'es pas curieux des mécaniques — tu veux savoir SI ça vaut le coup de mourir pour ça.\n"
            + _get_combat_prompt("paladin", "Devotion", 11) + "\n"
            "FORMAT SMITE OBLIGATOIRE — n'utilise JAMAIS un bloc [ACTION] séparé pour le smite :\n"
            "  [ACTION]\n"
            "  Type      : Action — Attaque × 2 (Extra Attack)\n"
            "  Intention : Frapper deux fois ; Divine Smite sur attaque 1 si touche\n"
            "  Règle 5e  : Attaque 1 : corps-à-corps +11, 2d6+8 | Divine Smite niv.2 si touche\n"
            "              Attaque 2 : corps-à-corps +11, 2d6+8\n"
            "  Cible     : [la cible]\n"
            "Ne déclare PAS le smite comme Action Bonus séparé — il doit toujours être dans le même bloc que l'attaque.\n"
        ),
        llm_config=cfg_fn("Kaelen"),
    )

    # ── Elara ────────────────────────────────────────────────────────────────
    elara_agent = autogen.AssistantAgent(
        name="Elara",
        system_message=(
            _regle +
            "Tu es Elara, une Magicienne de niveau 11, froide et méthodique.\n"
            "PERSONNALITÉ : Tu analyses, tu quantifies, tu cherches les failles logiques. Tes questions portent "
            "toujours sur la mécanique précise des choses : comment fonctionne la magie du phare, quelle est "
            "la source du pouvoir, y a-t-il des données concrètes, des artefacts, des textes. "
            "Tu t'ennuies des généralités et tu coupes court aux discours flous. "
            "Tu ne poses JAMAIS une question qu'Elara a déjà posée, ni une que quelqu'un d'autre vient de poser.\n"
            + _get_combat_prompt("wizard", "", 11) + "\n"
        ),
        llm_config=cfg_fn("Elara"),
    )

    # ── Thorne ───────────────────────────────────────────────────────────────
    thorne_agent = autogen.AssistantAgent(
        name="Thorne",
        system_message=(
            _regle +
            "Tu es Thorne, un Voleur (Assassin) Tieffelin de niveau 11, cynique et pragmatique.\n"
            "PERSONNALITÉ : Tu vois le monde en termes de risques, de profits et de qui manipule qui. "
            "Tes questions portent sur les motivations cachées, les pièges potentiels, ce qu'on ne te dit pas, "
            "et ce que rapporte concrètement la mission. Tu es sarcastique et tu n'accordes ta confiance à personne. "
            "Tu parles avec un accent québécois. "
            "Tu ne poses JAMAIS une question qu'un autre personnage vient de poser — tu trouves ça embarrassant.\n"
            "INTERDICTION ABSOLUE POUR THORNE : Tu n'utilises JAMAIS [SILENCE]. "
            "Tu as toujours quelque chose à dire — un commentaire sarcastique, une méfiance, "
            "une suspicion, une remarque cynique en québécois. "
            "Si tu n'as rien à dire sur le fond, tu exprimes ce que Thorne ressent : "
            "l'ennui, l'agacement, le malaise, la méfiance. "
            "Un Tieffelin cynique ne se tait jamais quand il peut piquer.\n"
            "COMPÉTENCES — RÈGLE ABSOLUE : Tu es un Voleur Assassin, PAS un mage ni un érudit. "
            "Tu ne fais JAMAIS d'analyse magique, arcanique ou planaire. "
            "Tu ne parles JAMAIS d'énergie corrompue, de pression planaire, de résidu magique, "
            "de failles dimensionnelles ou de phénomènes surnaturels en termes techniques. "
            "Ces sujets appartiennent à Elara (magie) et Lyra (divin) — tu les laisses parler. "
            "Toi, tu réagis en Voleur : qu'est-ce qui est dangereux pour ta peau, "
            "qui tire les ficelles, est-ce un piège, qu'est-ce qu'on peut ramasser, "
            "comment sortir vivant de là. Tes observations sont tactiques et pragmatiques, "
            "jamais magiques ni théoriques.\n"
            "FORMAT ATTAQUE OBLIGATOIRE — Tu te bats avec deux armes (Extra Attack) :\n"
            "  [ACTION]\n"
            "  Type      : Action — Attaque × 2\n"
            "  Intention : Frapper deux fois avec mes lames\n"
            "  Règle 5e  : Attaque 1 : corps-à-corps +11, 1d6+5 | Attaque 2 : corps-à-corps +11, 1d6+5\n"
            "  Cible     : [la cible]\n"
            "Déclare toujours tes deux attaques dans le MÊME bloc [ACTION].\n"
            + _get_combat_prompt("rogue", "Assassin", 11) + "\n"
        ),
        llm_config=cfg_fn("Thorne"),
    )

    # ── Lyra ────────────────────────────────────────────────────────────────
    lyra_agent = autogen.AssistantAgent(
        name="Lyra",
        system_message=(
            _regle +
            "Tu es Lyra, une Clerc (Domaine de la Vie) Demi-Elfe de niveau 11, bienveillante et implacable.\n"
            "PERSONNALITÉ : Tu penses d'abord aux innocents qui souffrent, à la dimension spirituelle et divine "
            "des événements, et à ce que les dieux pourraient vouloir ici. Tu poses des questions sur les victimes, "
            "la souffrance des gens ordinaires, les signes divins, et ce que signifie moralement la situation. "
            "Tu ne poses JAMAIS une question qu'un autre personnage vient de poser — chaque voix doit être unique.\n"
            + _get_combat_prompt("cleric", "Life", 11) + "\n"
        ),
        llm_config=cfg_fn("Lyra"),
    )

    # ── Enregistrement du modele configure dans agent_logger ─────────────────
    for _n in ["Kaelen", "Elara", "Thorne", "Lyra"]:
        try:
            set_agent_configured_model(_n, get_agent_config(_n).get("model", ""))
        except Exception:
            pass

    # ── Enregistrement des outils ─────────────────────────────────────────────
    roll_dice_safe = _build_roll_dice_safe()

    _update_hp_desc = (
        "Mettre à jour les PV d'un personnage. "
        "Utilise un entier NÉGATIF pour des dégâts (ex: -7), POSITIF pour un soin (ex: +12). "
        "Paramètres : character_name (str, ex: 'Thorne'), amount (int). "
        "À appeler dès que le MJ annonce que tu prends des dégâts ou reçois un soin."
    )
    for _upd_agent in [kaelen_agent, elara_agent, thorne_agent, lyra_agent]:
        autogen.agentchat.register_function(
            update_hp, caller=_upd_agent, executor=mj_agent,
            name="update_hp", description=_update_hp_desc,
        )

    _add_temp_hp_desc = (
        "Ajouter des PV temporaires à un personnage (sorts, capacités raciales, etc.). "
        "Règle D&D 5e : les PV temporaires ne se cumulent pas — seul le plus grand total est conservé. "
        "Ils absorbent les dégâts AVANT les PV réels. Les soins ne les restaurent pas. "
        "Paramètres : character_name (str, ex: 'Lyra'), amount (int positif, ex: 8). "
        "À appeler dès que le MJ confirme que tu gagnes des PV temporaires."
    )
    for _upd_agent in [kaelen_agent, elara_agent, thorne_agent, lyra_agent]:
        autogen.agentchat.register_function(
            add_temp_hp, caller=_upd_agent, executor=mj_agent,
            name="add_temp_hp", description=_add_temp_hp_desc,
        )

    _add_item_desc = (
        "Ajouter un objet à l'inventaire du groupe (ou incrémenter sa quantité). "
        "Paramètres : name (str), quantity (int, défaut 1), "
        "category (str : arme/armure/potion/objet_magique/munition/outil/divers), "
        "rarity (str : commun/peu_commun/rare/très_rare/légendaire/artéfact), "
        "description (str), notes (str). "
        "À appeler quand le MJ confirme que le groupe trouve ou reçoit un objet."
    )
    _remove_item_desc = (
        "Retirer une quantité d'un objet de l'inventaire du groupe. "
        "Paramètres : name (str), quantity (int, défaut 1). "
        "À appeler quand le groupe utilise, perd ou vend un objet."
    )
    _currency_desc = (
        "Mettre à jour la monnaie du groupe (positif = gain, négatif = dépense). "
        "Paramètres : gold (int), silver (int), copper (int), platinum (int), electrum (int). "
        "Exemple gain : gold=50, silver=10. Exemple dépense : gold=-30. "
        "À appeler quand le MJ annonce un gain ou une dépense de monnaie."
    )
    for _inv_agent in [kaelen_agent, elara_agent, thorne_agent, lyra_agent]:
        autogen.agentchat.register_function(
            add_item_to_inventory, caller=_inv_agent, executor=mj_agent,
            name="add_item_to_inventory", description=_add_item_desc,
        )
        autogen.agentchat.register_function(
            remove_item_from_inventory, caller=_inv_agent, executor=mj_agent,
            name="remove_item_from_inventory", description=_remove_item_desc,
        )
        autogen.agentchat.register_function(
            update_currency, caller=_inv_agent, executor=mj_agent,
            name="update_currency", description=_currency_desc,
        )

    # Kaelen et Thorne : dés + sorts
    for agent in [kaelen_agent, thorne_agent]:
        autogen.agentchat.register_function(
            roll_dice_safe, caller=agent, executor=mj_agent,
            name="roll_dice",
            description=(
                "Lancer des dés pour un personnage. "
                "Paramètres OBLIGATOIRES séparés : "
                "character_name (str, ex: 'Kaelen'), "
                "dice_type (str, formule SANS bonus, ex: '2d6' ou '1d20'), "
                "bonus (int, modificateur, ex: 5 ou -1). "
                "Exemple correct : character_name='Kaelen', dice_type='2d6', bonus=8. "
                "NE PAS utiliser dice_notation ni combiner le bonus dans dice_type."
            )
        )
        autogen.agentchat.register_function(
            use_spell_slot, caller=agent, executor=mj_agent,
            name="use_spell_slot",
            description="Consommer un slot de sort (1-9). À appeler UNIQUEMENT si le MJ te le demande explicitement."
        )

    # Elara : sorts + dés
    autogen.agentchat.register_function(
        roll_dice_safe, caller=elara_agent, executor=mj_agent,
        name="roll_dice",
        description=(
            "Lancer des dés pour un personnage. "
            "Paramètres OBLIGATOIRES séparés : "
            "character_name (str, ex: 'Elara'), "
            "dice_type (str, formule SANS bonus, ex: '8d6' ou '1d20'), "
            "bonus (int, modificateur, ex: 10 ou 0). "
            "Exemple correct : character_name='Elara', dice_type='8d6', bonus=0."
        )
    )
    autogen.agentchat.register_function(
        use_spell_slot, caller=elara_agent, executor=mj_agent,
        name="use_spell_slot",
        description="Consommer un slot de sort (1-9). Paramètres : character_name (str), level (str, ex: '3')."
    )

    # Lyra : sorts + soins + dés
    autogen.agentchat.register_function(
        roll_dice_safe, caller=lyra_agent, executor=mj_agent,
        name="roll_dice",
        description=(
            "Lancer des dés pour un personnage. "
            "Paramètres OBLIGATOIRES séparés : "
            "character_name (str, ex: 'Lyra'), "
            "dice_type (str, formule SANS bonus, ex: '1d8'), "
            "bonus (int, modificateur, ex: 7). "
            "Exemple correct : character_name='Lyra', dice_type='1d8', bonus=7."
        )
    )
    autogen.agentchat.register_function(
        use_spell_slot, caller=lyra_agent, executor=mj_agent,
        name="use_spell_slot",
        description="Consommer un slot de sort (1-9). Paramètres : character_name (str), level (str, ex: '3')."
    )

    # ── Thinking wrappers ─────────────────────────────────────────────────────
    agents_dict = {
        "Kaelen": kaelen_agent,
        "Elara":  elara_agent,
        "Thorne": thorne_agent,
        "Lyra":   lyra_agent,
    }
    for _think_name, _think_agent in agents_dict.items():
        _think_agent.generate_reply = make_thinking_wrapper(
            _think_agent, _think_name, app
        )

    return {
        "mj":         mj_agent,
        "kaelen":     kaelen_agent,
        "elara":      elara_agent,
        "thorne":     thorne_agent,
        "lyra":       lyra_agent,
        "agents":     agents_dict,
        "all_player": agents_dict,
    }