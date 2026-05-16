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

import sys
from unittest.mock import MagicMock
# FIX A2 — MOCK MASSIVE GOOGLE CORE DEPS
# pyautogen imports google.cloud.aiplatform and vertexai by default. On slow systems,
# these massive C-extensions can lock the Python GIL for multiple minutes while loading,
# totally freezing the Tkinter mainloop update tasks. Since VTTAI2 connects to Gemini 
# via the autogen OpenAI HTTP wrapper rather than VertexAI natively, we completely 
# eliminate the bottleneck by mocking them out.
sys.modules['google'] = MagicMock()
sys.modules['google.cloud.aiplatform'] = MagicMock()
sys.modules['vertexai'] = MagicMock()
sys.modules['grpc'] = MagicMock()

import subprocess
import os

# FIX X11 AT-SPI — Disable Assistive Technology DBus Bridge
# The dbus-daemon routing AT-SPI accessibility events can randomly hang 
# for 25-45 seconds on some Linux distros, completely halting the Tkinter 
# event loop. This bypasses the bridge entirely to guarantee stability.
os.environ["NO_AT_BRIDGE"] = "1"

import urllib.request
import time
import faulthandler as _fh; _fh.enable()

def _restart_ibus():
    try:
        subprocess.Popen(
            ["ibus-daemon", "--xim", "--replace", "-d"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except FileNotFoundError:
        pass  # ibus not installed, no problem

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
import time as _time_dbg
import tkinter as tk
import tkinter.font as tk_font
from tkinter import scrolledtext
from dotenv import load_dotenv

# ====================================================================
# FIX C — Early Imports to Bypass the Python Global Import Lock
# When `run_autogen` heavily loads `autogen` in a background thread, the global 
# import lock freezes the Tkinter UI thread if it encounters ANY lazy `import`.
# We eagerly import everything VTTAI2 needs right away so sys.modules is primed.
# ====================================================================
import spell_data
import class_data
import race_data
import agent_logger
import app_config
import llm_config
import tkinter.messagebox
import tkinter.simpledialog
import tkinter.ttk
import collections
import random
# FIX ABSOLU — Anti-Corruption du GC (Garbage Collector)
# Empêche la destruction asynchrone des objets Tkinter ET des images PIL
# par les threads en arrière-plan (cause #1 des Segfaults Tcl sous Linux).
# ====================================================================
_classes_to_patch =[tk.Variable, tk.Image, tk_font.Font]

try:
    from PIL import ImageTk
    _classes_to_patch.extend([ImageTk.PhotoImage, ImageTk.BitmapImage])
except ImportError:
    pass

for _cls in _classes_to_patch:
    _orig_del = getattr(_cls, "__del__", None)
    if _orig_del:
        def _make_safe_del(orig_del):
            def _safe_del(self):
                try:
                    import threading
                    # Si le GC tourne dans le thread principal, on nettoie proprement
                    if threading.current_thread() is threading.main_thread():
                        orig_del(self)
                except Exception:
                    pass
            return _safe_del
        setattr(_cls, "__del__", _make_safe_del(_orig_del))
# ====================================================================

_startup_t0 = _time_dbg.time()
def _dbg(msg):
    elapsed = _time_dbg.time() - _startup_t0
    line = f"[STARTUP +{elapsed:.3f}s] {msg}"
    print(line, flush=True)
    try:
        with open("/tmp/vttai_startup.log", "a") as f:
            f.write(line + "\n")
    except: pass

_dbg("stdlib importé")

# ── Imports des modules du projet ─────────────────────────────────────────────
_dbg("import tk_widgets...")
from tk_widgets import apply_safe_patches          # FIX C — patches Tk avant tout widget
_dbg("import llm_config...")
from llm_config import (build_llm_config, _default_model,
                        StopLLMRequested, DND_SKILLS, ABILITY_COLORS)
_dbg("import app_config...")
from app_config import (APP_CONFIG, get_agent_config, get_chronicler_config,
                        get_groupchat_config, get_memories_config, reload_app_config)
_dbg("import config_panel...")
from config_panel import open_config_panel
_dbg("import window_state...")
from window_state import (WindowManagerMixin, _load_window_state, _save_window_state,
                          _get_win_geometry, _apply_win_geometry)
_dbg("import ui_setup_mixin...")
from ui_setup_mixin    import UISetupMixin
_dbg("import chat_mixin...")
from chat_mixin        import ChatMixin
_dbg("import character_mixin...")
from character_mixin   import CharacterMixin
_dbg("import panels_core_mixin...")
from panels_core_mixin import PanelsCoreMixin
_dbg("import panels_calendar_mixin...")
from panels_calendar_mixin import PanelsCalendarMixin
_dbg("import panels_scene_mixin...")
from panels_scene_mixin import PanelsSceneMixin
_dbg("import panels_npc_mixin...")
from panels_npc_mixin import PanelsNPCMixin
_dbg("import panels_tools_mixin...")
from panels_tools_mixin import PanelsToolsMixin

# ── Imports des mixins issus du découpage de main.py ──────────────────────────
_dbg("import session_mixin...")
from session_mixin         import SessionMixin           # trigger_save, résumé session, reset
_dbg("import session_pause_mixin...")
from session_pause_mixin   import SessionPauseMixin      # pause/reprise globale de la session
_dbg("import combat_tracker_mixin...")
from combat_tracker_mixin  import CombatTrackerMixin     # open_combat_tracker, callbacks de tour
_dbg("import image_broadcast_mixin...")
from image_broadcast_mixin import ImageBroadcastMixin    # _broadcast_location_image
_dbg("import llm_control_mixin...")
from llm_control_mixin     import LLMControlMixin        # stop_llms, send_text, vote, skill check
_dbg("import autogen_engine...")
from autogen_engine        import AutogenEngineMixin     # run_autogen — moteur principal
_dbg("import campaign_log_mixin...")
from campaign_log_mixin    import CampaignLogMixin       # journal long terme + archivage
_dbg("import quest_tracker_mixin...")
from quest_tracker_mixin   import QuestTrackerMixin      # analyse IA des quêtes
_dbg("import volume_mixin...")
from volume_mixin          import VolumeControlMixin     # slider volume audio global
_dbg("import music_mixer...")
from music_mixer           import MusicMixerMixin        # mixer audio dual-channel

# ── Imports des modules métier ─────────────────────────────────────────────────
_dbg("import state_manager (big)...")
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
    save_session_log, get_session_logs_prompt,
    get_active_characters,
    get_spells_prompt,
    get_inventory_prompt,
    get_health_prompt,
    get_campaign_log_toc_prompt, get_campaign_log_prompt,
)
_dbg("import voice_interface...")
from voice_interface   import record_audio_and_transcribe, play_voice
_dbg("import agent_logger...")
from agent_logger      import log_llm_start, log_llm_end, log_tts_start, log_tts_end
_dbg("import character_faces...")
from character_faces   import create_character_faces, CharacterFaceWindow, CHARACTER_DATA
_dbg("import combat_tracker...")
from combat_tracker    import CombatTracker, COMBAT_STATE, get_combat_prompt, _is_fully_silenced
_dbg("import combat_simulator...")
from combat_simulator  import CombatSimulator
_dbg("import combat_map_panel...")
from combat_map_panel  import get_map_prompt
_dbg("TOUS LES IMPORTS TERMINÉS")

# ─── Variables .env requises selon les fournisseurs utilisés ─────────────────
# GEMINI_API_KEY=...          → https://aistudio.google.com/app/apikey
# GROQ_API_KEY=...            → https://console.groq.com/keys  (gratuit)
# OPENROUTER_API_KEY=...      → https://openrouter.ai/keys     (gratuit)
# DEFAULT_LLM_MODEL=gemini-2.5-flash   ← modèle du résumé et du GroupChatManager
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv()

# Applique les patches Tk AVANT toute création de widget (FIX C)
apply_safe_patches()

from tab_autocomplete_mixin import TabAutocompleteMixin

class DnDApp(
    WindowManagerMixin,
    UISetupMixin,
    ChatMixin,
    TabAutocompleteMixin,
    CharacterMixin,
    PanelsCoreMixin,
    PanelsCalendarMixin,
    PanelsSceneMixin,
    PanelsNPCMixin,
    PanelsToolsMixin,
    # ── Nouveaux mixins issus du découpage de main.py ─────────────────────────
    SessionMixin,           # session_mixin.py        — cycle de vie des sessions
    SessionPauseMixin,      # session_pause_mixin.py  — pause/reprise globale
    CombatTrackerMixin,     # combat_tracker_mixin.py — tracker de combat D&D
    ImageBroadcastMixin,    # image_broadcast_mixin.py — images de lieu aux agents
    LLMControlMixin,        # llm_control_mixin.py   — contrôle LLM + MJ commands
    AutogenEngineMixin,     # autogen_engine.py       — moteur AutoGen complet
    CampaignLogMixin,       # campaign_log_mixin.py   — journal long terme
    QuestTrackerMixin,      # quest_tracker_mixin.py  — analyse IA des quêtes
    VolumeControlMixin,     # volume_mixin.py         — slider volume audio global
    MusicMixerMixin,        # music_mixer.py          — mixer audio dual-channel
):
    """Moteur de l'Aube Brisée — Interface du Maître de Jeu."""

    CHAR_COLORS = {"Kaelen": "#e57373", "Elara": "#64b5f6", "Thorne": "#ce93d8", "Lyra": "#81c784"}

    # Modificateurs de compétence / sauvegardes par personnage (niveau 11)
    # Clés normalisées en minuscules sans accents pour la comparaison
    _SKILL_MODIFIERS: dict = {
        "Kaelen": {   # Paladin 11 — STR20 DEX10 CON16 INT10 WIS14 CHA18 — Prof+4
            "skills": {"athlétisme": +10, "perspicacité": +7, "perception": +7,
                       "médecine": +7, "persuasion": +9, "intimidation": +9,
                       "religion": +5, "histoire": +5},
            "saves":  {"force": +10, "dextérité": +5, "constitution": +8,
                       "intelligence": +5, "sagesse": +7, "charisme": +9},
            "default_ability": {"Force": +5, "Dextérité": +0, "Constitution": +3,
                                "Intelligence": +0, "Sagesse": +2, "Charisme": +4},
        },
        "Elara": {    # Magicienne 11 — STR8 DEX14 CON14 INT20 WIS12 CHA10 — Prof+4
            "skills": {"arcanes": +15, "histoire": +15, "investigation": +12,
                       "perception": +6, "perspicacité": +6, "médecine": +6,
                       "nature": +10, "religion": +10},
            "saves":  {"force": +4, "dextérité": +7, "constitution": +7,
                       "intelligence": +10, "sagesse": +6, "charisme": +5},
            "default_ability": {"Force": -1, "Dextérité": +2, "Constitution": +2,
                                "Intelligence": +5, "Sagesse": +1, "Charisme": +0},
        },
        "Thorne": {   # Roublard 11 — STR10 DEX20 CON14 INT14 WIS12 CHA14 — Prof+4
            "skills": {"discrétion": +15, "acrobaties": +10, "escamotage": +15,
                       "perception": +11, "perspicacité": +6, "acrobaties": +10,
                       "investigation": +8, "athlétisme": +6, "intimidation": +7},
            "saves":  {"force": +6, "dextérité": +10, "constitution": +7,
                       "intelligence": +8, "sagesse": +6, "charisme": +7},
            "default_ability": {"Force": +0, "Dextérité": +5, "Constitution": +2,
                                "Intelligence": +2, "Sagesse": +1, "Charisme": +2},
        },
        "Lyra": {     # Clerc Vie 11 — STR14 DEX12 CON14 INT12 WIS20 CHA16 — Prof+4
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

        # ── Etat fenêtres chargé en mémoire ─────────────────────────
        self._win_state: dict = _load_window_state()
        
        # 1. Avant mainloop(), on n'applique QUE la taille hardcodée 
        # (1100x750) pour empêcher catégoriquement le deadlock GNOME XWayland.
        # On ne transmet JAMAIS les tailles variables de window_state ici !
        self.root.geometry("1100x750")

        self.msg_queue = queue.Queue()
        self.audio_queue = queue.Queue()
        self.input_event = threading.Event()
        # FIX : lock pour protéger user_input contre les race conditions entre threads
        self._input_lock = threading.Lock()
        self._user_input = ""
        self.groupchat = None  # <-- Stockage de la session pour pouvoir la résumer
        # --- MODE PNJ ---
        self.active_npc = None          # dict du PNJ actif ou None (mode MJ normal)
        self._npc_var = None            # StringVar du dropdown (initialisé dans setup_ui)

        # --- STOP LLM ---
        self._autogen_thread_id: int | None = None
        self._autogen_thread: threading.Thread | None = None   # pour join() au reset
        self._stop_event = threading.Event()   # mécanisme d'arrêt fiable (sondé dans wrapper)
        self._llm_running = False
        self._waiting_for_mj = False          # True quand c'est au MJ de parler
        self._pending_interrupt_input: str | None = None
        self._pending_interrupt_display: dict | None = None
        # Trigger de tour combat pré-calculé par _on_pc_turn, consommé par gui_get_human_input.
        # Nécessaire car root.after(0,...) peut s'exécuter AVANT que get_human_input soit appelé,
        # soit quand _waiting_for_mj est encore False → le trigger serait perdu sans ce buffer.
        self._pending_combat_trigger: str | None = None
        # Retrigger IMPOSSIBLE : (char_name, instruction) stocké par append_message (thread Tk)
        # quand un [RÉSULTAT SYSTÈME — * IMPOSSIBLE — NomAgent] est affiché.
        # Consommé par gui_get_human_input (thread AutoGen).
        self._pending_impossible_retrigger: tuple | None = None

        # --- PAUSE SESSION ---
        self._session_paused: bool = False
        self._was_llm_running_at_pause: bool = False

        # --- APPROBATIONS MJ EN ATTENTE ---
        # Liste des threading.Event créés dans autogen_engine pour les confirmations
        # MJ (autoriser/refuser action, sort, dégâts…). Quand _inject_stop ou
        # _inject_stop_for_pause est appelé, ces events sont tous .set() pour
        # débloquer immédiatement les .wait(timeout=600) dans autogen_engine.
        self._pending_approval_events: list = []
        self._approval_events_lock = threading.Lock()

        # --- VISAGES & COMBAT ---
        self.face_windows: dict = {}
        self._combat_tracker_win = None   # référence à la fenêtre Toplevel du tracker
        self._agents: dict = {}              # {name: AssistantAgent} pour MAJ des prompts
        self._base_system_msgs: dict = {}    # system_message de base sans combat
        # État du switch LLM combat — True quand les agents sont sur _COMBAT_LLM_MODEL.
        # Utilisé par _update_agent_combat_prompts pour n'appeler _set_combat_llm
        # QUE lors des transitions (combat ON / combat OFF), pas à chaque action.
        self._combat_llm_active: bool = False
        self._pre_combat_llm: dict = {}      # snapshot llm_config + client avant combat
        # Mémoires activées dynamiquement au fil de la conversation
        self._active_memory_ids: set  = set()   # IDs déjà injectés dans les prompts
        self._contextual_mem_block: str = ""    # bloc cumulatif des mémoires contextuelles

        # FIX D — Tout différé dans mainloop() via after(0).
        # Aucun widget ni thread C lancé depuis __init__.
        _dbg("DnDApp.__init__ terminé, scheduling _deferred_init")
        self.root.after(0, self._deferred_init)

    def _deferred_init(self):
        """S'exécute dans mainloop() via root.after(0).
        Garantit que setup_ui tourne sous contrôle exclusif de Xlib par Tk.
        """
        _dbg("_deferred_init START")
        
        # 2. Après mainloop() (fenêtre mappée avec succès), on force les offsets +X+Y
        _apply_win_geometry(self.root, self._win_state.get("main"), "1100x750")
        self.root.after(2000, self._poll_main_geometry)
        
        try:
            self._deferred_init_inner()
        except Exception as _e:
            import traceback
            print(f"[STARTUP CRASH] _deferred_init a planté :", flush=True)
            traceback.print_exc()

    def _deferred_init_inner(self):
        _dbg("_deferred_init START")
        from voice_interface import load_volume_from_config
        load_volume_from_config()   # charge le volume sauvegardé avant setup_ui
        _dbg("volume chargé, lancement setup_ui...")
        self.setup_ui()
        _dbg("setup_ui terminé")
        threading.Thread(target=self.audio_worker, daemon=True).start()
        self.root.after(100, self.process_queue)
        self.root.after(1000, self.update_stats_panel)

        # ── WINDOW RESTORE FIRST ──────────────────────────────────────────────
        # Windows open at +500ms with moderate stagger (150/300ms).
        # This MUST finish before autogen starts, because autogen's import
        # holds the GIL for ~1-2s and blocks all Tk event processing.
        self.root.after(500, self._restore_windows)

        # ── AUTOGEN LAST ──────────────────────────────────────────────────────
        # Start autogen well after windows are done constructing (~5s).
        # autogen's import + gRPC init holds the GIL and would freeze any
        # concurrent Tk widget construction.
        def _start_autogen():
            _dbg("_start_autogen called")
            t = threading.Thread(target=self.run_autogen, daemon=True, name="autogen-worker")
            self._autogen_thread = t
            t.start()

            # Boucle d'attente : on vérifie toutes les 500ms si les agents
            # sont enfin instanciés en mémoire avant de lancer la bascule.
            def _wait_and_sync_combat():
                if getattr(self, "_agents", None):
                    try:
                        from combat_tracker import COMBAT_STATE as _CS
                        if _CS.get("active") and not getattr(self, "_combat_llm_active", False):
                            self.msg_queue.put({
                                "sender": "⚙️ Système",
                                "text": "⚔️ Reprise d'un combat en cours — PJ basculés vers le modèle de combat.",
                                "color": "#ff9800"
                            })
                            self._update_agent_combat_prompts()
                    except Exception as e:
                        pass
                else:
                    self.root.after(500, _wait_and_sync_combat)

            self.root.after(1500, _wait_and_sync_combat)

        self.root.after(5000, _start_autogen)
        _dbg("_deferred_init END")

    # --- Accès thread-safe à user_input ---
    @property
    def user_input(self):
        with self._input_lock:
            return self._user_input

    @user_input.setter
    def user_input(self, value):
        with self._input_lock:
            self._user_input = value



    # ── Reconstruction dynamique des prompts agents ───────────────────────────

    def _rebuild_agent_prompts(self):
        """Reconstruit le system_message de chaque agent en incluant :
          - le prompt de base (personnalite + regles)
          - la composition du groupe (qui est present/absent)
          - la scene active, les quetes, les memoires compactes, le calendrier
          - les memoires contextuelles activees dynamiquement ce tour
          - l etat actuel des emplacements de sort (mis a jour a chaque tour)
          - le bloc de combat (si combat en cours)
          - la carte de combat (positions des tokens) si disponible
          - la table des matières du journal long terme (compact, permanent)
          - les entrées pertinentes du journal long terme (selon la scène)
        Appele apres chaque message joueur et apres toute activation de memoire contextuelle."""
        combat_block_fn = get_combat_prompt  # importe depuis combat_tracker

        # Contexte de scène courant pour la recherche dans le journal long terme
        try:
            _scene_context = get_scene_prompt()
        except Exception:
            _scene_context = ""

        # ── Bloc de composition du groupe (qui est présent/absent) ──────────
        _ALL_PC = ["Kaelen", "Elara", "Thorne", "Lyra"]
        _active_pcs = set(get_active_characters())
        _absent_pcs = [n for n in _ALL_PC if n not in _active_pcs]
        _present_pcs = [n for n in _ALL_PC if n in _active_pcs]
        if _absent_pcs:
            _party_block = (
                "\n[GROUPE PRÉSENT]\n"
                f"Membres présents dans la scène : {', '.join(_present_pcs)}.\n"
                f"Membres ABSENTS (ne pas leur parler, ne pas les cibler) : {', '.join(_absent_pcs)}.\n"
            )
        else:
            _party_block = (
                "\n[GROUPE PRÉSENT]\n"
                f"Tous les membres du groupe sont présents : {', '.join(_present_pcs)}.\n"
            )

        for name, agent in self._agents.items():
            # Règles dynamiques : HORS COMBAT ou EN COMBAT selon l'état actuel
            from engine_agents import build_regle_outils as _bro
            _rules = _bro(combat_mode=COMBAT_STATE["active"])
            _char_only = getattr(self, "_base_char_msgs", {}).get(name, "")
            base          = _rules + _char_only + _party_block
            combat_block  = combat_block_fn(name)

            # Carte de combat : personnalisée par agent (distances propres uniquement)
            map_block = get_map_prompt(self._win_state, for_hero=name, in_combat=COMBAT_STATE["active"])

            # ── MODE COMBAT : prompt minimal — contexte lore supprimé ────────
            # Seuls les blocs mécaniquement utiles pendant un round sont injectés.
            # Tout le lore (scène, quêtes, mémoires, calendrier, journal) est élidé :
            # il alourdit le contexte sans aider à décider d'une action tactique.
            if COMBAT_STATE["active"]:
                agent.update_system_message(
                    base
                    + get_spells_prompt(name)
                    + get_inventory_prompt()
                    + combat_block
                    + map_block
                )
            # ── MODE EXPLORATION : prompt complet ────────────────────────────
            else:
                scene_block   = get_scene_prompt()
                quest_block   = get_active_quests_prompt()
                mem_compact   = get_memories_prompt_compact(importance_min=get_memories_config().get("compact_importance_min", 2))
                cal_block     = get_calendar_prompt()
                ctx_block     = self._contextual_mem_block
                sessions_block= get_session_logs_prompt(max_sessions=3)
                toc_block     = get_campaign_log_toc_prompt()
                log_block     = get_campaign_log_prompt(
                    context_text = _scene_context,
                    char_name    = name,
                    max_entries  = 2,
                )
                agent.update_system_message(
                    base + scene_block + quest_block + mem_compact + cal_block
                    + sessions_block + toc_block + log_block
                    + get_spells_prompt(name)
                    + get_inventory_prompt()
                    + get_health_prompt(name)
                    + ctx_block + combat_block + map_block
                )

    # Alias conservé pour compatibilité avec les appels existants depuis le combat
    def _update_agent_combat_prompts(self):
        self._rebuild_agent_prompts()
        
        # Protection absolue : si les agents ne sont pas encore créés par AutoGen,
        # on annule la bascule pour ne pas fausser le drapeau _combat_llm_active.
        if not getattr(self, "_agents", None):
            return

        # ── Switch LLM sur transition combat ON / combat OFF ─────────────
        try:
            from combat_tracker import COMBAT_STATE as _CS
            _combat_now = bool(_CS.get('active'))
            if _combat_now != getattr(self, "_combat_llm_active", False):
                if hasattr(self, "_set_combat_llm"):
                    self._set_combat_llm(_combat_now)
                self._combat_llm_active = _combat_now
        except Exception as _e:
            print(f'[CombatLLM] Erreur switch : {_e}')

    def _restore_windows(self):
        """Rouvre les fenêtres qui étaient ouvertes lors de la dernière session."""
        _dbg("_restore_windows START")
        delay = 3000
        if self._win_state.get("_open_combat_tracker"):
            delay += 300
            self.root.after(delay, lambda: (_dbg("RESTORE: CT start"), self.open_combat_tracker(), _dbg("RESTORE: CT end")))
        if self._win_state.get("_open_inventory"):
            delay += 300
            self.root.after(delay, lambda: (_dbg("RESTORE: Inv start"), self.open_inventory_panel(), _dbg("RESTORE: Inv end")))
        if self._win_state.get("_open_quest_journal"):
            delay += 300
            self.root.after(delay, lambda: (_dbg("RESTORE: Quest start"), self.open_quest_journal(), _dbg("RESTORE: Quest end")))
        if self._win_state.get("_open_npc_manager"):
            delay += 300
            self.root.after(delay, lambda: (_dbg("RESTORE: NPC start"), self.open_npc_manager(), _dbg("RESTORE: NPC end")))
        if self._win_state.get("_open_location_image"):
            delay += 300
            self.root.after(delay, lambda: (_dbg("RESTORE: Loc start"), self.open_location_image_popout(), _dbg("RESTORE: Loc end")))
        if self._win_state.get("_open_calendar"):
            delay += 300
            self.root.after(delay, lambda: (_dbg("RESTORE: Cal start"), self.open_calendar_popout(), _dbg("RESTORE: Cal end")))
        if self._win_state.get("_open_combat_map"):
            delay += 300
            self.root.after(delay, lambda: (_dbg("RESTORE: Map start"), self.open_combat_map(), _dbg("RESTORE: Map end")))
        if self._win_state.get("_open_music_mixer"):
            delay += 300
            self.root.after(delay, lambda: (_dbg("RESTORE: Music start"), self.open_music_mixer(), _dbg("RESTORE: Music end")))
        
        if self._win_state.get("_open_tarokka"):
            delay += 300
            self.root.after(delay, lambda: (_dbg("RESTORE: Tarokka start"), self.open_tarokka_window(), _dbg("RESTORE: Tarokka end")))
        
        for name in["Kaelen", "Elara", "Thorne", "Lyra"]:
            if self._win_state.get(f"_open_char_{name}"):
                delay += 500   # each popout is ~1800 lines of synchronous widget code
                self.root.after(delay, lambda n=name: (_dbg(f"RESTORE: Char {n} start"), self.open_char_popout(n), _dbg(f"RESTORE: Char {n} end")))

    def open_search_window(self, exact_phrase="", search_adv=True, search_book=True):
        """Ouvre la fenêtre de recherche dans les livres et aventures."""
        from combat_map_search import AdventureSearchWindow
        
        if getattr(self, "_search_win", None) and self._search_win.top.winfo_exists():
            self._search_win.top.lift()
        else:
            map_app = getattr(self, "_combat_map_win", None)
            self._search_win = AdventureSearchWindow(self.root, adventure_dir="adventure", book_dir="book", map_app=map_app)
            self._track_window("search", self._search_win.top)

        # Pré-remplissage via la commande /search
        if exact_phrase:
            self._search_win.exact_var.set(exact_phrase)
        self._search_win.search_adv_var.set(search_adv)
        self._search_win.search_book_var.set(search_book)
        
        # Lancer la recherche automatiquement s'il y a un mot clé
        if exact_phrase:
            self._search_win._do_search()

    def open_tarokka_window(self):
        """Ouvre la fenêtre du tirage de Tarokka avec lecture/écriture via state_manager."""
        from tarokka_window import TarokkaWindow
        
        if getattr(self, "_tarokka_win", None) and self._tarokka_win.top.winfo_exists():
            self._tarokka_win.top.lift()
            return

        tarokka_state = {}

        # LECTURE VIA STATE MANAGER
        try:
            from state_manager import load_state
            state_data = load_state()
            raw_state = state_data.get("tarokka")
            tarokka_state = raw_state if raw_state is not None else {}
        except Exception as e:
            print(f"[Tarokka] Erreur de lecture : {e}")

        # ECRITURE VIA STATE MANAGER
        def save_tarokka_state(new_state):
            try:
                from state_manager import load_state, save_state
                state_data = load_state()
                
                # Si c'est un reset (liste vide), on supprime la clé proprement
                if not new_state.get("drawn_cards"):
                    state_data.pop("tarokka", None)
                else:
                    state_data["tarokka"] = new_state
                
                # Enregistre officiellement via le gestionnaire d'état pour éviter les conflits d'écrasement
                save_state(state_data)
                
            except Exception as e:
                print(f"[Tarokka] Erreur de sauvegarde : {e}")

        self._tarokka_win = TarokkaWindow(
            self.root, 
            self.msg_queue, 
            initial_state=tarokka_state, 
            save_callback=save_tarokka_state
        )
        self._track_window("tarokka", self._tarokka_win.top)

    # ── Callback tour héros (CombatTracker → AutoGen) ────────────────────────

    def _on_pc_turn(self, char_name: str):
        """Déclenché par le CombatTracker au début du tour d'un héros (PJ vivant).

        Double rôle :
          1. Reconstruire les system_messages de tous les agents avec l'état
             de combat à jour (PV, CA, positions, slots restants…).
          2. Injecter dans le flow AutoGen un message de déclenchement de tour
             adressé au héros concerné, afin qu'il déclare son action.

        Le message est stocké dans _pending_combat_trigger (buffer thread-safe).
        Si gui_get_human_input attend déjà une entrée MJ, on débloque
        immédiatement via user_input + input_event.set().
        Si AutoGen n'est pas encore en attente, le buffer sera consommé dès
        que gui_get_human_input sera appelé (race condition évitée).
        """
        # 1. L'indicateur de tour est maintenant affiché par _log_turn du tracker pour tout le monde (PJ et PNJ).
        
        # 2. Mettre à jour les system prompts et forcer la bascule LLM si nécessaire
        self._update_agent_combat_prompts()

        # 3. Construire le message de déclenchement de tour
        trigger = (
            f"[TOUR DE COMBAT — {char_name.upper()}]\n"
            f"C'est maintenant le tour de {char_name}. "
            f"{char_name}, décris et déclare ton action de combat "
            f"(attaque, sort, déplacement, action bonus…). "
            f"Envoie une [ACTION] de type 'Fin de tour' quand tu as terminé."
        )

        # 4. Stocker dans le buffer (consommé par gui_get_human_input)
        self._pending_combat_trigger = trigger

        # 5. Si le moteur attend déjà une entrée MJ → débloquer immédiatement
        if self._waiting_for_mj:
            self.user_input = trigger
            self.input_event.set()

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


if __name__ == "__main__":
    _dbg("Création de tk.Tk()...")
    root = tk.Tk()
    _dbg("tk.Tk() créé, instanciation DnDApp...")
    app = DnDApp(root)
    _dbg("DnDApp instancié")

    def _on_app_close():
        import copy
        import os
        # 1. Marquer la fermeture applicative
        app._app_closing = True
        # 2. Capturer l'état exact AVANT la fermeture
        final_state = copy.deepcopy(app._win_state)
        
        # 2b. Rattrapage d'événements : Si l'utilisateur quitte immédiatement après
        # avoir fermé une fenêtre (via withdraw), l'événement <Unmap> peut ne pas
        # encore avoir été traité par la boucle Tk avant os._exit().
        # On va donc inspecter de façon synchrone toutes les fenêtres trackées.
        with open("/home/wa/VTTAI2/vtt_debug_exit.log", "w") as f:
            f.write(f"APP CLOSE. TRACKED LIST LENGTH: {len(getattr(app, '_tracked_windows_list', []))}\n")
            for key, win, is_modal in getattr(app, "_tracked_windows_list", []):
                try:
                    exists = win.winfo_exists()
                    state = win.state() if exists else "N/A"
                    f.write(f"  checking TRACKED: key={key} | exists={exists} | state={state} | is_modal={is_modal}\n")
                    if exists and state == "withdrawn" and not is_modal:
                        f.write(f"    -> POPPING _open_{key} from final_state\n")
                        final_state.pop(f"_open_{key}", None)
                except Exception as e:
                    f.write(f"    -> EXCEPTION: {e}\n")

        # 3. Sauvegarder la position exacte de Windows
        try:
            _save_window_state(final_state)
            # Debounced save — flush synchronously before os._exit kills the timer
            from window_state import _flush_window_state
            _flush_window_state()
        except Exception:
            pass
        # 4a. Kill music mixer ffplay processes before exit
        _mixer = getattr(app, "_music_mixer_win", None)
        if _mixer:
            try:
                _mixer.save_state()
                _mixer._bg_channel.destroy()
                _mixer._combat_channel.destroy()
            except Exception:
                pass
        # 4. Cleanup TTS audio processes because `os._exit(0)` bypasses `atexit` handlers.
        try:
            import voice_interface
            voice_interface._kill_all_audio()
        except Exception:
            pass
            
        # 5. Terminate the process immediately, bypassing standard cleanup and Tk/X11 teardown
        #    This prevents the 5s X11/XWayland freeze and avoids UI-related segmentation faults
        #    since the OS simply drops the socket and reclaims memory.
        os._exit(0)

    root.protocol("WM_DELETE_WINDOW", _on_app_close)
    _dbg(">>> root.mainloop() ENTRY <<<")
    root.mainloop()