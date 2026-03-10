"""
npc_bestiary_panel.py
─────────────────────
Widget qui affiche les PNJs actuellement dans le groupe et permet d'ouvrir
leur fiche de monstre (tirée du bestiary D&D 5e).

Structure d'un PNJ du groupe dans campaign_state.json :
{
    "name": "Ismark",
    "voice": "fr-FR-AlainNeural",
    "speed": "+0%",
    "color": "#a0c4ff",
    "bestiary_name": "Guard",      ← nom dans le bestiary (optionnel)
    "bestiary_source": "MM",       ← source (optionnel)
    "hp_current": 11,              ← PV actuels (optionnel)
    "notes": "Frère d'Ireena…"     ← notes MJ (optionnel)
}
"""

import json
import copy as _copy_module
import glob
import os
import tkinter as tk
from tkinter import scrolledtext
import re

# ─── Répertoire du bestiary ───────────────────────────────────────────────────
_BESTIARY_DIR   = os.path.join(os.path.dirname(__file__), "bestiary")
_LEGENDARY_FILE = os.path.join(_BESTIARY_DIR, "legendarygroups.json")

# ─── Cache des données du bestiary ───────────────────────────────────────────
_BESTIARY_DATA: dict[str, dict] = {}    # name.lower() → monster dict (résolu)
_FLUFF_DATA:    dict[str, dict] = {}    # name.lower() → fluff dict
_LEGENDARY_DATA: dict[str, dict] = {}  # name.lower() → legendary group dict
_BESTIARY_NAMES: list[str] = []        # liste triée pour l'autocomplétion

# ─── Résolution _copy / _versions (format 5etools) ───────────────────────────

def _apply_mod(base: dict, mod: dict) -> dict:
    """
    Applique un bloc _mod (format 5etools) à un dict de monstre de base.
    Supporte : appendArr, prependArr, replaceArr, removeArr, insertArr,
               replace (direct), et les overrides de champs scalaires.
    """
    result = _copy_module.deepcopy(base)
    for field, op in mod.items():
        if field == "_":
            # Opérations globales (addSpells, etc.) — ignorées pour l'affichage
            continue
        if not isinstance(op, dict) or "mode" not in op:
            # Override direct du champ
            result[field] = op
            continue
        mode = op["mode"]
        if mode == "appendArr":
            items = op.get("items", [])
            if field not in result:
                result[field] = []
            if isinstance(items, list):
                result[field].extend(items)
            else:
                result[field].append(items)
        elif mode == "prependArr":
            items = op.get("items", [])
            arr   = result.get(field, [])
            result[field] = (items if isinstance(items, list) else [items]) + arr
        elif mode == "replaceArr":
            replace_name = op.get("replace")
            new_item     = op.get("items")
            arr = result.get(field, [])
            result[field] = [
                new_item if (isinstance(x, dict) and x.get("name") == replace_name) else x
                for x in arr
            ]
        elif mode == "removeArr":
            names = op.get("names", [])
            if isinstance(names, str):
                names = [names]
            arr = result.get(field, [])
            result[field] = [
                x for x in arr
                if not (isinstance(x, dict) and x.get("name") in names)
            ]
        elif mode == "insertArr":
            items = op.get("items", [])
            idx   = op.get("index", 0)
            arr   = result.get(field, [])
            chunk = items if isinstance(items, list) else [items]
            result[field] = arr[:idx] + chunk + arr[idx:]
        elif mode == "replace":
            # { mode: "replace", replace: "OldName", items: {...} }
            # Same as replaceArr but the field key IS the array name
            replace_name = op.get("replace")
            new_item     = op.get("items")
            arr = result.get(field, [])
            result[field] = [
                new_item if (isinstance(x, dict) and x.get("name") == replace_name) else x
                for x in arr
            ]
        else:
            # Fallback : override direct
            result[field] = op
    return result


def _resolve_copy(raw: dict, index_by_key: dict, index_by_name: dict) -> dict:
    """
    Résout récursivement le champ _copy d'un monstre.
    index_by_key  : {(name.lower(), SOURCE) → dict}
    index_by_name : {name.lower() → dict}  (fallback toutes sources)
    """
    copy_ref = raw.get("_copy")
    if not copy_ref:
        return raw

    base_name   = copy_ref.get("name", "")
    base_source = copy_ref.get("source", "").upper()

    # Cherche le base d'abord par (nom, source), puis par nom seul
    base = (index_by_key.get((base_name.lower(), base_source))
            or index_by_name.get(base_name.lower()))

    if not base:
        print(f"[Bestiary] _copy non résolu : {base_name} ({base_source})")
        return raw  # Retourne tel quel si la base est introuvable

    # Résolution récursive de la base
    base = _resolve_copy(base, index_by_key, index_by_name)

    # Fusion : base + overrides du monstre enfant
    result = _copy_module.deepcopy(base)
    for k, v in raw.items():
        if k not in ("_copy", "_mod"):
            result[k] = v

    # Application des _mod
    if "_mod" in raw:
        result = _apply_mod(result, raw["_mod"])

    return result


