import os
import subprocess
import tempfile
import shutil
import atexit
import threading
import asyncio
import time
import re
import queue as _queue
from dotenv import load_dotenv

load_dotenv()

# ─── Logger TTS avec timestamps ──────────────────────────────────────────────

_VI_LOG_LOCK = threading.Lock()

def _ts() -> str:
    t  = time.time()
    ms = int((t % 1) * 1000)
    return time.strftime("%H:%M:%S", time.localtime(t)) + f".{ms:03d}"

_C = {
    "cyan":    "\033[96m",  "yellow":  "\033[93m",
    "green":   "\033[92m",  "red":     "\033[91m",
    "grey":    "\033[90m",  "reset":   "\033[0m",
}

def _log(tag: str, msg: str, color: str = ""):
    col   = _C.get(color, "")
    reset = _C["reset"] if col else ""
    tid   = threading.current_thread().name
    with _VI_LOG_LOCK:
        print(f"{_C['grey']}{_ts()}{_C['reset']}  {col}[TTS/{tag}]{reset}  {msg}"
              f"  {_C['grey']}({tid}){_C['reset']}", flush=True)

def _ms(t0: float) -> str:
    return f"{(time.perf_counter() - t0)*1000:.0f}ms"

VOICE_MAPPING = {
    "Kaelen": "fr-FR-HenriNeural",
    "Elara":  "fr-FR-DeniseNeural",
    "Thorne": "fr-CH-FabriceNeural",
    "Lyra":   "fr-FR-EloiseNeural",
    "default":"fr-FR-HenriNeural",
}

SPEED_MAPPING = {
    "Kaelen":  "+20%",
    "Elara":   "+25%",
    "Thorne":  "+20%",
    "Lyra":    "+20%",
    "default": "+10%",
}

# --- Detection des backends ---
def _check_tool(name):
    return shutil.which(name) is not None

FFPLAY_AVAILABLE = _check_tool("ffplay")

EDGE_TTS_ASYNC = None   # None = pas encore vérifié, True/False après premier appel
EDGE_TTS_CLI = _check_tool("edge-tts")

def _check_edge_tts_async() -> bool:
    """Vérifie edge_tts lors du premier appel TTS, pas au chargement du module.
    Importer edge_tts au niveau module fait démarrer aiohttp/selectors sur un
    thread C avant que Tk ait fini de construire ses widgets → segfault Xlib."""
    global EDGE_TTS_ASYNC
    if EDGE_TTS_ASYNC is not None:
        return EDGE_TTS_ASYNC
    try:
        import edge_tts as _  # noqa: F401
        EDGE_TTS_ASYNC = True
    except ImportError:
        EDGE_TTS_ASYNC = False
    return EDGE_TTS_ASYNC

if not EDGE_TTS_CLI:
    print("[TTS] edge-tts CLI non trouvé. pip install edge-tts ou vérifier PATH")
if not FFPLAY_AVAILABLE:
    print("[TTS] ffplay manquant. sudo apt install ffmpeg")

print(f"[TTS] Prêt (edge_tts async détecté à la première utilisation, CLI={'oui' if EDGE_TTS_CLI else 'non'})")

# --- Registre ffplay ---
_active_processes = []
_proc_lock = threading.Lock()

def _kill_all_audio():
    with _proc_lock:
        for proc in list(_active_processes):
            if proc.poll() is None:
                try:
                    proc.terminate(); proc.wait(timeout=2)
                except Exception:
                    try: proc.kill()
                    except Exception: pass
        _active_processes.clear()

atexit.register(_kill_all_audio)

def stop_audio():
    _kill_all_audio()

# --- Nettoyage texte ---
_MIN_ALPHANUM = 4
_JUNK_PATTERNS = re.compile(
    r'\*.*?\*|_{2,}|\[.*?\]|#{1,6}\s|`[^`]*`|<[^>]+>|\s{2,}',
    re.DOTALL
)

def _clean_for_tts(text):
    cleaned = _JUNK_PATTERNS.sub(' ', text)
    cleaned = re.sub(r'\[SILENCE\]|\[RÉSULTAT SYSTÈME\]', '', cleaned, flags=re.IGNORECASE)
    cleaned = ' '.join(cleaned.split())
    if len(re.findall(r'[a-zA-ZÀ-ÿ0-9]', cleaned)) < _MIN_ALPHANUM:
        return None
    return cleaned[:4000]

