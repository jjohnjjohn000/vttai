"""
autogen_engine.py — Orchestrateur AutoGen : démarre la boucle de jeu complète.

Fournit AutogenEngineMixin à injecter dans DnDApp :
  - run_autogen : initialise agents, groupchat, patched_receive et game loop

Ce fichier est délibérément mince — toute la logique D&D est dans :
  engine_agents.py    → création des agents, règles, outils, speaker selector
  engine_mechanics.py → stats, jets de dés, actions, sorts
  engine_spell_mj.py  → helpers sorts, parseur MJ, patterns PNJ
  engine_receive.py   → build_patched_receive (tous les guards + interceptions)

Prérequis sur l'instance hôte (DnDApp) :
  self.msg_queue, self.audio_queue, self.groupchat, self.root,
  self._agents, self._base_system_msgs, self._autogen_thread_id,
  self._llm_running, self._waiting_for_mj,
  self._pending_interrupt_input, self._pending_interrupt_display,
  self._pending_combat_trigger,
  self.CHAR_COLORS, self.wait_for_input(), self._set_waiting_for_mj(),
  self._set_llm_running(), self._update_agent_combat_prompts(),
  self._update_contextual_memories(), self._on_pc_turn_ended(),
  self._refresh_char_stats()
"""

import threading
import copy
import concurrent.futures as _cf

from llm_config    import build_llm_config, _default_model, StopLLMRequested


# ─── Timeout par appel LLM ───────────────────────────────────────────────────

class LLMTimeoutError(Exception):
    """Levée quand un appel LLM dépasse _LLM_TIMEOUT_SEC secondes."""

_LLM_TIMEOUT_SEC = 300   # secondes avant déclenchement du fallback (augmenté pour les gros contextes)

# ─── Chaîne de fallback (partagée entre timeout et quota épuisé) ─────────────

_FALLBACK_CHAIN = [
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-3-flash-preview",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "groq/meta-llama/llama-4-scout-17b-16e-instruct",
    "openrouter/mistralai/mistral-small-3.1-24b-instruct:free",
    "openrouter/arcee-ai/trinity-large-preview:free",
]
from app_config    import (get_agent_config, get_chronicler_config,
                           get_groupchat_config, get_combat_config,
                           APP_CONFIG,
                           save_app_config, reload_app_config)
from state_manager import (
    load_state, save_state, get_npcs, get_active_characters,
)
from chat_log_writer import ChatLogWriter
from combat_map_panel import get_map_prompt

from engine_agents   import build_agents_and_tools, combat_speaker_selector, build_regle_outils
from engine_spell_mj import build_pnj_patterns
from engine_receive  import EngineContext, build_patched_receive


def _get_combat_llm_model() -> str:
    """Retourne le modèle LLM combat configuré (app_config.json → combat.model)."""
    return get_combat_config().get("model", "gemini-3.1-flash-lite")
_PLAYER_NAMES_COMBAT = ["Kaelen", "Elara", "Thorne", "Lyra"]


