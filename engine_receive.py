"""
engine_receive.py — Construction de patched_receive pour GroupChatManager.

Exporte :
  EngineContext  — dataclass portant tout l'état mutable partagé entre les closures
  build_patched_receive(ctx) — retourne la fonction patched_receive

patched_receive est injectée sur le GroupChatManager via remplacement de classe
(approche atomique / safe gRPC). Elle intercèpte tous les messages autogen et :
  - filtre les violations (copie, silence, PNJ, hors-tour, outil parasite)
  - intercepte les sorts [SORT:...] et blocs [ACTION] pour validation MJ
  - gère le flow attaque (Phase 1 jet → Phase 2 smite → Phase 3 dégâts)
  - parse les directives MJ et injecte [DIRECTIVE SYSTÈME]
  - journalise les messages narratifs
  - met à jour les mémoires contextuelles
"""

import re as _re
import threading as _threading

from dataclasses import dataclass, field
from typing      import Any

from llm_config    import build_llm_config, _default_model
from app_config    import get_agent_config
from state_manager import (
    load_state, get_active_characters,
    use_spell_slot, update_hp,
)
from combat_tracker    import COMBAT_STATE, _is_fully_silenced
from chat_log_writer   import ChatLogWriter, strip_mechanical_blocks
from agent_logger      import log_tts_start
from engine_mechanics  import (
    CHAR_MECHANICS, split_into_subactions,
    roll_attack_only, roll_damage_only, execute_action_mechanics,
)
from engine_spell_mj   import (
    ACTION_PATTERN, SORT_PATTERN, DAMAGE_PATTERN, PC_NAME_RE,
    DIRECTIVE_PREFILTER, PARSER_SYSTEM,
    get_prepared_spell_names, extract_spell_name_llm, is_spell_prepared,
    can_ritual_cast, build_pnj_patterns, parse_mj_directives,
    validate_bonus_action_rule, validate_cast_time_in_combat
)


# ─── EngineContext ────────────────────────────────────────────────────────────

def _slots_superieurs_disponibles(name: str, niveau_demande: int) -> list:
    """
    Retourne la liste triée des niveaux de slot > niveau_demande
    encore disponibles pour le personnage 'name'.
    Utilisé pour proposer un upcast quand le slot demandé est épuisé.
    """
    try:
        _st    = load_state()
        _slots = _st.get("characters", {}).get(name, {}).get("spell_slots", {})
        return sorted(
            int(lvl) for lvl, nb in _slots.items()
            if int(lvl) > niveau_demande and int(nb) > 0
        )
    except Exception:
        return []

@dataclass
class EngineContext:
    """Tout l'état mutable partagé entre les closures de patched_receive."""
    app: Any                           # DnDApp instance
    chat_log: ChatLogWriter
    player_names: list                 # ["Kaelen","Elara","Thorne","Lyra"]
    spell_casters: list                # joueurs ayant des spell_slots

    # PNJ
    pnj_names: list
    pnj_patterns: dict                 # retourné par build_pnj_patterns()

    # État mutable
    pending_smite: dict          = field(default_factory=dict)
    pending_damage_narrators: set= field(default_factory=set)
    pending_skill_narrators: set = field(default_factory=set)
    last_player_messages: dict   = field(default_factory=dict)
    copy_strikes: dict           = field(default_factory=dict)
    tool_refusal_strikes: dict   = field(default_factory=dict)
    silence_strikes: dict        = field(default_factory=dict)

    # Événements sort (un seul actif à la fois)
    spell_confirm_event:  Any = field(default_factory=_threading.Event)
    spell_confirm_result: dict= field(default_factory=dict)


# ─── build_patched_receive ────────────────────────────────────────────────────

