"""
engine_mechanics_spells.py — Résolution des Sorts et des Mouvements
Partie 4/4 du module engine_mechanics.

Exporte :
  execute_spell_action
  execute_move_action
"""

import re as _re
from state_manager import roll_dice
from engine_spell_mj import can_ritual_cast

# ─── execute_spell_action ────────────────────────────────────────────────────

def execute_spell_action(
    char_name: str, intention: str, regle: str, cible: str, mj_note: str,
    type_label: str, char_mechanics: dict, pending_smite: dict, app,
    extract_spell_name_fn, is_spell_prepared_fn, get_prepared_spell_names_fn
) -> str:
    """Gère la branche complète d'un Sort (incantation, dégâts, soins, invocations)."""
    stats = char_mechanics.get(char_name, {})
    r_low = regle.lower()
    t_low = (type_label or "").lower()
    if "mouvement" in t_low:
        r_low = "mouvement "
    i_low = intention.lower()
    results = []
    if mj_note:
        results.append(f"Note MJ : {mj_note}")

    # Helpers locaux préservant la logique originale
    def _all_dice(text):
        text_mod = text
        if stats.get("spell_mod"):
            text_mod = _re.sub(r'\+\s*mod(?:ificateur|\.| )?(?:\s*de\s*sort)?', f"+{stats['spell_mod']}", text_mod, flags=_re.IGNORECASE)
        return[(int(m.group(1)), int(m.group(2)),
                 int(m.group(3).replace(" ","")) if m.group(3) else 0)
                for m in _re.finditer(r"(\d+)d(\d+)(?:\s*([+-]\s*\d+))?",
                                      text_mod, _re.IGNORECASE)]

    def _extract_dc(text):
        m = _re.search(r"\bDC\s*(\d+)", text, _re.IGNORECASE)
        return int(m.group(1)) if m else None

    def _extract_level(text):
        levels =[]
        for pat in (r"niv(?:eau)?\.?\s*(\d+)", r"niveau\s*(\d+)", r"\bniv(\d+)"):
            for m in _re.finditer(pat, text, _re.IGNORECASE):
                levels.append(int(m.group(1)))
        valid_levels = [l for l in levels if l <= 9]
        if valid_levels:
            return valid_levels[-1] 
        return None

    lvl       = _extract_level(regle) or _extract_level(intention)
    is_cantrip = lvl is None or lvl == 0
    is_heal   = any(k in r_low or k in i_low
                    for k in ("soin","soigne","heal","cure","guéri",
                              "restaure","parole curative","imposition","lay on hands"))
    is_atk_roll = (any(k in r_low for k in ("jet d attaque de sort",
                                              "attaque de sort"))
                   or (not is_heal and "rayon" in r_low
                       and not _re.search(r"rayon\s+de\s+\d+", r_low)))
    dc_val    = _extract_dc(regle)

    # Vérification liste de sorts préparés
    _combined_text = f"{intention} {regle}".strip()
    
    _CLASS_FEATURES = ("imposition", "lay on hands", "second wind", "second souffle", "potion", "conduit divin", "channel divinity")
    _is_class_feature = any(k in r_low or k in i_low for k in _CLASS_FEATURES)
    
    _spell_name_candidate = "" if _is_class_feature else (extract_spell_name_fn(_combined_text, char_name) if extract_spell_name_fn else "")
    
    if not is_cantrip and _spell_name_candidate:
        if not is_spell_prepared_fn(char_name, _spell_name_candidate):
            _avail = get_prepared_spell_names_fn(char_name)
            _avail_str = ", ".join(_avail) if _avail else "aucun sort préparé trouvé"
            _no_prep_msg = (
                f"[RÉSULTAT SYSTÈME — SORT IMPOSSIBLE — {char_name}]\n"
                f"« {_spell_name_candidate} » n'est pas dans la liste de sorts "
                f"préparés de {char_name}. Ce sort ne peut pas être lancé aujourd'hui.\n\n"
                f"[SORTS AUTORISÉS POUR {char_name.upper()}]\n"
                f"{_avail_str}\n\n"
                f"[INSTRUCTION]\n"
                f"Choisis UNIQUEMENT parmi les sorts listés ci-dessus. "
                f"Déclare une nouvelle action avec [ACTION]."
            )
            return _no_prep_msg

    # Injection des mécaniques depuis spell_data.py
    _sp_data = None
    if _spell_name_candidate:
        try:
            from spell_data import get_spell as _get_spell
            _sp_data = _get_spell(_spell_name_candidate)
        except Exception:
            pass

    # Fallback : recherche catalogue
    if _sp_data is None and not _is_class_feature:
        try:
            from spell_data import search_spells as _ss_fb, get_spell as _get_spell
            _i_r_fb = (intention + " " + regle).lower()
            _STOP_FB = {"lance", "lancer", "utilise", "sorts", "avec", "pour",
                        "dans", "contre", "vers", "cible", "niveau", "niveaux",
                        "sort", "magie", "spell", "cast", "magic", "bonus"}
            for _w in _re.split(r"[\s\-,;:!?]+", _i_r_fb):
                if len(_w) >= 5 and _w not in _STOP_FB:
                    _hits = _ss_fb(_w, max_results=1)
                    if _hits:
                        _fb_sp = _get_spell(_hits[0])
                        if _fb_sp:
                            _spell_name_candidate = _hits[0]
                            _sp_data = _fb_sp
                            break
        except Exception:
            pass

    if _sp_data:
        # Jet d'attaque ? — jamais pour un sort de soin.
        if _sp_data.get("spell_attack") and not is_atk_roll and not is_heal:
            is_atk_roll = True
        elif not _sp_data.get("spell_attack") and not is_heal:
            is_atk_roll = False

        # Sauvegarde ?
        _save = _sp_data.get("saving_throw", [])
        _dc_stat = stats.get("save_dc")
        if _save and not dc_val and _dc_stat:
            dc_val = _dc_stat

        # Dégâts/Soins dynamiques depuis le tag {@damage XdY} ou {@dice XdY}
        if not _all_dice(regle):
            import json as _json_parser
            _entries_str = _json_parser.dumps(_sp_data.get("entries",[]))
            _dmg_matches = _re.findall(r"\{@(damage|dice)\s+([^}]+)\}", _entries_str)
            if _dmg_matches:
                _base_dice = _dmg_matches[0][1]
                _base_lvl = _sp_data.get("level", 0)
                if lvl and lvl > _base_lvl and _sp_data.get("entries_higher"):
                    _higher_str = _json_parser.dumps(_sp_data["entries_higher"])
                    _scale_m = _re.search(r"\{@scale(?:damage|dice)\s+[^|]+\|[^|]+\|(\d+d\d+)\}", _higher_str)
                    if _scale_m:
                        _diff = lvl - _base_lvl
                        _scale_dice = _scale_m.group(1)
                        _sm_m = _re.match(r"(\d+)d(\d+)", _scale_dice)
                        if _sm_m:
                            _ext_dn = int(_sm_m.group(1)) * _diff
                            _ext_df = _sm_m.group(2)
                            _base_m = _re.match(r"(\d+)d(\d+)(.*)", _base_dice)
                            if _base_m and _base_m.group(2) == _ext_df:
                                _new_dn = int(_base_m.group(1)) + _ext_dn
                                _base_dice = f"{_new_dn}d{_ext_df}{_base_m.group(3)}"
                            else:
                                regle += f" + {_ext_dn}d{_ext_df}"
                regle += f" {_base_dice} "
                
                _low_entries = _entries_str.lower()
                if "spellcasting ability modifier" in _low_entries or "modificateur" in _low_entries or "modifier" in _low_entries:
                    regle += "+ mod"

    results.append(f"✨ {char_name} — {intention.strip()} (niv.{lvl or 0}) → {cible}")

    # ── Smite spells ──
    _SMITE_TABLE = {
        "wrathful smite":   ("1d6",  "psychique",  "Wrathful Smite"),
        "courroux divin":   ("1d6",  "psychique",  "Wrathful Smite"),
        "thunderous smite": ("2d6",  "tonnerre",   "Thunderous Smite"),
        "frappe tonnerre":  ("2d6",  "tonnerre",   "Thunderous Smite"),
        "branding smite":   ("2d6",  "radiant",    "Branding Smite"),
        "frappe lumière":   ("2d6",  "radiant",    "Branding Smite"),
    }
    _smite_match = next(
        ((dice, typ, lbl)
         for kw, (dice, typ, lbl) in _SMITE_TABLE.items()
         if kw in r_low or kw in i_low),
        None
    )
    if _smite_match:
        _sm_dice, _sm_type, _sm_label = _smite_match
        _sm_lvl = lvl or 1
        if _sm_dice is None:
            _sm_dice = f"{_sm_lvl + 1}d8"
        pending_smite[char_name] = {
            "dice":       _sm_dice,
            "type":       _sm_type,
            "label":      _sm_label,
            "slot_level": _sm_lvl,
        }
        results.append(
            f"  [✨ {_sm_label}] En attente — {_sm_dice} dégâts {_sm_type} "
            f"s'ajouteront sur la prochaine attaque de {char_name} SI elle touche. "
            f"(slot niv.{_sm_lvl} sera consommé uniquement sur toucher)"
        )
        narrative_hint = (
            f"Le sort {_sm_label} est prêt. "
            f"Narre en 1 phrase : la lueur sacrée qui enveloppe l'arme de {char_name}, "
            f"prête à se décharger sur le prochain coup."
        )
        return (
            f"[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ — {char_name}]\n"
            + "\n".join(results)
            + "\n\n[INSTRUCTION NARRATIVE]\n"
            + narrative_hint
        )

    # ── PRÉ-DÉTECTION INVOCATION SPECTRALE ──
    _spell_check_str = f"{_spell_name_candidate} {regle} {intention}".lower()
    _SPECTRAL_SPAWNS = {
        "spiritual weapon": {"name": "Arme", "src": "Spiritual_Weapon", "size": 1, "aura": 0, "color": ""},
        "arme spirituelle": {"name": "Arme", "src": "Spiritual_Weapon", "size": 1, "aura": 0, "color": ""},
        "marteau spirituel":{"name": "Arme", "src": "Spiritual_Weapon", "size": 1, "aura": 0, "color": ""},
        "flaming sphere":   {"name": "Sphère", "src": "Flaming_Sphere", "size": 1, "aura": 5, "color": "#ff6600"},
        "sphère de feu":    {"name": "Sphère", "src": "Flaming_Sphere", "size": 1, "aura": 5, "color": "#ff6600"},
        "bigby's hand":     {"name": "Main", "src": "Bigbys_Hand", "size": 2, "aura": 0, "color": ""},
        "main de bigby":    {"name": "Main", "src": "Bigbys_Hand", "size": 2, "aura": 0, "color": ""},
        "moonbeam":         {"name": "Rayon", "src": "Moonbeam", "size": 1, "aura": 5, "color": "#e0e0ff"},
        "rayon de lune":    {"name": "Rayon", "src": "Moonbeam", "size": 1, "aura": 5, "color": "#e0e0ff"},
        "cloud of daggers": {"name": "Dagues", "src": "Cloud_of_Daggers", "size": 1, "aura": 0, "color": ""},
        "nuage de dagues":  {"name": "Dagues", "src": "Cloud_of_Daggers", "size": 1, "aura": 0, "color": ""},
    }
    
    _match = next((v for k, v in _SPECTRAL_SPAWNS.items() if k in _spell_check_str), None)
    _cmap_win = getattr(app, "_combat_map_win", None)
    _spectral_exists = False
    _sum_name = f"{_match['name']} ({char_name})" if _match else ""

    if _cmap_win and _match:
        _spectral_exists = any(t.get("name") == _sum_name for t in _cmap_win.tokens)

    # ── Court-circuit : arme spectrale ──
    _SW_MOVE_KW = ("déplace", "deplace", "move", "repositionne",
                   "rapproche", "avance", "recule", "bouge", "mouvement")
    _SW_ATK_KW  = ("attaque", "attack", "frappe", "frapper", "assaut")
    _sw_has_move = (
        ("mouvement" in t_low)
        or any(k in i_low or k in r_low for k in _SW_MOVE_KW)
    )
    _sw_has_atk = any(k in i_low or k in r_low for k in _SW_ATK_KW)

    # Cas 1a : déplacement de l'arme SANS attaque
    if _spectral_exists and _match and _sw_has_move and not _sw_has_atk:
        results.append(f"  [✨ {_sum_name}] Déplacée vers {cible}.")
        results.append(
            "  → Déplacement libre (Action Gratuite) — Action Bonus non consommée.\n"
            "  Pour attaquer, déclare un [ACTION] Type: Action Bonus "
            "/ Intention: Attaquer avec l'arme spirituelle / Cible: <ennemi>."
        )
        narrative_hint = (
            f"L'arme spectrale de {char_name} se déplace vers {cible}. "
            f"Narre en 1 phrase uniquement le déplacement de l'arme. "
            f"{char_name} peut encore utiliser son Action Bonus pour attaquer."
        )
        return (
            f"[RÉSULTAT SYSTÈME — DÉPLACEMENT LIBRE ARME SPECTRALE — {char_name}]\n"
            f"✅ Déplacement confirmé. Action Gratuite — Action Bonus NON consommée.\n"
            + "\n".join(results)
            + "\n\n[INSTRUCTION NARRATIVE]\n"
            + narrative_hint
        )

    # Cas 1b : déplacement + attaque dans le même bloc
    elif _spectral_exists and _match and _sw_has_move and _sw_has_atk:
        results.append(
            f"  [✨ {_sum_name}] Déplacement vers {cible} (Action Gratuite) + Attaque (Action Bonus)."
        )
        _atk_spell  = stats.get("atk_spell", +5)
        _atk_res    = roll_dice(char_name, "1d20", _atk_spell)
        _sw_all_d   = _all_dice(regle)
        if _sw_all_d:
            _sw_dn, _sw_df, _sw_db = _sw_all_d[0]
        else:
            _sw_dn, _sw_df, _sw_db = 1, 8, max(0, _atk_spell - 4)
        _dmg_res = roll_dice(char_name, f"{_sw_dn}d{_sw_df}", _sw_db)
        results.append(f"  [jet d'attaque de sort] {_atk_res}")
        results.append(f"  [dégâts si touche] {_dmg_res}  (force)")
        results.append(f"  → MJ : confirmer Touché ou Raté")
        narrative_hint = (
            f"L'arme spectrale de {char_name} fonce vers {cible} et frappe. "
            f"Narre en 1-2 phrases le déplacement et l'attaque de l'arme. "
            f"Ne mentionne pas les chiffres."
        )
        return (
            f"[RÉSULTAT SYSTÈME — ATTAQUE ARME SPECTRALE — {char_name}]\n"
            f"⚠ AUCUN SLOT D'EMPLACEMENT REQUIS — L'ARME EST DÉJÀ INVOQUÉE.\n"
            f"Déplacement : Action Gratuite. Attaque : Action Bonus (1 seule consommation).\n"
            + "\n".join(results)
            + "\n\n[INSTRUCTION NARRATIVE]\n"
            + narrative_hint
        )

    # Cas 2 : attaque pure avec l'arme spectrale
    elif _spectral_exists and _match:
        _atk_spell  = stats.get("atk_spell", +5)
        _atk_res    = roll_dice(char_name, "1d20", _atk_spell)
        _sw_all_d   = _all_dice(regle)
        if _sw_all_d:
            _sw_dn, _sw_df, _sw_db = _sw_all_d[0]
        else:
            _sw_dn, _sw_df, _sw_db = 1, 8, max(0, _atk_spell - 4)
        _dmg_res = roll_dice(char_name, f"{_sw_dn}d{_sw_df}", _sw_db)
        results.append(
            f"  [✨ {_sum_name}] Active sur la carte — "
            f"Action Bonus d'attaque (AUCUN SLOT REQUIS)"
        )
        results.append(f"  [jet d'attaque de sort] {_atk_res}")
        results.append(f"  [dégâts si touche] {_dmg_res}  (force)")
        results.append(f"  → MJ : confirmer Touché ou Raté")
        narrative_hint = (
            f"L'arme spectrale de {char_name} est déjà présente sur le champ de bataille. "
            f"Narre en 1 phrase l'attaque de l'arme sur {cible}. "
            f"Ne mentionne pas les chiffres."
        )
        return (
            f"[RÉSULTAT SYSTÈME — ATTAQUE ARME SPECTRALE — {char_name}]\n"
            f"⚠ AUCUN SLOT D'EMPLACEMENT REQUIS — L'ARME EST DÉJÀ INVOQUÉE.\n"
            f"Cette action est une Action Bonus d'attaque, pas un nouveau lancer de sort.\n"
            + "\n".join(results)
            + "\n\n[INSTRUCTION NARRATIVE]\n"
            + narrative_hint
        )

    # Slot (uniquement pour les sorts NON-smite)
    if not is_cantrip and lvl:
        _combined_text = f"{intention} {regle}".strip()
        _spell_for_ritual = extract_spell_name_fn(_combined_text, char_name) if extract_spell_name_fn else ""
        if _spell_for_ritual and can_ritual_cast(char_name, _spell_for_ritual):
            results.append(
                f"[🕯️ RITUEL] {_spell_for_ritual} lancé en rituel "
                f"(+10 min d'incantation, aucun slot consommé)"
            )
        elif _spectral_exists:
            results.append(f"  [✨ {_match['name']}] Déjà active sur la carte — pas de nouveau slot requis.")
        else:
            results.append(f"  [slot niv.{lvl}] Validé (consommation gérée en amont par le lanceur).")

    # ── INVOCATIONS AUTOMATIQUES SUR LA CARTE ──
    if _match:
        try:
            if _cmap_win is not None:
                sum_src = _match['src']
                size = float(_match['size'])
                
                _c_col, _c_row = 0, 0
                _t_col, _t_row = None, None
                
                import re as _summon_re
                _m_coord = _summon_re.search(r'col(?:onne)?\s*(\d+)[,\s]+(?:lig(?:ne)?|rang(?:ée?)?)\s*(\d+)', cible + " " + intention, _summon_re.IGNORECASE)
                if _m_coord:
                    _t_col = int(_m_coord.group(1)) - 1
                    _t_row = int(_m_coord.group(2)) - 1
                
                for _tok in _cmap_win.tokens:
                    if _tok.get("name") == char_name:
                        _c_col, _c_row = int(round(_tok.get("col", 0))), int(round(_tok.get("row", 0)))
                    if _t_col is None and cible and _tok.get("name", "").lower() == cible.lower():
                        _t_col, _t_row = int(round(_tok.get("col", 0))), int(round(_tok.get("row", 0)))

                if _m_coord:
                    _n_col, _n_row = _t_col, _t_row
                else:
                    _ref_col, _ref_row = (_t_col, _t_row) if _t_col is not None else (_c_col, _c_row)
                    _n_col, _n_row = _cmap_win._nearest_free_cell(_ref_col, _ref_row, from_col=_c_col, from_row=_c_row)
                    
                def _spawn_on_main_thread():
                    _existing = next((t for t in _cmap_win.tokens if t.get("name") == _sum_name), None)
                    if _existing:
                        _existing["col"], _existing["row"] = _n_col, _n_row
                        _cmap_win._redraw_one_token(_existing)
                    else:
                        wpn_tok = {
                            "name": _sum_name,
                            "type": "spectral",
                            "size": size,
                            "col": _n_col, "row": _n_row,
                            "hp": -1, "max_hp": -1,
                            "source_name": sum_src,
                            "alignment": "ally",
                            "aura_radius": _match['aura'],
                            "aura_color": _match['color']
                        }
                        _cmap_win.tokens.append(wpn_tok)
                        _cmap_win._redraw_one_token(wpn_tok)
                    app._save_state()

                if hasattr(app, "root"):
                    app.root.after(0, _spawn_on_main_thread)
                    
                if _spectral_exists:
                    results.append(f"[✨ Invocation] {_sum_name} se déplace en Col {_n_col+1}, Lig {_n_row+1}.")
                else:
                    results.append(f"  [✨ Invocation] {_sum_name} apparaît en Col {_n_col+1}, Lig {_n_row+1}.")
        except Exception as e:
            print(f"[Engine] Erreur spawn invocation : {e}")

    # Jet d'attaque de sort
    if is_atk_roll and not is_heal:
        atk_spell = stats.get("atk_spell", +5)
        atk_res = roll_dice(char_name, "1d20", atk_spell)
        results.append(f"  [attaque sort] {atk_res}")

        _CANTRIP_DMG = {
            "rayon de givre":     ("1d8",  0, "froid"),
            "ray of frost":       ("1d8",  0, "froid"),
            "flamme sacrée":      ("2d8",  0, "radiant"),
            "sacred flame":       ("2d8",  0, "radiant"),
            "bourrasque":         ("1d8",  0, "tonnerre"),
            "dard du feu":        ("1d10", 0, "feu"),
            "fire bolt":          ("1d10", 0, "feu"),
            "contact glacial":    ("1d8",  0, "nécrotique"),
            "chill touch":        ("1d8",  0, "nécrotique"),
            "éclair de sorcière": ("1d10", 0, "foudre"),
            "eldritch blast":     ("1d10", 0, "force"),
            "trait de feu":       ("1d10", 0, "feu"),
            "rayon empoisonné":   ("1d4",  0, "poison"),
            "poison spray":       ("1d12", 0, "poison"),
        }
        all_dmg = _all_dice(regle)
        if all_dmg:
            _dn, _df, _db = all_dmg[0]
            _dmg_type = "magique"
        else:
            _cantrip_key = next((k for k in _CANTRIP_DMG if k in r_low or k in i_low), None)
            if _cantrip_key:
                _dice_str, _db, _dmg_type = _CANTRIP_DMG[_cantrip_key]
                _dm = _re.match(r"(\d+)d(\d+)", _dice_str)
                _dn, _df = (int(_dm.group(1)), int(_dm.group(2))) if _dm else (1, 8)
            else:
                _dn, _df, _db, _dmg_type = 1, 8, 0, "magique"

        dmg_res = roll_dice(char_name, f"{_dn}d{_df}", _db)
        results.append(f"  [dégâts si touche] {dmg_res}  ({_dmg_type})")
        results.append(f"  → MJ : confirmer Touché ou Raté")
        narrative_hint = (
            f"Le système a résolu l'attaque de sort. "
            f"Si touché : narre en 1-2 phrases l'impact du sort sur {cible}. "
            f"Si raté : narre l'esquive ou la résistance. Ne mentionne pas les chiffres."
        )
        return (
            f"[RÉSULTAT SYSTÈME — ATTAQUE DE SORT — {char_name}]\n"
            + "\n".join(results)
            + "\n\n[INSTRUCTION NARRATIVE]\n"
            + narrative_hint
        )

    # Sort à touche automatique
    _deals_damage = False
    if _sp_data:
        import json as _json_parser
        _entries_str = _json_parser.dumps(_sp_data.get("entries", []))
        _deals_damage = (
            bool(_sp_data.get("damage_inflict"))
            or "{@damage " in _entries_str
            or "{@dice " in _entries_str
        )
    if not _deals_damage and _all_dice(regle):
        _deals_damage = True

    _is_auto_hit = (
        _sp_data is not None
        and not _sp_data.get("spell_attack")
        and not _sp_data.get("saving_throw")
        and not is_heal
        and not dc_val
        and _deals_damage
    )
    if _is_auto_hit:
        is_atk_roll = False
        from spell_data import (
            get_spell_damage_expr as _gde,
            get_spell_projectile_count as _gpc,
        )
        _ah_lvl   = lvl if lvl and lvl >= 1 else (_sp_data.get("level", 1) or 1)
        _proj     = _gpc(_spell_name_candidate, _ah_lvl)
        _total_expr = _gde(_spell_name_candidate, _ah_lvl)

        if not _total_expr:
            _ah_all = _all_dice(regle)
            if _ah_all:
                _dn0, _df0, _db0 = _ah_all[0]
                _total_expr = f"{_dn0}d{_df0}+{_db0}" if _db0 else f"{_dn0}d{_df0}"

        _dmg_type     = (_sp_data.get("damage_inflict") or ["force"])[0]
        _spell_display = _spell_name_candidate or "Sort"

        results.append(
            f"  [{_spell_display} — niv.{_ah_lvl}] "
            f"{_proj} instance(s) — touche(nt) automatiquement"
        )

        _totals_ah: list[int] = []
        if _total_expr:
            _m_te = _re.match(r'(\d+)d(\d+)(?:\+(\d+))?', _total_expr)
            if _proj > 1 and _m_te:
                _dn_p = max(1, int(_m_te.group(1)) // _proj)
                _df_p = int(_m_te.group(2))
                _db_p = int(_m_te.group(3) or 0) // _proj
                for _i in range(1, _proj + 1):
                    _dr = roll_dice(char_name, f"{_dn_p}d{_df_p}", _db_p)
                    _dm = _re.search(r"Total\s*=\s*(\d+)", _dr)
                    _totals_ah.append(int(_dm.group(1)) if _dm else 0)
                    results.append(f"  [instance {_i}] {_dr}  ({_dmg_type})")
            else:
                if _m_te:
                    _dn_s = int(_m_te.group(1))
                    _df_s = int(_m_te.group(2))
                    _db_s = int(_m_te.group(3) or 0)
                    _dr = roll_dice(char_name, f"{_dn_s}d{_df_s}", _db_s)
                else:
                    _dr = roll_dice(char_name, _total_expr, 0)
                _dm = _re.search(r"Total\s*=\s*(\d+)", _dr)
                _totals_ah.append(int(_dm.group(1)) if _dm else 0)
                results.append(f"  [dégâts] {_dr}  ({_dmg_type})")

        _grand_total = sum(_totals_ah)
        _cible_note = (
            "répartis librement entre les cibles"
            if ("," in cible or " et " in cible)
            else cible
        )
        results.append(
            f"  → Total dégâts {_dmg_type} : {_grand_total} ({_cible_note})"
        )
        narrative_hint = (
            f"Le sort a été résolu automatiquement "
            f"({_proj} instance(s), {_grand_total} dégâts {_dmg_type}). "
            f"Narre en 1-2 phrases l'impact inévitable sur {cible}. "
            f"Ne mentionne pas les chiffres."
        )
        return (
            f"[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ — {char_name}]\n"
            + "\n".join(results)
            + "\n\n[INSTRUCTION NARRATIVE]\n"
            + narrative_hint
        )

    # Dés de dégâts / soin
    all_d = _all_dice(regle)
    _dmg_total_save = 0
    heal_amt = 0
    
    if all_d:
        dn2, df2, db2 = all_d[0]
        verb = "soin" if is_heal else "dégâts"
        res  = roll_dice(char_name, f"{dn2}d{df2}", db2)
        results.append(f"  [{verb}] {res}")
        if is_heal:
            m_tot_h  = _re.search(r"Total\s*=\s*(\d+)", res)
            heal_amt = int(m_tot_h.group(1)) if m_tot_h else 0
        elif dc_val:
            _m_tot_sv = _re.search(r"Total\s*=\s*(\d+)", res)
            _dmg_total_save = int(_m_tot_sv.group(1)) if _m_tot_sv else 0

    elif is_heal:
        _combined_text = regle + " " + intention
        _m_flat = _re.search(r"(\d+)\s*(?:pv|hp|points|de|d'imposition|chacun)", _combined_text, _re.IGNORECASE)
        heal_amt = int(_m_flat.group(1)) if _m_flat else 0
        
        _intent_low = _combined_text.lower()
        if heal_amt == 0 and ("imposition" in _intent_low or "lay on hands" in _intent_low):
            _nums = _re.findall(r"\b(\d+)\b", _combined_text)
            _valid_nums =[int(n) for n in _nums if int(n) > 0 and int(n) <= 100]
            if _valid_nums:
                heal_amt = _valid_nums[0]

    # Rétrogradation des faux soins
    if is_heal and not all_d and heal_amt <= 0:
        is_heal = False

    if is_heal and heal_amt > 0:
        _HEAL_NAMES =["Kaelen", "Elara", "Thorne", "Lyra"]
        try:
            from state_manager import load_state as _ls_heal
            _HEAL_NAMES = list(_ls_heal().get("characters", {}).keys()) or _HEAL_NAMES
        except Exception:
            pass
        targets =[n for n in _HEAL_NAMES if n.lower() in cible.lower()]
        if not targets:
            targets =[cible if cible.strip() not in ("-", "aucun", "aucune", "") else char_name]

        _intent_low = (regle + " " + intention).lower()
        _is_loh = "imposition" in _intent_low or "lay on hands" in _intent_low
        _curr_loh = 0
        
        if _is_loh:
            try:
                from state_manager import load_state as _ls_loh
                _st_loh = _ls_loh()
                _feats_loh = _st_loh.get("characters", {}).get(char_name, {}).get("features", {})
                _curr_loh = _feats_loh.get("lay_on_hands", 0)
            except Exception as e:
                print(f"[Lay on Hands Error] {e}")

        if not all_d and len(targets) > 1:
            _all_nums =[int(n) for n in _re.findall(r"\b(\d+)\b", regle + " " + intention) if int(n) > 0]
            if heal_amt in _all_nums and (heal_amt // len(targets)) in _all_nums:
                heal_amt = heal_amt // len(targets)
            elif any(kw in _intent_low for kw in ("partagé", "réparti", "reparti", "total", "divisé", "divise")):
                heal_amt = heal_amt // len(targets)
            elif _is_loh and (heal_amt * len(targets)) > _curr_loh >= heal_amt:
                heal_amt = heal_amt // len(targets)

        if not all_d:
            results.append(f"[soin] Total = {heal_amt} (montant fixe)")

        if _is_loh:
            _total_cost = heal_amt * len(targets)
            if _curr_loh >= _total_cost:
                results.append(f"[Imposition des mains] -{_total_cost} points demandés (reste {_curr_loh - _total_cost} après confirmation)")
            else:
                results.append(f"  [Attention] Pas assez de points Lay on Hands ({_curr_loh} vs {_total_cost} demandés) !")

        for tgt in targets:
            results.append(f"  [PV] En attente de confirmation MJ pour soigner {tgt} de {heal_amt} PV.")

    # Jet de sauvegarde avec cible
    if dc_val and not is_atk_roll and not is_heal:
        _save_stat = _save[0].upper() if (_sp_data and _sp_data.get("saving_throw")) else ""
        _save_hint = f" {_save_stat}" if _save_stat else ""
        results.append(f"  → Cibles : jet de sauvegarde{_save_hint} DC {dc_val}.")
        if _dmg_total_save:
            results.append(f"[Dégâts roulés : {_dmg_total_save} — pleins si raté, divisés par 2 si réussi]")
        else:
            results.append(f"[Aucun dégât — effets actifs uniquement si raté]")
        results.append(f"[__save_dmg_total__:{_dmg_total_save}]")
        narrative_hint = (
            f"Le MJ va confirmer le résultat du jet de sauvegarde. "
            f"Attends la confirmation avant de narrer."
        )
        return (
            f"[RÉSULTAT SYSTÈME — JET DE SAUVEGARDE — {char_name}]\n"
            + "\n".join(results)
            + "\n\n[INSTRUCTION NARRATIVE]\n"
            + narrative_hint
        )

    if is_heal:
        narrative_hint = (
            f"Le système a lancé les dés de soin. "
            f"Narre en 1-2 phrases comment {char_name} canalise l énergie divine "
            f"pour soigner {cible}. Ne mentionne pas les chiffres bruts."
        )
        return (
            f"[RÉSULTAT SYSTÈME — SOIN — {char_name}]\n"
            + "\n".join(results)
            + "\n\n[INSTRUCTION NARRATIVE]\n"
            + narrative_hint
        )

    narrative_hint = (
        f"Le système a exécuté la mécanique du sort. "
        f"Narre en 1-2 phrases comment {char_name} incante et l effet visible sur {cible}. "
        f"Ne mentionne pas les chiffres bruts."
    )
    return (
        f"[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ — {char_name}]\n"
        + "\n".join(results)
        + "\n\n[INSTRUCTION NARRATIVE]\n"
        + narrative_hint
    )


# ─── execute_move_action ─────────────────────────────────────────────────────

def execute_move_action(
    char_name: str, intention: str, regle: str, cible: str, mj_note: str,
    type_label: str, char_mechanics: dict, app
) -> str:
    """Gère la branche Mouvement sur la carte."""
    stats = char_mechanics.get(char_name, {})
    r_low_orig = regle.lower()
    i_low = intention.lower()
    t_low = (type_label or "").lower()
    results = []
    if mj_note:
        results.append(f"Note MJ : {mj_note}")

    MOVE_KW = ("mouvement", "déplace", "deplace", "repositionne",
               "avance", "recule", "cours", "marche", "approche",
               "éloigne", "eloigne", "dash", "sprint", "charge",
               "vers le nord", "vers le sud", "vers l est", "vers l ouest",
               "vers le", "cases vers", "metres vers", "mètres vers",
               "se deplace", "se déplace")
    is_move = any(k in r_low_orig or k in i_low for k in MOVE_KW) or "mouvement" in t_low

    if is_move:
        target_token_name = char_name
        _move_intent = i_low + " " + r_low_orig + " " + cible.lower()
        if any(w in _move_intent for w in ("arme", "weapon", "marteau", "hammer", "sphère", "sphere", "main", "hand", "rayon", "moonbeam", "beam", "dague", "dagger", "nuage", "cloud", "invocation", "summon")):
            _summons = {
                "arme": f"Arme ({char_name})", "weapon": f"Arme ({char_name})",
                "marteau": f"Arme ({char_name})", "hammer": f"Arme ({char_name})",
                "sphère": f"Sphère ({char_name})", "sphere": f"Sphère ({char_name})",
                "main": f"Main ({char_name})", "hand": f"Main ({char_name})",
                "rayon": f"Rayon ({char_name})", "moonbeam": f"Rayon ({char_name})", "beam": f"Rayon ({char_name})",
                "dague": f"Dagues ({char_name})", "dagger": f"Dagues ({char_name})", "nuage": f"Dagues ({char_name})", "cloud": f"Dagues ({char_name})"
            }
            _map_tokens =[]
            try:
                _cw = getattr(app, "_combat_map_win", None)
                _map_tokens = _cw.tokens if _cw else app._win_state.get("combat_map_data", {}).get("tokens",[])
            except: pass
            
            for kw, s_name in _summons.items():
                if kw in _move_intent and any(t.get("name") == s_name for t in _map_tokens):
                    target_token_name = s_name
                    break

        _cur_col, _cur_row = 0, 0
        _found_in_live = False
        try:
            _cmap_win = getattr(app, "_combat_map_win", None)
            if _cmap_win is not None:
                for _tok in getattr(_cmap_win, "tokens",[]):
                    if _tok.get("name") == target_token_name:
                        _cur_col = int(round(_tok.get("col", 0)))
                        _cur_row = int(round(_tok.get("row", 0)))
                        _found_in_live = True
                        break
        except Exception:
            pass
        if not _found_in_live:
            try:
                _map_data = app._win_state.get("combat_map_data", {})
                for _tok in _map_data.get("tokens",[]):
                    if _tok.get("name") == target_token_name:
                        _cur_col = int(round(_tok.get("col", 0)))
                        _cur_row = int(round(_tok.get("row", 0)))
                        break
            except Exception:
                pass

        _combined_mv = r_low_orig + " " + i_low + " " + cible.lower()
        _new_col, _new_row = _cur_col, _cur_row

        _m_exact_cible = _re.match(r'^col(?:onne)?\s*(\d+)[,\s]+(?:lig(?:ne)?|rang(?:ée?)?)\s*(\d+)$', cible.strip(), _re.IGNORECASE)
        _m_abs = _re.search(r'col(?:onne)?\s*(\d+)[,\s]+(?:lig(?:ne)?|rang(?:ée?)?)\s*(\d+)', r_low_orig, _re.IGNORECASE)

        if _m_exact_cible:
            _new_col = int(_m_exact_cible.group(1)) - 1
            _new_row = int(_m_exact_cible.group(2)) - 1
        elif _m_abs:
            _new_col = int(_m_abs.group(1)) - 1
            _new_row = int(_m_abs.group(2)) - 1
        else:
            _m_cases = _re.search(r'(\d+)\s*cases?', _combined_mv)
            _m_ft    = _re.search(r'(\d+)\s*ft', _combined_mv)
            _m_met   = _re.search(r'(\d+(?:[.,]\d+)?)\s*m(?:ètres?|etres?|\b)', _combined_mv)
            
            if _m_cases: _dist = int(_m_cases.group(1))
            elif _m_ft: _dist = max(1, round(int(_m_ft.group(1)) / 5.0))
            elif _m_met: _dist = max(1, round(float(_m_met.group(1).replace(",", ".")) / 1.5))
            else: _dist = 6

            _dcol, _drow = 0, 0

            try:
                _cmap_win2 = getattr(app, "_combat_map_win", None)
                _map_tokens = (
                    getattr(_cmap_win2, "tokens",[]) if _cmap_win2 is not None
                    else app._win_state.get("combat_map_data", {}).get("tokens",[])
                )
                for _other in _map_tokens:
                    _oname = _other.get("name", "").lower()
                    if (_oname and _oname in _combined_mv and _other.get("name") != char_name):
                        _oc = int(round(_other.get("col", 0)))
                        _or = int(round(_other.get("row", 0)))
                        _raw_dc = _oc - _cur_col
                        _raw_dr = _or - _cur_row
                        _mag    = max(abs(_raw_dc), abs(_raw_dr)) or 1
                        _dcol   = round(_raw_dc / _mag)
                        _drow   = round(_raw_dr / _mag)
                        break
            except Exception:
                pass

            if _dcol == 0 and _drow == 0:
                _DIR_EXACT =[
                    ("nord-est",   ( 1, -1)), ("nord-ouest", (-1, -1)),
                    ("sud-est",    ( 1,  1)), ("sud-ouest",  (-1,  1)),
                    ("north-east", ( 1, -1)), ("north-west", (-1, -1)),
                    ("south-east", ( 1,  1)), ("south-west", (-1,  1)),
                ]
                for _kd, (_dc, _dr) in _DIR_EXACT:
                    if _kd in _combined_mv:
                        _dcol, _drow = _dc, _dr
                        break

            if _dcol == 0 and _drow == 0:
                _DIR_WORD =[
                    ("nord",  ( 0, -1)), ("north", ( 0, -1)),
                    ("sud",   ( 0,  1)), ("south", ( 0,  1)),
                    ("est",   ( 1,  0)), ("east",  ( 1,  0)),
                    ("ouest", (-1,  0)), ("west",  (-1,  0)),
                ]
                for _kd, (_dc, _dr) in _DIR_WORD:
                    if _kd == "est" and not _re.search(r"(vers l'|à l'|direction )\b" + _kd + r"\b", _combined_mv):
                        continue
                    if _re.search(r'\b' + _kd + r'\b', _combined_mv):
                        _dcol, _drow = _dc, _dr
                        break

            _new_col = _cur_col + _dcol * _dist
            _new_row = _cur_row + _drow * _dist

            _chebyshev = max(abs(_new_col - _cur_col), abs(_new_row - _cur_row))
            _ft_requested = _chebyshev * 5
            try:
                from combat_tracker_state import COMBAT_STATE as _CS2
                _rem_mv = _CS2.get("turn_res", {}).get(char_name, {}).get("movement", stats.get("speed", 30))
            except Exception:
                _rem_mv = stats.get("speed", 30)

            if _ft_requested > _rem_mv:
                _allowed_cases = _rem_mv // 5
                if _chebyshev > 0:
                    _ratio = _allowed_cases / _chebyshev
                    _new_col = _cur_col + round((_new_col - _cur_col) * _ratio)
                    _new_row = _cur_row + round((_new_row - _cur_row) * _ratio)

            try:
                _cmap = getattr(app, "_combat_map_win", None)
                _all_toks = getattr(_cmap, "tokens", []) if _cmap else app._win_state.get("combat_map_data", {}).get("tokens", [])
                for _ot in _all_toks:
                    if (int(round(_ot.get("col", 0))) == _new_col
                            and int(round(_ot.get("row", 0))) == _new_row
                            and _ot.get("name") != char_name):
                        _dc = _new_col - _cur_col
                        _dr = _new_row - _cur_row
                        _mag = max(abs(_dc), abs(_dr), 1)
                        _new_col -= round(_dc / _mag)
                        _new_row -= round(_dr / _mag)
                        break
            except Exception:
                pass

        try:
            _cmap_win3 = getattr(app, "_combat_map_win", None)
            if _cmap_win3 is not None:
                _grid_cols = getattr(_cmap_win3, "cols", None) or app._win_state.get("combat_map_data", {}).get("cols", 200)
                _grid_rows = getattr(_cmap_win3, "rows", None) or app._win_state.get("combat_map_data", {}).get("rows", 200)
            else:
                _grid_cols = app._win_state.get("combat_map_data", {}).get("cols", 200)
                _grid_rows = app._win_state.get("combat_map_data", {}).get("rows", 200)
        except Exception:
            _grid_cols, _grid_rows = 200, 200
            
        _new_col = max(0, min(_grid_cols - 1, _new_col))
        _new_row = max(0, min(_grid_rows - 1, _new_row))

        _dist_actual = max(abs(_new_col - _cur_col), abs(_new_row - _cur_row))
        _dist_m = _dist_actual * 1.5
        _dist_ft = _dist_actual * 5

        _rem_mov_str = ""
        if target_token_name == char_name:
            try:
                from combat_tracker_state import COMBAT_STATE as _CS
                if _CS.get("active") and _CS.get("active_combatant") == char_name:
                    _tr = _CS.setdefault("turn_res", {}).setdefault(char_name, {})
                    _base_speed = stats.get("speed", 30)
                    _cur_mov = _tr.get("movement", _base_speed)
                    _tr["movement"] = max(0, _cur_mov - _dist_ft)
                    _rem_mov_str = f"\n  Vitesse restante  : {_tr['movement']} ft"
            except Exception:
                pass

        results.append(f"🏃 {target_token_name} — {intention}")
        results.append(f"  Position actuelle : Col {_cur_col+1}, Lig {_cur_row+1}")
        results.append(f"  Destination       : Col {_new_col+1}, Lig {_new_row+1}")
        results.append(f"  Distance          : {_dist_actual} cases ({_dist_ft} ft / {_dist_m:.1f} m){_rem_mov_str}")
        results.append(f"[MOVE_TOKEN:{target_token_name}:{_new_col}:{_new_row}]")

        _melee_reminder = ""
        try:
            _cmap_post = getattr(app, "_combat_map_win", None)
            _all_toks_post = (
                getattr(_cmap_post, "tokens", []) if _cmap_post is not None
                else app._win_state.get("combat_map_data", {}).get("tokens", [])
            )
            _pc_names = set(char_mechanics.keys())
            _in_melee = []
            _nearest_name = None
            _nearest_dist = 9999
            for _pt in _all_toks_post:
                _pn = _pt.get("name", "")
                if not _pn or _pn == target_token_name:
                    continue
                if _pn in _pc_names or _pt.get("alignment") == "ally":
                    continue
                _pc2 = int(round(_pt.get("col", 0)))
                _pr2 = int(round(_pt.get("row", 0)))
                _cheb = max(abs(_pc2 - _new_col), abs(_pr2 - _new_row))
                if _cheb <= 1:
                    _in_melee.append(_pn)
                elif _cheb < _nearest_dist:
                    _nearest_dist = _cheb
                    _nearest_name = _pn
            if _in_melee:
                _names_str = ", ".join(_in_melee)
                _melee_reminder = (
                    f"\n⚔️ PORTÉE MÊLÉE : {target_token_name} EST à portée de mêlée de "
                    f"{_names_str} — une attaque corps-à-corps est possible ce tour."
                )
            elif _nearest_name:
                _dist_ft_near = _nearest_dist * 5
                _melee_reminder = (
                    f"\n⚠️ PORTÉE MÊLÉE : {target_token_name} N'EST PAS encore à portée de mêlée. "
                    f"Ennemi le plus proche : {_nearest_name} "
                    f"({_nearest_dist} case{'s' if _nearest_dist > 1 else ''} / {_dist_ft_near} ft). "
                    f"Une attaque corps-à-corps n'est PAS possible depuis cette position."
                )
            else:
                _melee_reminder = (
                    f"\n⚠️ PORTÉE MÊLÉE : Aucun ennemi détecté à portée de mêlée."
                )
        except Exception:
            pass
        if _melee_reminder:
            results.append(_melee_reminder)

        narrative_hint = (
            f"Le système a calculé le déplacement. "
            f"Narre en 1 phrase le mouvement de {target_token_name} : {intention}. "
            f"Décris la façon dont il se déplace, son attitude, pas les coordonnées. "
            f"Vérifie le rappel PORTÉE MÊLÉE ci-dessus avant de proposer ou décrire "
            f"toute attaque au corps-à-corps."
        )
    else:
        results.append(f"⚙️ {char_name} — {intention}")
        results.append(f"  Mécanique : {regle} | Cible : {cible}")
        narrative_hint = (
            f"Narre en 1-2 phrases l action de {char_name} : {intention}. "
            f"Si des dés sont encore nécessaires, pose un nouveau [ACTION]."
        )

    return (
        f"[RÉSULTAT SYSTÈME — ACTION CONFIRMÉE PAR MJ — {char_name}]\n"
        + "\n".join(results)
        + "\n\n[INSTRUCTION NARRATIVE]\n"
        + narrative_hint
    )