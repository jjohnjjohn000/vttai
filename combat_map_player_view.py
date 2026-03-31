import tkinter as tk
from PIL import Image, ImageTk

from combat_map_constants import *
from combat_map_constants import _sep, _darken_rgb, _darken_rgb_tuple, _compress_ranges, _C_BG_A, _C_BG_B, _C_FOG_CLEAR, _C_FOG_DM, _C_FOG_PLAYER, _C_GRID, _rgb_to_hex

class PlayerMapView:
    """Fenêtre secondaire lecture-seule : carte avec fog opaque pour les joueurs."""

    def __init__(self, parent, on_close=None):
        self._on_close_cb = on_close
        self._photo       = None

        self.win = tk.Toplevel(parent)
        self.win.title("Vue Joueurs — Carte de Combat")
        self.win.configure(bg="#0a0a14")
        self.win.geometry("900x640")
        self.win.protocol("WM_DELETE_WINDOW", self._close)

        # En-tête
        hdr = tk.Frame(self.win, bg="#0a0a14", pady=6)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="VUE JOUEURS", bg="#0a0a14", fg="#e57373",
                 font=("Consolas", 9, "bold")).pack(side=tk.LEFT, padx=12)
        tk.Label(hdr, text="lecture seule  —  fog opaque",
                 bg="#0a0a14", fg="#333355", font=("Consolas", 8)).pack(side=tk.LEFT)

        # Canvas avec scrollbars
        frame = tk.Frame(self.win, bg="#0a0a14")
        frame.pack(fill=tk.BOTH, expand=True)
        v_sb = tk.Scrollbar(frame, orient=tk.VERTICAL,   bg="#0f0f1a", troughcolor="#0a0a14")
        h_sb = tk.Scrollbar(frame, orient=tk.HORIZONTAL, bg="#0f0f1a", troughcolor="#0a0a14")
        v_sb.pack(side=tk.RIGHT, fill=tk.Y)
        h_sb.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas = tk.Canvas(frame, bg="#0a0a14", highlightthickness=0,
                                yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        v_sb.config(command=self.canvas.yview)
        h_sb.config(command=self.canvas.xview)

        self._img_id = 0
        self._tok_drawn = []

    def refresh(self, bg_pil, fog_mask, cp: int,
                cols: int, rows: int, tokens: list,
                ox: int = 0, oy: int = 0):
        """Reçoit les données du MJ et re-rend la vue joueurs (fog opaque)."""
        if bg_pil is None:
            return

        W, H = cols * cp, rows * cp
        # Redimensionner le fog mask UNE SEULE FOIS (réutilisé pour fog + tokens)
        if fog_mask is not None:
            scaled = fog_mask.resize((W, H), Image.NEAREST)
            fog_arr = np.array(scaled, dtype=np.uint8)
        else:
            fog_arr = np.full((H, W), 255, dtype=np.uint8)
        rgba = np.zeros((H, W, 4), dtype=np.uint8)
        rgba[fog_arr > 0] = _C_FOG_PLAYER
        fog_opaque = Image.fromarray(rgba, "RGBA")

        # Ensure bg_pil matches the computed (W, H) before compositing.
        if bg_pil.size != (W, H):
            bg_pil = bg_pil.resize((W, H), Image.LANCZOS)

        scene = Image.alpha_composite(bg_pil, fog_opaque)
        self._photo = ImageTk.PhotoImage(scene)

        self.canvas.config(scrollregion=(
            min(0, ox), min(0, oy),
            W + max(0, ox) + 40, H + max(0, oy) + 40))

        if self._img_id:
            self.canvas.itemconfig(self._img_id, image=self._photo)
            self.canvas.coords(self._img_id, ox, oy)
        else:
            self._img_id = self.canvas.create_image(
                ox, oy, anchor="nw", image=self._photo, tags=("scene",))

        # Tokens — seulement ceux sur cases révélées
        for iid in self._tok_drawn:
            self.canvas.delete(iid)
        self._tok_drawn.clear()

        for tok in tokens:
            c, r = int(tok["col"]), int(tok["row"])
            if 0 <= r < rows and 0 <= c < cols:
                px = min(int((c + 0.5) * cp), W - 1)
                py = min(int((r + 0.5) * cp), H - 1)
                if fog_arr is None or fog_arr[py, px] <= 127:
                    self._draw_token(tok, cp, ox, oy)

        self.canvas.tag_raise("ptok")

    def _draw_token(self, tok: dict, cp: int, ox: int, oy: int):
        style = TOKEN_STYLES.get(tok["type"], TOKEN_STYLES["hero"])
        cx    = (tok["col"] + 0.5) * cp + ox
        cy    = (tok["row"] + 0.5) * cp + oy
        rad   = cp * 0.40
        name  = tok.get("name", "")

        fill_rgb = (HERO_COLORS.get(name, style["fill"])
                    if tok["type"] == "hero" else style["fill"])
        fill    = _rgb_to_hex(fill_rgb)
        outline = _rgb_to_hex(style["outline"])

        sh = style.get("shape", "circle")
        if sh == "circle":
            iid = self.canvas.create_oval(
                cx-rad, cy-rad, cx+rad, cy+rad,
                fill=fill, outline=outline, width=2, tags="ptok")
        elif sh == "diamond":
            pts = [cx, cy-rad, cx+rad, cy, cx, cy+rad, cx-rad, cy]
            iid = self.canvas.create_polygon(
                pts, fill=fill, outline=outline, width=2, tags="ptok")
        else:
            pts = [cx, cy-rad, cx+rad*0.88, cy+rad*0.75, cx-rad*0.88, cy+rad*0.75]
            iid = self.canvas.create_polygon(
                pts, fill=fill, outline=outline, width=2, tags="ptok")

        self._tok_drawn.append(iid)
        tid = self.canvas.create_text(
            cx, cy, text=(name[:3] if name else tok["type"][:1].upper()),
            fill="white", font=("Consolas", max(7, int(10 * cp / 44)), "bold"),
            tags="ptok")
        self._tok_drawn.append(tid)

        if cp >= 30 and name:
            nlbl = self.canvas.create_text(
                cx, cy + rad + 2, text=name, fill=outline,
                font=("Consolas", max(6, int(7 * cp / 44))),
                anchor="n", tags="ptok")
            self._tok_drawn.append(nlbl)

    def _close(self):
        if self._on_close_cb:
            self._on_close_cb()
        self.win.destroy()


# ─── Point d'entrée ───────────────────────────────────────────────────────────

def open_combat_map(parent, win_state, save_fn, track_fn,
                    msg_queue=None, inject_fn=None, update_sys_prompt_fn=None):
    from combat_map_window import CombatMapWindow
    return CombatMapWindow(parent, win_state=win_state,
                           save_fn=save_fn, track_fn=track_fn,
                           msg_queue=msg_queue, inject_fn=inject_fn,
                           update_sys_prompt_fn=update_sys_prompt_fn)


# ─── Export textuel de la carte pour les agents LLM ──────────────────────────

def get_map_prompt(win_state: dict, for_hero: str = "") -> str:
    """
    Génère une description textuelle de la carte de combat active.
    Lit depuis le fichier JSON de la carte active (nouveau système multi-cartes).
    Rétro-compatible avec l'ancien win_state["combat_map_data"].

    Si for_hero est spécifié, seules les distances de ce héros vers
    tous les autres tokens (ennemis + alliés) sont incluses.
    Sinon, la matrice complète est générée (rétro-compatible).

    Retourne "" si aucune carte n'est chargée ou si elle est vide de tokens.
    1 case = 1,5 m (équivalent D&D 5ft square).
    Les distances sont calculées en distance de Chebyshev (mouvement diagonale libre 5e).
    """
    # ── Nouveau système : lire depuis le fichier de la carte active ───────────
    data = {}
    try:
        active_name = win_state.get("active_map_name", "")
        if active_name:
            import json
            try:
                from app_config import get_campaign_name
                camp_name = get_campaign_name()
            except Exception:
                camp_name = "campagne"
            camp_name = "".join(
                c for c in camp_name if c.isalnum() or c in (" ", "-", "_")
            ).strip() or "campagne"
            safe_name = "".join(
                c for c in active_name if c.isalnum() or c in (" ", "-", "_")
            ).strip() or "carte"
            map_path = os.path.join("campagne", camp_name, "maps", f"{safe_name}.json")
            if os.path.exists(map_path):
                with open(map_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
    except Exception as e:
        print(f"[get_map_prompt] Erreur lecture carte : {e}")

    # ── Fallback rétro-compat ─────────────────────────────────────────────────
    if not data:
        data = win_state.get("combat_map_data", {})

    tokens = data.get("tokens", [])
    if not tokens:
        return ""

    cols = data.get("cols", 30)
    rows = data.get("rows", 20)

    # ── Classification par alignement (prioritaire) puis par type (fallback) ──
    def _is_ally(t):
        a = t.get("alignment", "")
        if a == "ally":
            return True
        if a == "hostile":
            return False
        return t.get("type") == "hero"

    def _is_hostile(t):
        a = t.get("alignment", "")
        if a == "hostile":
            return True
        if a == "ally":
            return False
        return t.get("type") == "monster"

    heroes    = [t for t in tokens if t.get("type") == "hero"]
    allies    = [t for t in tokens if _is_ally(t)]        # héros + PNJ alliés
    enemies   = [t for t in tokens if _is_hostile(t)]     # vrais ennemis
    neutrals  = [t for t in tokens if t.get("alignment") == "neutral" and t.get("type") != "trap"]
    traps     = [t for t in tokens if t.get("type") == "trap"]
    notes     = data.get("notes", [])

    def _coord(t):
        return int(round(t.get("col", 0))), int(round(t.get("row", 0)))

    def _label(t):
        base = t.get("name") or t.get("type", "?")
        alt  = int(t.get("altitude_ft", 0))
        return f"{base} [▲{alt}ft]" if alt > 0 else base

    import math as _math

    def _dist_horiz_ft(t1, t2) -> float:
        """Distance horizontale en pieds (Chebyshev 2D — diagonale libre D&D 5e)."""
        c1, r1 = _coord(t1)
        c2, r2 = _coord(t2)
        return max(abs(c1 - c2), abs(r1 - r2)) * 5.0

    def _dist3d_ft(t1, t2) -> float:
        """Distance 3D vraie en pieds : √(horiz² + Δalt²).
        C'est la distance utilisée pour les portées de sort, attaques à distance,
        et pour déterminer si une attaque de mêlée est possible en vol."""
        horiz = _dist_horiz_ft(t1, t2)
        dalt  = abs(int(t1.get("altitude_ft", 0)) - int(t2.get("altitude_ft", 0)))
        return _math.sqrt(horiz ** 2 + dalt ** 2)

    def _reach_verdict(t1, t2) -> str:
        """Retourne un verdict de portée clair pour deux tokens (incluant altitude)."""
        d3d   = _dist3d_ft(t1, t2)
        horiz = _dist_horiz_ft(t1, t2)
        dalt  = abs(int(t1.get("altitude_ft", 0)) - int(t2.get("altitude_ft", 0)))
        if d3d <= 5.0:
            return "mêlée ✅ (≤5ft 3D)"
        if d3d <= 10.0:
            return "mêlée Reach ✅ (≤10ft 3D)"
        return f"portée distance 🏹 ({d3d:.0f}ft 3D)"

    lines = [
        f"\n\n🗺️ ═══ CARTE DE COMBAT ({cols}×{rows} cases — 1 case = 5ft) ═══",
        "  • L'axe des Colonnes (Col) va de GAUCHE (1) vers la DROITE (est).",
        "  • L'axe des Rangées/Lignes (Lig) va du HAUT (1) vers le BAS (sud).",
        "  • Les distances intègrent l'ALTITUDE (vol 3D) : dist_3D = √(horiz²+Δalt²).",
        "  • Portée de mêlée : ≤5ft en 3D. Reach : ≤10ft en 3D.",
        "  • Un token en vol ne peut être attaqué en mêlée que si la dist 3D ≤ 5ft (ou 10ft Reach).",
    ]

    # ── Positions des alliés (héros + PNJ alliés) ─────────────────────────────
    if allies:
        lines.append("\n🔵 ALLIÉS — positions :")
        for h in allies:
            c, r = _coord(h)
            alt   = int(h.get("altitude_ft", 0))
            tag   = " (PNJ)" if h.get("type") != "hero" else ""
            if alt > 0:
                alt_s = f"  ✈ EN VOL — altitude : {alt}ft ({alt//5} cases au-dessus du sol)"
            else:
                alt_s = "  [au sol]"
            lines.append(f"  • {_label(h)}{tag} → Col {c+1}, Lig {r+1}{alt_s}")

    # ── Positions des ennemis ──────────────────────────────────────────────────
    if enemies:
        lines.append("\n🔴 ENNEMIS — positions :")
        for m in enemies:
            c, r = _coord(m)
            alt   = int(m.get("altitude_ft", 0))
            if alt > 0:
                alt_s = f"  ✈ EN VOL — altitude : {alt}ft ({alt//5} cases au-dessus du sol)"
            else:
                alt_s = "  [au sol]"
            lines.append(f"  • {_label(m)} → Col {c+1}, Lig {r+1}{alt_s}")

    # ── Positions des neutres ─────────────────────────────────────────────────
    if neutrals:
        lines.append("\n🟡 NEUTRES — positions :")
        for n in neutrals:
            c, r = _coord(n)
            alt   = int(n.get("altitude_ft", 0))
            alt_s = f"  ✈ EN VOL — altitude : {alt}ft" if alt > 0 else "  [au sol]"
            lines.append(f"  • {_label(n)} → Col {c+1}, Lig {r+1}{alt_s}")

    # ── Pièges / éléments spéciaux ────────────────────────────────────────────
    if traps:
        lines.append("\n⚠️ PIÈGES / ZONES :")
        for tr in traps:
            c, r = _coord(tr)
            lines.append(f"  • {_label(tr)} → Col {c+1}, Lig {r+1}")

    # ── Distances héros ↔ ennemis (3D complètes) ──────────────────────────────
    if allies and enemies:
        if for_hero:
            # Mode personnalisé : distances du héros spécifié uniquement
            hero_token = next((h for h in allies if (_label(h).split(' [')[0]).lower() == for_hero.lower()
                               or (h.get('name', '')).lower() == for_hero.lower()), None)
            if hero_token:
                h_alt = int(hero_token.get("altitude_ft", 0))
                lines.append(f"\n📏 TES DISTANCES → ENNEMIS (distances 3D — altitude incluse) :")
                sorted_enemies = sorted(enemies, key=lambda m: _dist3d_ft(hero_token, m))
                for m in sorted_enemies[:6]:
                    horiz = _dist_horiz_ft(hero_token, m)
                    dalt  = abs(h_alt - int(m.get("altitude_ft", 0)))
                    d3d   = _dist3d_ft(hero_token, m)
                    verdict = _reach_verdict(hero_token, m)
                    if dalt == 0:
                        breakdown = f"{horiz:.0f}ft horiz, même altitude"
                    else:
                        breakdown = f"{horiz:.0f}ft horiz + {dalt}ft vertical = {d3d:.0f}ft 3D"
                    lines.append(f"  → {_label(m)} : {breakdown} — {verdict}")
        else:
            # Mode global (rétro-compatible)
            lines.append("\n📏 DISTANCES HÉROS → ENNEMIS (distances 3D — altitude incluse) :")
            for h in allies:
                sorted_enemies = sorted(enemies, key=lambda m: _dist3d_ft(h, m))
                h_alt = int(h.get("altitude_ft", 0))
                lines.append(f"  ── {_label(h)} ({'vol' if h_alt else 'sol'}) ──")
                for m in sorted_enemies[:4]:
                    horiz = _dist_horiz_ft(h, m)
                    dalt  = abs(h_alt - int(m.get("altitude_ft", 0)))
                    d3d   = _dist3d_ft(h, m)
                    verdict = _reach_verdict(h, m)
                    if dalt == 0:
                        breakdown = f"{horiz:.0f}ft horiz, même altitude"
                    else:
                        breakdown = f"{horiz:.0f}ft horiz + {dalt}ft vertical = {d3d:.0f}ft 3D"
                    lines.append(f"    → {_label(m)} : {breakdown} — {verdict}")

    # ── Distances héros ↔ alliés (3D) ─────────────────────────────────────────
    if len(allies) >= 2:
        if for_hero:
            hero_token = next((h for h in allies if (_label(h).split(' [')[0]).lower() == for_hero.lower()
                               or (h.get('name', '')).lower() == for_hero.lower()), None)
            if hero_token:
                other_allies = [h for h in allies if h is not hero_token]
                if other_allies:
                    lines.append(f"\n🤝 TES DISTANCES → ALLIÉS (3D) :")
                    for ally in sorted(other_allies, key=lambda a: _dist3d_ft(hero_token, a)):
                        horiz = _dist_horiz_ft(hero_token, ally)
                        dalt  = abs(int(hero_token.get("altitude_ft", 0)) - int(ally.get("altitude_ft", 0)))
                        d3d   = _dist3d_ft(hero_token, ally)
                        if dalt == 0:
                            breakdown = f"{horiz:.0f}ft"
                        else:
                            breakdown = f"{horiz:.0f}ft horiz + {dalt}ft vertical = {d3d:.0f}ft 3D"
                        verdict = _reach_verdict(hero_token, ally)
                        lines.append(f"  → {_label(ally)} : {breakdown} — {verdict}")
        else:
            lines.append("\n🤝 DISTANCES ENTRE ALLIÉS (3D) :")
            for i, h1 in enumerate(allies):
                for h2 in allies[i + 1:]:
                    horiz = _dist_horiz_ft(h1, h2)
                    dalt  = abs(int(h1.get("altitude_ft", 0)) - int(h2.get("altitude_ft", 0)))
                    d3d   = _dist3d_ft(h1, h2)
                    if dalt == 0:
                        breakdown = f"{horiz:.0f}ft"
                    else:
                        breakdown = f"{horiz:.0f}ft horiz + {dalt}ft vertical = {d3d:.0f}ft 3D"
                    verdict = _reach_verdict(h1, h2)
                    lines.append(f"  • {_label(h1)} ↔ {_label(h2)} : {breakdown} — {verdict}")

    # ── Notes de carte visibles ────────────────────────────────────────────────
    if notes:
        note_texts = [n.get("text", "").strip() for n in notes if n.get("text", "").strip()]
        if note_texts:
            lines.append("\n📌 NOTES SUR LA CARTE :")
            for nt in note_texts[:6]:   # max 6 pour ne pas surcharger
                lines.append(f"  • {nt}")

    # ── Portes (état réel — priorité sur l'image) ─────────────────────────────
    doors = data.get("doors", [])
    if doors:
        lines.append("\n🚪 PORTES — état réel (priorité absolue sur l'image de fond) :")
        lines.append("  ⚠ L'image peut montrer un état différent — ces données font foi.")
        for d in doors:
            state = "OUVERTE" if d.get("open") else "FERMÉE"
            label = f" ({d['label']})" if d.get("label") else ""
            override = ("l'image montre une porte fermée — elle est en réalité OUVERTE"
                        if d.get("open")
                        else "l'image montre une porte ouverte — elle est en réalité FERMÉE")
            lines.append(
                f"  • Col {d['col']+1}, Lig {d['row']+1}{label} : {state} — {override}")

    # ── Obstacles / zones bloquées ────────────────────────────────────────────
    obstacles = data.get("obstacles", [])
    if obstacles:
        lines.append("\n🧱 OBSTACLES / ZONES BLOQUÉES :")
        lines.append("  ⚠ Ces zones sont physiquement bloquées — mouvement et ligne de vue impossibles.")
        for obs in obstacles:
            pts   = obs.get("pts", [])
            label = obs.get("label", "")
            label_txt = f" « {label} »" if label else ""
            if pts:
                # Calcule la case centrale approximative
                avg_x = sum(p[0] for p in pts) / len(pts)
                avg_y = sum(p[1] for p in pts) / len(pts)
                # Bounding box en cases
                min_c = int(min(p[0] for p in pts) / 44)
                max_c = int(max(p[0] for p in pts) / 44)
                min_r = int(min(p[1] for p in pts) / 44)
                max_r = int(max(p[1] for p in pts) / 44)
                lines.append(
                    f"  • Obstacle{label_txt} — cases Col {min_c+1}–{max_c+1}, "
                    f"Lig {min_r+1}–{max_r+1} : PASSAGE BLOQUÉ")

    lines.append(
        "\nUtilise ces positions pour décider de ton mouvement et de ta portée d'attaque."
    )

    return "\n".join(lines)