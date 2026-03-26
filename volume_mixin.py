"""
volume_mixin.py — Contrôle du volume audio global pour DnDApp.

Fournit VolumeControlMixin à injecter dans DnDApp :
  - build_volume_control(parent)  : construit le widget Slider + label dans parent
  - _on_volume_change(value)      : callback appelé à chaque déplacement du slider

Le slider contrôle voice_interface._GLOBAL_VOLUME (0–100), qui est appliqué
à tous les appels ffplay (TTS edge-tts, Piper, et futures mécaniques audio).

Usage dans ui_setup_mixin.py (ou là où la toolbar est construite) :
    # Dans _build_toolbar() ou _build_controls() :
    self.build_volume_control(toolbar_frame)

Chargement au démarrage (dans __init__ ou avant setup_ui) :
    from voice_interface import load_volume_from_config
    load_volume_from_config()

Le volume est automatiquement persisté dans app_config.json à chaque changement.
"""

import tkinter as tk


class VolumeControlMixin:
    """Mixin pour DnDApp — slider de volume audio global."""

    def build_volume_control(self, parent: tk.Widget) -> tk.Frame:
        """
        Construit un widget de contrôle de volume dans parent.

        Retourne le Frame conteneur pour permettre un positionnement flexible.

        Composition :
          [ 🔊  Volume  [slider────────]  75% ]
        """
        from voice_interface import get_volume

        frame = tk.Frame(parent, bg=parent.cget("bg"))

        # ── Icône ─────────────────────────────────────────────────────────────
        lbl_icon = tk.Label(
            frame,
            text="Vol.",
            bg=frame.cget("bg"),
            fg="#cccccc",
            font=("TkDefaultFont", 9),
        )
        lbl_icon.pack(side="left", padx=(4, 0))

        # ── Slider ────────────────────────────────────────────────────────────
        self._volume_var = tk.IntVar(value=get_volume())
        slider = tk.Scale(
            frame,
            from_=0,
            to=100,
            orient="horizontal",
            variable=self._volume_var,
            command=self._on_volume_change,
            length=110,
            showvalue=False,           # on affiche nous-mêmes le % à droite
            bd=0,
            highlightthickness=0,
            troughcolor="#444444",
            bg=frame.cget("bg"),
            fg="#cccccc",
            activebackground="#81c784",
            sliderrelief="flat",
        )
        slider.pack(side="left", padx=4)

        # ── Label pourcentage ─────────────────────────────────────────────────
        self._volume_pct_label = tk.Label(
            frame,
            text=f"{get_volume():3d}%",
            bg=frame.cget("bg"),
            fg="#aaaaaa",
            font=("TkDefaultFont", 9),
            width=4,
            anchor="w",
        )
        self._volume_pct_label.pack(side="left")

        # ── Clic droit → reset à 100 % ────────────────────────────────────────
        slider.bind("<Button-3>", lambda e: self._set_volume(100))

        return frame

    # ─── Callbacks ────────────────────────────────────────────────────────────

    def _on_volume_change(self, value):
        """Appelé à chaque déplacement du slider (value est une chaîne depuis Tk)."""
        self._set_volume(int(float(value)))

    def _set_volume(self, value: int):
        """Applique et persiste le volume (0–100)."""
        from voice_interface import set_volume
        set_volume(value)

        # Mettre à jour le label % et le slider si appelé depuis clic droit
        if hasattr(self, "_volume_var"):
            self._volume_var.set(value)
        if hasattr(self, "_volume_pct_label"):
            self._volume_pct_label.config(text=f"{value:3d}%")

        # Feedback icône : muet / bas / normal / fort
        icon = "🔇" if value == 0 else ("🔈" if value < 40 else ("🔉" if value < 75 else "🔊"))
        # (le label icône n'est pas stocké mais on peut le retrouver via le parent)