def _normalize_rate(rate):
    m = re.match(r'^([+-]?\d+)%$', rate.strip())
    if not m: return "+0%"
    return f"{max(-50, min(100, int(m.group(1)))):+d}%"

# --- Split en phrases ---
_SENTENCE_SPLIT = re.compile(
    r'(?<=[.!?»])\s+|(?<=[,;:])\s+(?=[A-ZÀÂÉÈÊËÎÏÔÙÛÜŒÆ])'
)
_MAX_CHUNK_CHARS = 250

def _split_chunks(text):
    parts = _SENTENCE_SPLIT.split(text)
    chunks, current = [], ""
    for part in parts:
        part = part.strip()
        if not part: continue
        if len(current) + len(part) + 1 <= _MAX_CHUNK_CHARS:
            current = (current + " " + part).strip() if current else part
        else:
            if current: chunks.append(current)
            current = part
    if current: chunks.append(current)
    return chunks or [text]

# --- Backend async ---
async def _generate_async(voice_id, text, rate, out_path):
    try:
        import edge_tts as _edge_tts_module  # lazy : pas de C-thread au chargement du module
        communicate = _edge_tts_module.Communicate(text, voice_id, rate=rate)
        await asyncio.wait_for(communicate.save(out_path), timeout=8.0)
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except asyncio.TimeoutError:
        _log("edge-async", f"\u2717 timeout : {text[:50]}", "red")
        return False
    except Exception as e:
        _log("edge-async", f"\u2717 erreur : {e}", "red")
        return False

# --- Backend async : boucle asyncio persistante sur thread dédié ---
#
# RÈGLE CRITIQUE : la boucle NE DOIT PAS démarrer au chargement du module.
# Si elle démarre trop tôt, selectors.py (epoll) et aiohttp/yarl initialisent
# leurs extensions C pendant que Tk construit encore ses widgets → race Xlib
# → segfault dans setup_ui.
#
# Solution : démarrage PARESSEUX à la première vraie demande TTS, qui
# intervient toujours bien après la fin de setup_ui.
_TTS_LOOP: asyncio.AbstractEventLoop | None = None
_TTS_THREAD: threading.Thread | None = None
_TTS_LOOP_LOCK = threading.Lock()

def _ensure_tts_loop() -> asyncio.AbstractEventLoop:
    """Démarre la boucle TTS une seule fois, à la première demande audio."""
    global _TTS_LOOP, _TTS_THREAD
    if _TTS_LOOP is not None and _TTS_LOOP.is_running():
        return _TTS_LOOP
    with _TTS_LOOP_LOCK:
        # Double-check après acquisition du verrou
        if _TTS_LOOP is not None and _TTS_LOOP.is_running():
            return _TTS_LOOP
        loop = asyncio.new_event_loop()
        t = threading.Thread(
            target=loop.run_forever,
            daemon=True,
            name="tts-event-loop",
        )
        t.start()
        # Attendre que la boucle soit réellement en marche
        for _ in range(50):
            if loop.is_running():
                break
            import time as _time; _time.sleep(0.01)
        _TTS_LOOP = loop
        _TTS_THREAD = t
    return _TTS_LOOP

def _generate_chunk_async(voice_id, text, rate):
    """Génère un chunk audio via edge_tts sur la boucle TTS persistante."""
    import edge_tts as _  # noqa — force l'import maintenant qu'on est hors de setup_ui
    loop = _ensure_tts_loop()
    try:
        fd, tmp = tempfile.mkstemp(suffix=".mp3", prefix="tts_")
        os.close(fd)
    except Exception:
        return None
    t0 = time.perf_counter()
    preview = text[:50].replace("\n", " ")
    future = asyncio.run_coroutine_threadsafe(
        _generate_async(voice_id, text, rate, tmp), loop
    )
    try:
        ok = future.result(timeout=10)
    except Exception as e:
        _log("edge-async", f"✗ future : {e}  {_ms(t0)}", "red")
        ok = False
    if not ok or not os.path.getsize(tmp):
        try: os.remove(tmp)
        except OSError: pass
        _log("edge-async", f"✗ fichier vide  {_ms(t0)}", "red")
        return None
    sz = os.path.getsize(tmp)
    return tmp

