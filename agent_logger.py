"""
agent_logger.py — Logs des agents joueurs en temps réel.

Affiche dans le terminal (stdout) :
  - ⏳ quand un agent commence à réfléchir (prompt envoyé au LLM)
  - ✅ quand la réponse revient, avec le temps écoulé
  - 🔊 quand un script est envoyé au lecteur TTS
  - ✔️  quand la lecture TTS se termine, avec la durée

Usage :
    from agent_logger import log_llm_start, log_llm_end, log_tts_start, log_tts_end
"""

import time
import threading

# ── Couleurs ANSI ─────────────────────────────────────────────────────────────
_RESET   = "\033[0m"
_BOLD    = "\033[1m"
_DIM     = "\033[2m"

_COLORS = {
    "Kaelen": "\033[91m",   # rouge clair
    "Elara":  "\033[94m",   # bleu clair
    "Thorne": "\033[95m",   # violet clair
    "Lyra":   "\033[92m",   # vert clair
}
_COL_LLM   = "\033[93m"    # jaune  — réflexion LLM
_COL_TTS   = "\033[96m"    # cyan   — lecture TTS
_COL_TIME  = "\033[90m"    # gris   — durées
_COL_ERR   = "\033[31m"    # rouge  — erreurs

_lock = threading.Lock()

# ── Horodatages en cours ───────────────────────────────────────────────────────
# clé → time.perf_counter() du début
_llm_starts: dict[str, float] = {}
_tts_starts: dict[str, float] = {}

# ── Référence optionnelle vers face_windows (injectée par DnDApp) ─────────────
# Permet d'activer la bulle de pensée pour les appels LLM hors groupchat
# (messages privés, votes, images). Setter : set_face_windows_ref(dict).
_face_windows_ref: dict = {}

# ── Modèles configurés par agent (injecté au démarrage depuis engine_agents) ──
_agent_configured_models: dict[str, str] = {}


def set_agent_configured_model(name: str, model: str):
    """Enregistre le modèle configuré pour un agent. Appelé une fois à l'init."""
    _agent_configured_models[name] = model


def _char_color(name: str) -> str:
    return _COLORS.get(name, "\033[97m")


def _now() -> str:
    return time.strftime("%H:%M:%S")


def _fmt_ms(seconds: float) -> str:
    if seconds < 1.0:
        return f"{seconds*1000:.0f}ms"
    return f"{seconds:.2f}s"


def _print(line: str):
    with _lock:
        print(line, flush=True)


def set_face_windows_ref(face_windows: dict):
    """Injecte la référence face_windows depuis DnDApp.
    Appelé une seule fois après create_character_faces().
    """
    global _face_windows_ref
    _face_windows_ref = face_windows


def _set_thinking(name: str, thinking: bool):
    """Active/désactive la bulle de pensée sur l'avatar de name (si ouvert)."""
    face = _face_windows_ref.get(name)
    if face:
        try:
            face.set_thinking(thinking)
        except Exception:
            pass


# ── API publique ───────────────────────────────────────────────────────────────

def log_llm_start(name: str, prompt_preview: str = "", context: str = ""):
    """
    Appelé juste avant client.create() pour un agent joueur.

    name           : nom du personnage ("Kaelen", "Elara"…)
    prompt_preview : extrait du prompt (optionnel, 80 premiers chars)
    context        : label du contexte ("groupchat", "msg_privé", "vote", "image"…)
    """
    _llm_starts[name] = time.perf_counter()
    _set_thinking(name, True)
    cc = _char_color(name)
    ctx = f" [{context}]" if context else ""
    prev = f"  {_DIM}↳ {prompt_preview[:100].strip()}…{_RESET}" if prompt_preview else ""
    _print(
        f"{_COL_TIME}{_now()}{_RESET}  "
        f"{_COL_LLM}⏳ {_BOLD}{name}{_RESET}{_COL_LLM}{ctx} — réflexion en cours…{_RESET}"
        + (f"\n{prev}" if prev else "")
    )


def log_llm_end(name: str, response_preview: str = "", error: str = ""):
    """
    Appelé juste après client.create() (succès ou échec).
    """
    t0 = _llm_starts.pop(name, None)
    elapsed = time.perf_counter() - t0 if t0 else 0.0
    _set_thinking(name, False)
    cc = _char_color(name)

    if error:
        _print(
            f"{_COL_TIME}{_now()}{_RESET}  "
            f"{_COL_ERR}❌ {_BOLD}{name}{_RESET}{_COL_ERR} — erreur LLM "
            f"({_fmt_ms(elapsed)}) : {error[:120]}{_RESET}"
        )
    else:
        prev = f"  {_DIM}↳ {response_preview[:100].strip()}…{_RESET}" if response_preview else ""
        _print(
            f"{_COL_TIME}{_now()}{_RESET}  "
            f"{cc}✅ {_BOLD}{name}{_RESET}{cc} — réponse reçue "
            f"{_COL_TIME}({_fmt_ms(elapsed)}){_RESET}"
            + (f"\n{prev}" if prev else "")
        )
        # Afficher le solde OpenRouter si cet agent utilise ce fournisseur
        if _agent_configured_models.get(name, "").startswith("openrouter/"):
            log_openrouter_status()