def _expand_versions(base: dict) -> list[dict]:
    """
    Développe les _versions d'un monstre en entrées autonomes.
    Retourne une liste de dicts (sans le champ _versions).
    """
    versions = base.get("_versions", [])
    expanded = []
    for v in versions:
        if not isinstance(v, dict) or "name" not in v:
            continue
        result = _copy_module.deepcopy(base)
        result.pop("_versions", None)
        result["name"] = v["name"]
        # Overrides directs (champs non-underscore)
        for k, val in v.items():
            if not k.startswith("_") and k not in ("name", "variant"):
                result[k] = val
        # _mod
        if "_mod" in v:
            result = _apply_mod(result, v["_mod"])
        expanded.append(result)
    return expanded


def _load_bestiary():
    """
    Charge tous les fichiers bestiary-*.json du dossier bestiary/ en mémoire.
    - Résout les références _copy inter-fichiers
    - Étend les _versions en entrées autonomes
    - Charge également tous les fluff-bestiary-*.json
    - Appelé une seule fois (lazy).
    """
    global _BESTIARY_DATA, _FLUFF_DATA, _LEGENDARY_DATA, _BESTIARY_NAMES
    if _BESTIARY_DATA:
        return

    # ── Étape 1 : collecter TOUS les monstres bruts (toutes sources) ───────
    raw_monsters: list[dict] = []
    stat_files = sorted(glob.glob(os.path.join(_BESTIARY_DIR, "bestiary-*.json")))
    if not stat_files:
        print(f"[Bestiary] Aucun fichier bestiary-*.json trouvé dans {_BESTIARY_DIR}")
        return

    for path in stat_files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            batch = data.get("monster", [])
            raw_monsters.extend(batch)
            print(f"[Bestiary] Chargé {len(batch)} monstres depuis {os.path.basename(path)}")
        except Exception as e:
            print(f"[Bestiary] Erreur lecture {path}: {e}")

    # ── Étape 2 : construire les index bruts (avant résolution) ────────────
    raw_by_key:  dict[tuple, dict] = {}   # (name.lower(), SOURCE) → raw dict
    raw_by_name: dict[str, dict]   = {}   # name.lower() → raw dict (dernier vu)

    for m in raw_monsters:
        name   = m.get("name", "")
        source = m.get("source", "").upper()
        raw_by_key[(name.lower(), source)] = m
        raw_by_name[name.lower()] = m  # écrase ; MM prioritaire si chargé en premier

    # ── Étape 3 : résoudre _copy et étendre _versions ──────────────────────
    for m in raw_monsters:
        resolved = _resolve_copy(m, raw_by_key, raw_by_name)
        name_key = resolved.get("name", "").lower()
        _BESTIARY_DATA[name_key] = resolved

        # Étend les _versions en entrées autonomes
        for variant in _expand_versions(resolved):
            v_key = variant.get("name", "").lower()
            _BESTIARY_DATA[v_key] = variant

    _BESTIARY_NAMES = sorted(_BESTIARY_DATA.keys())
    print(f"[Bestiary] {len(_BESTIARY_DATA)} entrées totales après résolution.")

    # ── Étape 4 : charger le fluff (lore) ──────────────────────────────────
    fluff_files = sorted(glob.glob(os.path.join(_BESTIARY_DIR, "fluff-bestiary-*.json")))
    for path in fluff_files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for m in data.get("monsterFluff", []):
                key = m.get("name", "").lower()
                _FLUFF_DATA[key] = m
        except Exception as e:
            print(f"[Bestiary] Erreur lecture fluff {path}: {e}")

    # ── Étape 5 : groupes légendaires ──────────────────────────────────────
    try:
        with open(_LEGENDARY_FILE, encoding="utf-8") as f:
            raw_leg = json.load(f)
        for g in raw_leg.get("legendaryGroup", []):
            key = g.get("name", "").lower()
            _LEGENDARY_DATA[key] = g
    except Exception as e:
        print(f"[Bestiary] Impossible de charger {_LEGENDARY_FILE}: {e}")