# --- Backend CLI ---
def _generate_chunk_cli(voice_id, text, rate):
    t0      = time.perf_counter()
    preview = text[:50].replace("\n", " ")
    try:
        fd, tmp = tempfile.mkstemp(suffix=".mp3", prefix="tts_")
        os.close(fd)
    except Exception:
        return None
    for r in [rate, "+0%"]:
        try:
            result = subprocess.run(
                ["edge-tts", "--voice", voice_id, "--text", text,
                 f"--rate={r}", "--write-media", tmp],
                timeout=8, capture_output=True, text=True,
            )
            if result.returncode == 0 and os.path.getsize(tmp) > 0:
                sz = os.path.getsize(tmp)
                return tmp
            if "NoAudioReceived" not in result.stderr:
                _log("edge-cli", f"✗ returncode={result.returncode}  {_ms(t0)}", "red")
                break
        except subprocess.TimeoutExpired:
            _log("edge-cli", f"✗ timeout 8s  {_ms(t0)}", "red")
            break
        except FileNotFoundError:
            _log("edge-cli", "✗ edge-tts CLI introuvable", "red")
            break
        open(tmp, 'wb').close()
    try: os.remove(tmp)
    except OSError: pass
    return None

# --- Durée WAV (pour timeout calculé) ---
def _wav_duration_s(path: str) -> float:
    """Retourne la durée en secondes d'un fichier WAV, ou 0 si illisible."""
    import wave as _wave
    try:
        with _wave.open(path, "rb") as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return 0.0


# --- Lecture audio (aplay pour WAV, ffplay pour MP3) ---
def _play_file(tmp_path):
    """
    Joue un fichier audio. Silencieux sauf erreurs.
    WAV → aplay (PulseAudio), MP3/autre → ffplay.
    """
    sz       = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
    is_wav   = tmp_path.lower().endswith(".wav")
    player   = "aplay" if (is_wav and shutil.which("aplay")) else "ffplay"

    proc = None
    try:
        if not _AUDIO_PAUSE_EVENT.is_set():
            return False

        if is_wav:
            dur_s = _wav_duration_s(tmp_path)
        else:
            dur_s = sz / 16_000
        play_timeout = max(dur_s * 1.5 + 5.0, 8.0)

        if player == "aplay":
            cmd = ["aplay", "-q", tmp_path]
        else:
            cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", tmp_path]

        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with _proc_lock:
            _active_processes.append(proc)
        try:
            proc.wait(timeout=play_timeout)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait()
            _log(player, f"✗ timeout {play_timeout:.0f}s (durée estimée {dur_s:.1f}s)", "red")
            return False
        finally:
            with _proc_lock:
                if proc in _active_processes:
                    _active_processes.remove(proc)

        ok = proc.returncode in (0, -15)
        if not ok:
            _log(player, f"✗ rc={proc.returncode}", "red")
        return ok

    except Exception as e:
        _log(player, f"✗ exception : {e}", "red")
        return False
    finally:
        try: os.remove(tmp_path)
        except OSError: pass


# --- Prefetch : génère tous les chunks en avance, retourne la liste de fichiers ---

def _prefetch_voice_edgetts(text: str, character_name: str) -> list[str]:
    """
    [Interne] Génère tous les chunks audio edge-tts en avance (sans les jouer).
    Retourne une liste ORDONNÉE de chemins mp3 prêts à lire.
    """
    from concurrent.futures import ThreadPoolExecutor, wait as _fw, ALL_COMPLETED

    t0 = time.perf_counter()
    use_async = _check_edge_tts_async()
    if not FFPLAY_AVAILABLE or (not use_async and not EDGE_TTS_CLI):
        _log("prefetch", "✗ aucun backend disponible", "red")
        return []

    voice_id    = VOICE_MAPPING.get(character_name, VOICE_MAPPING["default"])
    voice_speed = _normalize_rate(SPEED_MAPPING.get(character_name, SPEED_MAPPING["default"]))
    clean_text  = _clean_for_tts(text)
    if clean_text is None:
        return []

    chunks    = _split_chunks(clean_text)
    _generate = _generate_chunk_async if use_async else _generate_chunk_cli
    n_workers = min(len(chunks), 4)

    if n_workers <= 1 or len(chunks) == 1:
        files = [f for f in (_generate(voice_id, ch, voice_speed) for ch in chunks) if f]
    else:
        results: list[str | None] = [None] * len(chunks)
        def _gen(idx, chunk):
            results[idx] = _generate(voice_id, chunk, voice_speed)
        with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="edgetss-pre") as ex:
            _fw([ex.submit(_gen, i, ch) for i, ch in enumerate(chunks)], return_when=ALL_COMPLETED)
        files = [f for f in results if f]

    elapsed = (time.perf_counter() - t0) * 1000
    return files


