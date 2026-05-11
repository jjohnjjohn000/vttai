"""
music_mixer.py — Mixer Audio dual-channel (Background / Combat) pour DnDApp.

Fournit MusicMixerMixin à injecter dans DnDApp :
  - open_music_mixer()  : ouvre/relève la fenêtre mixer

Chaque canal gère indépendamment :
  - Lecture via subprocess ffplay (cohérent avec le reste de l'app)
  - Volume indépendant (0–100) via filtre -af volume=
  - Transport : ⏮ Previous, ▶ Play / ⏸ Pause, ⏹ Stop, ⏭ Next
  - Auto-avancement au morceau suivant en boucle
"""

import os
import signal
import time as _time
import subprocess
import threading
import tkinter as tk
from pathlib import Path


# ─── Répertoires par défaut ──────────────────────────────────────────────────
_BASE = Path(__file__).resolve().parent / "music"
_BG_DIR = _BASE / "background"
_COMBAT_DIR = _BASE / "combat"


def _scan_tracks(directory: Path) -> list[str]:
    """Retourne la liste triée des fichiers .mp3 dans directory."""
    if not directory.is_dir():
        return []
    return sorted(
        str(f) for f in directory.iterdir()
        if f.suffix.lower() == ".mp3" and f.is_file()
    )


def _short_name(path: str, max_len: int = 42) -> str:
    """Nom de fichier tronqué pour affichage."""
    name = Path(path).stem
    # Supprimer les suffixes courants de qualité
    for suf in (" (128k)", " (192k)", " (320k)"):
        name = name.replace(suf, "")
    if len(name) > max_len:
        return name[:max_len - 1] + "…"
    return name


# ═══════════════════════════════════════════════════════════════════════════════
#  ChannelStrip — contrôle complet pour un canal audio
# ═══════════════════════════════════════════════════════════════════════════════