def search_monsters(query: str, max_results: int = 12) -> list[str]:
    """Retourne les noms originaux de monstres correspondant à la recherche."""
    _load_bestiary()
    q = query.lower().strip()
    if not q:
        return [_BESTIARY_DATA[k]["name"] for k in _BESTIARY_NAMES[:max_results]]
    exact  = [k for k in _BESTIARY_NAMES if k == q]
    starts = [k for k in _BESTIARY_NAMES if k.startswith(q) and k != q]
    contains = [k for k in _BESTIARY_NAMES if q in k and not k.startswith(q)]
    results = (exact + starts + contains)[:max_results]
    return [_BESTIARY_DATA[k]["name"] for k in results]


def get_monster(name: str) -> dict | None:
    """Retourne le dict complet d'un monstre (ou None si introuvable)."""
    _load_bestiary()
    return _BESTIARY_DATA.get(name.lower())


def get_monster_fluff(name: str) -> dict | None:
    """Retourne le lore d'un monstre (ou None)."""
    _load_bestiary()
    return _FLUFF_DATA.get(name.lower())


def get_legendary_group(name: str) -> dict | None:
    """Retourne le groupe légendaire d'un monstre (ou None)."""
    _load_bestiary()
    return _LEGENDARY_DATA.get(name.lower())


# ─── Helpers de rendu ─────────────────────────────────────────────────────────

def _fmt_entries(entries) -> str:
    """Convertit la liste d'entrées JSON du bestiary en texte lisible."""
    if not entries:
        return ""
    parts = []
    for e in entries:
        if isinstance(e, str):
            # Nettoie les tags {@…}
            text = re.sub(r'\{@\w+\s*([^}]*)\}', r'\1', e)
            parts.append(text)
        elif isinstance(e, dict):
            if e.get("type") == "entries":
                name = e.get("name", "")
                sub  = _fmt_entries(e.get("entries", []))
                if name:
                    parts.append(f"► {name}: {sub}")
                else:
                    parts.append(sub)
            elif e.get("type") == "list":
                for item in e.get("items", []):
                    if isinstance(item, str):
                        text = re.sub(r'\{@\w+\s*([^}]*)\}', r'\1', item)
                        parts.append(f"  • {text}")
                    elif isinstance(item, dict):
                        t = _fmt_entries(item.get("entries", []))
                        parts.append(f"  • {t}")
            else:
                t = _fmt_entries(e.get("entries", []))
                if t:
                    parts.append(t)
    return "\n".join(parts)


def _fmt_damage_list(entries: list, key: str) -> str:
    """
    Formate une liste resist/immune du format 5etools.
    Chaque item peut être :
      - str                    → affiché tel quel
      - {"resist": [...], "note": "...", ...}  → liste + note
      - {"immune": [...], "note": "...", ...}  → idem
      - {"special": "..."}     → affiché tel quel
    """
    parts = []
    for item in entries:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, list):
            # Liste imbriquée directe — aplatit récursivement
            parts.append(_fmt_damage_list(item, key))
        elif isinstance(item, dict):
            if "special" in item:
                parts.append(item["special"])
            else:
                sub = item.get(key, [])
                # sub peut être une liste de strings ou de dicts
                sub_str = _fmt_damage_list(sub, key) if sub else ""
                note = item.get("note", "")
                pre  = item.get("preNote", "")
                chunk = sub_str
                if pre:
                    chunk = f"{pre} {chunk}".strip()
                if note:
                    chunk = f"{chunk} ({note})"
                if chunk:
                    parts.append(chunk)
        else:
            parts.append(str(item))
    return ", ".join(p for p in parts if p)


def _fmt_condition_list(entries: list) -> str:
    """
    Formate une liste conditionImmune.
    Items peuvent être str ou dict avec "condition".
    """
    parts = []
    for item in entries:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, list):
            parts.append(_fmt_condition_list(item))
        elif isinstance(item, dict):
            cond = item.get("condition", "")
            note = item.get("note", "")
            chunk = cond or str(item)
            if note:
                chunk = f"{chunk} ({note})"
            parts.append(chunk)
        else:
            parts.append(str(item))
    return ", ".join(p for p in parts if p)



    if not actions:
        return "(aucune)"
    lines = []
    for a in actions:
        name = a.get("name", "?")
        desc = _fmt_entries(a.get("entries", []))
        lines.append(f"▸ {name}\n  {desc}")
    return "\n\n".join(lines)


def _fmt_cr(cr) -> str:
    if isinstance(cr, dict):
        return str(cr.get("cr", "?"))
    return str(cr)


def _fmt_type(t) -> str:
    if isinstance(t, dict):
        base = t.get("type", "?")
        tags = t.get("tags", [])
        if tags:
            return f"{base} ({', '.join(tags)})"
        return base
    return str(t)


