"""
piper_tts.py — Backend TTS local (100 % hors-ligne) via piper-tts.

Installation une seule fois :
    pip install piper-tts

Modèles : téléchargés automatiquement depuis HuggingFace la première fois,
          puis mis en cache dans le dossier configuré (défaut : ./piper_models/).
          Comptez ~60–80 Mo par voix en qualité « medium ».

Voix françaises disponibles (fr_FR) :
  fr_FR-upmc-medium    — homme, naturel,  qualité correcte  (recommandé pour Thorne / Kaelen)
  fr_FR-mls-medium     — homme alternatif
  fr_FR-siwis-low      — femme, légère   (~25 Mo)
  fr_FR-siwis-medium   — femme, claire   (recommandé pour Elara / Lyra)
  fr_FR-gilles-low     — homme léger     (~15 Mo, moins naturel)

⚠ Il n'existe pas encore de modèle Piper fr-CA officiel.
  Pour un accent québécois *local*, fr_FR-upmc-medium reste la meilleure option
  gratuite offline disponible à ce jour. L'accent québécois natif est fourni par
  edge-tts (fr-CA-AntoineNeural) en mode en-ligne.

Usage direct :
    from piper_tts import play_piper_voice, prefetch_piper_voice
    files = prefetch_piper_voice("Bonjour, compagnons.", "Thorne",
                                  "fr_FR-upmc-medium", "piper_models")
    play_prefetched_piper(files)
"""

import os
import re
import threading
import tempfile
import time
import wave
import shutil
import queue as _queue
import urllib.request
from pathlib import Path

# ─── Logger TTS avec timestamps ──────────────────────────────────────────────

_LOG_LOCK = threading.Lock()

def _ts() -> str:
    """Retourne HH:MM:SS.mmm"""
    t  = time.time()
    ms = int((t % 1) * 1000)
    return time.strftime("%H:%M:%S", time.localtime(t)) + f".{ms:03d}"

_C = {
    "cyan":    "\033[96m",  "yellow":  "\033[93m",
    "green":   "\033[92m",  "red":     "\033[91m",
    "magenta": "\033[95m",  "grey":    "\033[90m",
    "reset":   "\033[0m",   "bold":    "\033[1m",
}

def _log(tag: str, msg: str, color: str = ""):
    """Ligne de log horodatée thread-safe."""
    col   = _C.get(color, "")
    reset = _C["reset"] if col else ""
    tid   = threading.current_thread().name
    with _LOG_LOCK:
        print(f"{_C['grey']}{_ts()}{_C['reset']}  {col}[Piper/{tag}]{reset}  {msg}"
              f"  {_C['grey']}({tid}){_C['reset']}", flush=True)

def _ms(t0: float) -> str:
    return f"{(time.perf_counter() - t0)*1000:.0f}ms"

# ─── Catalogue des voix françaises ───────────────────────────────────────────

KNOWN_PIPER_VOICES: list[str] = [
    "fr_FR-upmc-medium",
    "fr_FR-mls-medium",
    "fr_FR-siwis-low",
    "fr_FR-siwis-medium",
    "fr_FR-gilles-low",
]

_HF_BASE     = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"
_DEFAULT_DIR = "piper_models"

# ─── Mode debug ──────────────────────────────────────────────────────────────
# Mettre PIPER_DEBUG=1 dans l'environnement (ou dans .env) pour :
#   - Conserver les fichiers WAV après lecture (dans /tmp/, préfixe piper_DEBUG_)
#   - Afficher la sortie complète de ffplay (sans -loglevel quiet)
#   - Logger le chemin complet de chaque fichier généré
import os as _os
PIPER_DEBUG = _os.environ.get("PIPER_DEBUG", "0").strip() == "1"

# ─── Nettoyage texte ─────────────────────────────────────────────────────────

_MIN_ALPHANUM  = 4
_JUNK_RE       = re.compile(r'\*.*?\*|_{2,}|\[.*?\]|#{1,6}\s|`[^`]*`|<[^>]+>|\s{2,}', re.DOTALL)
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?»])\s+|(?<=[,;:])\s+(?=[A-ZÀÂÉÈÊËÎÏÔÙÛÜŒÆ])')
_MAX_CHUNK     = 250