class AutogenEngineMixin:
    """Mixin pour DnDApp — moteur AutoGen complet."""

    def _set_combat_llm(self, active: bool) -> None:
        """Bascule les agents PJ vers le modèle léger en combat, ou restaure.

        active=True  → _COMBAT_LLM_MODEL pour tous les PJ
        active=False → llm_config + client d'origine restaurés

        Réplique le pattern du fallback automatique : met à jour llm_config
        ET recrée le OpenAIWrapper (client) pour que la prochaine complétion
        utilise immédiatement le nouveau modèle.
        Appel thread-safe depuis le thread Tk (même pattern que _rebuild_agent_prompts).
        """

        agents = getattr(self, "_agents", None)
        if not agents:
            return  # moteur pas encore démarré — silencieux

        try:
            import autogen as _ag
        except ImportError:
            return

        if active:
            # ── Sauvegarde one-shot des configs originales ────────────────────
            if not hasattr(self, "_pre_combat_llm") or not self._pre_combat_llm:
                self._pre_combat_llm = {
                    name: {
                        "llm_config": copy.deepcopy(agents[name].llm_config),
                        "client":     agents[name].client,
                    }
                    for name in _PLAYER_NAMES_COMBAT
                    if name in agents
                }

            # ── Création de la config combat ──────────────────────────────────
            _combat_model = _get_combat_llm_model()
            combat_cfg = build_llm_config(_combat_model, temperature=0.7)

            for name in _PLAYER_NAMES_COMBAT:
                agent = agents.get(name)
                if agent is None:
                    continue
                try:
                    old_cfg = agent.llm_config or {}
                    new_cfg = copy.deepcopy(combat_cfg)
                    
                    # PRÉSERVATION DES OUTILS CRITIQUE 
                    # Sinon, les agents perdent la capacité d'attaquer ou lancer des sorts en combat.
                    if "tools" in old_cfg:
                        new_cfg["tools"] = copy.deepcopy(old_cfg["tools"])
                    if "functions" in old_cfg:
                        new_cfg["functions"] = copy.deepcopy(old_cfg["functions"])

                    agent.llm_config = new_cfg
                    agent.client = _ag.OpenAIWrapper(
                        # Exclusion des clés tools/functions pour éviter les TypeErrors sur l'instanciation de l'API OpenAI
                        **{k: v for k, v in new_cfg.items() if k not in ("functions", "tools")}
                    )
                except Exception as _e:
                    print(f"[CombatLLM] Erreur switch agent {name} : {_e}")

            self.msg_queue.put({
                "sender": "⚙️ Système",
                "text":   f"⚔️ Mode combat — PJ basculés vers `{_combat_model}`",
                "color":  "#ff9944",
            })

        else:
            # ── Restauration des configs d'origine ───────────────────────────
            saved = getattr(self, "_pre_combat_llm", {})
            for name, snap in saved.items():
                agent = agents.get(name)
                if agent is None:
                    continue
                try:
                    agent.llm_config = snap["llm_config"]
                    agent.client     = snap["client"]
                except Exception as _e:
                    print(f"[CombatLLM] Erreur restauration agent {name} : {_e}")
            self._pre_combat_llm = {}

            self.msg_queue.put({
                "sender": "⚙️ Système",
                "text":   "🏕 Mode exploration — PJ restaurés vers leur modèle d'origine",
                "color":  "#44bb88",
            })

    def run_autogen(self):
        import autogen  # lazy — gRPC démarre ici, bien après Tk.mainloop()
        from engine_agents import ensure_autogen_patched
        ensure_autogen_patched()

        # ── Voix PNJ dans le mapping TTS ─────────────────────────────────────
        try:
            from voice_interface import VOICE_MAPPING, SPEED_MAPPING, PITCH_MAPPING
            for npc in get_npcs():
                bare_key = npc['name']
                key = f"__npc__{bare_key}"
                # Populate both the prefixed key (for Piper selection) and bare key (for UI lookup)
                for k in (key, bare_key):
                    VOICE_MAPPING[k] = npc.get("voice", "fr-FR-HenriNeural")
                    SPEED_MAPPING[k] = npc.get("speed", "+0%")
                    PITCH_MAPPING[k] = npc.get("pitch", "+0%")
        except Exception as e:
            print(f"[NPC] Erreur chargement voix PNJ : {e}")

        # ── Résumé de campagne au démarrage ───────────────────────────────────
        try:
            state   = load_state()
            summary = state.get("session_summary", "Aucun résumé pour le moment.")
            if summary and summary != "Aucun résumé pour le moment.":
                self.msg_queue.put({
                    "sender": "Chroniqueur IA",
                    "text":   f"📜 Précédemment dans votre campagne :\n{summary}",
                    "color":  "#FF9800"
                })
        except Exception as e:
            print(f"Erreur lors du chargement du résumé : {e}")

        self.msg_queue.put({
            "sender": "Système",
            "text":   "⚔️ MOTEUR INITIALISÉ. Connexion aux LLMs en cours...",
            "color":  "#ffcc00"
        })

        # ── Journal narratif ─────────────────────────────────────────────────
        _chat_log = ChatLogWriter()
        self.msg_queue.put({
            "sender": "📋 Système",
            "text":   f"Journal de session ouvert → {_chat_log.path}",
            "color":  "#607d8b",
        })

        # ── Config LLM par personnage ─────────────────────────────────────────
        _char_state = load_state().get("characters", {})

        # ── SECONDARY LLM — null llm_session_override at program start ────────
        # llm_session_override is written to campaign_state.json whenever the
        # GM picks a model from the UI dropdown during a session.  It MUST be
        # cleared on every startup so a stale value from a previous session does
        # not silently shadow the per-agent "llm" field in campaign_state.json.
        #
        # Priority order (enforced by _cfg below):
        #   1. campaign_state.json "llm"   — permanent per-agent setting (default)
        #   2. llm_session_override        — set via UI dropdown during THIS session
        #   3. app_config model            — global fallback
        #   4. _default_model              — last-resort fallback
        # The existing fallback chain in build_llm_config() kicks in after step 4.
        _null_startup_state = load_state()
        _null_modified = False
        for _cn, _cd in _null_startup_state.get("characters", {}).items():
            if _cd.get("llm_session_override"):
                _cd["llm_session_override"] = ""
                _char_state.setdefault(_cn, {})["llm_session_override"] = ""
                _null_modified = True
        if _null_modified:
            save_state(_null_startup_state)
        print("[LLM] Startup: llm_session_override nulled for all characters.")
        del _null_startup_state, _null_modified

        def _cfg(char_name: str) -> dict:
            # Priority 1 — no secondary LLM set this session: use campaign_state "llm"
            # Priority 2 — secondary LLM set via UI dropdown:  use llm_session_override
            # Priority 3 — neither available:                  app_config → _default_model
            #              (build_llm_config then runs the full provider fallback chain)
            cs_char = _char_state.get(char_name, {})
            model = (cs_char.get("llm_session_override", "")
                     or cs_char.get("llm", "")
                     or get_agent_config(char_name).get("model", "")
                     or _default_model)
            temp  = get_agent_config(char_name).get("temperature", 0.7)
            return build_llm_config(model, temperature=temp)

        def _provider_label(char_name: str) -> str:
            cs_char = _char_state.get(char_name, {})
            model = (cs_char.get("llm_session_override", "")
                     or cs_char.get("llm", "")
                     or get_agent_config(char_name).get("model", "")
                     or _default_model)
            if model.startswith("groq/"):       return f"Groq ({model[5:]})"
            if model.startswith("openrouter/"): return f"OpenRouter ({model[11:]})"
            if model.startswith("deepseek/"):   return f"DeepSeek ({model[9:]})"
            if model.startswith("zyphra/"):     return f"Zyphra ({model[7:]})"
            return f"Gemini ({model})"

        providers_info = " | ".join(
            f"{n}: {_provider_label(n)}" for n in ["Kaelen", "Elara", "Thorne", "Lyra"]
        )
        self.msg_queue.put({
            "sender": "Système",
            "text":   f"🧠 Modèles chargés : {providers_info}",
            "color":  "#aaaaff"
        })

        # ── Création des agents ───────────────────────────────────────────────
        built = build_agents_and_tools(autogen, _cfg, self)
        mj_agent     = built["mj"]
        kaelen_agent = built["kaelen"]
        elara_agent  = built["elara"]
        thorne_agent = built["thorne"]
        lyra_agent   = built["lyra"]

        self._agents = built["agents"]
        self._base_system_msgs = {
            name: agent.system_message
            for name, agent in self._agents.items()
        }

        # Partie personnage uniquement (sans le bloc de règles) — utilisée par
        # _rebuild_agent_prompts() pour reconstruire base avec la bonne version
        # des règles (hors combat ou en combat) selon COMBAT_STATE["active"].
        _hc_regle = build_regle_outils(combat_mode=False)
        self._base_char_msgs = {
            name: agent.system_message.replace(_hc_regle, "", 1)
            for name, agent in self._agents.items()
        }

        # ── Rebuild initial : injecte le contexte dynamique dès le démarrage ──
        # Sans ça, les agents répondent sans scène/quêtes/sorts jusqu'au premier
        # message MJ qui déclenche le rebuild dans engine_receive.py.
        try:
            self._rebuild_agent_prompts()
        except Exception as _e:
            print(f"[Init] Erreur rebuild initial prompts : {_e}")

        # ── Détermination SPELL_CASTERS ───────────────────────────────────────
        PLAYER_NAMES = ["Kaelen", "Elara", "Thorne", "Lyra"]
        try:
            _sc_state = load_state()
            SPELL_CASTERS = [
                name for name in PLAYER_NAMES
                if _sc_state.get("characters", {}).get(name, {}).get("spell_slots")
            ]
            if not SPELL_CASTERS:
                SPELL_CASTERS = ["Kaelen", "Elara", "Lyra"]
        except Exception:
            SPELL_CASTERS = ["Kaelen", "Elara", "Lyra"]

        # ── PNJ patterns ──────────────────────────────────────────────────────
        try:
            _state_pnj = load_state()
            PNJ_NAMES = list({
                n["name"]
                for src in ("npcs", "group_npcs")
                for n in _state_pnj.get(src, [])
                if n.get("name")
            })
        except Exception:
            PNJ_NAMES = []

        pnj_patterns = build_pnj_patterns(PNJ_NAMES)

        # ── GroupChat ────────────────────────────────────────────────────────
        _gc_cfg    = get_groupchat_config()
        _chron_cfg = get_chronicler_config()
        _manager_llm = build_llm_config(
            _chron_cfg.get("model", _default_model),
            temperature=_chron_cfg.get("temperature", 0.3),
        )

        try:
            print(f"\n[DEBUG CHRONICLER] Modèle configuré dans app_config.json : {_chron_cfg.get('model', _default_model)}")
            _mlist = [c.get("model") for c in _manager_llm.get("config_list", [])]
            print(f"[DEBUG CHRONICLER] Liste de fallback générée ({len(_mlist)} modèles) :")
            for i, m in enumerate(_mlist):
                print(f"   Index {i} -> {m}")
        except Exception:
            pass

        _all_player_agents = {
            "Kaelen": kaelen_agent,
            "Elara":  elara_agent,
            "Thorne": thorne_agent,
            "Lyra":   lyra_agent,
        }
        _active_names = get_active_characters()
        _active_agents = [mj_agent] + [
            agent for name, agent in _all_player_agents.items()
            if name in _active_names
        ]
        _inactive_names = [n for n in _all_player_agents if n not in _active_names]
        if _inactive_names:
            self.msg_queue.put({
                "sender": "⚙ Scène",
                "text":   f"Agents absents de la scène (silenciés) : {', '.join(_inactive_names)}",
                "color":  "#666677",
            })

        self.groupchat = autogen.GroupChat(
            agents=_active_agents,
            messages=[],
            max_round=_gc_cfg.get("max_round", 9999),
            speaker_selection_method=combat_speaker_selector,
            allow_repeat_speaker=_gc_cfg.get("allow_repeat_speaker", False),
        )

        # ── DEBUG TRAP : TrackedList pour tracer TOUTE mutation de groupchat.agents ──
        class _TrackedList(list):
            def _trace(self, op):
                import traceback
                print(f"\n[GC TRAP] agents.{op}() → {[a.name for a in self]}")
                traceback.print_stack(limit=8)
            def append(self, v):
                super().append(v)
                self._trace("append")
            def remove(self, v):
                super().remove(v)
                self._trace("remove")
            def extend(self, v):
                super().extend(v)
                self._trace("extend")
            def insert(self, i, v):
                super().insert(i, v)
                self._trace("insert")
            def pop(self, *a):
                r = super().pop(*a)
                self._trace("pop")
                return r
            def clear(self):
                super().clear()
                self._trace("clear")
            def __setitem__(self, k, v):
                super().__setitem__(k, v)
                self._trace("__setitem__")
            def __delitem__(self, k):
                super().__delitem__(k)
                self._trace("__delitem__")
            def __iadd__(self, v):
                super().__iadd__(v)
                self._trace("__iadd__")
                return self

        import time
        _t0 = time.time()
        self.groupchat.agents = _TrackedList(self.groupchat.agents)
        print(f"[GC TRAP] TrackedList installed: {[a.name for a in self.groupchat.agents]} at {_t0}")
        # ────────────────────────────────────────────────────────────────────
        manager = autogen.GroupChatManager(
            groupchat=self.groupchat,
            llm_config=_manager_llm
        )
        
        # ── INTERCEPTION DES APPELS LLM DU MANAGER (LOGS CHRONICLER) ─────────
        if hasattr(manager, "client") and manager.client is not None:
            _orig_manager_create = manager.client.create
            def _patched_manager_create(*args, **kwargs):
                from agent_logger import log_chronicler_prompt, log_chronicler_response
                _msgs = kwargs.get("messages", [])
                _sys_msg = ""
                _chat_msgs = []
                if _msgs and isinstance(_msgs, list):
                    if isinstance(_msgs[0], dict) and _msgs[0].get("role") == "system":
                        _sys_msg = str(_msgs[0].get("content", ""))
                        _chat_msgs = _msgs[1:]
                    else:
                        _chat_msgs = _msgs
                
                log_chronicler_prompt("GroupChatManager", _sys_msg, _chat_msgs)
                
                try:
                    _client = manager.client
                    _idx = getattr(_client, "_last_config_idx", "Introuvable")
                    print(f"\n[DEBUG CHRONICLER] État du client AutoGen AVANT appel :")
                    print(f"   _last_config_idx mémorisé = {_idx}")
                    
                    if isinstance(_idx, int) and hasattr(_client, "config_list"):
                        _stuck_model = _client.config_list[_idx].get("model", "Inconnu") if _idx < len(_client.config_list) else "Hors limites"
                        print(f"   Modèle ciblé par cet index pour la tentative 1 : {_stuck_model}")
                    
                    # Reset du sticky-fallback pour toujours recommencer par Gemini
                    _client._last_config_idx = 0
                    print("   [CORRECTION] _last_config_idx forcé à 0.")
                except Exception as e:
                    print(f"[DEBUG CHRONICLER] Erreur de lecture de l'index : {e}")
                
                _res = _orig_manager_create(*args, **kwargs)
                
                _resp_text = ""
                try:
                    if hasattr(_res, "choices") and _res.choices:
                        _resp_text = _res.choices[0].message.content
                    else:
                        _resp_text = str(_res)
                except Exception:
                    _resp_text = str(_res)
                
                log_chronicler_response("GroupChatManager", _resp_text)
                return _res
                
            manager.client.create = _patched_manager_create
        # ────────────────────────────────────────────────────────────────────

        _t1 = time.time()
        print(f"[GC TRAP] GroupChatManager created in {_t1 - _t0:.2f}s")
        
        # Stocker la référence du manager pour _sync_groupchat_agents
        self._groupchat_manager = manager
        
        # Donner la référence du manager aux agents pour la redirection des /msg
        for agent in _all_player_agents.values():
            agent._groupchat_manager_ref = manager

        # ── Construction du contexte patched_receive ──────────────────────────
        groupchat_ref = [self.groupchat]   # référence mutable pour les closures

        ctx = EngineContext(
            app=self,
            chat_log=_chat_log,
            player_names=PLAYER_NAMES,
            spell_casters=SPELL_CASTERS,
            pnj_names=PNJ_NAMES,
            pnj_patterns=pnj_patterns,
        )

        _t2 = time.time()
        patched_receive = build_patched_receive(ctx, groupchat_ref)
        _t3 = time.time()
        print(f"[GC TRAP] build_patched_receive returned in {_t3 - _t2:.2f}s")
        
        # Substitution atomique de classe (safe gRPC, pas de MethodType sur instance)
        manager.__class__ = type(
            "PatchedGroupChatManager",
            (manager.__class__,),
            {"receive": patched_receive}
        )
        _t4 = time.time()
        print(f"[GC TRAP] class swapped in {_t4 - _t3:.2f}s")

        # ── REDIRECTION DES CHUCHOTEMENTS (/msg) VERS LE GROUPCHAT ────────────
        # Si un agent répond directement au MJ (suite à un /msg), la réponse contourne
        # le GroupChatManager et les blocs [ACTION] ne sont pas détectés.
        # On force l'agent à rediriger sa réponse dans le GroupChat principal.
        import types
        for _agent in[kaelen_agent, elara_agent, thorne_agent, lyra_agent]:
            _agent._groupchat_manager_ref = manager
            _orig_send = _agent.send
            
            def _patched_send(self_agent, message, recipient, request_reply=None, silent=False, _os=_orig_send):
                if recipient.name == "Alexis_Le_MJ" and hasattr(self_agent, "_groupchat_manager_ref"):
                    recipient = self_agent._groupchat_manager_ref
                return _os(message, recipient, request_reply, silent)
                
            _agent.send = types.MethodType(_patched_send, _agent)

        # ── Timeout par appel LLM : wrapper generate_reply ───────────────────
        # Chaque agent.generate_reply (y compris le manager) est wrappé dans un
        # ThreadPoolExecutor à 1 worker.  Si la réponse dépasse _LLM_TIMEOUT_SEC
        # secondes, LLMTimeoutError remonte jusqu'au except Exception du game
        # loop et déclenche le même basculement automatique que pour les quotas.
        # Note : le thread sous-jacent continue jusqu'à la réponse réseau, mais
        # son résultat est simplement ignoré — aucun double-post dans le groupchat.

        def _make_timed_generate_reply(agent_obj, orig_fn, timeout_sec):
            def _timed(*args, **kwargs):
                with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                    _fut = _ex.submit(orig_fn, *args, **kwargs)
                    try:
                        return _fut.result(timeout=timeout_sec)
                    except _cf.TimeoutError:
                        raise LLMTimeoutError(
                            f"LLM timeout après {timeout_sec}s — agent : {agent_obj.name}"
                        )
            return _timed

        _agents_to_wrap = list(self._agents.values())   # manager exclu — voir ci-dessous
        # Le GroupChatManager n'est PAS wrappé : son rôle est la sélection du
        # prochain speaker (appel LLM rapide).  Si un agent joueur dépasse
        # _LLM_TIMEOUT_SEC, son propre wrapper lève LLMTimeoutError qui remonte
        # naturellement à travers le manager jusqu'au game loop.
        # Wrapper le manager ajouterait un second timer qui se déclenche en
        # premier et masque quel agent est réellement lent.
        for _agt in _agents_to_wrap:
            _agt.generate_reply = _make_timed_generate_reply(
                _agt, _agt.generate_reply, _LLM_TIMEOUT_SEC
            )



        # ── Démarrage ────────────────────────────────────────────────────────
        self.msg_queue.put({
            "sender": "Système",
            "text":   "⚔️ Tous les joueurs sont à la table. À vous de lancer la partie (Texte ou 🎤)...",
            "color":  "#888888"
        })

        self._autogen_thread_id = threading.current_thread().ident

        self._set_waiting_for_mj(True)

        # ── REPRISE OFFICIELLE DU COMBAT AU DÉMARRAGE ──
        # L'IA est complètement prête et à l'écoute. On peut enfin annoncer
        # le tour de combat et déclencher le prompt du joueur actif.
        try:
            from combat_tracker import COMBAT_STATE as _CS
            if _CS.get("active"):
                ct = getattr(self, "_combat_tracker_win", None) or getattr(self, "_combat_tracker", None)
                if ct:
                    self.root.after(0, lambda: ct._log_turn())
                    self.root.after(100, lambda: ct._trigger_pc_turn_if_needed())
                    self.root.after(100, lambda: ct._trigger_npc_turn_if_needed())
        except Exception as e:
            print(f"[Init Combat] Erreur reprise : {e}")

        premier_message = self.wait_for_input()
        self._set_waiting_for_mj(False)
        clear_hist = True

        # ── Game loop ────────────────────────────────────────────────────────
        while True:
            try:
                self._stop_event.clear()
                self._set_llm_running(True)
                mj_agent.initiate_chat(
                    manager,
                    message=premier_message,
                    clear_history=clear_hist
                )
                self._set_llm_running(False)
                break  # session terminée normalement

            except StopLLMRequested:
                self._set_llm_running(False)
                self._set_waiting_for_mj(False)
                if self._pending_interrupt_input is not None:
                    premier_message = self._pending_interrupt_input
                    self._pending_interrupt_input = None
                    if self._pending_interrupt_display is not None:
                        self.msg_queue.put(self._pending_interrupt_display)
                        self._pending_interrupt_display = None
                    self.msg_queue.put({
                        "sender": "Système",
                        "text":   "▶️ Reprise avec le nouveau message.",
                        "color":  "#aaaaaa"
                    })
                else:
                    self._pending_interrupt_display = None
                    self.msg_queue.put({
                        "sender": "Système",
                        "text":   "⏹️ LLMs arrêtés. Tapez un message pour reprendre.",
                        "color":  "#FF9800"
                    })
                    self._set_waiting_for_mj(True)
                    premier_message = self.wait_for_input()
                    self._set_waiting_for_mj(False)
                clear_hist = False

            except Exception as e:
                self._set_llm_running(False)
                import traceback
                traceback.print_exc()

                err_msg = str(e)
                is_quota_error = (
                    "RESOURCE_EXHAUSTED" in err_msg
                    or "429" in err_msg
                    or "quota" in err_msg.lower()
                )
                
                is_timeout_error = isinstance(e, LLMTimeoutError)

                print(f"\n[Fallback Trace] Exception caught. Analyzing error message...")
                print(f"[Fallback Trace] is_quota_error={is_quota_error}")
                print(f"[Fallback Trace] is_timeout_error={is_timeout_error}")
                
                # ── Erreur 400 : capacité invalide (modèle sans tool-use) ─────────
                # Certains modèles free OpenRouter ou Groq n'acceptent pas les
                # appels de fonctions/outils. AutoGen lève une Exception avec
                # le code 400 dans le message. On tente de désactiver les outils
                # pour tous les agents et de relancer SANS clear_history.
                is_tool_capability_error = (
                    "400" in err_msg
                    and any(kw in err_msg.lower()
                            for kw in ("capacit", "invalid", "capability",
                                       "tool", "function", "unsupported"))
                )
                print(f"[Fallback Trace] is_tool_capability_error={is_tool_capability_error}")
                
                if is_tool_capability_error:
                    self.msg_queue.put({
                        "sender": "⚠️ Système (Tool Error)",
                        "text": (
                            "❌ Erreur 400 — le modèle actif ne supporte pas les appels d'outils.\n"
                            f"Détail : {err_msg[:200]}\n\n"
                            "💡 Changez le modèle de l'agent concerné pour un modèle compatible "
                            "(ex: gemini-2.5-flash, gpt-4o-mini) dans le panneau de config.\n"
                            "Tapez un message pour reprendre (historique conservé)."
                        ),
                        "color": "#FF5722",
                    })
                    self._set_waiting_for_mj(True)
                    premier_message = self.wait_for_input()
                    self._set_waiting_for_mj(False)
                    clear_hist = False
                    continue

                # ── Fallback automatique natif épuisé ──────
                if is_quota_error or is_timeout_error:
                    _fallback_reason = (
                        f"Timeout LLM ({_LLM_TIMEOUT_SEC}s sans réponse)"
                        if is_timeout_error else "Quota épuisé"
                    )
                    print(f"[Fallback Trace] {_fallback_reason}. La chaîne de secours ("
                          f"normalement gérée par AutoGen) a probablement échoué.")
                    
                    self.msg_queue.put({
                        "sender": "⚠️ Système (Quota total)",
                        "text": (
                            f"❌ Tous les modèles de la chaîne de fallback sont épuisés ou inaccessibles.\n"
                            f"Détail de l'erreur : {err_msg[:250]}\n"
                            f"💡 Attendez la réinitialisation des quotas ou ajoutez "
                            f"une clé API supplémentaire dans .env."
                        ),
                        "color": "#F44336",
                    })
                else:
                    self.msg_queue.put({
                        "sender": "⚠️ Système (Crash IA)",
                        "text": (
                            "❌ L'IA a rencontré une erreur fatale et tous les modèles de secours ont échoué.\n"
                            f"Détail : {err_msg}\n\n"
                            "💡 CONSEIL : Si c'est un problème de Quota (ex: 429), attendez quelques "
                            "minutes ou changez les modèles/clés API dans le fichier .env.\n"
                            "L'application est toujours active. Tapez un nouveau message pour relancer "
                            "la partie (l'historique est conservé)."
                        ),
                        "color": "#F44336"
                    })

                # Attendre une nouvelle entrée du MJ pour retenter
                self._set_waiting_for_mj(True)
                premier_message = self.wait_for_input()
                self._set_waiting_for_mj(False)
                clear_hist = False