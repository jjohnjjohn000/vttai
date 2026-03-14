import os
import subprocess
import tempfile
import shutil
import atexit
import threading
import asyncio
import re
import queue as _queue
from dotenv import load_dotenv

load_dotenv()

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
        print(f"[TTS] Timeout async: {text[:50]}")
        return False
    except Exception as e:
        print(f"[TTS] Erreur async: {e}")
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
    future = asyncio.run_coroutine_threadsafe(
        _generate_async(voice_id, text, rate, tmp), loop
    )
    try:
        ok = future.result(timeout=10)
    except Exception as e:
        print(f"[TTS] Erreur future: {e}")
        ok = False
    if not ok or not os.path.getsize(tmp):
        try: os.remove(tmp)
        except OSError: pass
        return None
    return tmp

# --- Backend CLI ---
def _generate_chunk_cli(voice_id, text, rate):
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
                return tmp
            if "NoAudioReceived" not in result.stderr:
                break
        except subprocess.TimeoutExpired:
            print(f"[TTS] Timeout CLI: {text[:50]}")
            break
        except FileNotFoundError:
            break
        open(tmp, 'wb').close()
    try: os.remove(tmp)
    except OSError: pass
    return None

# --- Lecture ffplay ---
def _play_file(tmp_path):
    proc = None
    try:
        proc = subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", tmp_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        with _proc_lock:
            _active_processes.append(proc)
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait(); return False
        finally:
            with _proc_lock:
                if proc in _active_processes:
                    _active_processes.remove(proc)
        return proc.returncode in (0, -15)
    except Exception:
        return False
    finally:
        try: os.remove(tmp_path)
        except OSError: pass

# --- Prefetch : génère tous les chunks en avance, retourne la liste de fichiers ---

def prefetch_voice(text: str, character_name: str) -> list[str]:
    """
    Génère tous les chunks audio en avance (sans les jouer).
    Retourne une liste de chemins de fichiers mp3 prêts à lire, dans l'ordre.
    Appelé depuis un thread de préfetch pendant que la voix précédente joue.
    """
    use_async = _check_edge_tts_async()
    if not FFPLAY_AVAILABLE or (not use_async and not EDGE_TTS_CLI):
        return []

    voice_id    = VOICE_MAPPING.get(character_name, VOICE_MAPPING["default"])
    voice_speed = _normalize_rate(SPEED_MAPPING.get(character_name, SPEED_MAPPING["default"]))
    clean_text  = _clean_for_tts(text)
    if clean_text is None:
        return []

    chunks    = _split_chunks(clean_text)
    _generate = _generate_chunk_async if use_async else _generate_chunk_cli
    files     = []
    for chunk in chunks:
        f = _generate(voice_id, chunk, voice_speed)
        if f:
            files.append(f)
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


def play_voice(text, character_name):
    """
    Génère et joue la voix via edge_tts async (si dispo) ou CLI.
    Pipeline : génération chunk N+1 en parallèle de la lecture chunk N.
    """
    if not FFPLAY_AVAILABLE:
        print(f"[TTS] ffplay manquant."); return False
    use_async = _check_edge_tts_async()
    if not use_async and not EDGE_TTS_CLI:
        print(f"[TTS] Aucun backend."); return False

    voice_id    = VOICE_MAPPING.get(character_name, VOICE_MAPPING["default"])
    voice_speed = _normalize_rate(SPEED_MAPPING.get(character_name, SPEED_MAPPING["default"]))

    clean_text = _clean_for_tts(text)
    if clean_text is None:
        return False

    chunks = _split_chunks(clean_text)
    _generate = _generate_chunk_async if use_async else _generate_chunk_cli

    # Pipeline : thread génère dans une queue bornée, on lit au fur et à mesure
    file_queue = _queue.Queue(maxsize=3)

    def _generate_all():
        for chunk in chunks:
            file_queue.put(_generate(voice_id, chunk, voice_speed))
        file_queue.put("__DONE__")

    gen_thread = threading.Thread(target=_generate_all, daemon=True)
    gen_thread.start()

    any_played = False
    while True:
        item = file_queue.get()
        if item == "__DONE__":
            break
        if item is not None and _play_file(item):
            any_played = True

    gen_thread.join(timeout=15)
    return any_played