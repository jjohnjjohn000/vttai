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

from llm_config    import build_llm_config, _default_model, StopLLMRequested
from app_config    import (get_agent_config, get_chronicler_config,
                           get_groupchat_config, APP_CONFIG,
                           save_app_config, reload_app_config)
from state_manager import (
    load_state, save_state, get_npcs, get_active_characters,
)
from chat_log_writer import ChatLogWriter
from combat_map_panel import get_map_prompt

from engine_agents   import build_agents_and_tools, combat_speaker_selector
from engine_spell_mj import build_pnj_patterns
from engine_receive  import EngineContext, build_patched_receive


class AutogenEngineMixin:
    """Mixin pour DnDApp — moteur AutoGen complet."""

    def run_autogen(self):
        import autogen  # lazy — gRPC démarre ici, bien après Tk.mainloop()

        # ── Voix PNJ dans le mapping TTS ─────────────────────────────────────
        try:
            from voice_interface import VOICE_MAPPING, SPEED_MAPPING
            for npc in get_npcs():
                key = f"__npc__{npc['name']}"
                VOICE_MAPPING[key] = npc.get("voice", "fr-FR-HenriNeural")
                SPEED_MAPPING[key] = npc.get("speed", "+0%")
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

        def _cfg(char_name: str) -> dict:
            model = (_char_state.get(char_name, {}).get("llm", "")
                     or get_agent_config(char_name).get("model", "")
                     or _default_model)
            temp  = get_agent_config(char_name).get("temperature", 0.7)
            return build_llm_config(model, temperature=temp)

        def _provider_label(char_name: str) -> str:
            model = (_char_state.get(char_name, {}).get("llm", "")
                     or get_agent_config(char_name).get("model", "")
                     or _default_model)
            if model.startswith("groq/"):        return f"Groq ({model[5:]})"
            if model.startswith("openrouter/"): return f"OpenRouter ({model[11:]})"
            if model.startswith("deepseek/"):   return f"DeepSeek ({model[9:]})"
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
        _PNJ_BASE = ["Ismark", "Strahd", "Ireena", "Madam Eva", "Rahadin",
                     "Viktor", "Morgantha", "Gil", "Mart", "Donavich", "Dori",
                     "Gustav", "Tavernier", "Garde", "Maire"]
        try:
            _state_pnj = load_state()
            _dynamic_pnj = (
                [n["name"] for n in _state_pnj.get("npcs", []) if n.get("name")]
                + [n["name"] for n in _state_pnj.get("group_npcs", []) if n.get("name")]
            )
            PNJ_NAMES = list({*_PNJ_BASE, *_dynamic_pnj})
        except Exception:
            PNJ_NAMES = _PNJ_BASE

        pnj_patterns = build_pnj_patterns(PNJ_NAMES)

        # ── GroupChat ────────────────────────────────────────────────────────
        _gc_cfg    = get_groupchat_config()
        _chron_cfg = get_chronicler_config()
        _manager_llm = build_llm_config(
            _chron_cfg.get("model", _default_model),
            temperature=_chron_cfg.get("temperature", 0.3),
        )

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
            max_round=_gc_cfg.get("max_round", 100),
            speaker_selection_method=combat_speaker_selector,
            allow_repeat_speaker=_gc_cfg.get("allow_repeat_speaker", False),
        )
        manager = autogen.GroupChatManager(
            groupchat=self.groupchat,
            llm_config=_manager_llm
        )

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

        patched_receive = build_patched_receive(ctx, groupchat_ref)

        # Substitution atomique de classe (safe gRPC, pas de MethodType sur instance)
        manager.__class__ = type(
            "PatchedGroupChatManager",
            (manager.__class__,),
            {"receive": patched_receive}
        )

        # ── Méthode _sync_groupchat_agents (utilisée par character_mixin) ──────
        # Doit rester sur self, référence le groupchat construit ici.
        _all_pa = _all_player_agents  # capture locale

        def _sync_groupchat_agents(char_name: str, active: bool):
            """Ajoute ou retire un agent du groupchat en cours de session."""
            agent = _all_pa.get(char_name)
            if agent is None:
                return
            agents = self.groupchat.agents
            if active and agent not in agents:
                agents.append(agent)
                self.msg_queue.put({
                    "sender": "⚙ Scène",
                    "text":   f"{char_name} rejoint la scène.",
                    "color":  "#666677",
                })
            elif not active and agent in agents:
                agents.remove(agent)
                self.msg_queue.put({
                    "sender": "⚙ Scène",
                    "text":   f"{char_name} quitte la scène.",
                    "color":  "#666677",
                })

        import types as _types
        self._sync_groupchat_agents = _types.MethodType(
            lambda self_inner, n, a: _sync_groupchat_agents(n, a), self
        )
        # Version simple (pas de MethodType trick nécessaire — closure directe)
        self._sync_gc = _sync_groupchat_agents

        # ── Démarrage ────────────────────────────────────────────────────────
        self.msg_queue.put({
            "sender": "Système",
            "text":   "⚔️ Tous les joueurs sont à la table. À vous de lancer la partie (Texte ou 🎤)...",
            "color":  "#888888"
        })

        self._autogen_thread_id = threading.current_thread().ident

        self._set_waiting_for_mj(True)
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

                # ── Fallback automatique sur quota épuisé ─────────────────────
                _FALLBACK_CHAIN = [
                    "gemini-3.1-pro-preview",
                    "gemini-3.1-flash-lite-preview",
                    "gemini-2.5-pro",
                    "groq/meta-llama/llama-4-scout-17b-16e-instruct",
                    "gemini-2.5-flash",
                    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
                    "openrouter/mistralai/mistral-small-3.1-24b-instruct:free",
                    "openrouter/arcee-ai/trinity-large-preview:free",
                ]

                if is_quota_error:
                    try:
                        exhausted_model = None
                        for candidate in _FALLBACK_CHAIN:
                            bare = candidate.split("/")[-1]
                            if candidate in err_msg or bare in err_msg:
                                exhausted_model = candidate
                                break

                        if exhausted_model is None:
                            for _cn in PLAYER_NAMES:
                                _m = get_agent_config(_cn).get("model", "")
                                if _m and any(kw in err_msg for kw in ["gemini","groq","llama","arcee"]):
                                    exhausted_model = _m
                                    break

                        next_model = None
                        if exhausted_model and exhausted_model in _FALLBACK_CHAIN:
                            idx = _FALLBACK_CHAIN.index(exhausted_model)
                            if idx + 1 < len(_FALLBACK_CHAIN):
                                next_model = _FALLBACK_CHAIN[idx + 1]

                        if next_model:
                            switched = []
                            try:
                                state = load_state()
                                for _cn in PLAYER_NAMES:
                                    current = (_char_state.get(_cn, {}).get("llm", "")
                                               or get_agent_config(_cn).get("model", ""))
                                    if current == exhausted_model:
                                        state.setdefault("characters", {}).setdefault(_cn, {})["llm"] = next_model
                                        switched.append(_cn)
                                if switched:
                                    save_state(state)
                            except Exception as _se:
                                print(f"[Auto-Fallback] Erreur écriture campaign_state : {_se}")

                            if switched:
                                try:
                                    cfg = APP_CONFIG
                                    for _cn in switched:
                                        cfg.setdefault("agents", {}).setdefault(_cn, {})["model"] = next_model
                                    save_app_config(cfg)
                                    reload_app_config()
                                except Exception as _ae:
                                    print(f"[Auto-Fallback] Erreur écriture app_config : {_ae}")
                                print(f"[Auto-Fallback] {exhausted_model} → {next_model} pour : {switched}")
                                self.msg_queue.put({
                                    "sender": "⚠️ Système (Auto-Fallback)",
                                    "text": (
                                        f"⚡ Quota épuisé : {exhausted_model}\n"
                                        f"✅ Basculement automatique → {next_model}\n"
                                        f"Agents concernés : {', '.join(switched)}\n"
                                        f"app_config.json et campaign_state.json mis à jour.\n"
                                        f"Tapez un message pour reprendre (historique conservé)."
                                    ),
                                    "color": "#FF9800",
                                })
                            else:
                                self.msg_queue.put({
                                    "sender": "⚠️ Système (Quota)",
                                    "text": (
                                        f"⚡ Quota épuisé ({exhausted_model or 'modèle inconnu'}) "
                                        f"mais aucun agent ne l'utilisait directement.\n"
                                        f"Le fallback automatique d'AutoGen a dû prendre le relais.\n"
                                        f"Tapez un message pour reprendre."
                                    ),
                                    "color": "#FF9800",
                                })
                        else:
                            self.msg_queue.put({
                                "sender": "⚠️ Système (Quota total)",
                                "text": (
                                    f"❌ Tous les modèles de la chaîne de fallback sont épuisés.\n"
                                    f"Dernier modèle tenté : {exhausted_model or 'inconnu'}\n"
                                    f"💡 Attendez la réinitialisation des quotas ou ajoutez "
                                    f"une clé API supplémentaire dans .env."
                                ),
                                "color": "#F44336",
                            })

                    except Exception as switch_err:
                        print(f"[Auto-Fallback] Erreur basculement : {switch_err}")
                        self.msg_queue.put({
                            "sender": "⚠️ Système (Auto-Fallback)",
                            "text": (
                                f"❌ Quota épuisé ET échec du basculement automatique.\n"
                                f"Détail : {err_msg}\n\n"
                                f"Erreur interne : {switch_err}\n"
                                f"💡 Modifiez manuellement le modèle dans app_config.json."
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