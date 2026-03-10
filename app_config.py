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
    # Gemini
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
    # Groq
    "groq/llama-4-scout-17b-16e-instruct",
    "groq/llama-3.3-70b-versatile",
    "groq/llama-3.1-70b-versatile",
    "groq/mixtral-8x7b-32768",
    # OpenRouter gratuits
    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    "openrouter/google/gemma-3-27b-it:free",
    "openrouter/mistralai/mistral-7b-instruct:free",
]

# ─── Valeurs par défaut ────────────────────────────────────────────────────────
DEFAULTS: dict = {
    "agents": {
        "Kaelen": {
            "model":       "gemini-2.5-pro",
            "temperature": 0.7,
        },
        "Elara": {
            "model":       "gemini-2.5-pro",
            "temperature": 0.7,
        },
        "Thorne": {
            "model":       "groq/llama-4-scout-17b-16e-instruct",
            "temperature": 0.8,
        },
        "Lyra": {
            "model":       "gemini-2.5-pro",
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
        "max_round":            100,
        "allow_repeat_speaker": False,
    },
    "memories": {
        "compact_importance_min":    2,   # importance min pour le bloc compact injecté en permanence
        "contextual_tag_min_length": 4,   # longueur min d'un tag pour la détection contextuelle
    },
    "voice": {
        "enabled": True,
    },
    "ui": {
        "poll_geometry_ms":    2000,
        "stats_refresh_ms":    2000,
    },
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
    """Sauvegarde la config dans app_config.json."""
    with _lock:
        try:
            with open(APP_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
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


def get_voice_config() -> dict:
    return APP_CONFIG.get("voice", DEFAULTS["voice"])


# ─── Singleton chargé au démarrage ────────────────────────────────────────────
APP_CONFIG: dict = load_app_config()


def reload_app_config():
    """Recharge le singleton depuis le fichier (après une sauvegarde UI)."""
    global APP_CONFIG
    APP_CONFIG = load_app_config()