def _clean(text: str) -> str | None:
    t = _JUNK_RE.sub(' ', text)
    t = re.sub(r'\[SILENCE\]|\[RÉSULTAT SYSTÈME\]', '', t, flags=re.IGNORECASE)
    t = ' '.join(t.split())
    if len(re.findall(r'[a-zA-ZÀ-ÿ0-9]', t)) < _MIN_ALPHANUM:
        return None
    return t[:4000]


def _split_chunks(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT.split(text)
    chunks, cur = [], ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(cur) + len(p) + 1 <= _MAX_CHUNK:
            cur = (cur + " " + p).strip() if cur else p
        else:
            if cur:
                chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return chunks or [text]

# ─── Résolution chemin + URL HuggingFace ─────────────────────────────────────

def _parse_voice_id(voice_id: str) -> tuple[str, str, str, str]:
    """
    'fr_FR-upmc-medium' → (lang='fr', locale='fr_FR', name='upmc', quality='medium').
    Accepte un chemin absolu → retourne ("", "", stem, "").
    """
    stem = Path(voice_id).stem  # enlève .onnx éventuel
    m = re.match(r'^([a-z]{2}_[A-Z]{2})-(.+)-(low|medium|high)$', stem)
    if m:
        locale  = m.group(1)
        name    = m.group(2)
        quality = m.group(3)
        return locale[:2].lower(), locale, name, quality
    return "", "", stem, ""


def get_model_paths(voice_id: str, models_dir: str = _DEFAULT_DIR) -> tuple[str, str]:
    """Retourne (onnx_path, json_path) pour voice_id."""
    if os.path.isabs(voice_id) and os.path.exists(voice_id):
        return voice_id, voice_id + ".json"
    os.makedirs(models_dir, exist_ok=True)
    stem  = Path(voice_id).stem
    onnx  = os.path.join(models_dir, stem + ".onnx")
    json_ = os.path.join(models_dir, stem + ".onnx.json")
    return onnx, json_


def _hf_url(voice_id: str, suffix: str) -> str:
    lang, locale, name, quality = _parse_voice_id(voice_id)
    if not lang:
        return ""
    stem = f"{locale}-{name}-{quality}"
    return f"{_HF_BASE}/{lang}/{locale}/{name}/{quality}/{stem}{suffix}?download=true"

# ─── Téléchargement automatique ─────────────────────────────────────────────

def ensure_model(voice_id: str, models_dir: str = _DEFAULT_DIR) -> bool:
    """
    Vérifie la présence locale du modèle Piper.
    Télécharge depuis HuggingFace si absent (une seule fois).
    Retourne True si les fichiers sont utilisables.
    """
    onnx, json_ = get_model_paths(voice_id, models_dir)

    pairs = []
    if not os.path.exists(onnx)  or os.path.getsize(onnx)  < 1000:
        pairs.append((onnx,  ".onnx"))
    if not os.path.exists(json_) or os.path.getsize(json_) < 10:
        pairs.append((json_, ".onnx.json"))

    if not pairs:
        return True  # déjà en cache

    print(f"[Piper] Téléchargement du modèle « {Path(voice_id).stem} » → {models_dir}/")

    for dest, suffix in pairs:
        url = _hf_url(voice_id, suffix)
        if not url:
            print(f"[Piper] ✗ URL introuvable pour '{voice_id}' ({suffix})")
            return False
        print(f"[Piper]   ↳ {url[:90]}…")
        try:
            def _hook(count, block, total, _dest=dest):
                if total > 0 and count % 200 == 0:
                    pct = min(100, count * block * 100 // total)
                    print(f"\r[Piper]   {pct:3d}%  {os.path.basename(_dest)}", end="", flush=True)
            urllib.request.urlretrieve(url, dest, reporthook=_hook)
            print()
            sz = os.path.getsize(dest) if os.path.exists(dest) else 0
            if sz < 10:
                print(f"[Piper] ✗ Fichier vide après téléchargement : {dest}")
                return False
            print(f"[Piper]   ✓ {os.path.basename(dest)}  ({sz // 1024} KB)")
        except Exception as e:
            print(f"\n[Piper] ✗ Erreur téléchargement : {e}")
            return False

    return True

# ─── Cache instances PiperVoice ──────────────────────────────────────────────

_voice_cache : dict[str, object] = {}
_cache_lock  = threading.Lock()
_piper_ok    : bool | None = None


def piper_available() -> bool:
    """Retourne True si le package piper-tts est installé."""
    global _piper_ok
    if _piper_ok is not None:
        return _piper_ok
    try:
        from piper.voice import PiperVoice  # noqa: F401
        _piper_ok = True
    except ImportError:
        _piper_ok = False
        print("[Piper] Package non installé. Lancer : pip install piper-tts")
    return _piper_ok


def _load_voice(voice_id: str, models_dir: str):
    """Charge (ou retourne depuis cache) une instance PiperVoice."""
    if not piper_available():
        return None
    key = f"{models_dir}::{Path(voice_id).stem}"
    with _cache_lock:
        if key in _voice_cache:
            return _voice_cache[key]

    if not ensure_model(voice_id, models_dir):
        return None

    onnx, _ = get_model_paths(voice_id, models_dir)
    try:
        from piper.voice import PiperVoice
        v = PiperVoice.load(onnx)
        with _cache_lock:
            _voice_cache[key] = v
        print(f"[Piper] ✓ Modèle chargé : {Path(voice_id).stem}")
        return v
    except Exception as e:
        print(f"[Piper] ✗ Chargement modèle : {e}")
        return None

# ─── Synthèse d'un chunk ────────────────────────────────────────────────────

def _pitch_shift(src: str, semitones: float) -> str:
    """
    Applique un pitch shift (en demi-tons) sur src via ffmpeg rubberband.
    Retourne le chemin du fichier résultant (nouveau fichier temp).
    Si semitones == 0 ou ffmpeg échoue, retourne src inchangé.
    Vérifie le pause event avant de lancer ffmpeg.
    """
    if abs(semitones) < 0.01:
        return src
    import subprocess, math
    # Vérifier pause avant de lancer un process
    try:
        from voice_interface import _AUDIO_PAUSE_EVENT
        if not _AUDIO_PAUSE_EVENT.is_set():
            return src
    except ImportError:
        pass
    ratio = 2 ** (semitones / 12.0)
    dst = None
    t0  = time.perf_counter()
    _log("pitch", f"ffmpeg rubberband  {semitones:+.1f} demi-tons  ratio={ratio:.4f}", "magenta")
    try:
        fd, dst = tempfile.mkstemp(suffix=".wav", prefix="piper_pitched_")
        os.close(fd)
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", src,
             "-af", f"rubberband=pitch={ratio:.6f}",
             dst],
            capture_output=True, timeout=15,
        )
        if r.returncode == 0 and os.path.getsize(dst) > 44:
            _log("pitch", f"✓ terminé  {_ms(t0)}", "green")
            try: os.remove(src)
            except OSError: pass
            return dst
        # Échec → garder l'original, nettoyer dst
        _log("pitch", f"✗ ffmpeg returncode={r.returncode}  {_ms(t0)}  stderr={r.stderr[:120]}", "red")
        try: os.remove(dst)
        except OSError: pass
        return src
    except Exception as e:
        _log("pitch", f"✗ exception : {e}  {_ms(t0)}", "red")
        if dst:
            try: os.remove(dst)
            except OSError: pass
        return src