def _fmt_ac(ac_list) -> str:
    if not ac_list:
        return "?"
    a = ac_list[0]
    if isinstance(a, int):
        return str(a)
    if isinstance(a, dict):
        val  = str(a.get("ac", "?"))
        frm  = a.get("from", [])
        cond = a.get("condition", "")
        extra = ", ".join(frm)
        if cond:
            extra = f"{extra} {cond}".strip()
        return f"{val} ({extra})" if extra else val
    return str(a)


def _fmt_speed(speed: dict) -> str:
    parts = []
    for k, v in speed.items():
        if k == "walk":
            parts.insert(0, f"{v} ft.")
        else:
            parts.append(f"{k} {v} ft.")
    return ", ".join(parts)


def _ability_mod(score: int) -> str:
    mod = (score - 10) // 2
    return f"{score} ({mod:+d})"


# ─── Fenêtre fiche monstre ─────────────────────────────────────────────────────

class MonsterSheetWindow:
    """
    Fenêtre Toplevel affichant la fiche complète d'un monstre D&D 5e.
    Peut être ouverte avec un monstre pré-sélectionné ou vide (avec recherche).
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
                 on_select_callback=None, win_state: dict = None, track_fn=None):
        """
        root            : fenêtre parente Tk
        npc_name        : nom du PNJ (pour le titre)
        bestiary_name   : nom dans le bestiary (si déjà sélectionné)
        on_select_callback(bestiary_name: str) : appelé quand le MJ sélectionne un monstre
        win_state       : dict de persistance de géométrie
        track_fn        : fonction _track_window de DnDApp
        """
        self.root = root
        self.npc_name = npc_name
        self.on_select_callback = on_select_callback

        _load_bestiary()

        win = tk.Toplevel(root)
        win.title(f"📋 {npc_name}" + (f" — {bestiary_name}" if bestiary_name else ""))
        win.configure(bg=self.BG)
        win.resizable(True, True)
        win.minsize(560, 600)
        win.geometry("620x780")
        self.win = win

        if track_fn:
            track_fn(f"monster_{npc_name}", win)

        # ── Layout principal ─────────────────────────────────────────────────
        # Barre de recherche en haut
        search_bar = tk.Frame(win, bg=self.BG2, pady=6)
        search_bar.pack(fill=tk.X, padx=0, pady=0)

        tk.Label(search_bar, text="🔍 Monstre :", bg=self.BG2, fg=self.FG_MID,
                 font=("Arial", 9)).pack(side=tk.LEFT, padx=(10, 4))

        self._search_var = tk.StringVar(value=bestiary_name or "")
        search_entry = tk.Entry(search_bar, textvariable=self._search_var,
                                bg=self.BG3, fg=self.FG, font=("Consolas", 10),
                                insertbackground=self.FG, relief="flat", width=28)
        search_entry.pack(side=tk.LEFT, padx=(0, 6), ipady=4)
        search_entry.bind("<KeyRelease>", self._on_search_key)
        search_entry.bind("<Return>",     self._on_search_confirm)

        self._select_btn = tk.Button(
            search_bar, text="✅ Sélectionner",
            bg="#1a3a1a", fg=self.GREEN,
            font=("Arial", 9, "bold"), relief="flat", padx=8,
            command=self._confirm_selection
        )
        self._select_btn.pack(side=tk.RIGHT, padx=8)

        # Dropdown de suggestions
        self._suggest_frame = tk.Frame(win, bg=self.BG2, relief="flat", bd=1)
        self._suggest_labels: list[tk.Label] = []
        self._suggest_visible = False

        # Corps scrollable de la fiche
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

        # Molette
        self._canvas.bind("<MouseWheel>",
                          lambda e: self._canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
        self._inner.bind("<MouseWheel>",
                         lambda e: self._canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        # Affiche la fiche si un monstre est déjà connu
        if bestiary_name:
            self._show_monster(bestiary_name)
        else:
            self._show_empty()

    # ── Recherche ─────────────────────────────────────────────────────────────

    def _on_search_key(self, event=None):
        q = self._search_var.get().strip()
        suggestions = search_monsters(q, 10)
        self._show_suggestions(suggestions)

    def _on_search_confirm(self, event=None):
        q = self._search_var.get().strip()
        if not q:
            return
        # Essai direct
        m = get_monster(q)
        if m:
            self._hide_suggestions()
            self._show_monster(m["name"])
        else:
            # Prend la première suggestion
            suggestions = search_monsters(q, 1)
            if suggestions:
                self._search_var.set(suggestions[0])
                self._hide_suggestions()
                self._show_monster(suggestions[0])

    def _show_suggestions(self, names: list[str]):
        self._hide_suggestions()
        if not names:
            return
        x = self._canvas.winfo_x()
        y = 42  # sous la barre de recherche

        self._suggest_frame.place(x=10, y=y, width=280)
        self._suggest_frame.lift()

        for name in names:
            lbl = tk.Label(self._suggest_frame, text=name, bg=self.BG2,
                           fg=self.FG, font=("Consolas", 9),
                           anchor="w", padx=8, pady=3, cursor="hand2")
            lbl.pack(fill=tk.X)
            lbl.bind("<Button-1>", lambda e, n=name: self._pick_suggestion(n))
            lbl.bind("<Enter>",    lambda e, l=lbl: l.config(bg=self.BG3))
            lbl.bind("<Leave>",    lambda e, l=lbl: l.config(bg=self.BG2))
            self._suggest_labels.append(lbl)
        self._suggest_visible = True

    def _hide_suggestions(self):
        for lbl in self._suggest_labels:
            lbl.destroy()
        self._suggest_labels.clear()
        self._suggest_frame.place_forget()
        self._suggest_visible = False

    def _pick_suggestion(self, name: str):
        self._search_var.set(name)
        self._hide_suggestions()
        self._show_monster(name)

    def _confirm_selection(self):
        """Valide le monstre actuellement affiché et appelle le callback."""
        name = self._search_var.get().strip()
        if name and get_monster(name):
            if self.on_select_callback:
                self.on_select_callback(name)
            self.win.title(f"📋 {self.npc_name} — {name}")

    # ── Rendu de la fiche ─────────────────────────────────────────────────────

    def _clear_body(self):
        for w in self._inner.winfo_children():
            w.destroy()

    def _show_empty(self):
        self._clear_body()
        tk.Label(self._inner, text="🔍 Recherchez un monstre ci-dessus",
                 bg=self.BG, fg=self.FG_DIM, font=("Consolas", 10, "italic"),
                 pady=40).pack()
        tk.Label(self._inner, text="Tapez un nom et appuyez sur Entrée",
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
        txt.bind("<MouseWheel>",
                 lambda e: self._canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

    def _show_monster(self, name: str):
        m = get_monster(name)
        if not m:
            self._show_empty()
            return

        self._clear_body()
        self._hide_suggestions()

        # ── EN-TÊTE ─────────────────────────────────────────────────────────
        hdr = tk.Frame(self._inner, bg="#1a0808", pady=8)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text=m.get("name", "?"), bg="#1a0808", fg=self.ACCENT,
                 font=("Arial", 16, "bold"), anchor="w", padx=12).pack(side=tk.LEFT)
        cr_txt = f"FP {_fmt_cr(m.get('cr', '?'))}"
        tk.Label(hdr, text=cr_txt, bg="#1a0808", fg=self.GOLD,
                 font=("Consolas", 11, "bold"), anchor="e", padx=12).pack(side=tk.RIGHT)

        # Taille · Type · Alignement
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

        # Résistances / Immunités
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

        # ── TRAITS ──────────────────────────────────────────────────────────
        traits = m.get("trait", [])
        if traits:
            self._section("Traits", self.PURPLE)
            for t in traits:
                t_name = t.get("name", "?")
                t_desc = _fmt_entries(t.get("entries", []))
                tk.Label(self._inner, text=f"▸ {t_name}", bg=self.BG, fg=self.PURPLE,
                         font=("Consolas", 9, "bold"), anchor="w", padx=10,
                         pady=2).pack(fill=tk.X)
                if t_desc:
                    tk.Label(self._inner, text=t_desc, bg=self.BG, fg=self.FG_MID,
                             font=("Consolas", 9), anchor="w", padx=20,
                             wraplength=540, justify=tk.LEFT).pack(fill=tk.X)

            self._sep()

        # ── ACTIONS ─────────────────────────────────────────────────────────
        actions = m.get("action", [])
        if actions:
            self._section("Actions", self.ACCENT)
            for a in actions:
                a_name = a.get("name", "?")
                a_desc = _fmt_entries(a.get("entries", []))
                tk.Label(self._inner, text=f"▸ {a_name}", bg=self.BG, fg=self.ACCENT,
                         font=("Consolas", 9, "bold"), anchor="w", padx=10,
                         pady=2).pack(fill=tk.X)
                if a_desc:
                    tk.Label(self._inner, text=a_desc, bg=self.BG, fg=self.FG_MID,
                             font=("Consolas", 9), anchor="w", padx=20,
                             wraplength=540, justify=tk.LEFT).pack(fill=tk.X)
            self._sep()

        # ── ACTIONS BONUS ────────────────────────────────────────────────────
        bonus = m.get("bonus_action", m.get("bonusAction", []))
        if bonus:
            self._section("Actions Bonus", "#ffb74d")
            for a in bonus:
                a_name = a.get("name", "?")
                a_desc = _fmt_entries(a.get("entries", []))
                tk.Label(self._inner, text=f"▸ {a_name}", bg=self.BG, fg="#ffb74d",
                         font=("Consolas", 9, "bold"), anchor="w", padx=10,
                         pady=2).pack(fill=tk.X)
                if a_desc:
                    tk.Label(self._inner, text=a_desc, bg=self.BG, fg=self.FG_MID,
                             font=("Consolas", 9), anchor="w", padx=20,
                             wraplength=540, justify=tk.LEFT).pack(fill=tk.X)
            self._sep()

        # ── RÉACTIONS ────────────────────────────────────────────────────────
        reactions = m.get("reaction", [])
        if reactions:
            self._section("Réactions", self.BLUE)
            for r in reactions:
                r_name = r.get("name", "?")
                r_desc = _fmt_entries(r.get("entries", []))
                tk.Label(self._inner, text=f"▸ {r_name}", bg=self.BG, fg=self.BLUE,
                         font=("Consolas", 9, "bold"), anchor="w", padx=10,
                         pady=2).pack(fill=tk.X)
                if r_desc:
                    tk.Label(self._inner, text=r_desc, bg=self.BG, fg=self.FG_MID,
                             font=("Consolas", 9), anchor="w", padx=20,
                             wraplength=540, justify=tk.LEFT).pack(fill=tk.X)
            self._sep()

        # ── ACTIONS LÉGENDAIRES ───────────────────────────────────────────────
        legendary = m.get("legendary", [])
        leg_group_name = m.get("legendaryGroup", {})
        if isinstance(leg_group_name, dict):
            leg_group_name = leg_group_name.get("name", "")

        if legendary or leg_group_name:
            self._section("Actions Légendaires", self.GOLD)
            # Intro du groupe légendaire
            lg = get_legendary_group(leg_group_name) if leg_group_name else None
            if lg:
                intro = _fmt_entries(lg.get("lairActions", lg.get("regional", [])))
                if intro:
                    tk.Label(self._inner, text=intro, bg=self.BG, fg=self.FG_DIM,
                             font=("Consolas", 8, "italic"), anchor="w", padx=10,
                             wraplength=540, justify=tk.LEFT, pady=3).pack(fill=tk.X)
            for la in legendary:
                la_name = la.get("name", "?")
                la_desc = _fmt_entries(la.get("entries", []))
                tk.Label(self._inner, text=f"▸ {la_name}", bg=self.BG, fg=self.GOLD,
                         font=("Consolas", 9, "bold"), anchor="w", padx=10,
                         pady=2).pack(fill=tk.X)
                if la_desc:
                    tk.Label(self._inner, text=la_desc, bg=self.BG, fg=self.FG_MID,
                             font=("Consolas", 9), anchor="w", padx=20,
                             wraplength=540, justify=tk.LEFT).pack(fill=tk.X)
            self._sep()

        # ── LORE / FLUFF ──────────────────────────────────────────────────────
        fluff = get_monster_fluff(name)
        if fluff:
            fluff_text = _fmt_entries(fluff.get("entries", []))
            if fluff_text and fluff_text.strip():
                self._section("Lore", self.FG_DIM)
                self._text_block(fluff_text[:1200] + ("…" if len(fluff_text) > 1200 else ""),
                                 color=self.FG_DIM)

        # ── Pad bas ──────────────────────────────────────────────────────────
        tk.Frame(self._inner, bg=self.BG, height=20).pack()
        self._canvas.yview_moveto(0)


# ─── Panel PNJs du groupe (intégré dans la sidebar de DnDApp) ─────────────────

class GroupNPCPanel:
    """
    Panneau latéral listant les PNJs actuellement dans le groupe.
    Cliquer sur un nom ouvre MonsterSheetWindow.
    Géré dans state_manager via get_group_npcs / save_group_npcs.
    """

    BG     = "#0d1a0d"
    BG2    = "#0f1f0f"
    FG     = "#a5d6a7"
    FG_DIM = "#3a5a3a"
    ACCENT = "#4CAF50"

    def __init__(self, parent_frame: tk.Frame, root, win_state: dict,
                 save_win_state_fn, track_fn, msg_queue):
        self.root             = root
        self._win_state       = win_state
        self._save_ws         = save_win_state_fn
        self._track           = track_fn
        self._msg_queue       = msg_queue
        self._open_sheets: dict[str, MonsterSheetWindow] = {}

        # Import ici pour éviter une dépendance circulaire
        from state_manager import get_group_npcs, save_group_npcs
        self._get_npcs  = get_group_npcs
        self._save_npcs = save_group_npcs

        # ── Conteneur principal ───────────────────────────────────────────────
        self._frame = tk.Frame(parent_frame, bg=self.BG)
        self._frame.pack(fill=tk.X, padx=8, pady=(0, 4))

        # En-tête
        hdr = tk.Frame(self._frame, bg=self.BG)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="👥 PNJs DU GROUPE", bg=self.BG, fg=self.FG,
                 font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=6, pady=(6, 2))
        tk.Button(hdr, text="＋", bg=self.BG, fg=self.ACCENT,
                  font=("Arial", 10, "bold"), relief="flat",
                  command=self._add_npc).pack(side=tk.RIGHT, padx=4, pady=2)

        # Zone de la liste
        self._list_frame = tk.Frame(self._frame, bg=self.BG)
        self._list_frame.pack(fill=tk.X)

        self._refresh()

    def _refresh(self):
        """Reconstruit la liste des PNJs du groupe."""
        for w in self._list_frame.winfo_children():
            w.destroy()

        npcs = self._get_npcs()
        if not npcs:
            tk.Label(self._list_frame, text="Aucun PNJ dans le groupe",
                     bg=self.BG, fg=self.FG_DIM,
                     font=("Consolas", 8, "italic"),
                     anchor="w", padx=8, pady=4).pack(fill=tk.X)
            return

        for i, npc in enumerate(npcs):
            name         = npc.get("name", "?")
            bestiary     = npc.get("bestiary_name", "")
            color        = npc.get("color", self.FG)
            hp_cur       = npc.get("hp_current")
            row_bg       = "#0d1a0d" if i % 2 == 0 else "#0f220f"

            row = tk.Frame(self._list_frame, bg=row_bg, cursor="hand2")
            row.pack(fill=tk.X, pady=1)

            # Indicateur monstre associé
            icon = "📋" if bestiary else "❓"
            tk.Label(row, text=icon, bg=row_bg, fg=color if bestiary else self.FG_DIM,
                     font=("TkDefaultFont", 9)).pack(side=tk.LEFT, padx=(6, 2), pady=4)

            # Nom cliquable
            name_lbl = tk.Label(row, text=name, bg=row_bg, fg=color,
                                 font=("Consolas", 9, "bold"), anchor="w",
                                 cursor="hand2")
            name_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=4)

            # PV actuels (si renseignés)
            if hp_cur is not None:
                m = get_monster(bestiary) if bestiary else None
                hp_max = m.get("hp", {}).get("average", "?") if m else "?"
                tk.Label(row, text=f"❤ {hp_cur}/{hp_max}", bg=row_bg,
                         fg="#81c784" if isinstance(hp_max, int) and hp_cur > hp_max * 0.5
                         else "#FF9800" if isinstance(hp_max, int) and hp_cur > hp_max * 0.25
                         else "#e57373",
                         font=("Consolas", 8)).pack(side=tk.RIGHT, padx=(0, 4))

            # Bouton supprimer
            tk.Button(row, text="✕", bg=row_bg, fg="#553333", font=("Arial", 7),
                      relief="flat", padx=2, cursor="hand2",
                      command=lambda idx=i: self._remove_npc(idx)).pack(side=tk.RIGHT, padx=2)

            # Click → ouvre la fiche
            for widget in (row, name_lbl):
                widget.bind("<Button-1>", lambda e, n=name, b=bestiary: self._open_sheet(n, b))

    def _open_sheet(self, npc_name: str, bestiary_name: str | None):
        """Ouvre (ou ramène) la fiche de monstre pour ce PNJ."""
        existing = self._open_sheets.get(npc_name)
        if existing:
            try:
                existing.win.deiconify()
                existing.win.lift()
                return
            except Exception:
                pass

        def _on_select(new_bestiary: str):
            """Callback quand le MJ sélectionne un monstre dans la fiche."""
            npcs = self._get_npcs()
            for npc in npcs:
                if npc.get("name") == npc_name:
                    npc["bestiary_name"] = new_bestiary
                    # Initialise les PV au max du monstre
                    m = get_monster(new_bestiary)
                    if m and npc.get("hp_current") is None:
                        npc["hp_current"] = m.get("hp", {}).get("average")
                    break
            self._save_npcs(npcs)
            self._refresh()
            self._msg_queue.put({
                "sender": "📋 PNJ",
                "text":   f"{npc_name} → fiche de monstre : {new_bestiary}",
                "color":  "#a5d6a7"
            })

        sheet = MonsterSheetWindow(
            self.root, npc_name, bestiary_name,
            on_select_callback=_on_select,
            win_state=self._win_state,
            track_fn=self._track
        )
        self._open_sheets[npc_name] = sheet

        def _on_close():
            self._open_sheets.pop(npc_name, None)
            try:
                sheet.win.destroy()
            except Exception:
                pass

        sheet.win.protocol("WM_DELETE_WINDOW", _on_close)

    def _add_npc(self):
        """Ouvre une mini-fenêtre pour ajouter un PNJ au groupe."""
        dialog = tk.Toplevel(self.root)
        dialog.title("＋ Ajouter un PNJ au groupe")
        dialog.geometry("400x310")
        dialog.configure(bg="#0d1117")
        dialog.resizable(False, True)
        dialog.grab_set()

        tk.Label(dialog, text="Nom du PNJ :", bg="#0d1117", fg="#a5d6a7",
                 font=("Arial", 10, "bold")).pack(anchor="w", padx=14, pady=(14, 2))
        name_var = tk.StringVar()
        tk.Entry(dialog, textvariable=name_var, bg="#161b22", fg="white",
                 font=("Consolas", 11), insertbackground="white",
                 relief="flat").pack(fill=tk.X, padx=14, ipady=5)

        tk.Label(dialog, text="Couleur (hex, ex: #a5d6a7) :", bg="#0d1117", fg="#888",
                 font=("Arial", 8)).pack(anchor="w", padx=14, pady=(8, 2))
        color_var = tk.StringVar(value="#a5d6a7")
        tk.Entry(dialog, textvariable=color_var, bg="#161b22", fg="white",
                 font=("Consolas", 10), insertbackground="white",
                 relief="flat", width=14).pack(anchor="w", padx=14, ipady=3)

        # ── Section Sorts ─────────────────────────────────────────────────────
        tk.Frame(dialog, bg="#2a3040", height=1).pack(fill=tk.X, padx=10, pady=(12, 4))

        spell_hdr = tk.Frame(dialog, bg="#0d1117")
        spell_hdr.pack(fill=tk.X, padx=14)
        tk.Label(spell_hdr, text="✨ Sorts du PNJ :", bg="#0d1117", fg="#9b8fc7",
                 font=("Arial", 9, "bold")).pack(side=tk.LEFT)

        _npc_spells: list = []   # sorts choisis pour ce PNJ

        def _open_spell_picker():
            try:
                from spell_data import SpellPickerDialog
            except ImportError:
                return
            def _on_pick(sp: dict):
                if not any(s["name"] == sp["name"] for s in _npc_spells):
                    _npc_spells.append(sp)
                _refresh_spell_lbl()
            SpellPickerDialog(dialog, _on_pick,
                              title="✨ Sorts — " + (name_var.get() or "PNJ"))

        def _clear_spells():
            _npc_spells.clear()
            _refresh_spell_lbl()

        def _refresh_spell_lbl():
            if _npc_spells:
                txt = "  ".join(
                    f"✨ {s['name']} (Niv {'TM' if s.get('level',1)==0 else s.get('level',1)})"
                    for s in _npc_spells
                )
                spells_lbl.config(text=txt, fg="#a855f7")
            else:
                spells_lbl.config(text="(aucun sort)", fg="#444466")

        btn_row = tk.Frame(dialog, bg="#0d1117")
        btn_row.pack(fill=tk.X, padx=14, pady=(2, 2))
        tk.Button(btn_row, text="＋ Ajouter sort",
                  bg="#1a103a", fg="#9b8fc7",
                  font=("Arial", 8, "bold"), relief="flat",
                  padx=8, pady=2,
                  command=_open_spell_picker).pack(side=tk.LEFT)
        tk.Button(btn_row, text="✕ Vider",
                  bg="#1a0808", fg="#885555",
                  font=("Arial", 7), relief="flat",
                  padx=4, pady=2,
                  command=_clear_spells).pack(side=tk.LEFT, padx=6)

        spells_lbl = tk.Label(dialog, text="(aucun sort)",
                              bg="#0d1117", fg="#444466",
                              font=("Consolas", 7, "italic"), anchor="w",
                              wraplength=370, justify=tk.LEFT)
        spells_lbl.pack(fill=tk.X, padx=14, pady=(2, 8))

        def _save():
            name = name_var.get().strip()
            if not name:
                return
            npcs = self._get_npcs()
            entry = {
                "name":  name,
                "color": color_var.get().strip() or "#a5d6a7",
                "bestiary_name": None,
                "hp_current": None,
                "notes": "",
            }
            if _npc_spells:
                entry["spells"] = list(_npc_spells)
            npcs.append(entry)
            self._save_npcs(npcs)
            self._refresh()
            dialog.destroy()

        tk.Button(dialog, text="✅ Ajouter", bg="#1a3a1a", fg="#81c784",
                  font=("Arial", 10, "bold"), relief="flat",
                  command=_save).pack(pady=10)

    def _remove_npc(self, idx: int):
        npcs = self._get_npcs()
        if 0 <= idx < len(npcs):
            npcs.pop(idx)
            self._save_npcs(npcs)
            self._refresh()