class _ChannelStrip:
    """Un canal audio : liste de pistes, contrôles de transport, volume."""

    # NOUVEAU
    def __init__(self, parent: tk.Frame, label: str, color: str,
                 tracks: list[str], root: tk.Tk, saved_state: dict = None):
        self._root = root
        self._tracks = tracks
        
        if saved_state is None:
            saved_state = {}
            
        self._index = saved_state.get("index", 0)
        if self._tracks and self._index >= len(self._tracks):
            self._index = 0
            
        self._proc: subprocess.Popen | None = None
        self._paused = False
        self._playing = False
        self._volume = saved_state.get("volume", 80)
        self._poll_id = None       # after() id pour détecter fin de piste
        self._vol_debounce_id = None  # after() id pour debounce volume
        self._play_start_time = 0.0   # time.time() au lancement de ffplay
        self._play_offset = saved_state.get("elapsed", 0.0)
        self._pause_elapsed = 0.0     # temps écoulé accumulé pendant les pauses
        self._color = color

        # ── Frame principal du canal ─────────────────────────────────────────
        frame = tk.Frame(parent, bg="#1a1a2e", relief="groove", bd=1)
        frame.pack(fill=tk.X, padx=6, pady=(6, 2))
        self._frame = frame

        # ── En-tête ──────────────────────────────────────────────────────────
        hdr = tk.Frame(frame, bg="#1a1a2e")
        hdr.pack(fill=tk.X, padx=6, pady=(6, 2))
        tk.Label(hdr, text=label, bg="#1a1a2e", fg=color,
                 font=("Arial", 10, "bold")).pack(side=tk.LEFT)

        count_txt = f"{len(tracks)} piste{'s' if len(tracks) != 1 else ''}"
        tk.Label(hdr, text=count_txt, bg="#1a1a2e", fg="#666688",
                 font=("Consolas", 8)).pack(side=tk.RIGHT)

        # ── Label piste courante ─────────────────────────────────────────────
        self._track_label = tk.Label(
            frame, text="— aucune piste —" if not tracks else _short_name(tracks[self._index]),
            bg="#1a1a2e", fg="#aaaacc",
            font=("Consolas", 9, "italic"), anchor="w",
            wraplength=380, justify=tk.LEFT,
        )
        self._track_label.pack(fill=tk.X, padx=8, pady=(0, 4))

        # ── Transport ────────────────────────────────────────────────────────
        transport = tk.Frame(frame, bg="#1a1a2e")
        transport.pack(fill=tk.X, padx=6, pady=(0, 2))

        btn_style = dict(
            bg="#2a2a4a", fg="white", font=("Arial", 11, "bold"),
            activebackground="#4a4a7a", activeforeground="white",
            relief="flat", width=3, padx=2, pady=1,
        )

        self._btn_prev = tk.Button(transport, text="⏮", command=self._prev, **btn_style)
        self._btn_prev.pack(side=tk.LEFT, padx=2)

        self._btn_play = tk.Button(transport, text="▶", command=self._toggle_play, **btn_style)
        self._btn_play.pack(side=tk.LEFT, padx=2)

        self._btn_stop = tk.Button(transport, text="⏹", command=self._stop, **btn_style)
        self._btn_stop.pack(side=tk.LEFT, padx=2)

        self._btn_next = tk.Button(transport, text="⏭", command=self._next, **btn_style)
        self._btn_next.pack(side=tk.LEFT, padx=2)

        # ── Indicateur d'état ────────────────────────────────────────────────
        self._status_label = tk.Label(
            transport, text="⏹", bg="#1a1a2e", fg="#555577",
            font=("Consolas", 9),
        )
        self._status_label.pack(side=tk.LEFT, padx=(8, 0))

        # ── Volume ───────────────────────────────────────────────────────────
        vol_frame = tk.Frame(frame, bg="#1a1a2e")
        vol_frame.pack(fill=tk.X, padx=6, pady=(0, 6))

        tk.Label(vol_frame, text="Vol.", bg="#1a1a2e", fg="#888899",
                 font=("Consolas", 8)).pack(side=tk.LEFT, padx=(2, 0))

        self._vol_var = tk.IntVar(value=self._volume)
        self._slider = tk.Scale(
            vol_frame, from_=0, to=100, orient="horizontal",
            variable=self._vol_var, command=self._on_volume_change,
            length=180, showvalue=False, bd=0, highlightthickness=0,
            troughcolor="#333355", bg="#1a1a2e", fg="#aaaacc",
            activebackground=color, sliderrelief="flat",
        )
        self._slider.pack(side=tk.LEFT, padx=4)

        self._vol_pct = tk.Label(
            vol_frame, text=f"{self._volume}%",
            bg="#1a1a2e", fg="#888899",
            font=("Consolas", 8), width=4, anchor="w",
        )
        self._vol_pct.pack(side=tk.LEFT)

        # Clic droit sur le slider → reset à 80%
        self._slider.bind("<Button-3>", lambda e: self._set_volume(80))

        if not tracks:
            for btn in (self._btn_prev, self._btn_play, self._btn_stop, self._btn_next):
                btn.config(state=tk.DISABLED)

        # Reprise automatique de la lecture si elle était active lors de la sauvegarde
        if saved_state.get("playing", False) and self._tracks:
            self._root.after(500, lambda: self._play(seek=self._play_offset))

    # ── Transport ─────────────────────────────────────────────────────────────

    def _toggle_play(self):
        if not self._tracks:
            return
        if self._paused:
            self._resume()
        elif self._playing:
            self._pause()
        else:
            self._play()

    def _play(self, seek: float = 0.0):
        """Lance la lecture de la piste courante, optionnellement à seek secondes."""
        if not self._tracks:
            return
        self._kill_proc()
        path = self._tracks[self._index]
        # Volume effectif = global (main slider) × canal (mixer slider)
        from voice_interface import get_volume as _get_global_vol
        global_vol = _get_global_vol() / 100.0
        channel_vol = self._volume / 100.0
        vol_factor = max(0.0, global_vol * channel_vol)
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
               "-af", f"volume={vol_factor:.2f}"]
        if seek > 0.5:
            cmd += ["-ss", f"{seek:.1f}"]
        cmd.append(path)
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self._track_label.config(text="❌ ffplay introuvable")
            return

        self._playing = True
        self._paused = False
        self._play_offset = seek
        self._play_start_time = _time.time()
        self._pause_elapsed = 0.0
        self._track_label.config(text=_short_name(path))
        self._btn_play.config(text="⏸")
        self._status_label.config(text="▶", fg=self._color)
        self._start_poll()

    def _get_elapsed(self) -> float:
        """Retourne le temps écoulé depuis le début de la lecture en secondes."""
        if not self._playing:
            return 0.0
        if self._paused:
            return self._play_offset + self._pause_elapsed
        return self._play_offset + (_time.time() - self._play_start_time) + self._pause_elapsed

    def _pause(self):
        """Met en pause via SIGSTOP."""
        if self._proc and self._proc.poll() is None:
            # Capturer le temps écoulé AVANT la pause
            self._pause_elapsed += _time.time() - self._play_start_time
            try:
                os.kill(self._proc.pid, signal.SIGSTOP)
            except OSError:
                pass
            self._paused = True
            self._btn_play.config(text="▶")
            self._status_label.config(text="⏸", fg="#e6a817")

    def _resume(self):
        """Reprend la lecture via SIGCONT."""
        if self._proc and self._proc.poll() is None:
            try:
                os.kill(self._proc.pid, signal.SIGCONT)
            except OSError:
                pass
            self._paused = False
            self._play_start_time = _time.time()  # Reset le chrono pour la suite
            self._btn_play.config(text="⏸")
            self._status_label.config(text="▶", fg=self._color)

    def _stop(self):
        """Arrête la lecture."""
        self._kill_proc()
        self._playing = False
        self._paused = False
        self._play_offset = 0.0
        self._pause_elapsed = 0.0
        self._btn_play.config(text="▶")
        self._status_label.config(text="⏹", fg="#555577")
        if self._tracks:
            self._track_label.config(text=_short_name(self._tracks[self._index]))

    def _next(self):
        """Passe à la piste suivante."""
        if not self._tracks:
            return
        self._index = (self._index + 1) % len(self._tracks)
        was_playing = self._playing
        self._stop()
        if was_playing:
            self._play()
        else:
            self._track_label.config(text=_short_name(self._tracks[self._index]))

    def _prev(self):
        """Revient à la piste précédente."""
        if not self._tracks:
            return
        self._index = (self._index - 1) % len(self._tracks)
        was_playing = self._playing
        self._stop()
        if was_playing:
            self._play()
        else:
            self._track_label.config(text=_short_name(self._tracks[self._index]))

    # ── Volume ────────────────────────────────────────────────────────────────

    def _on_volume_change(self, value):
        vol = int(float(value))
        self._volume = vol
        self._vol_pct.config(text=f"{vol}%")
        # Debounce : redémarrer ffplay au même point après 300ms d'inactivité
        if self._playing and not self._paused:
            if self._vol_debounce_id is not None:
                try:
                    self._root.after_cancel(self._vol_debounce_id)
                except Exception:
                    pass
            self._vol_debounce_id = self._root.after(300, self._apply_volume_live)

    def _apply_volume_live(self):
        """Relance ffplay à la position courante avec le nouveau volume."""
        self._vol_debounce_id = None
        if self._playing and not self._paused:
            elapsed = self._get_elapsed()
            self._play(seek=elapsed)

    def _set_volume(self, value: int):
        self._vol_var.set(value)
        self._on_volume_change(str(value))

    # ── Polling fin de piste ──────────────────────────────────────────────────

    def _start_poll(self):
        self._cancel_poll()
        self._poll_id = self._root.after(500, self._check_finished)

    def _cancel_poll(self):
        if self._poll_id is not None:
            try:
                self._root.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None

    def _check_finished(self):
        """Vérifie si ffplay a terminé → auto-next."""
        if self._proc and self._proc.poll() is not None and self._playing and not self._paused:
            self._playing = False
            self._proc = None
            # Auto-avancement
            self._index = (self._index + 1) % len(self._tracks)
            self._play()
            return
        if self._playing:
            self._poll_id = self._root.after(500, self._check_finished)

    # ── Nettoyage ─────────────────────────────────────────────────────────────

    def _kill_proc(self):
        self._cancel_poll()
        if self._proc:
            try:
                # Resume d'abord si pausé (sinon SIGTERM ne fonctionne pas)
                if self._paused:
                    os.kill(self._proc.pid, signal.SIGCONT)
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def get_state(self) -> dict:
        """Exporte l'état actuel du canal."""
        return {
            "volume": self._volume,
            "index": self._index,
            "elapsed": self._get_elapsed(),
            "playing": self._playing and not self._paused
        }

    def destroy(self):
        """Appelé à la fermeture de la fenêtre."""
        self._kill_proc()


