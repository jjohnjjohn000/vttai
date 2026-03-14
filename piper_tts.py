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
import wave
import shutil
import queue as _queue
import urllib.request
from pathlib import Path

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
            try: os.remove(src)
            except OSError: pass
            return dst
        # Échec → garder l'original, nettoyer dst
        try: os.remove(dst)
        except OSError: pass
        return src
    except Exception as e:
        print(f"[Piper] ✗ Pitch shift : {e}")
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
    try:
        fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="piper_")
        os.close(fd)

        chunks = list(voice_obj.synthesize(text))
        if not chunks:
            os.remove(tmp)
            return None

        # Paramètres audio lus depuis le premier chunk (identiques pour tous)
        first = chunks[0]
        sample_rate     = first.sample_rate
        sample_width    = first.sample_width     # 2 = PCM 16-bit
        sample_channels = first.sample_channels  # 1 = mono

        with wave.open(tmp, "wb") as wf:
            wf.setnchannels(sample_channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate)
            for chunk in chunks:
                wf.writeframes(chunk.audio_int16_bytes)

        if not (os.path.exists(tmp) and os.path.getsize(tmp) > 44):
            os.remove(tmp)
            return None

        # Pitch shift post-synthèse si demandé
        return _pitch_shift(tmp, pitch_semitones)

    except Exception as e:
        print(f"[Piper] ✗ Synthèse chunk : {e}")
        return None

# ─── Lecture ────────────────────────────────────────────────────────────────

# ─── Lecture ────────────────────────────────────────────────────────────────

def _play_file(path: str) -> bool:
    """Joue un fichier audio (WAV ou MP3) via ffplay. Supprime après lecture.

    Utilise le registre de process et le pause event de voice_interface pour :
      - être tué proprement par stop_audio() / pause_audio()
      - ne pas lancer de nouvelle lecture si la session est en pause
    Import lazy de voice_interface pour éviter les imports circulaires.
    """
    import subprocess
    try:
        # ── Vérifier le pause event AVANT de lancer ffplay ────────────────
        try:
            from voice_interface import _AUDIO_PAUSE_EVENT, _active_processes, _proc_lock
            if not _AUDIO_PAUSE_EVENT.is_set():
                return False   # session en pause — ignorer silencieusement
            use_registry = True
        except ImportError:
            use_registry = False

        proc = subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # ── Enregistrer dans le registre partagé pour stop/pause ──────────
        if use_registry:
            with _proc_lock:
                _active_processes.append(proc)
        try:
            proc.wait(timeout=60)
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
        print(f"[Piper] ✗ Lecture audio : {e}")
        return False
    finally:
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
    Retourne une liste ordonnée de chemins de fichiers WAV prêts à lire.
    Compatible avec voice_interface.play_prefetched().
    """
    if not shutil.which("ffplay"):
        return []
    clean = _clean(text)
    if not clean:
        return []
    voice_obj = _load_voice(voice_id, models_dir)
    if voice_obj is None:
        return []
    return [f for chunk in _split_chunks(clean)
            if (f := _synthesize_chunk(voice_obj, chunk, pitch_semitones)) is not None]


def play_piper_voice(text: str, character_name: str,
                     voice_id: str, models_dir: str = _DEFAULT_DIR,
                     pitch_semitones: float = 0.0) -> bool:
    """
    Synthétise et joue la voix via Piper TTS (hors-ligne).
    Pipeline pipeliné : génération chunk N+1 en parallèle de la lecture chunk N.
    Même signature que voice_interface.play_voice().
    S'arrête proprement si pause_audio() est appelé en cours de lecture.
    """
    if not shutil.which("ffplay"):
        print("[Piper] ffplay manquant — sudo apt install ffmpeg")
        return False
    # Vérifier le pause event avant même de commencer
    try:
        from voice_interface import _AUDIO_PAUSE_EVENT
        if not _AUDIO_PAUSE_EVENT.is_set():
            return False
    except ImportError:
        pass

    clean = _clean(text)
    if not clean:
        return False
    voice_obj = _load_voice(voice_id, models_dir)
    if voice_obj is None:
        return False

    chunks = _split_chunks(clean)
    file_q  = _queue.Queue(maxsize=3)
    _stop   = threading.Event()   # signale l'arrêt au générateur

    def _gen_all():
        for chunk in chunks:
            if _stop.is_set():
                break
            try:
                # put() avec timeout : évite un blocage permanent si le
                # thread principal a déjà abandonné et ne consomme plus.
                file_q.put(_synthesize_chunk(voice_obj, chunk, pitch_semitones),
                           timeout=90)
            except _queue.Full:
                print("[Piper] ✗ Queue pleine — générateur abandonné")
                break
        try:
            file_q.put("__DONE__", timeout=5)
        except _queue.Full:
            pass  # le thread principal a déjà quitté

    gen_t = threading.Thread(target=_gen_all, daemon=True)
    gen_t.start()

    any_played = False
    while True:
        try:
            # Timeout global par chunk : protège contre un synthesize() bloqué
            # (inférence Piper sans timeout interne) → échec propre après 90 s.
            item = file_q.get(timeout=90)
        except _queue.Empty:
            print("[Piper] ✗ Timeout attente chunk — abandon pipeline TTS")
            _stop.set()
            break

        if item == "__DONE__":
            break

        # _play_file retourne False si pause → on arrête le pipeline
        if item:
            ok = _play_file(item)
            if ok:
                any_played = True
            else:
                # Pause ou erreur : signaler au générateur et drainer la queue.
                # On continue de lire jusqu'à __DONE__ pour débloquer _gen_all
                # (sinon file_q.put() dans le générateur bloque à jamais).
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
                        break  # générateur bloqué en synthèse — on abandonne
                break

    _stop.set()
    gen_t.join(timeout=10)
    return any_played


def play_prefetched_piper(files: list[str]) -> bool:
    """Joue une liste de fichiers pré-générés par prefetch_piper_voice()."""
    any_played = False
    for f in files:
        if f and os.path.exists(f):
            if _play_file(f):
                any_played = True
    return any_played