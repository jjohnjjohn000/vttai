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
    SORT_PATTERN, DAMAGE_PATTERN, PC_NAME_RE,
    DIRECTIVE_PREFILTER, PARSER_SYSTEM,
    get_prepared_spell_names, extract_spell_name_llm, is_spell_prepared,
    can_ritual_cast, build_pnj_patterns, parse_mj_directives,
    validate_bonus_action_rule, validate_cast_time_in_combat
)

# ── OVERRIDE DU PATTERN ACTION (TOLÉRANCE MAXIMALE) ─────────────────────────
ACTION_PATTERN = _re.compile(
    r'(?:\*\*)?(?:\[\s*ACTION\s*\])?(?:\*\*)?\s*'
    r'(?:(?:[\*\-]\s*)?(?:Type|Action|Type d\'action)\s*:\s*(?P<type>[^\n]*)\s*)'
    r'(?:(?:[\*\-]\s*)?Intention\s*:\s*(?P<intention>.*?)\s*)?'
    r'(?:(?:[\*\-]\s*)?R[eéè]gle\s*(?:5e)?\s*:\s*(?P<regle>.*?)\s*)?'
    r'(?:(?:[\*\-]\s*)?Cible(?:s)?\s*:\s*(?P<cible>.*?))?(?=\n\s*\n|\[ACTION\]|</thought>|$)',
    _re.IGNORECASE | _re.DOTALL
)
# ────────────────────────────────────────────────────────────────────────────


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
        return[]

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
        # Supprimer les pensées du modèle (DeepSeek/Gemma 4)
        text = _re_strip.sub(r"<thought>.*?(?:</thought>|$)", "", text, flags=_re_strip.IGNORECASE | _re_strip.DOTALL)
        text = _re_strip.sub(r"<think>.*?(?:</think>|$)", "", text, flags=_re_strip.IGNORECASE | _re_strip.DOTALL)
        # Supprimer les marqueurs mécaniques qui n'ont rien à faire dans le chat
        text = _re_strip.sub(r"\[SILENCE\]",     "", text)
        text = _re_strip.sub(r"\[RÈGLES DU BLOC ACTION[^\]]*\](?:\s*•[^\n]*)*", "", text, flags=_re_strip.IGNORECASE)
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
            return [text] if text else[]
        _ABBREVS = r"(?:M|Mme|Dr|Prof|St|Ste|Mr|Jr|Sr|vol|p|pp|art|no|No|fig|cf|vs|env|hab|av|apr|J\.-C|etc)\."
        protected = _re_s.sub(_ABBREVS, lambda m: m.group().replace(".", "\x00"), text)
        parts = _re_s.split(r'(?<=[.!?;])\s+(?=[A-ZÀÂÄÉÈÊËÎÏÔÙÛÜÇ"«\u2019])', protected)
        parts =[p.replace("\x00", ".").strip() for p in parts if p.strip()]
        merged =[]
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

    # ── Helpers ressources de tour ────────────────────────────────────────────

    def _get_char_speed_ft(char_name: str) -> int:
        """Vitesse de déplacement en pieds (défaut 30 ft / 6 cases)."""
        try:
            return int(_CM.get(char_name, {}).get("speed", 30))
        except Exception:
            return 30

    def _get_turn_res(char_name: str) -> dict:
        """
        Retourne le dict de ressources pour le tour en cours de char_name.
        Réinitialise automatiquement dès que c'est un nouveau combattant actif.
        """
        _tr_dict = COMBAT_STATE.setdefault("turn_res", {})
        if _tr_dict.get("_last_initialized") != char_name:
            _tr_dict["_last_initialized"] = char_name
            _tr_dict[char_name] = {
                "action":      True,
                "bonus":       True,
                "movement_ft": _get_char_speed_ft(char_name),
                "sneak_used":  False,
            }
        return _tr_dict.setdefault(char_name, {
            "action":      True,
            "bonus":       True,
            "movement_ft": _get_char_speed_ft(char_name),
            "sneak_used":  False,
        })

    def _consume_turn_res(char_name: str, type_label: str, movement_ft: int = 0):
        """Marque une ressource comme consommée pour ce tour."""
        _tr = _get_turn_res(char_name)
        _lbl = type_label.lower()
        if "mouvement" in _lbl:
            _tr["movement_ft"] = max(0, _tr["movement_ft"] - movement_ft)
        elif "bonus" in _lbl:
            _tr["bonus"] = False
        elif "réaction" in _lbl or "reaction" in _lbl:
            _ru = COMBAT_STATE.setdefault("reactions_used", set())
            if char_name not in _ru:
                _ru.add(char_name)
                _app.msg_queue.put({
                    "sender": "⚔️ Combat",
                    "text":   f"↺ {char_name} — réaction consommée pour ce round.",
                    "color":  "#5588cc"
                })
        else:
            _tr["action"] = False

    def _build_tour_en_cours(char_name: str) -> str:
        """Construit le message[TOUR EN COURS] avec les ressources restantes."""
        _tr     = _get_turn_res(char_name)
        _mv_rem = _tr["movement_ft"]
        _react  = char_name not in COMBAT_STATE.get("reactions_used", set())
        _speed  = _get_char_speed_ft(char_name)

        # --- Détermination des options suggérées dynamiques ---
        _action_options =["Attaque physique (Mêlée ou Distance)"]
        _bonus_options =[]
        
        # Capacités spécifiques (Action Masking)
        if char_name == "Thorne":
            _bonus_options.append("Action Cunning (Se Cacher, Foncer, Se Désengager en Action Bonus)")
        elif char_name == "Kaelen":
            _action_options.append("Imposition des mains (Soin)")
            _action_options.append("Renvoi des Impies (Turn the Unholy)")
        elif char_name == "Lyra":
            _action_options.append("Renvoi des Morts-Vivants (Turn Undead)")
            
        # Extraction dynamique des sorts préparés
        try:
            from engine_spell_mj import get_prepared_spell_names as _gpsn
            from spell_data import get_spell as _get_sp
            _prep = _gpsn(char_name)
            _act_spells = []
            _ba_spells =[]
            for s in _prep:
                _sp = _get_sp(s)
                if _sp:
                    _ct = _sp.get("cast_time_raw", [{}])[0].get("unit", "action").lower()
                    if "bonus" in _ct:
                        _ba_spells.append(s)
                    elif "action" in _ct:
                        _act_spells.append(s)
            if _act_spells:
                _action_options.append(f"Lancer un sort : {', '.join(_act_spells)}")
            if _ba_spells:
                _bonus_options.append(f"Lancer un sort : {', '.join(_ba_spells)}")
        except Exception:
            pass

        # Options génériques
        _action_options.extend(["Foncer (Dash, +{_speed} ft)", "Esquiver (Dodge)", "Se Tenir Prêt (Ready Action)", "Se Désengager (Disengage)", "Aider (Help)"])
        
        if _tr["action"]:
            _opts_a = "\n    - ".join([opt.replace("{_speed}", str(_speed)) for opt in _action_options])
            a_str  = f"✅ disponible\n    Options suggérées :\n    - {_opts_a}"
        else:
            a_str  = "❌ utilisée"

        if _tr["bonus"]:
            if not _bonus_options:
                _bonus_options.append("Aucune action bonus spécifique dans ta liste (dépend de la situation/objets)")
            _opts_b = "\n    - ".join(_bonus_options)
            b_str  = f"✅ disponible\n    Options suggérées :\n    - {_opts_b}"
        else:
            b_str  = "❌ utilisée"

        r_str  = "✅ disponible" if _react else "❌ utilisée"
        
        try:
            _st_tec = load_state()
            _slots_tec = _st_tec.get("characters", {}).get(char_name, {}).get("spell_slots", {})
            if _slots_tec:
                _slots_avail =[f"Niv.{lvl}×{nb}" for lvl, nb in sorted(_slots_tec.items(), key=lambda x: int(x[0])) if int(nb) > 0]
                s_str = " | ".join(_slots_avail) if _slots_avail else "❌ TOUS LES SORTS SONT ÉPUISÉS"
            else:
                s_str = "— (Aucun emplacement de sort)"
        except Exception:
            s_str = "—"

        if _mv_rem > 0:
            mv_str = f"✅ {_mv_rem} ft ({_mv_rem // 5} cases)[Vitesse de base: {_speed} ft]"
        else:
            mv_str = "❌ épuisé"

        _any_left = _tr["action"] or _tr["bonus"] or _mv_rem > 0
        _any_action_left = _tr["action"] or _tr["bonus"]
        next_instr = (
            "Déclare ta prochaine action avec UN seul bloc [ACTION], ET UNE SEULE CHOSE À LA FOIS (ex: 1 mouvement OU 1 attaque)."
            if _any_action_left else
            (
                f"Il te reste {_mv_rem} ft de déplacement."
                " Déclare un [ACTION] Type: Mouvement si tu veux bouger,"
                if _mv_rem > 0 else
                "Toutes tes ressources sont épuisées. Envoie une [ACTION] de type 'Fin de tour' pour terminer ton tour."
            )
        )
        
        if _any_left and _tr["action"]:
            # Conseil contextuel : si mouvement épuisé et ennemi hors portée, guider vers Dash ou alternatives
            _dash_hint_tec = ""
            if _mv_rem == 0:
                try:
                    import os as _os_tec, json as _json_tec, math as _math_tec
                    _map_data_tec = {}
                    _act_map_tec = _app._win_state.get("active_map_name", "")
                    if _act_map_tec:
                        try:
                            from app_config import get_campaign_name as _gcn_tec
                            _camp_tec = _gcn_tec()
                        except Exception:
                            _camp_tec = "campagne"
                        _camp_tec = "".join(c for c in _camp_tec if c.isalnum() or c in (" ", "-", "_")).strip() or "campagne"
                        _safe_tec = "".join(c for c in _act_map_tec if c.isalnum() or c in (" ", "-", "_")).strip() or "carte"
                        _mp_tec = _os_tec.path.join("campagne", _camp_tec, "maps", f"{_safe_tec}.json")
                        if _os_tec.path.exists(_mp_tec):
                            with open(_mp_tec, "r", encoding="utf-8") as _f_tec:
                                _map_data_tec = _json_tec.load(_f_tec)
                    if not _map_data_tec:
                        _map_data_tec = _app._win_state.get("combat_map_data", {})
                    _toks_tec = _map_data_tec.get("tokens",[])
                    _hero_tec = None
                    _nearest_d = 9999.0
                    _nearest_n = ""
                    for _t in _toks_tec:
                        if (_t.get("name") or "").lower() == char_name.lower():
                            _hero_tec = _t
                    if _hero_tec:
                        _hc = int(round(_hero_tec.get("col", 0)))
                        _hr = int(round(_hero_tec.get("row", 0)))
                        for _t in _toks_tec:
                            if _t.get("type") == "monster":
                                _mc = int(round(_t.get("col", 0)))
                                _mr = int(round(_t.get("row", 0)))
                                _h = max(abs(_hc - _mc), abs(_hr - _mr)) * 5.0
                                _da = abs(int(_hero_tec.get("altitude_ft", 0)) - int(_t.get("altitude_ft", 0)))
                                _dd = max(float(_h), float(_da))
                                if _dd < _nearest_d:
                                    _nearest_d = _dd
                                    _nearest_n = _t.get("name", "ennemi")
                        if _nearest_d <= 10.0:
                            _dash_hint_tec = f"\n💡 Tu es déjà à portée de mêlée de {_nearest_n} ({_nearest_d:.0f} ft) — ATTAQUE !"
                        elif _nearest_d <= 5.0 + _speed:
                            _dash_hint_tec = (
                                f"\n💡 Ennemi le plus proche : {_nearest_n} à {_nearest_d:.0f} ft. "
                                f"FONCE (Dash, +{_speed} ft) pour le rejoindre et attaquer ensuite !"
                            )
                        else:
                            _dash_hint_tec = (
                                f"\n💡 Ennemi le plus proche : {_nearest_n} à {_nearest_d:.0f} ft. "
                                f"Foncer (Dash) te rapproche de {_speed} ft (tu attaqueras au prochain tour)."
                            )
                except Exception:
                    pass
            if _dash_hint_tec:
                next_instr += _dash_hint_tec

        # ── Concentration active ──
        conc_str = ""
        try:
            _trk = (
                getattr(_app, "_combat_tracker_win", None)
                or getattr(_app, "_combat_tracker", None)
            )
            if _trk and hasattr(_trk, "combatants"):
                for _cb in _trk.combatants:
                    if _cb.name == char_name and _cb.concentration and _cb.conc_spell:
                        conc_str = (
                            f"\n  🔮 Concentration : {_cb.conc_spell} "
                            f"({_cb.conc_rounds_left} tour(s) restant(s))\n"
                            f"     ⚠️ Lancer un autre sort à concentration "
                            f"mettra fin à {_cb.conc_spell}."
                        )
                        break
        except Exception:
            pass

        return (
            f"[TOUR EN COURS — {char_name}]\n"
            f"  Action       : {a_str}\n"
            f"  Action Bonus : {b_str}\n"
            f"  Déplacement  : {mv_str}\n"
            f"  Réaction     : {r_str}\n"
            f"  Sorts dispos : {s_str}"
            + conc_str + "\n\n"
            + next_instr
        )

    # ── Helper : narrative d'action publique pour le GroupChat ───────────────
    # Remplace [TOUR EN COURS] dans le GroupChat (visible par TOUS les agents).
    # Règle : le GroupChat ne doit contenir que des informations observables
    # par tous les personnages. Les ressources de tour (Action/Bonus/Déplacement)
    # sont privées → elles restent dans le system_message de l'agent actif,
    # mis à jour par _rebuild_agent_prompts() / get_combat_prompt().

    def _build_action_narrative(name: str, type_lbl: str, intention: str,
                                cible: str, feedback: str = "") -> str:
        """
        Construit une ligne narrative pour l'historique de combat et le chat.
        Exemple : "Thorne [action] : attaque l'Erinyes avec sa dague et porte un coup dévastateur."
        """
        t = (type_lbl or "").lower()
        if "bonus" in t:
            slot = "action bonus"
        elif "mouvement" in t or "déplace" in t or "move" in t:
            slot = "mouvement"
        elif "réaction" in t or "reaction" in t:
            slot = "réaction"
        else:
            slot = "action"

        intention_clean = (intention or "").strip().rstrip(".")
        cible_part = f" sur {cible.strip()}" if cible and cible.strip() else ""

        fb = feedback or ""
        if "ATTAQUE RÉSOLUE" in fb or "ATTAQUE DE SORT RÉSOLUE" in fb:
            result = " et porte un coup dévastateur."
        elif "ATTAQUE RATÉE" in fb or "ATTAQUE DE SORT RATÉE" in fb:
            result = " mais rate sa cible de peu."
        elif "RÉSULTAT SYSTÈME — SOIN" in fb:
            result = " et restaure des points de vie."
        elif "IMPOSSIBLE" in fb:
            result = " (action échouée ou bloquée)."
        elif "RÉSOLUE" in fb or "CONFIRMÉE" in fb:
            result = " avec succès."
        elif "SAUVEGARDE RÉUSSIE" in fb:
            result = " mais la cible résiste."
        elif "SAUVEGARDE RATÉE" in fb:
            result = " et la cible subit l'effet de plein fouet."
        else:
            result = "."

        narrative = f"• {name}[{slot}] : {intention_clean}{cible_part}{result}"
        
        # Ajout à l'historique de combat limité
        try:
            from combat_tracker_state import add_combat_history
            add_combat_history(narrative)
        except Exception:
            pass
        
        return narrative

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

        if content and name in PLAYER_NAMES:
            _thoughts = _re.findall(r'<(?:thought|think)>(.*?)(?:</(?:thought|think)>|$)', str(content), flags=_re.IGNORECASE | _re.DOTALL)
            if _thoughts:
                print(f"\n[{name} THOUGHTS]:")
                for t in _thoughts:
                    if t.strip():
                        print(f"  {t.strip()}")

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
                # Après 1 retry sans résultat (comportement normal de Gemini/Groq qui
                # retourne content=null lors d'un tool_call), on injecte une narration
                # synthétique pour roll_dice afin d'éviter la boucle infinie.
                _has_narrative = content and str(content).strip() not in ("", "[SILENCE]")
                if not _has_narrative and name in PLAYER_NAMES:
                    _g4_key = f"_g4_narr_{name}"
                    _g4_retry = ctx.tool_refusal_strikes.get(_g4_key, 0)

                    if _fn_name == "roll_dice" and _g4_retry >= 1:
                        # 2e tentative sans narrative : le modèle ne peut pas combiner
                        # texte + tool_call. On injecte une narration synthétique courte
                        # et on laisse passer l'appel roll_dice.
                        ctx.tool_refusal_strikes[_g4_key] = 0
                        _synth_text = f"*{name} ferme les yeux un instant et se concentre.*"
                        _app.msg_queue.put({
                            "sender": name,
                            "text":   _synth_text,
                            "color":  _app.CHAR_COLORS.get(name, "#e0e0e0"),
                        })
                        _original_receive(
                            self_mgr,
                            {"role": "assistant", "content": _synth_text, "name": name},
                            sender, request_reply=False, silent=True,
                        )
                        # Laisser le flux continuer vers « Appel légitime »
                    else:
                        ctx.tool_refusal_strikes[_g4_key] = _g4_retry + 1
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

        # ── FILTRE COMBAT : parole hors-tour trop longue (> 15 mots) ─────────
        # Appliqué avant affichage pour éviter qu'un héros narre un paragraphe
        # complet pendant le tour d'un ennemi ou d'un autre PJ.
        # Autorisé : [SILENCE], blocs [ACTION] réaction, jets demandés par le MJ.
        _is_offturn_verbose = (
            not is_system
            and not is_mj_roll_response
            and COMBAT_STATE["active"]
            and name in PLAYER_NAMES
            and name != COMBAT_STATE.get("active_combatant")
            and content
            and str(content).strip() not in ("[SILENCE]", "")
            and not ACTION_PATTERN.search(str(content))
            and name not in getattr(ctx, "pending_damage_narrators", set())
            and len(str(content).split()) > 15
        )
        if _is_offturn_verbose:
            _block_msg = (
                f"[SYSTÈME — HORS TOUR — PAROLE TROP LONGUE]\n"
                f"Ce n'est pas le tour de {name}. "
                f"Ta prise de parole hors-tour est limitée à UNE phrase courte (max ~10 mots).\n"
                f"Message masqué. Reformule en une seule phrase ou envoie [SILENCE]."
            )
            _app.msg_queue.put({"sender": "⚔️ Combat", "text": _block_msg, "color": "#cc4422"})
            _original_receive(
                self_mgr,
                {"role": "user", "content": _block_msg, "name": "Alexis_Le_MJ"},
                sender, request_reply=False, silent=True,
            )
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
            and not _is_reaction_block   # <--- AJOUT : On laisse passer si c'est une réaction !
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

        # ── INTERCEPTION SORT [SORT:] désactivée — tous les sorts passent par [ACTION] ──
        if False and (not is_system
                and name in SPELL_CASTERS
                and content
                and SORT_PATTERN.search(str(content))):
            m = SORT_PATTERN.search(str(content))
            spell_name  = m.group("nom").strip()
            spell_level = int(m.group("niveau"))
            target      = (m.group("cible") or "").strip()
            clean_content = SORT_PATTERN.sub("", str(content)).strip()

            # ── Garde-fou : [SORT:] + [ACTION] dans le même message ─────────
            # L'agent ne peut déclarer qu'une seule décision par message.
            # Si un bloc [ACTION] accompagne le tag [SORT:], on le retire du
            # contenu affiché et on injecte une correction après résolution.
            _combo_action_stripped = ACTION_PATTERN.search(clean_content)
            if _combo_action_stripped:
                clean_content = ACTION_PATTERN.sub("", clean_content).strip()
                _app.msg_queue.put({
                    "sender": "⚠️ Règle",
                    "text": (
                        f"[COMBO INTERDIT — {name}]\n"
                        f"{name} a déclaré un tag [SORT:] ET un bloc [ACTION] dans le même message.\n"
                        f"RÈGLE : une seule décision par message en combat.\n"
                        f"Le bloc [ACTION] a été ignoré — il sera redemandé après résolution du sort."
                    ),
                    "color": "#e67e22",
                })

            if not is_spell_prepared(name, spell_name):
                _avail3 = get_prepared_spell_names(name)
                _avail3_str = ", ".join(_avail3) if _avail3 else "aucun sort préparé trouvé"
                _not_prepared_msg = (
                    f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE — {name}]\n"
                    f"{spell_name} n'est pas dans la liste de sorts préparés de {name}. "
                    f"Ce sort ne peut pas être lancé aujourd'hui.\n\n"
                    f"[SORTS AUTORISÉS POUR {name.upper()}]\n{_avail3_str}\n\n"
                    f"[INSTRUCTION]\nChoisis UNIQUEMENT parmi les sorts listés ci-dessus. "
                    f"Ne tente PAS de lancer {spell_name} — déclare une nouvelle action avec[ACTION]."
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
                    name, spell_name, spell_level, _sp_data.get("cast_time_raw",[]), COMBAT_STATE.get("turn_spells",[])
                )
                if not _valid_ba:
                    _not_ba_msg = (
                        f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE — {name}]\n"
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
                    spell_name, _sp_data.get("cast_time_raw",[])
                )
                if not _valid_ct:
                    _not_ct_msg = (
                        f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE — {name}]\n"
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
                                f"  → Fais un nouveau bloc [ACTION] en précisant le niveau voulu dans 'Règle 5e' (ex: {spell_name} niv.{_supers[0]})."
                            )
                        else:
                            _upcast_hint = (
                                f"\n  Aucun emplacement de niveau supérieur disponible non plus."
                            )
                        _no_slot_msg = (
                            f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE — {name}]\n"
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

                # ── Concentration : activer automatiquement ──
                if _sp_data and _sp_data.get("concentration", False):
                    try:
                        from spell_data import get_concentration_rounds as _gcr2
                        _conc_rounds2 = _gcr2(spell_name)
                        if _conc_rounds2 > 0:
                            _trk2 = getattr(_app, "_combat_tracker_win", None)
                            if _trk2 and hasattr(_trk2, "_apply_concentration"):
                                for _cb2 in _trk2.combatants:
                                    if _cb2.name == name:
                                        _app.root.after(0, lambda c=_cb2, s=spell_name, r=_conc_rounds2: _trk2._apply_concentration(c, s, r))
                                        break
                    except Exception as _conc_err2:
                        print(f"[Concentration] Erreur auto-apply (spell_confirm) : {_conc_err2}")

                if _sp_data:
                    _unit = _sp_data.get("cast_time_raw", [{}])[0].get("unit", "") if _sp_data.get("cast_time_raw") else ""
                    COMBAT_STATE.setdefault("turn_spells",[]).append({
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

                    # NOTE : engine_mechanics émet "[RÉSULTAT SYSTÈME — SOIN — {char_name}]"
                    # Le startswith doit s'arrêter AVANT le "]" final pour matcher
                    # avec n'importe quel nom de personnage interpolé dans le header.
                    _is_healing_spell = _fb.startswith("[RÉSULTAT SYSTÈME — SOIN")
                    _is_save_spell    = _fb.startswith("[RÉSULTAT SYSTÈME — JET DE SAUVEGARDE")

                    if _is_save_spell:
                        # ── Boîte de confirmation JET DE SAUVEGARDE ───────────
                        _split_sv = "\n\n[INSTRUCTION NARRATIVE]"
                        _save_results_part = _fb.split(_split_sv)[0].strip()
                        # Supprimer le header "[RÉSULTAT SYSTÈME — JET DE SAUVEGARDE — CharName]"
                        # (le header contient le nom du perso → simple replace ne suffit pas)
                        _save_results_part = _re.sub(
                            r"^\[RÉSULTAT SYSTÈME — JET DE SAUVEGARDE[^\]]*\]\n?", "",
                            _save_results_part
                        ).strip()
                        # Extraire le total dégâts annoté par engine_mechanics
                        _sv_dmg_m = _re.search(r"\[__save_dmg_total__:(\d+)\]", _save_results_part)
                        _sv_dmg_total = int(_sv_dmg_m.group(1)) if _sv_dmg_m else 0
                        # Nettoyer l'annotation interne de l'affichage
                        _save_results_display = _re.sub(r"\[__save_dmg_total__:\d+\]\n?", "", _save_results_part).strip()

                        _sv_ev  = _threading.Event()
                        _sv_res: dict = {}

                        def _sv_cb(target_saved, mj_note_sv="", _ev=_sv_ev, _res=_sv_res):
                            _app._unregister_approval_event(_ev)
                            _res["saved"]  = target_saved
                            _res["note"]   = mj_note_sv
                            _ev.set()

                        _app._register_approval_event(_sv_ev)
                        _app.msg_queue.put({
                            "action":          "result_confirm",
                            "char_name":       name,
                            "type_label":      f"Sort — {spell_name}",
                            "results_text":    _save_results_display,
                            "mode":            "save",
                            "resume_callback": _sv_cb,
                        })
                        _sv_ev.wait(timeout=600)
                        _app._unregister_approval_event(_sv_ev)

                        _target_saved  = _sv_res.get("saved", False)
                        _sv_mj_note    = _sv_res.get("note", "")

                        def _apply_save_damage(cible_sv, dmg_sv, label_sv):
                            """Affiche damage_link et applique au PNJ."""
                            _dl_ev  = _threading.Event()
                            _dl_res: dict = {}
                            def _dl_cb(final, target_val=None, mj_note="", _ev=_dl_ev, _res=_dl_res):
                                _app._unregister_approval_event(_ev)
                                _res["amount"] = final
                                _res["target"] = target_val
                                _res["note"]   = mj_note
                                _ev.set()
                            _app._register_approval_event(_dl_ev)
                            _app.msg_queue.put({
                                "action":          "damage_link",
                                "sender":          name,
                                "char_name":       name,
                                "cible":           cible_sv,
                                "dmg_text":        label_sv,
                                "dmg_total":       dmg_sv,
                                "is_crit":         False,
                                "resume_callback": _dl_cb,
                            })
                            _dl_ev.wait(timeout=300)
                            _app._unregister_approval_event(_dl_ev)
                            _final_d = _dl_res.get("amount", dmg_sv)
                            _final_tgt = _dl_res.get("target") or cible_sv
                            try:
                                if getattr(_app, "_combat_tracker_win", None) is not None:
                                    _app._combat_tracker_win.apply_damage_to_npc(_final_tgt, _final_d)
                            except Exception as _dap_err:
                                print(f"[SaveDamageApply] {_dap_err}")
                            try:
                                from combat_tracker_state import add_combat_history
                                add_combat_history(f"  → {_final_tgt} subit {_final_d} dégâts.")
                                if hasattr(_app, "_update_agent_combat_prompts"): _app._update_agent_combat_prompts()
                            except Exception:
                                pass
                            return _final_d, _dl_res.get("note", "")

                        try:
                            from combat_tracker_state import add_combat_history
                            if _target_saved:
                                add_combat_history(f"  → {_sub.get('cible', 'La cible')} a réussi sa sauvegarde !")
                            else:
                                add_combat_history(f"  → {_sub.get('cible', 'La cible')} a raté sa sauvegarde.")
                            if hasattr(_app, "_update_agent_combat_prompts"): _app._update_agent_combat_prompts()
                        except Exception:
                            pass
                            
                        if _target_saved:
                            # Sauvegarde RÉUSSIE → vérifier si le sort inflige des demi-dégâts
                            _is_half_on_save = _sp_data.get("half_on_save", False) if _sp_data else False
                            _half = (_sv_dmg_total // 2) if _is_half_on_save else 0
                            if _half > 0:
                                _applied, _dl_note = _apply_save_damage(
                                    target or "Cible",
                                    _half,
                                    f"Sauvegarde réussie — demi-dégâts ({_sv_dmg_total}÷2)",
                                )
                                _fb = (
                                    "[RÉSULTAT SYSTÈME — SAUVEGARDE RÉUSSIE]\n"
                                    + _save_results_display
                                    + "\n  → SAUVEGARDE RÉUSSIE ✅ (sort raté)"
                                    + (f"\n  Note MJ (Sauvegarde) : {_sv_mj_note}" if _sv_mj_note else "")
                                    + f"\n  Demi-dégâts appliqués : {_applied}"
                                    + (f"\n  Note MJ (Dégâts) : {_dl_note}" if _dl_note else "")
                                    + "\n\n[INSTRUCTION NARRATIVE]\n"
                                    + f"La cible a résisté. Narre en 1-2 phrases comment {target or 'la cible'} "
                                    + f"résiste partiellement au sort de {name}. Ne mentionne pas les chiffres."
                                )
                            else:
                                _fb = (
                                    "[RÉSULTAT SYSTÈME — SAUVEGARDE RÉUSSIE]\n"
                                    + _save_results_display
                                    + "\n  → SAUVEGARDE RÉUSSIE ✅ (sort raté — aucun effet)"
                                    + (f"\n  Note MJ : {_sv_mj_note}" if _sv_mj_note else "")
                                    + "\n\n[INSTRUCTION NARRATIVE]\n"
                                    + f"La cible a esquivé le sort. Narre en 1-2 phrases comment "
                                    + f"{target or 'la cible'} résiste ou esquive le sort de {name}."
                                )
                        else:
                            # Sauvegarde RATÉE → sort touché
                            if _sv_dmg_total > 0:
                                _applied, _dl_note = _apply_save_damage(
                                    target or "Cible",
                                    _sv_dmg_total,
                                    f"Sauvegarde ratée — dégâts pleins",
                                )
                                _fb = (
                                    "[RÉSULTAT SYSTÈME — SAUVEGARDE RATÉE]\n"
                                    + _save_results_display
                                    + "\n  → SAUVEGARDE RATÉE ❌ (sort touché)"
                                    + (f"\n  Note MJ (Sauvegarde) : {_sv_mj_note}" if _sv_mj_note else "")
                                    + f"\n  Dégâts appliqués : {_applied}"
                                    + (f"\n  Note MJ (Dégâts) : {_dl_note}" if _dl_note else "")
                                    + "\n\n[INSTRUCTION NARRATIVE]\n"
                                    + f"La cible a raté son jet. Narre en 1-2 phrases l'impact du sort "
                                    + f"de {name} sur {target or 'la cible'}. Ne mentionne pas les chiffres."
                                )
                            else:
                                _fb = (
                                    "[RÉSULTAT SYSTÈME — SAUVEGARDE RATÉE]\n"
                                    + _save_results_display
                                    + "\n  → SAUVEGARDE RATÉE ❌ (sort touché — effets actifs)"
                                    + (f"\n  Note MJ : {_sv_mj_note}" if _sv_mj_note else "")
                                    + "\n\n[INSTRUCTION NARRATIVE]\n"
                                    + f"La cible a raté son jet. Le sort de {name} fait plein effet sur "
                                    + f"{target or 'la cible'}. Narre l'effet en 1-2 phrases."
                                )

                        _original_receive(
                            self_mgr,
                            {"role": "user", "content": _fb, "name": "Alexis_Le_MJ"},
                            sender, request_reply=False, silent=True,
                        )

                    elif _is_healing_spell:
                        # ── Boîte de confirmation SOIN (verte) ────────────
                        _split_mk = "\n\n[INSTRUCTION NARRATIVE]"
                        _heal_results_part = (
                            _fb.split(_split_mk)[0]
                            .replace("[RÉSULTAT SYSTÈME — SOIN]\n", "")
                            .strip()
                        )

                        _heal_ev = _threading.Event()
                        _heal_note: dict = {}

                        def _heal_cb(mj_note_heal="", _ev=_heal_ev, _res=_heal_note):
                            _app._unregister_approval_event(_ev)
                            _res["note"] = mj_note_heal
                            _ev.set()

                        _app._register_approval_event(_heal_ev)
                        _app.msg_queue.put({
                            "action": "result_confirm", "char_name": name,
                            "type_label": f"Sort — {spell_name}",
                            "results_text": _heal_results_part,
                            "mode": "healing", "resume_callback": _heal_cb,
                        })
                        _heal_ev.wait(timeout=600)
                        _app._unregister_approval_event(_heal_ev)

                        _heal_mj = _heal_note.get("note", "")
                        if _heal_mj:
                            _fb += f"\n[Modification MJ] {_heal_mj}"

                        _original_receive(
                            self_mgr,
                            {"role": "user", "content": _fb, "name": "Alexis_Le_MJ"},
                            sender, request_reply=False, silent=True,
                        )
                    else:
                        _app.msg_queue.put({"sender": "⚙️ Système", "text": _fb, "color": "#a89f91"})
                        _original_receive(
                            self_mgr,
                            {"role": "user", "content": _fb, "name": "Alexis_Le_MJ"},
                            sender, request_reply=False, silent=True,
                        )
                except Exception as _exec_err:
                    print(f"Erreur exec sort auto: {_exec_err}")

                # Consommer l'Action ou l'Action Bonus
                if _sp_data:
                    _unit = _sp_data.get("cast_time_raw", [{}])[0].get("unit", "action") if _sp_data.get("cast_time_raw") else "action"
                    _consume_turn_res(name, _unit)
                    _app._update_agent_combat_prompts()
                else:
                    _consume_turn_res(name, "action")
                    _app._update_agent_combat_prompts()

                # ── Narrative publique (visible tous agents) + trigger de tour ──
                # Le tableau [TOUR EN COURS] (ressources) reste dans le system_message
                # de l'agent actif (mis à jour par _rebuild_agent_prompts ci-dessus).
                # Le GroupChat reçoit UNIQUEMENT une narrative lisible + un trigger.
                if COMBAT_STATE["active"] and name == COMBAT_STATE.get("active_combatant"):
                    _tec_msg  = _build_tour_en_cours(name)
                    _tr       = _get_turn_res(name)
                    _has_res  = _tr["action"] or _tr["bonus"]

                    # [UI MJ] : tableau complet dans l'interface (pas dans le GroupChat)
                    _app.msg_queue.put({"sender": "⚔️ Combat", "text": _tec_msg, "color": "#5577aa"})

                    #[GroupChat public] : narrative de l'action pour tous les agents
                    _sort_type = "action bonus" if (_unit and "bonus" in str(_unit).lower()) else "action"
                    _narr_sort = _build_action_narrative(
                        name, _sort_type,
                        f"lancer {spell_name} (niv.{_final_level})",
                        target, _fb if "_fb" in dir() else "",
                    )
                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": _narr_sort, "name": "Alexis_Le_MJ"},
                        sender, request_reply=False, silent=False,
                    )

                    # [Trigger de continuation] : simple, sans données de ressources
                    _app._pending_combat_trigger = (
                        f"Tu as encore des actions disponibles. Continue ton tour, {name}."
                        if _has_res else
                        f"{name}, plus d'actions disponibles. Envoie [ACTION] de type 'Fin de tour' ou déclare un mouvement."
                    )
                    _gc_trigger_base = (
                        f"Continue ton tour, {name}."
                        if _has_res else
                        f"{name}, plus d'actions disponibles. Envoie [ACTION] de type 'Fin de tour' ou déclare un mouvement."
                    )
                    # Si l'agent avait combiné [SORT:] + [ACTION] dans son message,
                    # rappeler la règle et redemander la décision ignorée séparément.
                    if _combo_action_stripped:
                        _gc_trigger = (
                            _gc_trigger_base
                            + f"\n\n[RAPPEL RÈGLE] Tu avais inclus un bloc [ACTION] avec ton sort — il a été ignoré."
                            f"\nDéclare UN SEUL bloc [ACTION] par message. Si tu veux te déplacer, fais-le maintenant en UN seul message dédié."
                        )
                    else:
                        _gc_trigger = _gc_trigger_base

                    _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": _gc_trigger, "name": "Alexis_Le_MJ"},
                        sender,
                        request_reply=True,
                        silent=False,
                    )
                    return

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

        # ── INTERCEPTION ACTIONS[ACTION] ─────────────────────────────────────
        if (not is_system
                and name in PLAYER_NAMES
                and content
                and ACTION_PATTERN.search(str(content))):

            # ── GARDE-FOU ANTI-GROUPEMENT D'ATTAQUES (EXTRA ATTACK) ────────────
            _content_str = str(content)
            _lower_c = _content_str.lower()
            if _re.search(r'attaque\s*\d+\s*:', _content_str, _re.IGNORECASE) or "attaque 1" in _lower_c or "attaque 2" in _lower_c or "× 2" in _lower_c or "x 2" in _lower_c:
                _anti_group_msg = (
                    f"[DIRECTIVE SYSTÈME — FORMAT INCORRECT]\n"
                    f"{name} : TU AS GROUPÉ PLUSIEURS ATTAQUES DANS LE MÊME MESSAGE.\n\n"
                    f"RÈGLE ABSOLUE : Tu ne peux faire qu'UNE SEULE attaque par bloc [ACTION].\n"
                    f"Même si tu as Extra Attack, tu dois :\n"
                    f"1. Lancer ta première attaque.\n"
                    f"2. ATTENDRE le résultat du MJ.\n"
                    f"3. Lancer ta deuxième attaque dans un NOUVEAU message séparé.\n\n"
                    f"Recommence ton message avec UNIQUEMENT ta première attaque."
                )
                print(f"===========================================================")
                print(f"[DEBUG ANTI-GROUPE] Violation déclenchée pour {name}")
                print(f"[DEBUG ANTI-GROUPE] Contenu brut du message :")
                print(_content_str)
                print(f"===========================================================")
                _app.msg_queue.put({"sender": "⚠️ Règle", "text": f"[VIOLATION] {name} a groupé ses attaques. Message bloqué et relancé.", "color": "#F44336"})
                _original_receive(self_mgr, {"role": "user", "content": _anti_group_msg, "name": "Alexis_Le_MJ"}, sender, request_reply=True, silent=True)
                return
            # ───────────────────────────────────────────────────────────────────

            # Remove thoughts BEFORE looking for the final action block
            import re as _re_tmp
            _content_no_thoughts = _re_tmp.sub(r'<(?:thought|think)>.*?(?:</(?:thought|think)>|$)', "", str(content), flags=_re_tmp.IGNORECASE | _re_tmp.DOTALL)
            
            clean_content = ACTION_PATTERN.sub("", _content_no_thoughts).strip()
            if clean_content and clean_content != "[SILENCE]":
                clean_content = _strip_stars(clean_content)
                _app.msg_queue.put({
                    "sender": name, "text": clean_content,
                    "color":  _app.CHAR_COLORS.get(name, "#e0e0e0"),
                })
                log_tts_start(name, clean_content)
                _enqueue_tts(clean_content, name)

            # ── Ne traiter QUE le premier bloc [ACTION] (hors pensées) ────────
            # Les blocs suivants (s'il y en a) sont ignorés : le système enverra
            # un [TOUR EN COURS] après confirmation, et l'agent déclarera
            # sa prochaine action dans un nouveau message.
            _first_match = next(ACTION_PATTERN.finditer(_content_no_thoughts), None)
            if _first_match is None:
                _original_receive(self_mgr, message, sender, request_reply, silent)
                return

            _type_lbl  = (_first_match.group("type")      or "").strip().strip("'\"") or "Action"
            _intention = (_first_match.group("intention") or "").strip()
            _regle     = (_first_match.group("regle")     or "").strip()
            _cible     = (_first_match.group("cible")     or "").strip()

            # ── GARDE-FOU : cible morte (Kill Pool) ──────────────────────────
            # Si l'agent cible un combattant déjà retiré du combat, on bloque
            # l'action immédiatement et on lui indique les ennemis encore vivants.
            # Cas typique : Extra Attack déclarée après que la cible a été tuée
            # par la première attaque du même tour.
            if _cible and COMBAT_STATE.get("active"):
                try:
                    _trk_kp = (
                        getattr(_app, "_combat_tracker_win", None)
                        or getattr(_app, "_combat_tracker", None)
                    )
                    if _trk_kp and hasattr(_trk_kp, "kill_pool"):
                        _dead_names = [c.name for c in _trk_kp.kill_pool]
                        _matched_dead = next(
                            (d for d in _dead_names
                             if d.lower() in _cible.lower()
                             or _cible.lower() in d.lower()),
                            None,
                        )
                        if _matched_dead:
                            _alive_enemies = [
                                c.name for c in _trk_kp.combatants if not c.is_pc
                            ]
                            _alive_str = (
                                ", ".join(_alive_enemies)
                                if _alive_enemies
                                else "aucun ennemi restant en vie"
                            )
                            _dead_tgt_msg = (
                                f"[RÉSULTAT SYSTÈME — CIBLE IMPOSSIBLE — {name}]\n"
                                f"⚠️ {_matched_dead} est MORT(E) et retiré(e) du combat.\n"
                                f"Tu ne peux pas cibler un combattant hors combat.\n\n"
                                f"⚔️ ENNEMIS ENCORE EN VIE : {_alive_str}\n\n"
                                f"[INSTRUCTION]\n"
                                f"Choisis une cible différente parmi les ennemis listés ci-dessus,\n"
                                f"ou déclare une action différente (soin, repositionnement, Fin de tour…).\n"
                                f"Déclare ton choix avec un nouveau bloc [ACTION]."
                            )
                            _app.msg_queue.put({
                                "sender": "⚙️ Système",
                                "text":   _dead_tgt_msg,
                                "color":  "#cc4444",
                            })
                            _original_receive(
                                self_mgr, message, sender,
                                request_reply=False, silent=True,
                            )
                            _original_receive(
                                self_mgr,
                                {"role": "user", "content": _dead_tgt_msg,
                                 "name": "Alexis_Le_MJ"},
                                sender, request_reply=True, silent=False,
                            )
                            return
                except Exception as _kp_err:
                    print(f"[engine_receive] Garde-fou Kill Pool : {_kp_err}")
            # ─────────────────────────────────────────────────────────────────

            # Si l'agent utilise Type: Fin de tour, on termine le tour immédiatement sans interroger le MJ
            if _type_lbl.lower() in ("fin de tour", "fin du tour", "end of turn", "end turn"):
                if COMBAT_STATE["active"] and name == COMBAT_STATE.get("active_combatant"):
                    # On synchronise avec un Event pour garantir que _next_turn
                    # (et donc _rebuild_agent_prompts) s'exécute AVANT que AutoGen
                    # ne sélectionne et génère la réponse du prochain agent.
                    # Sans ça, le nouveau combatant reçoit un system_message périmé
                    # (sans bloc "c'est ton tour") → bug intermittent de prompt manquant.
                    _turn_advanced = _threading.Event()
                    def _end_t1(_ev=_turn_advanced):
                        _trk = getattr(_app, "_combat_tracker_win", None) or getattr(_app, "_combat_tracker", None)
                        if _trk and hasattr(_trk, "_next_turn"):
                            _trk._next_turn()   # appel direct : on est déjà sur le thread Tk
                        elif _trk and hasattr(_trk, "advance_turn"):
                            _trk.advance_turn() # fallback si _next_turn absent
                        _ev.set()
                    _app.root.after(0, _end_t1)
                    _turn_advanced.wait(timeout=8)  # attend que _next_turn + _rebuild soient finis
                elif not COMBAT_STATE["active"]:
                    # Hors combat : "Fin de tour" n'a aucun sens — corriger l'agent
                    _hc_fin_msg = (
                        f"[DIRECTIVE SYSTÈME — FORMAT INCORRECT]\n"
                        f"{name} : le Type 'Fin de tour' dans un bloc[ACTION] n'existe "
                        f"QUE dans le mode combat.\n\n"
                        f"HORS COMBAT, il n'y a pas de tour à terminer. Deux options :\n"
                        f"  • Si tu n'as plus rien à faire : réponds simplement par une "
                        f"phrase de roleplay ou [SILENCE].\n"
                        f"  • Si tu veux déclarer une action mécanique : utilise un bloc "
                        f"[ACTION] avec Type: Action / Action Bonus / Mouvement."
                    )
                    _app.msg_queue.put({
                        "sender": "⚠️ Système",
                        "text": _hc_fin_msg,
                        "color": "#ff9800",
                    })
                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": _hc_fin_msg, "name": "Alexis_Le_MJ"},
                        sender, request_reply=False, silent=True,
                    )
                _original_receive(self_mgr, message, sender, request_reply, silent)
                return

            if not _intention and not _regle:
                # If both intention and regle are missing, check if the type label contains the intention
                # Example: "Action: Ranged attack on VexSira" -> type is "Ranged attack on VexSira"
                _t_low = _type_lbl.lower()
                if _t_low and _t_low not in ("action", "action bonus", "bonus action", "mouvement", "movement", "réaction", "reaction"):
                    _intention = _type_lbl
                    _type_lbl = "Action"
                else:
                    # Bloc malformé — ignorer
                    _original_receive(self_mgr, message, sender, request_reply, silent)
                    return

            _all_subactions = split_into_subactions(
                _type_lbl, _intention, _regle, _cible,
                _CM.get(name, {}),
                name
            )
            # ── FALLBACK SI LA FONCTION ÉCHOUE ──
            if not _all_subactions:
                _all_subactions =[{
                    "type_label": _type_lbl,
                    "intention": _intention,
                    "regle": _regle,
                    "cible": _cible,
                    "single_attack": False
                }]

            _sub_total    = len(_all_subactions)
            _turn_aborted = False

            for _sub_idx, _sub in enumerate(_all_subactions, start=1):
                # ── VÉRIFICATION STRICTE DES RESSOURCES ──────────────────────
                if COMBAT_STATE["active"]:
                    _tr_pre = _get_turn_res(name)
                    _req_type = (_sub.get("type_label") or "").lower()
                    
                    _is_extra = any(k in _req_type for k in ("extra", "supplémentaire", "seconde", "deuxième"))
                    _is_req_act = "action" in _req_type and "action bonus" not in _req_type and "bonus" not in _req_type and not _is_extra
                    _is_req_ba = "action bonus" in _req_type or "bonus action" in _req_type or "bonus" in _req_type
                    
                    # ── GARDE-FOU : READY ACTION (Se Tenir Prêt) = ACTION PRINCIPALE UNIQUEMENT ──
                    if _sub.get("ready_action", False) and not _is_req_act:
                        _ready_err_msg = (
                            f"[RÉSULTAT SYSTÈME — ACTION IMPOSSIBLE — {name}]\n"
                            f"Tu as déclaré 'Se Tenir Prêt' (Ready Action) en tant que[{_sub.get('type_label', 'Action Bonus')}].\n"
                            f"⛔ RÈGLE D&D 5E : 'Se Tenir Prêt' coûte OBLIGATOIREMENT 1 ACTION PRINCIPALE. "
                            f"Tu NE PEUX PAS utiliser une Action Bonus ou un Mouvement pour ça.\n\n"
                            f"[INSTRUCTION]\nAnnule cette tentative. Si tu veux préparer une action, utilise un bloc [ACTION] avec Type : Action. "
                            f"Sinon, déclare une action bonus valide (ex: un sort d'action bonus)."
                        )
                        _app.msg_queue.put({"sender": "⚙️ Système", "text": _ready_err_msg, "color": "#cc4444"})
                        _app._pending_combat_trigger = _ready_err_msg
                        _app._pending_impossible_retrigger = None
                        _refus_public = f"{name} : tentative impossible ('Se Tenir Prêt' nécessite 1 Action Principale). Nouvelle déclaration requise."
                        _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                        _original_receive(self_mgr, {"role": "user", "content": _refus_public, "name": "Alexis_Le_MJ"}, sender, request_reply=False, silent=False)
                        _original_receive(self_mgr, {"role": "user", "content": f"Continue ton tour, {name}. Corrige ton action.", "name": "Alexis_Le_MJ"}, sender, request_reply=True, silent=False)
                        return

                    if _is_req_act and not _tr_pre.get("action"):
                        _has_other = _tr_pre.get("bonus") or _tr_pre.get("movement_ft", 0) > 0
                        _retry_msg = (
                            f"Continue ton tour, {name}. Envoie un bloc [ACTION] de Type: 'Action Bonus' ou 'Mouvement'. ⛔ Attention : un sort demandant 1 Action NE PEUT PAS être lancé avec une Action Bonus. Si tu ne peux plus rien faire, déclare [ACTION] Type: 'Fin de tour'."
                            if _has_other else
                            f"Toutes tes ressources sont épuisées, {name}. Envoie UNIQUEMENT [ACTION] de type 'Fin de tour'."
                        )
                        _no_res_msg = (
                            f"[RÉSULTAT SYSTÈME — ACTION IMPOSSIBLE — {name}]\n"
                            f"Tu as déclaré une Action, mais tu l'as DÉJÀ utilisée à ce tour (ton Action est ❌ épuisée).\n\n"
                            f"[INSTRUCTION]\n{_retry_msg}"
                        )
                        _app.msg_queue.put({"sender": "⚙️ Système", "text": _no_res_msg, "color": "#cc4444"})
                        _app._pending_combat_trigger = _no_res_msg
                        # FIX : request_reply=True appelle Lyra directement (bypass gui_get_human_input).
                        # _pending_impossible_retrigger sera positionné par le thread Tk APRÈS ce point,
                        # mais ne sera jamais consommé dans ce cycle → stale. On le vide préventivement.
                        _app._pending_impossible_retrigger = None
                        _refus_public = f"{name} : tentative d'Action impossible (ressource épuisée). Nouvelle déclaration requise."
                        _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                        _original_receive(self_mgr, {"role": "user", "content": _refus_public, "name": "Alexis_Le_MJ"}, sender, request_reply=False, silent=False)
                        _original_receive(self_mgr, {"role": "user", "content": _retry_msg, "name": "Alexis_Le_MJ"}, sender, request_reply=True, silent=False)
                        return

                    if _is_req_ba and not _tr_pre.get("bonus"):
                        _has_other_ba = _tr_pre.get("action") or _tr_pre.get("movement_ft", 0) > 0
                        _retry_msg_ba = (
                            f"Continue ton tour, {name}. Envoie un bloc [ACTION] de Type: 'Action' ou 'Mouvement'. ⛔ Attention : un sort demandant 1 Action Bonus NE PEUT PAS être lancé avec une Action Principale. Si tu ne peux plus rien faire, déclare [ACTION] Type: 'Fin de tour'."
                            if _has_other_ba else
                            f"Toutes tes ressources sont épuisées, {name}. Envoie UNIQUEMENT [ACTION] de type 'Fin de tour'."
                        )
                        _no_res_msg2 = (
                            f"[RÉSULTAT SYSTÈME — ACTION BONUS IMPOSSIBLE — {name}]\n"
                            f"Tu as déclaré une Action Bonus, mais tu l'as DÉJÀ utilisée à ce tour (elle est ❌ épuisée).\n\n"
                            f"[INSTRUCTION]\n{_retry_msg_ba}"
                        )
                        _app.msg_queue.put({"sender": "⚙️ Système", "text": _no_res_msg2, "color": "#cc4444"})
                        _app._pending_combat_trigger = _no_res_msg2
                        _app._pending_impossible_retrigger = None  # FIX : stale après request_reply=True
                        _refus_public = f"{name} : tentative d'Action Bonus impossible (ressource épuisée). Nouvelle déclaration requise."
                        _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                        _original_receive(self_mgr, {"role": "user", "content": _refus_public, "name": "Alexis_Le_MJ"}, sender, request_reply=False, silent=False)
                        _original_receive(self_mgr, {"role": "user", "content": _retry_msg_ba, "name": "Alexis_Le_MJ"}, sender, request_reply=True, silent=False)
                        return

                # ── Pré-vérification slots sort ──────────────────────────────
                _t_low_spellcheck = (_sub.get("type_label") or "").lower()
                _sk_combined_pre = (_sub.get("intention", "") + " " + _sub.get("regle", "") + " " + _t_low_spellcheck).lower()
                _SKILL_OVERRIDE_PRE = ("se cacher", "cacher", "discrétion", "stealth", "aim", "steady aim", "visée", "viser", "jet de compétence", "skill check")
                _is_physical_attack = (
                    "attaque" in _t_low_spellcheck 
                    and "sort" not in _t_low_spellcheck 
                    and "magie" not in _t_low_spellcheck
                    and not any(k in _sk_combined_pre for k in _SKILL_OVERRIDE_PRE)
                )

                _pre_is_spell = not _is_physical_attack and any(
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
                _pre_spell_candidate = None
                if _pre_is_spell:
                    for _pat in (r"niv(?:eau)?\.?\s*(\d+)", r"niveau\s*(\d+)", r"\bniv(\d+)",
                                 r"slot\s+(?:de\s+)?(?:niveau\s+)?(\d)",
                                 r"emplacement\s+(?:de\s+)?(?:niveau\s+)?(\d)"):
                        _pm = _re.search(_pat, _sub["regle"] + " " + _sub["intention"], _re.IGNORECASE)
                        if _pm:
                            _candidate_lvl = int(_pm.group(1))
                            # Filtre D&D 5e : les sorts s'arrêtent au niveau 9.
                            # Empêche de capturer le "11" de "Clerc niv 11".
                            if _candidate_lvl <= 9:
                                _pre_lvl = _candidate_lvl
                            break
                    # Fallback : si aucun niveau valide trouvé, lire le niveau
                    # de base du sort depuis la DB spell_data.
                    if _pre_lvl is None:
                        _fb_spell = extract_spell_name_llm(f"{_sub.get('intention', '')} {_sub.get('regle', '')}", name)
                        if _fb_spell:
                            try:
                                from spell_data import get_spell as _gs_fb
                                _sp_fb = _gs_fb(_fb_spell)
                                if _sp_fb:
                                    _fb_lvl = int(_sp_fb.get("level", 0))
                                    if 0 < _fb_lvl <= 9:
                                        _pre_lvl = _fb_lvl
                            except Exception:
                                pass

                # ── GARDE HORS COMBAT — EARLY EXIT ──────────────────────────────
                # Doit s'exécuter AVANT les validations de sort (slot, cast_time,
                # type_mismatch) qui injecteraient sinon des messages de combat
                # trompeurs ([TOUR DE COMBAT]) dans le chat hors combat.
                if not COMBAT_STATE["active"]:
                    _is_oc_move = "mouvement" in _sub.get("type_label", "").lower()
                    if _is_oc_move:
                        _mv_dist_m = _re.search(r'(\d+)\s*cases?', _sub.get("regle", ""), _re.IGNORECASE)
                        _mv_cases  = int(_mv_dist_m.group(1)) if _mv_dist_m else 999
                        if _mv_cases < 6:
                            continue
                    # Attaque ou sort hors combat → bloquer et corriger l'agent
                    if _is_physical_attack or _pre_is_spell:
                        _oc_block_msg = (
                            f"[DIRECTIVE SYSTÈME — ACTION IMPOSSIBLE HORS COMBAT]\n"
                            f"{name} : le combat est TERMINÉ. Tu ne peux PAS déclarer d'attaque "
                            f"ou de sort offensif hors combat.\n\n"
                            f"[INSTRUCTION]\n"
                            f"  • Si tu veux parler à un PNJ ou à un allié : écris simplement du roleplay.\n"
                            f"  • Si tu veux faire une action sociale/utilitaire : utilise un bloc [ACTION] "
                            f"avec une Intention non-offensive (ex: Soigner, Inspecter, Parler, Surveiller…).\n"
                            f"  • Ne déclare AUCUNE attaque tant que le MJ n'a pas déclenché un nouveau combat."
                        )
                        _app.msg_queue.put({"sender": "⚙️ Système", "text": _oc_block_msg, "color": "#cc4444"})
                        _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                        _original_receive(
                            self_mgr,
                            {"role": "user", "content": _oc_block_msg, "name": "Alexis_Le_MJ"},
                            sender, request_reply=True, silent=False,
                        )
                        return
                    # Pas d'attaque/sort → auto-approuver (interactions sociales, perception...)
                    _sub_ev  = _threading.Event()
                    _sub_res = {"confirmed": True, "mj_note": ""}
                    # Skip toutes les validations de combat, aller directement au flow post-confirmation
                    _confirmed = True
                    _mj_note   = ""
                    # ── Détection jet de compétence hors combat ──────────────────
                    _SKILL_CHECK_KEYS_OC = (
                        "test de", "jet de", "check",
                        "investigation", "perception", "arcane",
                        "athlétisme", "discrétion", "perspicacité",
                        "acrobaties", "histoire", "intimidation",
                        "médecine", "nature", "religion", "survie",
                        "persuasion", "tromperie", "représentation",
                        "escamotage", "dressage",
                        "sauvegarde", "aide", "assistance",
                        "cacher", "faufiler", "stealth",
                    )
                    _sk_combined_oc = (
                        (_sub.get("intention", "") + " " + _sub.get("regle", ""))
                        .lower()
                    )
                    _is_skill_chk_oc = any(k in _sk_combined_oc for k in _SKILL_CHECK_KEYS_OC)
                    if _is_skill_chk_oc:
                        # Ouvrir la boîte de jet de compétence hors combat
                        _SKILL_MAP_OC = {
                            "arcane":         ("Arcane",          "INT"),
                            "investigation":  ("Investigation",   "INT"),
                            "histoire":       ("Histoire",        "INT"),
                            "nature":         ("Nature",          "INT"),
                            "religion":       ("Religion",        "INT"),
                            "perception":     ("Perception",      "SAG"),
                            "perspicacité":   ("Perspicacité",    "SAG"),
                            "médecine":       ("Médecine",        "SAG"),
                            "survie":         ("Survie",          "SAG"),
                            "dressage":       ("Dressage",        "SAG"),
                            "athlétisme":     ("Athlétisme",      "FOR"),
                            "acrobaties":     ("Acrobaties",      "DEX"),
                            "discrétion":     ("Discrétion",      "DEX"),
                            "cacher":         ("Discrétion",      "DEX"),
                            "faufiler":       ("Discrétion",      "DEX"),
                            "stealth":        ("Discrétion",      "DEX"),
                            "escamotage":     ("Escamotage",      "DEX"),
                            "persuasion":     ("Persuasion",      "CHA"),
                            "tromperie":      ("Tromperie",       "CHA"),
                            "intimidation":   ("Intimidation",    "CHA"),
                            "représentation": ("Représentation",  "CHA"),
                            "sauvegarde":     ("Sauvegarde",      ""),
                            "aide":           ("Aide",            "INT"),
                            "assistance":     ("Assistance",      ""),
                        }
                        _sk_label_oc = "Compétence"
                        _sk_stat_oc  = ""
                        _sk_bonus_oc = 0
                        for _kw_oc, (_lbl_oc, _stat_oc) in _SKILL_MAP_OC.items():
                            if _kw_oc in _sk_combined_oc:
                                _sk_label_oc = _lbl_oc
                                _sk_stat_oc  = _stat_oc
                                _char_mc_oc  = _CM.get(name, {})
                                _sk_bonus_oc = (
                                    _char_mc_oc.get("skills", {}).get(_kw_oc, 0)
                                    or _char_mc_oc.get("saves", {}).get(_stat_oc.lower(), 0)
                                    or 0
                                )
                                break
                        _dc_m_oc = _re.search(
                            r'(?:DC|DD)\s*(\d+)',
                            _sub.get("regle", "") + " " + _sub.get("intention", ""),
                            _re.IGNORECASE,
                        )
                        _sk_dc_oc = _dc_m_oc.group(1) if _dc_m_oc else None
                        _sk_adv_oc = any(k in _sk_combined_oc for k in ("avantage", "advantage", "aide"))
                        _sk_dis_oc = any(k in _sk_combined_oc for k in ("désavantage", "disadvantage"))
                        _sk_ev_oc  = _threading.Event()
                        _sk_res_oc: dict = {}
                        def _sk_cb_oc(confirmed, total=0, mj_note="",
                                       _ev=_sk_ev_oc, _res=_sk_res_oc):
                            _app._unregister_approval_event(_ev)
                            _res["confirmed"] = confirmed
                            _res["total"]     = total
                            _res["mj_note"]   = mj_note
                            _ev.set()
                        _app._register_approval_event(_sk_ev_oc)
                        _app.msg_queue.put({
                            "action":           "skill_check_confirm",
                            "char_name":        name,
                            "skill_label":      _sk_label_oc,
                            "stat_label":       _sk_stat_oc,
                            "bonus":            _sk_bonus_oc,
                            "dc":               _sk_dc_oc,
                            "has_advantage":    _sk_adv_oc,
                            "has_disadvantage": _sk_dis_oc,
                            "resume_callback":  _sk_cb_oc,
                        })
                        _sk_ev_oc.wait(timeout=600)
                        _app._unregister_approval_event(_sk_ev_oc)
                        _sk_confirmed_oc = _sk_res_oc.get("confirmed", False)
                        _sk_total_oc     = _sk_res_oc.get("total", 0)
                        _sk_note_oc      = _sk_res_oc.get("mj_note", "")
                        if _sk_confirmed_oc:
                            feedback_oc = (
                                f"[RÉSULTAT SYSTÈME — JET DE COMPÉTENCE]\n"
                                f"🎲 {name} — {_sk_label_oc} : résultat {_sk_total_oc}"
                                + (f"  — Note MJ : {_sk_note_oc}" if _sk_note_oc else "")
                            )
                        else:
                            feedback_oc = (
                                f"[RÉSULTAT SYSTÈME — JET REFUSÉ]\n"
                                f"❌ MJ a refusé le jet de {_sk_label_oc} de {name}."
                            )
                        _app.msg_queue.put({"sender": "⚙️ Système", "text": feedback_oc, "color": "#4fc3f7"})
                        _original_receive(
                            self_mgr,
                            {"role": "user", "content": feedback_oc, "name": "Alexis_Le_MJ"},
                            sender, request_reply=False, silent=True,
                        )
                    # Enregistrer le message original dans le GroupChat
                    _original_receive(self_mgr, message, sender, request_reply, silent)
                    return

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
                        _pre_spell_for_ritual = extract_spell_name_llm(f"{_sub.get('intention', '')} {_sub.get('regle', '')}", name)
                        if _pre_spell_for_ritual and can_ritual_cast(name, _pre_spell_for_ritual):
                            _ritual_msg2 = (
                                f"🕯️ {name} lance {_pre_spell_for_ritual} en tant que RITUEL "
                                f"(+10 min d'incantation, aucun slot consommé)."
                            )
                            _app.msg_queue.put({"sender": "⚙️ Système", "text": _ritual_msg2, "color": "#8888cc"})
                        else:
                            _supers2 = _slots_superieurs_disponibles(name, _pre_lvl)
                            _spell_nm = extract_spell_name_llm(f"{_sub.get('intention', '')} {_sub.get('regle', '')}", name) or "ce sort"
                            if _supers2:
                                _upcast_hint2 = (
                                    f"\n  ↑ UPCAST DISPONIBLE : tu peux lancer {_spell_nm} "
                                    f"avec un slot de niveau supérieur.\n"
                                    f"  Niveaux disponibles : {', '.join(str(l) for l in _supers2)}\n"
                                    f"  → Fais un nouveau bloc [ACTION] en précisant le niveau voulu dans 'Règle 5e' (ex: {_spell_nm} niv.{_supers2[0]})."
                                )
                            else:
                                _upcast_hint2 = (
                                    f"\n  Aucun emplacement de niveau supérieur disponible non plus."
                                )
                            _no_slot_fb = (
                                f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE — {name}]\n"
                                f"{name} n'a plus d'emplacement de sort de niveau {_pre_lvl}. "
                                f"Ce sort ne peut pas être lancé à ce niveau.\n"
                                f"{_upcast_hint2}\n\n"
                                f"[INSTRUCTION]\n"
                                f"Choisis parmi : upcast (slot sup. si ✅ ci-dessus), "
                                f"sort de niveau inférieur, tour de magie, ou attaque physique."
                            )
                            _app.msg_queue.put({"sender": "⚙️ Système", "text": _no_slot_fb, "color": "#cc4444"})
                            _app._pending_combat_trigger = _no_slot_fb
                            _app._pending_impossible_retrigger = None  # FIX : stale après request_reply=True
                            _refus_public = f"{name} : tentative de sort impossible (plus d'emplacement de ce niveau). Nouvelle déclaration requise."
                            _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                            _original_receive(self_mgr, {"role": "user", "content": _refus_public, "name": "Alexis_Le_MJ"}, sender, request_reply=False, silent=False)
                            _original_receive(self_mgr, {"role": "user", "content": f"Continue ton tour, {name}. Déclare une action différente.", "name": "Alexis_Le_MJ"}, sender, request_reply=True, silent=False)
                            _sub_ev.set()
                            return

                _pre_spell_candidate = extract_spell_name_llm(f"{_sub.get('intention', '')} {_sub.get('regle', '')}", name)
                if _pre_spell_candidate:
                    if not is_spell_prepared(name, _pre_spell_candidate):
                        _avail2 = get_prepared_spell_names(name)
                        _avail2_str = ", ".join(_avail2) if _avail2 else "aucun sort préparé trouvé"
                        _no_prep_fb = (
                            f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE — {name}]\n"
                            f"« {_pre_spell_candidate} » n'est pas dans la liste de sorts "
                            f"préparés de {name}. Ce sort ne peut pas être lancé aujourd'hui.\n\n"
                            f"[SORTS AUTORISÉS POUR {name.upper()}]\n{_avail2_str}\n\n"
                            f"[INSTRUCTION]\nChoisis UNIQUEMENT parmi les sorts listés ci-dessus."
                        )
                        _app.msg_queue.put({"sender": "⚙️ Système", "text": _no_prep_fb, "color": "#cc4444"})
                        _app._pending_combat_trigger = _no_prep_fb
                        _app._pending_impossible_retrigger = None  # FIX : stale après request_reply=True
                        _refus_public = f"{name} : tentative de sort impossible (sort non préparé). Nouvelle déclaration requise."
                        _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                        _original_receive(self_mgr, {"role": "user", "content": _refus_public, "name": "Alexis_Le_MJ"}, sender, request_reply=False, silent=False)
                        _original_receive(self_mgr, {"role": "user", "content": f"Continue ton tour, {name}. Déclare une action différente.", "name": "Alexis_Le_MJ"}, sender, request_reply=True, silent=False)
                        _sub_ev.set()
                        return

                    from spell_data import get_spell as _get_sp
                    _sp_data_sub = _get_sp(_pre_spell_candidate)
                    if _sp_data_sub:
                        _eff_lvl = _pre_lvl or _sp_data_sub.get("level", 0)
                        _valid_ba_sub, _err_ba_sub = validate_bonus_action_rule(
                            name, _pre_spell_candidate, _eff_lvl, _sp_data_sub.get("cast_time_raw", []), COMBAT_STATE.get("turn_spells",[])
                        )
                        if not _valid_ba_sub:
                            _not_ba_fb2 = (
                                f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE — {name}]\n"
                                f"{_err_ba_sub}\n\n"
                                f"[INSTRUCTION]\nAnnule cette tentative. "
                                f"Choisis une action valide (attaque, esquive, ou un tour de magie coûtant 1 action si applicable)."
                            )
                            _app.msg_queue.put({"sender": "⚙️ Système", "text": _not_ba_fb2, "color": "#cc4444"})
                            _app._pending_combat_trigger = _not_ba_fb2
                            _app._pending_impossible_retrigger = None  # FIX : stale après request_reply=True
                            _refus_public = f"{name} : tentative de sort impossible (règle des actions bonus non respectée). Nouvelle déclaration requise."
                            _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                            _original_receive(self_mgr, {"role": "user", "content": _refus_public, "name": "Alexis_Le_MJ"}, sender, request_reply=False, silent=False)
                            _original_receive(self_mgr, {"role": "user", "content": f"Continue ton tour, {name}. Déclare une action différente.", "name": "Alexis_Le_MJ"}, sender, request_reply=True, silent=False)
                            _sub_ev.set()
                            return

                        _ct_unit = _sp_data_sub.get("cast_time_raw", [{}])[0].get("unit", "").lower() if _sp_data_sub.get("cast_time_raw") else ""
                        _type_low = (_sub.get("type_label", "") or "").lower()

                        _type_is_ba = "bonus" in _type_low
                        _type_is_act = "action" in _type_low and "bonus" not in _type_low
                        _type_is_reac = "reaction" in _type_low or "réaction" in _type_low
                        
                        _spell_is_ba = _ct_unit == "bonus"
                        _spell_is_act = _ct_unit == "action"
                        _spell_is_reac = _ct_unit == "reaction"
                        
                        _mismatch = False
                        _expected = ""
                        
                        if _type_is_ba and not _spell_is_ba:
                            _mismatch = True
                        elif _type_is_act and not _spell_is_act:
                            _mismatch = True
                        elif _type_is_reac and not _spell_is_reac:
                            _mismatch = True
                            
                        if _mismatch:
                            _expected = "Action Bonus" if _spell_is_ba else "Action" if _spell_is_act else "Réaction" if _spell_is_reac else f"1 {_ct_unit}"
                            _wrong_type_fb = (
                                f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE — {name}]\n"
                                f"Tu as déclaré le sort {_pre_spell_candidate} avec le type [{_sub.get('type_label', 'Action')}], mais ce sort exige strictement 1 {_expected}.\n"
                                f"⛔ RAPPELLE-TOI : Tu NE PEUX PAS utiliser une Action Bonus pour lancer un sort coûtant 1 Action (et inversement). "
                                f"Tu ne peux pas non plus diviser ou échanger ces ressources.\n\n"
                                f"[INSTRUCTION]\nAnnule cette tentative ou retente la avec le bon type d'Action.\n"
                                f"👉 Si tu voulais utiliser ton Action Bonus, choisis un sort qui coûte spécifiquement '1 action bonus'.\n"
                                f"👉 Sinon, déclare une action ou un mouvement valide."
                            )
                            _app.msg_queue.put({"sender": "⚙️ Système", "text": _wrong_type_fb, "color": "#cc4444"})
                            _app._pending_combat_trigger = _wrong_type_fb
                            _app._pending_impossible_retrigger = None  # FIX : stale après request_reply=True
                            _refus_public = f"{name} : tentative de sort impossible (type d'action incorrect : {_expected} requis). Nouvelle déclaration requise."
                            _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                            _original_receive(self_mgr, {"role": "user", "content": _refus_public, "name": "Alexis_Le_MJ"}, sender, request_reply=False, silent=False)
                            _original_receive(self_mgr, {"role": "user", "content": f"Continue ton tour, {name}. Déclare une action différente.", "name": "Alexis_Le_MJ"}, sender, request_reply=True, silent=False)
                            _sub_ev.set()
                            return

                        _valid_ct_sub, _err_ct_sub = validate_cast_time_in_combat(
                            _pre_spell_candidate, _sp_data_sub.get("cast_time_raw",[])
                        )
                        if not _valid_ct_sub:
                            _not_ct_fb2 = (
                                f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE — {name}]\n"
                                f"{_err_ct_sub}\n\n"
                                f"[INSTRUCTION]\nAnnule cette tentative. "
                                f"Choisis une action valide et déclare-la avec [ACTION]."
                            )
                            _app.msg_queue.put({"sender": "⚙️ Système", "text": _not_ct_fb2, "color": "#cc4444"})
                            _app._pending_combat_trigger = _not_ct_fb2
                            _app._pending_impossible_retrigger = None  # FIX : stale après request_reply=True
                            _refus_public = f"{name} : tentative de sort impossible (temps d'incantation incompatible). Nouvelle déclaration requise."
                            _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                            _original_receive(self_mgr, {"role": "user", "content": _refus_public, "name": "Alexis_Le_MJ"}, sender, request_reply=False, silent=False)
                            _original_receive(self_mgr, {"role": "user", "content": f"Continue ton tour, {name}. Déclare une action différente.", "name": "Alexis_Le_MJ"}, sender, request_reply=True, silent=False)
                            _sub_ev.set()
                            return

                def _sub_cb(confirmed, mj_note="", _ev=_sub_ev, _res=_sub_res, **kwargs):
                    _app._unregister_approval_event(_ev)
                    _res["confirmed"] = confirmed
                    _res["mj_note"]   = mj_note
                    for k, v in kwargs.items():
                        _res[k] = v
                    _ev.set()

                # ── Garde-fou de distance max (Dash et Mouvement en combat) ──
                # IMPORTANT : on détecte le mouvement via le type_label en PRIORITÉ.
                # Ne pas scanner intention/regle qui peuvent contenir des mots-clés
                # de mouvement dans la description d'un effet de sort
                # (ex: "Cible subit -10ft de vitesse" → pas un déplacement du PJ).
                _combined_low = (_sub.get("intention", "") + " " + _sub.get("regle", "") + " " + _sub.get("type_label", "")).lower()
                _type_low = (_sub.get("type_label", "") or _type_lbl or "").lower()
                _MOVE_KEYWORDS = ("mouvement", "déplace", "deplace", "dash", "foncer", "sprint", "avance", "recule", "fonce", "move")
                _type_is_move = any(k in _type_low for k in _MOVE_KEYWORDS)
                _type_is_generic = not _type_is_move and _type_low in ("", "action", "action bonus", "réaction", "reaction")
                _is_ic_move = (
                    COMBAT_STATE["active"]
                    and (
                        _type_is_move
                        or (
                            # Type générique : inspecter l'intention UNIQUEMENT, pas la regle
                            # (la regle décrit souvent les effets du sort/attaque, pas le déplacement)
                            _type_is_generic
                            and any(k in (_sub.get("intention", "")).lower() for k in _MOVE_KEYWORDS)
                        )
                    )
                )
                if _is_ic_move:
                    # Extraire les ft/cases uniquement depuis type + intention,
                    # jamais depuis regle (qui contient les effets du sort/attaque).
                    _regle_for_move = (_sub.get("regle", "")).lower() if _type_is_move else ""
                    _move_scan_text = (_type_low + " " + (_sub.get("intention", "")).lower() + " " + _regle_for_move)
                    _ft_m = _re.search(r'(\d+)\s*(?:ft|feet|pieds)', _move_scan_text)
                    _cs_m = _re.search(r'(\d+)\s*cases?', _move_scan_text)
                    _mv_req = 0
                    if _ft_m:
                        _mv_req = int(_ft_m.group(1))
                    elif _cs_m:
                        _mv_req = int(_cs_m.group(1)) * 5
                    
                    if _mv_req > 0:
                        _tr = _get_turn_res(name)
                        _rem = _tr["movement_ft"]
                        _speed = _get_char_speed_ft(name)
                        
                        _is_dash = any(k in _combined_low for k in ("dash", "foncer", "sprint"))
                        
                        _max_allowed = _rem
                        if _is_dash:
                            if name == "Thorne" and _tr["bonus"]:
                                _max_allowed = _rem + _speed
                            elif _tr["action"]:
                                _max_allowed = _rem + _speed
                                
                        if _mv_req > _max_allowed:
                            # ── Auto-Dash : si le mouvement dépasse le restant mais est
                            #    dans le range Dash ET l'Action est dispo → convertir auto
                            _can_auto_dash = (
                                not _is_dash
                                and _mv_req <= _rem + _speed
                                and _tr["action"]
                            )
                            _can_auto_dash_thorne = (
                                not _is_dash
                                and name == "Thorne"
                                and _mv_req <= _rem + _speed
                                and _tr["bonus"]
                            )

                            if _can_auto_dash or _can_auto_dash_thorne:
                                # ── AUTO-DASH : consommer l'Action (ou Bonus pour Thorne) ──
                                if _can_auto_dash_thorne:
                                    _consume_turn_res(name, "bonus")
                                    _dash_type = "Action Bonus"
                                else:
                                    _consume_turn_res(name, "action")
                                    _dash_type = "Action"
                                _auto_msg = (
                                    f"[SYSTÈME — AUTO-DASH]\n"
                                    f"{name} veut se déplacer de {_mv_req} ft (vitesse: {_speed} ft).\n"
                                    f"→ Le système utilise automatiquement son {_dash_type} pour Foncer (Dash).\n"
                                    f"  Déplacement restant après Dash : {_rem + _speed - _mv_req} ft.\n"
                                    f"  {_dash_type} consommée pour Foncer."
                                )
                                _app.msg_queue.put({"sender": "⚙️ Système", "text": _auto_msg, "color": "#5577aa"})
                                # Mettre à jour le mouvement restant : on laisse passer
                                # (la consommation réelle se fera plus bas dans _consume_turn_res)
                                _tr["movement_ft"] = _rem + _speed  # budget Dash complet
                                # Ne PAS break — laisser le flow continuer normalement

                            else:
                                # ── Rejet classique : au-delà même du Dash ──
                                _rem_str = f" ({_rem} restants + {_speed} Dash)" if _is_dash else ""
                                _dash_hint = f"💡 Distance maximale théorique ce tour : {_max_allowed} ft{_rem_str}."

                                _err_msg = (
                                    f"[RÉSULTAT SYSTÈME — MOUVEMENT IMPOSSIBLE — {name}]\n"
                                    f"Tu as déclaré un mouvement de {_mv_req} ft, mais "
                                    f"ta vitesse est de {_speed} ft et il ne te reste que {_rem} ft.\n"
                                    f"{_dash_hint}\n\n"
                                    f"[INSTRUCTION]\nTon mouvement a été annulé, tu n'as PAS bougé et tu n'as PAS attaqué.\n"
                                    f"Déclare une nouvelle action valide.\n"
                                    f"⛔ RAPPEL : ton déplacement par tour ne peut JAMAIS dépasser ta vitesse de base "
                                    f"({_speed} ft sans Dash, {_speed*2} ft avec Dash). "
                                    f"Consulte le champ 'Déplacement' du [TOUR EN COURS] pour tes ft restants."
                                )
                                _app.msg_queue.put({"sender": "⚙️ Système", "text": _err_msg, "color": "#cc4444"})
                                # Feedback privé → _pending_combat_trigger (ne pollue pas groupchat.messages)
                                _app._pending_combat_trigger = _err_msg
                                _app._pending_impossible_retrigger = None  # FIX : stale après request_reply=True
                                _refus_public = f"{name} : mouvement impossible (distance trop grande). Nouvelle déclaration requise."
                                _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                                _original_receive(self_mgr, {"role": "user", "content": _refus_public, "name": "Alexis_Le_MJ"}, sender, request_reply=False, silent=False)
                                _has_res_mv = _tr.get("action") or _tr.get("bonus")
                                _retry_msg_mv = (
                                    f"Continue ton tour, {name}. Déclare une action différente."
                                    if _has_res_mv else
                                    f"Tes ressources sont épuisées, {name}. Envoie UNIQUEMENT [ACTION] de type 'Fin de tour'."
                                )
                                _original_receive(self_mgr, {"role": "user", "content": _retry_msg_mv, "name": "Alexis_Le_MJ"}, sender, request_reply=True, silent=False)
                                return

                # ── Garde-fou de portée MÊLÉE (corps-à-corps) ────────────────
                # BUG FIX : "lame" a été retiré de la liste plain-string car il est
                # une sous-chaîne de "flame" (Sacred Flame → "sacred flame" → contient "lame").
                # Remplacement : _re.search(r'\blame\b', ...) pour la frontière de mot.
                # "sacred flame" et "flamme sacrée" ajoutés à la liste d'exclusions.
                _is_melee_atk = (
                    COMBAT_STATE["active"]
                    and not _turn_aborted
                    and (
                        any(k in _combined_low for k in (
                            "corps-à-corps", "corps à corps", "corps-a-corps",
                            "melee", "mêlée", "frappe", "frapper",
                            "attaque", "épée", "epee", "hache", "marteau",
                            "glaive", "rapière", "rapiere",
                        ))
                        or bool(_re.search(r'\blame\b', _combined_low))
                    )
                    and not any(k in _combined_low for k in (
                        "distance", "arc", "arbalète", "arbalete",
                        "javelot", "dard", "projectile", "sort",
                        "ray", "rayon", "bolt", "feu sacré",
                        "mouvement", "déplace", "deplace",
                        "sacred flame", "flamme sacrée", "flamme sacree",
                    ))
                )
                if _is_melee_atk and _sub.get("cible"):
                    try:
                        import os as _os_melee, json as _json_melee, math as _math_melee
                        _map_data_melee = {}
                        _active_map = _app._win_state.get("active_map_name", "")
                        if _active_map:
                            try:
                                from app_config import get_campaign_name as _gcn_m
                                _camp_m = _gcn_m()
                            except Exception:
                                _camp_m = "campagne"
                            _camp_m = "".join(
                                c for c in _camp_m if c.isalnum() or c in (" ", "-", "_")
                            ).strip() or "campagne"
                            _safe_m = "".join(
                                c for c in _active_map if c.isalnum() or c in (" ", "-", "_")
                            ).strip() or "carte"
                            _map_path_m = _os_melee.path.join("campagne", _camp_m, "maps", f"{_safe_m}.json")
                            if _os_melee.path.exists(_map_path_m):
                                with open(_map_path_m, "r", encoding="utf-8") as _fmap:
                                    _map_data_melee = _json_melee.load(_fmap)
                        if not _map_data_melee:
                            _map_data_melee = _app._win_state.get("combat_map_data", {})

                        _toks = _map_data_melee.get("tokens",[])
                        if _toks:
                            _cible_low = _sub["cible"].lower()
                            _target_tok = None
                            
                            _is_sw_atk = any(
                                k in (_sub.get("intention", "") + " " + _sub.get("regle", "")).lower() 
                                for k in ("spiritual weapon", "arme spirituelle", "marteau spirituel")
                            )
                            _char_tok = None
                            _sw_tok = None

                            for _tk in _toks:
                                _tk_name = (_tk.get("name") or "").lower()
                                if _tk_name == name.lower():
                                    _char_tok = _tk
                                if _is_sw_atk and _tk_name == f"arme ({name})".lower():
                                    _sw_tok = _tk
                                if _tk_name and (_tk_name in _cible_low or _cible_low in _tk_name):
                                    _target_tok = _tk
                                    
                            _attacker_tok = _sw_tok if (_is_sw_atk and _sw_tok) else _char_tok

                            if _attacker_tok and _target_tok:
                                _ac = int(round(_attacker_tok.get("col", 0)))
                                _ar = int(round(_attacker_tok.get("row", 0)))
                                _asize = max(1, int(round(float(_attacker_tok.get("size", 1)))))
                                _tc = int(round(_target_tok.get("col", 0)))
                                _tr_r = int(round(_target_tok.get("row", 0)))
                                _tsize = max(1, int(round(float(_target_tok.get("size", 1)))))

                                def _dist1d(a, a_sz, b, b_sz):
                                    a_end, b_end = a + a_sz - 1, b + b_sz - 1
                                    if a_end < b: return b - a_end
                                    if b_end < a: return a - b_end
                                    return 0

                                _horiz = max(_dist1d(_ac, _asize, _tc, _tsize), _dist1d(_ar, _asize, _tr_r, _tsize)) * 5.0
                                _dalt = abs(int(_attacker_tok.get("altitude_ft", 0)) - int(_target_tok.get("altitude_ft", 0)))
                                _d3d = max(float(_horiz), float(_dalt))

                                if _d3d > 10.0:
                                    # ── Conseil contextuel selon ressources restantes ──
                                    _tr_melee = _get_turn_res(name)
                                    _mv_left = _tr_melee["movement_ft"]
                                    _has_action = _tr_melee["action"]
                                    _has_bonus = _tr_melee["bonus"]
                                    _speed_m = _get_char_speed_ft(name)
                                    _need_ft = _d3d - 5.0  # ft à parcourir pour être en mêlée

                                    if _mv_left >= _need_ft:
                                        # Assez de mouvement restant
                                        _conseil = (
                                            f"💡 Tu as encore {_mv_left} ft de déplacement. "
                                            f"Déclare [ACTION] Type: Mouvement pour te rapprocher de {_need_ft:.0f} ft, "
                                            f"PUIS attaque au prochain bloc [ACTION]."
                                        )
                                    elif _has_action and (_mv_left + _speed_m) >= _need_ft:
                                        # Dash + mouvement restant suffit
                                        _conseil = (
                                            f"💡 Ton déplacement restant ({_mv_left} ft) ne suffit pas "
                                            f"(il te faut {_need_ft:.0f} ft).\n"
                                            f"→ Utilise ton ACTION pour FONCER (Dash) : déclare\n"
                                            f"[ACTION]\n"
                                            f"  Type      : Action\n"
                                            f"  Intention : Foncer (Dash) vers {_sub['cible']}\n"
                                            f"  Règle 5e  : Foncer — déplacement supplémentaire de {_speed_m} ft\n"
                                            f"  Cible     : —\n"
                                            f"Cela te donnera +{_speed_m} ft. Tu pourras attaquer au tour suivant."
                                        )
                                    elif _mv_left == 0 and _has_action:
                                        # Mouvement épuisé, action dispo → Dash
                                        _dash_total = _speed_m
                                        if _dash_total >= _need_ft:
                                            _conseil = (
                                                f"💡 Ton déplacement est ÉPUISÉ (0 ft restant). "
                                                f"Il te faut encore {_need_ft:.0f} ft pour être en mêlée.\n"
                                                f"→ Utilise ton ACTION pour FONCER (Dash) :\n"
                                                f"  [ACTION]\n"
                                                f"  Type      : Action\n"
                                                f"  Intention : Foncer (Dash) vers {_sub['cible']}\n"
                                                f"  Règle 5e  : Foncer — déplacement supplémentaire de {_speed_m} ft\n"
                                                f"  Cible     : —\n"
                                                f"Cela te donnera +{_speed_m} ft pour te rapprocher."
                                            )
                                        else:
                                            _remaining_after_dash = _need_ft - _speed_m
                                            _conseil = (
                                                f"💡 Ton déplacement est ÉPUISÉ. Foncer (Dash, +{_speed_m} ft) "
                                                f"ne suffit pas pour atteindre la mêlée (il te faut {_need_ft:.0f} ft).\n"
                                                f"→ MEILLEURE OPTION : Foncer pour te rapprocher de {_speed_m} ft "
                                                f"(il restera {_remaining_after_dash:.0f} ft — tu attaqueras au tour suivant).\n"
                                                f"  [ACTION]\n"
                                                f"  Type      : Action\n"
                                                f"  Intention : Foncer (Dash) vers {_sub['cible']}\n"
                                                f"  Règle 5e  : Foncer — déplacement supplémentaire de {_speed_m} ft\n"
                                                f"  Cible     : —\n"
                                                f"→ Autres options : attaque à DISTANCE, sort à distance, Esquive (Dodge)."
                                            )
                                    else:
                                        # Aucune option pour se rapprocher
                                        _conseil = (
                                            f"💡 Tu ne peux plus te rapprocher ce tour "
                                            f"(déplacement: {_mv_left} ft, action: {'✅' if _has_action else '❌'}).\n"
                                            f"→ Options : attaque à DISTANCE, sort à distance, "
                                            f"Esquive (Dodge), Aide (Help), ou [ACTION] de type 'Fin de tour'."
                                        )

                                    _melee_err = (
                                        f"[RÉSULTAT SYSTÈME — ATTAQUE MÊLÉE IMPOSSIBLE — {name}]\n"
                                        f"Tu as déclaré une attaque corps-à-corps contre {_sub['cible']}, "
                                        f"mais tu es à {_d3d:.0f} ft de distance (portée mêlée = 5 ft, reach = 10 ft).\n\n"
                                        f"[INSTRUCTION]\n"
                                        f"{_conseil}"
                                    )
                                    _app.msg_queue.put({"sender": "⚙️ Système", "text": _melee_err, "color": "#cc4444"})
                                    # Feedback privé → _pending_combat_trigger (ne pollue pas groupchat.messages)
                                    _app._pending_combat_trigger = _melee_err
                                    _app._pending_impossible_retrigger = None  # FIX : stale après request_reply=True
                                    _refus_public = f"{name} : attaque de mêlée impossible (cible hors de portée). Nouvelle déclaration requise."
                                    _original_receive(self_mgr, message, sender, request_reply=False, silent=True)
                                    _original_receive(self_mgr, {"role": "user", "content": _refus_public, "name": "Alexis_Le_MJ"}, sender, request_reply=False, silent=False)
                                    _original_receive(self_mgr, {"role": "user", "content": f"Continue ton tour, {name}. Déclare une action différente.", "name": "Alexis_Le_MJ"}, sender, request_reply=True, silent=False)
                                    return
                    except Exception as _melee_guard_err:
                        print(f"[MeleeGuard] Erreur non bloquante : {_melee_guard_err}")

                # ── Recalcul coordonnées si mouvement directionnel (ft → cases) ──────────
                # L'agent confond parfois 30 ft avec 30 cases. On recalcule la destination
                # depuis la position courante + _mv_req // 5 cases dans la bonne direction.
                if _is_ic_move and _mv_req > 0:
                    try:
                        _cw = getattr(_app, "_combat_map_win", None)
                        _toks = getattr(_cw, "tokens", []) if _cw else \
                                _app._win_state.get("combat_map_data", {}).get("tokens", [])
                        _my_tok = next((t for t in _toks if t.get("name") == name), None)
                        if _my_tok:
                            _cur_col = int(round(_my_tok.get("col", 0)))
                            _cur_row = int(round(_my_tok.get("row", 0)))
                            _mv_cases = _mv_req // 5  # ← LA conversion correcte

                            _scan = (_sub.get("intention", "") + " " + _sub.get("regle", "")).lower()
                            _dc = 0
                            _dr = 0
                            if any(k in _scan for k in ("nord", "north")):     _dr = -_mv_cases
                            elif any(k in _scan for k in ("sud", "south")):    _dr = +_mv_cases
                            # IMPORTANT : tester "ouest"/"west" AVANT "est"/"east"
                            # car "est" est une sous-chaîne de "ouest" et aussi le verbe "être".
                            if any(k in _scan for k in ("ouest", "west")):     _dc = -_mv_cases
                            elif _re.search(r"(?:vers l'|à l'|direction )\best\b", _scan) or "east" in _scan:
                                                                               _dc = +_mv_cases

                            if _dc != 0 or _dr != 0:
                                _dest_col = _cur_col + _dc
                                _dest_row = _cur_row + _dr
                                # Remplacer les coordonnées erronées de l'agent
                                _sub["cible"] = f"Col {_dest_col + 1}, Lig {_dest_row + 1}"
                    except Exception as _coord_fix_err:
                        print(f"[CoordFix] {_coord_fix_err}")

                # ── Confirmation MJ en combat ─────────────────────────────────
                # (Hors combat, le flow a déjà fait un early exit plus haut)
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

                # Si le MJ a déplacé le carré de preview du mouvement
                _MOVE_KW_PREV = ("mouvement", "déplace", "deplace", "dash", "foncer", "sprint", "avance", "recule", "fonce", "move")
                _t_low_prev = _sub["type_label"].lower()
                _type_is_mv_prev = any(k in _t_low_prev for k in _MOVE_KW_PREV)
                _type_is_gen_prev = not _type_is_mv_prev and _t_low_prev in ("", "action", "action bonus", "réaction", "reaction")
                
                if _type_is_mv_prev or (_type_is_gen_prev and any(k in _sub["intention"].lower() for k in _MOVE_KW_PREV)):
                    _extra_data = _sub_res.get("extra_data", None)
                    if _extra_data and isinstance(_extra_data, tuple) and len(_extra_data) == 2:
                        _new_col, _new_row = _extra_data
                        # Reconvertir les coordonnées 0-based de la carte en 1-based absolues
                        _sub["cible"] = f"Col {int(round(_new_col)) + 1}, Lig {int(round(_new_row)) + 1}"
                        
                if _confirmed:
                    # ── Retrait automatique de "Caché" et "Invisible" ──
                    # Si le perso effectue une action (autre que 'se cacher' ou 'invisibilité'), on retire ces états.
                    _typ_low = _sub["type_label"].lower()
                    if _typ_low in ("action", "action bonus", "réaction", "reaction"):
                        _intent_low = _sub["intention"].lower()
                        _is_hide = ("cach" in _intent_low or "hide" in _intent_low or "invisib" in _intent_low)
                        if not _is_hide:
                            try:
                                _tr = getattr(_app, "_combat_tracker_win", None)
                                if _tr and hasattr(_tr, "combatants"):
                                    for _cb in _tr.combatants:
                                        if _cb.name == name:
                                            _modified = False
                                            if hasattr(_cb, "tactics") and "Caché" in _cb.tactics:
                                                del _cb.tactics["Caché"]
                                                _modified = True
                                            if hasattr(_cb, "conditions") and "Invisible" in _cb.conditions:
                                                del _cb.conditions["Invisible"]
                                                _modified = True
                                            if _modified:
                                                _tr._refresh_list()
                                                _cm = getattr(_app, "_combat_map_win", None)
                                                if _cm:
                                                    for _t in _cm.tokens:
                                                        if _t.get("name") == name:
                                                            _t_modified = False
                                                            if "tactics" in _t and "Caché" in _t["tactics"]:
                                                                _t["tactics"].remove("Caché")
                                                                _t_modified = True
                                                            if "conditions" in _t and "Invisible" in _t["conditions"]:
                                                                _t["conditions"].remove("Invisible")
                                                                _t_modified = True
                                                            if _t_modified:
                                                                _cm._redraw_one_token(_t)
                            except Exception as e:
                                print(f"[Caché/Invisible Status] Erreur retrait automatique : {e}")

                    _is_single_atk = _sub.get("single_attack", False)

                    if _is_single_atk:
                        # ── FLOW ATTAQUE INDIVIDUELLE (Phase 1 / 2 / 3) ──────
                        _narr_sa = _build_action_narrative(
                            name, _sub["type_label"], _sub["intention"], _sub["cible"]
                        )
                        _original_receive(
                            self_mgr,
                            {"role": "user", "content": f"⚙️ Système: {_narr_sa}", "name": "Alexis_Le_MJ"},
                            sender, request_reply=False, silent=True,
                        )

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
                            try:
                                from combat_tracker_state import add_combat_history
                                add_combat_history(f"  → L'attaque de {name} contre {_sub['cible']} a raté.")
                                if hasattr(_app, "_update_agent_combat_prompts"): _app._update_agent_combat_prompts()
                            except Exception:
                                pass
                            feedback = (
                                "[RÉSULTAT SYSTÈME — ATTAQUE RATÉE]\n"
                                + _atk_data["atk_text"]
                                + "\n  → RATÉ ❌ (MJ)"
                                + (f"\n  Note : {_hit_note}" if _hit_note else "")
                                + "\n\n[INSTRUCTION NARRATIVE]\n"
                                + f"Attaque ratée. Narre en 1 phrase l'esquive ou la parade de {_sub['cible']}."
                            )
                            _app.msg_queue.put({"sender": "⚙️ Système", "text": feedback, "color": "#4fc3f7"})
                            # BUG FIX : ne PAS injecter le feedback ici — il serait ajouté à
                            # groupchat.messages AVANT le message original de l'agent (ajouté
                            # à la ligne 3332), le plaçant en position -4 et hors de la fenêtre
                            # recent_msgs[-3:] de _filter_messages_for_agent.
                            # On diffère l'injection après la ligne 3332 via _deferred_miss_feedback.
                            _deferred_miss_feedback = feedback
                            continue

                        _smite_used = None

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

                        # ── SNEAK ATTACK CONFIRMATION ────────────────────────
                        _sneak_approved = False
                        _has_sneak_dice = _CM.get(name, {}).get("dmg_sneak") is not None
                        _turn_sneak_used = _get_turn_res(name).get("sneak_used", False)

                        if _has_sneak_dice and not _turn_sneak_used:
                            _sn, _sf, _sb = _CM.get(name, {}).get("dmg_sneak", (6, 6, 0))
                            _sneak_dice_str = f"{_sn}d{_sf}"
                            if _sb:
                                _sneak_dice_str += f"+{_sb}" if _sb > 0 else str(_sb)
                            _sneak_ev  = _threading.Event()
                            _sneak_res: dict = {}

                            def _sneak_cb(apply_it, mj_note_sn="", _ev=_sneak_ev, _res=_sneak_res):
                                _app._unregister_approval_event(_ev)
                                _res["apply"] = apply_it
                                _res["note"]  = mj_note_sn
                                _ev.set()

                            _sneak_txt = (
                                f"{name} peut utiliser Sneak Attack (1/tour).\n"
                                f"Dés : {_sneak_dice_str} dégâts supplémentaires\n"
                                f"Conditions D&D 5e : avantage sur le jet d'attaque\n"
                                f"  OU un allié à 5 ft de la cible (sans désavantage).\n\n"
                                f"Les conditions de Sneak Attack sont-elles remplies ?"
                            )
                            _app._register_approval_event(_sneak_ev)
                            _app.msg_queue.put({
                                "action": "result_confirm", "char_name": name,
                                "type_label": "Sneak Attack",
                                "results_text": _sneak_txt,
                                "mode": "smite", "resume_callback": _sneak_cb,
                            })
                            _sneak_ev.wait(timeout=600)
                            _app._unregister_approval_event(_sneak_ev)

                            # Marquer comme utilisé ce tour (qu'il soit approuvé ou non)
                            _get_turn_res(name)["sneak_used"] = True

                            if _sneak_res.get("apply", False):
                                _sneak_approved = True
                                _app.msg_queue.put({
                                    "sender": "⚙️ Système",
                                    "text": f"[🗡️ Sneak Attack] {name} — approuvé par MJ, dégâts sournois ajoutés.",
                                    "color": "#aa88cc",
                                })
                            else:
                                _app.msg_queue.put({
                                    "sender": "⚙️ Système",
                                    "text": f"[🗡️ Sneak Attack] {name} — conditions non remplies, pas de dégâts sournois.",
                                    "color": "#888888",
                                })

                        _dmg_feedback, _dmg_total = roll_damage_only(
                            name, _sub["cible"],
                            _atk_data["dn"], _atk_data["df"], _atk_data["db"],
                            _atk_data["is_crit"], _smite_used, _hit_note, _CM,
                            sneak_approved=_sneak_approved
                        )

                        _dmg_part = (
                            _dmg_feedback
                            .split("\n\n[INSTRUCTION NARRATIVE]")[0]
                            .replace("[RÉSULTAT SYSTÈME — DÉGÂTS CONFIRMÉS PAR MJ]\n", "")
                            .strip()
                        )

                        _dmg_ev  = _threading.Event()
                        _dmg_res: dict = {}

                        def _dmg_link_cb(final_amount, target_val=None, mj_note="", _ev=_dmg_ev, _res=_dmg_res):
                            _app._unregister_approval_event(_ev)
                            _res["amount"] = final_amount
                            _res["target"] = target_val
                            _res["note"]   = mj_note
                            _ev.set()

                        _app._register_approval_event(_dmg_ev)
                        _app.msg_queue.put({
                            "action":          "damage_link",
                            "sender":          name,
                            "char_name":       name,
                            "cible":           _sub["cible"],
                            "dmg_text":        _dmg_part,
                            "dmg_total":       _dmg_total,
                            "is_crit":         _atk_data["is_crit"],
                            "resume_callback": _dmg_link_cb,
                        })
                        _dmg_ev.wait(timeout=300)
                        _app._unregister_approval_event(_dmg_ev)

                        _final_dmg = _dmg_res.get("amount", _dmg_total)
                        _final_tgt = _dmg_res.get("target") or _sub["cible"]

                        try:
                            if getattr(_app, "_combat_tracker_win", None) is not None:
                                _app._combat_tracker_win.apply_damage_to_npc(_final_tgt, _final_dmg)
                        except Exception as _npc_dmg_err:
                            print(f"[DamageApply PNJ] {_npc_dmg_err}")
                        try:
                            from combat_tracker_state import add_combat_history
                            add_combat_history(f"  → {_final_tgt} subit {_final_dmg} dégâts.")
                            if hasattr(_app, "_update_agent_combat_prompts"): _app._update_agent_combat_prompts()
                        except Exception:
                            pass

                        try:
                            from state_manager import load_state as _ls_d, save_state as _ss_d
                            _cible_str   = _final_tgt.lower()
                            _pj_targets  =[
                                _pn for _pn in PLAYER_NAMES
                                if _pn.lower() in _cible_str or _cible_str in _pn.lower()
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

                        _crit_tag = " 🎯 CRITIQUE" if _atk_data["is_crit"] else ""
                        _modif_note = (
                            f" (roulé : {_dmg_total}, modifié par MJ)"
                            if _final_dmg != _dmg_total else ""
                        )
                        _dmg_note = _dmg_res.get("note", "")
                        feedback = (
                            "[RÉSULTAT SYSTÈME — ATTAQUE RÉSOLUE]\n"
                            + _atk_data["atk_text"]
                            + "\n  → TOUCHÉ ✅ (MJ)"
                            + (f"\n  Note MJ (Touche) : {_hit_note}" if _hit_note else "")
                            + f"\n\n[RÉSULTAT SYSTÈME — DÉGÂTS CONFIRMÉS PAR MJ]\n"
                            + f"⚔️ {name} → {_sub['cible']}{_crit_tag}\n"
                            + f"  Dégâts appliqués : {_final_dmg}{_modif_note}\n"
                            + (f"  Note MJ (Dégâts) : {_dmg_note}\n" if _dmg_note else "")
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

                        # ── Détection jet de compétence / sauvegarde ──────────
                        # Si l'action demande un test de caractéristique ou un
                        # jet de compétence (hors sort avec son propre flow),
                        # on ouvre la boîte skill_check_confirm pour que le MJ
                        # valide le dé visuellement.
                        _SKILL_CHECK_KEYS = (
                            "test de", "jet de", "check",
                            "investigation", "perception", "arcane",
                            "athlétisme", "discrétion", "perspicacité",
                            "acrobaties", "histoire", "intimidation",
                            "médecine", "nature", "religion", "survie",
                            "persuasion", "tromperie", "représentation",
                            "escamotage", "dressage",
                            "sauvegarde", "vigueur", "agilité",
                            "aide", "assistance", "cacher", "faufiler", "stealth",
                        )
                        _sk_combined = (
                            (_sub.get("intention", "") + " " + _sub.get("regle", ""))
                            .lower()
                        )
                        _is_skill_chk = (
                            not _is_physical_attack
                            and not _pre_is_spell   # sorts gérés par leur propre flow
                            and "mouvement" not in (_sub.get("type_label", "") or "").lower()
                            and any(k in _sk_combined for k in _SKILL_CHECK_KEYS)
                        )

                        if _is_skill_chk:
                            # ── Extraire skill / bonus / DC / avantage ────────
                            _SKILL_MAP_SC = {
                                "arcane":         ("Arcane",          "INT"),
                                "investigation":  ("Investigation",   "INT"),
                                "histoire":       ("Histoire",        "INT"),
                                "nature":         ("Nature",          "INT"),
                                "religion":       ("Religion",        "INT"),
                                "perception":     ("Perception",      "SAG"),
                                "perspicacité":   ("Perspicacité",    "SAG"),
                                "médecine":       ("Médecine",        "SAG"),
                                "survie":         ("Survie",          "SAG"),
                                "dressage":       ("Dressage",        "SAG"),
                                "athlétisme":     ("Athlétisme",      "FOR"),
                                "acrobaties":     ("Acrobaties",      "DEX"),
                                "discrétion":     ("Discrétion",      "DEX"),
                                "cacher":         ("Discrétion",      "DEX"),
                                "faufiler":       ("Discrétion",      "DEX"),
                                "stealth":        ("Discrétion",      "DEX"),
                                "escamotage":     ("Escamotage",      "DEX"),
                                "persuasion":     ("Persuasion",      "CHA"),
                                "tromperie":      ("Tromperie",       "CHA"),
                                "intimidation":   ("Intimidation",    "CHA"),
                                "représentation": ("Représentation",  "CHA"),
                                "sauvegarde":     ("Sauvegarde",      ""),
                                "aide":           ("Aide",            "INT"),
                                "assistance":     ("Assistance",      ""),
                            }
                            _sk_label = "Compétence"
                            _sk_stat  = ""
                            _sk_bonus = 0
                            for _kw_sc, (_lbl_sc, _stat_sc) in _SKILL_MAP_SC.items():
                                if _kw_sc in _sk_combined:
                                    _sk_label = _lbl_sc
                                    _sk_stat  = _stat_sc
                                    _char_mc  = _CM.get(name, {})
                                    _sk_bonus = (
                                        _char_mc.get("skills", {}).get(_kw_sc, 0)
                                        or _char_mc.get("saves", {}).get(_stat_sc.lower(), 0)
                                        or 0
                                    )
                                    break

                            # DC depuis la règle ou l'intention
                            _dc_m_sc = _re.search(
                                r'(?:DC|DD)\s*(\d+)',
                                _sub.get("regle", "") + " " + _sub.get("intention", ""),
                                _re.IGNORECASE,
                            )
                            _sk_dc = _dc_m_sc.group(1) if _dc_m_sc else None

                            # Avantage/Désavantage
                            _sk_adv = any(k in _sk_combined for k in ("avantage", "advantage", "aide"))
                            _sk_dis = any(k in _sk_combined for k in ("désavantage", "disadvantage"))

                            # ── Boîte skill_check_confirm ─────────────────────
                            _sk_ev  = _threading.Event()
                            _sk_res: dict = {}

                            def _sk_cb(confirmed, total=0, mj_note="",
                                       _ev=_sk_ev, _res=_sk_res):
                                _app._unregister_approval_event(_ev)
                                _res["confirmed"] = confirmed
                                _res["total"]     = total
                                _res["mj_note"]   = mj_note
                                _ev.set()

                            _app._register_approval_event(_sk_ev)
                            _app.msg_queue.put({
                                "action":           "skill_check_confirm",
                                "char_name":        name,
                                "skill_label":      _sk_label,
                                "stat_label":       _sk_stat,
                                "bonus":            _sk_bonus,
                                "dc":               _sk_dc,
                                "has_advantage":    _sk_adv,
                                "has_disadvantage": _sk_dis,
                                "resume_callback":  _sk_cb,
                            })
                            _sk_ev.wait(timeout=600)
                            _app._unregister_approval_event(_sk_ev)

                            _sk_confirmed = _sk_res.get("confirmed", False)
                            _sk_total     = _sk_res.get("total", 0)
                            _sk_note      = _sk_res.get("mj_note", "")

                            _dc_tag_sk = ""
                            if _sk_dc:
                                try:
                                    _dc_tag_sk = (
                                        f"  ✅ SUCCÈS (DC {_sk_dc})"
                                        if _sk_total >= int(_sk_dc)
                                        else f"  ❌ ÉCHEC (DC {_sk_dc})"
                                    )
                                except Exception:
                                    pass

                            if _sk_confirmed:
                                feedback = (
                                    f"[RÉSULTAT SYSTÈME — JET DE COMPÉTENCE]\n"
                                    f"⚙️ {name} — {_sub['intention']}\n"
                                    f"  Test     : {_sk_label}"
                                    + (f" ({_sk_stat})" if _sk_stat else "")
                                    + (" avec Avantage" if _sk_adv else "")
                                    + f" | Cible : {_sub['cible']}\n"
                                    f"  Résultat : {_sk_total}{_dc_tag_sk}\n"
                                    + (f"  Note MJ  : {_sk_note}\n" if _sk_note else "")
                                    + "\n[INSTRUCTION NARRATIVE]\n"
                                    f"Narre en 1-2 phrases l'action de {name} : {_sub['intention']}. "
                                    f"Si des dés sont encore nécessaires, pose un nouveau [ACTION]."
                                )
                            else:
                                feedback = (
                                    f"[RÉSULTAT SYSTÈME — JET REFUSÉ]\n"
                                    f"[MJ → {name}] ❌ Jet de {_sk_label} refusé."
                                    + (f"  — {_sk_note}" if _sk_note else "")
                                    + "\n\n[INSTRUCTION]\nCette tentative a été bloquée par le MJ. "
                                    "Déclare une autre action si nécessaire."
                                )

                            _app.msg_queue.put({"sender": "⚙️ Système", "text": feedback, "color": "#4fc3f7"})
                            _original_receive(
                                self_mgr,
                                {"role": "user", "content": feedback, "name": "Alexis_Le_MJ"},
                                sender, request_reply=False, silent=True,
                            )

                        if not _is_skill_chk:
                            try:
                                _consumed_slot = False
                                if _pre_is_spell and _pre_spell_candidate:
                                    from spell_data import get_spell as _get_sp_fi
                                    _sp_fi = _get_sp_fi(_pre_spell_candidate)
                                    if _sp_fi:
                                        _fi_unit = _sp_fi.get("cast_time_raw", [{}])[0].get("unit", "") if _sp_fi.get("cast_time_raw") else ""
                                        _fi_lvl = _pre_lvl or _sp_fi.get("level", 0)
                                        COMBAT_STATE.setdefault("turn_spells",[]).append({
                                            "name": _pre_spell_candidate, "level": _fi_lvl, "cast_time_unit": _fi_unit
                                        })
                                        # Consommer le slot (sauf rituel)
                                        if _fi_lvl > 0 and not can_ritual_cast(name, _pre_spell_candidate):
                                            use_spell_slot(name, str(_fi_lvl))
                                            _app._update_agent_combat_prompts()
                                            _consumed_slot = True

                                        # ── Concentration : activer automatiquement ──
                                        if _sp_fi.get("concentration", False):
                                            try:
                                                from spell_data import get_concentration_rounds as _gcr
                                                _conc_rounds = _gcr(_pre_spell_candidate)
                                                if _conc_rounds > 0:
                                                    _trk = (
                                                        getattr(_app, "_combat_tracker_win", None)
                                                        or getattr(_app, "_combat_tracker", None)
                                                    )
                                                    if _trk and hasattr(_trk, "_apply_concentration"):
                                                        for _cb in _trk.combatants:
                                                            if _cb.name == name:
                                                                _app.root.after(0, lambda c=_cb, s=_pre_spell_candidate, r=_conc_rounds: _trk._apply_concentration(c, s, r))
                                                                break
                                            except Exception as _conc_err:
                                                print(f"[Concentration] Erreur auto-apply : {_conc_err}")

                                # Fallback : si le nom du sort n'a pas été trouvé dans la DB
                                # mais qu'on connaît le niveau, on consomme quand même le slot.
                                if _pre_is_spell and not _consumed_slot and _pre_lvl and _pre_lvl > 0:
                                    use_spell_slot(name, str(_pre_lvl))
                                    _app._update_agent_combat_prompts()


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
                                    f"[MJ → {name}] ✅[{_sub['type_label']}] autorisé. "
                                    f"(Erreur : {_exec_err}) "
                                    f"Narre : {_sub['intention']} — {_sub['regle']} → {_sub['cible']}"
                                )

                        # ── TÉLÉPORTATION INSTANTANÉE ──────────────────────────
                        # Sorts qui téléportent le lanceur à une destination
                        # immédiatement, sans utiliser le budget de mouvement.
                        # Si la cible contient des coordonnées, on déplace le
                        # token automatiquement via le tag [MOVE_TOKEN:].
                        _TELEPORT_SPELLS = (
                            "misty step", "pas brumeux", "pas de brume",
                            "thunder step", "pas du tonnerre",
                            "dimension door", "porte dimensionnelle",
                            "teleport", "téléportation", "teleportation",
                            "far step", "grand pas",
                            "fey step", "pas féerique", "pas ferique",
                            "arcane gate", "portail arcanique",
                            "plane shift", "changement de plan",
                            "word of recall", "rappel divin",
                            "blink", "clignotement",
                            "etherealness", "incorporalité",
                        )
                        _tp_combined = (
                            (_pre_spell_candidate or "").lower()
                            + " " + _sub.get("intention", "").lower()
                            + " " + _sub.get("regle", "").lower()
                        )
                        _is_teleport_spell = any(k in _tp_combined for k in _TELEPORT_SPELLS)

                        if _is_teleport_spell and "[MOVE_TOKEN:" not in feedback:
                            _tp_cible = _sub.get("cible", "")
                            # Chercher coordonnées absolues "Col X, Lig Y"
                            _tp_coord = _re.search(
                                r'col(?:onne)?\s*(\d+)[,\s]+(?:lig(?:ne)?|rang(?:ée?)?)\s*(\d+)',
                                _tp_cible, _re.IGNORECASE
                            )
                            if _tp_coord:
                                _tp_col = int(_tp_coord.group(1)) - 1
                                _tp_row = int(_tp_coord.group(2)) - 1
                                # Le tag sera consommé par le bloc [MOVE_TOKEN] plus bas
                                feedback += f"\n[MOVE_TOKEN:{name}:{_tp_col}:{_tp_row}]"
                                _app.msg_queue.put({
                                    "sender": "✨ Téléportation",
                                    "text": (
                                        f"⚡ {name} se téléporte instantanément → "
                                        f"Col {_tp_col+1}, Lig {_tp_row+1}"
                                    ),
                                    "color": "#b39ddb",
                                })

                        _split_marker = "\n\n[INSTRUCTION NARRATIVE]"
                        _results_part = feedback.split(_split_marker)[0].strip()
                        # Supprimer le header "[RÉSULTAT SYSTÈME — TYPE — CharName]"
                        # (les headers incluent le nom du perso → simple replace ne suffit pas)
                        _results_part = _re.sub(
                            r"^\[RÉSULTAT SYSTÈME — [^\]]+\]\n?", "",
                            _results_part
                        ).strip()

                        # NOTE : engine_mechanics émet "[RÉSULTAT SYSTÈME — TYPE — {char_name}]"
                        # → le startswith doit s'arrêter AVANT le "]" final pour matcher
                        # avec n'importe quel nom de personnage interpolé dans le header.
                        _is_spell_attack    = feedback.startswith("[RÉSULTAT SYSTÈME — ATTAQUE DE SORT")
                        _is_healing_action  = feedback.startswith("[RÉSULTAT SYSTÈME — SOIN")
                        _is_save_action     = feedback.startswith("[RÉSULTAT SYSTÈME — JET DE SAUVEGARDE")

                        if _is_spell_attack:
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

                        elif _is_save_action:
                            # ── Boîte de confirmation JET DE SAUVEGARDE ([ACTION] flow) ──
                            _sv2_results_display = _re.sub(r"\[__save_dmg_total__:\d+\]\n?", "", _results_part).strip()
                            _sv2_dmg_m = _re.search(r"\[__save_dmg_total__:(\d+)\]", _results_part)
                            _sv2_dmg_total = int(_sv2_dmg_m.group(1)) if _sv2_dmg_m else 0

                            _sv2_ev  = _threading.Event()
                            _sv2_res: dict = {}

                            def _sv2_cb(target_saved2, mj_note_sv2="", _ev=_sv2_ev, _res=_sv2_res):
                                _app._unregister_approval_event(_ev)
                                _res["saved"] = target_saved2
                                _res["note"]  = mj_note_sv2
                                _ev.set()

                            _app._register_approval_event(_sv2_ev)
                            _app.msg_queue.put({
                                "action":          "result_confirm",
                                "char_name":       name,
                                "type_label":      _sub["type_label"],
                                "results_text":    _sv2_results_display,
                                "mode":            "save",
                                "resume_callback": _sv2_cb,
                            })
                            _sv2_ev.wait(timeout=600)
                            _app._unregister_approval_event(_sv2_ev)

                            _target_saved2 = _sv2_res.get("saved", False)
                            _sv2_mj_note   = _sv2_res.get("note", "")
                            _sv2_cible     = _sub["cible"] or "Cible"

                            def _apply_save_dmg2(cible_s2, dmg_s2, label_s2):
                                _dl2_ev  = _threading.Event()
                                _dl2_res: dict = {}
                                def _dl2_cb(final2, target_val=None, mj_note="", _ev=_dl2_ev, _res=_dl2_res):
                                    _app._unregister_approval_event(_ev)
                                    _res["amount"] = final2
                                    _res["target"] = target_val
                                    _res["note"]   = mj_note
                                    _ev.set()
                                _app._register_approval_event(_dl2_ev)
                                _app.msg_queue.put({
                                    "action": "damage_link", "sender": name, "char_name": name,
                                    "cible": cible_s2, "dmg_text": label_s2,
                                    "dmg_total": dmg_s2, "is_crit": False,
                                    "resume_callback": _dl2_cb,
                                })
                                _dl2_ev.wait(timeout=300)
                                _app._unregister_approval_event(_dl2_ev)
                                _fd2 = _dl2_res.get("amount", dmg_s2)
                                _ftgt2 = _dl2_res.get("target") or cible_s2
                                try:
                                    if getattr(_app, "_combat_tracker_win", None) is not None:
                                        _app._combat_tracker_win.apply_damage_to_npc(_ftgt2, _fd2)
                                except Exception as _e2:
                                    print(f"[SaveAction DmgApply] {_e2}")
                                try:
                                    from combat_tracker_state import add_combat_history
                                    add_combat_history(f"  → {_ftgt2} subit {_fd2} dégâts.")
                                    if hasattr(_app, "_update_agent_combat_prompts"): _app._update_agent_combat_prompts()
                                except Exception:
                                    pass
                                return _fd2, _dl2_res.get("note", "")

                            try:
                                from combat_tracker_state import add_combat_history
                                if _target_saved2:
                                    add_combat_history(f"  → {_sv2_cible} a réussi sa sauvegarde !")
                                else:
                                    add_combat_history(f"  → {_sv2_cible} a raté sa sauvegarde.")
                                if hasattr(_app, "_update_agent_combat_prompts"): _app._update_agent_combat_prompts()
                            except Exception:
                                pass
                                
                            if _target_saved2:
                                # Sauvegarde RÉUSSIE → vérifier half_on_save depuis spell_data
                                _is_half_2 = False
                                if _pre_spell_candidate:
                                    try:
                                        from spell_data import get_spell as _gsp_half2
                                        _sp_half2 = _gsp_half2(_pre_spell_candidate)
                                        _is_half_2 = _sp_half2.get("half_on_save", False) if _sp_half2 else False
                                    except Exception:
                                        pass
                                _half2 = (_sv2_dmg_total // 2) if _is_half_2 else 0
                                if _half2 > 0:
                                    _app2, _app2_note = _apply_save_dmg2(
                                        _sv2_cible, _half2,
                                        f"Sauvegarde réussie — demi-dégâts ({_sv2_dmg_total}÷2)",
                                    )
                                    feedback = (
                                        "[RÉSULTAT SYSTÈME — SAUVEGARDE RÉUSSIE]\n"
                                        + _sv2_results_display
                                        + "\n  → SAUVEGARDE RÉUSSIE ✅ (sort raté)"
                                        + (f"\n  Note MJ (Sauvegarde) : {_sv2_mj_note}" if _sv2_mj_note else "")
                                        + f"\n  Demi-dégâts appliqués : {_app2}"
                                        + (f"\n  Note MJ (Dégâts) : {_app2_note}" if _app2_note else "")
                                        + "\n\n[INSTRUCTION NARRATIVE]\n"
                                        + f"La cible a résisté. Narre en 1-2 phrases comment {_sv2_cible} "
                                        + f"résiste partiellement. Ne mentionne pas les chiffres."
                                    )
                                else:
                                    feedback = (
                                        "[RÉSULTAT SYSTÈME — SAUVEGARDE RÉUSSIE]\n"
                                        + _sv2_results_display
                                        + "\n  → SAUVEGARDE RÉUSSIE ✅ (sort raté — aucun effet)"
                                        + (f"\n  Note MJ (Sauvegarde) : {_sv2_mj_note}" if _sv2_mj_note else "")
                                        + "\n\n[INSTRUCTION NARRATIVE]\n"
                                        + f"La cible résiste au sort. Narre en 1-2 phrases."
                                    )
                            else:
                                if _sv2_dmg_total > 0:
                                    _app2, _app2_note = _apply_save_dmg2(
                                        _sv2_cible, _sv2_dmg_total,
                                        "Sauvegarde ratée — dégâts pleins",
                                    )
                                    feedback = (
                                        "[RÉSULTAT SYSTÈME — SAUVEGARDE RATÉE]\n"
                                        + _sv2_results_display
                                        + "\n  → SAUVEGARDE RATÉE ❌ (sort touché)"
                                        + (f"\n  Note MJ (Sauvegarde) : {_sv2_mj_note}" if _sv2_mj_note else "")
                                        + f"\n  Dégâts appliqués : {_app2}"
                                        + (f"\n  Note MJ (Dégâts) : {_app2_note}" if _app2_note else "")
                                        + "\n\n[INSTRUCTION NARRATIVE]\n"
                                        + f"La cible a raté son jet. Narre en 1-2 phrases l'impact. "
                                        + f"Ne mentionne pas les chiffres."
                                    )
                                else:
                                    feedback = (
                                        "[RÉSULTAT SYSTÈME — SAUVEGARDE RATÉE]\n"
                                        + _sv2_results_display
                                        + "\n  → SAUVEGARDE RATÉE ❌ (sort touché — effets actifs)"
                                        + (f"\n  Note MJ (Sauvegarde) : {_sv2_mj_note}" if _sv2_mj_note else "")
                                        + "\n\n[INSTRUCTION NARRATIVE]\n"
                                        + f"Le sort fait plein effet. Narre en 1-2 phrases."
                                    )
                            _result_note = {}  # pas de note supplémentaire à fusionner

                        elif _is_healing_action:
                            _result_ev   = _threading.Event()
                            _result_note: dict = {}

                            def _heal_action_cb(mj_note_heal="", _ev=_result_ev, _res=_result_note):
                                _app._unregister_approval_event(_ev)
                                _res["note"] = mj_note_heal
                                _ev.set()

                            _app._register_approval_event(_result_ev)
                            _app.msg_queue.put({
                                "action": "result_confirm", "char_name": name,
                                "type_label": f"Sort — {_sub.get('intention', '')}",
                                "results_text": _results_part,
                                "mode": "healing", "resume_callback": _heal_action_cb,
                            })
                            _result_ev.wait(timeout=600)
                            _app._unregister_approval_event(_result_ev)

                        else:
                            _result_note: dict = {}

                        _res_mj_note = _result_note.get("note", "")

                        if _is_spell_attack:
                            _spell_hit = _result_note.get("hit", True)
                            cible = _sub["cible"]
                            if not _spell_hit:
                                try:
                                    from combat_tracker_state import add_combat_history
                                    add_combat_history(f"  → L'attaque de sort de {name} contre {cible} a raté.")
                                    if hasattr(_app, "_update_agent_combat_prompts"): _app._update_agent_combat_prompts()
                                except Exception:
                                    pass
                                feedback = (
                                    "[RÉSULTAT SYSTÈME — ATTAQUE DE SORT RATÉE]\n"
                                    + _results_part
                                    + "\n  → RATÉ ❌ (MJ)"
                                    + (f"\n  Note : {_res_mj_note}" if _res_mj_note else "")
                                    + "\n\n[INSTRUCTION NARRATIVE]\n"
                                    + f"Attaque ratée. Narre en 1 phrase comment {cible} esquive ou résiste."
                                )
                            else:
                                _dmg_m = _re.search(r"\[dégâts si touche\].*?Total\s*=\s*(\d+)", _results_part, _re.IGNORECASE)
                                _dmg_tot_spell = int(_dmg_m.group(1)) if _dmg_m else 0
                                
                                _dmg_line = ""
                                _line_m = _re.search(r"(\[dégâts si touche\].*?)\n", _results_part + "\n")
                                if _line_m: _dmg_line = _line_m.group(1).strip()
                                
                                _s_dmg_ev  = _threading.Event()
                                _s_dmg_res = {}
                                def _s_dmg_cb(amount, target_val=None, mj_note="", _ev=_s_dmg_ev, _res=_s_dmg_res):
                                    _app._unregister_approval_event(_ev)
                                    _res["amount"] = amount
                                    _res["target"] = target_val
                                    _res["note"]   = mj_note
                                    _ev.set()
                                
                                _app._register_approval_event(_s_dmg_ev)
                                _app.msg_queue.put({
                                    "action": "damage_link", "sender": name, "char_name": name,
                                    "cible": cible, "dmg_text": _dmg_line or "Dégâts du sort",
                                    "dmg_total": _dmg_tot_spell, "is_crit": False,
                                    "resume_callback": _s_dmg_cb,
                                })
                                _s_dmg_ev.wait(timeout=300)
                                _app._unregister_approval_event(_s_dmg_ev)
                                _fd_spell = _s_dmg_res.get("amount", _dmg_tot_spell)
                                _ftgt_spell = _s_dmg_res.get("target") or cible

                                try:
                                    if getattr(_app, "_combat_tracker_win", None) is not None:
                                        _app._combat_tracker_win.apply_damage_to_npc(_ftgt_spell, _fd_spell)
                                except Exception as _e2:
                                    print(f"[SpellDamageApply] {_e2}")
                                try:
                                    from combat_tracker_state import add_combat_history
                                    add_combat_history(f"  → {_ftgt_spell} subit {_fd_spell} dégâts.")
                                    if hasattr(_app, "_update_agent_combat_prompts"): _app._update_agent_combat_prompts()
                                except Exception:
                                    pass

                                _modif_note = f" (roulé : {_dmg_tot_spell}, modifié par MJ)" if _fd_spell != _dmg_tot_spell else ""
                                _s_dmg_note = _s_dmg_res.get("note", "")
                                feedback = (
                                    "[RÉSULTAT SYSTÈME — ATTAQUE DE SORT RÉSOLUE]\n"
                                    + _results_part
                                    + "\n  → TOUCHÉ ✅ (MJ)"
                                    + (f"\n  Note MJ (Touche) : {_res_mj_note}" if _res_mj_note else "")
                                    + f"\n  Dégâts appliqués : {_fd_spell}{_modif_note}"
                                    + (f"\n  Note MJ (Dégâts) : {_s_dmg_note}" if _s_dmg_note else "")
                                    + "\n\n[INSTRUCTION NARRATIVE]\n"
                                    + f"Attaque de sort réussie. Narre en 1-2 phrases l'impact sur {cible}."
                                )
                        else:
                            if _res_mj_note:
                                feedback += f"\n[Modification MJ] {_res_mj_note}"

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
                                        if isinstance(msg, str) and "introuvable" in msg.lower():
                                            _app.msg_queue.put({"sender": "⚠️ Carte", "text": msg, "color": "#ff9800"})
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
                    # Action refusée par le MJ
                    # Les ressources du tour NE sont PAS consommées — l'agent
                    # doit redéclarer une action valide dans le même tour.
                    _note_txt = f" {_mj_note}" if _mj_note else ""

                    # ── Feedback PRIVÉ (agent actif uniquement) ───────────────
                    _tec_refus = _build_tour_en_cours(name) if COMBAT_STATE["active"] else ""

                    _feedback_prive = (
                        f"[RÉSULTAT SYSTÈME — ACTION REFUSÉE PAR MJ]\n"
                        f"[MJ → {name}] ❌ [{_sub['type_label']}] refusé.{_note_txt}\n\n"
                        f"[INSTRUCTION SYSTÈME]\n"
                        f"Ton action a été refusée par le MJ. Tes ressources de tour sont INTACTES "
                        f"— tu n'as rien dépensé.\n"
                        f"Tu DOIS déclarer une nouvelle action avec un bloc[ACTION] valide.\n"
                        f"Ne répète PAS l'action refusée. Choisis une alternative différente.\n"
                        + (f"\n{_tec_refus}" if _tec_refus else "")
                    )

                    # Affichage UI MJ (local, ne va pas dans le GroupChat)
                    _app.msg_queue.put({"sender": "❌ MJ", "text": _feedback_prive, "color": "#ef9a9a"})

                    # ── FIX : Utiliser _pending_combat_trigger avec un format
                    # que le speaker selector route vers le joueur, PAS vers le MJ.
                    #
                    # Problème historique :
                    #   1. request_reply=True avec sender=player_agent → le speaker
                    #      selector voit "player spoke last" → route vers MJ
                    #   2. gui_get_human_input consomme _pending_combat_trigger
                    #      mais le texte commençait par [RÉSULTAT SYSTÈME →
                    #      speaker selector le traite comme tool_result → route MJ
                    #   3. → deadlock "En attente de votre action"
                    #
                    # Solution : formater le trigger comme [TOUR DE COMBAT — NAME]
                    # (même pattern que _pending_impossible_retrigger dans chat_mixin.py).
                    # Le speaker selector détecte le nom du joueur dans content_low
                    # et route directement vers l'agent concerné.
                    _app._pending_impossible_retrigger = None
                    if COMBAT_STATE["active"]:
                        _app._pending_combat_trigger = (
                            f"[TOUR DE COMBAT — {name.upper()}]\n"
                            f"C'est à nouveau le tour de {name}. "
                            f"Ton action précédente a été refusée par le MJ.\n"
                            f"[INSTRUCTION]\n"
                            f"Action refusée : {_sub['type_label']} — {_sub['intention']}\n"
                            f"Raison MJ :{_note_txt or ' (aucune précision)'}\n\n"
                            f"Tes ressources de tour sont INTACTES — tu n'as rien dépensé.\n"
                            f"Choisis une alternative DIFFÉRENTE. Ne répète PAS l'action refusée.\n"
                            f"{name}, déclare maintenant une nouvelle action valide "
                            f"(attaque, sort, mouvement, esquive, ou Fin de tour)."
                        )
                    else:
                        _app._pending_combat_trigger = (
                            f"[ACTION REFUSÉE — {name.upper()}]\n"
                            f"Ton action a été refusée par le MJ.\n"
                            f"[INSTRUCTION]\n"
                            f"Action refusée : {_sub['type_label']} — {_sub['intention']}\n"
                            f"Raison MJ :{_note_txt or ' (aucune précision)'}\n\n"
                            f"{name}, choisis une alternative différente ou "
                            f"réponds simplement en roleplay."
                        )

                    # ── GroupChat public : message original (silencieux) ───────
                    _original_receive(self_mgr, message, sender, request_reply=False, silent=True)

                    # ── GroupChat public : note neutre visible par tous ────────
                    _refus_public = f"[MJ → {name}] ❌ [{_sub['type_label']}] refusé.{_note_txt} Déclare une nouvelle action."
                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": _refus_public, "name": "Alexis_Le_MJ"},
                        sender, request_reply=False, silent=False,
                    )

                    # ── Retour au MJ → gui_get_human_input consomme le trigger ─
                    # request_reply=True route vers le MJ (car sender=player),
                    # gui_get_human_input y trouve _pending_combat_trigger formaté
                    # en [TOUR DE COMBAT — NAME], l'injecte dans le GroupChat,
                    # et le speaker selector route correctement vers le joueur.
                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": f"Continue ton tour, {name}.", "name": "Alexis_Le_MJ"},
                        sender, request_reply=True, silent=True,
                    )
                    return

            # ── Fin du traitement du bloc[ACTION] ────────────────────────────

            if not _turn_aborted:
                _t_low_consume = _type_lbl.lower()
                _combined_consume = (_t_low_consume + " " + _intention + " " + _regle).lower()
                
                _is_dash_consume = any(k in _combined_consume for k in ("dash", "foncer", "sprint"))
                _is_move_consume = any(k in _combined_consume for k in ("mouvement", "déplace", "deplace", "avance", "recule", "dash", "sprint", "fonce"))
                
                _mv_ft_used = 0
                if _is_move_consume:
                    _ft_m = _re.search(r'(\d+)\s*(?:ft|feet|pieds)', _regle + " " + _intention, _re.IGNORECASE)
                    _cs_m = _re.search(r'(\d+)\s*cases?', _regle + " " + _intention, _re.IGNORECASE)
                    if _ft_m:
                        _mv_ft_used = int(_ft_m.group(1))
                    elif _cs_m:
                        _mv_ft_used = int(_cs_m.group(1)) * 5
                    elif "mouvement" in _t_low_consume:
                        _mv_ft_used = 30 # Fallback si distance non précisée
                        
                    _tr_check = _get_turn_res(name)
                    # Facturer le Dash s'il est utilisé (même s'il est caché sous "Type: Mouvement")
                    if _is_dash_consume and _mv_ft_used > _tr_check["movement_ft"]:
                        if name == "Thorne" and _tr_check["bonus"]:
                            _consume_turn_res(name, "bonus")
                        elif _tr_check["action"]:
                            _consume_turn_res(name, "action")
                            
                    # Soustraire la distance manuellement de la réserve si le label principal n'était pas "Mouvement"
                    if "mouvement" not in _t_low_consume and _mv_ft_used > 0:
                        _tr_check["movement_ft"] = max(0, _tr_check["movement_ft"] - _mv_ft_used)
                        
                # Consommer le type de base de l'action
                try:
                    _consume_turn_res(name, _type_lbl, movement_ft=_mv_ft_used)
                    _app._update_agent_combat_prompts()
                except Exception as _e_upd:
                    print(f"[engine_receive] Erreur update prompts : {_e_upd}")

            # ── Narrative publique + trigger de tour (sans tableau de ressources) ──
            # On garantit l'envoi du trigger pour éviter le blocage, même si l'UI 
            # a désynchronisé active_combatant de façon asynchrone pendant les boîtes de dialogue.
            if COMBAT_STATE["active"]:
                _tec_msg  = _build_tour_en_cours(name)
                _tr       = _get_turn_res(name)
                _has_res  = _tr["action"] or _tr["bonus"]

                # [UI MJ] : tableau complet dans l'interface (pas dans le GroupChat)
                _app.msg_queue.put({
                    "sender": "⚔️ Combat",
                    "text":   _tec_msg,
                    "color":  "#5577aa",
                })

                # [GroupChat public] : narrative de l'action pour tous les agents
                # _feedback_for_narr : résultat de l'action si disponible
                _feedback_for_narr = locals().get("feedback", "")
                _narr_action = _build_action_narrative(
                    name, _type_lbl, _intention, _cible,
                    _feedback_for_narr or "",
                )
                # NOTE : NE PAS injecter _narr_action ici.
                # Il serait en position -4 dans groupchat.messages (avant le message
                # original de l'agent, ajouté ci-dessous), donc hors de la fenêtre
                # recent_msgs[-3:] de _filter_messages_for_agent → invisible aux agents.
                # → Injection différée après message + _dmf éventuel (voir plus bas).

                # [Trigger de continuation] : adapté pour les réactions hors-tour
                _active_comb = COMBAT_STATE.get("active_combatant")
                _is_out_of_turn = (name != _active_comb)

                if _is_out_of_turn:
                    # Si c'était une réaction hors-tour, on rend la parole au VRAI personnage actif
                    _app._pending_combat_trigger = f"La réaction de {name} est résolue. Reprends ton tour, {_active_comb}."
                    _gc_trigger = f"La réaction de {name} est résolue. Reprends ton tour, {_active_comb}."
                else:
                    _app._pending_combat_trigger = (
                        f"Tu as encore des actions disponibles. Continue ton tour, {name}."
                        if _has_res else
                        f"{name}, plus d'actions disponibles. Envoie [ACTION] de type 'Fin de tour' ou déclare un mouvement."
                    )
                    _gc_trigger = (
                        f"Continue ton tour, {name}."
                        if _has_res else
                        f"{name}, plus d'actions disponibles. Envoie [ACTION] de type 'Fin de tour' ou déclare un mouvement."
                    )

                _original_receive(self_mgr, message, sender, request_reply=False, silent=True)

                # ── Injection différée du feedback ATTAQUE RATÉE ─────────────────
                # Doit se faire APRÈS l'ajout du message original de l'agent (ci-dessus)
                # pour atterrir dans la fenêtre recent_msgs[-3:] de _filter_messages_for_agent.
                _dmf = locals().get("_deferred_miss_feedback")
                if _dmf:
                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": _dmf, "name": "Alexis_Le_MJ"},
                        sender, request_reply=False, silent=True,
                    )

                # ── Résumé public de l'action (visible par TOUS les agents) ──────
                # Injecté ICI — après le message original de l'agent ET après le
                # _dmf éventuel — pour être en position -2 dans groupchat.messages
                # (juste avant le trigger), garantissant sa présence dans
                # recent_msgs[-3:] de _filter_messages_for_agent.
                # Cas raté  : "• Thorne[action] : … mais rate sa cible de peu."
                # Cas touché : "• Thorne[action] : … et porte un coup dévastateur."
                _original_receive(
                    self_mgr,
                    {"role": "user", "content": _narr_action, "name": "Alexis_Le_MJ"},
                    sender, request_reply=False, silent=False,
                )

                _original_receive(
                    self_mgr,
                    {"role": "user", "content": _gc_trigger, "name": "Alexis_Le_MJ"},
                    sender,
                    request_reply=True,
                    silent=False,
                )
                return

            _original_receive(self_mgr, message, sender, request_reply, silent)
            return

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
                    [n for n in PLAYER_NAMES if n in[a.name for a in gc.agents]]
                    if _d_cible == "tous"
                    else[_d_cible] if _d_cible in PLAYER_NAMES
                    else[]
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

                        # ── Boîte skill_check_confirm pour le jet ─────────────
                        # Le dé est lancé directement dans le widget UI.
                        # Le MJ voit les deux d20, choisit Avantage/Normal/Désavantage,
                        # valide ou refuse — le résultat est injecté à l'agent.
                        import threading as _th_jet
                        _jet_ev  = _th_jet.Event()
                        _jet_res = {}

                        def _jet_cb(confirmed, total=0, mj_note="",
                                    _ev=_jet_ev, _res=_jet_res,
                                    _tgt2=_tgt, _carac2=_carac, _dc2=_dc,
                                    _bonus2=_real_bonus,
                                    _label2=_action_label, _dc_str2=_dc_str):
                            """
                            Callback skill_check_confirm.
                            confirmed=True  → total = résultat validé par le MJ.
                            confirmed=False → jet refusé.
                            """
                            _res["confirmed"] = confirmed
                            _res["total"]     = total
                            _res["mj_note"]   = mj_note
                            if confirmed:
                                _sign = "+" if _bonus2 >= 0 else ""
                                _raw  = total - _bonus2
                                _crit = ""
                                if _raw == 20: _crit = " 🎯 CRITIQUE!"
                                elif _raw == 1: _crit = " ☠ FUMBLE"
                                _dc_result = ""
                                if _dc2:
                                    try:
                                        _dc_result = (
                                            " ✅ SUCCÈS" if total >= int(_dc2) else " ❌ ÉCHEC"
                                        )
                                    except Exception:
                                        pass
                                _roll_txt = (
                                    f"# {_tgt2} — {_label2} {_carac2}{_dc_str2}\n"
                                    f"  Résultat : **{total}**{_crit}{_dc_result}"
                                    + (f"\n  Note MJ : {mj_note}" if mj_note else "")
                                )
                                _app.msg_queue.put({
                                    "sender": f"🎲 {_tgt2}",
                                    "text":   _roll_txt,
                                    "color":  "#aaddff",
                                })
                                # Stocker la directive narrative pour le thread AutoGen
                                # NE PAS appeler _original_receive ici : on est dans le
                                # thread Tk (callback bouton), ce qui bloquerait l'UI
                                # le temps de l'appel LLM complet (polling make_thinking_wrapper).
                                _res["narr_instr"] = (
                                    f"[RÉSULTAT SYSTÈME — JET]\n"
                                    f"{_label2} {_carac2}{_dc_str2} pour {_tgt2} : "
                                    f"total = {total}{_crit}{_dc_result}"
                                    + (f"\n\nNote MJ : {mj_note}" if mj_note else "")
                                    + f"\n\n[INSTRUCTION]\nNarre en 1 phrase comment {_tgt2} vit "
                                    f"physiquement ce moment. Ne mentionne pas le chiffre."
                                )
                            _ev.set()  # Débloque le thread AutoGen immédiatement

                        # Détecter avantage/désavantage dans le contexte récent
                        _has_adv_ctx = False
                        _has_dis_ctx = False
                        try:
                            _gc_obj = _gc()
                            for _ctx_m in reversed((_gc_obj.messages if _gc_obj else [])[-6:]):
                                _ctx_c = str(_ctx_m.get("content", "")).lower()
                                if any(k in _ctx_c for k in ("avantage", "advantage")):
                                    _has_adv_ctx = True
                                    break
                                if any(k in _ctx_c for k in ("désavantage", "disadvantage")):
                                    _has_dis_ctx = True
                                    break
                        except Exception:
                            pass

                        _app._register_approval_event(_jet_ev)
                        _app.msg_queue.put({
                            "action":           "skill_check_confirm",
                            "char_name":        _tgt,
                            "skill_label":      _carac or _action_label,
                            "stat_label":       _carac,
                            "bonus":            _real_bonus,
                            "dc":               str(_dc) if _dc else None,
                            "has_advantage":    _has_adv_ctx,
                            "has_disadvantage": _has_dis_ctx,
                            "resume_callback":  _jet_cb,
                        })
                        _jet_ev.wait(timeout=300)
                        _app._unregister_approval_event(_jet_ev)
                        # Injecter la directive narrative depuis le thread AutoGen
                        # (pas depuis le callback Tk pour ne pas bloquer l'UI)
                        _narr_to_inject = _jet_res.get("narr_instr", "")
                        if _narr_to_inject:
                            _original_receive(
                                self_mgr,
                                {"role": "user", "content": _narr_to_inject, "name": "Alexis_Le_MJ"},
                                sender, request_reply=True, silent=False,
                            )
                        continue
                    else:
                        continue

                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": _instr, "name": "Alexis_Le_MJ"},
                        sender, request_reply=False, silent=True,
                    )

        # ── Rebuild prompts avant l'appel normal (messages MJ) ─────────────
        # Garantit que les agents ont le contexte frais (scène, quêtes, sorts,
        # inventaire, sessions…) avant chaque réponse, y compris [PAROLE_SPONTANEE].
        if name == "Alexis_Le_MJ" and not is_system:
            try:
                _app._rebuild_agent_prompts()
            except Exception:
                pass

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

        # ── Journal narratif et Historique de Combat ─────────────────────────
        if not is_system and content and str(content).strip() not in ("[SILENCE]", ""):
            _chat_log.log_message(name, str(content))
            
            if COMBAT_STATE.get("active"):
                try:
                    from combat_tracker_state import add_combat_history
                    if name in PLAYER_NAMES:
                        clean_rp = ACTION_PATTERN.sub("", str(content)).strip()
                        if clean_rp and clean_rp != "[SILENCE]":
                            clean_rp = _strip_stars(clean_rp).replace('\n', ' ')
                            add_combat_history(f'• {name} : "{clean_rp}"')
                    elif name == "Alexis_Le_MJ":
                        if "[DIRECTIVE" not in str(content) and "[TOUR EN COURS" not in str(content) and "[RÉSULTAT" not in str(content):
                            clean_mj = _strip_stars(str(content)).replace('\n', ' ')
                            add_combat_history(f'• MJ : {clean_mj}')
                except Exception:
                    pass

        # ── Mémoires contextuelles ────────────────────────────────────────────
        if not is_system and content and str(content).strip() not in ("[SILENCE]", ""):
            # On suspend la mise à jour des mémoires en plein combat
            # pour éviter le bruit et la confusion.
            if not COMBAT_STATE.get("active"):
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