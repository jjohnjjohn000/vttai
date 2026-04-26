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
from agent_logger   import log_llm_model_used, set_agent_configured_model, log_agent_prompt, log_agent_response


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


# ─── Patch AutoGen : guard message_retrieval contre None choices/content ──────
# Cause : certains modèles retournent une réponse avec choices=None, ou un message
# dont le contenu est None (réponse tool-call only sans texte).
# AutoGen ne protège pas ce cas → TypeError: 'NoneType' object is not iterable.
def _patch_autogen_message_retrieval():
    try:
        import autogen.oai.client as _oai_client
        _OpenAIClient = _oai_client.OpenAIClient
        _orig_mr = _OpenAIClient.message_retrieval

        def _safe_message_retrieval(self, response):
            choices = getattr(response, "choices", None)
            if not choices:
                return [""]
            results = []
            for choice in choices:
                msg = getattr(choice, "message", None)
                if msg is None:
                    results.append("")
                    continue
                tool_calls = getattr(msg, "tool_calls", None)
                func_call  = getattr(msg, "function_call", None)
                if tool_calls is not None or func_call is not None:
                    try:
                        results.extend(_orig_mr(self, response))
                    except TypeError:
                        results.append("")
                    return results
                results.append(msg.content if msg.content is not None else "")
            return results

        _OpenAIClient.message_retrieval = _safe_message_retrieval
        print("[engine_agents] Patch AutoGen message_retrieval: OK")
    except Exception as _pe:
        print(f"[engine_agents] Patch AutoGen message_retrieval: SKIPPED ({_pe})")

_patch_autogen_message_retrieval()
# ──────────────────────────────────────────────────────────────────────────────


# ─── Règles anti-hallucination communes à tous les joueurs ───────────────────

# Bloc [ACTION] canonique — format unique partagé
_ACTION_FORMAT = (
    "[RÈGLES DU BLOC ACTION]\n"
    "  • Déclare UNE SEULE action par message.\n"
    "  • Ne combine JAMAIS un Déplacement et une Attaque/Sort dans le même bloc.\n"
    "  • 💡 COMBO (Sort + Attaque) : Si tu lances un sort en Action Bonus, fais-le en DEUX messages. (Message 1 : Sort [Action Bonus] -> Attends le MJ -> Message 2 : Attaque [Action]).\n"
    "  • Quand tu n'as plus d'action envisageable, tu peux terminer ton tour avec [ACTION] Type: Fin de tour.\n\n"
    "  [ACTION]\n"
    "  Type      : <Action / Action Bonus / Réaction / Mouvement / Fin de tour>\n"
    "  Intention : <Ce que ton personnage fait, en une phrase claire>\n"
    "  Règle 5e  : <Mécanique exacte : sort + niveau, attaque + bonus + dégâts, etc.>\n"
    "  Cible     : <Sur qui ou quoi>\n\n"
)

# Version allégée du bloc [ACTION] pour le mode HORS COMBAT.
_ACTION_FORMAT_HORS_COMBAT = (
    "  [RÈGLES DU BLOC ACTION (HORS COMBAT)]\n"
    "  • Fais 1 Déplacement OU 1 Action OU 1 Action Bonus — jamais plusieurs à la fois.\n"
    "  [ACTION]\n"
    "  Type      : <Action / Action Bonus / Réaction / Mouvement>\n"
    "  Intention : <Ce que ton personnage fait, en une phrase claire>\n"
    "  Règle 5e  : <Mécanique exacte : sort + niveau, compétence + bonus, etc.>\n"
    "  Cible     : <Sur qui ou quoi>\n\n"
)


_ACTION_MOUVEMENT_FORMAT = (
    "  [ACTION]\n"
    "  Type      : Mouvement\n"
    "  Intention : <Description narrative du déplacement>\n"
    "  Règle 5e  : <N cases (M m)> vers <nord/sud/est/ouest/nord-est…>\n"
    "              OU vers Col X, Lig Y  OU vers <nom d un allié/ennemi>\n"
    "  Cible     : <Destination>\n"
)