def play_prefetched(files: list[str]) -> bool:
    """Joue une liste de fichiers mp3 pré-générés par prefetch_voice()."""
    any_played = False
    for f in files:
        if f and os.path.exists(f):
            if _play_file(f):
                any_played = True
    return any_played


# --- API publique ---
def record_audio_and_transcribe():
    # Import lazy : PyAudio/PortAudio initialisent des threads C natifs qui
    # segfaultent sur Linux si chargés avant que le runtime gRPC soit stable.
    import speech_recognition as sr  # noqa: PLC0415
    r = sr.Recognizer()
    with sr.Microphone() as source:
        r.adjust_for_ambient_noise(source, duration=0.5)
        try:
            audio = r.listen(source, timeout=5, phrase_time_limit=30)
        except sr.WaitTimeoutError:
            return "[Le MJ observe en silence.]"
    try:
        return r.recognize_google(audio, language="fr-FR")
    except sr.UnknownValueError:
        return "[Erreur micro : Audio non compris.]"
    except sr.RequestError:
        return "[Erreur réseau Google Speech.]"


# ─── Push-to-Talk (PTT) ──────────────────────────────────────────────────────
#
# Contrairement à record_audio_and_transcribe() qui utilise la détection de
# silence (VAD) de SpeechRecognition, le PTT enregistre tant que le bouton
# est maintenu et s'arrête EXACTEMENT au relâchement — aucune coupure prématurée.
#
# API :
#   ptt_start()                → ButtonPress  : démarre l'enregistrement
#   ptt_stop_and_transcribe()  → ButtonRelease : arrête + transcrit (bloquant)

_PTT_RATE     = 16000   # Hz — taux d'échantillonnage (Google Speech requiert ≥8kHz)
_PTT_CHANNELS = 1       # mono
_PTT_CHUNK    = 1024    # frames par buffer

_ptt_frames:        list                    = []
_ptt_stop_event:    threading.Event         = threading.Event()
_ptt_record_thread: threading.Thread | None = None
_ptt_transcribe_lock: threading.Lock        = threading.Lock()  # un seul transcribe à la fois


def ptt_start() -> None:
    """Démarre l'enregistrement PTT en arrière-plan.
    Doit être appelé depuis le thread Tk sur ButtonPress.
    Idempotent : un 2ᵉ appel sans ptt_stop_and_transcribe() entre les deux est ignoré."""
    global _ptt_frames, _ptt_record_thread

    if _ptt_record_thread is not None and _ptt_record_thread.is_alive():
        return  # déjà en cours

    _ptt_stop_event.clear()
    _ptt_frames = []

    def _record():
        try:
            import pyaudio  # lazy — même raison que speech_recognition
            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=_PTT_CHANNELS,
                rate=_PTT_RATE,
                input=True,
                frames_per_buffer=_PTT_CHUNK,
            )
            _log("ptt", "● Enregistrement démarré", "cyan")
            while not _ptt_stop_event.is_set():
                try:
                    data = stream.read(_PTT_CHUNK, exception_on_overflow=False)
                    _ptt_frames.append(data)
                except Exception:
                    break
            stream.stop_stream()
            stream.close()
            pa.terminate()
            _log("ptt", f"■ Enregistrement arrêté ({len(_ptt_frames)} chunks)", "yellow")
        except Exception as e:
            _log("ptt", f"✗ Erreur PyAudio : {e}", "red")

    _ptt_record_thread = threading.Thread(target=_record, daemon=True, name="ptt-record")
    _ptt_record_thread.start()


