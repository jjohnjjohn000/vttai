import tkinter as tk
from tkinter import ttk
import background_data

def build_background_tab(creator, frame):
    """
    Construit l'onglet interactif pour l'historique dans l'assistant de création,
    incluant l'extraction des proficiencies et de la feature de base.
    """
    tk.Label(frame, text="Historique :", bg="#1e1e2e", fg="#aaaaaa").pack(anchor="w", pady=(10, 2))
    
    bgs = background_data.get_available_backgrounds()
    bg_cb = ttk.Combobox(frame, textvariable=creator.char_bg, values=bgs, state="readonly", font=("Arial", 11))
    bg_cb.pack(fill=tk.X, padx=5)
    
    # Valeur par défaut
    if bgs and not creator.char_bg.get():
        if "Acolyte" in bgs:
            creator.char_bg.set("Acolyte")
        else:
            bg_cb.current(0)
            
    txt_frame = tk.Frame(frame, bg="#1e1e2e")
    txt_frame.pack(fill=tk.BOTH, expand=True, pady=10, padx=5)
    
    creator.bg_info_txt = tk.Text(txt_frame, bg="#1a1a2e", fg="#a5d6a7", font=("Consolas", 9), wrap=tk.WORD, state=tk.DISABLED, relief="flat")
    scroll = ttk.Scrollbar(txt_frame, command=creator.bg_info_txt.yview)
    creator.bg_info_txt.configure(yscrollcommand=scroll.set)
    creator.bg_info_txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    creator.bg_info_txt.bind("<Leave>", creator._hide_tooltip)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    
    def _update_bg_info(e=None):
        creator.bg_choices_vars = getattr(creator, "bg_choices_vars", {})
        creator.bg_choices_vars.clear()
        bg_name = creator.char_bg.get()
        
        if not bg_name:
            return
            
        creator.bg_info_txt.config(state=tk.NORMAL)
        creator.bg_info_txt.delete("1.0", tk.END)
        
        creator.bg_info_txt.insert(tk.END, f"📜 Historique : {bg_name}\n\n", "title")
        
        # Maîtrises fixes
        fixed = background_data.get_background_fixed_proficiencies(bg_name)
        if fixed["skills"]:
            creator.bg_info_txt.insert(tk.END, f"▪ Compétences : {', '.join(fixed['skills'])}\n", "val")
        if fixed["tools"]:
            creator.bg_info_txt.insert(tk.END, f"▪ Outils : {', '.join(fixed['tools'])}\n", "val")
        if fixed["languages"]:
            creator.bg_info_txt.insert(tk.END, f"▪ Langues : {', '.join(fixed['languages'])}\n", "val")
            
        creator.bg_info_txt.insert(tk.END, "\n")
        
        # Choix interactifs
        choices = background_data.get_background_proficiency_choices(bg_name)
        if choices:
            choices_frame = tk.Frame(creator.bg_info_txt, bg="#1a1a2e")
            for choice in choices:
                choice_key = f"Historique ({choice['label']})"
                existing_val = getattr(creator, "existing_bg_choices", {}).get(choice_key, "")
                var = tk.StringVar(value=existing_val)
                creator.bg_choices_vars[choice_key] = var
                
                cf = tk.Frame(choices_frame, bg="#1a1a2e")
                cf.pack(fill=tk.X, pady=1)
                tk.Label(cf, text=f"↳ {choice['label']} :", bg="#1a1a2e", fg="#ffb74d", font=("Arial", 9, "italic")).pack(side=tk.LEFT, padx=(20, 5))
                
                entry = ttk.Combobox(cf, textvariable=var, values=choice['options'], font=("Consolas", 9), width=25)
                entry.pack(side=tk.LEFT, pady=2)
                
                var.trace_add("write", creator._live_save)
                entry.bind("<<ComboboxSelected>>", creator._live_save)
                
            creator.bg_info_txt.insert(tk.END, "▪ Choix Associés :\n", "key")
            creator.bg_info_txt.window_create(tk.END, window=choices_frame)
            creator.bg_info_txt.insert(tk.END, "\n\n", "val")
            
        # Caractéristiques & Capacités (Feature)
        features = background_data.get_background_features(bg_name)
        if features:
            creator.bg_info_txt.insert(tk.END, f"▪ Capacités d'Historique\n", "key")
            for feat in features:
                f_name = feat["name"].replace("Feature: ", "").strip()
                creator.bg_info_txt.insert(tk.END, f"\n  ♦ {f_name}\n", "feat_title")
                formatted = "\n".join([f"      {line}" for line in feat["text"].split("\n")]) + "\n"
                creator.bg_info_txt.insert(tk.END, formatted, "val")
                
        if not getattr(creator, "_is_building", True):
            creator._live_save()
            
        creator.bg_info_txt.tag_config("title", foreground="#81c784", font=("Arial", 11, "bold"))
        creator.bg_info_txt.tag_config("key", foreground="#ffb74d", font=("Arial", 9, "bold"))
        creator.bg_info_txt.tag_config("feat_title", foreground="#ce93d8", font=("Arial", 9, "bold", "underline"))
        creator.bg_info_txt.tag_config("val", foreground="#e0e0e0", font=("Arial", 9))
        
        creator.bg_info_txt.config(state=tk.DISABLED)
        
    bg_cb.bind("<<ComboboxSelected>>", _update_bg_info)
    _update_bg_info()