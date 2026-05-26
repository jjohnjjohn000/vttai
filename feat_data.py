import json
import os
import tkinter as tk
from functools import lru_cache

# We reuse class_data's formatting function
from class_data import _entries_to_text

_BASE_DIR = os.path.dirname(__file__)
_FEATS_FILE = os.path.join(_BASE_DIR, "data", "feats.json")

_FEAT_DATA: dict[str, dict] = {}
_FEAT_NAMES: list[str] = []

def load_feats():
    """Charge les dons depuis data/feats.json."""
    global _FEAT_DATA, _FEAT_NAMES
    if _FEAT_DATA:
        return
        
    if not os.path.exists(_FEATS_FILE):
        print(f"[FeatData] Fichier introuvable : {_FEATS_FILE}")
        return
        
    try:
        with open(_FEATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        count = 0
        for feat in data.get("feat", []):
            try:
                name = feat.get("name")
                if not name: continue
                
                # Formate les prérequis en texte optionnellement
                reqs = feat.get("prerequisite", [])
                req_text = ""
                if reqs:
                    rtxts = []
                    for r in reqs:
                        if isinstance(r, dict):
                            # Simple heuritique pour afficher les prérequis
                            for k, v in r.items():
                                if k == "level":
                                    rtxts.append(f"Niveau {v}")
                                elif k == "race":
                                    for rc in v:
                                        if isinstance(rc, dict): rtxts.append(rc.get("name", ""))
                                        else: rtxts.append(str(rc))
                                elif k == "ability":
                                    for ab in v:
                                        if isinstance(ab, dict):
                                            for abk, abv in ab.items():
                                                rtxts.append(f"{abk.upper()} {abv}+")
                                elif k == "proficiency":
                                    for p in v:
                                        if isinstance(p, dict):
                                            for pk, pv in p.items():
                                                rtxts.append(f"Maîtrise {pv}")
                                elif k == "spellcasting":
                                    rtxts.append("Capacité à lancer des sorts")
                                else:
                                    rtxts.append(str(v))
                    if rtxts:
                        req_text = ", ".join(rtxts)
                
                # Contenu
                desc = _entries_to_text(feat.get("entries", []))
                
                _FEAT_DATA[name.lower()] = {
                    "name": name,
                    "source": feat.get("source", "?"),
                    "prerequisite": req_text,
                    "description": desc,
                    "ability": feat.get("ability", [])
                }
                count += 1
            except Exception as e:
                print(f"[FeatData] Feat '{feat.get('name', 'Unknown')}' ignoré : {e}")
            
        _FEAT_NAMES = sorted(_FEAT_DATA.keys())
        print(f"[FeatData] Chargé {count} dons.")
        
    except Exception as e:
        import traceback
        print(f"[FeatData] Erreur globale au chargement : {e}")
        traceback.print_exc()

def get_feat(name: str) -> dict | None:
    """Retourne les infos formatées d'un don, ou None si non trouvé."""
    load_feats()
    if not name: return None
    return _FEAT_DATA.get(name.lower())

def darken_color(hex_color: str, factor: float = 0.2) -> str:
    """Assombrit une couleur hex (#RRGGBB)."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) != 6: return "#000000"
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    r = max(0, int(r * (1 - factor)))
    g = max(0, int(g * (1 - factor)))
    b = max(0, int(b * (1 - factor)))
    return f"#{r:02x}{g:02x}{b:02x}"

class FeatSheetWindow:
    """
    Fenêtre autonome (lecture seule) affichant la fiche détaillée d'un don.
    """
    BG      = "#0b0d12"
    PANEL   = "#090c15"
    BORDER  = "#2a3040"
    GOLD    = "#c8a820"
    FG      = "#dde0e8"
    
    def __init__(self, parent, feat_name: str):
        self.feat_dict = get_feat(feat_name.strip())
        
        self.win = tk.Toplevel(parent)
        self.win.geometry("500x400")
        self.win.configure(bg=self.BG)
        self.win.resizable(True, True)
        self.win.minsize(300, 200)
        
        if not self.feat_dict:
            self.win.title("Don inconnu")
            tk.Label(self.win, text=f"Le don '{feat_name}' n'a pas été trouvé.", fg="red", bg=self.BG).pack(pady=20)
            return

        self.win.title(f"📖 {self.feat_dict['name']}")

        # En-tête
        hdr = tk.Frame(self.win, bg=darken_color(self.GOLD, 0.4), pady=10)
        hdr.pack(fill=tk.X)
        
        tk.Label(
            hdr, text=f"📖 {self.feat_dict['name']}",
            bg=darken_color(self.GOLD, 0.4),
            fg=self.GOLD, 
            font=("Consolas", 14, "bold")
        ).pack(side=tk.LEFT, padx=15)
        
        src_text = f"Source : {self.feat_dict['source']}"
        tk.Label(
            hdr, text=src_text,
            bg=darken_color(self.GOLD, 0.4),
            fg="#cccccc",
            font=("Consolas", 9, "italic")
        ).pack(side=tk.RIGHT, padx=15)
        
        # Corps
        body = tk.Frame(self.win, bg=self.PANEL)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        if self.feat_dict["prerequisite"]:
            tk.Label(
                body, text=f"Prérequis : {self.feat_dict['prerequisite']}",
                bg=self.PANEL, fg="#e57373", font=("Consolas", 10, "italic")
            ).pack(anchor="w", pady=(0, 10))
            
        txt = tk.Text(
            body, bg=self.PANEL, fg=self.FG, 
            font=("Consolas", 10),
            wrap=tk.WORD, relief="flat",
            selectbackground="#2e3b5e",
            highlightthickness=0,
            padx=10, pady=10
        )
        
        scroll = tk.Scrollbar(body, command=txt.yview, bg=self.BORDER)
        txt.configure(yscrollcommand=scroll.set)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Insertion
        txt.insert(tk.END, self.feat_dict["description"])
        txt.config(state=tk.DISABLED)