def build_patched_receive(ctx: EngineContext, groupchat_ref: list):
    """
    Construit patched_receive fermée sur ctx et groupchat_ref (liste à 1 élément
    pour permettre la référence tardive après création du GroupChat).

    Retourne : la fonction patched_receive(self_mgr, message, sender, ...).
    """
    _app             = ctx.app
    _chat_log        = ctx.chat_log
    PLAYER_NAMES     = ctx.player_names
    SPELL_CASTERS    = ctx.spell_casters
    PNJ_NAMES        = ctx.pnj_names
    _pnj             = ctx.pnj_patterns
    _pnj_pattern     = _pnj["pnj_pattern"]
    _pnj_narrative_re     = _pnj["pnj_narrative_re"]
    _pnj_narrative_inv_re = _pnj["pnj_narrative_inv_re"]
    _pnj_dialogue_re      = _pnj["pnj_dialogue_re"]
    _CM              = CHAR_MECHANICS

    # Lazy groupchat access
    def _gc():
        return groupchat_ref[0] if groupchat_ref else None

    # ── Helpers TTS ────────────────────────────────────────────────────────────

    def _strip_stars(text: str) -> str:
        if not text:
            return text
        import re as _re_strip
        text = text.replace("*", "")
        # Supprimer les marqueurs mécaniques qui n'ont rien à faire dans le chat
        text = _re_strip.sub(r"\[FIN_DE_TOUR\]", "", text)
        text = _re_strip.sub(r"\[SILENCE\]",     "", text)
        # Effondrer les lignes blanches multiples en une seule
        text = _re_strip.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _tts_clean(text: str) -> str:
        if not text:
            return text
        import re as _re_tts
        text = text.replace("\n\n", ". ")
        text = text.replace("\n", ", ")
        text = _re_tts.sub(r',\s*\.', '.', text)
        text = _re_tts.sub(r'\.\s*,', '.', text)
        text = _re_tts.sub(r',\s*,', ',', text)
        text = _re_tts.sub(r'\s{2,}', ' ', text)
        return text.strip()

    def _split_sentences(text: str) -> list:
        import re as _re_s
        if not text or len(text) < 40:
            return [text] if text else []
        _ABBREVS = r"(?:M|Mme|Dr|Prof|St|Ste|Mr|Jr|Sr|vol|p|pp|art|no|No|fig|cf|vs|env|hab|av|apr|J\.-C|etc)\."
        protected = _re_s.sub(_ABBREVS, lambda m: m.group().replace(".", "\x00"), text)
        parts = _re_s.split(r'(?<=[.!?;])\s+(?=[A-ZÀÂÄÉÈÊËÎÏÔÙÛÜÇ"«\u2019])', protected)
        parts = [p.replace("\x00", ".").strip() for p in parts if p.strip()]
        merged = []
        buf = ""
        for p in parts:
            buf = (buf + " " + p).strip() if buf else p
            if len(buf) >= 18:
                merged.append(buf)
                buf = ""
        if buf:
            if merged:
                merged[-1] = (merged[-1] + " " + buf).strip()
            else:
                merged.append(buf)
        return merged if merged else [text]

    def _enqueue_tts(text: str, char_name: str):
        cleaned = _tts_clean(strip_mechanical_blocks(text))
        for sentence in _split_sentences(cleaned):
            _app.audio_queue.put((sentence, char_name))

    # ── Helper : directive récente dans l'historique ──────────────────────────

    def _has_recent_directive(agent_name: str) -> bool:
        import re as _re_dir
        _natural_jet_re = _re_dir.compile(
            r'\b' + _re_dir.escape(agent_name) + r'\b'
            r'.*?(?:fait|fais|faites|faire|lance|lances|lancer|effectue|effectues|effectuer'
            r'|doit faire|tente|tentes|tenter|r[eé]alise|proc[eè]de [àa]|roule|roules|rouler)'
            r'\s+(?:un\s+)?jet',
            _re_dir.IGNORECASE | _re_dir.DOTALL,
        )
        _natural_jet_direct_re = _re_dir.compile(
            r'^(?:fais|faites|lance|effectue|roule|tente)\s+(?:un\s+)?jet',
            _re_dir.IGNORECASE,
        )
        _natural_roll_re = _re_dir.compile(
            r'\b' + _re_dir.escape(agent_name) + r'\b'
            r'.*?(?:roll|jet\s+d[e\']|tirage)',
            _re_dir.IGNORECASE | _re_dir.DOTALL,
        )
        # Détecte : "Elara lance investigation(int)" / "tu peux faire perception"
        # sans nécessiter le mot-clé "jet". Couvre toutes les compétences D&D 5e.
        _SKILL_NAMES = (
            r'investigation|perception|athl[eé]tisme|discr[eé]tion|perspicacit[eé]'
            r'|acrobaties?|arcanes?|histoire|intimidation|m[eé]decine|nature|religion'
            r'|survie|persuasion|tromperie|repr[eé]sentation|escamotage|dressage'
            r'|force|dext[eé]rit[eé]|constitution|intelligence|sagesse|charisme'
            r'|sauvegarde|save|check'
        )
        _natural_skill_re = _re_dir.compile(
            r'\b' + _re_dir.escape(agent_name) + r'\b'
            r'.{0,120}?'
            r'(?:lance[sz]?|lancer|fai[st]|faire|effectue[sz]?|effectuer'
            r'|tente[sz]?|tenter|peux|peut|dois|roule[sz]?|rouler'
            r'|essaie[sz]?|essayer)'
            r'.{0,60}?'
            r'(?:' + _SKILL_NAMES + r')',
            _re_dir.IGNORECASE | _re_dir.DOTALL,
        )
        # Détecte aussi les invitations sans le nom de l'agent en début de message
        # ex: "Lance investigation(int) pour comprendre ce qui manque"
        _natural_skill_direct_re = _re_dir.compile(
            r'^(?:lance[sz]?|fai[st]|effectue[sz]?|tente[sz]?|roule[sz]?|essaie[sz]?)'
            r'\s+(?:un[e]?\s+)?(?:' + _SKILL_NAMES + r')',
            _re_dir.IGNORECASE,
        )
        try:
            gc = _gc()
            for _msg in reversed((gc.messages if gc else [])[-10:]):
                _mc  = str(_msg.get("content", ""))
                _who = str(_msg.get("name", ""))
                if "[DIRECTIVE SYSTÈME" in _mc and agent_name in _mc:
                    return True
                if _who in ("Alexis_Le_MJ", "Alexis Le MJ"):
                    if (_natural_jet_re.search(_mc)
                            or _natural_roll_re.search(_mc)
                            or _natural_jet_direct_re.search(_mc)
                            or _natural_skill_re.search(_mc)
                            or _natural_skill_direct_re.search(_mc)):
                        return True
        except Exception:
            pass
        return False

    # ── Helper : args d'un tool_call ─────────────────────────────────────────

    def _extract_tool_args(tc) -> dict:
        try:
            import json as _j
            raw = (tc.get("function", {}).get("arguments", "{}")
                   if isinstance(tc, dict)
                   else getattr(getattr(tc, "function", None), "arguments", "{}"))
            return _j.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            return {}

    _FREE_TOOLS = frozenset({"roll_dice", "update_hp", "use_spell_slot", "add_temp_hp",
                              "add_item_to_inventory", "remove_item_from_inventory", "update_currency"})

    # ── Regex hors-tour ──────────────────────────────────────────────────────

    _ILLEGAL_OFFTURN = _re.compile(
        r"\b(je me d[eé]place|je cours|je bouge|je marche|je recule|je charge"
        r"|j'attaque(?! d'opportunit)|j'effectue une attaque"
        r"|je lance (?!un regard|un cri|un mot|un avertissement)"
        r"|je d[eé]coche|je frappe|je plonge|je saute|je roule"
        r"|action bonus|j'utilise mon action(?! de r[eé]action)"
        r"|je m'interpose|je me pr[eé]cipite"
        r"|\[ACTION\]|⚔️\s*ACTION"
        r"|s'abat|\bfrappe\b|enchaîn|troisième frappe|seconde frappe|deuxième frappe"
        r"|mon épée.*s'abat|ma lame.*frappe|mon arme.*touche)\b",
        _re.IGNORECASE
    )

    _MECH_INTENT_RE = _re.compile(
        r"(?:je dois|je vais|je tente de|je m.appr.te\s*..|"
        r"je commence\s*..|je souhaite|je cherche\s*..|je d.cide\s*de|"
        r"j.essaie\s*de|je proc.de\s*..|je veux)\s+"
        r"(?:analys|isol|inspect|examin|.tudi|d.tect|"
        r"identifi|lanc|invoqu|utilis|appliqu|"
        r"test\w|sond|mesur|dissip|contrer|enqu.t|investig|"
        r"purifi|sanctifi|soign|gu.ri|consacr|compar|"
        r"moduli|stabilis|calibr|amplifi)",
        _re.IGNORECASE
    )

    _ENV_DISCOVERY_RE = _re.compile(
        r'\b(?:pierre|bois|m[eé]tal|structure|fondations?|sol\b|mur\b|plafond|d[eé]bris|ruines?|surface|ma[cç]onnerie)\b'
        r'.{0,40}'
        r'\b(?:qualit[eé]|bonne facture|solide|fragile|corrompu|comprom|intact|fissu|d[eé]grad|pourri|stables?|instables?|sain\b|magique|enchant[eé]|hant[eé]|r[eé]sistan|renforc)',
        _re.IGNORECASE | _re.DOTALL
    )

    # ─────────────────────────────────────────────────────────────────────────
    # patched_receive
    # ─────────────────────────────────────────────────────────────────────────

    def patched_receive(self_mgr, message, sender, request_reply=None, silent=False):

        # ── Décodage du message ───────────────────────────────────────────────
        if isinstance(message, dict):
            content    = message.get("content", "")
            name       = message.get("name", sender.name)
            tool_calls = message.get("tool_calls", None)
        else:
            content    = message
            name       = sender.name
            tool_calls = None

        # Référence à la méthode originale (capturée au moment du patch)
        _original_receive = self_mgr.__class__.__mro__[1].receive

        # ── is_system ────────────────────────────────────────────────────────
        is_system = False
        if isinstance(message, dict) and message.get("role") == "tool":
            is_system = True
        if content and str(content).startswith("[RÉSULTAT SYSTÈME"):
            if name not in PLAYER_NAMES:
                is_system = True
            else:
                # Violation : PJ a usurpé le préfixe système
                _app.msg_queue.put({
                    "sender": "⚠️ Règle",
                    "text": (
                        f"[VIOLATION SYSTÈME — {name}]\n"
                        f"{name} a utilisé le préfixe [RÉSULTAT SYSTÈME] réservé au MJ. "
                        f"Message masqué.\n\n"
                        f"RAPPEL : Seul Alexis (MJ) produit des [RÉSULTAT SYSTÈME]. "
                        f"Après un résultat, tu narres UNIQUEMENT ce que ton personnage "
                        f"ressent physiquement ou mentalement — jamais ce qui existe dans le monde."
                    ),
                    "color": "#F44336",
                })
                _original_receive(
                    self_mgr,
                    {"role": "user", "content": (
                        f"[DIRECTIVE SYSTÈME — VIOLATION]\n"
                        f"{name} : ton dernier message a été masqué car tu as utilisé "
                        f"le préfixe [RÉSULTAT SYSTÈME] qui appartient exclusivement au MJ.\n\n"
                        f"RÈGLE : Après qu'Alexis t'a donné un résultat, tu décris UNIQUEMENT "
                        f"ce que {name} ressent dans son corps ou son esprit (tension, douleur, "
                        f"intuition, doute). Tu ne décris PAS ce que tu trouves, vois ou perçois "
                        f"dans le monde. Reformule en une phrase de ressenti personnel."
                    ), "name": "Alexis_Le_MJ"},
                    sender, request_reply=False, silent=True,
                )
                return

        # ── GARDE-FOU ANTI-COPIE ─────────────────────────────────────────────
        if (not is_system
                and name in PLAYER_NAMES
                and content
                and str(content).strip() not in ("[SILENCE]", "")):
            import re as _re_copy
            def _word_set(t):
                return set(_re_copy.findall(r"[a-zA-ZÀ-ÿ]{4,}", t.lower()))
            _cur_words = _word_set(str(content))
            _copy_detected = False
            _copy_ratio    = 0.0
            if _cur_words and len(_cur_words) >= 4:
                # Helper : calcule le ratio d'overlap
                def _overlap(w1, w2, min_common=3):
                    common = w1 & w2
                    if len(common) < min_common:
                        return 0.0
                    return len(common) / max(len(w2), 1)

                # ── 1) Auto-répétition (même joueur, dernier message) ─────
                _self_prev = ctx.last_player_messages.get(f"_hist_{name}_0", "")
                if _self_prev:
                    _self_words = _word_set(_self_prev)
                    if _self_words and len(_self_words) >= 3:
                        _self_ratio = _overlap(_cur_words, _self_words, min_common=2)
                        if _self_ratio > 0.80:
                            _copy_detected = True
                            _copy_ratio    = _self_ratio

                # ── 2) Copie inter-joueurs (autres joueurs, 5 derniers msgs) ─
                if not _copy_detected:
                    for _pn in PLAYER_NAMES:
                        if _pn == name:
                            continue
                        for _slot in range(5):
                            _prev = ctx.last_player_messages.get(f"_hist_{_pn}_{_slot}", "")
                            if not _prev:
                                continue
                            _prev_words = _word_set(_prev)
                            if not _prev_words or len(_prev_words) < 4:
                                continue
                            _ratio = _overlap(_cur_words, _prev_words, min_common=3)
                            if _ratio > 0.60:
                                _copy_detected = True
                                _copy_ratio    = _ratio
                                break
                        if _copy_detected:
                            break

            if _copy_detected:
                ctx.copy_strikes[name] = ctx.copy_strikes.get(name, 0) + 1
                _strike_n = ctx.copy_strikes[name]

                # ── LOG CONSOLE DÉTAILLÉ ──────────────────────────────────
                _HR = "─" * 72
                print(f"\n{_HR}")
                print(f"[COPIE DÉTECTÉE] {name}  |  ratio={int(_copy_ratio*100)}%  |  strike={_strike_n}")
                print(f"  Mots communs   : {sorted(_cur_words)}")
                # Identifier quelle source a déclenché le match
                _self_prev_dbg = ctx.last_player_messages.get(f"_hist_{name}_0", "")
                if _self_prev_dbg and _word_set(_self_prev_dbg) & _cur_words:
                    print(f"  Source         : AUTO-RÉPÉTITION ({name})")
                    print(f"  Msg précédent  : {_self_prev_dbg[:200]!r}")
                else:
                    for _pn_dbg in PLAYER_NAMES:
                        if _pn_dbg == name:
                            continue
                        for _sl_dbg in range(5):
                            _src = ctx.last_player_messages.get(f"_hist_{_pn_dbg}_{_sl_dbg}", "")
                            if _src and (_word_set(_src) & _cur_words):
                                _r_dbg = len(_word_set(_src) & _cur_words) / max(len(_word_set(_src)), 1)
                                if _r_dbg > 0.40:
                                    print(f"  Source         : COPIE INTER-JOUEURS ({_pn_dbg}, slot {_sl_dbg})")
                                    print(f"  Msg source     : {_src[:200]!r}")
                                    break
                print(f"  Msg incriminé  : {str(content)[:300]!r}")
                print(_HR)
                # ─────────────────────────────────────────────────────────

                _app.msg_queue.put({
                    "sender": "⚠️ Règle",
                    "text": (
                        f"[COPIE DÉTECTÉE] {name} a reproduit ~{int(_copy_ratio*100)}% "
                        f"d'un message récent (strike {_strike_n}). [SILENCE] injecté."
                    ),
                    "color": "#e67e22",
                })
                _original_receive(
                    self_mgr,
                    {"role": "assistant", "content": "[SILENCE]", "name": name},
                    sender, request_reply=False, silent=True,
                )
                if _strike_n == 1:
                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": (
                            f"[AUTO-CORRECTION — {name}] Tu viens de répéter un message "
                            f"existant mot pour mot. Réponds avec ta propre pensée originale "
                            f"en une seule phrase, ou écris [SILENCE] si tu n'as rien à ajouter."
                        ), "name": "Alexis_Le_MJ"},
                        sender, request_reply=False, silent=True,
                    )
                if _strike_n >= 2:
                    ctx.copy_strikes[name] = 0
                return
            else:
                ctx.copy_strikes[name] = 0

            # Mémoriser dans la fenêtre glissante (5 slots FIFO)
            for _slot in range(4, 0, -1):
                ctx.last_player_messages[f"_hist_{name}_{_slot}"] = \
                    ctx.last_player_messages.get(f"_hist_{name}_{_slot-1}", "")
            ctx.last_player_messages[f"_hist_{name}_0"] = str(content)
            for _pn in PLAYER_NAMES:
                if _pn != name:
                    ctx.last_player_messages["_last_other_" + _pn] = str(content)

        # ── GARDE-FOU ANTI-SILENCE ────────────────────────────────────────────
        if (not is_system
                and name in PLAYER_NAMES
                and str(content or "").strip() == "[SILENCE]"):
            ctx.silence_strikes[name] = ctx.silence_strikes.get(name, 0) + 1
            _sil_n = ctx.silence_strikes[name]

            # Notifier le MJ dans l'UI pour qu'il sache ce qui se passe
            _app.msg_queue.put({
                "sender": "⚙️ Système",
                "text": (
                    f"[SILENCE] reçu de {name} (strike {_sil_n}). "
                    f"Nudge injecté — le personnage va retenter."
                ),
                "color": "#888888",
            })

            if _sil_n <= 2:
                # request_reply=True : force AutoGen à faire répondre l'agent immédiatement
                # silent=False      : le nudge est visible dans les logs AutoGen
                _original_receive(
                    self_mgr,
                    {"role": "user", "content": (
                        f"[NUDGE SYSTÈME — {name}] [SILENCE] refusé dans ce contexte. "
                        f"Tu dois contribuer une phrase — une pensée, une réaction émotionnelle, "
                        f"un doute, une question au MJ. [SILENCE] n'est autorisé que si tu es "
                        f"physiquement incapable de parler. Réponds maintenant en une seule phrase, "
                        f"dans la peau de {name}."
                    ), "name": "Alexis_Le_MJ"},
                    sender, request_reply=True, silent=False,
                )
            else:
                # Strike 3+ : abandon, remise à zéro du compteur
                ctx.silence_strikes[name] = 0

            return  # CRUCIAL : empêche la chute vers le bloc d'affichage vide

        elif not is_system and name in PLAYER_NAMES and str(content or "").strip():
            ctx.silence_strikes[name] = 0

        # ── FILTRE INACTIF ────────────────────────────────────────────────────
        if name in PLAYER_NAMES and name not in get_active_characters():
            return

        # ── GARDE-FOU OUTILS ──────────────────────────────────────────────────
        is_mj_roll_response = False
        is_auto_roll = False   # True quand c'est un roll_dice suite à [DIRECTIVE SYSTÈME — JET]
        if tool_calls and isinstance(tool_calls, list):
            for _tc in tool_calls:
                _fn_name = (
                    _tc.get("function", {}).get("name")
                    if isinstance(_tc, dict)
                    else getattr(getattr(_tc, "function", None), "name", None)
                )
                if _fn_name not in _FREE_TOOLS:
                    continue

                # Guard 1 : update_hp(amount=0) toujours parasite
                if _fn_name == "update_hp":
                    _args = _extract_tool_args(_tc)
                    if int(_args.get("amount", 1)) == 0:
                        _parasite_msg = (
                            f"[SYSTÈME — APPEL INVALIDE]\n"
                            f"{name} a appelé update_hp(amount=0) — appel ignoré.\n"
                            f"update_hp ne doit être appelé qu'avec un montant non nul "
                            f"(négatif pour dégâts, positif pour soin) ET uniquement "
                            f"sur instruction [DIRECTIVE SYSTÈME — DÉGÂTS/SOIN] du MJ.\n"
                            f"Ne modifie JAMAIS tes PV de ta propre initiative."
                        )
                        _original_receive(
                            self_mgr,
                            {"role": "user", "content": _parasite_msg, "name": "Alexis_Le_MJ"},
                            sender, request_reply=False, silent=True,
                        )
                        return

                # Guard 2 : appel sans directive MJ préalable
                if name in PLAYER_NAMES and not _has_recent_directive(name):
                    ctx.tool_refusal_strikes[name] = ctx.tool_refusal_strikes.get(name, 0) + 1
                    _strike_n = ctx.tool_refusal_strikes[name]
                    _parasite_msg = (
                        f"[REFUS OUTIL — strike {_strike_n}] {name} : "
                        f"NE PAS appeler {_fn_name} sans [DIRECTIVE SYSTÈME] du MJ. "
                        f"Écris uniquement du roleplay ou un bloc [ACTION]."
                    )
                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": _parasite_msg, "name": "Alexis_Le_MJ"},
                        sender, request_reply=False, silent=True,
                    )
                    if _strike_n >= 2:
                        _original_receive(
                            self_mgr,
                            {"role": "assistant", "content": "[SILENCE]", "name": name},
                            sender, request_reply=False, silent=True,
                        )
                        ctx.tool_refusal_strikes[name] = 0
                    # Si le message contient du texte en plus de l'outil parasite
                    # (fréquent avec les modèles Groq/LLaMA), laisser le texte
                    # s'afficher plutôt que silencer complètement l'agent.
                    _has_text = content and str(content).strip() not in ("", "[SILENCE]")
                    if not _has_text:
                        return
                    # Neutraliser le tool_call pour ne pas l'exécuter,
                    # puis laisser le flux normal afficher le contenu textuel.
                    tool_calls = None
                    break

                # Guard 3 : dice_type invalide pour roll_dice
                # L'agent passe parfois un nom de compétence ("Perception", "Investigation")
                # au lieu d'une formule de dés ("1d20"). On re-prompt avec correction.
                if _fn_name == "roll_dice":
                    _rd_args = _extract_tool_args(_tc)
                    _dice_val = str(_rd_args.get("dice_type", "")).strip()
                    _VALID_DICE_RE = _re.compile(r'^\d+d\d+$', _re.IGNORECASE)
                    if not _VALID_DICE_RE.match(_dice_val):
                        _bad_dice_msg = (
                            f"[SYSTÈME — PARAMÈTRE INVALIDE]\n"
                            f"{name} : dice_type=\"{_dice_val}\" n'est pas une formule valide.\n"
                            f"CORRECTION : dice_type doit être une formule comme \"1d20\", \"2d6\".\n"
                            f"Le bonus de compétence/sauvegarde se passe dans le champ bonus (entier).\n\n"
                            f"[DIRECTIVE SYSTÈME — JET OBLIGATOIRE]\n"
                            f"Rappelle d'abord en UNE phrase courte ce que {name} fait physiquement, "
                            f"puis rappelle roll_dice avec dice_type=\"1d20\" et le bonus approprié."
                        )
                        _app.msg_queue.put({
                            "sender": "⚠️ Système",
                            "text":   _bad_dice_msg,
                            "color":  "#FF9800",
                        })
                        _original_receive(
                            self_mgr,
                            {"role": "user", "content": _bad_dice_msg, "name": "Alexis_Le_MJ"},
                            sender, request_reply=True, silent=False,
                        )
                        return

                # Guard 4 : appel d'outil sans narration
                # L'agent doit toujours fournir une phrase décrivant ce qu'il fait
                # avant (ou avec) l'appel d'outil. Si content est vide, on re-prompt.
                _has_narrative = content and str(content).strip() not in ("", "[SILENCE]")
                if not _has_narrative and name in PLAYER_NAMES:
                    _no_narr_msg = (
                        f"[DIRECTIVE SYSTÈME — NARRATION MANQUANTE]\n"
                        f"{name} : avant d'appeler {_fn_name}, tu dois écrire UNE phrase "
                        f"décrivant brièvement ce que ton personnage fait ou ressent "
                        f"(ex : « Je scrute les alentours avec attention. »).\n"
                        f"Réponds avec ta phrase narrative ET l'appel d'outil dans le même message."
                    )
                    _app.msg_queue.put({
                        "sender": "⚠️ Système",
                        "text":   _no_narr_msg,
                        "color":  "#FF9800",
                    })
                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": _no_narr_msg, "name": "Alexis_Le_MJ"},
                        sender, request_reply=True, silent=False,
                    )
                    return

                # Appel légitime
                is_mj_roll_response = True
                # Capturer AVANT que gui_get_human_input consomme le flag
                if _fn_name == "roll_dice":
                    is_auto_roll = getattr(_app, "_pending_auto_roll", False)
                ctx.tool_refusal_strikes[name] = 0
                if _fn_name == "add_temp_hp":
                    try:
                        if _app._combat_tracker is not None:
                            _app.root.after(300, _app._combat_tracker.sync_pc_hp_from_state)
                    except Exception:
                        pass
                break

        # ── FILTRE COMBAT : [ACTION] hors-tour ──────────────────────────────
        _content_str_offturn = str(content) if content else ""
        _action_match_offturn = ACTION_PATTERN.search(_content_str_offturn)
        _is_reaction_block = (
            _action_match_offturn is not None
            and "réaction" in (_action_match_offturn.group("type") or "").lower()
        )
        _is_offturn_action = (
            not is_system
            and not is_mj_roll_response
            and COMBAT_STATE["active"]
            and name in PLAYER_NAMES
            and name != COMBAT_STATE.get("active_combatant")
            and content
            and _action_match_offturn is not None
            and not _is_reaction_block
        )
        if _is_offturn_action:
            _block_msg = (
                f"[SYSTÈME — HORS TOUR]\n"
                f"Ce n'est pas le tour de {name}. "
                f"C'est actuellement le tour de {COMBAT_STATE.get('active_combatant', '?')}. "
                f"Tu ne peux PAS déclarer d'[ACTION] hors de ton tour.\n"
                f"Options autorisées : réaction D&D 5e (Attaque d'opportunité, Shield, Contresort…) "
                f"ou une phrase de roleplay sans mécanique. "
                f"Attends ton tour avant d'agir."
            )
            _app.msg_queue.put({"sender": "⚔️ Combat", "text": _block_msg, "color": "#cc4422"})
            _original_receive(
                self_mgr,
                {"role": "user", "content": _block_msg, "name": "Alexis_Le_MJ"},
                sender, request_reply=False, silent=True,
            )
            _original_receive(self_mgr, message, sender, request_reply, silent)
            return

        # ── FILTRE COMBAT : silencé (réaction + parole épuisées) ─────────────
        if (not is_system
                and not is_mj_roll_response
                and COMBAT_STATE["active"]
                and name in PLAYER_NAMES
                and name != COMBAT_STATE.get("active_combatant")
                and _is_fully_silenced(name)):
            _app.msg_queue.put({
                "sender": "⚔️ Combat",
                "text":   f"🤫 {name} — silencieux (réaction ET parole déjà utilisées ce round).",
                "color":  "#444455"
            })
            _original_receive(self_mgr, message, sender, request_reply, silent)
            return

        # ── FILTRE COMBAT : action illégale hors-tour ────────────────────────
        _is_offturn_violation = (
            not is_system
            and not is_mj_roll_response
            and COMBAT_STATE["active"]
            and name in PLAYER_NAMES
            and name != COMBAT_STATE.get("active_combatant")
            and content
            and str(content).strip() != "[SILENCE]"
            and _ILLEGAL_OFFTURN.search(str(content))
        )
        if _is_offturn_violation:
            _app.msg_queue.put({
                "sender": "⚠️ Combat",
                "text": (
                    f"[VIOLATION] {name} a tenté une action interdite hors-tour "
                    f"(mouvement, attaque ou sort hors réaction). "
                    f"Ce n'est pas son tour — seule une réaction D&D 5e ou une phrase brève est permise."
                ),
                "color": "#cc4422"
            })
            _original_receive(self_mgr, message, sender, request_reply, silent)
            return

        # ── INTERCEPTION SORT [SORT: Nom | Niveau: X | Cible: Y] ─────────────
        if (not is_system
                and name in SPELL_CASTERS
                and content
                and SORT_PATTERN.search(str(content))):
            m = SORT_PATTERN.search(str(content))
            spell_name  = m.group("nom").strip()
            spell_level = int(m.group("niveau"))
            target      = (m.group("cible") or "").strip()
            clean_content = SORT_PATTERN.sub("", str(content)).strip()

            if not is_spell_prepared(name, spell_name):
                _avail3 = get_prepared_spell_names(name)
                _avail3_str = ", ".join(_avail3) if _avail3 else "aucun sort préparé trouvé"
                _not_prepared_msg = (
                    f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE]\n"
                    f"{spell_name} n'est pas dans la liste de sorts préparés de {name}. "
                    f"Ce sort ne peut pas être lancé aujourd'hui.\n\n"
                    f"[SORTS AUTORISÉS POUR {name.upper()}]\n{_avail3_str}\n\n"
                    f"[INSTRUCTION]\nChoisis UNIQUEMENT parmi les sorts listés ci-dessus. "
                    f"Ne tente PAS de lancer {spell_name} — déclare une nouvelle action avec [ACTION]."
                )
                _app.msg_queue.put({"sender": "⚙️ Système", "text": _not_prepared_msg, "color": "#cc4444"})
                _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                _original_receive(
                    self_mgr,
                    {"role": "user", "content": _not_prepared_msg, "name": "Alexis_Le_MJ"},
                    sender, request_reply=request_reply, silent=silent,
                )
                return

            # Vérification de la règle des Actions Bonus (D&D 5e)
            from spell_data import get_spell as _get_sp
            _sp_data = _get_sp(spell_name)
            if _sp_data:
                _valid_ba, _err_ba = validate_bonus_action_rule(
                    name, spell_name, spell_level, _sp_data.get("cast_time_raw", []), COMBAT_STATE.get("turn_spells", [])
                )
                if not _valid_ba:
                    _not_ba_msg = (
                        f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE]\n"
                        f"{_err_ba}\n\n"
                        f"[INSTRUCTION]\nAnnule cette tentative. "
                        f"Choisis une action valide (attaque, esquive, sort permis) "
                        f"et déclare tes intentions."
                    )
                    _app.msg_queue.put({"sender": "⚙️ Système", "text": _not_ba_msg, "color": "#cc4444"})
                    _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": _not_ba_msg, "name": "Alexis_Le_MJ"},
                        sender, request_reply=request_reply, silent=silent,
                    )
                    return

            # Vérification du temps d'incantation (combat only)
            if _sp_data is None:
                from spell_data import get_spell as _get_sp
                _sp_data = _get_sp(spell_name)
            if _sp_data:
                _valid_ct, _err_ct = validate_cast_time_in_combat(
                    spell_name, _sp_data.get("cast_time_raw", [])
                )
                if not _valid_ct:
                    _not_ct_msg = (
                        f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE]\n"
                        f"{_err_ct}\n\n"
                        f"[INSTRUCTION]\nAnnule cette tentative. "
                        f"Choisis une action valide et déclare-la avec [ACTION]."
                    )
                    _app.msg_queue.put({"sender": "⚙️ Système", "text": _not_ct_msg, "color": "#cc4444"})
                    _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": _not_ct_msg, "name": "Alexis_Le_MJ"},
                        sender, request_reply=request_reply, silent=silent,
                    )
                    return

            if spell_level and spell_level > 0:
                _state_check = load_state()
                _slots_avail = (
                    _state_check.get("characters", {}).get(name, {})
                    .get("spell_slots", {}).get(str(spell_level), 0)
                )
                if _slots_avail <= 0:
                    # ── Bypass rituel : Wizard/Cleric peuvent caster sans slot ──
                    if can_ritual_cast(name, spell_name):
                        _ritual_msg = (
                            f"🕯️ {name} lance {spell_name} en tant que RITUEL "
                            f"(+10 min d'incantation, aucun slot consommé)."
                        )
                        _app.msg_queue.put({"sender": "⚙️ Système", "text": _ritual_msg, "color": "#8888cc"})
                    else:
                        _supers = _slots_superieurs_disponibles(name, spell_level)
                        if _supers:
                            _upcast_hint = (
                                f"\n  ↑ UPCAST DISPONIBLE : tu peux lancer {spell_name} "
                                f"avec un slot de niveau supérieur.\n"
                                f"  Niveaux disponibles : {', '.join(str(l) for l in _supers)}\n"
                                f"  → Déclare : [SORT: {spell_name} | Niveau: {_supers[0]} | Cible: {target}]"
                            )
                        else:
                            _upcast_hint = (
                                f"\n  Aucun emplacement de niveau supérieur disponible non plus."
                            )
                        _no_slot_msg = (
                            f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE]\n"
                            f"{name} n'a plus d'emplacement de sort de niveau {spell_level}. "
                            f"Le sort {spell_name} ne peut pas être lancé à ce niveau.\n"
                            f"{_upcast_hint}\n\n"
                            f"[INSTRUCTION]\n"
                            f"Choisis parmi : upcast (slot sup. si ✅ ci-dessus), "
                            f"sort de niveau inférieur, tour de magie, ou attaque physique. "
                            f"Déclare une nouvelle action avec [ACTION]."
                        )
                        _app.msg_queue.put({"sender": "⚙️ Système", "text": _no_slot_msg, "color": "#cc4444"})
                        _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                        _original_receive(
                            self_mgr,
                            {"role": "user", "content": _no_slot_msg, "name": "Alexis_Le_MJ"},
                            sender, request_reply=request_reply, silent=silent,
                        )
                        return

            ctx.spell_confirm_event.clear()
            ctx.spell_confirm_result.clear()

            def _resume_cb(confirmed, actual_level,
                           _ev=ctx.spell_confirm_event, _res=ctx.spell_confirm_result):
                _app._unregister_approval_event(_ev)
                _res["confirmed"]    = confirmed
                _res["actual_level"] = actual_level
                _ev.set()

            _app._register_approval_event(ctx.spell_confirm_event)
            _app.msg_queue.put({
                "action": "spell_confirm", "char_name": name,
                "spell_name": spell_name, "spell_level": spell_level,
                "target": target, "resume_callback": _resume_cb,
            })

            if clean_content and clean_content != "[SILENCE]":
                clean_content = _strip_stars(clean_content)
                _app.msg_queue.put({"sender": name, "text": clean_content,
                                    "color": _app.CHAR_COLORS.get(name, "#e0e0e0")})
                log_tts_start(name, clean_content)
                _enqueue_tts(clean_content, name)

            ctx.spell_confirm_event.wait(timeout=300)
            _app._unregister_approval_event(ctx.spell_confirm_event)

            _final_level = ctx.spell_confirm_result.get("actual_level", spell_level)
            if ctx.spell_confirm_result.get("confirmed", False):
                if _final_level > 0 and not can_ritual_cast(name, spell_name):
                    # Consume slot
                    use_spell_slot(name, str(_final_level))
                    _app._update_agent_combat_prompts()
                
                if _sp_data:
                    _unit = _sp_data.get("cast_time_raw", [{}])[0].get("unit", "") if _sp_data.get("cast_time_raw") else ""
                    COMBAT_STATE.setdefault("turn_spells", []).append({
                        "name": spell_name, "level": _final_level, "cast_time_unit": _unit
                    })
                
                # Exécution des mécaniques dynamiques du sort
                try:
                    _fb = execute_action_mechanics(
                        name, "Action — Sort", f"Lancer {spell_name}", "", target or "Ennemi", COMBAT_STATE["characters"], ctx,
                        lvl=_final_level,
                        single_attack=False,
                        type_label="Action — Sort",
                        char_mechanics=COMBAT_STATE["characters"],
                        pending_smite=ctx.pending_smite,
                        pending_skill_narrators=ctx.pending_skill_narrators,
                        app=_app,
                        extract_spell_name_fn=extract_spell_name_llm,
                        is_spell_prepared_fn=is_spell_prepared,
                        get_prepared_spell_names_fn=get_prepared_spell_names,
                    )
                    _app.msg_queue.put({"sender": "⚙️ Système", "text": _fb, "color": "#a89f91"})
                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": _fb, "name": "Alexis_Le_MJ"},
                        sender, request_reply=False, silent=True,
                    )
                except Exception as _exec_err:
                    print(f"Erreur exec sort auto: {_exec_err}")
                
                _original_receive(self_mgr, message, sender, request_reply, silent)
                return
            else:
                _app.msg_queue.put({
                    "sender": "❌ MJ",
                    "text": f"[SORT] Le sort {spell_name} de {name} a été refusé par le MJ.",
                    "color": "#ef9a9a"
                })
                _original_receive(
                    self_mgr,
                    {"role": "user", "content": f"[SORT] Le sort {spell_name} de {name} a été refusé par le MJ.", "name": "Alexis_Le_MJ"},
                    sender, request_reply=False, silent=True,
                )
                _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                return

            _original_receive(self_mgr, message, sender, request_reply, silent)
            return

        # ── INTERCEPTION ACTIONS [ACTION] ─────────────────────────────────────
        if (not is_system
                and name in PLAYER_NAMES
                and content
                and ACTION_PATTERN.search(str(content))):

            clean_content = ACTION_PATTERN.sub("", str(content)).strip()
            if clean_content and clean_content != "[SILENCE]":
                clean_content = _strip_stars(clean_content)
                _app.msg_queue.put({
                    "sender": name, "text": clean_content,
                    "color":  _app.CHAR_COLORS.get(name, "#e0e0e0"),
                })
                log_tts_start(name, clean_content)
                _enqueue_tts(clean_content, name)

            # Collecte toutes les sous-actions
            _all_subactions: list = []
            for _m_a in ACTION_PATTERN.finditer(str(content)):
                _type_lbl = (_m_a.group("type") or "").strip() or "Action"
                _intention = _m_a.group("intention").strip()
                _regle     = _m_a.group("regle").strip()
                _cible     = _m_a.group("cible").strip()
                _all_subactions.extend(
                    split_into_subactions(
                        _type_lbl, _intention, _regle, _cible,
                        _CM.get(name, {}),   # stats du perso → n_attacks correct
                    )
                )

            _sub_total = len(_all_subactions)
            _turn_aborted = False

            for _sub_idx, _sub in enumerate(_all_subactions, start=1):
                # ── Pré-vérification slots sort ──────────────────────────────
                _pre_is_spell = any(
                    k in _sub["regle"].lower() or k in _sub["intention"].lower()
                    for k in (
                        "sort","magie","incant","invoque","appelle","convoque","projette","déclenche",
                        "boule","projectile","éclair","feu","dard","rayon","missile","flamme","froid",
                        "nécro","acide","tonnerre","soin","soigne","heal","cure","guéri","restaure",
                        "parole","bénédic","sanctif","gardien","arme spirit","bannit","contresort",
                        "dissip","mur de","bouclier","protection","résistance","balise",
                    )
                )
                _pre_lvl = None
                _pre_spell_candidate = None   # initialisé ici — assigned plus tard si _pre_lvl > 0
                if _pre_is_spell:
                    for _pat in (r"niv(?:eau)?\.?\s*(\d+)", r"niveau\s*(\d+)", r"\bniv(\d+)",
                                 r"slot\s+(?:de\s+)?(?:niveau\s+)?(\d)",
                                 r"emplacement\s+(?:de\s+)?(?:niveau\s+)?(\d)"):
                        _pm = _re.search(_pat, _sub["regle"] + " " + _sub["intention"], _re.IGNORECASE)
                        if _pm:
                            _pre_lvl = int(_pm.group(1))
                            break

                _sub_ev  = _threading.Event()
                _sub_res: dict = {}

                if _pre_is_spell and _pre_lvl and _pre_lvl > 0:
                    try:
                        _pre_state = load_state()
                        _pre_slots = (
                            _pre_state.get("characters", {})
                            .get(name, {}).get("spell_slots", {}).get(str(_pre_lvl), 0)
                        )
                    except Exception:
                        _pre_slots = 1
                    if _pre_slots <= 0:
                        # ── Bypass rituel via [ACTION] ──
                        _pre_spell_for_ritual = extract_spell_name_llm(_sub["intention"], name)
                        if _pre_spell_for_ritual and can_ritual_cast(name, _pre_spell_for_ritual):
                            _ritual_msg2 = (
                                f"🕯️ {name} lance {_pre_spell_for_ritual} en tant que RITUEL "
                                f"(+10 min d'incantation, aucun slot consommé)."
                            )
                            _app.msg_queue.put({"sender": "⚙️ Système", "text": _ritual_msg2, "color": "#8888cc"})
                        else:
                            _supers2 = _slots_superieurs_disponibles(name, _pre_lvl)
                            _spell_nm = extract_spell_name_llm(_sub["intention"], name) or "ce sort"
                            if _supers2:
                                _upcast_hint2 = (
                                    f"\n  ↑ UPCAST DISPONIBLE : tu peux lancer {_spell_nm} "
                                    f"avec un slot de niveau supérieur.\n"
                                    f"  Niveaux disponibles : {', '.join(str(l) for l in _supers2)}\n"
                                    f"  → Déclare : [SORT: {_spell_nm} | Niveau: {_supers2[0]} | Cible: ...]"
                                )
                            else:
                                _upcast_hint2 = (
                                    f"\n  Aucun emplacement de niveau supérieur disponible non plus."
                                )
                            _no_slot_fb = (
                                f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE]\n"
                                f"{name} n'a plus d'emplacement de sort de niveau {_pre_lvl}. "
                                f"Ce sort ne peut pas être lancé à ce niveau.\n"
                                f"{_upcast_hint2}\n\n"
                                f"[INSTRUCTION]\n"
                                f"Choisis parmi : upcast (slot sup. si ✅ ci-dessus), "
                                f"sort de niveau inférieur, tour de magie, ou attaque physique."
                            )
                            _app.msg_queue.put({"sender": "⚙️ Système", "text": _no_slot_fb, "color": "#cc4444"})
                            # Insert the original message first so that the agent hasn't theoretically responded to the feedback
                            _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                            _original_receive(
                                self_mgr,
                                {"role": "user", "content": _no_slot_fb, "name": "Alexis_Le_MJ"},
                                sender, request_reply=request_reply, silent=silent,
                            )
                            _sub_ev.set()
                            return

                    # Vérification sorts préparés (pré-check)
                    _pre_spell_candidate = extract_spell_name_llm(_sub["intention"], name)
                    if _pre_spell_candidate:
                        if not is_spell_prepared(name, _pre_spell_candidate):
                            _avail2 = get_prepared_spell_names(name)
                            _avail2_str = ", ".join(_avail2) if _avail2 else "aucun sort préparé trouvé"
                            _no_prep_fb = (
                                f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE]\n"
                                f"« {_pre_spell_candidate} » n'est pas dans la liste de sorts "
                                f"préparés de {name}. Ce sort ne peut pas être lancé aujourd'hui.\n\n"
                                f"[SORTS AUTORISÉS POUR {name.upper()}]\n{_avail2_str}\n\n"
                                f"[INSTRUCTION]\nChoisis UNIQUEMENT parmi les sorts listés ci-dessus."
                            )
                            _app.msg_queue.put({"sender": "⚙️ Système", "text": _no_prep_fb, "color": "#cc4444"})
                            _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                            _original_receive(
                                self_mgr,
                                {"role": "user", "content": _no_prep_fb, "name": "Alexis_Le_MJ"},
                                sender, request_reply=request_reply, silent=silent,
                            )
                            _sub_ev.set()
                            return
                        
                        # Vérification de la règle des Actions Bonus (D&D 5e)
                        from spell_data import get_spell as _get_sp
                        _sp_data_sub = _get_sp(_pre_spell_candidate)
                        if _sp_data_sub:
                            _eff_lvl = _pre_lvl or _sp_data_sub.get("level", 0)
                            _valid_ba_sub, _err_ba_sub = validate_bonus_action_rule(
                                name, _pre_spell_candidate, _eff_lvl, _sp_data_sub.get("cast_time_raw", []), COMBAT_STATE.get("turn_spells", [])
                            )
                            if not _valid_ba_sub:
                                _not_ba_fb2 = (
                                    f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE]\n"
                                    f"{_err_ba_sub}\n\n"
                                    f"[INSTRUCTION]\nAnnule cette tentative. "
                                    f"Choisis une action valide (attaque, esquive, ou un tour de magie coûtant 1 action si applicable)."
                                )
                                _app.msg_queue.put({"sender": "⚙️ Système", "text": _not_ba_fb2, "color": "#cc4444"})
                                _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                                _original_receive(
                                    self_mgr,
                                    {"role": "user", "content": _not_ba_fb2, "name": "Alexis_Le_MJ"},
                                    sender, request_reply=request_reply, silent=silent,
                                )
                                _sub_ev.set()
                                return

                            # Vérification du temps d'incantation (combat only)
                            _valid_ct_sub, _err_ct_sub = validate_cast_time_in_combat(
                                _pre_spell_candidate, _sp_data_sub.get("cast_time_raw", [])
                            )
                            if not _valid_ct_sub:
                                _not_ct_fb2 = (
                                    f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE]\n"
                                    f"{_err_ct_sub}\n\n"
                                    f"[INSTRUCTION]\nAnnule cette tentative. "
                                    f"Choisis une action valide et déclare-la avec [ACTION]."
                                )
                                _app.msg_queue.put({"sender": "⚙️ Système", "text": _not_ct_fb2, "color": "#cc4444"})
                                _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                                _original_receive(
                                    self_mgr,
                                    {"role": "user", "content": _not_ct_fb2, "name": "Alexis_Le_MJ"},
                                    sender, request_reply=request_reply, silent=silent,
                                )
                                _sub_ev.set()
                                return

                def _sub_cb(confirmed, mj_note="", _ev=_sub_ev, _res=_sub_res):
                    _app._unregister_approval_event(_ev)
                    _res["confirmed"] = confirmed
                    _res["mj_note"]   = mj_note
                    _ev.set()

                _app._register_approval_event(_sub_ev)
                _app.msg_queue.put({
                    "action": "action_confirm", "char_name": name,
                    "type_label": _sub["type_label"], "intention": _sub["intention"],
                    "regle": _sub["regle"], "cible": _sub["cible"],
                    "sub_index": _sub_idx, "sub_total": _sub_total,
                    "resume_callback": _sub_cb,
                })

                _sub_ev.wait(timeout=600)
                _app._unregister_approval_event(_sub_ev)

                _confirmed = _sub_res.get("confirmed", False)
                _mj_note   = _sub_res.get("mj_note", "")

                if _confirmed:
                    _is_single_atk = _sub.get("single_attack", False)

                    if _is_single_atk:
                        # ── FLOW ATTAQUE INDIVIDUELLE (Phase 1 / 2 / 3) ──────
                        _atk_data = roll_attack_only(
                            name, _sub["regle"], _sub["intention"],
                            _sub["cible"], _mj_note, _CM
                        )

                        if _atk_data["is_fumble"]:
                            feedback = (
                                "[RÉSULTAT SYSTÈME — ATTAQUE]\n"
                                + _atk_data["atk_text"]
                                + "\n\n[INSTRUCTION NARRATIVE]\n"
                                + f"Nat.1 — attaque automatiquement ratée. "
                                + f"Narre en 1 phrase la maladresse de {name}."
                            )
                            _app.msg_queue.put({"sender": "⚙️ Système", "text": feedback, "color": "#4fc3f7"})
                            _original_receive(
                                self_mgr,
                                {"role": "user", "content": feedback, "name": "Alexis_Le_MJ"},
                                sender, request_reply=False, silent=True,
                            )
                            continue

                        # Phase 1 : touché/raté ?
                        _hit_ev  = _threading.Event()
                        _hit_res: dict = {}

                        def _hit_cb(hit, mj_note_hit="", _ev=_hit_ev, _res=_hit_res):
                            _app._unregister_approval_event(_ev)
                            _res["hit"]  = hit
                            _res["note"] = mj_note_hit
                            _ev.set()

                        _app._register_approval_event(_hit_ev)
                        _app.msg_queue.put({
                            "action": "result_confirm", "char_name": name,
                            "type_label": _sub["type_label"],
                            "results_text": _atk_data["atk_text"],
                            "mode": "attack", "resume_callback": _hit_cb,
                        })
                        _hit_ev.wait(timeout=600)
                        _app._unregister_approval_event(_hit_ev)

                        _hit      = _hit_res.get("hit", False)
                        _hit_note = _hit_res.get("note", "")

                        if not _hit:
                            feedback = (
                                "[RÉSULTAT SYSTÈME — ATTAQUE RATÉE]\n"
                                + _atk_data["atk_text"]
                                + "\n  → RATÉ ❌ (MJ)"
                                + (f"\n  Note : {_hit_note}" if _hit_note else "")
                                + "\n\n[INSTRUCTION NARRATIVE]\n"
                                + f"Attaque ratée. Narre en 1 phrase l'esquive ou la parade de {_sub['cible']}."
                            )
                            _app.msg_queue.put({"sender": "⚙️ Système", "text": feedback, "color": "#4fc3f7"})
                            _original_receive(
                                self_mgr,
                                {"role": "user", "content": feedback, "name": "Alexis_Le_MJ"},
                                sender, request_reply=False, silent=True,
                            )
                            continue

                        # Phase 2 : smite ?
                        _smite_used = None

                        # Détection inline si pas dans pending_smite
                        if name not in ctx.pending_smite:
                            _sub_i_low = _sub["intention"].lower()
                            _sub_r_low = _sub["regle"].lower()
                            _full_msg_low = str(content).lower()
                            _inline_smite_table = {
                                "divine smite":     (None,  "radiant",   "Divine Smite"),
                                "smite divin":      (None,  "radiant",   "Divine Smite"),
                                "châtiment divin":  (None,  "radiant",   "Divine Smite"),
                                "chatiment divin":  (None,  "radiant",   "Divine Smite"),
                                "wrathful smite":   ("1d6", "psychique", "Wrathful Smite"),
                                "courroux divin":   ("1d6", "psychique", "Wrathful Smite"),
                                "thunderous smite": ("2d6", "tonnerre",  "Thunderous Smite"),
                                "frappe tonnerre":  ("2d6", "tonnerre",  "Thunderous Smite"),
                                "branding smite":   ("2d6", "radiant",   "Branding Smite"),
                                "frappe lumière":   ("2d6", "radiant",   "Branding Smite"),
                            }
                            for _kw, (_dice, _typ, _lbl) in _inline_smite_table.items():
                                if (_kw in _sub_i_low or _kw in _sub_r_low or _kw in _full_msg_low):
                                    _sm_lvl = None
                                    for _pat in (r"niv(?:eau)?\.?\s*(\d+)", r"\bniv(\d+)",
                                                 r"slot\s+(?:de\s+)?(?:niveau\s+)?(\d)",
                                                 r"emplacement\s+(?:de\s+)?(?:niveau\s+)?(\d)"):
                                        _pm = _re.search(_pat, _sub_i_low + " " + _sub_r_low, _re.IGNORECASE)
                                        if _pm:
                                            _sm_lvl = int(_pm.group(1))
                                            break
                                    if _sm_lvl is None:
                                        _sm_lvl = 1
                                    if _dice is None:
                                        _dice = f"{_sm_lvl + 1}d8"
                                    ctx.pending_smite[name] = {"dice": _dice, "type": _typ, "label": _lbl}
                                    break

                        if name in ctx.pending_smite:
                            _sm_candidate = ctx.pending_smite[name]
                            _smite_ev  = _threading.Event()
                            _smite_res: dict = {}

                            def _smite_cb(apply_it, mj_note_sm="", _ev=_smite_ev, _res=_smite_res):
                                _app._unregister_approval_event(_ev)
                                _res["apply"] = apply_it
                                _res["note"]  = mj_note_sm
                                _ev.set()

                            try:
                                _smcard_slots = (
                                    load_state().get("characters", {})
                                    .get(name, {}).get("spell_slots", {})
                                )
                                _slots_avail_str = ", ".join(
                                    f"niv.{k}×{v}"
                                    for k, v in sorted(_smcard_slots.items(), key=lambda x: int(x[0]))
                                    if v > 0
                                ) or "⚠ Aucun slot disponible !"
                            except Exception:
                                _slots_avail_str = "(inconnu)"

                            _smite_txt = (
                                f"{name} a {_sm_candidate['label']} actif.\n"
                                f"Dés : {_sm_candidate['dice']} dégâts {_sm_candidate['type']}\n"
                                f"Slots disponibles : {_slots_avail_str}\n"
                                f"L'attaque a touché — appliquer le smite ?"
                            )
                            _app._register_approval_event(_smite_ev)
                            _app.msg_queue.put({
                                "action": "result_confirm", "char_name": name,
                                "type_label": _sm_candidate["label"],
                                "results_text": _smite_txt,
                                "mode": "smite", "resume_callback": _smite_cb,
                            })
                            _smite_ev.wait(timeout=600)
                            _app._unregister_approval_event(_smite_ev)

                            if _smite_res.get("apply", False):
                                _smite_used = ctx.pending_smite.pop(name)
                                _sm_slot_lvl = _smite_used.get("slot_level", 1)
                                try:
                                    from state_manager import use_spell_slot as _uss, load_state as _ls_sm
                                    _sm_state = _ls_sm()
                                    _sm_slots = _sm_state.get("characters", {}).get(name, {}).get("spell_slots", {})
                                    if _sm_slots.get(str(_sm_slot_lvl), 0) <= 0:
                                        _avail = sorted((int(k) for k, v in _sm_slots.items() if v > 0))
                                        if _avail:
                                            _sm_slot_lvl = _avail[0]
                                            if _smite_used["label"] == "Divine Smite":
                                                _smite_used["dice"] = f"{_sm_slot_lvl + 1}d8"
                                        else:
                                            _app.msg_queue.put({
                                                "sender": "⚙️ Système",
                                                "text": (
                                                    f"[Divine Smite annulé] {name} n'a plus "
                                                    f"aucun emplacement de sort disponible."
                                                ),
                                                "color": "#cc4444",
                                            })
                                            _smite_used = None
                                    if _smite_used:
                                        _slot_result = _uss(name, str(_sm_slot_lvl))
                                        _app.msg_queue.put({
                                            "sender": "⚙️ Système",
                                            "text": f"[Slot niv.{_sm_slot_lvl}] {_slot_result}",
                                            "color": "#8888cc",
                                        })
                                        _app._update_agent_combat_prompts()
                                except Exception as _sse:
                                    print(f"[Smite slot] Erreur : {_sse}")

                        # Phase 3 : dégâts
                        # roll_damage_only retourne maintenant (str, int)
                        _dmg_feedback, _dmg_total = roll_damage_only(
                            name, _sub["cible"],
                            _atk_data["dn"], _atk_data["df"], _atk_data["db"],
                            _atk_data["is_crit"], _smite_used, _hit_note, _CM
                        )

                        # Texte compact des dés (sans l'instruction narrative ni le header)
                        _dmg_part = (
                            _dmg_feedback
                            .split("\n\n[INSTRUCTION NARRATIVE]")[0]
                            .replace("[RÉSULTAT SYSTÈME — DÉGÂTS CONFIRMÉS PAR MJ]\n", "")
                            .strip()
                        )

                        # ── Hyperlien dans le chat → popup de confirmation ────────────
                        # Le message "damage_link" est intercepté par le consumer de
                        # msg_queue dans l'application principale, qui crée un widget
                        # cliquable (label/bouton) dans la fenêtre de chat.
                        # Cliquer sur ce widget appelle resume_callback(final_amount).
                        # ─────────────────────────────────────────────────────────────
                        # Code à ajouter dans le handler msg_queue de l'app principale :
                        #
                        #   elif msg.get("action") == "damage_link":
                        #       _cname  = msg["sender"]
                        #       _cible  = msg["cible"]
                        #       _total  = msg["dmg_total"]
                        #       _dtext  = msg["dmg_text"]
                        #       _crit   = msg["is_crit"]
                        #       _cb     = msg["resume_callback"]
                        #       _color  = self.CHAR_COLORS.get(_cname, "#4fc3f7")
                        #       _lbl_txt = (
                        #           f"⚔️  {_cname}  →  {_cible}  :  "
                        #           f"{'🎯 CRITIQUE — ' if _crit else ''}{_total} dégâts "
                        #           f" ─  [Modifier / Confirmer]"
                        #       )
                        #       # Insérer dans le chat_text (tk.Text) un widget cliquable :
                        #       lnk = tk.Label(
                        #           self.chat_text,
                        #           text=_lbl_txt,
                        #           bg="#1e1e2e", fg="#ff9944",
                        #           font=("Consolas", 9, "underline"),
                        #           cursor="hand2",
                        #       )
                        #       lnk.bind("<Button-1>", lambda e, cb=_cb, cn=_cname,
                        #                                        ci=_cible, dt=_dtext,
                        #                                        tot=_total, crit=_crit:
                        #           self.root.after(0, lambda:
                        #               self._open_damage_popup(cn, ci, dt, tot, crit, cb)))
                        #       self.chat_text.window_create(tk.END, window=lnk)
                        #       self.chat_text.insert(tk.END, "\n")
                        #       self.chat_text.see(tk.END)
                        # ─────────────────────────────────────────────────────────────

                        _dmg_ev  = _threading.Event()
                        _dmg_res: dict = {}

                        def _dmg_link_cb(final_amount,
                                         _ev=_dmg_ev, _res=_dmg_res):
                            _app._unregister_approval_event(_ev)
                            _res["amount"] = final_amount
                            _ev.set()

                        _app._register_approval_event(_dmg_ev)
                        _app.msg_queue.put({
                            "action":          "damage_link",
                            "sender":          name,
                            "char_name":       name,        # clé lue par _handle_damage_link
                            "cible":           _sub["cible"],
                            "dmg_text":        _dmg_part,
                            "dmg_total":       _dmg_total,
                            "is_crit":         _atk_data["is_crit"],
                            "resume_callback": _dmg_link_cb,
                        })

                        # Bloque le thread AutoGen jusqu'au clic MJ (timeout 300 s)
                        _dmg_ev.wait(timeout=300)
                        _app._unregister_approval_event(_dmg_ev)

                        _final_dmg = _dmg_res.get("amount", _dmg_total)

                        # ── Appliquer les dégâts si la cible est un PNJ dans le tracker ────────
                        try:
                            if _app._combat_tracker is not None:
                                _app._combat_tracker.apply_damage_to_npc(_sub["cible"], _final_dmg)
                        except Exception as _npc_dmg_err:
                            print(f"[DamageApply PNJ] {_npc_dmg_err}")

                        # ── Appliquer les dégâts si la cible est un PJ ───────────────
                        try:
                            from state_manager import load_state as _ls_d, save_state as _ss_d
                            _cible_str   = _sub["cible"].lower()
                            _pj_targets  = [
                                _pn for _pn in PLAYER_NAMES
                                if _pn.lower() in _cible_str
                                or _cible_str in _pn.lower()
                            ]
                            _state_d = _ls_d()
                            for _pj in _pj_targets:
                                _hp_now  = _state_d.get("characters", {}).get(_pj, {}).get("hp", 0)
                                _new_hp  = max(0, _hp_now - _final_dmg)
                                _state_d["characters"][_pj]["hp"] = _new_hp
                            if _pj_targets:
                                _ss_d(_state_d)
                                try:
                                    _app.root.after(300, _app._refresh_char_stats)
                                except Exception:
                                    pass
                                try:
                                    if _app._combat_tracker is not None:
                                        _app.root.after(400, _app._combat_tracker.sync_pc_hp_from_state)
                                except Exception:
                                    pass
                        except Exception as _dmg_err:
                            print(f"[DamageApply] {_dmg_err}")

                        # ── Feedback propre pour les agents (sans les lignes de dés) ─
                        _crit_tag = " 🎯 CRITIQUE" if _atk_data["is_crit"] else ""
                        _modif_note = (
                            f" (roulé : {_dmg_total}, modifié par MJ)"
                            if _final_dmg != _dmg_total else ""
                        )
                        feedback = (
                            "[RÉSULTAT SYSTÈME — ATTAQUE RÉSOLUE]\n"
                            + _atk_data["atk_text"]
                            + "\n  → TOUCHÉ ✅ (MJ)"
                            + (f"\n  Note : {_hit_note}" if _hit_note else "")
                            + f"\n\n[RÉSULTAT SYSTÈME — DÉGÂTS CONFIRMÉS PAR MJ]\n"
                            + f"⚔️ {name} → {_sub['cible']}{_crit_tag}\n"
                            + f"  Dégâts appliqués : {_final_dmg}{_modif_note}\n"
                            + "\n[INSTRUCTION NARRATIVE]\n"
                            + f"Le système vient d exécuter les dégâts. "
                            + f"Narre en 1-2 phrases vivantes l impact sur {_sub['cible']}. "
                            + f"Ne mentionne PAS les chiffres."
                        )

                        _app.msg_queue.put({
                            "sender": "⚙️ Système",
                            "text": (
                                f"[Dégâts confirmés] {name} → {_sub['cible']}{_crit_tag} : "
                                f"{_final_dmg} dégâts{_modif_note}"
                            ),
                            "color": "#ff9944",
                        })
                        _original_receive(
                            self_mgr,
                            {"role": "user", "content": feedback, "name": "Alexis_Le_MJ"},
                            sender, request_reply=False, silent=True,
                        )

                    else:
                        # ── FLOW NON-ATTAQUE ──────────────────────────────────
                        try:
                            # Ajout aux turn_spells si c'est un sort validé
                            if _pre_is_spell and _pre_spell_candidate:
                                from spell_data import get_spell as _get_sp_fi
                                _sp_fi = _get_sp_fi(_pre_spell_candidate)
                                if _sp_fi:
                                    _fi_unit = _sp_fi.get("cast_time_raw", [{}])[0].get("unit", "") if _sp_fi.get("cast_time_raw") else ""
                                    _fi_lvl = _pre_lvl or _sp_fi.get("level", 0)
                                    COMBAT_STATE.setdefault("turn_spells", []).append({
                                        "name": _pre_spell_candidate, "level": _fi_lvl, "cast_time_unit": _fi_unit
                                    })
                                    
                            feedback = execute_action_mechanics(
                                name, _sub["intention"], _sub["regle"],
                                _sub["cible"], _mj_note,
                                single_attack=False,
                                type_label=_sub.get("type_label", ""),
                                char_mechanics=_CM,
                                pending_smite=ctx.pending_smite,
                                pending_skill_narrators=ctx.pending_skill_narrators,
                                app=_app,
                                extract_spell_name_fn=extract_spell_name_llm,
                                is_spell_prepared_fn=is_spell_prepared,
                                get_prepared_spell_names_fn=get_prepared_spell_names,
                            )
                        except Exception as _exec_err:
                            feedback = (
                                f"[MJ → {name}] ✅ [{_sub['type_label']}] autorisé. "
                                f"(Erreur : {_exec_err}) "
                                f"Narre : {_sub['intention']} — {_sub['regle']} → {_sub['cible']}"
                            )

                        _split_marker = "\n\n[INSTRUCTION NARRATIVE]"
                        _results_part = (
                            feedback.split(_split_marker)[0]
                            .replace("[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ]\n", "")
                            .replace("[RÉSULTAT SYSTÈME — ATTAQUE DE SORT]\n", "")
                            .strip()
                        )

                        _is_spell_attack = feedback.startswith("[RÉSULTAT SYSTÈME — ATTAQUE DE SORT]")

                        if _is_spell_attack:
                            # Attaque de sort → confirmation touché/raté nécessaire
                            _result_ev   = _threading.Event()
                            _result_note: dict = {}

                            def _result_cb(hit, mj_note_res="", _ev=_result_ev, _res=_result_note):
                                _app._unregister_approval_event(_ev)
                                _res["hit"]  = hit
                                _res["note"] = mj_note_res
                                _ev.set()

                            _app._register_approval_event(_result_ev)
                            _app.msg_queue.put({
                                "action": "result_confirm", "char_name": name,
                                "type_label": _sub["type_label"],
                                "results_text": _results_part,
                                "mode": "attack", "resume_callback": _result_cb,
                            })
                            _result_ev.wait(timeout=600)
                            _app._unregister_approval_event(_result_ev)
                        else:
                            # Mode non-attaque → pas d'affichage préliminaire
                            # (⚙️ Système affichera le feedback complet juste après)
                            _result_note: dict = {}

                        _res_mj_note = _result_note.get("note", "")

                        if _is_spell_attack:
                            _spell_hit = _result_note.get("hit", True)
                            cible = _sub["cible"]
                            if not _spell_hit:
                                feedback = (
                                    "[RÉSULTAT SYSTÈME — ATTAQUE DE SORT RATÉE]\n"
                                    + _results_part
                                    + "\n  → RATÉ ❌ (MJ)"
                                    + (f"\n  Note : {_res_mj_note}" if _res_mj_note else "")
                                    + "\n\n[INSTRUCTION NARRATIVE]\n"
                                    + f"Attaque ratée. Narre en 1 phrase comment {cible} esquive ou résiste."
                                )
                            else:
                                feedback = (
                                    "[RÉSULTAT SYSTÈME — ATTAQUE DE SORT RÉSOLUE]\n"
                                    + _results_part
                                    + "\n  → TOUCHÉ ✅ (MJ)"
                                    + (f"\n  Note : {_res_mj_note}" if _res_mj_note else "")
                                    + "\n\n[INSTRUCTION NARRATIVE]\n"
                                    + f"Attaque de sort réussie. Narre en 1-2 phrases l'impact sur {cible}."
                                )
                        else:
                            if _res_mj_note:
                                feedback += f"\n[Modification MJ] {_res_mj_note}"

                        # Mouvement → déplacer le token sur la carte
                        _move_match = _re.search(r'\[MOVE_TOKEN:([^:]+):(\d+):(\d+)\]', feedback)
                        if _move_match:
                            _mv_name = _move_match.group(1)
                            _mv_col  = int(_move_match.group(2))
                            _mv_row  = int(_move_match.group(3))
                            feedback = _re.sub(r'\[MOVE_TOKEN:[^\]]+\]', '', feedback).strip()
                            try:
                                _cmap = getattr(_app, "_combat_map_win", None)
                                if _cmap is not None:
                                    def _do_move(cmap=_cmap, n=_mv_name, c=_mv_col, r=_mv_row):
                                        msg = cmap.move_token(n, c, r)
                                        # Si le token n'existe pas sur la carte, on skip silencieusement
                                        # (pas de placement préalable = mécanique carte non applicable)
                                        if msg and "introuvable" not in msg.lower():
                                            _app.msg_queue.put({"sender": "🗺️ Carte", "text": msg, "color": "#64b5f6"})
                                        try:
                                            _app._rebuild_agent_prompts()
                                        except Exception:
                                            pass
                                    _app.root.after(0, _do_move)
                                else:
                                    _app.msg_queue.put({
                                        "sender": "🗺️ Carte",
                                        "text": (
                                            f"[Mouvement {_mv_name}] Carte non ouverte — "
                                            f"token non déplacé. Ouvrez la carte pour voir les positions."
                                        ),
                                        "color": "#888888",
                                    })
                            except Exception as _mv_err:
                                print(f"[MoveToken] {_mv_err}")

                        _app.msg_queue.put({"sender": "⚙️ Système", "text": feedback, "color": "#4fc3f7"})
                        _original_receive(
                            self_mgr,
                            {"role": "user", "content": feedback, "name": "Alexis_Le_MJ"},
                            sender, request_reply=False, silent=True,
                        )

                else:
                    _note_txt = f" {_mj_note}" if _mj_note else ""
                    feedback  = (f"[MJ → {name}] ❌ [{_sub['type_label']}] refusé.{_note_txt}\n\n"
                                 f"[INSTRUCTION SYSTÈME]\n"
                                 f"L'action de {name} a été refusée. Les ressources (Action, Bonus, etc.) pour cette action "
                                 f"n'ont pas été consommées. Repense ton tour avec tes actions restantes et "
                                 f"re-déclare tes intentions, en terminant par un nouveau [FIN_DE_TOUR].")
                    _app.msg_queue.put({"sender": "❌ MJ", "text": feedback, "color": "#ef9a9a"})
                    _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": feedback, "name": "Alexis_Le_MJ"},
                        sender, request_reply=request_reply, silent=silent,
                    )
                    return

            # End of action subaction loop
            if _turn_aborted:
                pass
            else:
                _app._update_agent_combat_prompts()

            # FIN DE TOUR optionnel
            if (not _turn_aborted
                    and COMBAT_STATE["active"]
                    and name == COMBAT_STATE.get("active_combatant")
                    and "[FIN_DE_TOUR]" in str(content)):
                _app.root.after(0, lambda n=name: _app._on_pc_turn_ended(n))
                
            _original_receive(self_mgr, message, sender, request_reply, silent)

            return

        # ── [FIN_DE_TOUR] sans bloc [ACTION] ────────────────────────────────
        if (not is_system
                and COMBAT_STATE["active"]
                and name in PLAYER_NAMES
                and name == COMBAT_STATE.get("active_combatant")
                and content
                and "[FIN_DE_TOUR]" in str(content)
                and not ACTION_PATTERN.search(str(content))):
            _app.root.after(0, lambda n=name: _app._on_pc_turn_ended(n))

        # ── DIRECTIVES MJ → héros (parseur regex + LLM) ─────────────────────
        if not is_system and name == "Alexis_Le_MJ" and content:
            _directives = parse_mj_directives(
                str(content), PLAYER_NAMES, _CM,
                get_agent_config, _default_model,
            )
            for _d in _directives:
                _d_action  = _d.get("action", "")
                _d_cible   = _d.get("cible", "")
                gc = _gc()
                _d_targets = (
                    [n for n in PLAYER_NAMES if n in [a.name for a in gc.agents]]
                    if _d_cible == "tous"
                    else [_d_cible] if _d_cible in PLAYER_NAMES
                    else []
                )
                if not _d_targets:
                    continue

                for _tgt in _d_targets:
                    import json as _json_d

                    if _d_action == "degats":
                        _montant = int(_d.get("montant", 0))
                        _type_d  = _d.get("type_degat", "")
                        _type_str = f" de {_type_d}" if _type_d else ""
                        _instr = (
                            f"[DIRECTIVE SYSTÈME — DÉGÂTS] ⚠️ APPEL D'OUTIL OBLIGATOIRE\n"
                            f"Destinataire : {_tgt} UNIQUEMENT\n"
                            f"{_montant} dégâts{_type_str}\n\n"
                            f"▶ {_tgt} : tu DOIS appeler update_hp maintenant (règle 4 exception).\n"
                            f"   Appel exact : update_hp(character_name=\"{_tgt}\", amount=-{_montant})\n\n"
                            f"Après le retour de l'outil, narre en 1-2 phrases comment tu encaisses le coup. "
                            f"Pas de chiffres.\n"
                            f"⚠️ Cette réponse NE COÛTE AUCUNE ressource hors-tour."
                        )
                        ctx.pending_damage_narrators.add(_tgt)
                        try:
                            _app.root.after(500, _app._refresh_char_stats)
                        except Exception:
                            pass
                        try:
                            if _app._combat_tracker is not None:
                                _app.root.after(600, _app._combat_tracker.sync_pc_hp_from_state)
                        except Exception:
                            pass

                    elif _d_action == "soin":
                        _montant = int(_d.get("montant", 0))
                        _directive_json = _json_d.dumps(
                            {"action": "soin", "cible": _tgt, "montant": _montant},
                            ensure_ascii=False
                        )
                        _instr = (
                            f"[DIRECTIVE SYSTÈME — SOIN]\n{_directive_json}\n\n"
                            f"{_tgt} : appelle update_hp(character_name=\"{_tgt}\", amount=+{_montant}) "
                            f"IMMÉDIATEMENT — AVANT tout texte.\n"
                            f"Ensuite, narre en 1 phrase ta réaction au soin."
                        )

                    elif _d_action in ("jet_sauvegarde", "jet_competence", "jet_attaque"):
                        _carac  = _d.get("caracteristique", "")
                        _dc     = _d.get("dc")
                        _de     = _d.get("de", "1d20")
                        _bonus  = int(_d.get("bonus", 0))
                        _dc_str = f" contre DC {_dc}" if _dc else ""
                        _action_label = {
                            "jet_sauvegarde": "Jet de sauvegarde",
                            "jet_competence": "Jet de compétence",
                            "jet_attaque":    "Jet d'attaque",
                        }.get(_d_action, "Jet")
                        _real_bonus = _bonus
                        if _real_bonus == 0 and _tgt in _CM:
                            _stats = _CM[_tgt]
                            _carac_low = _carac.lower()
                            _real_bonus = (
                                _stats.get("saves", {}).get(_carac_low)
                                or _stats.get("skills", {}).get(_carac_low)
                                or 0
                            )
                        _directive_json = _json_d.dumps(_d, ensure_ascii=False)
                        _instr = (
                            f"[DIRECTIVE SYSTÈME — JET] ⚠️ APPEL D'OUTIL OBLIGATOIRE\n"
                            f"Destinataire : {_tgt} UNIQUEMENT\n"
                            f"{_action_label} de {_carac}{_dc_str}\n\n"
                            f"▶ {_tgt} : tu DOIS appeler roll_dice maintenant (règle 4 exception).\n"
                            f"   Appel exact : roll_dice("
                            f"character_name=\"{_tgt}\", "
                            f"dice_type=\"{_de}\", "
                            f"bonus={_real_bonus})\n\n"
                            f"Après le résultat du dé, narre en 1 phrase comment "
                            f"{_tgt} vit physiquement ce moment. "
                            f"Ne mentionne pas le chiffre du résultat."
                        )
                    else:
                        continue

                    # Pour les jets de dés demandés explicitement par le MJ,
                    # l'agent doit pouvoir appeler roll_dice sans confirmation MJ.
                    if _d_action in ("jet_sauvegarde", "jet_competence", "jet_attaque"):
                        _app._pending_auto_roll = True

                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": _instr, "name": "Alexis_Le_MJ"},
                        sender, request_reply=False, silent=True,
                    )

        # ── Appel normal ─────────────────────────────────────────────────────
        _original_receive(self_mgr, message, sender, request_reply, silent)

        # ── Garde-fou intention mécanique sans [ACTION] ──────────────────────
        if (not is_system
                and name in PLAYER_NAMES
                and content
                and str(content).strip() not in ("[SILENCE]", "")
                and not ACTION_PATTERN.search(str(content))
                and _MECH_INTENT_RE.search(str(content))):
            _mech_hint = (
                f"[DIRECTIVE SYSTÈME — FORMAT REQUIS]\n"
                f"{name} a déclaré une intention mécanique sans bloc [ACTION].\n\n"
                f"Toute action qui consomme une ressource ou interagit avec le monde\n"
                f"(sort, compétence, investigation, analyse, soin, mouvement tactique…)\n"
                f"DOIT être formalisée en [ACTION] pour que le MJ puisse la valider :\n\n"
                f"  [ACTION]\n"
                f"  Type      : Action / Action Bonus / Réaction\n"
                f"  Intention : <ce que {name} fait exactement>\n"
                f"  Règle 5e  : <sort + niveau OU jet de compétence + bonus OU autre mécanique>\n"
                f"  Cible     : <objet / créature / zone / soi-même>\n\n"
                f"Sans bloc [ACTION], aucune mécanique ne peut être exécutée ni validée par le MJ.\n"
                f"Complète ton message avec ce bloc."
            )
            _original_receive(
                self_mgr,
                {"role": "user", "content": _mech_hint, "name": "Alexis_Le_MJ"},
                sender, request_reply=False, silent=True,
            )

        # ── Garde-fou post-jet : agent décrit l'environnement ────────────────
        if (not is_system
                and name in PLAYER_NAMES
                and name in ctx.pending_skill_narrators
                and content
                and str(content).strip() not in ("[SILENCE]", "")
                and _ENV_DISCOVERY_RE.search(str(content))):
            ctx.pending_skill_narrators.discard(name)
            _env_viol_msg = (
                f"[DIRECTIVE SYSTÈME — VIOLATION RÈGLE 3]\n"
                f"{name} a décrit des propriétés de l'environnement après un jet de dés.\n\n"
                f"RÈGLE ABSOLUE (point 3) : après un [RÉSULTAT SYSTÈME], tu narres UNIQUEMENT\n"
                f"l'effort physique ou mental de ton personnage.\n"
                f"TU NE DÉCRIS JAMAIS ce que tu trouves, découvres ou perçois.\n"
                f"La qualité des matériaux, l'état de la structure, les propriétés magiques :\n"
                f"tout cela appartient au MJ — même si ton jet est élevé.\n\n"
                f"Exemple interdit : 'la pierre est de qualité / de bonne facture'\n"
                f"Exemple correct  : 'Mes doigts s'arrêtent. Quelque chose cloche ici.'\n\n"
                f"Reformule en narrant uniquement la sensation physique de {name}."
            )
            _app.msg_queue.put({
                "sender": "⚠️ Règle",
                "text": (
                    f"[VIOLATION RÈGLE 3] {name} a décrit des propriétés de l'environnement "
                    f"après un jet de dés. Message masqué — le MJ révèle les découvertes."
                ),
                "color": "#F44336"
            })
            _original_receive(
                self_mgr,
                {"role": "user", "content": _env_viol_msg, "name": "Alexis_Le_MJ"},
                sender, request_reply=False, silent=True,
            )
            return
        elif name in ctx.pending_skill_narrators:
            ctx.pending_skill_narrators.discard(name)

        # ── Journal narratif ─────────────────────────────────────────────────
        if not is_system and content and str(content).strip() not in ("[SILENCE]", ""):
            _chat_log.log_message(name, str(content))

        # ── Mémoires contextuelles ────────────────────────────────────────────
        if not is_system and content and str(content).strip() not in ("[SILENCE]", ""):
            _app._update_contextual_memories(str(content))

        # ── Filtre PNJ ────────────────────────────────────────────────────────
        if not is_system and name in PLAYER_NAMES and content and _pnj_pattern.search(str(content)):
            _viol_type = "paroles inventées"
            if _pnj_narrative_re.search(str(content)) or _pnj_narrative_inv_re.search(str(content)):
                _viol_type = "description des actions/expressions d'un PNJ"
            _app.msg_queue.put({
                "sender": "⚠️ Règle",
                "text": (
                    f"[VIOLATION PNJ — {_viol_type}]\n"
                    f"{name} a outrepassé la règle PNJ. Message masqué.\n\n"
                    f"RAPPEL : Si tu t'adresses à un PNJ, UNE seule phrase d'adresse maximum. "
                    f"Tu ne décris pas leurs expressions, tu n'anticipes pas leurs réponses, "
                    f"tu n'imagines pas leurs besoins. Pose la question et arrête-toi.\n"
                    f"Alexis, c'est à vous de donner la réplique du PNJ."
                ),
                "color": "#F44336"
            })
            _original_receive(
                self_mgr,
                {"role": "user", "content": (
                    f"[DIRECTIVE SYSTÈME — VIOLATION PNJ]\n"
                    f"{name} : ton dernier message a été masqué car tu as "
                    f"outrepassé la règle PNJ ({_viol_type}).\n\n"
                    f"RÈGLE : Si tu t'adresses à un PNJ, UNE seule phrase maximum. "
                    f"Tu t'arrêtes immédiatement après. "
                    f"Ne décris pas leurs réactions, n'élabore pas leurs besoins, "
                    f"n'anticipe pas leur réponse.\n"
                    f"Reformule en une seule phrase d'adresse si nécessaire, puis attends le MJ."
                ), "name": "Alexis_Le_MJ"},
                sender, request_reply=False, silent=True,
            )
            return

        # ── Suivi combat : ressource hors-tour ───────────────────────────────
        if (not is_system
                and not is_mj_roll_response
                and COMBAT_STATE["active"]
                and name in PLAYER_NAMES
                and name != COMBAT_STATE.get("active_combatant")
                and content
                and str(content).strip() != "[SILENCE]"):

            if name in ctx.pending_damage_narrators:
                ctx.pending_damage_narrators.discard(name)
            else:
                _content_str = str(content)
                _REACTION_TRIGGER = _re.compile(
                    r"\b(r[eé]action|attaque d.opportunit[eé]|bouclier|riposte"
                    r"|pas de c[oô]t[eé]|sort de r[eé]action|contre-attaque"
                    r"|j.utilise (ma|mon) action de r[eé]action"
                    r"|j.interpose|frappe en r[eé]action)\b",
                    _re.IGNORECASE
                )
                _SPEECH_TRIGGER = _re.compile(
                    r'[«»\"\u201c\u201d]'
                    r'|\bje (crie|hurle|chuchote|dis|murmure|siffle|avertis|lance un cri|lance un mot)\b'
                    r'|\b(attention|garde[sz]?-vous|derrière|à droite|à gauche|recule[sz]?|fuyez)\b',
                    _re.IGNORECASE
                )
                is_reaction = bool(_REACTION_TRIGGER.search(_content_str))
                is_speech   = bool(_SPEECH_TRIGGER.search(_content_str))
                if not is_reaction and not is_speech:
                    is_speech = True

                if is_reaction and name not in COMBAT_STATE["reactions_used"]:
                    COMBAT_STATE["reactions_used"].add(name)
                    _app._update_agent_combat_prompts()
                    _app.msg_queue.put({
                        "sender": "⚔️ Combat",
                        "text":   f"↺ {name} — réaction hors-tour consommée pour ce round.",
                        "color":  "#5588cc"
                    })
                if is_speech and name not in COMBAT_STATE["speech_used"]:
                    COMBAT_STATE["speech_used"].add(name)
                    _app._update_agent_combat_prompts()
                    _app.msg_queue.put({
                        "sender": "⚔️ Combat",
                        "text":   f"💬 {name} — parole hors-tour consommée pour ce round.",
                        "color":  "#8855aa"
                    })

        # ── Affichage final ──────────────────────────────────────────────────
        if name != "Alexis_Le_MJ" or is_system:
            if isinstance(message, dict) and message.get("role") == "tool":
                nom_outil      = message.get("name", "Outil")
                resultat_outil = message.get("content", "")
                _app.msg_queue.put({
                    "sender": f"🎲 Résultat ({nom_outil})",
                    "text":   resultat_outil,
                    "color":  "#4CAF50"
                })
            elif content and str(content).strip() != "[SILENCE]":
                display_name = "Système" if is_system else name
                color        = "#ffcc00" if is_system else "#e0e0e0"
                display_text = (
                    _strip_stars(str(content))
                    if not is_system and display_name in PLAYER_NAMES
                    else content
                )
                _app.msg_queue.put({"sender": display_name, "text": display_text, "color": color})
                if not is_system and display_name in PLAYER_NAMES:
                    log_tts_start(display_name, str(display_text))
                    _enqueue_tts(display_text, display_name)

            if tool_calls and not is_auto_roll:
                # ── Confirmation MJ : lien cliquable dans le chat ────────────
                _tc0 = tool_calls[0] if tool_calls else {}
                _tool_name = (
                    _tc0.get("function", {}).get("name", "outil")
                    if isinstance(_tc0, dict)
                    else getattr(getattr(_tc0, "function", None), "name", "outil")
                )
                _tool_args = _extract_tool_args(_tc0) if tool_calls else {}

                _tc_event  = _threading.Event()
                _tc_result = {}

                def _tc_cb(_ev=_tc_event, _res=_tc_result):
                    _res["confirmed"] = True
                    _ev.set()

                _app._register_approval_event(_tc_event)
                _app.msg_queue.put({
                    "action":          "tool_confirm",
                    "sender":          name,
                    "tool_name":       _tool_name,
                    "tool_args":       _tool_args,
                    "resume_callback": _tc_cb,
                })

                # Bloque le thread AutoGen jusqu'au clic MJ (timeout 180 s → auto-confirme)
                _tc_event.wait(timeout=180)
                _app._unregister_approval_event(_tc_event)

            elif not tool_calls and name in PLAYER_NAMES:
                # L'agent a répondu par du texte au lieu d'appeler roll_dice :
                # annuler le flag pour éviter un auto-roll parasite ultérieur.
                _app._pending_auto_roll = False

    return patched_receive