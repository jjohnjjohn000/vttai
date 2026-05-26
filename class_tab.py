import tkinter as tk
from tkinter import ttk
import class_data
import race_data

def build_class_tab(creator, frame):
    """
    Construit l'onglet interactif pour la classe dans l'assistant de création,
    incluant les choix de maîtrises et les capacités dynamiques (Niveau 1).
    """
    tk.Label(frame, text="Classe :", bg="#1e1e2e", fg="#aaaaaa").pack(anchor="w", pady=(10, 2))
    class_cb = ttk.Combobox(frame, textvariable=creator.char_class, values=creator.CLASSES_5E, state="readonly", font=("Arial", 11))
    class_cb.pack(fill=tk.X, padx=5)
    if not creator.char_class.get() or creator.char_class.get() not in creator.CLASSES_5E:
        class_cb.current(5) # Fighter par défaut
        
    tk.Label(frame, text="Niveau :", bg="#1e1e2e", fg="#aaaaaa").pack(anchor="w", pady=(5, 2))
    level_cb = ttk.Combobox(frame, textvariable=creator.char_level, values=list(range(1, 21)), state="readonly", font=("Arial", 11))
    level_cb.pack(fill=tk.X, padx=5)
        
    subclass_frame = tk.Frame(frame, bg="#1e1e2e")
    tk.Label(subclass_frame, text="Archétype :", bg="#1e1e2e", fg="#aaaaaa").pack(side=tk.LEFT, padx=5)
    creator.char_subclass = tk.StringVar(value=getattr(creator, "existing_class_choices", {}).get("subclass", ""))
    subclass_cb = ttk.Combobox(subclass_frame, textvariable=creator.char_subclass, state="readonly", font=("Arial", 11))
    subclass_cb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
    
    txt_frame = tk.Frame(frame, bg="#1e1e2e")
    txt_frame.pack(fill=tk.BOTH, expand=True, pady=10, padx=5)
    
    creator.class_info_txt = tk.Text(txt_frame, bg="#1a1a2e", fg="#64b5f6", font=("Consolas", 9), wrap=tk.WORD, state=tk.DISABLED, relief="flat")
    scroll = ttk.Scrollbar(txt_frame, command=creator.class_info_txt.yview)
    creator.class_info_txt.configure(yscrollcommand=scroll.set)
    creator.class_info_txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    creator.class_info_txt.bind("<Leave>", creator._hide_tooltip)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    
    def _update_class_info(e=None):
        creator.class_choices_vars = getattr(creator, "class_choices_vars", {})
        creator.class_choices_vars.clear()
        cls = creator.char_class.get().lower()
        
        try: level = creator.char_level.get()
        except tk.TclError: level = 1
        
        req_level = class_data.get_subclass_level(cls)
        if level >= req_level:
            subclass_frame.pack(fill=tk.X, pady=5, after=level_cb)
            subs = class_data.get_available_subclasses(cls)
            subclass_cb.config(values=subs)
            if creator.char_subclass.get() not in subs and subs:
                subclass_cb.current(0)
        else:
            subclass_frame.pack_forget()
            creator.char_subclass.set("")

        try:
            hd = class_data.get_hit_die(cls)
            profs = class_data.get_proficiencies(cls)
            armor = ", ".join([a.title() for a in profs.get("armor", [])]) or "Aucune"
            weapons = ", ".join([w.title() for w in profs.get("weapons", [])]) or "Aucune"
            saves = ", ".join([s.upper() for s in profs.get("saves", [])])
            
            creator.class_info_txt.config(state=tk.NORMAL)
            creator.class_info_txt.delete("1.0", tk.END)
            
            creator.class_info_txt.insert(tk.END, f"⚔️ Classe : {cls.title()}\n\n", "title")
            creator.class_info_txt.insert(tk.END, f"▪ Dé de vie ", "key")
            creator.class_info_txt.insert(tk.END, f": 1d{hd}\n", "val")
            creator.class_info_txt.insert(tk.END, f"▪ Maîtrises d'armure ", "key")
            creator.class_info_txt.insert(tk.END, f": {armor}\n", "val")
            creator.class_info_txt.insert(tk.END, f"▪ Maîtrises d'arme ", "key")
            creator.class_info_txt.insert(tk.END, f": {weapons}\n", "val")
            creator.class_info_txt.insert(tk.END, f"▪ Jets de sauvegarde ", "key")
            creator.class_info_txt.insert(tk.END, f": {saves}\n\n", "val")
            
            prof_choices = class_data.get_class_proficiency_choices(cls)
            if prof_choices:
                choices_frame = tk.Frame(creator.class_info_txt, bg="#1a1a2e")
                for choice in prof_choices:
                    choice_key = f"Maîtrise ({choice['label']})"
                    existing_val = getattr(creator, "existing_class_choices", {}).get(choice_key, "")
                    var = tk.StringVar(value=existing_val)
                    creator.class_choices_vars[choice_key] = var
                    
                    cf = tk.Frame(choices_frame, bg="#1a1a2e")
                    cf.pack(fill=tk.X, pady=1)
                    tk.Label(cf, text=f"↳ {choice['label']} :", bg="#1a1a2e", fg="#ffb74d", font=("Arial", 9, "italic")).pack(side=tk.LEFT, padx=(20, 5))
                    entry = ttk.Combobox(cf, textvariable=var, values=choice['options'], font=("Consolas", 9), width=25)
                    entry.pack(side=tk.LEFT, pady=2)
                    
                    var.trace_add("write", creator._live_save)
                    entry.bind("<<ComboboxSelected>>", creator._live_save)
                    
                creator.class_info_txt.insert(tk.END, "▪ Choix de Maîtrises :\n", "key")
                creator.class_info_txt.window_create(tk.END, window=choices_frame)
                creator.class_info_txt.insert(tk.END, "\n\n", "val")

            subclass_short = creator.char_subclass.get() if level >= req_level else ""
            features = class_data.get_all_feature_details(cls, subclass_short, level=level)
            
            if features:
                creator.class_info_txt.insert(tk.END, f"▪ Capacités (Niveau {level}) ", "key")
                creator.class_info_txt.insert(tk.END, ": (cliquez pour afficher, survolez pour l'aperçu)\n", "hint")
                
                for i, feat in enumerate(features):
                    f_name = feat["name"]
                    f_text = feat["text"]
                    
                    tag_name = f"cfeat_{i}"
                    tag_text = f"cfeat_text_{i}"
                    
                    prefix = "♦" if feat["type"] == "class" else "◈"
                    creator.class_info_txt.insert(tk.END, f"  {prefix} {f_name}\n", tag_name)
                    
                    formatted_text = "\n".join([f"      {line}" for line in f_text.split("\n")]) + "\n"
                    creator.class_info_txt.insert(tk.END, formatted_text, tag_text)
                    
                    creator.class_info_txt.tag_config(tag_name, foreground="#ce93d8" if feat["type"] == "class" else "#b39ddb", font=("Arial", 9, "bold", "underline"))
                    creator.class_info_txt.tag_config(tag_text, foreground="#cccccc", font=("Consolas", 9), elide=True)
                    
                    def on_enter(e, tn=tag_name, text=f_text):
                        creator.class_info_txt.config(cursor="hand2")
                        creator.class_info_txt.tag_config(tn, foreground="#ffb74d")
                        creator._show_tooltip(e, text)
                        
                    def on_leave(e, tn=tag_name, is_sub=(feat["type"] == "subclass")):
                        creator.class_info_txt.config(cursor="")
                        creator.class_info_txt.tag_config(tn, foreground="#b39ddb" if is_sub else "#ce93d8")
                        creator._hide_tooltip()
                        
                    def on_click(e, tt=tag_text):
                        is_elided = creator.class_info_txt.tag_cget(tt, "elide")
                        new_state = False if str(is_elided) in ("1", "True", "true") else True
                        creator.class_info_txt.tag_config(tt, elide=new_state)
                        creator._hide_tooltip()
                        
                    def on_motion(e, text=f_text):
                        creator._move_tooltip(e)

                    creator.class_info_txt.tag_bind(tag_name, "<Enter>", on_enter)
                    creator.class_info_txt.tag_bind(tag_name, "<Leave>", on_leave)
                    creator.class_info_txt.tag_bind(tag_name, "<Motion>", on_motion)
                    creator.class_info_txt.tag_bind(tag_name, "<Button-1>", on_click)
                    
                    # Exploite l'extracteur générique de race_data pour les choix (style de combat, sorts de pacte...)
                    choices_extracted = race_data.extract_trait_choices(f_text)
                    if choices_extracted:
                        creator.class_info_txt.tag_config(tag_text, elide=False)
                        choices_frame = tk.Frame(creator.class_info_txt, bg="#1a1a2e")
                        
                        def build_choices(parent_frame, choices, parent_path=""):
                            for choice in choices:
                                choice_key = f"{f_name} ({parent_path}{choice['label']})"
                                existing_val = getattr(creator, "existing_class_choices", {}).get(choice_key, "")
                                var = tk.StringVar(value=existing_val)
                                creator.class_choices_vars[choice_key] = var
                                
                                cf = tk.Frame(parent_frame, bg="#1a1a2e")
                                cf.pack(fill=tk.X, pady=1)
                                indent = 20 + 20 * parent_path.count("->")
                                tk.Label(cf, text=f"↳ {choice['label']} :", bg="#1a1a2e", fg="#ffb74d", font=("Arial", 9, "italic")).pack(side=tk.LEFT, padx=(indent, 5))
                                
                                entry = ttk.Combobox(cf, textvariable=var, values=choice['options'], font=("Consolas", 9), width=25)
                                entry.pack(side=tk.LEFT, pady=2)
                                
                                sub_frame = tk.Frame(parent_frame, bg="#1a1a2e")
                                sub_frame.pack(fill=tk.X)
                                
                                def on_change(*args, v=var, sf=sub_frame, ck=choice_key, cl=choice['label'], pp=parent_path):
                                    for widget in sf.winfo_children(): widget.destroy()
                                    prefix = f"{f_name} ({pp}{cl} ->"
                                    keys_to_remove = [k for k in creator.class_choices_vars.keys() if k.startswith(prefix)]
                                    for k in keys_to_remove:
                                        creator.class_choices_vars.pop(k, None)
                                        if hasattr(creator, "existing_class_choices") and k in creator.existing_class_choices:
                                            creator.existing_class_choices.pop(k, None)
                                    
                                    val = v.get().strip()
                                    if val:
                                        sub_choices = race_data.extract_trait_choices(val)
                                        if sub_choices:
                                            build_choices(sf, sub_choices, parent_path=f"{pp}{cl} -> ")
                                    creator._live_save()

                                var.trace_add("write", on_change)
                                entry.bind("<<ComboboxSelected>>", on_change)
                                
                                if existing_val:
                                    sub_choices = race_data.extract_trait_choices(existing_val)
                                    if sub_choices:
                                        build_choices(sub_frame, sub_choices, parent_path=f"{parent_path}{choice['label']} -> ")

                        build_choices(choices_frame, choices_extracted)

                        creator.class_info_txt.insert(tk.END, "      ", tag_text)
                        creator.class_info_txt.window_create(tk.END, window=choices_frame)
                        creator.class_info_txt.insert(tk.END, "\n", tag_text)

            if not getattr(creator, "_is_building", True):
                creator._live_save()
            
            creator.class_info_txt.tag_config("title", foreground="#64b5f6", font=("Arial", 11, "bold"))
            creator.class_info_txt.tag_config("key", foreground="#81c784", font=("Arial", 9, "bold"))
            creator.class_info_txt.tag_config("val", foreground="#e0e0e0", font=("Arial", 9))
            creator.class_info_txt.tag_config("hint", foreground="#666666", font=("Arial", 8, "italic"))
            
            creator.class_info_txt.config(state=tk.DISABLED)
        except Exception as e:
            import traceback
            print(f"[Class Info Error] {e}")
            traceback.print_exc()
            
            creator.class_info_txt.config(state=tk.NORMAL)
            creator.class_info_txt.delete("1.0", tk.END)
            creator.class_info_txt.insert(tk.END, "Détails indisponibles.\n(Voir la console pour l'erreur technique).")
            creator.class_info_txt.config(state=tk.DISABLED)
            
    class_cb.bind("<<ComboboxSelected>>", _update_class_info)
    level_cb.bind("<<ComboboxSelected>>", _update_class_info)
    subclass_cb.bind("<<ComboboxSelected>>", _update_class_info)
    
    _update_class_info()