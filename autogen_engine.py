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

from engine_agents   import build_agents_and_tools, combat_speaker_selector, build_regle_outils
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
                    "text":   f"{char_name} entre dans la scene.",
                    "color":  "#666677",
                })
                # ── Injecter un message MJ dans l'historique AutoGen ─────────
                # Sans ça, combat_speaker_selector ignore le nouvel arrivant :
                # aucun message MJ ne le cible, donc il n'est jamais sélectionné.
                # Le message injecté contient le nom explicite → Cas 1 du
                # sélecteur → ce PJ et seulement lui est appelé en priorité.
                if hasattr(self, "groupchat") and self.groupchat.messages is not None:
                    self.groupchat.messages.append({
                        "role":    "user",
                        "name":    "Alexis_Le_MJ",
                        "content": (
                            f"[ENTRÉE EN SCÈNE] {char_name} rejoint le groupe. "
                            f"{char_name}, quelle est ta première réaction ?"
                        ),
                    })
                # ─────────────────────────────────────────────────────────────
            elif not active and agent in agents:
                agents.remove(agent)
                # ── Nettoyer le message [ENTRÉE EN SCÈNE] injecté ───────────
                # Sans ça, _find_last_mj_msg() peut encore trouver ce message
                # et sélectionner l'agent comme prochain speaker malgré son
                # retrait de groupchat.agents (ex: [PAROLE_SPONTANEE] vide).
                if hasattr(self, "groupchat") and self.groupchat.messages is not None:
                    self.groupchat.messages = [
                        m for m in self.groupchat.messages
                        if not (
                            m.get("name") == "Alexis_Le_MJ"
                            and f"[ENTRÉE EN SCÈNE] {char_name}" in str(m.get("content", ""))
                        )
                    ]
                # ─────────────────────────────────────────────────────────────
                self.msg_queue.put({
                    "sender": "⚙ Scène",
                    "text":   f"{char_name} quitte la scène.",
                    "color":  "#666677",
                })

        # _sync_groupchat_agents est défini dans ui_setup_mixin.py — pas d'override ici.
        # On garde _sync_gc comme alias interne si besoin depuis autogen_engine.
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

                # ── Fallback automatique sur quota épuisé ─────────────────────
                _FALLBACK_CHAIN = [
                    "gemini-3-flash-preview",
                    "gemini-3.1-flash-lite-preview",
                    "gemini-2.5-pro",
                    "gemini-2.5-flash",
                    "groq/meta-llama/llama-4-scout-17b-16e-instruct",
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
                            # ── Détermine les agents concernés (lecture seule) ─
                            # campaign_state.json["characters"][*]["llm"] est en
                            # LECTURE SEULE — le fallback ne l'écrit jamais.
                            # Seul app_config.json est mis à jour sur disque.
                            switched = []
                            for _cn in PLAYER_NAMES:
                                current = (_char_state.get(_cn, {}).get("llm", "")
                                           or get_agent_config(_cn).get("model", ""))
                                if current == exhausted_model:
                                    switched.append(_cn)

                            if switched:
                                try:
                                    cfg = APP_CONFIG
                                    for _cn in switched:
                                        cfg.setdefault("agents", {}).setdefault(_cn, {})["model"] = next_model
                                    save_app_config(cfg)
                                    reload_app_config()
                                except Exception as _ae:
                                    print(f"[Auto-Fallback] Erreur écriture app_config : {_ae}")

                                # ── Mise à jour des agents EN MÉMOIRE ──────────
                                # Sans ça, les agents continuent d'utiliser
                                # l'ancien modèle épuisé et le fallback boucle.
                                for _cn in switched:
                                    _agent_obj = self._agents.get(_cn)
                                    if _agent_obj is None:
                                        continue
                                    try:
                                        _new_cfg = build_llm_config(next_model, temperature=0.7)
                                        _agent_obj.llm_config = _new_cfg
                                        import autogen as _ag
                                        _agent_obj.client = _ag.OpenAIWrapper(
                                            **{k: v for k, v in _new_cfg.items()
                                               if k != "functions"}
                                        )
                                        print(f"[Auto-Fallback] Agent {_cn} mis à jour en mémoire → {next_model}")
                                    except Exception as _me:
                                        print(f"[Auto-Fallback] Erreur mise à jour agent {_cn} en mémoire : {_me}")

                                # ── Mise à jour de _char_state EN MÉMOIRE ─────
                                # campaign_state.json ne stocke plus le modèle
                                # de fallback → on met à jour _char_state
                                # directement pour que _cfg() voie next_model.
                                try:
                                    for _cn in switched:
                                        _char_state.setdefault(_cn, {})["llm"] = next_model
                                except Exception as _cse:
                                    print(f"[Auto-Fallback] Erreur mise à jour _char_state : {_cse}")

                                print(f"[Auto-Fallback] {exhausted_model} → {next_model} pour : {switched}")
                                self.msg_queue.put({
                                    "sender": "⚠️ Système (Auto-Fallback)",
                                    "text": (
                                        f"⚡ Quota épuisé : {exhausted_model}\n"
                                        f"✅ Basculement automatique → {next_model}\n"
                                        f"Agents concernés : {', '.join(switched)}\n"
                                        f"app_config.json mis à jour.\n"
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