def log_llm_model_used(name: str, model: str, configured_model: str = ""):
    """
    Appelé après chaque réponse autogen pour afficher quel modèle a réellement répondu.
    Utile pour détecter les basculements silencieux sur un modèle de secours.

    name             : nom du personnage
    model            : modèle qui a effectivement répondu (extrait de la réponse API)
    configured_model : modèle configuré dans app_config (pour comparaison)
    """
    cc = _char_color(name)

    def _canonical(m):
        # Retire les prefixes fournisseur : groq/ et openrouter/
        for prefix in ("groq/", "openrouter/"):
            if m.startswith(prefix):
                return m[len(prefix):]
        return m

    was_fallback = (
        configured_model
        and _canonical(model) != _canonical(configured_model)
    )

    if was_fallback:
        _print(
            f"{_COL_TIME}{_now()}{_RESET}  "
            f"{cc}🔀 {_BOLD}{name}{_RESET}{cc} — "
            f"modèle configuré : {_DIM}{configured_model}{_RESET}{cc}  →  "
            f"modèle ayant répondu : {_BOLD}{model}{_RESET}"
            f"{_COL_ERR}  ⚠ FALLBACK DÉTECTÉ{_RESET}"
        )
    else:
        _print(
            f"{_COL_TIME}{_now()}{_RESET}  "
            f"{cc}🤖 {_BOLD}{name}{_RESET}{cc} — répondu par : {model}{_RESET}"
        )

    # Afficher le solde OpenRouter si le modèle configuré ou ayant répondu vient d'OpenRouter
    if configured_model.startswith("openrouter/") or model.startswith("openrouter/"):
        log_openrouter_status()


def log_tts_start(name: str, text_preview: str = ""):
    """
    Appelé quand le script est mis dans audio_queue (avant génération TTS).
    """
    _tts_starts[name] = time.perf_counter()
    cc = _char_color(name)
    prev = f"  {_DIM}↳ « {text_preview[:100].strip()}… »{_RESET}" if text_preview else ""
    _print(
        f"{_COL_TIME}{_now()}{_RESET}  "
        f"{_COL_TTS}🔊 {_BOLD}{name}{_RESET}{_COL_TTS} — envoyé au lecteur TTS{_RESET}"
        + (f"\n{prev}" if prev else "")
    )


def log_tts_end(name: str, success: bool = True):
    """Nettoyage du timer TTS — log supprimé (appelé N fois par phrase, cause 0ms)."""
    _tts_starts.pop(name, None)
# ─── OpenRouter : affichage du solde après chaque réponse ────────────────────

_COL_CREDITS = "\033[96m"   # cyan — infos crédits

# Cache : timestamp du dernier affichage pour éviter le spam
# (affiché au max une fois toutes les 60 s)
_openrouter_last_check: float = 0.0
_OPENROUTER_CHECK_INTERVAL = 60.0   # secondes entre deux affichages


def log_openrouter_status():
    """
    Interroge l'endpoint /api/v1/key d'OpenRouter et affiche le solde
    et les limites dans le terminal.
    Appelé en arrière-plan après chaque réponse d'un agent OpenRouter.
    Throttlé à 1 affichage / 60 s pour ne pas spammer.
    """
    global _openrouter_last_check
    now = time.perf_counter()
    if now - _openrouter_last_check < _OPENROUTER_CHECK_INTERVAL:
        return
    _openrouter_last_check = now

    def _fetch():
        try:
            from llm_config import fetch_openrouter_key_status, format_openrouter_status
            data = fetch_openrouter_key_status()
            if not data:
                return
            status_line = format_openrouter_status(data)
            if status_line:
                _print(
                    f"{_COL_TIME}{_now()}{_RESET}  "
                    f"{_COL_CREDITS}💳 OpenRouter  {status_line}{_RESET}"
                )
        except Exception as e:
            _print(f"{_COL_TIME}{_now()}{_RESET}  {_COL_ERR}[OpenRouter status] erreur : {e}{_RESET}")

    threading.Thread(target=_fetch, daemon=True, name="or-status").start()