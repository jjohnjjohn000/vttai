"""
app_config.py — Configuration persistante de l'application (hors données campagne).

Stockée dans app_config.json, séparément de campaign_state.json.
Fournit load_app_config() / save_app_config() et APP_CONFIG (singleton chargé au démarrage).

Paramètres couverts :
  agents.*          → modèle LLM + température par personnage joueur
  chronicler.*      → modèle + température + importance_min mémoires + system_prompt override
  groupchat.*       → max_round, allow_repeat_speaker
  memories.*        → importance_min compact, tag_min_length (détection contextuelle)
  voice.*           → activer/désactiver TTS globalement + délai entre chunks
  ui.*              → délai polling géométrie fenêtres
"""

import os
import json
import threading

APP_CONFIG_FILE = "app_config.json"
_lock = threading.Lock()

# ─── Modèles disponibles (pour les dropdowns) ─────────────────────────────────
KNOWN_MODELS = [
    # ── Ollama local (préfixe "ollama/") ──────────────────────────────────────
    # Gemma 4 — sorti avril 2025, Apache 2.0, support Ollama dès le jour 1
    # RX 6700 XT 12 GB VRAM : e4b (9.6 GB) tient entièrement en VRAM ← recommandé
    #                          e2b (3.5 GB) ultra-léger, moins capable
    #                          27b/31b déconseillés (dépassent 12 GB)
    "ollama/gemma4:e4b",            # Gemma 4 effective 4B — 9.6 GB — RECOMMANDÉ RX 6700 XT
    "ollama/gemma4:e2b",            # Gemma 4 effective 2B — 3.5 GB — ultra-léger
    "ollama/gemma4:27b",            # Gemma 4 26B MoE — 18 GB — déborde sur RAM
    # Autres modèles Ollama populaires (s'ajoutent automatiquement via list_ollama_models())
    "ollama/llama3.3:latest",
    "ollama/mistral:latest",
    "ollama/deepseek-r1:8b",
    "ollama/qwen3.5:9b",

    # Gemini — modèles actifs
    "gemini-3.1-flash-lite-preview",
    "gemini-3-flash-preview",
    "gemma-4-31b-it",
    "gemma-4-26b-a4b-it",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    # gemini-2.0-flash — DÉPRÉCIÉ, retiré
    # Gemini 1.5 — DÉPRÉCIÉS, retirés
    # DeepSeek direct (DEEPSEEK_API_KEY)
    "deepseek/deepseek-chat",       # V3.2 — outil calls, 128K ctx, pas cher
    "deepseek/deepseek-reasoner",   # V3.2 thinking — CoT, ignorer temperature
    # Groq
    "groq/meta-llama/llama-4-scout-17b-16e-instruct",
    "groq/llama-3.3-70b-versatile",
    # groq/mixtral-8x7b-32768 — DÉPRÉCIÉ, retiré
    # OpenRouter — modèles moins restrictifs, adaptés au jeu de rôle
    "openrouter/nousresearch/hermes-3-llama-3.1-70b",        # Hermes 3 — roleplay, peu restrictif
    "openrouter/nousresearch/hermes-3-llama-3.1-405b",       # Hermes 3 large — plus puissant
    "openrouter/mistralai/mistral-small-3.1-24b-instruct",   # Mistral Small 3.1 — rapide, peu restrictif
    "openrouter/meta-llama/llama-3.3-70b-instruct",          # LLaMA 3.3 70B — non censuré
    "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/google/gemma-4-26b-a4b-it:free",
    "openrouter/minimax/minimax-m2.5:free",
    "openrouter/inclusionai/ling-2.6-1t:free"
]