def _synthesize_chunk(voice_obj, text: str, pitch_semitones: float = 0.0) -> str | None:
    """Synthétise un chunk → fichier WAV temporaire. Retourne le chemin ou None.

    API piper-tts >= 1.2 : synthesize() retourne un itérable d'AudioChunk.
    Chaque chunk expose audio_int16_bytes (PCM 16-bit) et les paramètres audio.
    On concatène tous les chunks dans un seul fichier WAV.
    Si pitch_semitones != 0, applique un pitch shift via ffmpeg rubberband.
    """
    prefix = "piper_DEBUG_" if PIPER_DEBUG else "piper_"
    preview = text[:60].replace("\n", " ")
    t0 = time.perf_counter()
    _log("synth", f"▶ « {preview}… »  ({len(text)} car)", "cyan")
    try:
        fd, tmp = tempfile.mkstemp(suffix=".wav", prefix=prefix)
        os.close(fd)
        if PIPER_DEBUG:
            _log("synth", f"  fichier : {tmp}", "grey")

        t_infer = time.perf_counter()
        chunks = list(voice_obj.synthesize(text))
        _log("synth", f"  inférence ONNX  {_ms(t_infer)}", "yellow")

        if not chunks:
            os.remove(tmp)
            _log("synth", "  ✗ synthesize() a retourné 0 chunks", "red")
            return None

        # Paramètres audio lus depuis le premier chunk (identiques pour tous)
        first = chunks[0]
        sample_rate     = first.sample_rate
        sample_width    = first.sample_width     # 2 = PCM 16-bit
        sample_channels = first.sample_channels  # 1 = mono

        t_wav = time.perf_counter()
        with wave.open(tmp, "wb") as wf:
            wf.setnchannels(sample_channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate)
            for chunk in chunks:
                wf.writeframes(chunk.audio_int16_bytes)
        _log("synth", f"  écriture WAV  {_ms(t_wav)}", "grey")

        if not (os.path.exists(tmp) and os.path.getsize(tmp) > 44):
            os.remove(tmp)
            _log("synth", "  ✗ fichier WAV vide après écriture", "red")
            return None

        # Pitch shift post-synthèse si demandé
        result = _pitch_shift(tmp, pitch_semitones)
        sz = os.path.getsize(result) if result and os.path.exists(result) else 0
        _log("synth", f"✓ total  {_ms(t0)}  ({sz//1024} KB)", "green")
        return result

    except Exception as e:
        _log("synth", f"✗ exception : {e}  {_ms(t0)}", "red")
        return None