# ═══════════════════════════════════════════════════════════════════════════════
#  MusicMixerWindow — fenêtre Toplevel dual-channel
# ═══════════════════════════════════════════════════════════════════════════════

class MusicMixerWindow:
    """Fenêtre standalone de mixage audio avec deux canaux indépendants."""

    def __init__(self, root: tk.Tk):
        self.top = tk.Toplevel(root, bg="#12122a")
        self.top.withdraw()  # Fix XWayland mapping freeze
        self.top.title("🎵 Mixer Audio")
        self.top.geometry("440x400")
        self.top.minsize(380, 350)
        self.top.configure(bg="#12122a")

        # ── Titre ────────────────────────────────────────────────────────────
        tk.Label(
            self.top, text="🎵 MIXER AUDIO", bg="#12122a", fg="#c8b8ff",
            font=("Arial", 12, "bold"),
        ).pack(pady=(10, 4))

        tk.Frame(self.top, bg="#333366", height=1).pack(fill=tk.X, padx=12, pady=2)

        # ── Canaux ───────────────────────────────────────────────────────────
        bg_tracks = _scan_tracks(_BG_DIR)
        combat_tracks = _scan_tracks(_COMBAT_DIR)

        try:
            from state_manager import load_state
            mixer_state = load_state().get("music_mixer", {})
        except Exception:
            mixer_state = {}

        self._bg_channel = _ChannelStrip(
            self.top, "🌙 Ambiance", "#81c784", bg_tracks, root, mixer_state.get("bg", {})
        )
        tk.Frame(self.top, bg="#333366", height=1).pack(fill=tk.X, padx=12, pady=2)
        self._combat_channel = _ChannelStrip(
            self.top, "⚔️ Combat", "#e57373", combat_tracks, root, mixer_state.get("combat", {})
        )

        # Mapping asynchrone
        self.top.after(40, self.top.deiconify)
        self.top.after(80, self.top.lift)

        # ── Fermeture propre ─────────────────────────────────────────────────
        self.top.protocol("WM_DELETE_WINDOW", self._on_close)

    def save_state(self):
        try:
            from state_manager import load_state, save_state as _save_state
            state_data = load_state()
            state_data["music_mixer"] = {
                "bg": self._bg_channel.get_state(),
                "combat": self._combat_channel.get_state()
            }
            _save_state(state_data)
        except Exception:
            pass

    def _on_close(self):
        self.save_state()
        self._bg_channel.destroy()
        self._combat_channel.destroy()
        # X11 fix : withdraw + ghost
        try: self.top.selection_clear()
        except Exception: pass
        self.top.withdraw()
        self.top.update_idletasks()
        _root = self.top.master
        if not hasattr(_root, "_ghosted_panels"):
            _root._ghosted_panels = []
        _root._ghosted_panels.append(self.top)


