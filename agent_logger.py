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


# ── API publique ───────────────────────────────────────────────────────────────

def log_llm_start(name: str, prompt_preview: str = "", context: str = ""):
    """
    Appelé juste avant client.create() pour un agent joueur.

    name           : nom du personnage ("Kaelen", "Elara"…)
    prompt_preview : extrait du prompt (optionnel, 80 premiers chars)
    context        : label du contexte ("groupchat", "msg_privé", "vote", "image"…)
    """
    _llm_starts[name] = time.perf_counter()
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
    """
    Appelé quand play_voice() retourne (lecture terminée ou échouée).
    """
    t0 = _tts_starts.pop(name, None)
    elapsed = time.perf_counter() - t0 if t0 else 0.0
    cc = _char_color(name)
    icon = "✔️ " if success else "⚠️ "
    label = "lecture terminée" if success else "échec lecture"
    _print(
        f"{_COL_TIME}{_now()}{_RESET}  "
        f"{cc}{icon}{_BOLD}{name}{_RESET}{cc} — {label} "
        f"{_COL_TIME}({_fmt_ms(elapsed)}){_RESET}"
    )