# ─── Lecture ────────────────────────────────────────────────────────────────

# ─── Lecture ────────────────────────────────────────────────────────────────

def _wav_duration_s(path: str) -> float:
    """Retourne la durée en secondes d'un fichier WAV, ou 0 si illisible."""
    try:
        with wave.open(path, "rb") as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return 0.0


def _play_wav_aplay(path: str) -> bool:
    """
    Joue un WAV via aplay (ALSA userspace — respecte PulseAudio/PipeWire).
    aplay utilise le mixing logiciel → jamais de lock device exclusif.
    Enregistre le process dans le registre partagé voice_interface pour
    que pause_audio() / stop_audio() puissent le tuer proprement.
    Retourne None si aplay est absent (signal au caller de fallback sur ffplay).
    """
    import subprocess
    if not shutil.which("aplay"):
        return None
    try:
        use_registry = False
        try:
            from voice_interface import _active_processes, _proc_lock
            use_registry = True
        except ImportError:
            pass

        proc = subprocess.Popen(
            ["aplay", "-q", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if use_registry:
            with _proc_lock:
                _active_processes.append(proc)

        dur     = _wav_duration_s(path)
        timeout = max(dur * 1.5 + 3.0, 8.0)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait()
            return False
        finally:
            if use_registry:
                with _proc_lock:
                    if proc in _active_processes:
                        _active_processes.remove(proc)

        return proc.returncode in (0, -15)
    except Exception as e:
        _log("play", f"✗ aplay exception : {e}", "red")
        return False


def _play_file(path: str) -> bool:
    """
    Joue un fichier audio WAV via aplay (preferred) puis ffplay (fallback).

    Stratégie anti-hang :
      - aplay pour les WAV : utilise le mixing PulseAudio natif, pas de lock exclusif.
      - ffplay en fallback (MP3, ou si aplay absent) avec timeout calculé sur la durée
        réelle du fichier (taille / bitrate estimé) + 5s de marge.
      - Jamais de timeout fixe à 60s pour un clip de 3s.

    En mode PIPER_DEBUG=1 :
      - ffplay tourne sans -loglevel quiet (stderr visible en console)
      - le fichier WAV est conservé après lecture pour inspection
    """
    import subprocess

    sz = os.path.getsize(path) if os.path.exists(path) else 0
    t0 = time.perf_counter()
    is_wav = path.lower().endswith(".wav")
    _log("play", f"▶ {'aplay' if is_wav else 'ffplay'}  {os.path.basename(path)}  "
                 f"{sz//1024} KB  {sz} B", "cyan")
    if PIPER_DEBUG:
        _log("play", f"  chemin complet : {path}", "grey")

    try:
        # ── Vérifier le pause event AVANT de lancer quoi que ce soit ─────
        try:
            from voice_interface import _AUDIO_PAUSE_EVENT, _active_processes, _proc_lock
            if not _AUDIO_PAUSE_EVENT.is_set():
                _log("play", "  session en pause — ignoré", "grey")
                return False
            use_registry = True
        except ImportError:
            use_registry = False

        # ── Calcul du timeout réel ────────────────────────────────────────
        # WAV : durée exacte depuis l'en-tête.
        # MP3 : estimation grossière depuis la taille (128 kbps ≈ 16 KB/s).
        if is_wav:
            dur_s = _wav_duration_s(path)
        else:
            dur_s = sz / 16_000  # estimation MP3 128kbps
        play_timeout = max(dur_s * 1.5 + 5.0, 8.0)
        _log("play", f"  durée estimée {dur_s:.1f}s  timeout {play_timeout:.0f}s", "grey")

        # ── Essai aplay pour les WAV (pas de lock device exclusif) ────────
        if is_wav and shutil.which("aplay"):
            _log("play", "  tentative aplay", "grey")
            result = _play_wav_aplay(path)
            if result is not None:   # None = aplay absent
                elapsed = time.perf_counter() - t0
                _log("play", f"{'✓' if result else '✗'} aplay terminé  {_ms(t0)}  "
                             f"durée_réelle={elapsed:.2f}s", "green" if result else "red")
                return result
            _log("play", "  aplay absent — fallback ffplay", "yellow")

        # ── ffplay (fallback ou MP3) ───────────────────────────────────────
        ffplay_cmd = ["ffplay", "-nodisp", "-autoexit", path]
        if not PIPER_DEBUG:
            ffplay_cmd += ["-loglevel", "quiet"]
        _log("play", f"  cmd : {' '.join(ffplay_cmd)}", "grey")

        proc = subprocess.Popen(
            ffplay_cmd,
            stdout=subprocess.DEVNULL,
            stderr=None if PIPER_DEBUG else subprocess.DEVNULL,
        )
        if use_registry:
            with _proc_lock:
                _active_processes.append(proc)
        try:
            proc.wait(timeout=play_timeout)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait()
            _log("play", f"  ✗ timeout {play_timeout:.0f}s  (durée estimée {dur_s:.1f}s)  {_ms(t0)}", "red")
            return False
        finally:
            if use_registry:
                with _proc_lock:
                    if proc in _active_processes:
                        _active_processes.remove(proc)

        ok = proc.returncode in (0, -15)
        _log("play", f"{'✓' if ok else '✗'} ffplay terminé  {_ms(t0)}  rc={proc.returncode}", "green" if ok else "red")
        return ok

    except Exception as e:
        _log("play", f"✗ exception : {e}  {_ms(t0)}", "red")
        return False
    finally:
        if PIPER_DEBUG:
            _log("play", f"  [DEBUG] fichier conservé → {path}", "magenta")
        else:
            try:
                os.remove(path)
            except OSError:
                pass

# ─── API publique ─────────────────────────────────────────────────────────────

def prefetch_piper_voice(text: str, character_name: str,
                          voice_id: str, models_dir: str = _DEFAULT_DIR,
                          pitch_semitones: float = 0.0) -> list[str]:
    """
    Génère tous les chunks audio en avance (sans les jouer).
    Retourne une liste ORDONNÉE de chemins de fichiers WAV prêts à lire.
    Compatible avec voice_interface.play_prefetched().

    Les chunks sont synthétisés EN PARALLÈLE (ThreadPoolExecutor) :
    ONNX Runtime est thread-safe pour les appels concurrents sur la même
    session → gain ≈ N_chunks × temps_unitaire → temps_max_chunk.
    L'ordre des fichiers est garanti par indexation, pas par as_completed.
    """
    from concurrent.futures import ThreadPoolExecutor, wait as _fw, ALL_COMPLETED

    t0 = time.perf_counter()
    _log("prefetch", f"▶ {character_name}  voice={Path(voice_id).stem}  pitch={pitch_semitones:+.1f}st", "cyan")

    if not shutil.which("ffplay"):
        _log("prefetch", "✗ ffplay introuvable", "red")
        return []
    clean = _clean(text)
    if not clean:
        _log("prefetch", "✗ texte vide après nettoyage", "grey")
        return []
    voice_obj = _load_voice(voice_id, models_dir)
    if voice_obj is None:
        _log("prefetch", "✗ modèle non chargé", "red")
        return []

    chunks    = _split_chunks(clean)
    n_workers = min(len(chunks), os.cpu_count() or 2, 4)
    _log("prefetch", f"  {len(chunks)} chunk(s)  {n_workers} worker(s)  texte={len(clean)} car", "yellow")

    if n_workers <= 1 or len(chunks) == 1:
        results = []
        for i, chunk in enumerate(chunks):
            tc = time.perf_counter()
            f = _synthesize_chunk(voice_obj, chunk, pitch_semitones)
            if f:
                results.append(f)
            _log("prefetch", f"  chunk {i+1}/{len(chunks)}  {_ms(tc)}", "grey")
        _log("prefetch", f"✓ {len(results)}/{len(chunks)} fichiers  total {_ms(t0)}", "green")
        return results

    results: list[str | None] = [None] * len(chunks)

    def _synth(idx: int, chunk: str):
        tc = time.perf_counter()
        results[idx] = _synthesize_chunk(voice_obj, chunk, pitch_semitones)
        _log("prefetch", f"  chunk {idx+1}/{len(chunks)} terminé  {_ms(tc)}", "grey")

    t_pool = time.perf_counter()
    _log("prefetch", f"  lancement ThreadPoolExecutor({n_workers})", "yellow")
    with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="piper-pre") as ex:
        futures = [ex.submit(_synth, i, ch) for i, ch in enumerate(chunks)]
        _fw(futures, return_when=ALL_COMPLETED)
    _log("prefetch", f"  pool terminé  {_ms(t_pool)}", "yellow")

    files = [f for f in results if f is not None]
    _log("prefetch", f"✓ {len(files)}/{len(chunks)} fichiers  total {_ms(t0)}", "green")
    return files


def play_piper_voice(text: str, character_name: str,
                     voice_id: str, models_dir: str = _DEFAULT_DIR,
                     pitch_semitones: float = 0.0) -> bool:
    """
    Synthétise et joue la voix via Piper TTS (hors-ligne).
    Pipeline pipeliné : génération chunk N+1 en parallèle de la lecture chunk N.
    Même signature que voice_interface.play_voice().
    S'arrête proprement si pause_audio() est appelé en cours de lecture.
    """
    t0 = time.perf_counter()
    _log("play_voice", f"▶ {character_name}  voice={Path(voice_id).stem}", "cyan")

    if not shutil.which("ffplay"):
        _log("play_voice", "✗ ffplay introuvable — sudo apt install ffmpeg", "red")
        return False
    # Vérifier le pause event avant même de commencer
    try:
        from voice_interface import _AUDIO_PAUSE_EVENT
        if not _AUDIO_PAUSE_EVENT.is_set():
            _log("play_voice", "  session en pause — ignoré", "grey")
            return False
    except ImportError:
        pass

    clean = _clean(text)
    if not clean:
        _log("play_voice", "  ✗ texte vide après nettoyage", "grey")
        return False
    voice_obj = _load_voice(voice_id, models_dir)
    if voice_obj is None:
        _log("play_voice", "  ✗ modèle non chargé", "red")
        return False

    chunks    = _split_chunks(clean)
    n_workers = min(len(chunks), os.cpu_count() or 2, 4)
    _log("play_voice", f"  {len(chunks)} chunk(s)  {n_workers} worker(s)  texte={len(clean)} car", "yellow")

    file_q    = _queue.Queue(maxsize=3)
    _stop     = threading.Event()   # signale l'arrêt au générateur

    def _gen_all():
        """
        Synthétise tous les chunks EN PARALLÈLE, puis les place dans file_q
        DANS L'ORDRE pour garantir la cohérence de la lecture.

        ThreadPoolExecutor → N inférences ONNX simultanées sur N cœurs.
        On itère les futures dans l'ordre d'envoi (pas as_completed) pour
        préserver la séquence narrative.
        """
        from concurrent.futures import ThreadPoolExecutor

        tg = time.perf_counter()
        _log("gen", f"  pool({n_workers}) démarré pour {len(chunks)} chunks", "yellow")

        def _synth_safe(idx: int, chunk: str) -> tuple[int, str | None]:
            if _stop.is_set():
                return idx, None
            tc = time.perf_counter()
            result = _synthesize_chunk(voice_obj, chunk, pitch_semitones)
            _log("gen", f"  chunk {idx+1}/{len(chunks)} prêt  {_ms(tc)}", "grey")
            return idx, result

        with ThreadPoolExecutor(max_workers=n_workers,
                                thread_name_prefix="piper-gen") as ex:
            # Soumettre TOUS les chunks simultanément
            futures = [ex.submit(_synth_safe, i, ch) for i, ch in enumerate(chunks)]

            # Récupérer les résultats dans l'ordre pour la queue de lecture
            for fut in futures:
                if _stop.is_set():
                    fut.cancel()
                    continue
                try:
                    idx, result = fut.result(timeout=90)
                    file_q.put(result, timeout=90)
                except Exception as _e:
                    _log("gen", f"  ✗ chunk : {_e}", "red")
                    file_q.put(None, timeout=5)

        _log("gen", f"  pool terminé  {_ms(tg)}", "yellow")
        try:
            file_q.put("__DONE__", timeout=5)
        except _queue.Full:
            pass  # le thread principal a déjà quitté

    gen_t = threading.Thread(target=_gen_all, daemon=True)
    gen_t.start()

    any_played = False
    chunk_idx  = 0
    while True:
        try:
            # Timeout global par chunk : protège contre un synthesize() bloqué
            # (inférence Piper sans timeout interne) → échec propre après 90 s.
            item = file_q.get(timeout=90)
        except _queue.Empty:
            _log("play_voice", f"  ✗ timeout attente chunk {chunk_idx+1} — abandon pipeline TTS", "red")
            _stop.set()
            break

        if item == "__DONE__":
            break

        chunk_idx += 1
        tp = time.perf_counter()
        _log("play_voice", f"  ▶ lecture chunk {chunk_idx}/{len(chunks)}", "cyan")

        # _play_file retourne False si pause → on arrête le pipeline
        if item:
            ok = _play_file(item)
            _log("play_voice", f"  {'✓' if ok else '✗'} lecture terminée  {_ms(tp)}", "green" if ok else "yellow")
            if ok:
                any_played = True
            else:
                # Pause ou erreur : signaler au générateur et drainer la queue.
                _stop.set()
                while True:
                    try:
                        leftover = file_q.get(timeout=5)
                        if leftover == "__DONE__":
                            break
                        if leftover and os.path.exists(leftover):
                            try: os.remove(leftover)
                            except OSError: pass
                    except _queue.Empty:
                        break
                break

    _stop.set()
    gen_t.join(timeout=10)
    _log("play_voice", f"✓ terminé  total {_ms(t0)}", "green" if any_played else "yellow")
    return any_played


def play_prefetched_piper(files: list[str]) -> bool:
    """Joue une liste de fichiers pré-générés par prefetch_piper_voice()."""
    any_played = False
    for f in files:
        if f and os.path.exists(f):
            if _play_file(f):
                any_played = True
    return any_played