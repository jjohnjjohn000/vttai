"""
character_faces.py — v2
───────────────────────────────────────────────────────────────────
Nouvelles fonctionnalités v2 :

  🎤  LIPSYNC ORGANIQUE
      Mouvement de bouche multi-fréquence pendant la lecture vocale.
      Appel : face.set_talking(True / False)   ← inchangé

  💭  ANIMATION DE RÉFLEXION
      Points flottants + lueur pulsante quand le LLM génère une réponse.
      Appel : face.set_thinking(True / False)

  😨  SYSTÈME D'ÉMOTIONS  (10 états)
      Les sourcils, les yeux et la bouche réagissent à l'émotion courante.
      Appel : face.set_emotion("happy" | "sad" | "angry" | "fear" |
                               "surprise" | "disgust" | "impatient" |
                               "tenderness" | "focused" | "neutral")
      L'émotion s'efface progressivement vers "neutral" après `EMOTION_DECAY`
      frames (≈12 s) si on ne la renouvelle pas.

API publique (résumé)
─────────────────────
  face.set_talking(bool)       → lipsync
  face.set_thinking(bool)      → animation réflexion
  face.set_emotion(str)        → émotion immédiate (se reset auto)
  face.show() / face.hide()    → visibilité
  face.destroy()               → nettoyage
"""

import tkinter as tk
import math
import random

# ─── Dimensions ───────────────────────────────────────────────────────────────
FACE_W   = 112
FACE_H   = 148
TITLE_H  = 20
BOT_H    = 22
CANVAS_H = FACE_H - TITLE_H - BOT_H   # ≈106

# Nombre de frames (≈34 ms chacune) avant que l'émotion revienne à "neutral"
EMOTION_DECAY = 350   # ≈12 s

# ─── Émotions ─────────────────────────────────────────────────────────────────
#
#  brow_base  : décalage Y global des sourcils  (< 0 → hausse, > 0 → baisse)
#  brow_inner : décalage Y de l'extrémité interne (vers le nez)
#               > 0 → baisse interne → froncement (angry, disgust)
#               < 0 → hausse interne → tristesse (sad)
#  brow_outer : décalage Y de l'extrémité externe
#  eye_widen  : agrandissement de l'iris (> 0 → grand ouvert, < 0 → plissé)
#  mouth_curve: intensité de la courbe  (> 0 → sourire, < 0 → grimace)
#  mouth_open_bonus : s'ajoute à la bouche même sans parler
#
EMOTION_CONFIG = {
    "neutral":    {"icon": "",   "brow_base":  0, "brow_inner":  0, "brow_outer":  0, "eye_widen":  0, "mouth_curve":  0, "mouth_open_bonus": 0.0},
    "fear":       {"icon": "😨", "brow_base": -5, "brow_inner":  3, "brow_outer": -3, "eye_widen":  3, "mouth_curve": -1, "mouth_open_bonus": 0.2},
    "surprise":   {"icon": "😮", "brow_base": -7, "brow_inner":  4, "brow_outer": -4, "eye_widen":  4, "mouth_curve":  0, "mouth_open_bonus": 0.5},
    "disgust":    {"icon": "🤢", "brow_base":  0, "brow_inner":  4, "brow_outer": -1, "eye_widen": -1, "mouth_curve": -2, "mouth_open_bonus": 0.0},
    "impatient":  {"icon": "😒", "brow_base":  1, "brow_inner":  2, "brow_outer":  0, "eye_widen": -1, "mouth_curve": -1, "mouth_open_bonus": 0.0},
    "tenderness": {"icon": "🥰", "brow_base": -2, "brow_inner": -1, "brow_outer":  1, "eye_widen": -1, "mouth_curve":  2, "mouth_open_bonus": 0.0},
    "happy":      {"icon": "😊", "brow_base": -3, "brow_inner": -1, "brow_outer":  1, "eye_widen": -2, "mouth_curve":  3, "mouth_open_bonus": 0.0},
    "sad":        {"icon": "😢", "brow_base":  0, "brow_inner": -3, "brow_outer":  3, "eye_widen": -1, "mouth_curve": -3, "mouth_open_bonus": 0.0},
    "angry":      {"icon": "😠", "brow_base":  2, "brow_inner":  5, "brow_outer": -1, "eye_widen": -2, "mouth_curve": -2, "mouth_open_bonus": 0.0},
    "focused":    {"icon": "🎯", "brow_base":  0, "brow_inner":  2, "brow_outer":  0, "eye_widen": -1, "mouth_curve":  0, "mouth_open_bonus": 0.0},
}

