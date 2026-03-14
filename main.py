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

# ── Imports des mixins issus du découpage de main.py ──────────────────────────
from session_mixin         import SessionMixin           # trigger_save, résumé session, reset
from combat_tracker_mixin  import CombatTrackerMixin     # open_combat_tracker, callbacks de tour
from image_broadcast_mixin import ImageBroadcastMixin    # _broadcast_location_image
from llm_control_mixin     import LLMControlMixin        # stop_llms, send_text, vote, skill check
from autogen_engine        import AutogenEngineMixin     # run_autogen — moteur principal

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
    save_session_log, get_session_logs_prompt,
    get_active_characters,
)
from voice_interface   import record_audio_and_transcribe, play_voice
from agent_logger      import log_llm_start, log_llm_end, log_tts_start, log_tts_end
from character_faces   import create_character_faces, CharacterFaceWindow, CHARACTER_DATA
from combat_tracker    import CombatTracker, COMBAT_STATE, get_combat_prompt, _is_fully_silenced
from combat_simulator  import CombatSimulator
from combat_map_panel  import get_map_prompt

# ─── Variables .env requises selon les fournisseurs utilisés ─────────────────
# GEMINI_API_KEY=...          → https://aistudio.google.com/app/apikey
# GROQ_API_KEY=...            → https://console.groq.com/keys  (gratuit)
# OPENROUTER_API_KEY=...      → https://openrouter.ai/keys     (gratuit)
# DEFAULT_LLM_MODEL=gemini-2.5-flash   ← modèle du résumé et du GroupChatManager
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv()

# Applique les patches Tk AVANT toute création de widget (FIX C)
apply_safe_patches()


class DnDApp(
    WindowManagerMixin,
    UISetupMixin,
    ChatMixin,
    CharacterMixin,
    PanelsMixin,
    # ── Nouveaux mixins issus du découpage de main.py ─────────────────────────
    SessionMixin,           # session_mixin.py        — cycle de vie des sessions
    CombatTrackerMixin,     # combat_tracker_mixin.py — tracker de combat D&D
    ImageBroadcastMixin,    # image_broadcast_mixin.py — images de lieu aux agents
    LLMControlMixin,        # llm_control_mixin.py   — contrôle LLM + MJ commands
    AutogenEngineMixin,     # autogen_engine.py       — moteur AutoGen complet
):
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
        self.groupchat = None  # <-- Stockage de la session pour pouvoir la résumer
        # --- MODE PNJ ---
        self.active_npc = None          # dict du PNJ actif ou None (mode MJ normal)
        self._npc_var = None            # StringVar du dropdown (initialisé dans setup_ui)

        # --- STOP LLM ---
        self._autogen_thread_id: int | None = None
        self._llm_running = False
        self._waiting_for_mj = False          # True quand c'est au MJ de parler
        self._pending_interrupt_input: str | None = None
        self._pending_interrupt_display: dict | None = None
        # Trigger de tour combat pré-calculé par _on_pc_turn, consommé par gui_get_human_input.
        # Nécessaire car root.after(0,...) peut s'exécuter AVANT que get_human_input soit appelé,
        # soit quand _waiting_for_mj est encore False → le trigger serait perdu sans ce buffer.
        self._pending_combat_trigger: str | None = None

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

    # ── Reconstruction dynamique des prompts agents ───────────────────────────

    def _rebuild_agent_prompts(self):
        """Reconstruit le system_message de chaque agent en incluant :
          - le prompt de base (personnalite + regles)
          - la scene active, les quetes, les memoires compactes, le calendrier
          - les memoires contextuelles activees dynamiquement ce tour
          - l etat actuel des emplacements de sort (mis a jour a chaque tour)
          - le bloc de combat (si combat en cours)
          - la carte de combat (positions des tokens) si disponible
        Appele apres chaque message joueur et apres toute activation de memoire contextuelle."""
        combat_block_fn = get_combat_prompt  # importe depuis combat_tracker
        # Carte de combat : toujours injectée si elle contient des tokens
        # (exploration ET combat — la carte est utilisée en permanence)
        map_block = get_map_prompt(self._win_state)

        # Snapshot live des spell slots (source de verite : campaign_state)
        try:
            from state_manager import load_state as _ls_slots
            _slots_state = _ls_slots().get("characters", {})
        except Exception:
            _slots_state = {}

        for name, agent in self._agents.items():
            base          = self._base_system_msgs.get(name, "")
            scene_block   = get_scene_prompt()
            quest_block   = get_active_quests_prompt()
            mem_compact   = get_memories_prompt_compact(importance_min=get_memories_config().get("compact_importance_min", 2))
            cal_block     = get_calendar_prompt()
            ctx_block     = self._contextual_mem_block
            sessions_block= get_session_logs_prompt(max_sessions=3)
            combat_block  = combat_block_fn(name)

            # Bloc spell slots dynamique - relit campaign_state a chaque rebuild
            slots_block = ""
            _char_slots = _slots_state.get(name, {}).get("spell_slots", {})
            if _char_slots:
                _avail = [(k, v) for k, v in sorted(_char_slots.items(), key=lambda x: int(x[0])) if v > 0]
                _empty = [k for k, v in sorted(_char_slots.items(), key=lambda x: int(x[0])) if v == 0]
                lines_slots = ["\n\nEMPLACEMENTS DE SORT ACTUELS (mis a jour en temps reel) :"]
                if _avail:
                    lines_slots.append("  Disponibles : " + ", ".join(f"niv.{k}x{v}" for k, v in _avail))
                else:
                    lines_slots.append("  AUCUN emplacement disponible -- sorts a slot IMPOSSIBLES ce tour.")
                if _empty:
                    lines_slots.append("  Epuises    : " + ", ".join(f"niv.{k}" for k in _empty))
                lines_slots.append("  -> Ne declare PAS un sort a slot si le niveau requis est epuise.")
                slots_block = "\n".join(lines_slots)

            agent.update_system_message(
                base + scene_block + quest_block + mem_compact + cal_block
                + sessions_block + ctx_block + slots_block + combat_block + map_block
            )

    # Alias conservé pour compatibilité avec les appels existants depuis le combat
    def _update_agent_combat_prompts(self):
        self._rebuild_agent_prompts()

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
    root = tk.Tk()
    app = DnDApp(root)
    root.mainloop()