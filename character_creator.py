import tkinter as tk
from tkinter import ttk, messagebox
import math

from state_manager import load_state, save_state
import race_data
import class_data

class CharacterCreatorWindow:
    """Assistant de création de personnage étape par étape (Niveau 1)."""
    
    def _show_tooltip(self, event, text):
        self._hide_tooltip()
        self._tooltip_win = tk.Toplevel(self.top)
        self._tooltip_win.wm_overrideredirect(True)
        self._tooltip_win.attributes("-topmost", True)
        
        display_text = text[:300] + ("..." if len(text) > 300 else "")
        
        label = tk.Label(self._tooltip_win, text=display_text, justify=tk.LEFT, 
                         bg="#1a1a2e", fg="#e0e0e0", font=("Consolas", 9),
                         relief="solid", borderwidth=1, wraplength=400, padx=5, pady=5)
        label.pack()
        
        x = event.x_root + 15
        y = event.y_root + 15
        self._tooltip_win.wm_geometry(f"+{x}+{y}")
        
    def _move_tooltip(self, event):
        if hasattr(self, "_tooltip_win") and self._tooltip_win:
            x = event.x_root + 15
            y = event.y_root + 15
            self._tooltip_win.wm_geometry(f"+{x}+{y}")

    def _hide_tooltip(self, event=None):
        if hasattr(self, "_tooltip_win") and self._tooltip_win:
            self._tooltip_win.destroy()
            self._tooltip_win = None

    CLASSES_5E = [
        "Artificer", "Barbarian", "Bard", "Cleric", "Druid", "Fighter", 
        "Monk", "Paladin", "Ranger", "Rogue", "Sorcerer", "Warlock", "Wizard"
    ]

    def __init__(self, parent, on_complete=None, on_update=None, edit_char_name=None):
        self.top = tk.Toplevel(parent)
        self.edit_mode = edit_char_name is not None
        self.top.title(f"🎲 {'Modification de ' + edit_char_name if self.edit_mode else 'Création de Personnage'}")
        self.top.geometry("600x650")
        self.top.configure(bg="#1e1e2e")
        self.top.grab_set()  # Rend la fenêtre modale
        
        self.on_complete = on_complete
        self.on_update = on_update
        self._is_building = True
        
        state = load_state()
        
        if self.edit_mode:
            self.current_char_key = edit_char_name
            char_data = state.get("characters", {}).get(edit_char_name, {})
            self.existing_racial_choices = char_data.get("racial_choices", {})
            self.existing_class_choices = char_data.get("class_choices", {})
            self.existing_bg_choices = char_data.get("bg_choices", {})
        else:
            base_name = "Nouveau Personnage"
            name = base_name
            idx = 1
            while name in state.get("characters", {}):
                name = f"{base_name} {idx}"
                idx += 1
            self.current_char_key = name
            char_data = {
                "name": name, "level": 1, "hp": 10, "max_hp": 10, "ac": 10, "active": False, "in_party": False
            }
            state.setdefault("characters", {})[self.current_char_key] = char_data
            save_state(state)
            self.existing_racial_choices = {}
            self.existing_class_choices = {}
            self.existing_bg_choices = {}
            
        # Déterminer la valeur pour le dropdown de race
        race_val = char_data.get("race", "")
        dropdown_race = race_val
        if race_val:
            try:
                from race_data import get_available_races, split_race_source
                for r in get_available_races():
                    r_name, _ = split_race_source(r)
                    if r_name == race_val:
                        dropdown_race = r
                        break
            except Exception:
                pass
        
        # Variables de création
        self.char_name = tk.StringVar(value=char_data.get("name", self.current_char_key))
        self.char_level = tk.IntVar(value=char_data.get("level", 1))
        self.char_race = tk.StringVar(value=dropdown_race)
        self.char_class = tk.StringVar(value=char_data.get("class", "Fighter").title())
        self.char_bg = tk.StringVar(value=char_data.get("background", ""))
        self.char_alignment = tk.StringVar(value=char_data.get("alignment", "Neutre"))
        
        # Stats
        self.stats_vars = {
            "str": tk.IntVar(value=char_data.get("str", 10)),
            "dex": tk.IntVar(value=char_data.get("dex", 10)),
            "con": tk.IntVar(value=char_data.get("con", 10)),
            "int": tk.IntVar(value=char_data.get("int", 10)),
            "wis": tk.IntVar(value=char_data.get("wis", 10)),
            "cha": tk.IntVar(value=char_data.get("cha", 10))
        }
        
        self._applied_racial_bonuses = {"str": 0, "dex": 0, "con": 0, "int": 0, "wis": 0, "cha": 0}
        if dropdown_race:
            try:
                import race_data
                r_name, r_source = race_data.split_race_source(dropdown_race)
                blocks = race_data.get_race_ability_bonuses(r_name, source=r_source)
                for block in blocks:
                    for stat, val in block.items():
                        if stat in self._applied_racial_bonuses and isinstance(val, int):
                            self._applied_racial_bonuses[stat] += val
            except Exception:
                pass

        self._build_ui()
        
        # Attachement des écouteurs en temps réel
        self.char_name.trace_add("write", self._live_save)
        self.char_level.trace_add("write", self._live_save)
        self.char_race.trace_add("write", self._live_save)
        self.char_class.trace_add("write", self._live_save)
        self.char_bg.trace_add("write", self._live_save)
        self.char_alignment.trace_add("write", self._live_save)
        for v in self.stats_vars.values():
            v.trace_add("write", self._live_save)

        self._is_building = False
        self._live_save()

    def _build_ui(self):
        style = ttk.Style(self.top)
        style.theme_use("default")
        style.configure("TNotebook", background="#1e1e2e", borderwidth=0)
        style.configure("TNotebook.Tab", background="#252535", foreground="#cccccc", padding=[10, 5])
        style.map("TNotebook.Tab", background=[("selected", "#4CAF50")], foreground=[("selected", "#ffffff")])
        style.configure("TFrame", background="#1e1e2e")

        self.notebook = ttk.Notebook(self.top)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Étapes
        self._build_step1_identity()
        self._build_step2_class()
        self._build_step3_background()
        self._build_step4_abilities()
        self._build_step5_finalize()

    def _build_step1_identity(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="1. Identité & Race")
        
        tk.Label(frame, text="Nom du personnage :", bg="#1e1e2e", fg="#aaaaaa").pack(anchor="w", pady=(10, 2))
        tk.Entry(frame, textvariable=self.char_name, bg="#252535", fg="white", insertbackground="white", font=("Arial", 12)).pack(fill=tk.X, padx=5)

        tk.Label(frame, text="Race :", bg="#1e1e2e", fg="#aaaaaa").pack(anchor="w", pady=(15, 2))
        
        try:
            races = race_data.get_available_races()
        except Exception:
            races = ["Humain", "Elfe", "Nain", "Halfelin"]
            
        race_cb = ttk.Combobox(frame, textvariable=self.char_race, values=races, state="readonly", font=("Arial", 11))
        race_cb.pack(fill=tk.X, padx=5)
        if races and not self.char_race.get(): 
            race_cb.current(0)
        
        txt_frame = tk.Frame(frame, bg="#1e1e2e")
        txt_frame.pack(fill=tk.BOTH, expand=True, pady=10, padx=5)
        
        self.race_info_txt = tk.Text(txt_frame, bg="#1a1a2e", fg="#81c784", font=("Consolas", 9), wrap=tk.WORD, state=tk.DISABLED, relief="flat")
        scroll = ttk.Scrollbar(txt_frame, command=self.race_info_txt.yview)
        self.race_info_txt.configure(yscrollcommand=scroll.set)
        self.race_info_txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.race_info_txt.bind("<Leave>", self._hide_tooltip)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        def _update_race_info(e=None):
            self.racial_choices_vars = getattr(self, "racial_choices_vars", {})
            self.racial_choices_vars.clear()
            try:
                race_selection = self.char_race.get()
                race_name, source = race_data.split_race_source(race_selection)
                
                info = race_data.get_race_prompt_block(race_name, source=source)
                fluff = race_data.get_race_fluff(race_name, source=source)
                
                self.race_info_txt.config(state=tk.NORMAL)
                self.race_info_txt.delete("1.0", tk.END)
                
                def _recalc_ability_bonuses(*args):
                    new_bonuses = {"str": 0, "dex": 0, "con": 0, "int": 0, "wis": 0, "cha": 0}
                    blocks = race_data.get_race_ability_bonuses(race_name, source=source)
                    # 1. Modificateurs fixes
                    for block in blocks:
                        for stat, val in block.items():
                            if stat in new_bonuses and isinstance(val, int):
                                new_bonuses[stat] += val
                                
                    # 2. Choix dynamiques
                    rev_ability_names = {v: k for k, v in race_data._ABILITY_NAMES.items()}
                    for key, var in self.racial_choices_vars.items():
                        if key.startswith("Bonus de caractéristiques ("):
                            val = var.get().strip()
                            if val in rev_ability_names:
                                stat = rev_ability_names[val]
                                import re
                                m = re.search(r"\(\+?(-?\d+)\)", key)
                                if m:
                                    new_bonuses[stat] += int(m.group(1))

                    # 3. Application au UI
                    for stat in new_bonuses:
                        diff = new_bonuses[stat] - getattr(self, "_applied_racial_bonuses", {}).get(stat, 0)
                        if diff != 0:
                            try:
                                current = self.stats_vars[stat].get()
                                self.stats_vars[stat].set(current + diff)
                            except Exception:
                                pass
                            self._applied_racial_bonuses[stat] = new_bonuses[stat]

                    if hasattr(self, "racial_bonus_labels"):
                        for stat, lbl in self.racial_bonus_labels.items():
                            bonus = getattr(self, "_applied_racial_bonuses", {}).get(stat, 0)
                            if bonus > 0:
                                lbl.config(text=f"(inclus +{bonus})")
                            elif bonus < 0:
                                lbl.config(text=f"(inclus {bonus})")
                            else:
                                lbl.config(text="")
                                
                    self._live_save()

                # Exécuté une première fois pour les bonus fixes
                _recalc_ability_bonuses()

                # Construction des choix de caractéristiques
                ability_choices = []
                blocks = race_data.get_race_ability_bonuses(race_name, source=source)
                for block in blocks:
                    choose = block.get("choose")
                    if choose:
                        from_list = choose.get("from", [])
                        count = choose.get("count", 1)
                        amount = choose.get("amount", 1)
                        if from_list == "asi":
                            opts = ["Force", "Dextérité", "Constitution", "Intelligence", "Sagesse", "Charisme"]
                        elif isinstance(from_list, list):
                            opts = [race_data._ABILITY_NAMES.get(s, s.title()) for s in from_list]
                        else:
                            opts = ["Force", "Dextérité", "Constitution", "Intelligence", "Sagesse", "Charisme"]
                            
                        for i in range(count):
                            lbl = f"Caractéristique (+{amount})"
                            if count > 1:
                                lbl += f" {i+1}"
                            ability_choices.append({
                                "label": lbl,
                                "options": opts
                            })

                resist_choices = race_data.get_race_resist_choices(race_name, source=source)
                immune_choices = race_data.get_race_immune_choices(race_name, source=source)

                def _render_choices(category_name, choices_list):
                    choices_frame = tk.Frame(self.race_info_txt, bg="#1a1a2e")
                    for choice in choices_list:
                        choice_key = f"{category_name} ({choice['label']})"
                        existing_val = getattr(self, "existing_racial_choices", {}).get(choice_key, "")
                        var = tk.StringVar(value=existing_val)
                        self.racial_choices_vars[choice_key] = var
                        
                        cf = tk.Frame(choices_frame, bg="#1a1a2e")
                        cf.pack(fill=tk.X, pady=1)
                        tk.Label(cf, text=f"↳ {choice['label']} :", bg="#1a1a2e", fg="#ffb74d", font=("Arial", 9, "italic")).pack(side=tk.LEFT, padx=(20, 5))
                        
                        from tkinter import ttk
                        entry = ttk.Combobox(cf, textvariable=var, values=choice['options'], font=("Consolas", 9), width=25)
                        entry.pack(side=tk.LEFT, pady=2)
                        
                        if "Don" in choice['label'] or "Don" in choice_key:
                            def open_feat_win(v_=var):
                                val = v_.get().strip()
                                if val:
                                    import feat_data
                                    feat_data.FeatSheetWindow(self.top, val)
                            info_btn = tk.Button(cf, text="ℹ️", bg="#1a1a2e", fg="#ce93d8", font=("Arial", 9), borderwidth=0, activebackground="#252535", cursor="hand2", command=open_feat_win)
                            info_btn.pack(side=tk.LEFT, padx=5)

                        
                        if category_name == "Bonus de caractéristiques":
                            var.trace_add("write", _recalc_ability_bonuses)
                            entry.bind("<<ComboboxSelected>>", _recalc_ability_bonuses)
                        else:
                            var.trace_add("write", self._live_save)
                            entry.bind("<<ComboboxSelected>>", self._live_save)
                        
                    self.race_info_txt.insert(tk.END, "      ", "val")
                    idx = self.race_info_txt.index("end-1c")
                    self.race_info_txt.window_create(idx, window=choices_frame)
                    self.race_info_txt.insert(tk.END, "\n", "val")

                # Formatage embelli des caractéristiques techniques
                for line in info.split("\n"):
                    if not line.strip():
                        continue
                    if line.startswith("## Race :"):
                        title_text = line.replace("## Race :", "🧬").strip()
                        self.race_info_txt.insert(tk.END, title_text + "\n\n", "title")
                    elif line.startswith("Traits :"):
                        pass # Ignore la ligne texte brut, on va l'insérer interactivement plus bas
                    elif " : " in line:
                        k, v = line.split(" : ", 1)
                        self.race_info_txt.insert(tk.END, f"▪ {k.strip()} ", "key")
                        self.race_info_txt.insert(tk.END, f": {v.strip()}\n", "val")
                        
                        # Insertion des menus déroulants pour les choix
                        if k.strip() == "Bonus de caractéristiques" and ability_choices:
                            _render_choices(k.strip(), ability_choices)
                            _recalc_ability_bonuses()
                        elif k.strip() == "Résistances" and resist_choices:
                            _render_choices(k.strip(), resist_choices)
                        elif k.strip() == "Immunités" and immune_choices:
                            _render_choices(k.strip(), immune_choices)
                    else:
                        self.race_info_txt.insert(tk.END, line + "\n", "val")

                # Rendu interactif des traits raciaux
                traits = race_data.get_race_traits(race_name, source=source)
                if traits:
                    self.race_info_txt.insert(tk.END, f"▪ Traits ", "key")
                    self.race_info_txt.insert(tk.END, ": (cliquez pour afficher, survolez pour l'aperçu)\n", "hint")
                    for i, t in enumerate(traits):
                        t_name = t["name"]
                        t_text = t["text"]
                        
                        tag_name = f"trait_{i}"
                        tag_text = f"trait_text_{i}"
                        
                        self.race_info_txt.insert(tk.END, f"  ♦ {t_name}\n", tag_name)
                        
                        formatted_text = "\n".join([f"      {line}" for line in t_text.split("\n")]) + "\n"
                        self.race_info_txt.insert(tk.END, formatted_text, tag_text)
                        
                        self.race_info_txt.tag_config(tag_name, foreground="#ce93d8", font=("Arial", 9, "bold", "underline"))
                        self.race_info_txt.tag_config(tag_text, foreground="#cccccc", font=("Consolas", 9), elide=True)
                        
                        def on_enter(e, tn=tag_name, text=t_text):
                            self.race_info_txt.config(cursor="hand2")
                            self.race_info_txt.tag_config(tn, foreground="#ffb74d")
                            self._show_tooltip(e, text)
                            
                        def on_leave(e, tn=tag_name):
                            self.race_info_txt.config(cursor="")
                            self.race_info_txt.tag_config(tn, foreground="#ce93d8")
                            self._hide_tooltip()
                            
                        def on_click(e, tt=tag_text):
                            is_elided = self.race_info_txt.tag_cget(tt, "elide")
                            new_state = False if str(is_elided) in ("1", "True", "true") else True
                            self.race_info_txt.tag_config(tt, elide=new_state)
                            self._hide_tooltip()
                            
                        def on_motion(e, text=t_text):
                            self._move_tooltip(e)

                        self.race_info_txt.tag_bind(tag_name, "<Enter>", on_enter)
                        self.race_info_txt.tag_bind(tag_name, "<Leave>", on_leave)
                        self.race_info_txt.tag_bind(tag_name, "<Motion>", on_motion)
                        self.race_info_txt.tag_bind(tag_name, "<Button-1>", on_click)
                        
                        # Ajout dynamique d'un ou plusieurs choix si détectés
                        choices_extracted = race_data.extract_trait_choices(t_text)
                        if choices_extracted:
                            # Rend le texte du trait visible (déployé) automatiquement
                            self.race_info_txt.tag_config(tag_text, elide=False)
                            
                            choices_frame = tk.Frame(self.race_info_txt, bg="#1a1a2e")
                            
                            def build_choices(parent_frame, choices, parent_path=""):
                                for choice in choices:
                                    choice_key = f"{t_name} ({parent_path}{choice['label']})"
                                    existing_val = getattr(self, "existing_racial_choices", {}).get(choice_key, "")
                                    var = tk.StringVar(value=existing_val)
                                    self.racial_choices_vars[choice_key] = var
                                    
                                    cf = tk.Frame(parent_frame, bg="#1a1a2e")
                                    cf.pack(fill=tk.X, pady=1)
                                    indent = 20 + 20 * parent_path.count("->")
                                    tk.Label(cf, text=f"↳ {choice['label']} :", bg="#1a1a2e", fg="#ffb74d", font=("Arial", 9, "italic")).pack(side=tk.LEFT, padx=(indent, 5))
                                    
                                    from tkinter import ttk
                                    entry = ttk.Combobox(cf, textvariable=var, values=choice['options'], font=("Consolas", 9), width=25)
                                    entry.pack(side=tk.LEFT, pady=2)
                                    
                                    if "Don" in choice['label'] or "Don" in choice_key:
                                        def open_feat_win(v_=var):
                                            val = v_.get().strip()
                                            if val:
                                                import feat_data
                                                feat_data.FeatSheetWindow(self.top, val)
                                        info_btn = tk.Button(cf, text="ℹ️", bg="#1a1a2e", fg="#ce93d8", font=("Arial", 9), borderwidth=0, activebackground="#252535", cursor="hand2", command=open_feat_win)
                                        info_btn.pack(side=tk.LEFT, padx=5)

                                    
                                    sub_frame = tk.Frame(parent_frame, bg="#1a1a2e")
                                    sub_frame.pack(fill=tk.X)
                                    
                                    def on_change(*args, v=var, sf=sub_frame, ck=choice_key, cl=choice['label'], pp=parent_path):
                                        # Nettoyer les sous-choix visuellement
                                        for widget in sf.winfo_children():
                                            widget.destroy()
                                            
                                        # Nettoyer les variables enfants pour éviter de sauvegarder des choix obsolètes
                                        prefix = f"{t_name} ({pp}{cl} ->"
                                        keys_to_remove = [k for k in self.racial_choices_vars.keys() if k.startswith(prefix)]
                                        for k in keys_to_remove:
                                            self.racial_choices_vars.pop(k, None)
                                            if hasattr(self, "existing_racial_choices") and k in self.existing_racial_choices:
                                                self.existing_racial_choices.pop(k, None)
                                        
                                        val = v.get().strip()
                                        if val:
                                            sub_choices = race_data.extract_trait_choices(val)
                                            if sub_choices:
                                                build_choices(sf, sub_choices, parent_path=f"{pp}{cl} -> ")
                                                
                                        self._live_save()

                                    var.trace_add("write", on_change)
                                    entry.bind("<<ComboboxSelected>>", on_change)
                                    
                                    # Initialiser les sous-choix si une valeur existe déjà
                                    if existing_val:
                                        sub_choices = race_data.extract_trait_choices(existing_val)
                                        if sub_choices:
                                            build_choices(sub_frame, sub_choices, parent_path=f"{parent_path}{choice['label']} -> ")

                            build_choices(choices_frame, choices_extracted)

                            self.race_info_txt.insert(tk.END, "      ", tag_text)
                            idx = self.race_info_txt.index("end-1c")
                            self.race_info_txt.window_create(idx, window=choices_frame)
                            self.race_info_txt.tag_add(tag_text, idx)
                            self.race_info_txt.insert(tk.END, "\n", tag_text)

                # Provoquer une sauvegarde si de nouveaux champs viennent d'apparaître
                if not getattr(self, "_is_building", True):
                    self._live_save()

                if fluff:
                    self.race_info_txt.insert(tk.END, "\n" + "─" * 40 + "\n\n", "divider")
                    source_text = f" [{source}]" if source else ""
                    self.race_info_txt.insert(tk.END, f"📖 LORE / FLUFF{source_text}\n\n", "header")
                    self.race_info_txt.insert(tk.END, fluff + "\n", "fluff")
                    
                self.race_info_txt.tag_config("title", foreground="#ffb74d", font=("Arial", 11, "bold"))
                self.race_info_txt.tag_config("key", foreground="#64b5f6", font=("Arial", 9, "bold"))
                self.race_info_txt.tag_config("val", foreground="#e0e0e0", font=("Arial", 9))
                self.race_info_txt.tag_config("hint", foreground="#666666", font=("Arial", 8, "italic"))
                self.race_info_txt.tag_config("header", foreground="#ce93d8", font=("Arial", 10, "bold"))
                self.race_info_txt.tag_config("fluff", foreground="#aaaaaa", font=("Arial", 9, "italic"))
                self.race_info_txt.tag_config("divider", foreground="#444455")
                
                self.race_info_txt.config(state=tk.DISABLED)
            except Exception as e:
                import traceback
                print(f"[Race Info Error] {e}")
                traceback.print_exc()
                
                self.race_info_txt.config(state=tk.NORMAL)
                self.race_info_txt.delete("1.0", tk.END)
                self.race_info_txt.insert(tk.END, "Détails indisponibles.\n(Voir la console pour l'erreur technique).")
                self.race_info_txt.config(state=tk.DISABLED)
                
        race_cb.bind("<<ComboboxSelected>>", _update_race_info)
        _update_race_info()
        
        tk.Label(frame, text="Alignement :", bg="#1e1e2e", fg="#aaaaaa").pack(anchor="w", pady=(10, 2))
        alignments = ["Loyal Bon", "Neutre Bon", "Chaotique Bon", "Loyal Neutre", "Neutre", "Chaotique Neutre", "Loyal Mauvais", "Neutre Mauvais", "Chaotique Mauvais"]
        ttk.Combobox(frame, textvariable=self.char_alignment, values=alignments, state="readonly").pack(fill=tk.X, padx=5)

    def _build_step2_class(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="2. Classe")
        
        import class_tab
        class_tab.build_class_tab(self, frame)

    def _build_step3_background(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="3. Historique")
        
        import background_tab
        background_tab.build_background_tab(self, frame)

    def _build_step4_abilities(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="4. Caractéristiques")

        tk.Label(frame, text="Répartissez vos scores (ex: Array standard 15, 14, 13, 12, 10, 8)\nLes bonus raciaux fixes de votre race sont automatiquement appliqués.", 
                 bg="#1e1e2e", fg="#aaaaaa", justify=tk.CENTER).pack(pady=10)

        grid_fr = tk.Frame(frame, bg="#1e1e2e")
        grid_fr.pack(pady=10)

        labels = [("str", "Force (STR)"), ("dex", "Dextérité (DEX)"), ("con", "Constitution (CON)"),
                  ("int", "Intelligence (INT)"), ("wis", "Sagesse (WIS)"), ("cha", "Charisme (CHA)")]

        self.racial_bonus_labels = {}
        for i, (key, label) in enumerate(labels):
            tk.Label(grid_fr, text=label, bg="#1e1e2e", fg="#ffffff", font=("Arial", 10, "bold"), width=15, anchor="w").grid(row=i, column=0, pady=5, padx=5)
            spx = tk.Spinbox(grid_fr, from_=3, to=30, textvariable=self.stats_vars[key], width=5, bg="#252535", fg="#4CAF50", font=("Consolas", 11, "bold"), buttonbackground="#252535")
            spx.grid(row=i, column=1, pady=5)
            
            # Label pour le bonus racial
            rb_lbl = tk.Label(grid_fr, text="", bg="#1e1e2e", fg="#ce93d8", font=("Arial", 9, "italic"), width=10, anchor="w")
            rb_lbl.grid(row=i, column=2, pady=5, padx=5)
            bonus = getattr(self, "_applied_racial_bonuses", {}).get(key, 0)
            if bonus > 0:
                rb_lbl.config(text=f"(inclus +{bonus})")
            elif bonus < 0:
                rb_lbl.config(text=f"(inclus {bonus})")
            self.racial_bonus_labels[key] = rb_lbl
            
            # Affichage dynamique du modificateur
            mod_lbl = tk.Label(grid_fr, text="+0", bg="#1e1e2e", fg="#aaaaaa", font=("Consolas", 10), width=4)
            mod_lbl.grid(row=i, column=3, pady=5)
            
            def _update_mod(var=self.stats_vars[key], lbl=mod_lbl, *args):
                try:
                    val = var.get()
                    mod = math.floor((val - 10) / 2)
                    lbl.config(text=f"{mod:+d}")
                except Exception:
                    pass
            self.stats_vars[key].trace_add("write", _update_mod)
            _update_mod()

    def _build_step5_finalize(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="5. Finaliser")
        
        self.summary_lbl = tk.Label(frame, text="", bg="#1e1e2e", fg="#ffffff", justify=tk.LEFT, font=("Consolas", 10))
        self.summary_lbl.pack(fill=tk.BOTH, expand=True, pady=20, padx=10)
        
        tk.Label(frame, text="Votre personnage est sauvegardé en temps réel.\nVous pouvez fermer cette fenêtre à tout moment.", bg="#1e1e2e", fg="#888899", font=("Arial", 9, "italic")).pack(pady=5)
        
        btn_fr = tk.Frame(frame, bg="#1e1e2e")
        btn_fr.pack(fill=tk.X, pady=10, side=tk.BOTTOM)
                  
        btn_text = "✅ Sauvegarder les modifications" if getattr(self, "edit_mode", False) else "✅ Terminer la Création"
        tk.Button(btn_fr, text=btn_text, bg="#4CAF50", fg="black", font=("Arial", 10, "bold"), relief="flat", padx=10, pady=5,
                  command=self._finish_creation).pack(side=tk.RIGHT, padx=10)

    def _live_save(self, *args):
        if getattr(self, "_is_building", True):
            return
        
        self._perform_state_save()
        self._refresh_summary()
        
        # Debounce (anti-rebond) de l'UI pour ne pas lagger pendant qu'on tape
        if hasattr(self, "_update_ui_timer"):
            self.top.after_cancel(self._update_ui_timer)
        self._update_ui_timer = self.top.after(300, self._trigger_on_update)
        
    def _trigger_on_update(self):
        if self.on_update:
            self.on_update()
            
    def _perform_state_save(self):
        new_name = self.char_name.get().strip()
        if not new_name:
            new_name = "Nouveau Personnage"
            
        state = load_state()
        
        # Remplacement dynamique de la clé dans le dictionnaire D&D (Changement de Nom)
        if new_name != self.current_char_key:
            actual_new_name = new_name
            suffix = 1
            while actual_new_name in state.get("characters", {}) and actual_new_name != self.current_char_key:
                actual_new_name = f"{new_name} ({suffix})"
                suffix += 1
                
            char_data = state["characters"].pop(self.current_char_key, {})
            self.current_char_key = actual_new_name
            state["characters"][self.current_char_key] = char_data
            
        char_data = state.setdefault("characters", {}).setdefault(self.current_char_key, {})
        char_data["name"] = self.current_char_key
        
        race_selection = self.char_race.get()
        race_name, _ = race_data.split_race_source(race_selection)
        char_data["race"] = race_name
        
        cls = self.char_class.get().lower()
        char_data["class"] = cls
        char_data["alignment"] = self.char_alignment.get()
        char_data["background"] = self.char_bg.get()
        
        try: level = self.char_level.get()
        except tk.TclError: level = 1
        char_data["level"] = level
        
        if hasattr(self, "char_subclass"):
            char_data["subclass"] = self.char_subclass.get()
        
        try: con_val = self.stats_vars["con"].get()
        except tk.TclError: con_val = 10
        try: dex_val = self.stats_vars["dex"].get()
        except tk.TclError: dex_val = 10
        
        con_mod = math.floor((con_val - 10) / 2)
        dex_mod = math.floor((dex_val - 10) / 2)
        
        try: hd = class_data.get_hit_die(cls)
        except Exception: hd = 8
        
        # Formule D&D 5e: Dé de vie max au niveau 1 + moyenne arrondie au supérieur ensuite
        base_hp = hd + con_mod
        extra_hp_per_lvl = int((hd / 2) + 1) + con_mod
        new_max_hp = max(1, base_hp + (extra_hp_per_lvl * (level - 1)))
            
        old_max_hp = char_data.get("max_hp", new_max_hp)
        hp_diff = new_max_hp - old_max_hp
        
        if not self.edit_mode or "hp" not in char_data:
            char_data["hp"] = new_max_hp
        else:
            # Garde la santé actuelle intacte, mais répercute les augmentations (ou diminutions) de PV max
            char_data["hp"] = min(new_max_hp, max(0, char_data["hp"] + hp_diff))
            
        char_data["max_hp"] = new_max_hp
        char_data["ac"] = 10 + dex_mod
        char_data["hit_die"] = hd
        
        for k in ["str", "dex", "con", "int", "wis", "cha"]:
            try: score = self.stats_vars[k].get()
            except tk.TclError: score = 10
            char_data[k] = score
            char_data[f"{k}_mod"] = math.floor((score - 10) / 2)

        racial_choices = {}
        existing_spells = char_data.get("spells_prepared", [])
        
        for t_name, var in getattr(self, "racial_choices_vars", {}).items():
            choice_val = var.get().strip()
            if choice_val:
                racial_choices[t_name] = choice_val
                # Si le choix est un sort ou sort mineur, on l'ajoute automatiquement au grimoire 
                # (sans écraser ceux que le joueur a pu ajouter manuellement)
                if "cantrip" in t_name.lower() or "spell" in t_name.lower() or "sort" in t_name.lower():
                    if choice_val not in existing_spells:
                        existing_spells.append(choice_val)

        class_choices = {}
        for t_name, var in getattr(self, "class_choices_vars", {}).items():
            choice_val = var.get().strip()
            if choice_val:
                class_choices[t_name] = choice_val
                if "cantrip" in t_name.lower() or "spell" in t_name.lower() or "sort" in t_name.lower():
                    if choice_val not in existing_spells:
                        existing_spells.append(choice_val)

        bg_choices = {}
        for t_name, var in getattr(self, "bg_choices_vars", {}).items():
            choice_val = var.get().strip()
            if choice_val:
                bg_choices[t_name] = choice_val

        char_data["racial_choices"] = racial_choices
        char_data["class_choices"] = class_choices
        char_data["bg_choices"] = bg_choices
        char_data["spells_prepared"] = existing_spells
        
        # Initialisations de base
        char_data.setdefault("subrace", "")
        char_data.setdefault("subclass", "")
        char_data.setdefault("hit_dice_used", 0)
        char_data.setdefault("unconscious", False)
        char_data.setdefault("unconscious_state", "stable")
        char_data.setdefault("active", False)
        char_data.setdefault("in_party", False)
        
        state["characters"][self.current_char_key] = char_data
        save_state(state)

    def _refresh_summary(self):
        name = self.char_name.get() or "Inconnu"
        race_selection = self.char_race.get()
        race_name, _ = race_data.split_race_source(race_selection)
        cls = self.char_class.get()
        bg = self.char_bg.get()
        
        try: con_val = self.stats_vars["con"].get()
        except tk.TclError: con_val = 10
        try: dex_val = self.stats_vars["dex"].get()
        except tk.TclError: dex_val = 10
        
        con_mod = math.floor((con_val - 10) / 2)
        dex_mod = math.floor((dex_val - 10) / 2)
        
        try:
            hd = class_data.get_hit_die(cls.lower())
        except Exception:
            hd = 8
            
        try: level = self.char_level.get()
        except tk.TclError: level = 1
            
        base_hp = hd + con_mod
        extra_hp_per_lvl = int((hd / 2) + 1) + con_mod
        max_hp = max(1, base_hp + (extra_hp_per_lvl * (level - 1)))
        
        summary = (
            f"Nom : {name}\n"
            f"Race : {race_name}\n"
            f"Classe : {cls} (Niveau {level})\n"
            f"Historique : {bg}\n\n"
            f"PV Max : {max_hp} (Niv.1: {base_hp} | Niv.sup: +{extra_hp_per_lvl}/niv)\n"
            f"Classe d'Armure de base : {10 + dex_mod} (sans armure)\n\n"
        )
        
        stats_str = []
        for k in ["str", "dex", "con", "int", "wis", "cha"]:
            try: v = self.stats_vars[k].get()
            except tk.TclError: v = 10
            stats_str.append(f"{k.upper()}: {v}")
            
        summary += " | ".join(stats_str[:3]) + "\n" + " | ".join(stats_str[3:])
        
        choices = []
        for t_name, var in getattr(self, "racial_choices_vars", {}).items():
            if var.get().strip():
                choices.append(f"{t_name} : {var.get().strip()}")
        
        if choices:
            summary += "\n\nChoix Raciaux :\n" + "\n".join([f"- {c}" for c in choices])
            
        c_choices = []
        for t_name, var in getattr(self, "class_choices_vars", {}).items():
            if var.get().strip():
                c_choices.append(f"{t_name} : {var.get().strip()}")
        
        if c_choices:
            summary += "\n\nChoix de Classe :\n" + "\n".join([f"- {c}" for c in c_choices])
            
        b_choices = []
        for t_name, var in getattr(self, "bg_choices_vars", {}).items():
            if var.get().strip():
                b_choices.append(f"{t_name} : {var.get().strip()}")
        
        if b_choices:
            summary += "\n\nChoix d'Historique :\n" + "\n".join([f"- {c}" for c in b_choices])
            
        self.summary_lbl.config(text=summary)

    def _finish_creation(self):
        # Ultime vérification
        self._live_save()
        if self.on_complete:
            self.on_complete(self.current_char_key)
        self.top.destroy()