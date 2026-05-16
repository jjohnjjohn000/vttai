"""
npc_sheet_action_mixins.py — Mixins pour le rendu, l'analyse des actions et les widgets de jets.
"""

import tkinter as tk
import random
import re

from npc_utils import _fmt_entries, _SKILL_TO_STAT, _SKILL_FR

class MonsterSheetRenderMixin:
    """Mixin pour les éléments de base du rendu (sections, lignes, textes)."""

    def _clear_body(self):
        for w in self._inner.winfo_children():
            w.destroy()

    def _show_empty(self):
        self._clear_body()
        self._current_monster = None
        self._refresh_speak_panel(None)
        tk.Label(self._inner, text="Recherchez un monstre ci-dessus",
                 bg=self.BG, fg=self.FG_DIM, font=("Consolas", 10, "italic"),
                 pady=30).pack()
        tk.Label(self._inner, text="Tapez un nom et appuyez sur Entree",
                 bg=self.BG, fg=self.FG_DIM, font=("Consolas", 9)).pack()

    def _sep(self, color="#2a2a3a", height=1, pady=4):
        tk.Frame(self._inner, bg=color, height=height).pack(
            fill=tk.X, padx=8, pady=pady)

    def _section(self, title: str, color=None):
        color = color or self.GOLD
        tk.Label(self._inner, text=title.upper(), bg=self.BG, fg=color,
                 font=("Arial", 8, "bold"), anchor="w",
                 pady=3, padx=10).pack(fill=tk.X)

    def _row(self, label: str, value: str, label_color=None, value_color=None):
        label_color = label_color or self.FG_DIM
        value_color = value_color or self.FG
        row = tk.Frame(self._inner, bg=self.BG)
        row.pack(fill=tk.X, padx=10, pady=1)
        tk.Label(row, text=label, bg=self.BG, fg=label_color,
                 font=("Arial", 8), width=14, anchor="w").pack(side=tk.LEFT)
        tk.Label(row, text=value, bg=self.BG, fg=value_color,
                 font=("Consolas", 9), anchor="w", wraplength=360,
                 justify=tk.LEFT).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _text_block(self, content: str, color=None, font=None):
        color = color or self.FG_MID
        font  = font  or ("Consolas", 9)
        txt = tk.Text(self._inner, bg=self.BG2, fg=color, font=font,
                      relief="flat", wrap=tk.WORD, height=1,
                      padx=10, pady=6, state=tk.NORMAL,
                      highlightthickness=0, borderwidth=0)
        txt.insert("1.0", content)
        txt.config(state=tk.DISABLED)
        # Ajuste la hauteur automatiquement
        lines = content.count("\n") + 1
        estimated = max(2, min(lines + 1, 20))
        txt.config(height=estimated)
        txt.pack(fill=tk.X, padx=8, pady=2)
        # _bind_mouse_scroll se chargera de la molette pour ce widget