# ─── Thèmes par personnage ────────────────────────────────────────────────────
CHARACTER_DATA = {
    "Kaelen": {
        "color":    "#a0c4ff",
        "border":   "#5080c0",
        "bg":       "#0d1a2a",
        "title":    "Kaelen",
        "subtitle": "⚔️ Paladin",
    },
    "Elara": {
        "color":    "#c8b8ff",
        "border":   "#7050c0",
        "bg":       "#110920",
        "title":    "Elara",
        "subtitle": "🔮 Mage",
    },
    "Thorne": {
        "color":    "#ff9999",
        "border":   "#b03030",
        "bg":       "#180808",
        "title":    "Thorne",
        "subtitle": "🗡️ Assassin",
    },
    "Lyra": {
        "color":    "#a8f0a8",
        "border":   "#30a030",
        "bg":       "#071407",
        "title":    "Lyra",
        "subtitle": "✨ Clerc",
    },
}

FACE_CONFIGS = {
    "Kaelen": {
        "skin":       "#c8a882",
        "hair":       "#3a2010",
        "eye_color":  "#4080e0",
        "expression": "stern",
    },
    "Elara": {
        "skin":       "#e0cdb8",
        "hair":       "#200838",
        "eye_color":  "#9040d0",
        "expression": "analytical",
    },
    "Thorne": {
        "skin":       "#9a7080",
        "hair":       "#0a0808",
        "eye_color":  "#cc2020",
        "expression": "smirk",
    },
    "Lyra": {
        "skin":       "#d8c4a0",
        "hair":       "#c8a820",
        "eye_color":  "#28a028",
        "expression": "gentle",
    },
}


def _darken(hex_color: str, factor: float) -> str:
    try:
        h = hex_color.lstrip("#")
        if len(h) == 6:
            r = max(0, min(255, int(int(h[0:2], 16) * factor)))
            g = max(0, min(255, int(int(h[2:4], 16) * factor)))
            b = max(0, min(255, int(int(h[4:6], 16) * factor)))
            return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        pass
    return hex_color


def _blend(c1: str, c2: str, t: float) -> str:
    """Interpolation linéaire entre deux couleurs HEX. t ∈ [0, 1]."""
    try:
        h1, h2 = c1.lstrip("#"), c2.lstrip("#")
        if len(h1) == 6 and len(h2) == 6:
            r = int(int(h1[0:2], 16) * (1-t) + int(h2[0:2], 16) * t)
            g = int(int(h1[2:4], 16) * (1-t) + int(h2[2:4], 16) * t)
            b = int(int(h1[4:6], 16) * (1-t) + int(h2[4:6], 16) * t)
            return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        pass
    return c1