# Règles claires, aérées et sans surplus cognitif
_REGLES_COMMUNES = (
    "\n\n═══════════════════════════════════════════"
    "\n📜 CONTRAT DE JEU — LIS ATTENTIVEMENT"
    "\n═══════════════════════════════════════════"
    "\n\n1. TON RÔLE (TU N'ES PAS LE MJ)"
    "\n• Joue UNIQUEMENT ton personnage. Tu connais ton nom, ne parle pas à la 3ème personne."
    "\n• Ne décris JAMAIS les actions, paroles ou réactions des PNJ (Van Richten, Ireena, etc.)."
    "\n• Ne décris JAMAIS l'environnement, les objets découverts ou les conséquences de tes actes."
    "\n• Si tu t'adresses à un PNJ, pose ta question en une phrase et arrête-toi net. Le MJ répondra."
    "\n\n2. NARRATION ET SYSTÈME"
    "\n• Le système (MJ) lance les dés et gère les PV. N'invente jamais un résultat de ton côté."
    "\n• Après un[RÉSULTAT SYSTÈME] ou des dégâts reçus, narre UNIQUEMENT ta réaction physique ou mentale (douleur, effort, doute) en 1 ou 2 phrases. Pas de chiffres dans ton roleplay."
    "\n• INTERDICTION DE COPIE : Ne paraphrase jamais le message d'un autre joueur. Sois unique."
    "\n\n3. MÉCANIQUES ET SORTS"
    "\n• Pour lancer un sort ou attaquer, utilise TOUJOURS un bloc [ACTION]."
    "\n• ⚠️ ANTI-SPAM (RÈGLE ABSOLUE) : Ne lance JAMAIS un sort (détection, buff, etc.) s'il a déjà été lancé récemment et est toujours actif. Le MJ gère les compétences passivement (Perception passive, Investigation passive, etc.) — ne demande PAS de jet toi-même sauf si le MJ t'y invite."
    "\n• ⚠️ UPCAST OBLIGATOIRE : Tu DOIS respecter les 'Sorts dispos' affichés dans ton [TOUR EN COURS]. Si tu n'as plus d'emplacement pour le niveau de base d'un sort et que tu veux lancer quand même, tu DOIS le lancer à un niveau supérieur en l'écrivant explicitement (ex: 'Règle 5e: Shield of Faith niv. 3')."
    "\n• N'appelle pas les outils (update_hp, roll_dice) de ta propre initiative, sauf si une [DIRECTIVE SYSTÈME] te le demande explicitement."
    "\n\n4. FORMAT DE RÉPONSE"
    "\n• Structure : 1 réplique dialoguée (avec ton attitude incrustée dedans) + 1 bloc [ACTION] UNIQUEMENT si le MJ le demande ou si tu as une action physique délibérée à déclarer."
    "\n• N'inclus JAMAIS les en-têtes d'instructions comme[RÈGLES DU BLOC ACTION] ou [RÈGLES DU BLOC ACTION (HORS COMBAT)] dans ta réponse."
    "\n• Sois concis : pas de monologues, pas de descriptions entre parenthèses en paragraphe séparé."
    "\n• N'utilise [SILENCE] que si tu es physiquement incapable de parler. Sinon, donne au moins une pensée ou une courte réaction."
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
        "\n▶ COMPÉTENCES PASSIVES — NE JETTE PAS DE DÉS INUTILEMENT"
        "\nLe MJ utilise tes SCORES PASSIFS (10 + ton bonus) pour Perception, Investigation et Perspicacité."
        "\nCela signifie que le MJ détecte automatiquement les menaces et détails que ton personnage"
        "\nremarquerait naturellement — TU N'AS PAS BESOIN de demander un jet pour ça."
        "\n⛔ N'utilise un bloc [ACTION] avec un jet de compétence QUE si :"
        "\n  • Tu as une RAISON NARRATIVE FORTE et SPÉCIFIQUE (indice concret, événement déclencheur)."
        "\n  • Tu fais quelque chose d'ACTIF et DÉLIBÉRÉ (fouiller un meuble, crocheter une serrure,"
        "\n    interroger quelqu'un avec insistance, escalader un mur…)."
        "\n⛔ INTERDIT : jets de Perception / Investigation / Arcanes « au cas où », par prudence"
        "\n    ou pour « surveiller les alentours ». Le MJ gère ça passivement.\n"
        "\n▶ ACTIONS MÉCANIQUES"
        "\nTu peux librement déclarer un bloc [ACTION] pour toute action non offensive :"
        "\n  • Fouiller, crocheter, escalader, soigner, lancer un sort utilitaire, se déplacer…"
        "\n  • ⛔ INTERDIT sans l'accord du MJ : attaques, sorts offensifs, actions hostiles."
        "\nTermine ton message par :\n\n"
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
        "\n▶ RÈGLE FONDAMENTALE — UNE ACTION À LA FOIS"
        "\nDéclare UN SEUL bloc[ACTION] par message — jamais plusieurs à la fois."
        "\nAprès chaque action confirmée, le système t'envoie un [TOUR EN COURS]"
        "\nqui liste tes ressources restantes. Tu déclares alors ta prochaine action."
        "\n⚠️ RAPPEL TRÈS IMPORTANT : Quand tu n'as plus rien à faire ou que tes ressources sont épuisées, TU DOIS terminer ton tour en envoyant [ACTION] de type: Fin de tour.\n"
        "\n▶ ACTIONS EN COMBAT — FORMAT OBLIGATOIRE\n\n"
        + _ACTION_FORMAT
        + "\n⚔️ PORTÉE DE MÊLÉE — VÉRIFIE AVANT D'ATTAQUER"
        "\n   Consulte la section 📏 DISTANCES HÉROS → ENNEMIS dans ton prompt."
        "\n   • Mêlée standard : tu dois être à ≤ 5 ft (1 case adjacente). Au-delà → IMPOSSIBLE."
        "\n   • Si l'ennemi est hors de portée, tu dois D'ABORD te DÉPLACER avec un bloc Mouvement AVANT d'attaquer.\n\n"
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
    
    msgs = messages if messages is not None else agent.chat_messages.get(sender,[])
    
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
      •[TOUR EN COURS — AutrePerso] … ressources/directives de tour
      • [MJ → AutrePerso] ❌ …          action refusée / directive privée
      • Tu as encore des actions disponibles. Continue ton tour, AutrePerso.
      •[RÉSULTAT SYSTÈME — * IMPOSSIBLE — AutrePerso] … correction de sort/action/mouvement

    CONSERVÉS pour tous les agents :
      • Narrations / roleplay de chaque personnage
      • [RÉSULTAT SYSTÈME — ATTAQUE/SOIN/SORT…] — résultats observables par tous
        (Ces messages n'ont PAS de 3e segment « — NomAgent » dans leur préfixe.)
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
        r'\[TOUR EN COURS\s*[—\-](?!\s*' + _n + r'[\s\]])'
        # Refus d'action ou directive interne dirigée vers un autre personnage
        r'|\[MJ\s*[→>](?!\s*' + _n + r'[\]\s,»])'
        # Message d'auto-continue dirigé vers un autre personnage
        r'|Tu as encore des actions disponibles\. Continue ton tour,(?!\s*' + _n + r'[\s\.,])'
        # Corrections privées de sort/action/mouvement adressées à un autre personnage.
        # Format produit par engine_receive.py :[RÉSULTAT SYSTÈME — TYPE IMPOSSIBLE — NomAgent]
        # Les résultats observables (ATTAQUE RÉSOLUE, SOIN, SAUVEGARDE…) n'ont PAS ce 3e segment
        # et ne sont donc PAS filtrés par cette regex.
        r'|\[RÉSULTAT SYSTÈME\s*—[^—\]\n]+—(?!\s*' + _n + r'[\s\]])',
        _re_f.IGNORECASE,
    )
    
    _action_block_re = _re_f.compile(
        r'\s*(?:\[ACTION\])?\s*(?:Type|Action|Type d\'action)\s*:.*?(?=\n\n|\[ACTION\]|</thought>|</think>|\Z)', 
        _re_f.IGNORECASE | _re_f.DOTALL
    )
    
    _thought_re = _re_f.compile(r'<(thought|think)>.*?</\1>\s*', _re_f.IGNORECASE | _re_f.DOTALL)

    filtered_msgs = []
    for m in msgs:
        content = str(m.get("content", ""))
        sender = str(m.get("name", ""))
        
        # 0. Retirer les blocs de pensée
        content = _thought_re.sub('', content).strip()
        
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

    # ── SUPPRESSION DE TOUT L'HISTORIQUE EN COMBAT ──
    try:
        from combat_tracker import COMBAT_STATE
        if COMBAT_STATE.get("active"):
            # En combat, l'historique complet, les événements tactiques récents, ou
            # même les messages des autres agents ont tendance à embrouiller l'agent.
            # On ne conserve STRICTEMENT QUE le tout dernier message (la directive
            # de tour du MJ "C'est à toi Kaelen, déclare ton action").
            if filtered_msgs:
                return [filtered_msgs[-1]]
            return []
    except Exception:
        pass
            
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
        # ── Guard pause session : attendre la fin de la pause ─────────────────
        # IMPORTANT : ne PAS retourner None ici — ça tuerait le run AutoGen
        # ("TERMINATING RUN: No reply generated") et bloquerait tout le combat.
        # On attend plutôt la reprise, comme gui_get_human_input.
        import time as _pause_time
        while getattr(app_ref, '_session_paused', False):
            _pause_time.sleep(0.3)
            if getattr(app_ref, '_stop_event', None) and app_ref._stop_event.is_set():
                raise StopLLMRequested()

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
                        _actual_messages = messages if messages is not None else self_agent.chat_messages.get(sender, [])
                        _msgs_to_log = _filter_turn_private_messages(_actual_messages, name)
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
                    _actual_messages = messages if messages is not None else self_agent.chat_messages.get(sender, [])
                    _msgs_for_llm = _filter_turn_private_messages(_actual_messages, name)

                    # Console log du prompt (hors combat uniquement)
                    try:
                        log_agent_prompt(name, getattr(self_agent, 'system_message', ''), _msgs_for_llm)
                    except Exception:
                        pass

                    result[0] = _orig_gr(
                        self_agent, messages=_msgs_for_llm, sender=sender, **_safe_kwargs
                    )

                    # Console log de la réponse (hors combat uniquement)
                    try:
                        log_agent_response(name, result[0])
                    except Exception:
                        pass
                    # Guard: None = pas de réponse (convention AutoGen : (False, None))
                    if result[0] is None:
                        result[0] = (False, None)

                    # Log du modèle ayant effectivement répondu
                    try:
                        _usage_after = getattr(self_agent.client, "actual_usage_summary", None) or {}
                        _new =[
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
                    # Priorité de récupération :
                    #   EN COMBAT    → gemini-3.1-flash-lite-preview (modèle combat obligatoire)
                    #   HORS COMBAT  → modèle configuré dans la fiche du personnage
                    #                  (llm_session_override > llm > app_config, même logique que _cfg())
                    if COMBAT_STATE.get("active"):
                        from app_config import get_combat_config as _gcc
                        _recovery_model = _gcc().get("model", "gemini-3.1-flash-lite-preview")
                    else:
                        try:
                            _cs_rec = load_state().get("characters", {}).get(name, {})
                            _recovery_model = (
                                _cs_rec.get("llm_session_override", "")
                                or _cs_rec.get("llm", "")
                                or get_agent_config(name).get("model", "")
                                or _default_model
                            )
                        except Exception:
                            _recovery_model = _default_model
                    _log_end(name, error=f"BadRequestError (400): tool_use_failed (Tentative de Récupération → {_recovery_model})")
                    app_ref.msg_queue.put({"sender": "⚙️ Système", "color": "#ff9800",
                        "text": f"Agent {name} : erreur de formatage (tool_use_failed). Tentative de récupération avec {_recovery_model}..."})
                    
                    _old_client = getattr(self_agent, "client", None)
                    if _old_client is not None:
                        try:
                            from llm_config import build_llm_config
                            import autogen
                            _fallback_cfg = build_llm_config(_recovery_model, temperature=0.0)
                            self_agent.client = autogen.OpenAIWrapper(config_list=_fallback_cfg["config_list"])
                            
                            _fallback_notice = (
                                "[RÈGLE SYSTÈME TEMPORAIRE — RÉCUPÉRATION D'ERREUR]\n"
                                "Ta précédente tentative de réponse a échoué car tu as mal formaté "
                                "l'appel d'outil ou as utilisé un outil inexistant (tool_use_failed).\n"
                                "Analyse tes intentions, corrige ton formatage, et réponds de nouveau correctement."
                            )
                            _fallback_messages = list(messages) if messages else[]
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
                    _tips =[
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
                    _txt404 =[
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
                    _txt404 =[
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
                _txt402 =["Credits insuffisants pour " + name + ".",
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
        recent =[]
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
        never_spoken =[c for c in choices if c.name not in recent]
        if never_spoken: return random.choice(never_spoken)
        for name in reversed(recent):
            cand = next((c for c in choices if c.name == name), None)
            if cand: return cand
        return random.choice(choices)

    _ALL_PLAYERS =["Kaelen", "Elara", "Thorne", "Lyra"]
    _GROUP_MARKERS = ("tout le monde", "vous tous", "le groupe", "chacun",
                      "l'équipe", "vous avez", "que faites-vous",
                      "vos réactions", "qu'en pensez-vous")

    _players_in_gc =[a for a in groupchat.agents if a.name in _ALL_PLAYERS]
    _player_names_in_gc = {a.name for a in _players_in_gc}

    def _eligible_agents():
        if not COMBAT_STATE["active"]:
            return list(groupchat.agents)
        else:
            _active = COMBAT_STATE.get("active_combatant")
            candidates =[
                a for a in groupchat.agents
                if not _is_fully_silenced(a.name) or a.name not in _ALL_PLAYERS or a.name == _active
            ]
            if not candidates:
                candidates =[a for a in groupchat.agents if a.name == "Alexis_Le_MJ"]
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
            players_eligible =[a for a in eligible if a.name in _ALL_PLAYERS]
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
                for n in _sel_state.get(src,[])
                if n.get("name")
            })
        except Exception:
            _PNJ_NAMES_SEL =[]

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
        mentioned =[
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
                mentioned =[n for n in _ALL_PLAYERS if n in _player_names_in_gc]

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
    # Exception : si c'est le tour d'un PNJ/ennemi, les héros ne parlent PAS
    # spontanément — ils ne peuvent répondre que si le MJ les nomme explicitement.
    if last_name == "Alexis_Le_MJ":
        _active_cbt = COMBAT_STATE.get("active_combatant")
        _active_is_npc = (
            COMBAT_STATE.get("active")
            and _active_cbt is not None
            and _active_cbt not in _ALL_PLAYERS
        )
        if _active_is_npc:
            # Tour PNJ : MJ reprend directement, les héros n'interviennent pas
            return mj_agent_ref or eligible[0]
        players_eligible =[a for a in eligible if a.name in _ALL_PLAYERS]
        if players_eligible:
            responded = _responded_since(last_mj_idx) if last_mj_idx is not None else set()
            not_yet =[a for a in players_eligible if a.name not in responded]
            if not_yet:
                return _pick_least_recent(not_yet)
            return _pick_least_recent(players_eligible)
        return mj_agent_ref or eligible[0]

    # Fallback ultime : choix parmi les PJ éligibles qui ne viennent pas de parler
    players_eligible =[a for a in eligible if a.name in _ALL_PLAYERS]
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
        import time as _time

        # ── 0. GARDE PAUSE SESSION ──────────────────────────────────────────
        # Si la session est en pause, on attend la reprise AVANT de consommer
        # tout trigger automatique. Sinon les triggers relancent le flow.
        while getattr(app, '_session_paused', False):
            _time.sleep(0.3)
            # Si le thread autogen est interrompu pendant l'attente
            if getattr(app, '_stop_event', None) and app._stop_event.is_set():
                from llm_config import StopLLMRequested
                raise StopLLMRequested()

        # ── 1. Trigger de tour pré-calculé (début de tour normal) ────────────
        if app._pending_combat_trigger is not None:
            trigger = app._pending_combat_trigger
            app._pending_combat_trigger = None
            return trigger

        # ── 2. Re-trigger automatique après [RÉSULTAT SYSTÈME — * IMPOSSIBLE] ─
        # NOTE : AutoGen passe à get_human_input un prompt générique du type
        # "Provide feedback to Alexis_Le_MJ. Press enter..." — pas le contenu
        # du GroupChat. Il est donc impossible de détecter IMPOSSIBLE ici via
        # le texte de `prompt`.
        #
        # La détection est faite côté Tk dans append_message (chat_mixin.py) :
        # quand un message [RÉSULTAT SYSTÈME — * IMPOSSIBLE — NomAgent] est
        # affiché, append_message stocke (char_name, instruction) dans
        # app._pending_impossible_retrigger.
        #
        # On attend jusqu'à ~300 ms pour laisser process_queue traiter le
        # message (il tourne toutes les 100 ms).
        _retrig = getattr(app, "_pending_impossible_retrigger", None)
        if _retrig is None:
            # Petite attente pour laisser le thread Tk traiter le msg_queue
            for _ in range(4):
                _time.sleep(0.08)
                _retrig = getattr(app, "_pending_impossible_retrigger", None)
                if _retrig is not None:
                    break

        if _retrig is not None:
            app._pending_impossible_retrigger = None
            _char_name, _instruction = _retrig
            # ⚠️ Format [TOUR DE COMBAT — NOM] et non [RÉSULTAT SYSTÈME…] :
            # custom_speaker_selection route tout [RÉSULTAT SYSTÈME vers mj_agent
            # (ligne ~807) → boucle infinie. Avec [TOUR DE COMBAT — NOM], le
            # sélecteur détecte le nom du joueur dans content_low (~ligne 849)
            # et route directement vers l'agent concerné.
            return (
                f"[TOUR DE COMBAT — {_char_name.upper()}]\n"
                f"C'est à nouveau le tour de {_char_name}. "
                f"Ton action précédente a été annulée par le système.\n"
                f"[INSTRUCTION]\n{_instruction}\n"
                f"{_char_name}, déclare maintenant une nouvelle action valide "
                f"(sort différent, tour de magie, attaque physique, ou Fin de tour)."
            )

        # ── 3. Auto-exécution du roll_dice ([DIRECTIVE SYSTÈME — JET] en attente)
        if getattr(app, "_pending_auto_roll", False):
            app._pending_auto_roll = False
            return ""

        # ── 4. Attente MJ humain (cas normal hors combat / hors IMPOSSIBLE) ───
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
            "⚔️ RÈGLE DES CHÂTIMENTS ET ATTAQUES — LIS ATTENTIVEMENT :\n"
            "1. CHÂTIMENT DIVIN (Divine Smite - Capacité de classe) :\n"
            "   Ce N'EST PAS une action ni un sort. Il s'ajoute simplement à une attaque réussie.\n"
            "   Pour l'utiliser, ajoute '| Divine Smite niv.X si touche' à la fin de la ligne Règle 5e de ton attaque.\n"
            "   NE FAIS JAMAIS de bloc [ACTION] séparé pour ça.\n"
            "2. SORTS DE CHÂTIMENT (Wrathful Smite, Thunderous Smite, Faveur Divine) :\n"
            "   Ce SONT des sorts qui coûtent une ACTION BONUS.\n"
            "   Tu DOIS les lancer dans un bloc [ACTION] Type: Action Bonus SÉPARÉ, puis attendre le résultat avant d'attaquer au message suivant.\n"
            "3. EXTRA ATTACK (Attaque Supplémentaire) :\n"
            "   Tu as droit à 2 attaques par Action. Tu DOIS les déclarer SÉPARÉMENT.\n"
            "   Fais ta première attaque ([ACTION] Type: Action), attends le résultat du MJ, "
            "   puis fais ta seconde attaque dans un NOUVEAU message ([ACTION] Type: Extra Attack).\n"
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
            #"⚠️ ANTI-ACHARNEMENT : Si tu viens de faire un jet d'Investigation, d'Arcanes, ou de lancer 'Détection de la Magie' dans la scène actuelle, NE LE REFAIS PLUS. Fais confiance à tes données actuelles et passe à autre chose (réfléchis, discute, ou attends les actions des autres).\n"
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
            "FORMAT ATTAQUE OBLIGATOIRE — Tu te bats avec deux armes, tu n'es pas obligé de faire tes deux attaques:\n"
            "  Tu dois déclarer chaque attaque SÉPARÉMENT dans des messages distincts.\n"
            "  Message 1 (Première attaque) :\n"
            "    [ACTION]\n"
            "    Type      : Action\n"
            "    Intention : Frapper avec ma première lame\n"
            "    Règle 5e  : Attaque : corps-à-corps +11, 1d6+5\n"
            "    Cible     : [la cible]\n"
            "  Message 2 (après avoir reçu le résultat du MJ) :\n"
            "    [ACTION]\n"
            "    Type      : Action Bonus\n"
            "    Intention : Frapper avec ma seconde lame\n"
            "    Règle 5e  : Attaque : corps-à-corps +11, 1d6+5\n"
            "    Cible     : [la cible]\n"
            "  Ne déclare JAMAIS tes deux attaques dans le même bloc !\n"
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
    for _upd_agent in[kaelen_agent, elara_agent, thorne_agent, lyra_agent]:
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
    for _upd_agent in[kaelen_agent, elara_agent, thorne_agent, lyra_agent]:
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
    for _inv_agent in[kaelen_agent, elara_agent, thorne_agent, lyra_agent]:
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