# ═══════════════════════════════════════════════════════════════════════════════
#  MusicMixerMixin — mixin pour DnDApp
# ═══════════════════════════════════════════════════════════════════════════════

class MusicMixerMixin:
    """Mixin pour DnDApp — ouvre/relève la fenêtre mixer audio."""

    def open_music_mixer(self):
        win = getattr(self, "_music_mixer_win", None)
        if win and win.top.winfo_exists():
            win.top.deiconify()  # Necessaire après un withdraw() !
            win.top.lift()
            return
        self._music_mixer_win = MusicMixerWindow(self.root)
        self._track_window("music_mixer", self._music_mixer_win.top)
        # S'assurer que les touches sont liées (idempotent)
        self.setup_media_keys()

    def setup_media_keys(self):
        """
        Lie les touches multimédia au mixer.

        Deux mécanismes complémentaires :
          1. bind_all() Tkinter  — fonctionne quand l'app a le focus
             (contrairement à bind() qui exige que la fenêtre *racine* ait le focus).
          2. pynput Listener     — capture globale X11/uinput, fonctionne même
             quand l'app n'a pas le focus ET même si GNOME/PulseAudio a grabé
             les touches avant Tk.

        Appeler cette méthode UNE FOIS au démarrage (depuis __init__ ou après
        setup_ui) pour que les touches fonctionnent même sans ouvrir le mixer.
        Elle est idempotente.
        """
        if getattr(self, "_media_keys_bound", False):
            return
        self._media_keys_bound = True

        # ── 1. bind_all : couvre root ET tous les Toplevel focalisés ─────────
        _TK_MEDIA = [
            ("<XF86AudioPlay>",  "play"),
            ("<XF86AudioPause>", "play"),
            ("<XF86AudioStop>",  "stop"),
            ("<XF86AudioNext>",  "next"),
            ("<XF86AudioPrev>",  "prev"),
        ]
        for seq, action in _TK_MEDIA:
            self.root.bind_all(seq, lambda e, a=action: self._media_key(a))

        # ── 2. pynput : capture globale (GNOME grab, app sans focus, etc.) ───
        self._start_pynput_media_listener()

    def _start_pynput_media_listener(self):
        """
        Lance un thread pynput qui écoute les touches multimédia globalement.
        - Utilise root.after(0, …) pour repasser dans le thread Tk (thread-safe).
        - Le thread est daemon → il meurt avec l'app sans besoin de cleanup.
        - Si pynput n'est pas installé, cette méthode est silencieusement ignorée.
          Pour l'installer : pip install pynput
        """
        try:
            from pynput import keyboard as _kb
        except ImportError:
            print("[MediaKeys] pynput non installé — seules les touches Tk (focus requis) "
                  "fonctionneront.\n           Pour activer la capture globale : pip install pynput")
            return

        # Correspondance pynput Key → action mixer
        MEDIA_MAP = {
            _kb.Key.media_play_pause: "play",
            _kb.Key.media_next:       "next",
            _kb.Key.media_previous:   "prev",
        }

        def _on_press(key):
            action = MEDIA_MAP.get(key)
            if action:
                # Repasser dans le thread principal Tk
                try:
                    self.root.after(0, lambda a=action: self._media_key(a))
                except Exception:
                    pass  # root déjà détruit (fermeture de l'app)

        try:
            listener = _kb.Listener(on_press=_on_press)
            listener.daemon = True
            listener.start()
            self._pynput_listener = listener
            print("[MediaKeys] pynput global listener actif")
        except Exception as exc:
            print(f"[MediaKeys] Impossible de démarrer pynput : {exc}")

    def _media_key_target(self):
        """Retourne le canal actif à contrôler, ou None."""
        mixer = getattr(self, "_music_mixer_win", None)
        if not mixer:
            return None
        try:
            if not mixer.top.winfo_exists():
                return None
        except Exception:
            return None
        bg = mixer._bg_channel
        cb = mixer._combat_channel
        # Si seul le combat joue → contrôler le combat
        if cb._playing and not bg._playing:
            return cb
        # Sinon → contrôler l'ambiance (par défaut)
        return bg

    def _media_key(self, action: str):
        ch = self._media_key_target()
        if not ch:
            return
        if action == "play":
            ch._toggle_play()
        elif action == "stop":
            ch._stop()
        elif action == "next":
            ch._next()
        elif action == "prev":
            ch._prev()