# ─── Classe principale ────────────────────────────────────────────────────────
class CharacterFaceWindow:
    """Fenêtre flottante semi-transparente avec visage animé."""

    def __init__(self, root: tk.Tk, character_name: str, x: int = 0, y: int = 0,
                 parent_frame: tk.Frame = None):
        self.root = root
        self.name = character_name
        self.data = CHARACTER_DATA[character_name]
        self.cfg  = FACE_CONFIGS[character_name]
        self._embedded = parent_frame is not None   # True = embedded in a frame

        # ── États d'animation ─────────────────────────────────────────────────
        self._tick          = 0
        self._breath        = 0.0
        self._blink_open    = True
        self._blink_counter = 0

        # Lipsync
        self._mouth_open    = 0.0
        self._mouth_phase   = 0.0          # accumulateur multi-fréquence
        self._talking       = False

        # Réflexion
        self._thinking      = False
        self._think_tick    = 0

        # Émotion
        self._emotion       = "neutral"
        self._emotion_timer = 0            # compte à rebours EMOTION_DECAY
        self._prev_emotion  = "neutral"    # pour transition douce
        self._emotion_blend = 1.0          # 1 = émotion courante, 0 = précédente

        # Particules émotionnelles (liste de (x, y, vx, vy, life, max_life))
        self._particles: list = []

        # Drag (désactivé en mode embedded)
        self._drag_offset = None
        self._alive       = True

        # ── Fenêtre ou frame conteneur ────────────────────────────────────────
        if self._embedded:
            self.win = parent_frame
        else:
            self.win = tk.Toplevel(root)
            self.win.overrideredirect(True)
            self.win.attributes("-alpha", 0.88)
            self.win.attributes("-topmost", True)
            self.win.geometry(f"{FACE_W}x{FACE_H}+{x}+{y}")
            self.win.configure(bg=self.data["bg"])
            self.win.protocol("WM_DELETE_WINDOW", self.hide)

        self._build_ui()
        if not self._embedded:
            self._bind_drag(self._title_bar)
            self._bind_drag(self._title_lbl)
            self._bind_context_menu()
        else:
            self._bind_context_menu()   # menu émotions toujours disponible
        # Différer le premier tick pour laisser le canvas être rendu par Tk
        # (appel immédiat sur un widget non encore affiché → segfault possible)
        self.root.after(100, self._animate)

    # ── Construction UI ───────────────────────────────────────────────────────
    def _build_ui(self):
        border = self.data["border"]
        bg     = self.data["bg"]
        color  = self.data["color"]

        if not self._embedded:
            self._title_bar = tk.Frame(self.win, bg=border, height=TITLE_H, cursor="fleur")
            self._title_bar.pack(fill=tk.X)
            self._title_bar.pack_propagate(False)

            self._title_lbl = tk.Label(
                self._title_bar, text=self.data["title"],
                bg=border, fg="white", font=("Consolas", 8, "bold"),
                cursor="fleur"
            )
            self._title_lbl.pack(side=tk.LEFT, padx=5)

            tk.Button(
                self._title_bar, text="×",
                bg=border, fg="white", font=("Arial", 10, "bold"),
                bd=0, relief="flat", padx=3, pady=0,
                activebackground=_darken(border, 1.3),
                command=self.hide
            ).pack(side=tk.RIGHT)
        else:
            # Placeholder pour éviter AttributeError dans _bind_drag
            self._title_bar = None
            self._title_lbl = None

        self.canvas = tk.Canvas(
            self.win, width=FACE_W, height=CANVAS_H,
            bg=bg, highlightthickness=2,
            highlightbackground=border
        )
        self.canvas.pack()

        bot = tk.Frame(self.win, bg=bg, height=BOT_H)
        bot.pack(fill=tk.X)
        bot.pack_propagate(False)

        self._status_lbl = tk.Label(
            bot, text=self.data["subtitle"],
            bg=bg, fg=color, font=("Consolas", 8)
        )
        self._status_lbl.pack(side=tk.LEFT, padx=5)

        if not self._embedded:
            alpha_frame = tk.Frame(bot, bg=bg)
            alpha_frame.pack(side=tk.RIGHT, padx=3)
            self._alpha_val = [0.88]

            def _change_alpha(delta):
                self._alpha_val[0] = round(
                    max(0.15, min(1.0, self._alpha_val[0] + delta)), 2
                )
                self.win.attributes("-alpha", self._alpha_val[0])

            tk.Button(alpha_frame, text="−", bg=bg, fg="#888", font=("Arial", 7),
                      bd=0, relief="flat", command=lambda: _change_alpha(-0.15)
                      ).pack(side=tk.LEFT)
            tk.Button(alpha_frame, text="+", bg=bg, fg="#888", font=("Arial", 7),
                      bd=0, relief="flat", command=lambda: _change_alpha(+0.15)
                      ).pack(side=tk.LEFT)

    # ── Déplacement ───────────────────────────────────────────────────────────
    def _bind_drag(self, widget):
        widget.bind("<ButtonPress-1>", self._on_drag_start)
        widget.bind("<B1-Motion>",     self._on_drag_motion)

    def _on_drag_start(self, event):
        self._drag_offset = (
            event.x_root - self.win.winfo_x(),
            event.y_root - self.win.winfo_y()
        )

    def _on_drag_motion(self, event):
        if self._drag_offset:
            nx = event.x_root - self._drag_offset[0]
            ny = event.y_root - self._drag_offset[1]
            self.win.geometry(f"+{nx}+{ny}")

    # ── Menu contextuel ───────────────────────────────────────────────────────
    def _bind_context_menu(self):
        self._ctx = None

        def post_menu(e):
            if self._ctx is None:
                m = tk.Menu(self.root, tearoff=0, bg="#2a2a2a", fg="white",
                            activebackground="#4a4a4a", font=("Arial", 9))
                m.add_command(label="👻 Fantôme  (0.3)",  command=lambda: self.win.attributes("-alpha", 0.3))
                m.add_command(label="🌫️  Mi-opaque (0.6)", command=lambda: self.win.attributes("-alpha", 0.6))
                m.add_command(label="🔲 Opaque   (0.95)", command=lambda: self.win.attributes("-alpha", 0.95))
                m.add_separator()
                m.add_command(label="📌 Toujours au 1er plan ON",  command=lambda: self.win.attributes("-topmost", True))
                m.add_command(label="   Toujours au 1er plan OFF", command=lambda: self.win.attributes("-topmost", False))
                m.add_separator()
                # Sous-menu test émotions
                em = tk.Menu(m, tearoff=0, bg="#2a2a2a", fg="white",
                             activebackground="#4a4a4a", font=("Arial", 9))
                for emo in EMOTION_CONFIG:
                    cfg = EMOTION_CONFIG[emo]
                    label = f"{cfg['icon']} {emo}" if cfg['icon'] else emo
                    em.add_command(label=label, command=lambda e=emo: self.set_emotion(e))
                m.add_cascade(label="🎭 Tester émotion", menu=em)
                m.add_separator()
                m.add_command(label="✕ Masquer", command=self.hide)
                self._ctx = m

            self._ctx.post(e.x_root, e.y_root)

        self.canvas.bind("<Button-3>", post_menu)

    # ── Boucle d'animation ────────────────────────────────────────────────────
    def _animate(self):
        if not self._alive:
            return
        self._tick    += 1
        self._breath  += 0.055

        # ── Clignement ───────────────────────────────────────────────────────
        self._blink_counter += 1
        if self._emotion in ("fear", "surprise"):
            blink_interval = 60         # clignement plus fréquent
        else:
            blink_interval = 100
        bc = self._blink_counter % blink_interval
        self._blink_open = not (3 <= bc <= 5)

        # ── Lipsync organique ─────────────────────────────────────────────────
        eco = EMOTION_CONFIG.get(self._emotion, EMOTION_CONFIG["neutral"])
        if self._talking:
            self._mouth_phase += 0.35
            # Combinaison de 3 sinusoïdes pour imiter la variété phonétique
            w1 = abs(math.sin(self._mouth_phase))
            w2 = abs(math.sin(self._mouth_phase * 1.73 + 1.2))
            w3 = abs(math.sin(self._mouth_phase * 2.31 + 0.7))
            base_open = 0.20 + 0.42 * w1 + 0.22 * w2 + 0.10 * w3
            self._mouth_open = min(1.0, base_open + eco["mouth_open_bonus"])
        else:
            # Fermeture progressive
            self._mouth_open = max(0.0, self._mouth_open - 0.09)

        # ── Compteur de réflexion ─────────────────────────────────────────────
        if self._thinking:
            self._think_tick += 1
        else:
            self._think_tick = max(0, self._think_tick - 2)   # fondu sortant

        # ── Décroissance d'émotion ────────────────────────────────────────────
        if self._emotion != "neutral":
            self._emotion_timer -= 1
            if self._emotion_timer <= 0:
                self._prev_emotion = self._emotion
                self._emotion      = "neutral"
                self._emotion_blend = 0.0
        if self._emotion_blend < 1.0:
            self._emotion_blend = min(1.0, self._emotion_blend + 0.04)

        # ── Particules ────────────────────────────────────────────────────────
        self._update_particles()

        # ── Rendu ────────────────────────────────────────────────────────────
        self._draw_face()
        self._update_status_bar()

        try:
            # En mode embedded, self.win est un Frame — utilise root.after
            scheduler = self.root if self._embedded else self.win
            scheduler.after(34, self._animate)
        except Exception:
            self._alive = False

    def _update_status_bar(self):
        eco  = EMOTION_CONFIG.get(self._emotion, EMOTION_CONFIG["neutral"])
        icon = eco["icon"]
        base = self.data["subtitle"]

        if self._thinking:
            label = f"💭 {base}"
        elif icon:
            label = f"{icon} {base}"
        else:
            label = base

        self._status_lbl.config(text=label)

        # Couleur de la barre de titre selon l'émotion
        border = self.data["border"]
        emotion_colors = {
            "angry":     "#8b0000",
            "fear":      "#1a1a4a",
            "sad":       "#1a2a3a",
            "happy":     "#1a4a1a",
            "tenderness":"#3a1a3a",
            "surprise":  "#4a3a00",
            "disgust":   "#1a3a1a",
        }
        tint = emotion_colors.get(self._emotion, border)
        blended = _blend(border, tint, 0.5 * self._emotion_blend)
        try:
            self._title_bar.config(bg=blended)
            self._title_lbl.config(bg=blended)
        except Exception:
            pass

    # ── Particules ────────────────────────────────────────────────────────────
    def _spawn_emotion_particles(self):
        """Émet quelques particules colorées lors d'un changement d'émotion."""
        emotion_particle_colors = {
            "happy":      "#ffe066",
            "tenderness": "#ff99cc",
            "angry":      "#ff4444",
            "fear":       "#8888ff",
            "surprise":   "#ffaa00",
            "sad":        "#aabbff",
            "disgust":    "#88cc44",
        }
        color = emotion_particle_colors.get(self._emotion)
        if not color:
            return

        cw, ch = FACE_W, CANVAS_H
        cx = cw // 2
        cy = ch // 2

        for _ in range(6):
            angle = random.uniform(0, 2 * math.pi)
            speed = random.uniform(1.2, 3.0)
            life  = random.randint(18, 35)
            self._particles.append([
                cx + random.uniform(-15, 15),    # x
                cy + random.uniform(-10, 10),    # y
                math.cos(angle) * speed,          # vx
                math.sin(angle) * speed - 1.0,   # vy (légèrement vers le haut)
                life,                             # vie restante
                life,                             # vie max
                color,
            ])

    def _update_particles(self):
        alive = []
        for p in self._particles:
            p[0] += p[2]      # x += vx
            p[1] += p[3]      # y += vy
            p[3] += 0.15      # gravité
            p[4] -= 1         # vie
            if p[4] > 0:
                alive.append(p)
        self._particles = alive

    # ── Dessin principal ──────────────────────────────────────────────────────
    def _draw_face(self):
        c   = self.canvas
        cfg = self.cfg
        cw, ch = FACE_W, CANVAS_H
        cx  = cw // 2
        eco = EMOTION_CONFIG.get(self._emotion, EMOTION_CONFIG["neutral"])

        c.delete("all")

        breath = math.sin(self._breath) * 1.8
        fy = int(ch * 0.46 + breath)

        # ── Fond lumineux (pensée) ────────────────────────────────────────────
        if self._think_tick > 0:
            intensity = min(1.0, self._think_tick / 20)
            glow_pulse = 0.5 + 0.5 * math.sin(self._think_tick * 0.12)
            glow_alpha = intensity * glow_pulse
            color = self.data["color"]
            for i in range(3, 0, -1):
                r_adj = 255 - int((255 - int(color[1:3], 16)) * (1 - glow_alpha * 0.3 * i))
                g_adj = 255 - int((255 - int(color[3:5], 16)) * (1 - glow_alpha * 0.3 * i))
                b_adj = 255 - int((255 - int(color[5:7], 16)) * (1 - glow_alpha * 0.3 * i))
                try:
                    gc = f"#{max(0,min(255,r_adj)):02x}{max(0,min(255,g_adj)):02x}{max(0,min(255,b_adj)):02x}"
                    c.create_rectangle(i, i, cw-i, ch-i, outline=gc, width=1)
                except Exception:
                    pass

        # ── Cheveux arrière ───────────────────────────────────────────────────
        self._hair_back(c, cx, fy, cfg)

        # ── Visage ────────────────────────────────────────────────────────────
        fw, fh = 52, 60
        x1, y1 = cx - fw//2, fy - fh//2
        x2, y2 = cx + fw//2, fy + fh//2

        c.create_oval(x1+3, y1+3, x2+3, y2+3,
                      fill="#000000", outline="", stipple="gray25")
        c.create_oval(x1, y1, x2, y2,
                      fill=cfg["skin"], outline=_darken(cfg["skin"], 0.70), width=1)

        # ── Sourcils ──────────────────────────────────────────────────────────
        ey = fy - 8
        self._draw_eyebrows(c, cx, ey, eco)

        # ── Yeux ──────────────────────────────────────────────────────────────
        self._draw_eyes(c, cx, ey, cfg, eco)

        # ── Nez ───────────────────────────────────────────────────────────────
        c.create_oval(cx-3, fy-1, cx+3, fy+6,
                      fill=_darken(cfg["skin"], 0.82), outline="")

        # ── Bouche ────────────────────────────────────────────────────────────
        self._draw_mouth(c, cx, fy + 19, cfg["expression"], eco)

        # ── Cheveux avant ─────────────────────────────────────────────────────
        self._hair_front(c, cx, fy, cfg)

        # ── Feature spéciale ──────────────────────────────────────────────────
        self._draw_feature(c, cx, fy, cfg)

        # ── Vêtements ─────────────────────────────────────────────────────────
        self._draw_clothes(c, cx, ch)

        # ── Animation de réflexion ────────────────────────────────────────────
        if self._think_tick > 0:
            self._draw_thinking_overlay(c, cx, fy)

        # ── Particules émotionnelles ───────────────────────────────────────────
        for p in self._particles:
            x, y, _, _, life, max_life, color = p
            size = max(1, int(3 * life / max_life))
            try:
                c.create_oval(x-size, y-size, x+size, y+size,
                              fill=color, outline="")
            except Exception:
                pass

        # ── Bordure ───────────────────────────────────────────────────────────
        c.create_rectangle(0, 0, cw-1, ch-1,
                           outline=self.data["border"], width=2)

    # ── Sourcils ──────────────────────────────────────────────────────────────
    def _draw_eyebrows(self, c, cx, ey, eco):
        """
        Dessine deux sourcils dont la forme réagit à l'émotion.
        Axe Y : valeurs plus grandes = plus bas sur l'écran.

        Anatomie :
          sourcil gauche : extrémité ext. (cx-21) → extrémité int. (cx-5)
          sourcil droit  : extrémité int. (cx+5)  → extrémité ext. (cx+21)
        """
        hair  = self.cfg["hair"]
        base  = ey - 12 + eco["brow_base"]     # y de référence
        y_in  = base + eco["brow_inner"]         # y des extrémités internes
        y_out = base + eco["brow_outer"]         # y des extrémités externes

        # Sourcil gauche (outer=gauche, inner=droite)
        c.create_line(cx - 21, y_out, cx - 5, y_in,
                      fill=hair, width=2, capstyle="round")
        # Sourcil droit (inner=gauche, outer=droite)
        c.create_line(cx + 5, y_in, cx + 21, y_out,
                      fill=hair, width=2, capstyle="round")

    # ── Yeux ──────────────────────────────────────────────────────────────────
    def _draw_eyes(self, c, cx, ey, cfg, eco):
        """Yeux dont la taille varie avec l'émotion."""
        ew = max(4, 8 + eco["eye_widen"])   # demi-largeur  de l'iris
        eh = max(3, 6 + eco["eye_widen"])   # demi-hauteur  de l'iris
        pw = max(3, 5 + eco["eye_widen"])   # demi-largeur  de la pupille

        for ex in [cx - 13, cx + 13]:
            # Blanc de l'œil
            c.create_oval(ex - ew - 1, ey - eh - 1,
                          ex + ew + 1, ey + eh + 1,
                          fill="white", outline="#666666", width=1)

            if self._blink_open:
                # Iris
                c.create_oval(ex - ew, ey - eh, ex + ew, ey + eh,
                              fill=cfg["eye_color"], outline="")
                # Pupille
                c.create_oval(ex - pw + 2, ey - pw + 1,
                              ex + pw - 2, ey + pw - 1,
                              fill="#111111", outline="")
                # Reflet
                c.create_oval(ex - 1, ey - eh + 1, ex + 1, ey - eh + 3,
                              fill="white", outline="")

                # Larme (tristesse)
                if self._emotion == "sad" and self._tick % 90 < 45:
                    tear_y = ey + eh + int((self._tick % 45) * 0.8)
                    c.create_oval(ex - 2, tear_y, ex + 2, tear_y + 5,
                                  fill="#88aaff", outline="")
            else:
                c.create_line(ex - ew, ey, ex + ew, ey,
                              fill="#888888", width=2)

    # ── Bouche ────────────────────────────────────────────────────────────────
    def _draw_mouth(self, c, cx, my, expression, eco):
        """
        La bouche combine la personnalité de base (expression) et l'émotion.
        mouth_curve > 0 → sourire, < 0 → grimace.
        """
        mo    = self._mouth_open
        curve = eco["mouth_curve"]
        skin_shadow = _darken(self.cfg["skin"], 0.55)
        mouth_fill  = "#aa4444"
        mouth_out   = "#884444"

        # ── Bouche ouverte (parole ou surprise) ───────────────────────────────
        if mo > 0.25 or self._emotion == "surprise":
            open_h = int(mo * 14) + (8 if self._emotion == "surprise" else 0)
            open_h = max(4, open_h)
            c.create_arc(cx - 12, my - 4, cx + 12, my + open_h,
                         start=200, extent=140,
                         fill=mouth_fill, outline=mouth_out, width=1)
            # Dents légères
            if mo > 0.5:
                c.create_line(cx - 8, my + 2, cx + 8, my + 2,
                              fill="#f0e8d8", width=2)
            return

        # ── Bouche fermée selon émotion ───────────────────────────────────────
        if curve >= 2:
            # Sourire franc
            c.create_arc(cx - 11, my - 7, cx + 11, my + 7,
                         start=200, extent=140,
                         outline=skin_shadow, width=2, style="arc")
            c.create_arc(cx - 10, my - 5, cx + 10, my + 6,
                         start=200, extent=140,
                         outline=_darken(self.cfg["skin"], 0.50), width=1, style="arc")

        elif curve == 1:
            # Léger sourire
            c.create_arc(cx - 10, my - 4, cx + 10, my + 5,
                         start=200, extent=140,
                         outline=skin_shadow, width=2, style="arc")

        elif curve <= -2:
            # Grimace / tristesse franche
            c.create_arc(cx - 11, my - 3, cx + 11, my + 9,
                         start=20, extent=140,
                         outline=skin_shadow, width=2, style="arc")

        elif curve == -1:
            # Légère insatisfaction
            c.create_arc(cx - 9, my - 2, cx + 9, my + 6,
                         start=20, extent=140,
                         outline=skin_shadow, width=2, style="arc")

        else:
            # Neutre → expression de base du personnage
            if expression == "stern":
                c.create_line(cx - 10, my, cx + 10, my,
                              fill=skin_shadow, width=2)

            elif expression == "analytical":
                c.create_line(cx - 9, my, cx + 9, my,
                              fill=skin_shadow, width=2)
                c.create_line(cx - 9, my + 1, cx - 11, my - 2,
                              fill=skin_shadow, width=1)
                c.create_line(cx + 9, my + 1, cx + 11, my - 2,
                              fill=skin_shadow, width=1)

            elif expression == "smirk":
                c.create_line(cx - 8, my + 1, cx + 2, my,
                              fill=skin_shadow, width=2)
                c.create_line(cx + 2, my, cx + 11, my - 3,
                              fill=skin_shadow, width=2)

            elif expression == "gentle":
                c.create_arc(cx - 10, my - 5, cx + 10, my + 5,
                             start=200, extent=140,
                             outline=skin_shadow, width=2, style="arc")

    # ── Animation de réflexion ────────────────────────────────────────────────
    def _draw_thinking_overlay(self, c, cx, fy):
        """
        Bulle de pensée : 3 points rebondissants + mini-bulle elliptique.
        Apparaît / disparaît en fondu via think_tick.
        """
        t         = self._think_tick
        intensity = min(1.0, t / 20)
        color     = self.data["color"]
        bg        = self.data["bg"]

        # Petite bulle (ellipse)
        bx, by = cx + 16, fy - 44
        c.create_oval(bx - 10, by - 6, bx + 10, by + 6,
                      fill=bg, outline=color, width=1)

        # Trois points animés dans la bulle
        for i in range(3):
            phase  = t * 0.18 + i * 1.1
            dot_y  = by + int(3 * math.sin(phase))
            dot_x  = bx - 6 + i * 6
            r      = 2 if math.sin(phase) > 0 else 1
            dot_c  = color if intensity > 0.5 else _darken(color, 0.6)
            c.create_oval(dot_x - r, dot_y - r, dot_x + r, dot_y + r,
                          fill=dot_c, outline="")

        # Trait de connexion bulle → tête
        c.create_oval(bx - 14, by + 5, bx - 8, by + 9,
                      fill=bg, outline=color, width=1)
        c.create_oval(bx - 17, by + 10, bx - 13, by + 13,
                      fill=bg, outline=color, width=1)

    # ── Sous-routines de dessin (cheveux, features, vêtements) ───────────────
    def _hair_back(self, c, cx, fy, cfg):
        hair = cfg["hair"]
        if self.name == "Lyra":
            pts = [cx-34, fy-26, cx-42, fy+28, cx-22, fy+62,
                   cx+22, fy+62, cx+42, fy+28, cx+34, fy-26]
            c.create_polygon(pts, fill=hair, outline=_darken(hair, 0.75), smooth=True)
        elif self.name == "Elara":
            pts = [cx-30, fy-30, cx-40, fy+18, cx-26, fy+66,
                   cx+26, fy+66, cx+40, fy+18, cx+30, fy-30]
            c.create_polygon(pts, fill=hair, outline=_darken(hair, 0.75), smooth=True)

    def _hair_front(self, c, cx, fy, cfg):
        hair = cfg["hair"]
        if self.name == "Kaelen":
            c.create_arc(cx-28, fy-38, cx+28, fy+4,
                         start=0, extent=180, fill=hair, outline=hair)
            c.create_rectangle(cx-28, fy-38, cx+28, fy-22, fill=hair, outline="")
        elif self.name == "Elara":
            c.create_arc(cx-28, fy-38, cx+28, fy+2,
                         start=0, extent=180, fill=hair, outline=hair)
            c.create_polygon([cx+20, fy-32, cx+38, fy-10, cx+26, fy-6],
                             fill=hair, outline="")
        elif self.name == "Thorne":
            c.create_arc(cx-26, fy-32, cx+26, fy+6,
                         start=0, extent=180, fill=hair, outline=hair)
        elif self.name == "Lyra":
            c.create_arc(cx-28, fy-38, cx+28, fy+2,
                         start=0, extent=180, fill=hair, outline=hair)

    def _draw_feature(self, c, cx, fy, cfg):
        if self.name == "Thorne":
            hc = "#2a1010"
            c.create_polygon([cx-18, fy-30, cx-22, fy-56, cx-12, fy-32],
                             fill=hc, outline=_darken(hc, 0.6), smooth=True)
            c.create_polygon([cx+18, fy-30, cx+22, fy-56, cx+12, fy-32],
                             fill=hc, outline=_darken(hc, 0.6), smooth=True)
        elif self.name == "Lyra":
            r = int(30 + 2.5 * math.sin(self._tick * 0.08))
            glow_color = self.data["color"]
            for i in range(4):
                ri = r + i * 3
                c.create_oval(cx-ri, fy-50-ri//2, cx+ri, fy-50+ri//2,
                              outline=glow_color if i == 0 else _darken(glow_color, 0.5),
                              width=max(1, 3-i))
        elif self.name == "Elara":
            gx, gy   = cx, fy - 24
            pulse    = abs(math.sin(self._tick * 0.07))
            gem_color = cfg["eye_color"] if pulse > 0.5 else _darken(cfg["eye_color"], 0.6)
            c.create_polygon([gx, gy-7, gx-5, gy, gx, gy+4, gx+5, gy],
                             fill=gem_color, outline="white", width=1)
        elif self.name == "Kaelen":
            c.create_line(cx+11, fy-12, cx+17, fy+2,
                          fill=_darken(cfg["skin"], 0.55), width=1)

    def _draw_clothes(self, c, cx, ch):
        name = self.name
        data = self.data
        sy   = ch - 30

        if name == "Kaelen":
            armor = "#708090"
            gold  = "#c0a820"
            c.create_polygon([cx-36, ch, cx-30, sy, cx-10, sy-6,
                               cx+10, sy-6, cx+30, sy, cx+36, ch],
                             fill=armor, outline="#505060")
            for side in [-1, 1]:
                ex = cx + side * 30
                c.create_rectangle(ex - 9*abs(side)//abs(side), sy-6,
                                   ex + 8*abs(side)//abs(side), sy+12,
                                   fill=gold, outline=_darken(gold, 0.7))
            c.create_rectangle(cx-2, sy+3, cx+2, sy+18, fill=gold)
            c.create_rectangle(cx-8, sy+8, cx+8, sy+12, fill=gold)

        elif name == "Elara":
            robe = "#200838"
            c.create_polygon([cx-30, ch, cx-24, sy, cx-8, sy-8,
                               cx+8, sy-8, cx+24, sy, cx+30, ch],
                             fill=robe, outline="#4a2060")
            for i in range(4):
                sx2 = cx - 16 + i * 11
                c.create_text(sx2, sy+14, text="·", fill=data["color"],
                              font=("Arial", 8))
            c.create_text(cx, sy+14, text="✦", fill=data["color"],
                          font=("Arial", 7))

        elif name == "Thorne":
            dark = "#150505"
            c.create_polygon([cx-28, ch, cx-23, sy, cx-8, sy-5,
                               cx+8, sy-5, cx+23, sy, cx+28, ch],
                             fill=dark, outline="#2a0a0a")
            c.create_polygon([cx-30, sy-5, cx-44, ch, cx-24, ch],
                             fill="#100404", outline="#2a0808")

        elif name == "Lyra":
            robe = "#112011"
            c.create_polygon([cx-30, ch, cx-24, sy, cx-8, sy-8,
                               cx+8, sy-8, cx+24, sy, cx+30, ch],
                             fill=robe, outline="#2a5a2a")
            c.create_text(cx, sy+14, text="☀", fill=data["color"],
                          font=("Arial", 11))

    # ── API publique ──────────────────────────────────────────────────────────

    def set_talking(self, talking: bool):
        """Active / désactive le lipsync. Thread-safe."""
        self._talking = talking

    def set_thinking(self, thinking: bool):
        """
        Active l'animation de réflexion (bulle + lueur).
        Appeler set_thinking(True) avant que le LLM génère,
        set_thinking(False) quand la réponse est prête.
        """
        self._thinking = thinking

    def set_emotion(self, emotion: str):
        """
        Définit l'émotion courante.
        Valeurs valides :
          neutral | fear | surprise | disgust | impatient |
          tenderness | happy | sad | angry | focused

        L'émotion revient automatiquement à "neutral" après ≈12 s.
        """
        if emotion not in EMOTION_CONFIG:
            emotion = "neutral"
        if emotion != self._emotion:
            self._prev_emotion  = self._emotion
            self._emotion       = emotion
            self._emotion_blend = 0.0
            self._emotion_timer = EMOTION_DECAY
            if emotion != "neutral":
                self._spawn_emotion_particles()
        else:
            # Renouvelle le timer si même émotion
            self._emotion_timer = EMOTION_DECAY

    def show(self):
        if not self._embedded:
            self.win.deiconify()
            self.win.lift()

    def hide(self):
        if not self._embedded:
            self.win.withdraw()

    def destroy(self):
        self._alive = False
        if not self._embedded:
            try:
                self.win.destroy()
            except Exception:
                pass


# ─── Fabrique ─────────────────────────────────────────────────────────────────
def create_character_faces(
    root: tk.Tk,
    main_x: int,
    main_y: int,
    chat_width: int = 800,
) -> dict:
    """
    Instancie les 4 visages flottants au-dessus du chat.
    Retourne un dict  { "Kaelen": CharacterFaceWindow, ... }
    """
    characters = ["Kaelen", "Elara", "Thorne", "Lyra"]
    gap        = 6
    total_w    = len(characters) * FACE_W + (len(characters) - 1) * gap

    start_x = main_x + max(0, (chat_width - total_w) // 2)
    start_y = main_y - FACE_H - 8
    if start_y < 4:
        start_y = 4

    faces = {}
    for i, name in enumerate(characters):
        x = start_x + i * (FACE_W + gap)
        faces[name] = CharacterFaceWindow(root, name, x, start_y)

    return faces