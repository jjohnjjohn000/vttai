"""
npc_sheet_window.py — Fenêtre principale de la fiche de monstre.
"""

import tkinter as tk
import re

from npc_utils import (
    load_npc_image_bytes, _fmt_entries, _fmt_cr, _fmt_type, _fmt_ac, 
    _fmt_speed, _fmt_damage_list, _fmt_condition_list
)
from npc_bestiary_manager import _load_bestiary, get_monster, get_legendary_group, get_monster_fluff

from npc_sheet_top_mixins import MonsterSheetImageSpeakMixin, MonsterSheetSearchMixin
from npc_sheet_action_mixins import MonsterSheetRenderMixin, MonsterSheetActionMixin


class MonsterSheetWindow(
    MonsterSheetImageSpeakMixin,
    MonsterSheetSearchMixin,
    MonsterSheetRenderMixin,
    MonsterSheetActionMixin
):
    """
    Fenêtre Toplevel affichant la fiche complète d'un monstre D&D 5e.
    Hérite des Mixins pour gérer l'image, le LLM, la recherche et le rendu des actions.
    """

    BG      = "#0d1117"
    BG2     = "#161b22"
    BG3     = "#1e2430"
    FG      = "#e0e0e0"
    FG_DIM  = "#666677"
    FG_MID  = "#aaaaaa"
    ACCENT  = "#e57373"       # rouge sang
    GOLD    = "#ffd54f"
    GREEN   = "#81c784"
    BLUE    = "#64b5f6"
    PURPLE  = "#ce93d8"

    def __init__(self, root, npc_name: str, bestiary_name: str | None = None,
                 on_select_callback=None, win_state: dict = None, track_fn=None,
                 chat_queue=None, audio_queue=None, npc_color: str = "#e0e0e0",
                 get_scene_fn=None):
        self.root = root
        self.npc_name = npc_name
        self.on_select_callback = on_select_callback
        self.chat_queue  = chat_queue
        self.audio_queue = audio_queue
        self.npc_color   = npc_color
        self.get_scene_fn = get_scene_fn
        self._current_monster: dict | None = None
        self._img_tk = None       # référence PhotoImage anti-GC
        self._img_bytes: bytes | None = load_npc_image_bytes(npc_name)

        win = tk.Toplevel(root)
        win.withdraw()  # Fix XWayland mapping freeze
        win.title(f"📋 {npc_name}" + (f" — {bestiary_name}" if bestiary_name else ""))
        win.configure(bg=self.BG)
        win.resizable(True, True)
        win.minsize(580, 640)
        win.geometry("660x840")
        self.win = win

        if track_fn:
            track_fn(f"monster_{npc_name}", win)

        # ── Layout principal ─────────────────────────────────────────────────
        # 1. Barre de recherche (fixe)
        search_bar = tk.Frame(win, bg=self.BG2, pady=6)
        search_bar.pack(fill=tk.X, padx=0, pady=0)

        tk.Label(search_bar, text="Monstre :", bg=self.BG2, fg=self.FG_MID,
                 font=("Arial", 9)).pack(side=tk.LEFT, padx=(10, 4))

        self._search_var = tk.StringVar(value=bestiary_name or "")
        search_entry = tk.Entry(search_bar, textvariable=self._search_var,
                                bg=self.BG3, fg=self.FG, font=("Consolas", 10),
                                insertbackground=self.FG, relief="flat", width=28)
        search_entry.pack(side=tk.LEFT, padx=(0, 6), ipady=4)
        search_entry.bind("<KeyRelease>", self._on_search_key)
        search_entry.bind("<Return>",     self._on_search_confirm)

        self._select_btn = tk.Button(
            search_bar, text="Selectionner",
            bg="#1a3a1a", fg=self.GREEN,
            font=("Arial", 9, "bold"), relief="flat", padx=8,
            command=self._confirm_selection
        )
        self._select_btn.pack(side=tk.RIGHT, padx=8)

        # Dropdown de suggestions
        self._suggest_frame = tk.Frame(win, bg=self.BG2, relief="flat", bd=1)
        self._suggest_labels: list[tk.Label] = []
        self._suggest_visible = False

        # 2. Zone fixe : image NPC + parler en tant que
        self._fixed_top = tk.Frame(win, bg=self.BG2)
        self._fixed_top.pack(fill=tk.X)
        self._build_image_panel(self._fixed_top)
        self._speak_frame = tk.Frame(self._fixed_top, bg="#0e1a10")
        self._speak_frame.pack(fill=tk.X)
        self._build_speak_as_content(self._speak_frame, monster=None)

        # 3. Corps scrollable de la fiche
        body_outer = tk.Frame(win, bg=self.BG)
        body_outer.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        self._canvas = tk.Canvas(body_outer, bg=self.BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(body_outer, orient="vertical", command=self._canvas.yview)
        self._inner = tk.Frame(self._canvas, bg=self.BG)
        self._inner.bind("<Configure>",
                         lambda e: self._canvas.configure(
                             scrollregion=self._canvas.bbox("all")))
        self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._canvas.configure(yscrollcommand=scrollbar.set)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._loading_lbl = tk.Label(self._inner, text="⏳ Chargement du bestiaire…",
                                     bg=self.BG, fg=self.FG_DIM,
                                     font=("Consolas", 10, "italic"), pady=30)
        self._loading_lbl.pack()

        # Affiche la fenêtre immédiatement, puis charge le contenu en différé
        win.after(20, win.deiconify)
        win.after(40, win.lift)
        win.after(60, lambda: self._deferred_load(bestiary_name))

    def _deferred_load(self, bestiary_name):
        """Charge le bestiaire et affiche la fiche après que la fenêtre soit visible."""
        _load_bestiary()
        try:
            self._loading_lbl.destroy()
        except Exception:
            pass
        if bestiary_name:
            self._show_monster(bestiary_name)
        else:
            self._show_empty()

    # ── Spellcasting ─────────────────────────────────────────────────────────

    @staticmethod
    def _clean_spell_name(raw: str) -> str:
        """Extrait le nom lisible d'un tag {@spell nom} ou retourne la chaîne brute."""
        m = re.search(r'\{@spell\s+([^|}]+)', raw)
        return m.group(1).strip() if m else raw.strip()

    def _spellcasting_block(self, parent, sc: dict, monster: dict):
        """
        Rend un bloc spellcasting complet (standard ou inné) :
          • En-tête descriptif avec boutons DD / Attaque de sort
          • Cantrips (niveau 0, à volonté)
          • Emplacements par niveau (1-9)
          • Sorts inné : à volonté, X/jour
        """
        SPELL_LEVEL_FR = {
            "0": "Sorts mineurs",
            "1": "Niveau 1", "2": "Niveau 2", "3": "Niveau 3",
            "4": "Niveau 4", "5": "Niveau 5", "6": "Niveau 6",
            "7": "Niveau 7", "8": "Niveau 8", "9": "Niveau 9",
        }
        ABILITY_FR = {
            "int": "INT", "wis": "SAG", "cha": "CHA",
            "str": "FOR", "dex": "DEX", "con": "CON",
        }
        SPELL_BG   = "#0d0d1f"
        SPELL_FG   = "#b39ddb"
        CANTRIP_FG = "#9b8fc7"
        SLOT_FG    = "#ce93d8"
        INNATE_FG  = "#80cbc4"

        sc_name = sc.get("name", "Spellcasting")
        ability = sc.get("ability", "int")
        header_entries = sc.get("headerEntries", [])
        header_text = _fmt_entries(header_entries)

        # Extraire DC et bonus d'attaque depuis le header
        dc_m   = re.search(r'\{@dc\s+(\d+)\}',  header_text + " ".join(str(e) for e in header_entries))
        hit_m  = re.search(r'\{@hit\s+(-?\d+)\}', " ".join(str(e) for e in header_entries))
        spell_dc  = int(dc_m.group(1))  if dc_m  else None
        spell_hit = int(hit_m.group(1)) if hit_m else None

        # ── Titre de section ──────────────────────────────────────────────────
        title_row = tk.Frame(parent, bg=SPELL_BG)
        title_row.pack(fill=tk.X, padx=8, pady=(6, 0))

        tk.Label(title_row, text=f"✨ {sc_name}",
                 bg=SPELL_BG, fg=SPELL_FG,
                 font=("Consolas", 9, "bold"), anchor="w").pack(side=tk.LEFT, padx=4)

        ability_lbl = ABILITY_FR.get(ability, ability.upper())
        tk.Label(title_row, text=f"({ability_lbl})",
                 bg=SPELL_BG, fg=self.FG_DIM,
                 font=("Consolas", 8)).pack(side=tk.LEFT, padx=2)

        # Boutons DD et Attaque de sort
        if spell_dc is not None or spell_hit is not None:
            btn_row = tk.Frame(title_row, bg=SPELL_BG)
            btn_row.pack(side=tk.RIGHT, padx=4)

            if spell_dc is not None:
                dc_val = spell_dc
                def _show_dc(dc=dc_val, ab=ability_lbl, name=sc_name):
                    self._send_to_chat(
                        f"✨ **{name}** — DD de sauvegarde\n  DD {dc} ({ab}) — les cibles doivent réussir !",
                        "#9b8fc7")
                tk.Button(btn_row, text=f"DD {spell_dc}",
                          bg="#1a0d2e", fg="#ce93d8",
                          font=("Consolas", 8, "bold"), relief="flat",
                          padx=6, pady=2, cursor="hand2",
                          command=_show_dc).pack(side=tk.LEFT, padx=(0, 4))

            if spell_hit is not None:
                hit_val = spell_hit
                sign    = "+" if hit_val >= 0 else ""
                def _roll_spell_attack(b=hit_val, name=sc_name):
                    import random as _rnd
                    d20  = _rnd.randint(1, 20)
                    tot  = d20 + b
                    s    = "+" if b >= 0 else ""
                    crit = " 🎯 CRITIQUE!" if d20 == 20 else (" ☠ FUMBLE" if d20 == 1 else "")
                    self._send_to_chat(
                        f"✨ **{name}** — Attaque de sort\n  d20({d20}){s}{b} = **{tot}**{crit}",
                        "#ce93d8")
                tk.Button(btn_row, text=f"Attaque {sign}{hit_val}",
                          bg="#1a0d2e", fg="#b39ddb",
                          font=("Consolas", 8, "bold"), relief="flat",
                          padx=6, pady=2, cursor="hand2",
                          command=_roll_spell_attack).pack(side=tk.LEFT)

        # ── Description / header ──────────────────────────────────────────────
        clean_header = re.sub(r'\{@(?:dc|hit)\s+[^}]+\}',
                               lambda mo: (f"DD {mo.group(0)[4:-1]}" if "@dc" in mo.group(0)
                                           else f"+{mo.group(0)[5:-1]}"),
                               header_text)
        clean_header = re.sub(r'\{@[^}]+\}', '', clean_header).strip()
        if clean_header:
            tk.Label(parent, text=clean_header,
                     bg=SPELL_BG, fg=self.FG_DIM,
                     font=("Consolas", 8, "italic"), anchor="w",
                     padx=14, wraplength=520, justify=tk.LEFT).pack(fill=tk.X)

        def _spell_btn(wrap, spell_raw: str, fg: str):
            """
            Groupe sort : [Nom du sort ──────────][▶]
            """
            name = self._clean_spell_name(spell_raw)

            grp = tk.Frame(wrap, bg="#130d24", bd=0, highlightthickness=0)
            grp.pack(side=tk.LEFT, padx=2, pady=1)

            def _open_sheet(n=name):
                try:
                    from spell_data import SpellSheetWindow, get_spell
                    sp = get_spell(n)
                    if sp:
                        SpellSheetWindow(self.win, sp)
                    else:
                        self._send_to_chat(f"✨ {n}  (fiche de sort introuvable)", "#b39ddb")
                except ImportError:
                    self._send_to_chat(f"✨ {n}", "#b39ddb")

            def _cast(n=name):
                self._send_to_chat(f"✨ **Lancé :** {n}", "#b39ddb")

            tk.Button(grp, text=name,
                      bg="#130d24", fg=fg,
                      font=("Consolas", 8), relief="flat",
                      padx=5, pady=1, cursor="hand2",
                      command=_open_sheet).pack(side=tk.LEFT)

            tk.Button(grp, text="▶",
                      bg="#1a0f30", fg="#9b8fc7",
                      font=("Consolas", 7, "bold"), relief="flat",
                      padx=3, pady=1, cursor="hand2",
                      command=_cast).pack(side=tk.LEFT)

        # ── Emplacements de sorts (standard) ─────────────────────────────────
        spells_by_level = sc.get("spells", {})
        for lvl_key in sorted(spells_by_level.keys(), key=lambda x: int(x)):
            lvl_data  = spells_by_level[lvl_key]
            spell_list = lvl_data.get("spells", [])
            if not spell_list:
                continue
            slots = lvl_data.get("slots")
            lvl_int = int(lvl_key)

            row_lbl = tk.Frame(parent, bg=SPELL_BG)
            row_lbl.pack(fill=tk.X, padx=14, pady=(4, 0))

            lvl_fr = SPELL_LEVEL_FR.get(lvl_key, f"Niveau {lvl_key}")
            fg_lbl = CANTRIP_FG if lvl_int == 0 else SLOT_FG
            lbl_txt = lvl_fr
            if slots is not None:
                lbl_txt += f"  ({slots} emplacement{'s' if slots > 1 else ''})"

            tk.Label(row_lbl, text=lbl_txt,
                     bg=SPELL_BG, fg=fg_lbl,
                     font=("Arial", 8, "bold"), anchor="w").pack(side=tk.LEFT)

            if slots:
                for _ in range(min(slots, 9)):
                    tk.Label(row_lbl, text="□",
                             bg=SPELL_BG, fg=SLOT_FG,
                             font=("Consolas", 9)).pack(side=tk.LEFT, padx=1)

            spell_wrap = tk.Frame(parent, bg=SPELL_BG)
            spell_wrap.pack(fill=tk.X, padx=24, pady=(1, 2))
            for sp in spell_list:
                _spell_btn(spell_wrap, sp, fg_lbl)

        # ── Sorts inné : à volonté (will) ─────────────────────────────────────
        will_spells = sc.get("will", [])
        if will_spells:
            row_lbl = tk.Frame(parent, bg=SPELL_BG)
            row_lbl.pack(fill=tk.X, padx=14, pady=(4, 0))
            tk.Label(row_lbl, text="À volonté",
                     bg=SPELL_BG, fg=INNATE_FG,
                     font=("Arial", 8, "bold"), anchor="w").pack(side=tk.LEFT)
            spell_wrap = tk.Frame(parent, bg=SPELL_BG)
            spell_wrap.pack(fill=tk.X, padx=24, pady=(1, 2))
            for sp in will_spells:
                _spell_btn(spell_wrap, sp, INNATE_FG)

        # ── Sorts innés : X/jour ───────────────────────────────────────────────
        daily = sc.get("daily", {})
        DAILY_LABELS = {
            "1": "1/jour", "1e": "1/jour chacun",
            "2": "2/jour", "2e": "2/jour chacun",
            "3": "3/jour", "3e": "3/jour chacun",
            "4": "4/jour", "4e": "4/jour chacun",
        }
        for freq_key in sorted(daily.keys()):
            freq_spells = daily[freq_key]
            if not freq_spells:
                continue
            freq_lbl = DAILY_LABELS.get(freq_key, f"{freq_key}/jour")
            row_lbl = tk.Frame(parent, bg=SPELL_BG)
            row_lbl.pack(fill=tk.X, padx=14, pady=(4, 0))
            tk.Label(row_lbl, text=freq_lbl,
                     bg=SPELL_BG, fg=INNATE_FG,
                     font=("Arial", 8, "bold"), anchor="w").pack(side=tk.LEFT)
            spell_wrap = tk.Frame(parent, bg=SPELL_BG)
            spell_wrap.pack(fill=tk.X, padx=24, pady=(1, 2))
            for sp in freq_spells:
                _spell_btn(spell_wrap, sp, INNATE_FG)

        tk.Frame(parent, bg="#2a1a4a", height=1).pack(fill=tk.X, padx=8, pady=(6, 0))

    # ── Pipeline de Rendu Principal ──────────────────────────────────────────

    def _show_monster(self, name: str):
        m = get_monster(name)
        if not m:
            self._show_empty()
            return

        self._current_monster = m
        self._clear_body()
        self._hide_suggestions()
        self.win.title(f"[{self.npc_name}] {name}")

        self._refresh_speak_panel(m)

        # ── EN-TÊTE ─────────────────────────────────────────────────────────
        hdr = tk.Frame(self._inner, bg="#1a0808", pady=8)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text=m.get("name", "?"), bg="#1a0808", fg=self.ACCENT,
                 font=("Arial", 16, "bold"), anchor="w", padx=12).pack(side=tk.LEFT)
        cr_txt = f"FP {_fmt_cr(m.get('cr', '?'))}"
        tk.Label(hdr, text=cr_txt, bg="#1a0808", fg=self.GOLD,
                 font=("Consolas", 11, "bold"), anchor="e", padx=12).pack(side=tk.RIGHT)

        size_map = {"T": "Très petit", "S": "Petit", "M": "Moyen",
                    "L": "Grand", "H": "Très grand", "G": "Gigantesque"}
        align_map = {"L": "Loyal", "N": "Neutre", "C": "Chaotique",
                     "G": "Bon", "E": "Mauvais", "A": "Quelconque",
                     "U": "Sans alignement"}
        sizes = [size_map.get(s, s) for s in m.get("size", [])]
        type_txt = _fmt_type(m.get("type", "?"))
        align_raw = m.get("alignment", [])
        align_txt = " ".join(align_map.get(a, a) for a in align_raw)

        tk.Label(self._inner, text=f"{' / '.join(sizes)} {type_txt}, {align_txt}",
                 bg=self.BG, fg=self.FG_MID, font=("Arial", 9, "italic"),
                 anchor="w", padx=10).pack(fill=tk.X, pady=(4, 0))

        self._sep(color="#5a1a1a", height=2, pady=4)

        # ── STATS DÉFENSIVES ────────────────────────────────────────────────
        hp = m.get("hp", {})
        hp_txt = f"{hp.get('average','?')} ({hp.get('formula','?')})"
        self._row("Classe d'Armure", _fmt_ac(m.get("ac", [])), value_color=self.GREEN)
        self._row("Points de Vie",   hp_txt,                    value_color=self.GREEN)
        self._row("Vitesse",         _fmt_speed(m.get("speed", {})))

        self._sep()

        # ── CARACTÉRISTIQUES ────────────────────────────────────────────────
        self._section("Caractéristiques", self.GOLD)
        stats_frame = tk.Frame(self._inner, bg=self.BG2, pady=6)
        stats_frame.pack(fill=tk.X, padx=8, pady=4)

        STAT_LABELS = [("FOR", "str"), ("DEX", "dex"), ("CON", "con"),
                       ("INT", "int"), ("SAG", "wis"), ("CHA", "cha")]
        STAT_COLORS = {"FOR": "#e57373", "DEX": "#81c784", "CON": "#ffb74d",
                       "INT": "#64b5f6", "SAG": "#ce93d8", "CHA": "#f06292"}

        for i, (label, key) in enumerate(STAT_LABELS):
            col = tk.Frame(stats_frame, bg=self.BG2)
            col.grid(row=0, column=i, padx=6, pady=2, sticky="n")
            c = STAT_COLORS.get(label, self.FG)
            tk.Label(col, text=label, bg=self.BG2, fg=c,
                     font=("Arial", 8, "bold")).pack()
            val = m.get(key, 10)
            mod = (val - 10) // 2
            tk.Label(col, text=str(val), bg=self.BG2, fg=self.FG,
                     font=("Consolas", 11, "bold")).pack()
            tk.Label(col, text=f"({mod:+d})", bg=self.BG2, fg=self.FG_MID,
                     font=("Consolas", 8)).pack()
        for i in range(6):
            stats_frame.columnconfigure(i, weight=1)

        self._sep()

        # ── SAUVEGARDES & COMPÉTENCES ────────────────────────────────────────
        saves = m.get("save", {})
        if saves:
            self._row("Jets de sauvegarde",
                      "  ".join(f"{k.upper()} {v}" for k, v in saves.items()),
                      value_color=self.BLUE)

        skills = m.get("skill", {})
        if skills:
            self._row("Compétences",
                      "  ".join(f"{k.capitalize()} {v}" for k, v in skills.items()),
                      value_color=self.BLUE)

        dr = m.get("resist", [])
        di = m.get("immune", [])
        ci = m.get("conditionImmune", [])
        senses = m.get("senses", [])
        passive = m.get("passive", "?")
        langs = m.get("languages", [])

        if dr:
            self._row("Résistances",     _fmt_damage_list(dr, "resist"), value_color="#ffb74d")
        if di:
            self._row("Immunités dégâts", _fmt_damage_list(di, "immune"), value_color="#e57373")
        if ci:
            self._row("Immunités états",  _fmt_condition_list(ci),        value_color="#e57373")
        if senses:
            self._row("Sens", ", ".join(senses) + f", Perception passive {passive}")
        if langs:
            self._row("Langues", ", ".join(langs))

        self._sep()

        # ── Sections lourdes : construites en pipeline via after() ──────────
        _phases = []

        def _phase_skill_rolls():
            self._sep(color="#1a1a2a")
            self._section("Jets Rapides", "#888899")
            self._skill_roll_widget(self._inner, m, self.BG)
            self._sep(color="#1a1a2a")
        _phases.append(_phase_skill_rolls)

        traits = m.get("trait", [])
        if traits:
            def _phase_traits(t=traits):
                self._section("Traits", self.PURPLE)
                for t_ in t:
                    self._action_block(self._inner, t_, m, self.PURPLE, self.BG)
                self._sep()
            _phases.append(_phase_traits)

        spellcasting = m.get("spellcasting", [])
        if spellcasting:
            def _phase_spells(sc_list=spellcasting):
                self._section("Sorts", "#b39ddb")
                spell_outer = tk.Frame(self._inner, bg="#0d0d1f")
                spell_outer.pack(fill=tk.X, padx=4, pady=(0, 4))
                for sc in sc_list:
                    self._spellcasting_block(spell_outer, sc, m)
                self._sep(color="#2a1a4a")
            _phases.append(_phase_spells)

        actions = m.get("action", [])
        if actions:
            def _phase_actions(a=actions):
                self._section("Actions", self.ACCENT)
                for a_ in a:
                    self._action_block(self._inner, a_, m, self.ACCENT, self.BG)
                self._sep()
            _phases.append(_phase_actions)

        bonus = m.get("bonus_action", m.get("bonusAction", []))
        if bonus:
            def _phase_bonus(b=bonus):
                self._section("Actions Bonus", "#ffb74d")
                for b_ in b:
                    self._action_block(self._inner, b_, m, "#ffb74d", self.BG)
                self._sep()
            _phases.append(_phase_bonus)

        reactions = m.get("reaction", [])
        if reactions:
            def _phase_reactions(r=reactions):
                self._section("Réactions", self.BLUE)
                for r_ in r:
                    self._action_block(self._inner, r_, m, self.BLUE, self.BG)
                self._sep()
            _phases.append(_phase_reactions)

        legendary = m.get("legendary", [])
        leg_group_name = m.get("legendaryGroup", {})
        if isinstance(leg_group_name, dict):
            leg_group_name = leg_group_name.get("name", "")
        if legendary or leg_group_name:
            def _phase_legendary(leg=legendary, lgn=leg_group_name):
                self._section("Actions Légendaires", self.GOLD)
                lg = get_legendary_group(lgn) if lgn else None
                if lg:
                    intro = _fmt_entries(lg.get("lairActions", lg.get("regional", [])))
                    if intro:
                        tk.Label(self._inner, text=intro, bg=self.BG, fg=self.FG_DIM,
                                 font=("Consolas", 8, "italic"), anchor="w", padx=10,
                                 wraplength=540, justify=tk.LEFT, pady=3).pack(fill=tk.X)
                for la in leg:
                    self._action_block(self._inner, la, m, self.GOLD, self.BG)
                self._sep()
            _phases.append(_phase_legendary)

        def _phase_lore():
            fluff = get_monster_fluff(name)
            if fluff:
                fluff_text = _fmt_entries(fluff.get("entries", []))
                if fluff_text and fluff_text.strip():
                    self._section("Lore", self.FG_DIM)
                    self._text_block(fluff_text[:1200] + ("…" if len(fluff_text) > 1200 else ""),
                                     color=self.FG_DIM)
        _phases.append(_phase_lore)

        def _phase_finalize():
            tk.Frame(self._inner, bg=self.BG, height=20).pack()
            self._canvas.yview_moveto(0)
            self._bind_mouse_scroll(self._inner)
        _phases.append(_phase_finalize)

        def _run_phase(idx):
            if idx >= len(_phases):
                return
            try:
                if not self.win.winfo_exists():
                    return
            except Exception:
                return
            _phases[idx]()
            self.win.after(1, lambda: _run_phase(idx + 1))

        self.win.after(1, lambda: _run_phase(0))

    def _bind_mouse_scroll(self, parent):
        """Bind les événements de défilement de façon récursive (Linux + Win/Mac)."""
        def _on_mousewheel(event):
            if event.num == 4 or getattr(event, "delta", 0) > 0:
                self._canvas.yview_scroll(-1, "units")
            elif event.num == 5 or getattr(event, "delta", 0) < 0:
                self._canvas.yview_scroll(1, "units")

        def _recursive_bind(w):
            w.bind("<MouseWheel>", _on_mousewheel)
            w.bind("<Button-4>", _on_mousewheel)
            w.bind("<Button-5>", _on_mousewheel)
            for c in w.winfo_children():
                _recursive_bind(c)

        _recursive_bind(parent)