# ====================================================================
# FIX A — XInitThreads() EN TOUT PREMIER, AVANT MÊME `import os`
# Doit être la toute première chose exécutée. Sur certains glibc/Xlib,
# un import de ctypes ultérieur peut déjà ouvrir un Display en interne.
try:
    import ctypes as _ct
    _ct.CDLL("libX11.so.6").XInitThreads()
    print("[X11] XInitThreads() OK")
except Exception as _e:
    print(f"[X11] XInitThreads() indisponible: {_e}")
# ====================================================================

import os
import json
import urllib.request
import time
import faulthandler as _fh; _fh.enable()

# ====================================================================
# FIX B — Variables gRPC AVANT tout thread C
# "epoll1" = un seul fd-watcher compatible Tcl. "none" désactive le
# poller → deadlocks sur gRPC >= 1.46 — ne pas utiliser "none".
os.environ["GRPC_POLL_STRATEGY"] = "epoll1"
os.environ["GRPC_ENABLE_FORK_SUPPORT"] = "0"
os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GRPC_TRACE"] = ""
try:
    urllib.request.getproxies()
except Exception:
    pass
# ====================================================================

# `import autogen` est différé (lazy) dans chaque méthode qui l'utilise.
# L'importer ici lancerait les threads C de gRPC AVANT que Tcl/Tk crée son
# Display Xlib, ce qui provoque un race dans le notifier Tcl → segfault.
import threading
import queue
import types
import ctypes
import tkinter as tk
from tkinter import scrolledtext
from dotenv import load_dotenv

# ── Imports des modules du projet ─────────────────────────────────────────────
from tk_widgets import apply_safe_patches          # FIX C — patches Tk avant tout widget
from llm_config import (build_llm_config, llm_config, _default_model,
                        StopLLMRequested, DND_SKILLS, ABILITY_COLORS)
from app_config import (APP_CONFIG, get_agent_config, get_chronicler_config,
                        get_groupchat_config, get_memories_config, reload_app_config)
from config_panel import open_config_panel
from window_state import (WindowManagerMixin, _load_window_state, _save_window_state,
                          _get_win_geometry, _apply_win_geometry)
from ui_setup_mixin    import UISetupMixin
from chat_mixin        import ChatMixin
from character_mixin   import CharacterMixin
from panels_mixin      import PanelsMixin

# ── Imports des modules métier ─────────────────────────────────────────────────
from state_manager import (
    roll_dice, use_spell_slot, update_hp, load_state, save_state, update_summary,
    get_npcs, save_npcs, AVAILABLE_VOICES,
    get_quests, save_quests, get_active_quests_prompt, QUEST_STATUSES,
    get_scene, save_scene, get_scene_prompt,
    get_location_image_base64,
    get_calendar, save_calendar, advance_day, get_calendar_prompt,
    lunar_phase, BAROVIAN_MONTHS, DAYS_PER_MONTH,
    get_memories_prompt_compact,
    get_contextual_memories_prompt,
)
from voice_interface   import record_audio_and_transcribe, play_voice
from character_faces   import create_character_faces, CharacterFaceWindow, CHARACTER_DATA
from combat_tracker    import CombatTracker, COMBAT_STATE, get_combat_prompt
from combat_simulator  import CombatSimulator

# ─── Variables .env requises selon les fournisseurs utilisés ─────────────────
# GEMINI_API_KEY=...          → https://aistudio.google.com/app/apikey
# GROQ_API_KEY=...            → https://console.groq.com/keys  (gratuit)
# OPENROUTER_API_KEY=...      → https://openrouter.ai/keys     (gratuit)
# DEFAULT_LLM_MODEL=gemini-2.5-flash   ← modèle du résumé et du GroupChatManager
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv()

# Applique les patches Tk AVANT toute création de widget (FIX C)
apply_safe_patches()


