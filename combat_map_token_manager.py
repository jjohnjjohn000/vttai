import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox
import os
import tempfile
import base64
try:
    import numpy as np
    from PIL import Image, ImageTk, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from combat_map_constants import *
from combat_map_constants import _sep, _darken_rgb, _darken_rgb_tuple, _compress_ranges, _C_BG_A, _C_BG_B, _C_FOG_CLEAR, _C_FOG_DM, _C_FOG_PLAYER, _C_GRID, _rgb_to_hex

# ─── Portrait image cache ──────────────────────────────────────────────────────
# Clé : (chemin_absolu, diamètre_pixels) → ImageTk.PhotoImage
# Évite de recharger et de re-rogner l'image PIL à chaque redraw.
# Les PhotoImage stockés ici ne sont jamais collectés par le GC.
_PORTRAIT_CACHE: dict[tuple, object] = {}

# ─── Cache d'images d'Aura ───────────────────────────────────────────────────
_AURA_CACHE = {}

def _get_aura_image(color_hex: str, diameter_px: int):
    """
    Génère une image PIL avec un vrai canal alpha (transparence douce) pour l'aura.
    Mise en cache pour les performances (évite de recalculer à chaque zoom).
    """
    if not PIL_AVAILABLE or diameter_px < 2:
        return None
        
    key = (color_hex, diameter_px)
    if key in _AURA_CACHE:
        return _AURA_CACHE[key]
        
    try:
        from PIL import Image, ImageTk, ImageDraw
        
        # Créer une image vide avec fond transparent (RGBA)
        img = Image.new('RGBA', (diameter_px, diameter_px), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Convertir la couleur HEX (ex: "#00ccff") en RGB
        h = color_hex.lstrip('#')
        r, g, b = (int(h[i:i+2], 16) for i in (0, 2, 4)) if len(h) == 6 else (0, 255, 255)
        
        # --- RÉGLAGE DE LA TRANSPARENCE ---
        # 255 = totalement opaque | 0 = invisible.
        # 40 donne une couleur très douce et moderne (~15% d'opacité).
        alpha = 40 
        
        # Dessiner le cercle avec la couleur et l'opacité alpha
        draw.ellipse((0, 0, diameter_px-1, diameter_px-1), fill=(r, g, b, alpha))
        
        photo = ImageTk.PhotoImage(img)
        _AURA_CACHE[key] = photo
        
        # Nettoyage automatique du cache si on zoom/dézoom trop (protection RAM)
        if len(_AURA_CACHE) > 150:
            for k in list(_AURA_CACHE.keys())[:50]:
                del _AURA_CACHE[k]
                
        return photo
    except Exception as e:
        print(f"[Aura] Erreur création image transparente : {e}")
        return None

def _make_circular_portrait(path: str, diameter: int):
    """
    Charge l'image au chemin donné, la redimensionne à `diameter`×`diameter`
    pixels et lui applique un masque circulaire (fond transparent).

    Retourne un ImageTk.PhotoImage prêt à être affiché sur un Canvas Tkinter,
    ou None si PIL n'est pas disponible ou si le fichier est introuvable.

    Le résultat est mis en cache dans _PORTRAIT_CACHE pour des raisons de
    performance — les appels suivants avec les mêmes paramètres sont O(1).
    """
    if not PIL_AVAILABLE:
        return None
    if not path or not os.path.exists(path):
        return None

    key = (path, diameter)
    if key in _PORTRAIT_CACHE:
        return _PORTRAIT_CACHE[key]

    try:
        from PIL import Image, ImageTk, ImageDraw
        img = Image.open(path).convert("RGBA")

        # Rogner en carré centré avant le redimensionnement (évite la distorsion)
        w, h = img.size
        if w != h:
            side = min(w, h)
            left = (w - side) // 2
            top  = (h - side) // 2
            img  = img.crop((left, top, left + side, top + side))

        img = img.resize((diameter, diameter), Image.LANCZOS)

        # Masque circulaire
        mask = Image.new("L", (diameter, diameter), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
        img.putalpha(mask)

        photo = ImageTk.PhotoImage(img)
        _PORTRAIT_CACHE[key] = photo
        return photo

    except Exception as e:
        print(f"[PortraitToken] Erreur chargement portrait '{path}' : {e}")
        return None


def resolve_missing_token_portraits(tokens: list) -> None:
    """
    Parcourt une liste de dicts token et résout le portrait de chacun
    via portrait_resolver si la clé 'portrait' est absente ou invalide.

    À appeler depuis le chargement de l'état de la carte (wherever
    self.tokens est peuplé depuis le JSON sauvegardé) pour s'assurer
    que les tokens chargés depuis une ancienne sauvegarde obtiennent
    également leur portrait automatiquement.

    Exemple d'utilisation dans le state loader :
        from combat_map_token_manager import resolve_missing_token_portraits
        self.tokens = loaded_tokens
        resolve_missing_token_portraits(self.tokens)
    """
    try:
        import re
        from portrait_resolver import resolve_token_art, resolve_portrait
    except ImportError:
        return

    for tok in tokens:
        # Clé de résolution : source_name (nom de fichier canonique) en priorité,
        # sinon le nom affiché sans suffixe numérique.
        _src  = tok.get("source_name", "").strip()
        _name = tok.get("name", "")
        base  = _src if _src else re.sub(r"\s+\d+\s*$", "", _name).strip()

        # token_art : image canvas (images/tokens/ en priorité)
        if not (tok.get("token_art") and os.path.exists(tok.get("token_art", ""))):
            if base:
                art = resolve_token_art(base)
                if art:
                    tok["token_art"] = art

        # portrait : image brute pour les tooltips (images/portraits/ uniquement)
        if not (tok.get("portrait") and os.path.exists(tok.get("portrait", ""))):
            if base:
                path = resolve_portrait(base)
                if path:
                    tok["portrait"] = path


class TokenManagerMixin:
    pass
    # ─── Tokens ───────────────────────────────────────────────────────────────

    @staticmethod
    def _tok_fingerprint(tok: dict, zoom: float, cp: int, sel: set) -> tuple:
        """Hashable fingerprint of a token's visual state."""
        return (
            tok.get("col"), tok.get("row"), tok.get("type"),
            tok.get("name", ""), tok.get("size", 1),
            tok.get("hp", -1), tok.get("max_hp", -1),
            tuple(tok.get("conditions", [])),
            tok.get("altitude_ft", 0),
            tok.get("aura_radius", 0),
            tok.get("aura_color", ""),
            tok.get("source_name", ""),  # source name — redraw if it changes
            tok.get("token_art", ""),    # token art path — redraw if it changes
            tok.get("portrait", ""),     # portrait path — redraw if it changes
            zoom, cp,
            id(tok) in sel,
        )

    def _tok_is_visible_for_players(self, tok: dict) -> bool:
        """En mode vue joueurs, un token ennemi/neutre est visible seulement si
        sa case est révélée dans le fog mask. Les alliés sont toujours visibles."""
        alignment = tok.get("alignment", "")
        tok_type  = tok.get("type", "")
        if alignment == "ally" or (alignment == "" and tok_type == "hero"):
            return True   # héros + PNJ alliés toujours visibles
        # Ennemis/neutres/pièges : vérifier le fog mask
        if self._fog_mask is None:
            return False  # pas de masque = tout caché
        c = int(round(tok.get("col", 0)))
        r = int(round(tok.get("row", 0)))
        mW, mH = self._fog_mask.size
        if mW == 0 or mH == 0:
            return False
        fpx = min(max(0, int((c + 0.5) * mW / self.cols)), mW - 1)
        fpy = min(max(0, int((r + 0.5) * mH / self.rows)), mH - 1)
        val = self._fog_mask.getpixel((fpx, fpy))
        return val <= 127  # 0 = révélé, 255 = brouillard

    def set_view_mode(self, dm_view: bool):
        """Bascule entre vue MJ (dm_view=True) et vue joueurs (dm_view=False).

        Invalide le cache fingerprint de tous les tokens pour forcer leur
        re-rendu immédiat selon la nouvelle visibilité — sans attendre un
        zoom ou un scroll.
        """
        if getattr(self, "_dm_view", True) == dm_view:
            return  # déjà dans le bon mode, rien à faire
        self._dm_view = dm_view
        # Invalider le cache visuel de chaque token pour que _redraw_all_tokens
        # ne les saute pas avec le raccourci « fingerprint inchangé ».
        for tok in getattr(self, "tokens", []):
            tok.pop("_fp", None)
        self._redraw_all_tokens()

    def _redraw_all_tokens(self):
        for tok in self.tokens:
            # ── Mode vue joueurs (fenêtre principale) : cacher ennemis dans le fog ──
            if not getattr(self, "_dm_view", True):
                if not self._tok_is_visible_for_players(tok):
                    # Effacer le token s'il était affiché
                    for iid in tok.get("ids", ()):
                        self.canvas.delete(iid)
                    tok.pop("ids", None)
                    tok.pop("_fp", None)
                    continue
            fp = self._tok_fingerprint(tok, self.zoom, self._cp,
                                       self._selected_tokens)
            old_fp = tok.get("_fp")
            if old_fp == fp and tok.get("ids"):
                continue  # unchanged — skip
            # Dirty — delete old items and redraw
            for iid in tok.get("ids", ()):
                self.canvas.delete(iid)
            tok.pop("ids", None)
            self._draw_one_token(tok)
            tok["_fp"] = fp

    def _draw_one_token(self, tok: dict):
        import math
        style = TOKEN_STYLES.get(tok["type"], TOKEN_STYLES["hero"])
        cp    = self._cp
        size  = float(tok.get("size", 1))
        alt   = int(tok.get("altitude_ft", 0))   # altitude en pieds D&D (0 = sol)
        flying = alt > 0
        tag     = f"tok_{id(tok)}"
        ids     = []

        # ── Cas spécial : Carré de prévisualisation (Movement preview) ────────
        if tok.get("is_preview", False):
            base_cx = tok["col"] * cp
            base_cy = tok["row"] * cp
            sw = cp * size
            
            # Un carré avec bordure pointillée animée ou de couleur vive
            ids.append(self.canvas.create_rectangle(
                base_cx, base_cy, base_cx + sw, base_cy + sw,
                outline="#4fc3f7", width=3, dash=(6, 4), fill="#4fc3f7", stipple="gray25",
                tags=("token", tag)
            ))
            tok["ids"] = tuple(ids)
            for iid in ids:
                self.canvas.tag_bind(iid, "<ButtonPress-1>",
                                      lambda e, t=tok: self._tok_press(e, t))
                self.canvas.tag_bind(iid, "<Enter>",
                                      lambda e, t=tok: self._tok_enter(e, t))
                self.canvas.tag_bind(iid, "<Leave>",
                                      lambda e, t=tok: self._tok_leave(e, t))
            return

        # ── Centre de base du token (case grille) ─────────────────────────────
        base_cx = (tok["col"] + size / 2) * cp
        base_cy = (tok["row"] + size / 2) * cp
        rad     = cp * size * 0.40

        # ── Décalage vertical isométrique-lite ────────────────────────────────
        # Le token "lévite" au-dessus de son ombre : 0.4px par pied, plafonné à rad*1.2
        lift_px = min(alt * 0.4 * self.zoom, rad * 1.2) if flying else 0.0
        cx = base_cx
        cy = base_cy - lift_px   # token levé vers le haut du canvas

        name  = tok.get("name", "")
        fill_rgb = (HERO_COLORS.get(name, style["fill"])
                    if tok["type"] == "hero" else style["fill"])
        fill    = _rgb_to_hex(fill_rgb)
        outline = _rgb_to_hex(style["outline"])
        tag     = f"tok_{id(tok)}"
        ids     = []

        # ── Aura du token ─────────────────────────────────────────────────────
        aura_radius = float(tok.get("aura_radius", 0))
        if aura_radius > 0:
            aura_color = tok.get("aura_color", "#00ffff")
            aura_px = (aura_radius / 5.0 + size / 2.0) * cp
            
            # Utilisation de l'image transparente (si PIL est installé)
            if PIL_AVAILABLE:
                diameter = int(aura_px * 2)
                aura_img = _get_aura_image(aura_color, diameter)
                if aura_img:
                    # Garder une référence dans le dico du token pour éviter que 
                    # le ramasse-miettes (Garbage Collector) de Python ne l'efface
                    tok["_aura_photo"] = aura_img  
                    
                    ids.append(self.canvas.create_image(
                        base_cx, base_cy, image=aura_img,
                        anchor="center", state=tk.DISABLED, tags=("token", "aura", tag)))
            else:
                # Fallback de sécurité (stipple) si la librairie PIL venait à manquer
                ids.append(self.canvas.create_oval(
                    base_cx - aura_px, base_cy - aura_px,
                    base_cx + aura_px, base_cy + aura_px,
                    fill=aura_color, outline="", stipple="gray12",
                    state=tk.DISABLED, tags=("token", "aura", tag)))
            
            # Bordure pointillée de l'aura
            ids.append(self.canvas.create_oval(
                base_cx - aura_px, base_cy - aura_px,
                base_cx + aura_px, base_cy + aura_px,
                outline=aura_color, width=2, dash=(4, 4), fill="",
                state=tk.DISABLED, tags=("token", "aura", tag)))

        # ── Ombre au sol (projeté sous le token) ──────────────────────────────
        if flying:
            sh_rx = rad * 0.85           # ellipse légèrement aplatie
            sh_ry = rad * 0.30
            # transparence via stipple : gray25 = très transparent
            ids.append(self.canvas.create_oval(
                base_cx - sh_rx, base_cy - sh_ry,
                base_cx + sh_rx, base_cy + sh_ry,
                fill="#000000", outline=outline,
                stipple="gray25", width=1,
                tags=("token", "tok_shadow", tag)))
            # Ligne verticale de "fil" reliant l'ombre au token
            if lift_px > rad * 0.4:
                ids.append(self.canvas.create_line(
                    base_cx, base_cy - sh_ry,
                    cx, cy + rad,
                    fill=outline, width=1, dash=(3, 4),
                    tags=("token", tag)))

        # ── Anneau de sélection ────────────────────────────────────────────────
        sel_col = "#ffffff" if id(tok) in self._selected_tokens else ""
        ids.append(self.canvas.create_oval(
            cx-rad-5, cy-rad-5, cx+rad+5, cy+rad+5,
            outline=sel_col, width=2, fill="", dash=(4, 3),
            tags=("token", "sel_ring", tag)))

        # Halo externe coloré selon l'alignement du token
        _ALIGN_COLORS = {
            "hostile": "#e53935",   # rouge
            "neutral": "#fdd835",   # jaune
            "ally":    "#43a047",   # vert
        }
        alignment     = tok.get("alignment", "")
        align_color   = _ALIGN_COLORS.get(alignment, outline)
        align_width   = 2 if alignment in _ALIGN_COLORS else 1
        ids.append(self.canvas.create_oval(
            cx-rad-3, cy-rad-3, cx+rad+3, cy+rad+3,
            outline=align_color, width=align_width, fill="",
            tags=("token", "align_ring", tag)))

        # ── Corps du token ────────────────────────────────────────────────────
        # Stipple gray50 si en vol → aspect semi-transparent
        stipple_val = "gray50" if flying else ""

        # Art du token sur le canvas : images/tokens/ en priorité.
        # Utilise source_name (nom canonique du fichier, ex. "Rictavio") si dispo,
        # sinon le nom affiché sans suffixe numérique.
        # Si token_art n'est pas encore résolu, on le résout et on le met en cache.
        portrait_path = tok.get("token_art", "")
        if not portrait_path or not os.path.exists(portrait_path):
            try:
                import re as _re
                from portrait_resolver import resolve_token_art
                _src = tok.get("source_name", "").strip()
                base = _src if _src else                        _re.sub(r"\s+\d+\s*$", "", tok.get("name", "")).strip()
                portrait_path = resolve_token_art(base)
                if portrait_path:
                    tok["token_art"] = portrait_path   # cache pour les prochains redraws
            except Exception:
                portrait_path = ""
        diameter_px   = max(4, int(rad * 2))
        portrait_photo = (
            _make_circular_portrait(portrait_path, diameter_px)
            if portrait_path else None
        )

        sh = style.get("shape", "circle")
        if sh == "circle":
            ids.append(self.canvas.create_oval(
                cx-rad, cy-rad, cx+rad, cy+rad,
                fill=fill, outline=outline, width=2,
                stipple=stipple_val,
                tags=("token", tag)))
        elif sh == "diamond":
            pts = [cx, cy-rad, cx+rad, cy, cx, cy+rad, cx-rad, cy]
            ids.append(self.canvas.create_polygon(
                pts, fill=fill, outline=outline, width=2,
                stipple=stipple_val,
                tags=("token", tag)))
        else:
            pts = [cx, cy-rad, cx+rad*0.88, cy+rad*0.75, cx-rad*0.88, cy+rad*0.75]
            ids.append(self.canvas.create_polygon(
                pts, fill=fill, outline=outline, width=2,
                stipple=stipple_val,
                tags=("token", tag)))

        # ── Portrait (par-dessus le fond coloré) ─────────────────────────────
        if portrait_photo is not None:
            # Maintenir une référence sur le tok pour éviter le GC
            tok["_portrait_photo"] = portrait_photo
            img_id = self.canvas.create_image(
                cx, cy, image=portrait_photo,
                anchor="center",
                tags=("token", "portrait", tag))
            ids.append(img_id)
            # Réduire le stipple sur l'image en vol via un rectangle semi-transparent
            # (la transparence PNG suffit à montrer le "vol", pas besoin de stipple image)

        # ── Texte du token ────────────────────────────────────────────────────
        # Initiales uniquement quand pas de portrait (ou token trop petit)
        fs = max(7, int(10 * self.zoom * size))
        show_initials = portrait_photo is None or diameter_px < 20
        if show_initials:
            ids.append(self.canvas.create_text(
                cx, cy, text=(name[:3] if name else tok["type"][:1].upper()),
                fill="white", font=("Consolas", fs, "bold"), tags=("token", tag)))

        # Nom sous le token (toujours affiché à zoom suffisant)
        if self.zoom >= 0.55 and name:
            ids.append(self.canvas.create_text(
                cx, cy + rad + 2, text=name, fill=outline,
                font=("Consolas", max(6, int(7 * self.zoom * size))),
                anchor="n", tags=("token", tag)))

        # ── Badge altitude ▲ Nft ──────────────────────────────────────────────
        if flying and self.zoom >= 0.35:
            badge_fs = max(6, int(7 * self.zoom))
            badge_txt = f"▲{alt}ft"
            # Fond noir semi-transparent derrière le badge
            ids.append(self.canvas.create_text(
                cx + rad + 2, cy - rad + 2,
                text=badge_txt,
                fill="#00ccff",
                font=("Consolas", badge_fs, "bold"),
                anchor="nw",
                tags=("token", tag)))

        # ── Textes (PV et AC) ─────────────────────────────────────────────────
        tok_ac = tok.get("ac", -1)
        hp     = tok.get("hp", -1)
        max_hp = tok.get("max_hp", -1)
        has_hp = hp >= 0 and max_hp > 0
        has_ac = tok_ac > 0

        bar_h = max(3, int(cp * 0.10)) if has_hp else 0
        by0   = cy - rad - bar_h - 2

        # Rendre la jauge de vie
        if has_hp:
            bar_w = rad * 2
            bx0   = cx - rad
            by1   = by0 + bar_h
            ids.append(self.canvas.create_rectangle(
                bx0, by0, bx0 + bar_w, by1,
                fill="#333333", outline="", tags=("token", tag)))
            ratio = max(0.0, min(1.0, hp / max_hp))
            bar_color = (
                "#4caf50" if ratio > 0.5 else
                "#ff9800" if ratio > 0.25 else
                "#f44336"
            )
            if ratio > 0:
                ids.append(self.canvas.create_rectangle(
                    bx0, by0, bx0 + bar_w * ratio, by1,
                    fill=bar_color, outline="", tags=("token", tag)))

        # Rendre les textes si on est assez zoomé
        if cp >= 20 and self.zoom >= 0.5:
            text_str = ""
            if has_hp:
                text_str += f"{hp}/{max_hp}"
            if has_ac:
                if text_str:
                    text_str += " | "
                text_str += f"🛡️{tok_ac}"
            
            if text_str:
                # Un petit fond ombré pour la lisibilité
                fs = max(6, int(8 * self.zoom))
                text_y = by0 - fs + 2
                
                # Ombre
                ids.append(self.canvas.create_text(
                    cx + 1, text_y + 1,
                    text=text_str, fill="black",
                    font=("Consolas", fs, "bold"),
                    tags=("token", tag)))
                # Texte
                ids.append(self.canvas.create_text(
                    cx, text_y,
                    text=text_str, fill="white",
                    font=("Consolas", fs, "bold"),
                    tags=("token", tag)))

        # ── Badges de conditions ───────────────────────────────────────────────
        conditions = tok.get("conditions", [])
        if conditions and self.zoom >= 0.4:
            badge_r = max(4, int(cp * 0.13))
            import math as _m
            arc_r = rad + badge_r + 2
            for i, cond in enumerate(conditions):
                angle_deg = 270 + i * 360 / len(conditions) if len(conditions) > 1 else 270
                angle_rad = _m.radians(angle_deg)
                bx = cx + arc_r * _m.cos(angle_rad)
                by = cy + arc_r * _m.sin(angle_rad)
                cond_col = DND_CONDITIONS.get(cond, "#aaaaaa")
                ids.append(self.canvas.create_oval(
                    bx - badge_r, by - badge_r, bx + badge_r, by + badge_r,
                    fill=cond_col, outline="#ffffff", width=1,
                    tags=("token", tag)))
                if badge_r >= 7:
                    ids.append(self.canvas.create_text(
                        bx, by, text=cond[:1], fill="#000000",
                        font=("Consolas", max(5, badge_r - 2), "bold"),
                        tags=("token", tag)))

        # ── Badges de statuts tactiques (anneau interne) ──────────────────────
        tactics = tok.get("tactics", [])
        if tactics and self.zoom >= 0.4:
            badge_r = max(4, int(cp * 0.09))
            import math as _m
            arc_r = max(2, rad - badge_r - 2)
            for i, tac in enumerate(tactics):
                angle_deg = 270 + i * 360 / len(tactics) if len(tactics) > 1 else 270
                angle_rad = _m.radians(angle_deg)
                bx = cx + arc_r * _m.cos(angle_rad)
                by = cy + arc_r * _m.sin(angle_rad)
                tac_col = DND_TACTICS.get(tac, "#aaaaaa")
                ids.append(self.canvas.create_oval(
                    bx - badge_r, by - badge_r, bx + badge_r, by + badge_r,
                    fill=tac_col, outline="#ffffff", width=1, stipple="gray50",
                    tags=("token", tag)))
                if badge_r >= 6:
                    ids.append(self.canvas.create_text(
                        bx, by, text=tac[:1], fill="#000000",
                        font=("Consolas", max(5, badge_r - 1), "bold"),
                        tags=("token", tag)))

        tok["ids"] = tuple(ids)
        for iid in ids:
            self.canvas.tag_bind(iid, "<ButtonPress-1>",
                                  lambda e, t=tok: self._tok_press(e, t))
            self.canvas.tag_bind(iid, "<Enter>",
                                  lambda e, t=tok: self._tok_enter(e, t))
            self.canvas.tag_bind(iid, "<Leave>",
                                  lambda e, t=tok: self._tok_leave(e, t))

    def _redraw_one_token(self, tok: dict):
        for iid in tok.get("ids", ()):
            self.canvas.delete(iid)
        tok.pop("ids", None)
        self._draw_one_token(tok)

    def spawn_floating_text(self, tok: dict, text: str, color: str = "#ff5252"):
        """Affiche un texte flottant animé au-dessus du token."""
        if getattr(self, "canvas", None) is None: return
        cp = getattr(self, "_cp", 50)
        zoom = getattr(self, "zoom", 1.0)
        size = float(tok.get("size", 1))
        
        # Position de départ
        cx = (tok.get("col", 0) + size / 2.0) * cp
        cy = (tok.get("row", 0) + size / 2.0) * cp - (cp * size * 0.40) - 20
        
        fs = max(14, int(18 * zoom))
        
        # Ombre pour lisibilité
        sh_id = self.canvas.create_text(
            cx + 2, cy + 2, text=text, fill="#000000",
            font=("Consolas", fs, "bold"),
            tags=("floating_text",)
        )
        text_id = self.canvas.create_text(
            cx, cy, text=text, fill=color,
            font=("Consolas", fs, "bold"),
            tags=("floating_text",)
        )
        
        # S'assurer que le texte est au-dessus de tout
        self.canvas.tag_raise("floating_text")
        
        def _animate(step_=0, max_steps_=40):
            if not self.canvas.winfo_exists(): return
            if step_ >= max_steps_:
                self.canvas.delete(sh_id)
                self.canvas.delete(text_id)
                return
            self.canvas.move(sh_id, 0, -2)
            self.canvas.move(text_id, 0, -2)
            self.canvas.after(30, _animate, step_ + 1, max_steps_)
            
        _animate()

    # ─── Actions sur tokens individuels ──────────────────────────────────────

    def _rename_token(self, tok):
        new_name = simpledialog.askstring(
            "Renommer le token", "Nouveau nom :",
            initialvalue=tok.get("name", ""), parent=self.win)
        if new_name is not None and new_name.strip():
            tok["name"] = new_name.strip()
            self._redraw_one_token(tok)
            self._save_state()

    def _teleport_token(self, tok):
        col = simpledialog.askinteger(
            "Déplacer token", f"Colonne (1–{self.cols}) :",
            initialvalue=int(tok["col"]) + 1,
            minvalue=1, maxvalue=self.cols, parent=self.win)
        if col is None:
            return
        row = simpledialog.askinteger(
            "Déplacer token", f"Ligne (1–{self.rows}) :",
            initialvalue=int(tok["row"]) + 1,
            minvalue=1, maxvalue=self.rows, parent=self.win)
        if row is None:
            return
        old_col, old_row = int(tok["col"]), int(tok["row"])
        tok["col"] = col - 1
        tok["row"] = row - 1
        self._redraw_one_token(tok)
        self._save_state()
        if not getattr(getattr(self, "app", None), "_session_paused", False):
            self._notify_token_moved(tok.get("name", "?"), tok["type"],
                                     old_col, old_row, col - 1, row - 1,
                                     alignment=tok.get("alignment", ""))

    def _delete_single_token(self, tok):
        name = tok.get("name", "?")
        for iid in tok.get("ids", ()):
            self.canvas.delete(iid)
        self._selected_tokens.discard(id(tok))
        if tok in self.tokens:
            self.tokens.remove(tok)
        self._save_state()
        if hasattr(self, "_notify_tokens_deleted"):
            self._notify_tokens_deleted([name])

    def _edit_token_hp(self, tok):
        """Dialogue pour modifier les PV actuels et max d'un token."""
        dw = tk.Toplevel(self.win)
        dw.title(f"PV — {tok.get('name','?')}")
        dw.geometry("260x160")
        dw.configure(bg="#0d1018")
        dw.resizable(False, False)
        dw.wait_visibility()
        dw.grab_set()

        tk.Label(dw, text=f"Points de vie — {tok.get('name','?')}",
                 bg="#0d1018", fg="#ef9a9a",
                 font=("Consolas", 9, "bold")).pack(pady=(10, 6))

        frm = tk.Frame(dw, bg="#0d1018")
        frm.pack(padx=14)

        tk.Label(frm, text="PV actuels :", bg="#0d1018", fg="#aaaacc",
                 font=("Consolas", 8), width=12, anchor="w").grid(row=0, column=0, pady=3)
        hp_var = tk.StringVar(value=str(tok.get("hp", "")) if tok.get("hp", -1) >= 0 else "")
        tk.Entry(frm, textvariable=hp_var, bg="#252538", fg="#ef9a9a",
                 font=("Consolas", 10), insertbackground="#ef5350",
                 relief="flat", width=8).grid(row=0, column=1, ipady=3)

        tk.Label(frm, text="PV max :", bg="#0d1018", fg="#aaaacc",
                 font=("Consolas", 8), width=12, anchor="w").grid(row=1, column=0, pady=3)
        maxhp_var = tk.StringVar(value=str(tok.get("max_hp", "")) if tok.get("max_hp", -1) >= 0 else "")
        tk.Entry(frm, textvariable=maxhp_var, bg="#252538", fg="#ef9a9a",
                 font=("Consolas", 10), insertbackground="#ef5350",
                 relief="flat", width=8).grid(row=1, column=1, ipady=3)

        tk.Label(frm, text="Ca (Armor) :", bg="#0d1018", fg="#aaaacc",
                 font=("Consolas", 8), width=12, anchor="w").grid(row=2, column=0, pady=3)
        ac_var = tk.StringVar(value=str(tok.get("ac", "")) if tok.get("ac", -1) >= 0 else "")
        tk.Entry(frm, textvariable=ac_var, bg="#252538", fg="#64b5f6",
                 font=("Consolas", 10), insertbackground="#64b5f6",
                 relief="flat", width=8).grid(row=2, column=1, ipady=3)

        def _apply(event=None):
            try:
                hp_s  = hp_var.get().strip()
                mhp_s = maxhp_var.get().strip()
                ac_s  = ac_var.get().strip()
                tok["hp"]     = int(hp_s)  if hp_s  else -1
                tok["max_hp"] = int(mhp_s) if mhp_s else tok["hp"]
                tok["ac"]     = int(ac_s)  if ac_s  else -1
            except ValueError:
                pass
                
            # ── Synchro avec le tracker ──
            if getattr(self, "app", None):
                tracker = getattr(self.app, "_combat_tracker_win", None)
                if tracker and hasattr(tracker, "combatants"):
                    for cb in tracker.combatants:
                        if getattr(cb, "name", "") == tok["name"]:
                            cb.hp = tok["hp"]
                            cb.max_hp = tok["max_hp"]
                            cb.ac = tok["ac"]
                            tracker._refresh_list()
                            break

            dw.destroy()
            self._redraw_one_token(tok)
            self._save_state()

        dw.bind("<Return>", _apply)
        dw.bind("<Escape>", lambda e: dw.destroy())
        tk.Button(dw, text="Appliquer", bg="#2c1000", fg="#ef9a9a",
                  font=("Consolas", 9, "bold"), relief="flat", padx=10,
                  command=_apply).pack(pady=8)

    def _edit_token_conditions(self, tok):
        """Dialogue checkboxes pour gérer les conditions D&D 5e d'un token."""
        dw = tk.Toplevel(self.win)
        dw.title(f"Conditions & Tactiques — {tok.get('name','?')}")
        dw.geometry("320x520")
        dw.configure(bg="#0d1018")
        dw.resizable(False, True)
        dw.wait_visibility()
        dw.grab_set()

        tk.Label(dw, text=f"Conditions & Tactiques — {tok.get('name','?')}",
                 bg="#0d1018", fg="#ce93d8",
                 font=("Consolas", 10, "bold")).pack(pady=(10, 4))

        current = set(tok.get("conditions", []))
        vars_map = {}

        canvas_frm = tk.Frame(dw, bg="#0d1018")
        canvas_frm.pack(fill=tk.BOTH, expand=True, padx=12)

        cols_n = 2
        for i, (cond_name, cond_color) in enumerate(DND_CONDITIONS.items()):
            row_f = i // cols_n
            col_f = i % cols_n
            var = tk.BooleanVar(value=cond_name in current)
            vars_map[cond_name] = var
            frm_c = tk.Frame(canvas_frm, bg="#0d1018")
            frm_c.grid(row=row_f, column=col_f, sticky="w", padx=6, pady=2)
            tk.Canvas(frm_c, width=12, height=12, bg="#0d1018",
                      highlightthickness=0).pack(side=tk.LEFT, padx=(0, 4))
            dot = frm_c.children[list(frm_c.children)[-1]]
            dot.create_oval(1, 1, 11, 11, fill=cond_color, outline="")
            tk.Checkbutton(frm_c, text=cond_name, variable=var,
                           bg="#0d1018", fg="#ccccee", selectcolor="#1a1a2e",
                           activebackground="#0d1018",
                           font=("Consolas", 8)).pack(side=tk.LEFT)

        tk.Label(dw, text="Statuts tactiques", bg="#0d1018", fg="#8888aa",
                 font=("Consolas", 8, "italic")).pack(pady=(10, 2))
        
        tac_current = set(tok.get("tactics", []))
        tac_vars = {}
        tac_frm = tk.Frame(dw, bg="#0d1018")
        tac_frm.pack(fill=tk.BOTH, expand=True, padx=12)
        
        for i, (t_name, t_color) in enumerate(DND_TACTICS.items()):
            row_f = i // cols_n
            col_f = i % cols_n
            var = tk.BooleanVar(value=t_name in tac_current)
            tac_vars[t_name] = var
            frm_t = tk.Frame(tac_frm, bg="#0d1018")
            frm_t.grid(row=row_f, column=col_f, sticky="w", padx=6, pady=2)
            tk.Canvas(frm_t, width=12, height=12, bg="#0d1018",
                      highlightthickness=0).pack(side=tk.LEFT, padx=(0, 4))
            dot = frm_t.children[list(frm_t.children)[-1]]
            dot.create_oval(1, 1, 11, 11, fill=t_color, outline="")
            tk.Checkbutton(frm_t, text=t_name, variable=var,
                           bg="#0d1018", fg="#ccccee", selectcolor="#1a1a2e",
                           activebackground="#0d1018",
                           font=("Consolas", 8)).pack(side=tk.LEFT)

        def _apply():
            tok["conditions"] = [c for c, v in vars_map.items() if v.get()]
            tok["tactics"]    = [t for t, v in tac_vars.items() if v.get()]
            
            # ── Synchro avec le tracker ──
            if getattr(self, "app", None):
                tracker = getattr(self.app, "_combat_tracker_win", None)
                if tracker and hasattr(tracker, "combatants"):
                    for cb in tracker.combatants:
                        if getattr(cb, "name", "") == tok["name"]:
                            cb.conditions.clear()
                            for cond in tok.get("conditions", []):
                                cb.conditions[cond] = True
                            
                            cb.tactics.clear()
                            for tac in tok.get("tactics", []):
                                cb.tactics[tac] = True
                                
                            tracker._refresh_list()
                            if getattr(tracker, "_schedule_save", None):
                                tracker._schedule_save()
                            break

            dw.destroy()
            self._redraw_one_token(tok)
            self._save_state()

        tk.Button(dw, text="Appliquer", bg="#1a0a2a", fg="#ce93d8",
                  font=("Consolas", 9, "bold"), relief="flat", padx=12, pady=4,
                  command=_apply).pack(pady=8)
        dw.bind("<Return>", lambda e: _apply())
        dw.bind("<Escape>", lambda e: dw.destroy())

    def _set_token_size(self, tok, size_val: float):
        tok["size"] = size_val
        self._redraw_one_token(tok)
        self._save_state()

    def _edit_token_aura(self, tok):
        """Dialogue pour configurer l'aura d'un token."""
        dw = tk.Toplevel(self.win)
        dw.title(f"Aura — {tok.get('name','?')}")
        dw.geometry("280x220")
        dw.configure(bg="#0d1018")
        dw.resizable(False, False)
        dw.wait_visibility()
        dw.grab_set()

        tk.Label(dw, text=f"🌀 Aura de {tok.get('name','?')}",
                 bg="#0d1018", fg="#00ccff",
                 font=("Consolas", 10, "bold")).pack(pady=(12, 6))

        frm = tk.Frame(dw, bg="#0d1018")
        frm.pack(pady=5)

        # Rayon de l'aura
        tk.Label(frm, text="Rayon (ft) :", bg="#0d1018", fg="#aaaacc",
                 font=("Consolas", 9)).grid(row=0, column=0, pady=5, sticky="e")
        
        radius_var = tk.StringVar(value=str(tok.get("aura_radius", 0)))
        spx = tk.Spinbox(frm, from_=0, to=150, increment=5,
                         textvariable=radius_var, width=8, 
                         bg="#252538", fg="#00ccff", font=("Consolas", 10, "bold"),
                         buttonbackground="#252538", relief="flat")
        spx.grid(row=0, column=1, padx=5, pady=5)

        # Couleur de l'aura
        tk.Label(frm, text="Couleur :", bg="#0d1018", fg="#aaaacc",
                 font=("Consolas", 9)).grid(row=1, column=0, pady=5, sticky="e")
        
        COLORS = {
            "Bleu / Cyan (Défaut)": "#00ccff",
            "Or / Jaune (Paladin)": "#ffcc00",
            "Vert (Poison/Soin)": "#4caf50",
            "Rouge (Feu/Hostile)": "#f44336",
            "Violet (Magie/Ombre)": "#ce93d8",
            "Blanc (Lumière)": "#ffffff"
        }
        
        color_var = tk.StringVar()
        current_color = tok.get("aura_color", "#00ccff")
        # Trouver le nom correspondant à la couleur actuelle
        color_name = next((name for name, hex_c in COLORS.items() if hex_c == current_color), "Bleu / Cyan (Défaut)")
        color_var.set(color_name)

        color_menu = tk.OptionMenu(frm, color_var, *COLORS.keys())
        color_menu.config(bg="#252538", fg="#aaaacc", font=("Consolas", 9), relief="flat", highlightthickness=0)
        color_menu["menu"].config(bg="#0d1018", fg="#aaaacc", font=("Consolas", 9))
        color_menu.grid(row=1, column=1, padx=5, pady=5, sticky="ew")

        def _apply(event=None):
            try:
                val = max(0, int(radius_var.get()))
            except ValueError:
                val = 0
                
            tok["aura_radius"] = val
            tok["aura_color"] = COLORS.get(color_var.get(), "#00ccff")
            
            dw.destroy()
            self._redraw_one_token(tok)
            self._save_state()

        tk.Button(dw, text="✅ Appliquer",
                  bg="#003344", fg="#00ccff", font=("Consolas", 9, "bold"),
                  relief="flat", padx=12, pady=5, cursor="hand2",
                  command=_apply).pack(pady=15)
        
        dw.bind("<Return>", _apply)
        dw.bind("<Escape>", lambda e: dw.destroy())

    def _edit_token_altitude(self, tok):
        """Dialogue pour régler l'altitude d'un token (en pieds D&D, 0 = au sol)."""
        dw = tk.Toplevel(self.win)
        dw.title(f"Altitude — {tok.get('name','?')}")
        dw.geometry("300x165")
        dw.configure(bg="#0d1018")
        dw.resizable(False, False)
        dw.wait_visibility()
        dw.grab_set()

        tk.Label(dw, text=f"✈  Altitude de {tok.get('name','?')}",
                 bg="#0d1018", fg="#00ccff",
                 font=("Consolas", 10, "bold")).pack(pady=(12, 2))
        tk.Label(dw, text="0 = au sol  |  multiples de 5 recommandés (5ft = 1 case)",
                 bg="#0d1018", fg="#555577",
                 font=("Consolas", 7)).pack()

        frm = tk.Frame(dw, bg="#0d1018")
        frm.pack(pady=10)
        tk.Label(frm, text="Pieds :", bg="#0d1018", fg="#aaaacc",
                 font=("Consolas", 9)).pack(side=tk.LEFT, padx=(0, 6))
        spx = tk.Spinbox(frm, from_=0, to=500, increment=5,
                         width=6, bg="#252538", fg="#00ccff",
                         font=("Consolas", 12, "bold"),
                         buttonbackground="#252538", relief="flat",
                         highlightthickness=1, highlightcolor="#00ccff")
        spx.delete(0, tk.END)
        spx.insert(0, str(tok.get("altitude_ft", 0)))
        spx.pack(side=tk.LEFT)
        spx.focus_set()
        spx.selection_range(0, tk.END)

        def _apply(event=None):
            try:
                val = max(0, min(500, int(spx.get())))
            except ValueError:
                val = 0
            tok["altitude_ft"] = val
            dw.destroy()
            self._redraw_one_token(tok)
            self._save_state()
            # Notifier le chat si altitude non nulle
            if self.msg_queue is not None:
                name = tok.get("name", "?")
                alt_txt = (f"▲ {val}ft ({val//5} cases)" if val > 0
                           else "↓ retour au sol")
                self.msg_queue.put({
                    "sender": "🗺️ Carte",
                    "text":   f"✈ {name} — altitude : {alt_txt}",
                    "color":  "#00ccff",
                })

        spx.bind("<Return>", _apply)
        dw.bind("<Escape>", lambda e: dw.destroy())
        tk.Button(dw, text="✅ Appliquer",
                  bg="#003344", fg="#00ccff",
                  font=("Consolas", 9, "bold"),
                  relief="flat", padx=12, pady=5,
                  cursor="hand2",
                  command=_apply).pack(pady=2)

    # ─── Recherche de case libre (utilisée par les agents) ───────────────────

    def _nearest_free_cell(self, target_col: int, target_row: int,
                           moving_tok=None,
                           from_col=None,
                           from_row=None):
        """
        Retourne la case libre (col, row) la plus proche de (target_col, target_row).

        Parcourt les cases en spirale carrée croissante (rayon 0, 1, 2, …) jusqu'à
        trouver une case non occupée par un autre token.  Si toute la carte est
        pleine (improbable), retourne (target_col, target_row) tel quel.

        moving_tok : token en cours de déplacement — ignoré dans le test
                     d'occupation (on ne bloque pas sa propre case de départ).

        from_col, from_row : position de départ du token qui se déplace.
                     Quand renseignés, les cases équidistantes du target sont
                     départagées en faveur de celle qui est la plus proche du
                     point de départ — ce qui place l'attaquant du bon côté de
                     la cible plutôt que derrière elle.
        """
        occupied: set[tuple[int, int]] = set()
        for t in self.tokens:
            if moving_tok is not None and t is moving_tok:
                continue
            size = int(t.get("size", 1))
            tc, tr = int(t["col"]), int(t["row"])
            for dc in range(size):
                for dr in range(size):
                    occupied.add((tc + dc, tr + dr))

        # Spirale carrée : rayon 0, 1, 2, …
        max_radius = max(self.cols, self.rows)
        for radius in range(max_radius + 1):
            if radius == 0:
                candidates = [(target_col, target_row)]
            else:
                candidates = []
                r = radius
                # Côté haut et bas
                for dc in range(-r, r + 1):
                    candidates.append((target_col + dc, target_row - r))
                    candidates.append((target_col + dc, target_row + r))
                # Côtés gauche et droit (sans les coins déjà ajoutés)
                for dr in range(-r + 1, r):
                    candidates.append((target_col - r, target_row + dr))
                    candidates.append((target_col + r, target_row + dr))
                # Tri : case la plus proche du target (primaire), puis la plus
                # proche de l'origine (secondaire) pour placer l'attaquant du
                # bon côté de la cible et non derrière elle.
                candidates.sort(key=lambda p: (
                    (p[0] - target_col) ** 2 + (p[1] - target_row) ** 2,
                    ((p[0] - from_col) ** 2 + (p[1] - from_row) ** 2)
                    if from_col is not None and from_row is not None else 0,
                ))
            for col, row in candidates:
                if 0 <= col < self.cols and 0 <= row < self.rows:
                    if (col, row) not in occupied:
                        return col, row

        return target_col, target_row   # fallback (carte pleine)

    def move_token(self, name: str, target_col: int, target_row: int) -> bool:
        """
        API publique pour les agents : déplace le token 'name' vers la case libre
        la plus proche de (target_col, target_row).

        Si la case cible est déjà occupée, le token atterrit sur la case vide
        adjacente la plus proche — jamais sur un autre token.

        Retourne True si le token a été trouvé et bougé, False sinon.
        """
        tok = next((t for t in self.tokens
                    if t.get("name", "").lower() == name.lower()), None)
        if tok is None:
            return False

        old_col, old_row = int(tok["col"]), int(tok["row"])
        new_col, new_row = self._nearest_free_cell(
            target_col, target_row,
            moving_tok=tok,
            from_col=old_col,
            from_row=old_row,
        )

        if (old_col, old_row) == (new_col, new_row):
            return True  # déjà là, rien à faire

        tok["col"] = new_col
        tok["row"] = new_row
        self._redraw_one_token(tok)
        self._save_state()
        if not getattr(getattr(self, "app", None), "_session_paused", False):
            self._notify_token_moved(tok.get("name", "?"), tok["type"],
                                     old_col, old_row, new_col, new_row,
                                     alignment=tok.get("alignment", ""))
        return True

    # ─── Drag tokens (multi-sélection) ───────────────────────────────────────

    def _tok_press(self, event, tok):
        self._tok_leave(event, tok)
        if getattr(self, "tool", "select") != "select":
            return
        shift = bool(event.state & 0x0001)
        if shift:
            if id(tok) in self._selected_tokens:
                self._selected_tokens.discard(id(tok))
            else:
                self._selected_tokens.add(id(tok))
            self._redraw_one_token(tok)
            return
        if id(tok) not in self._selected_tokens:
            self._clear_selection()
            self._selected_tokens.add(id(tok))
            self._redraw_one_token(tok)
        cx, cy = self._canvas_xy(event)
        cp = self._cp
        self._drag_token  = tok
        self._drag_offset = (cx - (tok["col"] + 0.5) * cp,
                             cy - (tok["row"] + 0.5) * cp)
        self._drag_origins = {
            id(t): (t["col"], t["row"])
            for t in self.tokens if id(t) in self._selected_tokens
        }
        
        # --- NOUVEAU : On s'assure qu'aucun ancien compteur ne traîne ---
        self.canvas.delete("drag_counter")

    def _tok_drag(self, event, tok):
        if getattr(self, "_drag_token", None) is None:
            return
        cx, cy = self._canvas_xy(event)
        cp = self._cp
        new_col = (cx - self._drag_offset[0]) / cp - 0.5
        new_row = (cy - self._drag_offset[1]) / cp - 0.5
        dcol = new_col - self._drag_origins[id(tok)][0]
        drow = new_row - self._drag_origins[id(tok)][1]
        
        # --- NOUVEAU : Calcul et affichage du compteur de distance (D&D 5e) ---
        oc, or_ = self._drag_origins[id(tok)]
        dist_cases = max(abs(new_col - oc), abs(new_row - or_))
        dist_ft = dist_cases * 5.0
        label = f"{dist_cases:.1f} cases ({dist_ft:.0f} ft)"

        tx, ty = cx, cy - 40  # Affichage 40 pixels au-dessus du curseur

        if not self.canvas.find_withtag("drag_counter_txt"):
            # Création du fond et du texte
            self.canvas.create_rectangle(tx-10, ty-10, tx+10, ty+10, 
                                         fill="#1e1e1e", outline="#fff176", width=1, 
                                         tags=("drag_counter", "drag_counter_bg"))
            self.canvas.create_text(tx, ty, text=label, fill="#fff176", 
                                    font=("Consolas", 10, "bold"), 
                                    tags=("drag_counter", "drag_counter_txt"))
        else:
            # Mise à jour
            self.canvas.itemconfigure("drag_counter_txt", text=label)
            self.canvas.coords("drag_counter_txt", tx, ty)
        
        # Ajustement du fond noir à la taille du texte
        bbox = self.canvas.bbox("drag_counter_txt")
        if bbox:
            self.canvas.coords("drag_counter_bg", bbox[0]-6, bbox[1]-3, bbox[2]+6, bbox[3]+3)
        self.canvas.tag_raise("drag_counter")
        # ----------------------------------------------------------------------

        for t in self.tokens:
            if id(t) not in self._selected_tokens:
                continue
            oc, or_ = self._drag_origins[id(t)]
            t["col"] = max(0.0, min(self.cols - 1.0, oc + dcol))
            t["row"] = max(0.0, min(self.rows - 1.0, or_ + drow))
            self._redraw_one_token(t)

    def _tok_release(self, event, tok):
        if getattr(self, "_drag_token", None) is None:
            return
            
        # --- NOUVEAU : Suppression du compteur à la fin du mouvement ---
        self.canvas.delete("drag_counter")
        
        moved =[]
        for t in self.tokens:
            if id(t) not in self._selected_tokens:
                continue
            old_col, old_row = self._drag_origins.get(id(t), (t["col"], t["row"]))
            t["col"] = round(max(0, min(self.cols - 1, t["col"])))
            t["row"] = round(max(0, min(self.rows - 1, t["row"])))
            self._redraw_one_token(t)
            new_col, new_row = int(t["col"]), int(t["row"])
            if (int(round(old_col)), int(round(old_row))) != (new_col, new_row):
                moved.append((t, int(round(old_col)), int(round(old_row)), new_col, new_row))
        self._drag_token   = None
        self._drag_origins = {}
        self._save_state()
        if not getattr(getattr(self, "app", None), "_session_paused", False):
            for t, oc, or_, nc, nr in moved:
                self._notify_token_moved(t.get("name", "?"), t["type"], oc, or_, nc, nr,
                                         alignment=t.get("alignment", ""))

    # ─── Image Tooltip (Survol) ──────────────────────────────────────────────

    def _tok_enter(self, event, tok):
        # Annuler la suppression du tooltip en cours s'il y en a une (debounce)
        if getattr(self, "_leave_timer", None):
            self.win.after_cancel(self._leave_timer)
            self._leave_timer = None

        if getattr(self, "tool", "select") != "select":
            return
            
        if getattr(self, "_hovered_tok", None) == tok:
            self._hover_x = event.x_root
            self._hover_y = event.y_root
            return

        self._tok_leave_now()
        self._hovered_tok = tok
        self._hover_x = event.x_root
        self._hover_y = event.y_root
        self._hover_timer = self.win.after(500, self._show_tok_image_tooltip)

    def _tok_leave(self, event, tok):
        if getattr(self, "_leave_timer", None):
            self.win.after_cancel(self._leave_timer)
        # Délai de grâce pour éviter le scintillement entre les éléments du token
        self._leave_timer = self.win.after(50, self._tok_leave_now)

    def _tok_leave_now(self):
        if getattr(self, "_hover_timer", None):
            self.win.after_cancel(self._hover_timer)
            self._hover_timer = None
        if getattr(self, "_img_tooltip_win", None):
            self._img_tooltip_win.destroy()
            self._img_tooltip_win = None
        self._hovered_tok = None

    def _show_tok_image_tooltip(self):
        tok = getattr(self, "_hovered_tok", None)
        if not tok: return

        print(f"[Debug-Tooltip-Map] Survol du token : '{tok.get('name', '???')}'")
        img_path = None
        source = ""

        # Helper : vérifie qu'un chemin provient d'un dossier d'images géré
        # (images/tokens/ ou images/portraits/)
        def _in_portraits(p: str) -> bool:
            try:
                from portrait_resolver import is_known_image_path
                return is_known_image_path(p)
            except Exception:
                return False

        # ── 0. Portrait pré-résolu (passé par place_new_token depuis le tracker) ──
        # Accepté uniquement s'il provient de images/portraits/.
        pre = tok.get("portrait", "")
        if pre and os.path.exists(pre) and _in_portraits(pre):
            img_path = pre
            source = "Portrait pré-résolu"

        # ── 1. Cache du token (image sélectionnée manuellement précédemment) ──
        # Accepté uniquement s'il provient de images/portraits/.
        if not img_path:
            cached = tok.get("image") or tok.get("portrait_manual")
            if cached and os.path.exists(cached) and _in_portraits(cached):
                img_path = cached
                source = "Cache token"

        # ── 2. Chercher dans les personnages (Héros) ─────────────────────────
        # Accepté uniquement si le chemin est dans images/portraits/.
        if not img_path:
            try:
                from state_manager import load_state
                st = load_state()
                chars = st.get("characters", {})
                name_key = tok.get("name", "")
                cdata = chars.get(name_key)
                if not cdata:
                    for k, v in chars.items():
                        if k.lower() == name_key.lower():
                            cdata = v
                            break
                if cdata:
                    p = cdata.get("image") or cdata.get("portrait")
                    if p and os.path.exists(p) and _in_portraits(p):
                        img_path = p
                        source = "Héros (characters)"
            except Exception as e:
                print(f"[Debug-Tooltip-Map] Erreur Héros : {e}")

        # ── 3. Résolution live via portrait_resolver — portraits uniquement
        #       (images/portraits/, pas images/tokens/ qui contient l'art de token)
        #       Utilise source_name (ex. "Rictavio") si dispo, sinon le nom affiché.
        if not img_path:
            try:
                import re
                from portrait_resolver import resolve_portrait
                _src = tok.get("source_name", "").strip()
                base_name = _src if _src else                             re.sub(r'\s+\d+$', '', tok.get("name", "")).strip()
                p = resolve_portrait(base_name)
                if p and os.path.exists(p):
                    img_path = p
                    source = "Portrait resolver"
                    tok["portrait"] = p   # mise en cache sur le token
            except Exception as e:
                print(f"[Debug-Tooltip-Map] Erreur portrait_resolver : {e}")

        print(f"[Debug-Tooltip-Map] Chemin d'image trouvé : {img_path} (Source: {source})")

        # ── 4. (dialog de localisation supprimé) ────────────────────────────

        # ── 5. Afficher l'image dans une fenêtre flottante ───────────────────
        if img_path and os.path.exists(img_path) and PIL_AVAILABLE:
            print("[Debug-Tooltip-Map] Affichage de la fenêtre...")
            import tkinter as tk
            tw = tk.Toplevel(self.win)
            tw.wm_overrideredirect(True)
            x = self._hover_x + 15
            y = self._hover_y + 15
            tw.geometry(f"+{x}+{y}")
            tw.configure(bg="#000000", highlightbackground="#ffffff", highlightthickness=1)
            try:
                from PIL import Image, ImageTk
                img = Image.open(img_path)
                img.thumbnail((250, 250))
                photo = ImageTk.PhotoImage(img)
                lbl = tk.Label(tw, image=photo, bg="#000000")
                lbl.image = photo
                lbl.pack(padx=2, pady=2)
                self._img_tooltip_win = tw
            except Exception as e:
                tw.destroy()
                print(f"[CombatMap] Erreur tooltip image : {e}")

    # ─── Prévisualisation de mouvement ────────────────────────────────────────

    def request_movement_preview(self, name: str, col: float, row: float):
        """Affiche un carré factice déplaçable (is_preview: True) pour le mouvement en cours."""
        self.clear_movement_preview(name)
        # Find the original token to match its size
        size = 1.0
        for t in self.tokens:
            if t.get("name") == name and not t.get("is_preview"):
                size = float(t.get("size", 1.0))
                break
        
        preview_tok = {
            "name": name,
            "type": "ghost",
            "col": col,
            "row": row,
            "size": size,
            "is_preview": True
        }
        self.tokens.append(preview_tok)
        self._redraw_all_tokens()
        # Bring focus to the map if it's open
        if hasattr(self, "win") and self.win:
            self.win.lift()

    def get_movement_preview(self, name: str) -> tuple:
        """Récupère les coordonnées courantes du carré de prévisualisation de 'name'."""
        for t in self.tokens:
            if t.get("is_preview") and t.get("name") == name:
                return (t["col"], t["row"])
        return None

    def clear_movement_preview(self, name: str):
        """Supprime le carré de prévisualisation associé à 'name'."""
        deleted = False
        for t in list(self.tokens):
            if t.get("is_preview") and t.get("name") == name:
                for iid in t.get("ids", ()):
                    self.canvas.delete(iid)
                self.tokens.remove(t)
                deleted = True
        
        if deleted:
            self._redraw_all_tokens()