# ─── Valeurs par défaut ────────────────────────────────────────────────────────
DEFAULTS: dict = {
    "agents": {
        "Kaelen": {
            "model":       "gemini-2.5-flash",
            "temperature": 0.7,
        },
        "Elara": {
            "model":       "gemini-2.5-flash",
            "temperature": 0.7,
        },
        "Thorne": {
            "model":       "groq/meta-llama/llama-4-scout-17b-16e-instruct",
            "temperature": 0.8,
        },
        "Lyra": {
            "model":       "gemini-2.5-flash",
            "temperature": 0.6,
        },
    },
    "chronicler": {
        "model":              "gemini-2.5-flash",
        "temperature":        0.3,
        "memories_importance": 1,      # importance_min des mémoires passées au Chroniqueur
        "system_prompt":      (
            "Tu es le Chroniqueur IA d'une campagne D&D. Ton but est de maintenir un résumé "
            "global à jour de l'histoire. Je vais te fournir l'ancien résumé de la campagne, "
            "le journal de quêtes actif, les mémoires clés du groupe, puis la transcription "
            "de la nouvelle session. Rédige un UNIQUE résumé mis à jour qui inclut l'essentiel "
            "de l'ancien résumé ET de façon fluide les nouveaux événements. Note si des objectifs "
            "de quête semblent avoir progressé ou été accomplis. Sois immersif, concis "
            "(pas de détails inutiles), et liste les objets majeurs trouvés."
        ),
    },
    "groupchat": {
        "max_round":            9999,
        "allow_repeat_speaker": False,
        "allow_skill_checks":   True,
    },
    "memories": {
        "compact_importance_min":    2,   # importance min pour le bloc compact injecté en permanence
        "contextual_tag_min_length": 4,   # longueur min d'un tag pour la détection contextuelle
    },
    "voice": {
        "enabled": True,
        "backend": "edge-tts",          # "edge-tts" (en ligne) | "piper" (local, hors-ligne)
        "volume":  100,                 # 0–100 : volume global de toutes les sorties audio
    },
    "piper": {
        "models_dir": "piper_models",   # dossier de cache des modèles .onnx
        "voices": {
            # Voix Piper par personnage (format : locale-nom-qualité, ex: fr_FR-upmc-medium)
            # Aucun modèle fr-CA officiel n'existe dans Piper — fr_FR est le meilleur choix local.
            # Pour l'accent québécois, utiliser le backend edge-tts (fr-CA-AntoineNeural).
            "Kaelen":  "fr_FR-upmc-medium",
            "Elara":   "fr_FR-siwis-medium",
            "Thorne":  "fr_FR-upmc-medium",
            "Lyra":    "fr_FR-siwis-medium",
            "default": "fr_FR-upmc-medium",
        },
        "pitch": {
            # Décalage de pitch en demi-tons par personnage. 0 = voix naturelle du modèle.
            # Valeurs typiques : +2 à +5 pour voix féminine plus aiguë, -2 à -4 pour voix grave.
            "Kaelen":  0.0,
            "Elara":   2.0,
            "Thorne": -2.0,
            "Lyra":    1.0,
            "default": 0.0,
        },
    },
    "ui": {
        "poll_geometry_ms":    2000,
        "stats_refresh_ms":    2000,
    },
    "ptt": {
        "hotkey": "F12",   # keysym Tk — ex: "F12", "space", "Insert", "grave"
    },
    "combat": {
        "model": "gemini-3.1-flash-lite-preview",   # LLM utilisé par TOUS les PJ en mode combat
    },
    "fallback_chain": [
        # Ordre de tentative quand le modèle principal (Gemini) est épuisé.
        # Modifiable depuis l'onglet Agents du panneau de configuration.
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemma-4-31b-it",
        "gemma-4-26b-a4b-it",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview",
        "groq/meta-llama/llama-4-scout-17b-16e-instruct",
        "openrouter/meta-llama/llama-3.3-70b-instruct:free",
        "openrouter/mistralai/mistral-small-3.1-24b-instruct:free",
        "openrouter/arcee-ai/trinity-large-preview:free",
    ],
    "campaign_name": "campagne",   # Nom du dossier de sauvegarde (campagne/<nom>/)
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Fusionne override dans base récursivement (ne supprime pas les clés de base)."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_app_config() -> dict:
    """Charge la config depuis app_config.json et fusionne avec les défauts."""
    with _lock:
        try:
            if os.path.exists(APP_CONFIG_FILE):
                with open(APP_CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                return _deep_merge(DEFAULTS, saved)
        except Exception as e:
            print(f"[AppConfig] Erreur chargement : {e}")
        return dict(DEFAULTS)


def save_app_config(cfg: dict):
    """Sauvegarde la config dans app_config.json (écriture atomique via fichier temporaire)."""
    with _lock:
        try:
            tmp = APP_CONFIG_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            os.replace(tmp, APP_CONFIG_FILE)  # atomique sur Linux et Windows
        except Exception as e:
            print(f"[AppConfig] Erreur sauvegarde : {e}")


def get_agent_config(char_name: str) -> dict:
    """Retourne la config LLM d'un agent joueur depuis APP_CONFIG."""
    return APP_CONFIG.get("agents", {}).get(char_name, DEFAULTS["agents"].get(char_name, {}))


def get_chronicler_config() -> dict:
    return APP_CONFIG.get("chronicler", DEFAULTS["chronicler"])


def get_groupchat_config() -> dict:
    return APP_CONFIG.get("groupchat", DEFAULTS["groupchat"])


def get_memories_config() -> dict:
    return APP_CONFIG.get("memories", DEFAULTS["memories"])


def get_combat_config() -> dict:
    return APP_CONFIG.get("combat", DEFAULTS["combat"])


def get_voice_config() -> dict:
    return APP_CONFIG.get("voice", DEFAULTS["voice"])

def get_piper_config() -> dict:
    return APP_CONFIG.get("piper", DEFAULTS["piper"])

def get_piper_pitch(char_name: str) -> float:
    """Retourne le pitch shift (demi-tons) configuré pour un personnage Piper."""
    pitch_cfg = APP_CONFIG.get("piper", {}).get("pitch", DEFAULTS["piper"]["pitch"])
    return float(pitch_cfg.get(char_name, pitch_cfg.get("default", 0.0)))

def get_ptt_config() -> dict:
    return APP_CONFIG.get("ptt", DEFAULTS["ptt"])


def get_fallback_chain() -> list[str]:
    """Retourne la chaîne de fallback LLM configurée (liste ordonnée de noms de modèles)."""
    return list(APP_CONFIG.get("fallback_chain", DEFAULTS["fallback_chain"]))


def get_campaign_name() -> str:
    """Retourne le nom de la campagne (utilisé pour le dossier de sauvegarde)."""
    name = APP_CONFIG.get("campaign_name", DEFAULTS["campaign_name"]).strip()
    return name or "campagne"


def get_known_models_with_ollama() -> list[str]:
    """
    Retourne KNOWN_MODELS enrichi des modèles actuellement installés sur Ollama.

    Interroge l'API Ollama locale et ajoute les modèles trouvés (préfixés "ollama/")
    s'ils ne sont pas déjà dans KNOWN_MODELS. Tombe en silence si Ollama n'est
    pas disponible — ne bloque jamais le démarrage de l'app.

    Utilisé par les dropdowns du panneau de config pour montrer les modèles réels.
    """
    try:
        from llm_config import list_ollama_models
        installed = list_ollama_models()
        extra = [f"ollama/{name}" for name in installed
                 if f"ollama/{name}" not in KNOWN_MODELS]
        return KNOWN_MODELS + extra
    except Exception:
        return KNOWN_MODELS


# ─── Singleton chargé au démarrage ────────────────────────────────────────────
APP_CONFIG: dict = load_app_config()


def reload_app_config():
    """Recharge le singleton depuis le fichier (après une sauvegarde UI)."""
    global APP_CONFIG
    APP_CONFIG = load_app_config()