def ptt_stop_and_transcribe() -> str:
    """Arrête l'enregistrement PTT et retourne la transcription.
    BLOQUANT — doit être appelé dans un thread daemon (pas le thread Tk).
    Protégé par _ptt_transcribe_lock — un seul appel actif à la fois."""
    global _ptt_record_thread

    # Un seul transcribe à la fois — les appels concurrents (key-repeat résiduel)
    # repartent immédiatement avec un message neutre.
    if not _ptt_transcribe_lock.acquire(blocking=False):
        _log("ptt", "✗ Transcription déjà en cours — ignoré", "grey")
        return "[Transcription déjà en cours.]"

    tmp_path = ""
    try:
        # Signaler l'arrêt et attendre la fin du thread d'enregistrement
        _ptt_stop_event.set()
        if _ptt_record_thread is not None:
            _ptt_record_thread.join(timeout=1.0)
        _ptt_record_thread = None

        frames = list(_ptt_frames)
        if not frames:
            _log("ptt", "✗ Aucun frame capté", "red")
            return "[Aucun audio capté.]"

        import wave
        import tempfile
        import speech_recognition as sr

        fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="ptt_")
        os.close(fd)

        with wave.open(tmp_path, "wb") as wf:
            wf.setnchannels(_PTT_CHANNELS)
            wf.setsampwidth(2)          # paInt16 → 2 octets/sample
            wf.setframerate(_PTT_RATE)
            wf.writeframes(b"".join(frames))

        duration_s = len(frames) * _PTT_CHUNK / _PTT_RATE
        _log("ptt", f"  WAV écrit : {duration_s:.1f}s  ({len(frames)} chunks)", "grey")

        recognizer = sr.Recognizer()
        with sr.AudioFile(tmp_path) as source:
            audio = recognizer.record(source)

        try:
            result = recognizer.recognize_google(audio, language="fr-FR")
            _log("ptt", f"✓ Transcription : « {result[:80]} »", "green")
            return result
        except sr.UnknownValueError:
            _log("ptt", "✗ Audio non compris", "yellow")
            return "[Audio non compris.]"
        except sr.RequestError as e:
            _log("ptt", f"✗ Erreur réseau Google Speech : {e}", "red")
            return "[Erreur réseau Google Speech.]"

    except Exception as e:
        _log("ptt", f"✗ Exception transcription : {e}", "red")
        return "[Erreur interne PTT.]"
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        _ptt_transcribe_lock.release()


def _play_voice_edgetts(text, character_name):
    """
    [Interne] edge-tts async (si dispo) ou CLI.
    Pipeline : génération chunk N+1 en parallèle de la lecture chunk N.
    """
    t0 = time.perf_counter()
    if not FFPLAY_AVAILABLE:
        _log("play_edge", "✗ ffplay manquant", "red"); return False
    use_async = _check_edge_tts_async()
    if not use_async and not EDGE_TTS_CLI:
        _log("play_edge", "✗ aucun backend", "red"); return False

    voice_id    = VOICE_MAPPING.get(character_name, VOICE_MAPPING["default"])
    voice_speed = _normalize_rate(SPEED_MAPPING.get(character_name, SPEED_MAPPING["default"]))

    clean_text = _clean_for_tts(text)
    if clean_text is None:
        return False

    chunks    = _split_chunks(clean_text)
    _generate = _generate_chunk_async if use_async else _generate_chunk_cli
    n_workers = min(len(chunks), 4)

    file_queue = _queue.Queue(maxsize=3)

    def _generate_all():
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="edgetss-gen") as ex:
            futures = [ex.submit(_generate, voice_id, chunk, voice_speed) for chunk in chunks]
            for i, fut in enumerate(futures):
                try:
                    file_queue.put(fut.result(timeout=12))
                except Exception as _e:
                    _log("gen_edge", f"  ✗ chunk {i+1} : {_e}", "red")
                    file_queue.put(None)
        file_queue.put("__DONE__")

    gen_thread = threading.Thread(target=_generate_all, daemon=True)
    gen_thread.start()

    any_played = False
    chunk_idx  = 0
    while True:
        item = file_queue.get()
        if item == "__DONE__":
            break
        chunk_idx += 1
        if item is not None and _play_file(item):
            any_played = True

    gen_thread.join(timeout=15)
    return any_played