class DnDApp(WindowManagerMixin, UISetupMixin, ChatMixin, CharacterMixin, PanelsMixin):
    """Moteur de l'Aube Brisée — Interface du Maître de Jeu."""

    CHAR_COLORS = {"Kaelen": "#e57373", "Elara": "#64b5f6", "Thorne": "#ce93d8", "Lyra": "#81c784"}

    # Modificateurs de compétence / sauvegardes par personnage (niveau 15)
    # Clés normalisées en minuscules sans accents pour la comparaison
    _SKILL_MODIFIERS: dict = {
        "Kaelen": {   # Paladin 15 — STR20 DEX10 CON16 INT10 WIS14 CHA18 — Prof+5
            "skills": {"athlétisme": +10, "perspicacité": +7, "perception": +7,
                       "médecine": +7, "persuasion": +9, "intimidation": +9,
                       "religion": +5, "histoire": +5},
            "saves":  {"force": +10, "dextérité": +5, "constitution": +8,
                       "intelligence": +5, "sagesse": +7, "charisme": +9},
            "default_ability": {"Force": +5, "Dextérité": +0, "Constitution": +3,
                                "Intelligence": +0, "Sagesse": +2, "Charisme": +4},
        },
        "Elara": {    # Magicienne 15 — STR8 DEX14 CON14 INT20 WIS12 CHA10 — Prof+5
            "skills": {"arcanes": +15, "histoire": +15, "investigation": +12,
                       "perception": +6, "perspicacité": +6, "médecine": +6,
                       "nature": +10, "religion": +10},
            "saves":  {"force": +4, "dextérité": +7, "constitution": +7,
                       "intelligence": +10, "sagesse": +6, "charisme": +5},
            "default_ability": {"Force": -1, "Dextérité": +2, "Constitution": +2,
                                "Intelligence": +5, "Sagesse": +1, "Charisme": +0},
        },
        "Thorne": {   # Roublard 15 — STR10 DEX20 CON14 INT14 WIS12 CHA14 — Prof+5
            "skills": {"discrétion": +15, "acrobaties": +10, "escamotage": +15,
                       "perception": +11, "perspicacité": +6, "acrobaties": +10,
                       "investigation": +8, "athlétisme": +6, "intimidation": +7},
            "saves":  {"force": +6, "dextérité": +10, "constitution": +7,
                       "intelligence": +8, "sagesse": +6, "charisme": +7},
            "default_ability": {"Force": +0, "Dextérité": +5, "Constitution": +2,
                                "Intelligence": +2, "Sagesse": +1, "Charisme": +2},
        },
        "Lyra": {     # Clerc Vie 15 — STR14 DEX12 CON14 INT12 WIS20 CHA16 — Prof+5
            "skills": {"médecine": +15, "perspicacité": +10, "religion": +6,
                       "persuasion": +8, "perception": +10, "histoire": +6},
            "saves":  {"force": +7, "dextérité": +6, "constitution": +7,
                       "intelligence": +6, "sagesse": +10, "charisme": +8},
            "default_ability": {"Force": +2, "Dextérité": +1, "Constitution": +2,
                                "Intelligence": +1, "Sagesse": +5, "Charisme": +3},
        },
    }

    def __init__(self, root):
        self.root = root
        self.root.title("⚔️ Moteur de l'Aube Brisée - Interface du MJ")
        self.root.configure(bg="#1e1e1e")

        # ── Restauration géométrie fenêtre principale ─────────────────────────
        self._win_state: dict = _load_window_state()
        _apply_win_geometry(self.root, self._win_state.get("main"), "1100x750")
        # Polling toutes les 2s pour sauvegarder la géométrie principale
        # (pas de <Configure> : se propage depuis tous les widgets enfants)
        self.root.after(2000, self._poll_main_geometry)

        self.msg_queue = queue.Queue()
        self.audio_queue = queue.Queue()
        self.input_event = threading.Event()
        # FIX : lock pour protéger user_input contre les race conditions entre threads
        self._input_lock = threading.Lock()
        self._user_input = ""
        self.groupchat = None # <-- Stockage de la session pour pouvoir la résumer
        # --- MODE PNJ ---
        self.active_npc = None          # dict du PNJ actif ou None (mode MJ normal)
        self._npc_var = None            # StringVar du dropdown (initialisé dans setup_ui)

        # --- STOP LLM ---
        self._autogen_thread_id: int | None = None
        self._llm_running = False
        self._waiting_for_mj = False          # True quand c'est au MJ de parler
        self._pending_interrupt_input: str | None = None
        self._pending_interrupt_display: dict | None = None

        # --- VISAGES & COMBAT ---
        self.face_windows: dict = {}
        self._combat_tracker = None
        self._agents: dict = {}              # {name: AssistantAgent} pour MAJ des prompts
        self._base_system_msgs: dict = {}    # system_message de base sans combat
        # Mémoires activées dynamiquement au fil de la conversation
        self._active_memory_ids: set  = set()   # IDs déjà injectés dans les prompts
        self._contextual_mem_block: str = ""    # bloc cumulatif des mémoires contextuelles

        # FIX D — Tout différé dans mainloop() via after(0).
        # Aucun widget ni thread C lancé depuis __init__.
        self.root.after(0, self._deferred_init)

    def _deferred_init(self):
        """S'exécute dans mainloop() via root.after(0).
        Garantit que setup_ui tourne sous contrôle exclusif de Xlib par Tk.
        """
        self.setup_ui()
        threading.Thread(target=self.audio_worker, daemon=True).start()
        self.root.after(100, self.process_queue)
        self.root.after(1000, self.update_stats_panel)
        self.root.after(3000, self._restore_windows)
        # FIX D — run_autogen (et donc gRPC/autogen) démarre 500 ms après
        # la fin de setup_ui, quand mainloop() a déjà rendu tous les widgets.
        # Démarrer immédiatement créait une race entre les threads C de gRPC
        # et le notifier Tcl/Tk → segfault Xlib.
        self.root.after(500, lambda: threading.Thread(
            target=self.run_autogen, daemon=True, name="autogen-worker"
        ).start())

    # --- Accès thread-safe à user_input ---
    @property
    def user_input(self):
        with self._input_lock:
            return self._user_input

    @user_input.setter
    def user_input(self, value):
        with self._input_lock:
            self._user_input = value

    # ── Persistance état des fenêtres ─────────────────────────────────────────

    def _poll_main_geometry(self):
        """Sauvegarde la géométrie de la fenêtre principale toutes les 2 s."""
        try:
            if not self.root.winfo_exists():
                return
            g = _get_win_geometry(self.root)
            if g:
                self._win_state["main"] = g
                _save_window_state(self._win_state)
            self.root.after(2000, self._poll_main_geometry)
        except Exception:
            pass

    def _track_window(self, key: str, win):
        """Attache le suivi géométrie à une Toplevel. Restaure si déjà sauvegardée.
        Les clés préfixées 'modal_' sauvegardent la géométrie mais ne rouvrent pas
        la fenêtre automatiquement au démarrage (fenêtres modales bloquantes).

        IMPORTANT : on n'utilise PAS <Configure> pour sauvegarder — cet event
        se propage depuis tous les widgets enfants (canvas, frames scrollables…)
        et crée des cascades qui segfaultent les extensions C de Tk.
        À la place on utilise un polling léger toutes les 2 secondes.
        """
        saved = self._win_state.get(key)
        if saved:
            _apply_win_geometry(win, saved, "")
        is_modal = key.startswith("modal_")

        # ── Polling géométrie (toutes les 2 s, seulement si fenêtre vivante) ──
        def _poll():
            try:
                if not win.winfo_exists():
                    return
                g = _get_win_geometry(win)
                if g:
                    self._win_state[key] = g
                    _save_window_state(self._win_state)
                win.after(2000, _poll)
            except Exception:
                pass

        win.after(2000, _poll)

        # ── Nettoyage du flag _open_ à la fermeture manuelle ─────────────────
        def _on_destroy_cleanup(event=None):
            try:
                if self.root.winfo_exists():
                    # Fermeture manuelle : retire le flag
                    if not is_modal:
                        self._win_state.pop(f"_open_{key}", None)
                        _save_window_state(self._win_state)
            except Exception:
                pass

        win.bind("<Destroy>", _on_destroy_cleanup)

        if not is_modal:
            self._win_state[f"_open_{key}"] = True
            _save_window_state(self._win_state)
        return win

    def _restore_windows(self):
        """Rouvre les fenêtres qui étaient ouvertes lors de la dernière session.
        Les délais sont échelonnés pour laisser Tk et gRPC se stabiliser."""
        delay = 0
        if self._win_state.get("_open_combat_tracker"):
            delay += 300
            self.root.after(delay, self.open_combat_tracker)
        if self._win_state.get("_open_quest_journal"):
            delay += 300
            self.root.after(delay, self.open_quest_journal)
        if self._win_state.get("_open_npc_manager"):
            delay += 300
            self.root.after(delay, self.open_npc_manager)
        if self._win_state.get("_open_location_image"):
            delay += 300
            self.root.after(delay, self.open_location_image_popout)
        if self._win_state.get("_open_calendar"):
            delay += 300
            self.root.after(delay, self.open_calendar_popout)
        for name in ["Kaelen", "Elara", "Thorne", "Lyra"]:
            if self._win_state.get(f"_open_char_{name}"):
                delay += 400   # 400 ms entre chaque popout pour éviter les races gRPC/Tk
                self.root.after(delay, lambda n=name: self.open_char_popout(n))


    def _rebuild_agent_prompts(self):
        """Reconstruit le system_message de chaque agent en incluant :
          - le prompt de base (personnalité + règles)
          - la scène active, les quêtes, les mémoires compactes, le calendrier
          - les mémoires contextuelles activées dynamiquement ce tour
          - le bloc de combat (si combat en cours)
        Appelé après chaque message joueur et après toute activation de mémoire contextuelle."""
        combat_block_fn = get_combat_prompt  # importé depuis combat_tracker
        for name, agent in self._agents.items():
            base        = self._base_system_msgs.get(name, "")
            scene_block = get_scene_prompt()
            quest_block = get_active_quests_prompt()
            mem_compact = get_memories_prompt_compact(importance_min=get_memories_config().get("compact_importance_min", 2))
            cal_block   = get_calendar_prompt()
            ctx_block   = self._contextual_mem_block          # mémoires activées dynamiquement
            combat_block= combat_block_fn(name)
            agent.update_system_message(
                base + scene_block + quest_block + mem_compact + cal_block + ctx_block + combat_block
            )

    # Alias conservé pour compatibilité avec les appels existants depuis le combat
    def _update_agent_combat_prompts(self):
        self._rebuild_agent_prompts()

    def _update_contextual_memories(self, text: str):
        """Détecte les mémoires pertinentes dans text et les injecte dans les agents si nouvelles."""
        if not text or not self._agents:
            return
        block, new_ids = get_contextual_memories_prompt(text, self._active_memory_ids)
        if not new_ids:
            return
        self._active_memory_ids |= new_ids
        self._contextual_mem_block += block      # accumulation sur toute la session
        self._rebuild_agent_prompts()
        # Notifier discrètement le MJ dans le chat
        names = []
        try:
            from state_manager import load_state as _ls
            mems = {m["id"]: m["titre"] for m in _ls().get("memories", [])}
            names = [mems[i] for i in new_ids if i in mems]
        except Exception:
            pass
        if names:
            self.msg_queue.put({
                "sender": "📚 Mémoire",
                "text":   f"Activée : {', '.join(names)}",
                "color":  "#7a6a9a",
            })

    # ─── INJECTION MULTIMODALE : IMAGE DU LIEU ────────────────────────────────

    @staticmethod
    def _is_multimodal_agent(agent) -> bool:
        """Retourne True si l'agent utilise un modèle Gemini (supporte la vision)."""
        try:
            configs = agent.llm_config.get("config_list", [])
            if not configs:
                return False
            model = configs[0].get("model", "")
            return model.startswith("gemini-")
        except Exception:
            return False

    def _broadcast_location_image(self, announce: bool = True):
        """
        Envoie l'image du lieu actuel à tous les agents multimodaux (Gemini).
        Chaque agent voit l'image et décrit brièvement ce que son personnage perçoit.
        
        announce=True  → affiche un message système dans le chat avant l'envoi.
        announce=False → injection silencieuse (ex: au démarrage de scène).
        """
        if not self._agents:
            self.msg_queue.put({
                "sender": "⚠️ Système",
                "text": "Agents non initialisés — lancez la partie d'abord.",
                "color": "#FF9800"
            })
            return

        img_data = get_location_image_base64()
        if img_data is None:
            self.msg_queue.put({
                "sender": "⚠️ Système",
                "text": "Aucune image de lieu définie. Ajoutez-en une via ✏️ Scène Active.",
                "color": "#FF9800"
            })
            return

        media_type, b64 = img_data
        scene = get_scene()
        lieu = scene.get("lieu", "ce lieu")

        if announce:
            self.msg_queue.put({
                "sender": "🖼️ Système",
                "text": f"📸 Image du lieu envoyée aux agents multimodaux : {lieu}",
                "color": "#81c784"
            })

        def _send_to_agent(name, agent):
            if not self._is_multimodal_agent(agent):
                return  # Thorne (Groq) ne reçoit pas l'image

            try:
                import autogen as _ag
                client = _ag.OpenAIWrapper(config_list=agent.llm_config["config_list"])

                system_msg = agent.system_message or ""
                prompt_text = (
                    f"[IMAGE DU LIEU — CONTEXTE VISUEL PRIVÉ]\n"
                    f"Le MJ te montre une illustration de l'endroit où se trouve ton groupe : {lieu}.\n"
                    f"En UNE phrase courte de roleplay, décris ce que {name} perçoit ou ressent en voyant ce lieu. "
                    f"Ne pose pas de question. Reste dans le personnage. "
                    f"Si l'image ne correspond pas exactement à la scène décrite, adapte ta perception au contexte narratif."
                )

                response = client.create(messages=[
                    {"role": "system", "content": system_msg},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt_text},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{b64}"
                                }
                            }
                        ]
                    }
                ])

                text = (response.choices[0].message.content or "").strip()
                if text and text != "[SILENCE]":
                    color = self.CHAR_COLORS.get(name, "#e0e0e0")
                    self.msg_queue.put({"sender": name, "text": text, "color": color})
                    self.audio_queue.put((text, name))

            except Exception as e:
                self.msg_queue.put({
                    "sender": f"⚠️ Image ({name})",
                    "text": f"Échec envoi image : {e}",
                    "color": "#F44336"
                })

        import threading as _t
        import concurrent.futures as _cf

        def _run_all():
            with _cf.ThreadPoolExecutor(max_workers=3) as ex:
                futures = [
                    ex.submit(_send_to_agent, name, agent)
                    for name, agent in self._agents.items()
                ]
                for f in _cf.as_completed(futures):
                    try: f.result()
                    except Exception: pass

        _t.Thread(target=_run_all, daemon=True).start()

    # --- TRACKER DE COMBAT ---
    def open_combat_tracker(self):
        """Ouvre (ou ramène au premier plan) la fenêtre de combat D&D 5e."""
        if self._combat_tracker is not None:
            try:
                self._combat_tracker.win.deiconify()
                self._combat_tracker.win.lift()
                return
            except Exception:
                self._combat_tracker = None
        self._combat_tracker = CombatTracker(
            root=self.root,
            state_loader=load_state,
            chat_queue=self.msg_queue,
        )
        try:
            self._track_window("combat_tracker", self._combat_tracker.win)
        except Exception:
            pass

    def trigger_save(self):
        self.msg_queue.put({"sender": "Système", "text": "💾 Sauvegarde en cours... Le Chroniqueur IA rédige le résumé...", "color": "#FF9800"})
        threading.Thread(target=self._generate_and_save_summary, args=(False,), daemon=True).start()

    def trigger_end_session(self):
        self.msg_queue.put({"sender": "Système", "text": "🛑 Fin de session demandée. Génération du résumé final...", "color": "#F44336"})
        threading.Thread(target=self._generate_and_save_summary, args=(True,), daemon=True).start()

    def _generate_and_save_summary(self, end_session):
        import autogen  # lazy : gRPC s'initialise après mainloop()
        if not self.groupchat:
            self.msg_queue.put({"sender": "Système", "text": "❌ Erreur: La session n'a pas encore commencé.", "color": "#F44336"})
            return

        # 1. On récupère le texte pur de la session (sans les appels systèmes)
        chat_history = ""
        for msg in self.groupchat.messages:
            name = msg.get("name", "Inconnu")
            content = msg.get("content", "")
            if content and not str(content).startswith("[RÉSULTAT SYSTÈME]"):
                chat_history += f"{name}: {content}\n"

        if not chat_history.strip():
            self.msg_queue.put({"sender": "Système", "text": "⚠️ Historique vide, rien à sauvegarder.", "color": "#FF9800"})
            if end_session: os._exit(0)
            return

        # 2. On récupère l'ancien résumé
        state = load_state()
        old_summary = state.get("session_summary", "Aucun résumé précédent.")

        try:
            # 3. On demande au LLM de fusionner les deux
            _chron = get_chronicler_config()
            _chron_llm = build_llm_config(
                _chron.get("model", _default_model),
                temperature=_chron.get("temperature", 0.3),
            )
            client = autogen.OpenAIWrapper(config_list=_chron_llm["config_list"])
            scene_context_txt = get_scene_prompt()
            quests_context    = get_active_quests_prompt()
            memories_txt = get_memories_prompt_compact(importance_min=_chron.get("memories_importance", 1))
            system_prompt = _chron.get("system_prompt", (
                "Tu es le Chroniqueur IA d'une campagne D&D. Ton but est de maintenir un résumé global à jour de l'histoire. "
                "Je vais te fournir l'ancien résumé de la campagne, le journal de quêtes actif, les mémoires clés du groupe, "
                "puis la transcription de la nouvelle session. "
                "Rédige un UNIQUE résumé mis à jour qui inclut l'essentiel de l'ancien résumé ET de façon fluide les nouveaux événements. "
                "Note si des objectifs de quête semblent avoir progressé ou été accomplis. "
                "Sois immersif, concis (pas de détails inutiles), et liste les objets majeurs trouvés."
            ))
            user_prompt = (
                f"--- ANCIEN RÉSUMÉ ---\n{old_summary}\n\n"
                f"--- SCÈNE ---\n{scene_context_txt}\n\n"
                f"--- QUÊTES ACTIVES ---\n{quests_context}\n\n"
                f"--- MÉMOIRES CLÉS ---\n{memories_txt}\n\n"
                f"--- NOUVELLE SESSION ---\n{chat_history}"
            )

            response = client.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            new_summary = response.choices[0].message.content
            
            # 4. On écrase l'ancien par le nouveau
            update_summary(new_summary)
            
            self.msg_queue.put({"sender": "Système", "text": f"✅ Résumé sauvegardé !\n\n📜 Aperçu de l'Histoire Globale :\n{new_summary}", "color": "#4CAF50"})
            
            if end_session:
                self.msg_queue.put({"sender": "Système", "text": "🛑 Fermeture de l'application dans 5 secondes...", "color": "#F44336"})
                time.sleep(5)
                os._exit(0) # Fermeture forcée propre
        except Exception as e:
            self.msg_queue.put({"sender": "Système", "text": f"❌ Erreur lors du résumé : {str(e)}", "color": "#F44336"})


    # --- CONTRÔLE LLM ---
    def _inject_stop(self):
        tid = self._autogen_thread_id
        if tid:
            res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(tid), ctypes.py_object(StopLLMRequested))
            if res > 1:
                ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid), None)

    def _set_llm_running(self, running: bool):
        self._llm_running = running
        # root.after() n'est pas thread-safe sur Linux — on passe par msg_queue
        self.msg_queue.put({"action": "set_llm_running", "value": running})

    def _set_waiting_for_mj(self, waiting: bool):
        """Active/désactive l'indicateur 'tour du MJ' et met à jour le bouton Stop."""
        self._waiting_for_mj = waiting
        # root.after() n'est pas thread-safe sur Linux — on passe par msg_queue
        self.msg_queue.put({"action": "set_waiting_for_mj", "value": waiting})

    def stop_llms(self):
        if not self._llm_running or self._waiting_for_mj:
            return
        self.msg_queue.put({"sender": "⏹ Système", "text": "Interruption demandée — LLMs arrêtés. Tapez un message pour reprendre.", "color": "#FF9800"})
        self._inject_stop()

    def send_text(self):
        text = self.entry.get().strip()
        self.entry.delete(0, tk.END)
        if self._llm_running and not self._waiting_for_mj:
            if not text:
                self.stop_llms()
                return
            npc = self.active_npc
            if npc:
                formatted = f"[{npc['name']}] : {text}"
                display = {"sender": f"🎭 {npc['name']}", "text": text, "color": npc.get("color", "#c77dff")}
            else:
                formatted = text
                display = {"sender": "Alexis_Le_MJ", "text": text, "color": "#4CAF50"}
            # Stocke le message à afficher APRÈS l'arrêt — le except StopLLMRequested le postera
            self._pending_interrupt_input = formatted
            self._pending_interrupt_display = display
            self.msg_queue.put({"sender": "⏹ Système", "text": "LLMs interrompus — reprise avec votre nouveau message.", "color": "#FF9800"})
            self._inject_stop()   # pas de with_input ici — géré par _pending_interrupt_display
            return
        if self.input_event.is_set():
            return
        # ── Détection commande /vote choix1 choix2 ... ───────────────────────
        import re as _re_msg
        _pv = _re_msg.match(r'^/vote\s+(.+)$', text, _re_msg.IGNORECASE)
        if _pv:
            raw_choices = _pv.group(1).strip()
            choices = [c.strip() for c in _re_msg.split(r'\s+', raw_choices) if c.strip()]
            if len(choices) < 2:
                self.msg_queue.put({"sender": "⚠️ Système",
                                    "text": "Usage : /vote choix_1 choix_2 [choix_3 ...]",
                                    "color": "#FF9800"})
                return
            if not self._agents:
                self.msg_queue.put({"sender": "⚠️ Système",
                                    "text": "Agents non initialisés — lancez la partie d'abord.",
                                    "color": "#FF9800"})
                return
            threading.Thread(target=self._run_vote, args=(choices,), daemon=True).start()
            return

        # ── Détection commande /msg NomPersonnage texte... ────────────────────
        _pm = _re_msg.match(r'^/msg\s+(\S+)\s+(.+)$', text, _re_msg.IGNORECASE)
        if _pm:
            target_raw = _pm.group(1)
            private_text = _pm.group(2).strip()
            if not self._agents:
                self.msg_queue.put({"sender": "⚠️ Système", "text": "Agents non initialisés — lancez la partie d'abord.", "color": "#FF9800"})
                return
            real_name = next((n for n in self._agents if n.lower().startswith(target_raw.lower())), None)
            if real_name is None:
                self.msg_queue.put({
                    "sender": "⚠️ Système",
                    "text": f"Personnage '{target_raw}' introuvable. Valides : {', '.join(self._agents.keys())}",
                    "color": "#FF9800"
                })
                return
            threading.Thread(target=self._send_private_message, args=(real_name, private_text), daemon=True).start()
            return
        self.user_input = text
        if text:
            npc = self.active_npc
            if npc:
                display_name = f"🎭 {npc['name']}"
                color = npc.get("color", "#c77dff")
                self.msg_queue.put({"sender": display_name, "text": text, "color": color})
                self.user_input = f"[{npc['name']}] : {text}"
            else:
                self.msg_queue.put({"sender": "Alexis_Le_MJ", "text": text, "color": "#4CAF50"})
        else:
            self.msg_queue.put({"sender": "Système", "text": "✅ [Approbation de l'action en cours...]", "color": "#aaaaaa"})
        self.input_event.set()

    # ─────────────────────────────────────────────────────────────────────────
    # --- JETS DE COMPÉTENCE ---
    # ─────────────────────────────────────────────────────────────────────────

    def _send_private_message(self, char_name: str, message: str):
        import autogen  # lazy
        """Envoie un message secret directement à un agent (bypass groupchat). Affiché en chat côté MJ."""
        agent = self._agents.get(char_name)
        if agent is None:
            self.msg_queue.put({"sender": "Système", "text": f"❌ Agent {char_name} introuvable.", "color": "#F44336"})
            return

        # ── Affichage côté MJ (message envoyé + indicateur secret) ──────────
        CHAR_COLORS = {"Kaelen": "#e57373", "Elara": "#64b5f6", "Thorne": "#ce93d8", "Lyra": "#81c784"}
        char_color = CHAR_COLORS.get(char_name, "#aaaaaa")
        self.msg_queue.put({
            "sender": f"🔒 MJ → {char_name}",
            "text": message,
            "color": "#888844"
        })

        # ── Prompt : l'agent choisit de répondre au groupe ou en secret ─────
        system_msg = agent.system_message or ""
        prompt = (
            f"[MESSAGE PRIVÉ DU MJ — POUR {char_name.upper()} UNIQUEMENT — LES AUTRES JOUEURS NE VOIENT PAS CECI]\n"
            f"{message}\n\n"
            f"Tu dois choisir comment répondre. Deux options EXCLUSIVES :\n\n"
            f"Option A — Répondre DIRECTEMENT AU GROUPE (les autres joueurs entendent) :\n"
            f"  Commence ta réponse par : [GROUPE]\n"
            f"  Utilise ça si tu veux que ton personnage parle ouvertement, réagit à voix haute, ou partage l'info.\n\n"
            f"Option B — Répondre SECRÈTEMENT au MJ seulement :\n"
            f"  Commence ta réponse par : [SECRET]\n"
            f"  Utilise ça si ton personnage garde l'info pour lui, réfléchit intérieurement, ou veut d'abord en parler en privé.\n\n"
            f"Reste dans le personnage de {char_name}. Réponse courte, en roleplay pur. "
            f"Ne mentionne jamais les balises [GROUPE] ou [SECRET] dans le corps de ta réponse."
        )

        # FIX SEGFAULT : agent.generate_reply() appelle gRPC depuis un thread
        # daemon séparé → crash natif. On utilise OpenAIWrapper directement.
        try:
            client = autogen.OpenAIWrapper(config_list=agent.llm_config["config_list"])
            response = client.create(messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": prompt},
            ])
            text_content = response.choices[0].message.content or ""
            text_content = text_content.strip()
        except Exception as e:
            self.msg_queue.put({"sender": "❌ Erreur", "text": f"Échec msg privé pour {char_name} : {e}", "color": "#F44336"})
            return

        if not text_content or text_content == "[SILENCE]":
            return

        # ── Analyse du choix de l'agent ──────────────────────────────────────
        if text_content.startswith("[GROUPE]"):
            # L'agent veut parler au groupe directement — on injecte sans demander
            clean_text = text_content[len("[GROUPE]"):].strip()
            self.msg_queue.put({"sender": char_name, "text": clean_text, "color": char_color})
            self.audio_queue.put((clean_text, char_name))
            # Injecter dans le groupchat comme si c'était un vrai message du joueur
            relayed = f"[{char_name}, s'adressant au groupe] {clean_text}"
            if self._llm_running and not self._waiting_for_mj:
                self._pending_interrupt_input = relayed
                self._pending_interrupt_display = None
                self._inject_stop()
            else:
                self.user_input = relayed
                self.input_event.set()

        else:
            # L'agent répond en secret ([SECRET] ou pas de balise reconnue)
            clean_text = text_content[len("[SECRET]"):].strip() if text_content.startswith("[SECRET]") else text_content
            self.msg_queue.put({"sender": f"🔒 {char_name} (privé)", "text": clean_text, "color": char_color})
            self.audio_queue.put((clean_text, char_name))
            # Garder le bouton relay : le MJ peut décider de partager au groupe
            self.msg_queue.put({"action": "relay_button", "char_name": char_name, "reply_text": clean_text})

    def _run_vote(self, choices: list[str]):
        import autogen  # lazy
        """
        Lance un vote simultané sur tous les agents joueurs.
        Chaque agent choisit parmi les options et justifie brièvement en roleplay.
        Les résultats s'affichent dans le chat avec un récapitulatif.
        """
        import re as _re_v

        PLAYER_NAMES = ["Kaelen", "Elara", "Thorne", "Lyra"]
        CHAR_COLORS  = {"Kaelen": "#e57373", "Elara": "#64b5f6",
                        "Thorne": "#ce93d8", "Lyra":  "#81c784"}

        choices_str  = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(choices))
        choices_list = ", ".join(f'"{c}"' for c in choices)

        self.msg_queue.put({
            "sender": "🗳️ Vote",
            "text":   f"Le MJ demande une décision au groupe :\n{choices_str}",
            "color":  "#ffcc00"
        })

        # Interroge chaque agent en parallèle
        import concurrent.futures as _cf

        def _ask_agent(name):
            agent = self._agents.get(name)
            if not agent:
                return name, None, ""
            system_msg = agent.system_message or ""
            prompt = (
                f"[DÉCISION DU GROUPE — VOTE DU MJ]\n"
                f"Le groupe doit choisir immédiatement sa prochaine action parmi ces options :\n"
                f"{choices_str}\n\n"
                f"Tu dois :\n"
                f"1. Choisir UNE option parmi : {choices_list}\n"
                f"2. Répondre UNIQUEMENT avec ce format exact sur deux lignes :\n"
                f"   VOTE: <option choisie exactement comme écrite>\n"
                f"   RAISON: <une phrase courte en roleplay expliquant ton choix>\n\n"
                f"Ne dévie pas du format. Choisis selon la personnalité de {name}."
            )
            try:
                client = autogen.OpenAIWrapper(config_list=agent.llm_config["config_list"])
                response = client.create(messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user",   "content": prompt},
                ])
                raw = (response.choices[0].message.content or "").strip()
                # Parse VOTE: et RAISON:
                vote_m   = _re_v.search(r'VOTE\s*:\s*(.+)', raw, _re_v.IGNORECASE)
                raison_m = _re_v.search(r'RAISON\s*:\s*(.+)', raw, _re_v.IGNORECASE)
                vote_txt   = vote_m.group(1).strip()   if vote_m   else raw.splitlines()[0]
                raison_txt = raison_m.group(1).strip() if raison_m else ""
                # Normalise le vote vers le choix le plus proche
                best = min(choices, key=lambda c: (
                    0 if c.lower() == vote_txt.lower()
                    else (1 if c.lower() in vote_txt.lower() or vote_txt.lower() in c.lower()
                          else 2)
                ))
                return name, best, raison_txt
            except Exception as e:
                return name, None, f"(erreur: {e})"

        with _cf.ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(_ask_agent, n): n for n in PLAYER_NAMES}
            results = {}   # name -> (choice, raison)
            for f in _cf.as_completed(futures):
                name, choice, raison = f.result()
                results[name] = (choice, raison)
                if choice:
                    color = CHAR_COLORS.get(name, "#aaaaaa")
                    self.msg_queue.put({
                        "sender": f"🗳️ {name}",
                        "text":   f"→ **{choice}**" + (f"  —  {raison}" if raison else ""),
                        "color":  color
                    })
                    self.audio_queue.put((raison or choice, name))

        # Décompte
        tally: dict[str, list[str]] = {c: [] for c in choices}
        for name, (choice, _) in results.items():
            if choice and choice in tally:
                tally[choice].append(name)

        # Résumé visuel
        max_votes  = max((len(v) for v in tally.values()), default=0)
        winners    = [c for c, v in tally.items() if len(v) == max_votes and v]
        tally_lines = []
        for c in choices:
            voters = tally[c]
            bar    = "█" * len(voters) + "░" * (len(PLAYER_NAMES) - len(voters))
            marker = " ◀ MAJORITÉ" if c in winners else ""
            tally_lines.append(f"  {bar} {c} ({len(voters)}/{len(PLAYER_NAMES)}){marker}")

        summary = "─── Résultats ───\n" + "\n".join(tally_lines)
        if len(winners) == 1:
            summary += f"\n\n✅ Décision : {winners[0]}"
        else:
            summary += f"\n\n⚖️ Égalité entre : {' / '.join(winners)} — au MJ de trancher."

        self.msg_queue.put({"sender": "🗳️ Vote terminé", "text": summary, "color": "#ffcc00"})

        # Injecte la décision dans le groupchat pour que les agents en soient informés
        if len(winners) == 1:
            inject = f"[RÉSULTAT DU VOTE] Le groupe a décidé : {winners[0]}."
        else:
            inject = f"[RÉSULTAT DU VOTE] Égalité entre {' et '.join(winners)} — le MJ tranchera."
        self.user_input = inject
        self.input_event.set()

    def _execute_skill_check(self, char_name: str, skill: str, ability: str, dc: int | None, reason: str | None = None):
        """Appelle directement l'agent concerné pour un jet de compétence (bypass groupchat).

        FIX : Le system prompt des agents interdit d'appeler roll_dice soi-même (règle 5).
        On sépare donc la narration (agent) et le lancer de dés (Python direct).
        """
        agent = self._agents.get(char_name)
        if agent is None:
            self.msg_queue.put({"sender": "Système", "text": f"❌ Agent {char_name} introuvable.", "color": "#F44336"})
            return

        # ── Récupération du bonus de compétence ──────────────────────────────
        char_mods = self._SKILL_MODIFIERS.get(char_name, {})
        skill_low = skill.lower()
        bonus = (
            char_mods.get("skills", {}).get(skill_low)
            or char_mods.get("saves", {}).get(skill_low)
            or char_mods.get("default_ability", {}).get(ability)
            or 0
        )

        # ── Annonce publique dans le chat ────────────────────────────────────
        dc_txt    = f"  |  DC {dc}" if dc is not None else "  |  DC secret"
        reason_txt = f"  |  {reason}" if reason else ""
        bonus_txt  = f"  |  Bonus {bonus:+d}" if bonus else ""
        announce = f"🎲 Jet de compétence → [{char_name}] : {skill} ({ability}){dc_txt}{bonus_txt}{reason_txt}"
        self.msg_queue.put({"sender": "🎲 MJ", "text": announce, "color": "#ffcc00"})

        # ── Lancer de dés IMMÉDIAT (Python — ne dépend pas de l'agent) ───────
        dice_result = roll_dice(
            character_name=char_name,
            dice_type="1d20",
            bonus=bonus,
        )
        self.msg_queue.put({"sender": f"🎲 Résultat ({char_name})", "text": dice_result, "color": "#4CAF50"})

        # ── Évaluation DC ─────────────────────────────────────────────────────
        if dc is not None:
            try:
                import re as _re
                m = _re.search(r"Total\s*=\s*(\d+)", dice_result)
                if m:
                    total = int(m.group(1))
                    outcome = "✅ Réussite" if total >= dc else "❌ Échec"
                    self.msg_queue.put({
                        "sender": "🎲 MJ (DC secret)",
                        "text": f"{outcome} — Total {total} vs DC {dc}",
                        "color": "#4CAF50" if total >= dc else "#e57373"
                    })
            except Exception:
                pass

        # ── Prompt narration UNIQUEMENT (pas de demande d'appel d'outil) ─────
        reason_instruction = f"\nContexte : {reason}" if reason else ""
        prompt = (
            f"[INSTRUCTION NARRATIVE]\n"
            f"Le système a exécuté la mécanique du sort. "
            f"Narre en 1-2 phrases comment {char_name} incante et l'effet visible sur {reason or 'la cible'}."
            f"{reason_instruction}\n"
            f"Ne mentionne pas les chiffres bruts."
        )

        try:
            reply = agent.generate_reply(
                messages=[{"role": "user", "content": prompt}]
            )
        except Exception as e:
            self.msg_queue.put({"sender": "❌ Erreur", "text": f"Narration impossible pour {char_name} : {e}", "color": "#F44336"})
            return

        # ── Affichage de la narration ─────────────────────────────────────────
        text_content = ""
        if isinstance(reply, str):
            text_content = reply.strip()
        elif isinstance(reply, dict):
            raw = reply.get("content") or ""
            text_content = raw.strip() if isinstance(raw, str) else ""

        if text_content:
            self.msg_queue.put({"sender": char_name, "text": text_content, "color": "#e0e0e0"})
            self.audio_queue.put((text_content, char_name))

    # --- MOTEUR AUTOGEN ---
    def run_autogen(self):
        import autogen  # lazy — gRPC démarre ici, bien après Tk.mainloop()
        # === NOUVEAU : Chargement et affichage du résumé au démarrage ===
        # Charge les voix PNJ dans le mapping TTS au démarrage
        try:
            from voice_interface import VOICE_MAPPING, SPEED_MAPPING
            for npc in get_npcs():
                key = f"__npc__{npc['name']}"
                VOICE_MAPPING[key] = npc.get("voice", "fr-FR-HenriNeural")
                SPEED_MAPPING[key] = npc.get("speed", "+0%")
        except Exception as e:
            print(f"[NPC] Erreur chargement voix PNJ : {e}")

        try:
            state = load_state()
            summary = state.get("session_summary", "Aucun résumé pour le moment.")
            
            # On ne l'affiche que s'il y a un vrai résumé
            if summary and summary != "Aucun résumé pour le moment.":
                self.msg_queue.put({
                    "sender": "Chroniqueur IA", 
                    "text": f"📜 Précédemment dans votre campagne :\n{summary}", 
                    "color": "#FF9800"
                })
        except Exception as e:
            print(f"Erreur lors du chargement du résumé : {e}")
        # ================================================================

        self.msg_queue.put({"sender": "Système", "text": "⚔️ MOTEUR INITIALISÉ. Connexion aux LLMs en cours...", "color": "#ffcc00"})

        # ── Chargement des configs LLM par personnage ─────────────────────────
        _char_state = load_state().get("characters", {})
        def _cfg(char_name: str) -> dict:
            # Priorité : app_config > campaign_state > défaut env
            ac = get_agent_config(char_name)
            model = ac.get("model") or _char_state.get(char_name, {}).get("llm", _default_model)
            temp  = ac.get("temperature", 0.7)
            return build_llm_config(model, temperature=temp)

        def _provider_label(char_name: str) -> str:
            ac = get_agent_config(char_name)
            model = ac.get("model") or _char_state.get(char_name, {}).get("llm", _default_model)
            if model.startswith("groq/"):        return f"Groq ({model[5:]})"
            if model.startswith("openrouter/"): return f"OpenRouter ({model[11:]})"
            return f"Gemini ({model})"

        providers_info = " | ".join(
            f"{n}: {_provider_label(n)}" for n in ["Kaelen","Elara","Thorne","Lyra"]
        )
        self.msg_queue.put({"sender": "Système", "text": f"🧠 Modèles chargés : {providers_info}", "color": "#aaaaff"})

        mj_agent = autogen.UserProxyAgent(
            name="Alexis_Le_MJ",
            system_message="Tu es Alexis, le Maître du Jeu suprême. Tu as l'autorité absolue sur le monde et les règles de D&D 5e.",
            human_input_mode="ALWAYS", 
            code_execution_config=False,
        )

        def gui_get_human_input(self_agent, prompt: str, **kwargs) -> str:
            self.msg_queue.put({"sender": "Système", "text": "En attente de votre action (Texte ou 🎤)...", "color": "#888888"})
            self._set_waiting_for_mj(True)
            result = self.wait_for_input()
            self._set_waiting_for_mj(False)
            return result
        
        mj_agent.get_human_input = types.MethodType(gui_get_human_input, mj_agent)

        # --- RÈGLES ANTI-HALLUCINATION (communes à tous les joueurs) ---
        _regle_outils = (
            "\n\n═══════════════════════════════════════════"
            "\nRÈGLES ABSOLUES — LIRE ET APPLIQUER À CHAQUE MESSAGE"
            "\n═══════════════════════════════════════════"
            "\n\n▶ ACTIONS MÉCANIQUES : DÉCLARE, N'EXÉCUTE PAS"
            "\nQuand ton personnage VEUT faire quelque chose de mécanique, termine ton message "
            "par un bloc [ACTION] dans ce format exact :\n\n"
            "  [ACTION]\n"
            "  Intention : <ce que ton personnage veut faire, en une phrase claire>\n"
            "  Règle 5e  : <mécanique D&D 5e 2014 : sort + niveau, compétence + caracteristique, "
            "attaque corps-à-corps/distance, jet de sauvegarde, etc.>\n"
            "  Cible     : <sur qui ou quoi>\n\n"
            "  Exemples :\n"
            "    [ACTION]\n"
            "    Intention : Kaelen charge et frappe le vampire de son épée sacrée\n"
            "    Règle 5e  : Attaque corps-à-corps, bonus +11, dégâts 2d6+8 radiants\n"
            "    Cible     : Vampire au centre de la salle\n\n"
            "    [ACTION]\n"
            "    Intention : Elara identifie les runes sur la porte\n"
            "    Règle 5e  : Jet de compétence Arcanes (Intelligence)\n"
            "    Cible     : Runes gravées sur la porte en fer\n\n"
            "    [ACTION]\n"
            "    Intention : Lyra soigne Thorne grièvement blessé\n"
            "    Règle 5e  : Sort Soins (Cure Wounds) niveau 3, 3d8+5 PV\n"
            "    Cible     : Thorne\n\n"
            "▶ CONTRAT SYSTÈME :"
            "\n  1. Le MJ valide ou refuse ton [ACTION]."
            "\n  2. Si validé → le SYSTÈME exécute les dés et la mécanique automatiquement."
            "\n  3. Tu reçois un message [RÉSULTAT SYSTÈME] avec les valeurs exactes."
            "\n  4. Ton seul rôle : narrer l'effet en 1-2 phrases de roleplay fidèles au résultat."
            "\n  5. NE JAMAIS appeler roll_dice, use_spell_slot, update_hp toi-même."
            "\n  6. NE JAMAIS inventer un résultat différent de celui donné par le système.\n"
            "\n▶ PNJ"
            "\nTu n'inventes JAMAIS les paroles d'un PNJ. Si tu t'adresses à un PNJ, ARRÊTE "
            "immédiatement après. Le MJ est la seule voix des PNJ."
            "\n\n▶ MONDE & UNICITÉ"
            "\nN'invente aucun élément qu'Alexis n'a pas établi. Ne répète jamais une question "
            "ou idée déjà exprimée — apporte un angle nouveau ou reste silencieux."
            "\n\n▶ ÉLOCUTION (SYNTHÈSE VOCALE)"
            "\nRépliques : 1-2 phrases MAX, courtes et percutantes. Ponctuation forte (?, !). "
            "Zéro tirade. Parle comme en pleine action."
            "\n═══════════════════════════════════════════\n"
        )

        kaelen_agent = autogen.AssistantAgent(
            name="Kaelen",
            system_message=(
                "Tu es Kaelen, un Paladin Humain de niveau 15, hanté par un serment passé.\n"
                "PERSONNALITÉ : Tu es économe en mots, fier et grave. Tes préoccupations sont toujours liées "
                "à l'honneur, aux serments, à qui mérite protection et à ce qui constitue une cause juste. "
                "Quand tu interviens, c'est pour évaluer la valeur morale de la mission ou jurer ta protection. "
                "Tu n'es pas curieux des mécaniques — tu veux savoir SI ça vaut le coup de mourir pour ça.\n"
                "RÈGLES : 1. Alexis est MJ. C'est le MJ qui invente l'histoire. "
                "2. Si tu veux attaquer, décris ton attaque et attends que le MJ te demande de lancer les dés avec 'roll_dice'. "
                "3. Si tu veux utiliser un sort, décris-le et attends que le MJ te demande d'utiliser 'use_spell_slot'. "
                "4. Ne décide pas si tu touches ou tues. N'invente pas d'environnement. "
                "5. Tu ne connais pas la vallée de Barovie, tout est nouveau ici pour toi."
                + get_scene_prompt()
                + get_active_quests_prompt()
                + get_memories_prompt_compact(importance_min=get_memories_config().get("compact_importance_min", 2))
                + get_calendar_prompt()
                + _regle_outils
            ),
            llm_config=_cfg("Kaelen"),
        )

        elara_agent = autogen.AssistantAgent(
            name="Elara",
            system_message=(
                "Tu es Elara, une Magicienne de niveau 15, froide et méthodique.\n"
                "PERSONNALITÉ : Tu analyses, tu quantifies, tu cherches les failles logiques. Tes questions portent "
                "toujours sur la mécanique précise des choses : comment fonctionne la magie du phare, quelle est "
                "la source du pouvoir, y a-t-il des données concrètes, des artefacts, des textes. "
                "Tu t'ennuies des généralités et tu coupes court aux discours flous. "
                "Tu ne poses JAMAIS une question qu'Elara a déjà posée, ni une que quelqu'un d'autre vient de poser.\n"
                "RÈGLES : 1. Alexis est MJ. C'est le MJ qui invente l'histoire. "
                "2. Si tu veux lancer un sort, décris-le et attends que le MJ te demande d'utiliser 'use_spell_slot'. "
                "3. Ne décide pas du résultat. N'invente pas d'environnement. "
                "4. Tu ne connais pas la vallée de Barovie, tout est nouveau ici pour toi."
                + get_scene_prompt()
                + get_active_quests_prompt()
                + get_memories_prompt_compact(importance_min=get_memories_config().get("compact_importance_min", 2))
                + get_calendar_prompt()
                + _regle_outils
            ),
            llm_config=_cfg("Elara"),
        )

        thorne_agent = autogen.AssistantAgent(
            name="Thorne",
            system_message=(
                "Tu es Thorne, un Voleur (Assassin) Tieffelin de niveau 15, cynique et pragmatique.\n"
                "PERSONNALITÉ : Tu vois le monde en termes de risques, de profits et de qui manipule qui. "
                "Tes questions portent sur les motivations cachées, les pièges potentiels, ce qu'on ne te dit pas, "
                "et ce que rapporte concrètement la mission. Tu es sarcastique et tu n'accordes ta confiance à personne. "
                "Tu ne poses JAMAIS une question qu'un autre personnage vient de poser — tu trouves ça embarrassant.\n"
                "RÈGLES : 1. Alexis est ton MJ. C'est le MJ qui invente l'histoire. "
                "2. Si tu veux attaquer, décris ton action et attends que le MJ te demande de lancer les dés avec 'roll_dice'. "
                "3. Ne décide jamais si tu réussis. N'invente pas d'environnement. "
                "4. Tu connais la légende de la vallée de Barovie, les grands mots, mais tu n'y crois pas."
                + get_scene_prompt()
                + get_active_quests_prompt()
                + get_memories_prompt_compact(importance_min=get_memories_config().get("compact_importance_min", 2))
                + get_calendar_prompt()
                + _regle_outils
            ),
            llm_config=_cfg("Thorne"),
        )

        lyra_agent = autogen.AssistantAgent(
            name="Lyra",
            system_message=(
                "Tu es Lyra, une Clerc (Domaine de la Vie) Demi-Elfe de niveau 15, bienveillante et implacable.\n"
                "PERSONNALITÉ : Tu penses d'abord aux innocents qui souffrent, à la dimension spirituelle et divine "
                "des événements, et à ce que les dieux pourraient vouloir ici. Tu poses des questions sur les victimes, "
                "la souffrance des gens ordinaires, les signes divins, et ce que signifie moralement la situation. "
                "Tu ne poses JAMAIS une question qu'un autre personnage vient de poser — chaque voix doit être unique.\n"
                "RÈGLES : 1. Alexis est ton MJ. C'est le MJ qui invente l'histoire. "
                "2. Si tu veux lancer un sort ou soigner, décris ton intention et attends que le MJ te demande d'utiliser 'use_spell_slot' ou 'update_hp'. "
                "3. Ne décide pas du résultat. N'invente pas d'environnement. "
                "4. Tu ne connais pas la vallée de Barovie, tout est nouveau ici pour toi."
                + get_scene_prompt()
                + get_active_quests_prompt()
                + get_memories_prompt_compact(importance_min=get_memories_config().get("compact_importance_min", 2))
                + get_calendar_prompt()
                + _regle_outils
            ),
            llm_config=_cfg("Lyra"),
        )

        # --- STOCKAGE DES AGENTS pour MAJ dynamique des prompts combat ---
        self._agents = {
            "Kaelen": kaelen_agent,
            "Elara":  elara_agent,
            "Thorne": thorne_agent,
            "Lyra":   lyra_agent,
        }
        self._base_system_msgs = {
            name: agent.system_message
            for name, agent in self._agents.items()
        }

        # --- ENREGISTREMENT DES OUTILS PAR RÔLE ---
        # Kaelen et Thorne : combat (dés + sorts uniquement, pas de soins)
        for agent in [kaelen_agent, thorne_agent]:
            autogen.agentchat.register_function(
                roll_dice, caller=agent, executor=mj_agent,
                name="roll_dice",
                description="Lancer des dés (ex: 1d20, 8d6). À appeler UNIQUEMENT si le MJ te le demande explicitement."
            )
            autogen.agentchat.register_function(
                use_spell_slot, caller=agent, executor=mj_agent,
                name="use_spell_slot",
                description="Consommer un slot de sort (1-9). À appeler UNIQUEMENT si le MJ te le demande explicitement."
            )

        # Elara : sorts uniquement (pas de dés bruts, pas de soins)
        autogen.agentchat.register_function(
            use_spell_slot, caller=elara_agent, executor=mj_agent,
            name="use_spell_slot",
            description="Consommer un slot de sort (1-9). À appeler UNIQUEMENT si le MJ te le demande explicitement."
        )

        # Lyra : sorts + soins (pas de dés d'attaque bruts)
        autogen.agentchat.register_function(
            use_spell_slot, caller=lyra_agent, executor=mj_agent,
            name="use_spell_slot",
            description="Consommer un slot de sort (1-9). À appeler UNIQUEMENT si le MJ te le demande explicitement."
        )
        autogen.agentchat.register_function(
            update_hp, caller=lyra_agent, executor=mj_agent,
            name="update_hp",
            description="Modifier les PV d'un personnage (- pour dégâts, + pour soin). À appeler UNIQUEMENT si le MJ valide le soin."
        )

        # --- SÉLECTEUR DE SPEAKER COMBAT-AWARE ---
        PLAYER_NAMES = ["Kaelen", "Elara", "Thorne", "Lyra"]
        _app_ref = self   # référence pour les closures

        def combat_speaker_selector(last_speaker, groupchat):
            """
            Hors combat : sélection auto normale.
            En combat : les agents hors-tour qui ont déjà réagi sont exclus.
            """
            if not COMBAT_STATE["active"]:
                # Délègue à la logique "auto" d'AutoGen en restituant tous les agents
                return "auto"

            silenced = COMBAT_STATE["spoken_off_turn"]
            eligible = [
                a for a in groupchat.agents
                if a.name not in silenced or a.name not in PLAYER_NAMES
            ]
            # Garde au minimum le MJ + l'agent actif
            if not eligible:
                eligible = [a for a in groupchat.agents
                            if a.name == "Alexis_Le_MJ"]
            # Retire temporairement les agents silenciés de groupchat.agents
            # pour forcer "auto" à les ignorer
            original_agents = groupchat.agents
            groupchat.agents = eligible
            result = "auto"   # signale à AutoGen d'utiliser sa sélection LLM parmi eligible
            groupchat.agents = original_agents
            return result

        # Sauvegarde de l'objet groupchat sur l'instance (self) pour pouvoir faire le résumé plus tard
        _gc_cfg   = get_groupchat_config()
        _chron_cfg = get_chronicler_config()
        _manager_llm = build_llm_config(
            _chron_cfg.get("model", _default_model),
            temperature=_chron_cfg.get("temperature", 0.3),
        )
        self.groupchat = autogen.GroupChat(
            agents=[mj_agent, kaelen_agent, elara_agent, thorne_agent, lyra_agent],
            messages=[],
            max_round=_gc_cfg.get("max_round", 100),
            speaker_selection_method=combat_speaker_selector,
            allow_repeat_speaker=_gc_cfg.get("allow_repeat_speaker", False),
        )
        manager = autogen.GroupChatManager(groupchat=self.groupchat, llm_config=_manager_llm)

        # FIX SEGFAULT : on capture la méthode originale au niveau CLASSE (unbound),
        # pas au niveau instance. Puis on remplace __class__ par une sous-classe anonyme.
        # Raison : types.MethodType() sur une instance crée un objet Python temporaire
        # que les threads C natifs de gRPC peuvent accéder sans tenir le GIL → SEGFAULT.
        # Remplacer __class__ fait résoudre receive() via la MRO de façon atomique.
        # Noms des PNJ connus — à compléter au fil de la campagne
        PNJ_NAMES = ["Ismark", "Strahd", "Ireena", "Madam Eva", "Rahadin", "Viktor", "Morgantha"]
        PLAYER_NAMES = ["Kaelen", "Elara", "Thorne", "Lyra"]
        SPELL_CASTERS = ["Kaelen", "Elara", "Lyra"]  # Thorne n'a pas de sorts
        import re as _re
        _pnj_pattern = _re.compile(
            r'(?:^|\n)\s*(?:' + '|'.join(_re.escape(n) for n in PNJ_NAMES) + r')\s*:',
            _re.IGNORECASE | _re.MULTILINE
        )
        # Détecte [SORT: NomDuSort | Niveau: X | Cible: Y]
        _sort_pattern = _re.compile(
            r'\[SORT\s*:\s*(?P<nom>[^|\]]+?)\s*\|\s*Niveau\s*:\s*(?P<niveau>\d)\s*(?:\|\s*Cible\s*:\s*(?P<cible>[^\]]*?))?\s*\]',
            _re.IGNORECASE
        )
        # Détecte le bloc [ACTION] ... (multiligne)
        _action_pattern = _re.compile(
            r'\[ACTION\]\s*\n'
            r'\s*Intention\s*:\s*(?P<intention>[^\n]+)\n'
            r'\s*Règle 5e\s*:\s*(?P<regle>[^\n]+)\n'
            r'\s*Cible\s*:\s*(?P<cible>[^\n]+)',
            _re.IGNORECASE
        )
        # Event pour bloquer l'agent pendant la confirmation du MJ (sort)
        import threading as _threading
        _spell_confirm_event = _threading.Event()
        _spell_confirm_result = {}   # {"confirmed": bool, "level": int}
        # Event pour bloquer l'agent pendant la confirmation du MJ (action générique)
        _action_confirm_event = _threading.Event()
        _action_confirm_result = {}  # {"confirmed": bool, "mj_note": str}

        _original_receive = manager.__class__.receive
        _app = self  # référence explicite pour les closures

        # ── Stats mécaniques D&D 5e 2014, niveau 15 ──────────────────────────
        _CHAR_MECHANICS = {
            "Kaelen": {  # Paladin 15 — STR20 DEX14 CON16 INT10 WIS14 CHA18 — Prof+5
                "atk_melee": +11, "atk_ranged": +7, "atk_spell": +9,
                "dmg_melee": (2, 6, +8), "n_attacks": 3, "save_dc": 18,
                "skills": {"athlétisme":+10,"religion":+5,"persuasion":+9,
                           "perspicacité":+7,"intimidation":+9,"perception":+7},
                "saves":  {"force":+10,"dextérité":+7,"constitution":+8,
                           "intelligence":+5,"sagesse":+7,"charisme":+9},
            },
            "Elara": {   # Mage 15 — STR8 DEX16 CON14 INT20 WIS14 CHA10 — Prof+5
                "atk_melee": +3, "atk_ranged": +8, "atk_spell": +10,
                "dmg_melee": (1, 4, -1), "n_attacks": 1, "save_dc": 18,
                "skills": {"arcanes":+15,"histoire":+10,"investigation":+10,
                           "nature":+10,"religion":+10,"perception":+7,"perspicacité":+7},
                "saves":  {"force":-1,"dextérité":+8,"constitution":+7,
                           "intelligence":+10,"sagesse":+7,"charisme":+5},
            },
            "Thorne": {  # Voleur Assassin 15 — STR12 DEX20 CON14 INT16 WIS12 CHA14 — Prof+5
                "atk_melee": +11, "atk_ranged": +11, "atk_spell": None,
                "dmg_melee": (1, 6, +5), "dmg_sneak": (8, 6, 0),
                "n_attacks": 2, "save_dc": None,
                "skills": {"discrétion":+15,"escamotage":+15,"tromperie":+12,
                           "perception":+11,"perspicacité":+6,"acrobaties":+10,
                           "investigation":+8,"athlétisme":+6,"intimidation":+7},
                "saves":  {"force":+6,"dextérité":+10,"constitution":+7,
                           "intelligence":+8,"sagesse":+6,"charisme":+7},
            },
            "Lyra": {    # Clerc Vie 15 — STR14 DEX12 CON14 INT12 WIS20 CHA16 — Prof+5
                "atk_melee": +7, "atk_ranged": +6, "atk_spell": +10,
                "dmg_melee": (1, 8, +2), "n_attacks": 2, "save_dc": 18,
                "skills": {"médecine":+15,"perspicacité":+10,"religion":+6,
                           "persuasion":+8,"perception":+10,"histoire":+6},
                "saves":  {"force":+7,"dextérité":+6,"constitution":+7,
                           "intelligence":+6,"sagesse":+10,"charisme":+8},
            },
        }

        def _execute_action_mechanics(char_name, intention, regle, cible, mj_note):
            """
            Exécute directement les mécaniques D&D 5e en Python et retourne
            un résumé [RÉSULTAT SYSTÈME] à injecter dans le contexte de l agent.
            """
            from state_manager import roll_dice, use_spell_slot, update_hp

            stats  = _CHAR_MECHANICS.get(char_name, {})
            r_low  = regle.lower()
            i_low  = intention.lower()
            results = []
            narrative_hint = ""

            if mj_note:
                results.append(f"Note MJ : {mj_note}")

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
                """Retourne le premier dé individuel (nat 20 critique)."""
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

            is_spell = any(k in r_low or k in i_low for k in SPELL_KW)
            is_atk   = any(k in r_low or k in i_low for k in ATK_KW) and not is_spell
            is_skill = any(k in r_low or k in i_low for k in SKILL_KW) and not is_atk and not is_spell

            # ── ATTAQUE ──────────────────────────────────────────────────────
            if is_atk:
                ranged = any(k in r_low or k in i_low
                             for k in ("distance","arc","arbalète","javelot","projectile"))
                m_bon = _re.search(r"bonus\s*([+-]\d+)", r_low)
                atk_bonus = (int(m_bon.group(1)) if m_bon
                             else stats.get("atk_ranged" if ranged else "atk_melee", +5))

                all_d  = _all_dice(regle)
                # 1ère expr = 1d20 de l agent, 2ème = dégâts si présents
                dmg_d  = all_d[1] if len(all_d) >= 2 else None
                if dmg_d is None:
                    dn, df, db = stats.get("dmg_melee", (1, 8, 0))
                else:
                    dn, df, db = dmg_d

                n_atk  = stats.get("n_attacks", 1)
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

                narrative_hint = (
                    f"Le système a lancé le jet. "
                    f"Narre en 1 phrase la tentative de {char_name} : {intention}. "
                    f"Ne répète pas les chiffres. Attends que le MJ décrive l effet si DC inconnue."
                )

            # ── SORT ─────────────────────────────────────────────────────────
            elif is_spell:
                lvl       = _extract_level(regle) or _extract_level(intention)
                is_cantrip = lvl is None or lvl == 0
                is_heal   = any(k in r_low or k in i_low
                                for k in ("soin","soigne","heal","cure","guéri",
                                          "restaure","parole curative"))
                is_atk_roll = any(k in r_low for k in ("jet d attaque de sort",
                                                         "attaque de sort","rayon"))
                dc_val    = _extract_dc(regle)

                results.append(f"✨ {char_name} — {regle} → {cible}")

                # Slot
                if not is_cantrip and lvl:
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

                # Jet d attaque de sort
                if is_atk_roll:
                    atk_spell = stats.get("atk_spell", +5)
                    res = roll_dice(char_name, "1d20", atk_spell)
                    results.append(f"  [attaque sort] {res}")
                    if dc_val:
                        tot = _total(res)
                        if tot is not None:
                            results.append(f"  → CA {dc_val} : {'TOUCHÉ ✅' if tot >= dc_val else 'RATÉ ❌'}")

                # Dés de dégâts / soin
                all_d = _all_dice(regle)
                if all_d:
                    dn, df, db = all_d[0]
                    verb = "soin" if is_heal else "dégâts"
                    res  = roll_dice(char_name, f"{dn}d{df}", db)
                    results.append(f"  [{verb}] {res}")
                    if is_heal:
                        tot = _total(res)
                        heal_amt = tot or 0
                        targets = [n for n in PLAYER_NAMES if n.lower() in cible.lower()]
                        if not targets:
                            targets = [char_name]
                        for tgt in targets:
                            hp_res = update_hp(tgt, heal_amt)
                            results.append(f"  [PV] {hp_res}")

                if dc_val and not is_atk_roll:
                    results.append(
                        f"  → Cibles : jet de sauvegarde DC {dc_val}. "
                        f"Le MJ gère la réussite/échec."
                    )

                narrative_hint = (
                    f"Le système a exécuté la mécanique du sort. "
                    f"Narre en 1-2 phrases comment {char_name} incante et l effet visible sur {cible}. "
                    f"Ne mentionne pas les chiffres bruts."
                )

            # ── ACTION SPÉCIALE non couverte ─────────────────────────────────
            else:
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

        def patched_receive(self_mgr, message, sender, request_reply=None, silent=False):
            if isinstance(message, dict):
                content    = message.get("content", "")
                name       = message.get("name", sender.name)
                tool_calls = message.get("tool_calls", None)
            else:
                content    = message
                name       = sender.name
                tool_calls = None

            is_system = False
            if isinstance(message, dict) and message.get("role") == "tool":
                is_system = True
            if content and str(content).startswith("[RÉSULTAT SYSTÈME]"):
                is_system = True

            # ── FILTRE COMBAT : agent hors-tour ayant déjà utilisé sa réaction ──
            if (not is_system
                    and COMBAT_STATE["active"]
                    and name in PLAYER_NAMES
                    and name != COMBAT_STATE.get("active_combatant")
                    and name in COMBAT_STATE["spoken_off_turn"]):
                _app.msg_queue.put({
                    "sender": "⚔️ Combat",
                    "text":   f"🤫 {name} — silencieux (intervention déjà utilisée ce round).",
                    "color":  "#444455"
                })
                _original_receive(self_mgr, message, sender, request_reply, silent)
                return

            # ── FILTRE COMBAT : violation hors-tour (action/mouvement/sort interdit) ──
            # Détecte les tentatives d'action physique non autorisées hors réaction/parole.
            _ILLEGAL_OFFTURN = _re.compile(
                r"\b(je me d[eé]place|je cours|je bouge|je marche|je recule|je charge"
                r"|j'attaque(?! d'opportunit)|j'effectue une attaque"
                r"|je lance (?!un regard|un cri|un mot|un avertissement)"
                r"|je d[eé]coche|je frappe|je plonge|je saute|je roule"
                r"|action bonus|j'utilise mon action(?! de r[eé]action)"
                r"|je m'interpose|je me pr[eé]cipite)\b",
                _re.IGNORECASE
            )
            _is_offturn_violation = (
                not is_system
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

            # ── INTERCEPTION SORT : [SORT: Nom | Niveau: X | Cible: Y] ──────────
            if (not is_system
                    and name in SPELL_CASTERS
                    and content
                    and _sort_pattern.search(str(content))):
                m = _sort_pattern.search(str(content))
                spell_name  = m.group("nom").strip()
                spell_level = int(m.group("niveau"))
                target      = (m.group("cible") or "").strip()
                # Retire la balise du contenu affiché — ne montrer que le roleplay
                clean_content = _sort_pattern.sub("", str(content)).strip()

                # Bloque l'agent pendant que le MJ décide
                _spell_confirm_event.clear()
                _spell_confirm_result.clear()

                def _resume_cb(confirmed, actual_level,
                               _ev=_spell_confirm_event, _res=_spell_confirm_result):
                    _res["confirmed"]    = confirmed
                    _res["actual_level"] = actual_level
                    _ev.set()

                _app.msg_queue.put({
                    "action":          "spell_confirm",
                    "char_name":       name,
                    "spell_name":      spell_name,
                    "spell_level":     spell_level,
                    "target":          target,
                    "resume_callback": _resume_cb,
                })

                # Affiche la partie roleplay sans la balise
                if clean_content and clean_content != "[SILENCE]":
                    _app.msg_queue.put({"sender": name, "text": clean_content,
                                        "color": _app.CHAR_COLORS.get(name, "#e0e0e0")})
                    _app.audio_queue.put((clean_content, name))

                # Bloque jusqu'à la décision du MJ (max 5 min)
                _spell_confirm_event.wait(timeout=300)

                if not _spell_confirm_result.get("confirmed", False):
                    # Sort refusé : on laisse passer le message original mais sans effet
                    pass

                # Dans tous les cas on continue (l'agent reprendra naturellement)
                _original_receive(self_mgr, message, sender, request_reply, silent)
                return

            # ── INTERCEPTION ACTION : [ACTION] Intention / Règle 5e / Cible ──────
            # Présent quand un joueur déclare une intention mécanique explicite.
            # On affiche le bloc au MJ sous forme de carte de confirmation,
            # puis on bloque l'agent jusqu'à la réponse du MJ.
            if (not is_system
                    and name in PLAYER_NAMES
                    and content
                    and _action_pattern.search(str(content))):

                m_a = _action_pattern.search(str(content))
                intention = m_a.group("intention").strip()
                regle     = m_a.group("regle").strip()
                cible     = m_a.group("cible").strip()

                # Affiche le roleplay (tout ce qui précède le bloc [ACTION])
                clean_content = _action_pattern.sub("", str(content)).strip()
                if clean_content and clean_content != "[SILENCE]":
                    _app.msg_queue.put({
                        "sender": name,
                        "text":   clean_content,
                        "color":  _app.CHAR_COLORS.get(name, "#e0e0e0"),
                    })
                    _app.audio_queue.put((clean_content, name))

                # Bloque l'agent pendant la confirmation du MJ
                _action_confirm_event.clear()
                _action_confirm_result.clear()

                def _action_resume_cb(confirmed, mj_note="",
                                      _ev=_action_confirm_event,
                                      _res=_action_confirm_result):
                    _res["confirmed"] = confirmed
                    _res["mj_note"]   = mj_note
                    _ev.set()

                _app.msg_queue.put({
                    "action":          "action_confirm",
                    "char_name":       name,
                    "intention":       intention,
                    "regle":           regle,
                    "cible":           cible,
                    "resume_callback": _action_resume_cb,
                })

                # Bloque jusqu'à décision du MJ (max 10 min)
                _action_confirm_event.wait(timeout=600)

                confirmed = _action_confirm_result.get("confirmed", False)
                mj_note   = _action_confirm_result.get("mj_note", "")

                if confirmed:
                    # Exécute les mécaniques D&D 5e directement en Python,
                    # puis injecte le résultat pour que l'agent narre l'effet.
                    try:
                        feedback = _execute_action_mechanics(
                            name, intention, regle, cible, mj_note
                        )
                    except Exception as _exec_err:
                        feedback = (
                            f"[MJ → {name}] ✅ Action autorisée. "
                            f"(Erreur système lors de l'exécution : {_exec_err}) "
                            f"Narre l'action : {intention} — {regle} → {cible}"
                        )

                    # Affiche le résultat dans le chat (visible par le MJ)
                    _app.msg_queue.put({
                        "sender": "⚙️ Système",
                        "text":   feedback,
                        "color":  "#4fc3f7",
                    })

                    # Injecte dans le contexte autogen (silencieux côté affichage)
                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": feedback, "name": "Alexis_Le_MJ"},
                        sender,
                        request_reply=False,
                        silent=True,
                    )
                else:
                    note_txt = f" {mj_note}" if mj_note else ""
                    feedback = f"[MJ → {name}] ❌ Action refusée.{note_txt}"
                    _app.msg_queue.put({
                        "sender": "❌ MJ",
                        "text":   feedback,
                        "color":  "#ef9a9a",
                    })
                    _original_receive(
                        self_mgr,
                        {"role": "user", "content": feedback, "name": "Alexis_Le_MJ"},
                        sender,
                        request_reply=False,
                        silent=True,
                    )

                _original_receive(self_mgr, message, sender, request_reply, silent)
                return

            # Appel normal
            _original_receive(self_mgr, message, sender, request_reply, silent)

            # ── MÉMOIRES CONTEXTUELLES : détection dynamique sur chaque message ──
            # Scan sur le contenu de TOUS les messages non-système (joueurs et MJ)
            # pour activer les mémoires mentionnées et enrichir les prompts en temps réel.
            if not is_system and content and str(content).strip() not in ("[SILENCE]", ""):
                _app._update_contextual_memories(str(content))

            # ── FILTRE PNJ : détection de paroles inventées ──────────────────────
            if not is_system and name in PLAYER_NAMES and content and _pnj_pattern.search(str(content)):
                _app.msg_queue.put({
                    "sender": "⚠️ Règle",
                    "text": (
                        f"[VIOLATION] {name} a tenté d'incarner un PNJ. "
                        f"Ce passage a été masqué. Alexis, c'est à vous de donner la réplique du PNJ."
                    ),
                    "color": "#F44336"
                })
                return

            # ── SUIVI COMBAT : marque la réaction/parole hors-tour utilisée ──────
            if (not is_system
                    and COMBAT_STATE["active"]
                    and name in PLAYER_NAMES
                    and name != COMBAT_STATE.get("active_combatant")
                    and content
                    and str(content).strip() != "[SILENCE]"):
                COMBAT_STATE["spoken_off_turn"].add(name)
                _app._update_agent_combat_prompts()
                _app.msg_queue.put({
                    "sender": "⚔️ Combat",
                    "text":   f"↺ {name} — réaction/parole hors-tour consommée pour ce round.",
                    "color":  "#8855aa"
                })

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
                    _app.msg_queue.put({"sender": display_name, "text": content, "color": color})
                    if not is_system and display_name in PLAYER_NAMES:
                        _app.audio_queue.put((content, display_name))

                if tool_calls:
                    _app.msg_queue.put({"sender": name, "text": "✨[Est en train de préparer une action/un sort...]", "color": "#aaaaaa"})

        # Substitution de classe (atomique, safe avec gRPC) au lieu de types.MethodType sur l'instance
        manager.__class__ = type(
            "PatchedGroupChatManager",
            (manager.__class__,),
            {"receive": patched_receive}
        )

        self.msg_queue.put({"sender": "Système", "text": "⚔️ Tous les joueurs sont à la table. À vous de lancer la partie (Texte ou 🎤)...", "color": "#888888"})

        # Enregistre l'ID du thread pour pouvoir l'interrompre via ctypes
        self._autogen_thread_id = threading.current_thread().ident

        self._set_waiting_for_mj(True)
        premier_message = self.wait_for_input()
        self._set_waiting_for_mj(False)
        clear_hist = True
        
        while True:
            try:
                self._set_llm_running(True)
                mj_agent.initiate_chat(
                    manager,
                    message=premier_message,
                    clear_history=clear_hist
                )
                self._set_llm_running(False)
                break  # La session s'est terminée normalement
            except StopLLMRequested:
                self._set_llm_running(False)
                self._set_waiting_for_mj(False)
                if self._pending_interrupt_input is not None:
                    premier_message = self._pending_interrupt_input
                    self._pending_interrupt_input = None
                    # ← Affiche le message utilisateur APRÈS l'arrêt effectif
                    if self._pending_interrupt_display is not None:
                        self.msg_queue.put(self._pending_interrupt_display)
                        self._pending_interrupt_display = None
                    self.msg_queue.put({"sender": "Système", "text": "▶️ Reprise avec le nouveau message.", "color": "#aaaaaa"})
                else:
                    self._pending_interrupt_display = None
                    self.msg_queue.put({"sender": "Système", "text": "⏹️ LLMs arrêtés. Tapez un message pour reprendre.", "color": "#FF9800"})
                    self._set_waiting_for_mj(True)
                    premier_message = self.wait_for_input()
                    self._set_waiting_for_mj(False)
                clear_hist = False
            except Exception as e:
                self._set_llm_running(False)
                import traceback
                traceback.print_exc()

                err_msg = str(e)
                is_quota_error = "RESOURCE_EXHAUSTED" in err_msg or "429" in err_msg or "quota" in err_msg.lower()

                # ── Détection quota gemini-2.5-pro → bascule auto vers flash ──────
                if is_quota_error and "gemini-2.5-pro" in err_msg:
                    try:
                        state = load_state()
                        switched = []
                        for char_name, char_data in state.get("characters", {}).items():
                            if char_data.get("llm", "") == "gemini-2.5-pro":
                                state["characters"][char_name]["llm"] = "gemini-2.5-flash"
                                switched.append(char_name)
                        if switched:
                            save_state(state)
                            self.msg_queue.put({
                                "sender": "⚠️ Système (Auto-Fallback)",
                                "text": (
                                    f"⚡ Quota Gemini Pro épuisé pour aujourd'hui.\n"
                                    f"✅ Basculement automatique vers gemini-2.5-flash pour : {', '.join(switched)}.\n"
                                    f"Les modèles ont été mis à jour dans campaign_state.json.\n"
                                    f"Tapez un nouveau message pour reprendre (l'historique est conservé)."
                                ),
                                "color": "#FF9800"
                            })
                    except Exception as switch_err:
                        print(f"[Auto-Fallback] Erreur lors du basculement : {switch_err}")
                        self.msg_queue.put({
                            "sender": "⚠️ Système (Crash IA)",
                            "text": (
                                "❌ Quota Gemini Pro épuisé ET échec du basculement automatique.\n"
                                f"Détail : {err_msg}\n\n"
                                "💡 Changez manuellement 'gemini-2.5-pro' → 'gemini-2.5-flash' dans campaign_state.json\n"
                                "puis relancez l'application."
                            ),
                            "color": "#F44336"
                        })
                else:
                    # Autre type d'erreur — message générique
                    self.msg_queue.put({
                        "sender": "⚠️ Système (Crash IA)",
                        "text": (
                            "❌ L'IA a rencontré une erreur fatale et tous les modèles de secours ont échoué.\n"
                            f"Détail : {err_msg}\n\n"
                            "💡 CONSEIL : Si c'est un problème de Quota (ex: 429), attendez quelques minutes ou changez les modèles/clés API dans le fichier .env.\n"
                            "L'application est toujours active. Tapez un nouveau message pour relancer la partie (l'historique est conservé)."
                        ),
                        "color": "#F44336"
                    })

                # On attend une nouvelle entrée du MJ pour retenter
                self._set_waiting_for_mj(True)
                premier_message = self.wait_for_input()
                self._set_waiting_for_mj(False)
                clear_hist = False  # On ne vide pas l'historique pour reprendre là où ça a crashé

if __name__ == "__main__":
    root = tk.Tk()
    app = DnDApp(root)
    root.mainloop()