class MonsterSheetActionMixin:
    """Mixin pour le parsing des attaques/actions et la création des widgets interactifs."""

    def _roll_dice(self, expr: str) -> tuple[int, str]:
        """Lance une expression de dés (ex: '2d6+5') → (total, détail)."""
        expr = expr.strip()
        total = 0
        detail_parts = []
        # Traite chaque terme : NdX, +N, -N
        for term in re.finditer(r'([+-]?\s*\d*d\d+|[+-]?\s*\d+)', expr):
            t = term.group(0).replace(' ', '')
            if 'd' in t:
                sign = -1 if t.startswith('-') else 1
                t2 = t.lstrip('+-')
                parts = t2.split('d')
                n = int(parts[0]) if parts[0] else 1
                sides = int(parts[1])
                rolls = [random.randint(1, sides) for _ in range(n)]
                s = sum(rolls)
                total += sign * s
                detail_parts.append(f"[{','.join(str(r) for r in rolls)}]")
            else:
                val = int(t.replace(' ',''))
                total += val
                detail_parts.append(str(val))
        return total, '+'.join(detail_parts).replace('+-', '-')

    def _parse_action_rolls(self, entries: list) -> dict:
        """
        Extrait depuis les entries d'une action :
          hit      : int | None  (bonus d'attaque, tag {@hit N})
          damages  : list[(expr, type)]  (tags {@damage NdX+Y}, {@scaledice...})
          dc       : int | None   ({@dc N})
          dc_save  : str | None   ({@skill X} ou inféré depuis le texte)
          desc     : str  (texte nettoyé)
        """
        full_text = _fmt_entries(entries)
        raw_text  = "\n".join(e if isinstance(e, str) else "" for e in entries
                              if isinstance(e, str))

        hit_m  = re.search(r'\{@hit\s+(-?\d+)\}', raw_text)
        dc_m   = re.search(r'\{@dc\s+(\d+)\}', raw_text)

        dmg_tags = re.findall(r'\{@damage\s+([^}]+)\}', raw_text)
        type_tags = re.findall(r'\{@damage\s+[^}]+\}\s*([a-zA-Zéâ]+(?:\s+[a-zA-Zéâ]+)?)',
                                raw_text)

        # Cherche aussi les types de dégâts depuis le texte nettoyé
        dmg_types_raw = re.findall(
            r'(\d+d\d+(?:[+-]\d+)?)\s+(?:de\s+)?([a-zA-Zé]+(?:\s+et\s+[a-zA-Zé]+)?)\s*(?:dégâts|damage)',
            full_text, re.IGNORECASE)

        damages = []
        for i, expr in enumerate(dmg_tags):
            t = type_tags[i] if i < len(type_tags) else ""
            damages.append((expr.strip(), t.strip()))
        if not damages and dmg_types_raw:
            for expr, typ in dmg_types_raw:
                damages.append((expr, typ))

        # Jet de sauvegarde associé
        save_m = re.search(
            r'\{@dc\s+\d+\}[^{]*\{@skill\s+([^}]+)\}|'
            r'jet\s+de\s+sauvegarde\s+(?:de\s+)?(\w+)|'
            r'(\w+)\s+saving\s+throw',
            raw_text, re.IGNORECASE)
        dc_save = None
        if save_m:
            dc_save = (save_m.group(1) or save_m.group(2) or save_m.group(3) or "").strip()

        return {
            "hit":     int(hit_m.group(1)) if hit_m else None,
            "dc":      int(dc_m.group(1))  if dc_m  else None,
            "dc_save": dc_save,
            "damages": damages,
            "desc":    full_text,
        }

    def _send_to_chat(self, text: str, color: str = "#f0d060"):
        """Envoie un message dans le chat principal."""
        if self.chat_queue:
            self.chat_queue.put({"sender": f"⚔ {self.npc_name}", "text": text, "color": color})

    def _action_roll_widget(self, parent, action_name: str, rolls: dict,
                            monster: dict, row_bg: str, recharge_val: int = None):
        """
        Construit le bloc interactif sous une action :
        [Attaque] [Dégât X] [DD N — Sauvegarde] [♻ Recharge]
        """
        if not rolls["hit"] and not rolls["dc"] and not rolls["damages"] and recharge_val is None:
            return  # Rien à lancer

        btn_frame = tk.Frame(parent, bg=row_bg)
        btn_frame.pack(anchor="w", padx=20, pady=(2, 6))

        from state_manager import get_npc_cooldown, set_npc_cooldown
        on_cooldown = False
        if recharge_val is not None:
            on_cooldown = get_npc_cooldown(self.npc_name, action_name)

        def _consume_if_needed(name=action_name):
            if recharge_val is not None and not get_npc_cooldown(self.npc_name, name):
                set_npc_cooldown(self.npc_name, name, True)
                if self._current_monster:
                    self.root.after(50, lambda: self._show_monster(self._current_monster["name"]))

        def _btn(text, bg, fg, cmd):
            if on_cooldown and not text.startswith("♻"):
                # Style désactivé visuellement (grisé) mais toujours cliquable par sécurité
                bg = "#2a2a2a"
                fg = "#666666"
                text = f"[En Recharge] {text}"

            tk.Button(btn_frame, text=text, bg=bg, fg=fg,
                      font=("Consolas", 8, "bold"), relief="flat",
                      padx=6, pady=2, cursor="hand2",
                      command=cmd).pack(side=tk.LEFT, padx=(0, 4))

        # ── Bouton Recharge / Statut ──────────────────────────────────────
        if recharge_val is not None:
            if on_cooldown:
                def _roll_recharge(r=recharge_val, name=action_name):
                    d6 = random.randint(1, 6)
                    if d6 >= r:
                        res_txt = "🟢 **Réussi !** L'action est rechargée."
                        color = "#81c784"
                        set_npc_cooldown(self.npc_name, name, False)
                    else:
                        res_txt = "🔴 **Échec.** Doit encore recharger."
                        color = "#e57373"
                    msg = f"**{name}** — Jet de Recharge (Recharge {r}-6)\n  d6({d6}) : {res_txt}"
                    self._send_to_chat(msg, color)
                    if self._current_monster:
                        self.root.after(50, lambda: self._show_monster(self._current_monster["name"]))
                
                _btn(f"♻ Tenter Recharge {recharge_val}+", "#302607", "#ffd54f", _roll_recharge)
            else:
                def _mark_used(name=action_name):
                    set_npc_cooldown(self.npc_name, name, True)
                    if self._current_monster:
                        self.root.after(50, lambda: self._show_monster(self._current_monster["name"]))
                _btn("🟢 Action Prête (marquer utilisée)", "#1a351a", "#81c784", _mark_used)

        # ── Bouton Attaque ──────────────────────────────────────────────
        if rolls["hit"] is not None:
            bonus = rolls["hit"]
            sign  = "+" if bonus >= 0 else ""

            def _roll_attack(b=bonus, name=action_name):
                _consume_if_needed(name)
                d20  = random.randint(1, 20)
                tot  = d20 + b
                sign2 = "+" if b >= 0 else ""
                crit = " 🎯 CRITIQUE!" if d20 == 20 else (" ☠ FUMBLE" if d20 == 1 else "")
                msg = f"**{name}** — Attaque\n  d20({d20}) {sign2}{b} = **{tot}**{crit}"
                self._send_to_chat(msg, "#e57373")

            _btn(f"Attaque {sign}{bonus}", "#3a1010", "#e57373", _roll_attack)

        # ── Bouton(s) Dégâts ────────────────────────────────────────────
        for i, (expr, dmg_type) in enumerate(rolls["damages"]):
            lbl_type = f" {dmg_type}" if dmg_type else ""
            btn_text = f"Dégâts{lbl_type} ({expr})" if i == 0 else f"+ {expr}{lbl_type}"

            def _roll_damage(e=expr, t=dmg_type, name=action_name):
                _consume_if_needed(name)
                total, detail = self._roll_dice(e)
                type_str = f" {t}" if t else ""
                msg = f"**{name}** — Dégâts{type_str}\n  {e} → {detail} = **{total}**"
                self._send_to_chat(msg, "#ffb74d")

            _btn(btn_text, "#2a1800", "#ffb74d", _roll_damage)

        # ── Bouton DD / Sauvegarde ──────────────────────────────────────
        if rolls["dc"] is not None:
            save_lbl = rolls["dc_save"].upper() if rolls["dc_save"] else "SAU"
            dc_val   = rolls["dc"]

            def _show_dc(dc=dc_val, sv=save_lbl, name=action_name):
                _consume_if_needed(name)
                msg = f"**{name}** — Jet de sauvegarde\n  DD {dc} ({sv}) — les cibles doivent réussir !"
                self._send_to_chat(msg, "#64b5f6")

            _btn(f"DD {dc_val} — {save_lbl}", "#0a1a30", "#64b5f6", _show_dc)

    def _skill_roll_widget(self, parent, monster: dict, row_bg: str):
        """
        Bloc interactif complet :
          • Initiative
          • 6 caractéristiques brutes
          • 6 sauvegardes (avec bonus proficiency si présent dans fiche, sinon stat seule)
          • Toutes les compétences (proficiency si dans fiche, sinon stat de base)
        """
        STAT_MAP_FR = {
            "str": ("FOR", "#e57373"), "dex": ("DEX", "#81c784"),
            "con": ("CON", "#ffb74d"), "int": ("INT", "#64b5f6"),
            "wis": ("SAG", "#ce93d8"), "cha": ("CHA", "#f06292"),
        }
        SAVE_FR = {
            "str": "FOR", "dex": "DEX", "con": "CON",
            "int": "INT", "wis": "SAG", "cha": "CHA",
        }

        outer = tk.Frame(parent, bg=row_bg)
        outer.pack(fill=tk.X, padx=8, pady=(0, 8))

        def _section_lbl(txt):
            tk.Label(outer, text=txt, bg=row_bg, fg=self.FG_DIM,
                     font=("Consolas", 7, "bold")).pack(anchor="w", padx=2, pady=(6, 1))

        def _btn_wrap():
            f = tk.Frame(outer, bg=row_bg)
            f.pack(fill=tk.X)
            return f

        def _qbtn(wrap, text, bg, fg, cmd):
            b = tk.Button(wrap, text=text, bg=bg, fg=fg,
                          font=("Consolas", 7, "bold"), relief="flat",
                          padx=5, pady=2, cursor="hand2", command=cmd)
            b.pack(side=tk.LEFT, padx=2, pady=1)

        def _roll_d20(bonus: int, label: str, color: str):
            d20  = random.randint(1, 20)
            tot  = d20 + bonus
            sign = "+" if bonus >= 0 else ""
            crit = " 🎯 CRITIQUE!" if d20 == 20 else (" ☠ FUMBLE" if d20 == 1 else "")
            msg  = f"**{label}** : d20({d20}){sign}{bonus} = **{tot}**{crit}"
            self._send_to_chat(msg, color)

        # ── Initiative ───────────────────────────────────────────────────────
        _section_lbl("INITIATIVE")
        w = _btn_wrap()
        dex_val = monster.get("dex", 10)
        dex_mod = (dex_val - 10) // 2
        sign    = "+" if dex_mod >= 0 else ""
        _qbtn(w, f"Initiative {sign}{dex_mod}", "#101820", "#81c784",
              lambda m=dex_mod: _roll_d20(m, "Initiative", "#81c784"))

        # ── Caractéristiques ─────────────────────────────────────────────────
        _section_lbl("CARACTÉRISTIQUES")
        w = _btn_wrap()
        for key, (label, color) in STAT_MAP_FR.items():
            val = monster.get(key, 10)
            mod = (val - 10) // 2
            s   = "+" if mod >= 0 else ""
            _qbtn(w, f"{label} {s}{mod}", "#1a1a2a", color,
                  lambda m=mod, l=f"Jet de {label}", c=color: _roll_d20(m, l, c))

        # ── Sauvegardes (TOUTES, avec proficiency si dispo) ──────────────────
        _section_lbl("SAUVEGARDES")
        w = _btn_wrap()
        saves_dict = monster.get("save", {})
        for key, (label, color) in STAT_MAP_FR.items():
            if key in saves_dict:
                # Proficiency explicite dans la fiche
                m4 = re.search(r'([+-]?\d+)', str(saves_dict[key]))
                bonus = int(m4.group(1)) if m4 else 0
                star  = "★"
            else:
                # Pas de proficiency — bonus = mod de stat seul
                bonus = (monster.get(key, 10) - 10) // 2
                star  = ""
            sign = "+" if bonus >= 0 else ""
            fr   = SAVE_FR.get(key, key.upper())
            _qbtn(w, f"Sauv.{fr}{star} {sign}{bonus}", "#0a1a0a", color,
                  lambda b=bonus, l=f"Sauvegarde {fr}", c=color: _roll_d20(b, l, c))

        # ── Compétences (TOUTES, avec proficiency si dispo) ──────────────────
        _section_lbl("COMPÉTENCES")
        skills_dict = monster.get("skill", {})

        for stat_key in ("str", "dex", "int", "wis", "cha"):
            color = STAT_MAP_FR[stat_key][1]
            stat_val = monster.get(stat_key, 10)
            stat_mod = (stat_val - 10) // 2
            row_skills = [(k, v) for k, v in _SKILL_TO_STAT.items() if v == stat_key]
            if not row_skills:
                continue
            w = _btn_wrap()
            for skill_en, _ in sorted(row_skills, key=lambda x: x[0]):
                fr_name = _SKILL_FR.get(skill_en, skill_en.capitalize())
                # Cherche le bonus dans la fiche
                match_key = next(
                    (k for k in skills_dict
                     if k.lower().replace(" ", "") == skill_en.replace(" ", "")), None
                )
                if match_key:
                    m5 = re.search(r'([+-]?\d+)', str(skills_dict[match_key]))
                    bonus = int(m5.group(1)) if m5 else stat_mod
                    star  = "★"
                else:
                    bonus = stat_mod
                    star  = ""
                sign = "+" if bonus >= 0 else ""
                short_fr = fr_name[:10]
                _qbtn(w, f"{short_fr}{star} {sign}{bonus}", "#0d0d1a", color,
                      lambda b=bonus, l=fr_name, c=color: _roll_d20(b, l, c))


    def _action_block(self, parent, action: dict, monster: dict,
                      name_color: str, row_bg: str):
        """Rend une action complète : titre + description + boutons de lancer."""
        raw_name = action.get("name", "?")
        
        recharge_val = None
        
        m_tag = re.search(r'\{@recharge\s+(\d+)\}', raw_name)
        if m_tag:
            recharge_val = int(m_tag.group(1))
            a_name = re.sub(r'\s*\{@recharge\s+\d+\}', f' (Recharge {recharge_val}-6)', raw_name)
        else:
            m_text = re.search(r'\(Recharge\s+(\d+)(?:-\d+)?\)', raw_name, re.IGNORECASE)
            if m_text:
                recharge_val = int(m_text.group(1))
            a_name = raw_name

        entries = action.get("entries", [])
        a_desc  = _fmt_entries(entries)
        rolls   = self._parse_action_rolls(entries)

        tk.Label(parent, text=f"▸ {a_name}", bg=self.BG, fg=name_color,
                 font=("Consolas", 9, "bold"), anchor="w", padx=10,
                 pady=2).pack(fill=tk.X)
        if a_desc:
            tk.Label(parent, text=a_desc, bg=self.BG, fg=self.FG_MID,
                     font=("Consolas", 9), anchor="w", padx=20,
                     wraplength=520, justify=tk.LEFT).pack(fill=tk.X)
        self._action_roll_widget(parent, a_name, rolls, monster, self.BG, recharge_val=recharge_val)