# ─── Pause / Reprise audio ───────────────────────────────────────────────────
#
# _AUDIO_PAUSE_EVENT.set()   = actif (lecture normale)
# _AUDIO_PAUSE_EVENT.clear() = en pause (toute demande play_voice() est ignorée)

_AUDIO_PAUSE_EVENT = threading.Event()
_AUDIO_PAUSE_EVENT.set()   # actif par défaut au démarrage


def pause_audio():
    """Stoppe immédiatement la lecture en cours ET bloque les appels suivants."""
    _kill_all_audio()          # tue ffplay immédiatement
    _AUDIO_PAUSE_EVENT.clear() # bloque play_voice / prefetch futurs


def resume_audio():
    """Débloque la lecture audio après une pause."""
    _AUDIO_PAUSE_EVENT.set()


# ─── API publique routée (edge-tts ↔ Piper local) ────────────────────────────
#
# Ces trois fonctions remplacent les anciennes `play_voice` / `prefetch_voice` /
# `play_prefetched`. Le backend est choisi dynamiquement depuis APP_CONFIG.
#
# backend = "edge-tts"  → Microsoft Neural (en ligne, fr-CA disponible)
# backend = "piper"     → Piper TTS         (hors-ligne, fr_FR)

def _get_backend() -> str:
    """Lit le backend TTS actif depuis APP_CONFIG (rechargé à chaque appel)."""
    try:
        from app_config import APP_CONFIG
        return APP_CONFIG.get("voice", {}).get("backend", "edge-tts")
    except Exception:
        return "edge-tts"


def _get_piper_voice_id(character_name: str) -> tuple[str, str]:
    """Retourne (voice_id, models_dir) Piper pour un personnage."""
    try:
        from app_config import APP_CONFIG
        pcfg      = APP_CONFIG.get("piper", {})
        voices    = pcfg.get("voices", {})
        voice_id  = voices.get(character_name, voices.get("default", "fr_FR-upmc-medium"))
        models_dir = pcfg.get("models_dir", "piper_models")
        return voice_id, models_dir
    except Exception:
        return "fr_FR-upmc-medium", "piper_models"


def play_voice(text: str, character_name: str) -> bool:
    """
    Point d'entrée principal TTS.
    Route vers edge-tts (en ligne) ou Piper (local) selon APP_CONFIG['voice']['backend'].
    Retourne silencieusement False si la session est en pause.
    """
    if not _AUDIO_PAUSE_EVENT.is_set():
        return False   # session en pause — ignorer silencieusement
    if _get_backend() == "piper":
        voice_id, models_dir = _get_piper_voice_id(character_name)
        from piper_tts import play_piper_voice
        from app_config import get_piper_pitch
        return play_piper_voice(text, character_name, voice_id, models_dir,
                                pitch_semitones=get_piper_pitch(character_name))
    return _play_voice_edgetts(text, character_name)


def prefetch_voice(text: str, character_name: str) -> list[str]:
    """
    Pré-génère les chunks audio en avance sans les jouer.
    Route vers edge-tts ou Piper selon la config.
    Retourne [] si la session est en pause (rien à pré-générer).
    """
    if not _AUDIO_PAUSE_EVENT.is_set():
        return []
    if _get_backend() == "piper":
        voice_id, models_dir = _get_piper_voice_id(character_name)
        from piper_tts import prefetch_piper_voice
        from app_config import get_piper_pitch
        return prefetch_piper_voice(text, character_name, voice_id, models_dir,
                                    pitch_semitones=get_piper_pitch(character_name))
    return _prefetch_voice_edgetts(text, character_name)


def play_prefetched(files: list[str]) -> bool:
    """
    Joue une liste de fichiers pré-générés (mp3 edge-tts ou wav Piper).
    ffplay gère les deux formats transparentement.
    """
    any_played = False
    for f in files:
        if f and os.path.exists(f):
            if _play_file(f):
                any_played = True
    return any_played