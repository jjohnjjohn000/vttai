import tkinter as tk
from PIL import Image, ImageTk
import random
import os
import json
import re

class TarokkaWindow:
    def __init__(self, parent, msg_queue, initial_state=None, save_callback=None):
        self.top = tk.Toplevel(parent)
        self.top.title("🃏 Tirage des Destinées Planaire")
        self.top.geometry("1024x820")
        self.top.configure(bg="#1e1e1e")
        
        self.msg_queue = msg_queue
        self.save_callback = save_callback
        self.tarokka_data = self.load_tarokka_data()
        self.initial_state = initial_state

        # Configuration des decks
        self.base_common_deck =[
            "1 - coins.jpg", "2 - coins.jpg", "3 - coins.jpg", "4 - coins.jpg", "5 - coins.jpg", "6 - coins.jpg", "7 - coins.jpg", "8 - coins.jpg", "9 - coins.jpg", "Rogue.jpg",
            "1 - swords.jpg", "2 - swords.jpg", "3 - swords.jpg", "4 - swords.jpg", "5 - swords.jpg", "6 - swords.jpg", "7 - swords.jpg", "8 - swords.jpg", "9 - swords.jpg", "Warrior.jpg",
            "1 - stars.jpg", "2 - stars.jpg", "3 - stars.jpg", "4 - stars.jpg", "5 - stars.jpg", "6 - stars.jpg", "7 - stars.jpg", "8 - stars.jpg", "9 - stars.jpg", "Wizard.jpg",
            "1 - glyphs.jpg", "2 - glyphs.jpg", "3 - glyphs.jpg", "4 - glyphs.jpg", "5 - glyphs.jpg", "6 - glyphs.jpg", "7 - gylphs.jpg", "8 - glyphs.jpg", "9 - glyphs.jpg", "Priest.jpg"
        ]
        self.base_high_deck =[
            "Artifact.jpg", "Beast.jpg", "Broken One.jpg", "Dark Lord.jpg", "Donjon.jpg",
            "Executioner.jpg", "Ghost.jpg", "Horseman.jpg", "Innocent.jpg", "Marionette.jpg",
            "Mists.jpg", "Raven.jpg", "Seer.jpg", "Tempter.jpg"
        ]

        self.common_deck = self.base_common_deck.copy()
        self.high_deck = self.base_high_deck.copy()
        random.shuffle(self.common_deck)
        random.shuffle(self.high_deck)

        self.step = 0
        self.drawn_cards_refs =[]
        self.drawn_cards_files = []
        self.current_card_descriptions =[]

        # Coordonnées (x, y) de la croix
        self.positions =[
            (300, 384), # 1: Source (Gauche)
            (512, 168), # 2: Ancre (Haut)
            (724, 384), # 3: Larme (Droite)
            (512, 600), # 4: Allié / Gardien (Bas)
            (512, 384), # 5: Cœur de l'Ennemi (Centre)
        ]

        self.meanings =[
            "1. La Source de la Blessure",
            "2. L'Ancre de Lumière",
            "3. La Larme d'Achéron",
            "4. Le Gardien Improbable",
            "5. Le Cœur de l'Invasion"
        ]

        self.step_intros =[
            "« La première carte révèle la nature de la blessure... la source de l'instabilité qui ronge ce monde. Elle vous montrera où la réalité s'est rompue. »",
            "« La deuxième carte est votre bouclier. Elle révèle l'ancre qui vous protégera de la corruption du temple et stabilisera le rituel. »",
            "« La troisième carte est votre instrument. C'est la larme qui suturera la plaie, le catalyseur qui refermera la faille. »",
            "« La quatrième carte, issue du jeu supérieur, révèle votre allié inattendu. Le destin vous enverra un gardien pour vous aider dans l'heure la plus sombre. »",
            "« Et la dernière carte... elle révèle le cœur de votre ennemi. Le lieu où l'invasion a pris racine, où vous devrez affronter le mal à sa source. »"
        ]

        # Structure de l'interface
        self.canvas = tk.Canvas(self.top, width=1024, height=768, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.control_frame = tk.Frame(self.top, bg="#1e1e1e", height=50)
        self.control_frame.pack(side=tk.BOTTOM, fill=tk.X)

        self.btn_draw = tk.Button(self.control_frame, text="🃏 Tirer la carte suivante",
                                  bg="#4a1e3a", fg="white", font=("Arial", 12, "bold"),
                                  command=self.draw_next_card, relief="flat", padx=10, pady=5)
        self.btn_draw.pack(side=tk.LEFT, padx=20, pady=10)

        self.btn_reset = tk.Button(self.control_frame, text="🔄 Réinitialiser",
                                   bg="#7d3d3d", fg="white", font=("Arial", 12, "bold"),
                                   command=self.reset_tarokka, relief="flat", padx=10, pady=5)
        self.btn_reset.pack(side=tk.LEFT, padx=10, pady=10)

        self.lbl_info = tk.Label(self.control_frame, text="Prêt pour le tirage des Destinées.",
                                 bg="#1e1e1e", fg="#e0e0e0", font=("Consolas", 12, "italic"))
        self.lbl_info.pack(side=tk.LEFT, padx=20, pady=10)

        # Chargement de l'image de fond
        try:
            bg_image = Image.open("images/tarokka_bg.jpg")
            bg_image = bg_image.resize((1024, 768), Image.Resampling.LANCZOS)
            self.bg_photo = ImageTk.PhotoImage(bg_image)
            self.canvas.create_image(0, 0, image=self.bg_photo, anchor="nw", tags="bg")
        except Exception as e:
            print(f"[Tarokka] Image de fond non trouvée : {e}")

        self.top.protocol("WM_DELETE_WINDOW", self.on_closing)
        self._restore_state()

    def on_closing(self):
        self._notify_save()
        self.top.destroy()

    def _restore_state(self):
        """Restaure les cartes de manière sécurisée sans crasher la fenêtre."""
        print(f"[Tarokka] Tentative de restauration avec l'état : {self.initial_state}")
        
        if not self.initial_state or "drawn_cards" not in self.initial_state:
            print("[Tarokka] Aucun tirage précédent trouvé. Plateau vide.")
            return
            
        try:
            # On se limite strictement à 5 cartes pour éviter les crashs d'index
            cards_to_restore = self.initial_state["drawn_cards"][:5]
            print(f"[Tarokka] Début du placement des {len(cards_to_restore)} cartes sauvegardées...")
            
            for card_file in cards_to_restore:
                self._render_card(self.step, card_file)
                self.drawn_cards_files.append(card_file)
                if card_file in self.common_deck:
                    self.common_deck.remove(card_file)
                if card_file in self.high_deck:
                    self.high_deck.remove(card_file)
                self.step += 1
            
            if self.step >= 5:
                self.btn_draw.config(state=tk.DISABLED, text="Tirage Terminé", bg="#333333", fg="#888888")
            print("[Tarokka] Restauration réussie !")
                
        except Exception as e:
            print(f"[Tarokka] Erreur critique lors de la restauration: {e}")

    def load_tarokka_data(self):
        data_map = {}
        try:
            file_path = None
            for path in["adventure-barovie.json", "adventure/adventure-barovie.json", "book/adventure-barovie.json"]:
                if os.path.exists(path):
                    file_path = path
                    break
            
            if not file_path:
                print("[Tarokka] Fichier adventure-barovie.json non trouvé.")
                return data_map

            with open(file_path, "r", encoding="utf-8") as f:
                content = json.load(f)

            def clean_text(text):
                if not isinstance(text, str): return ""
                return re.sub(r'\{@[a-zA-Z0-9_-]+\s+([^}]+)\}', r'\1', text)

            def extract_player_text(raw_string):
                cleaned = clean_text(raw_string)
                match = re.search(r'«\s*(.*?)\s*»', cleaned)
                if match:
                    return f"« {match.group(1)} »"
                return ""

            def parse_mj_blocks(blocks):
                out = ""
                for b in blocks:
                    if isinstance(b, str):
                        out += clean_text(b) + "\n\n"
                    elif isinstance(b, dict):
                        if b.get("type") == "insetReadaloud":
                            out += "« "
                        if "name" in b:
                            out += f"\n[{clean_text(b['name'])}]\n"
                        if "items" in b:
                            for item in b["items"]:
                                if isinstance(item, str):
                                    out += f"• {clean_text(item)}\n"
                                elif isinstance(item, dict) and "name" in item:
                                    out += f"• {clean_text(item['name'])}\n"
                            out += "\n"
                        if "entries" in b:
                            out += parse_mj_blocks(b["entries"])
                        if b.get("type") == "insetReadaloud":
                            out = out.strip() + " »\n\n"
                return out

            def extract_entries(entries, current_context=""):
                for item in entries:
                    if isinstance(item, dict):
                        curr_name = item.get("name", "")
                        if curr_name and curr_name.startswith(("1.", "2.", "3.", "4.", "5.")):
                            current_context = curr_name
                        
                        if curr_name and ("—" in curr_name or " (Joker " in curr_name or " (Roi de " in curr_name or " (Valet de " in curr_name or " (Reine de " in curr_name):
                            player_text = ""
                            mj_text = ""
                            for sub in item.get("entries",[]):
                                if isinstance(sub, str):
                                    extracted = extract_player_text(sub)
                                    if extracted:
                                        player_text += extracted + "\n\n"
                                    mj_text += clean_text(sub) + "\n\n"
                                elif isinstance(sub, dict):
                                    mj_text += parse_mj_blocks([sub])
                            
                            data_map[f"{current_context} > {curr_name}"] = {
                                "player": player_text.strip(),
                                "full": mj_text.strip()
                            }
                        elif "entries" in item:
                            extract_entries(item["entries"], current_context)

            for section in content.get("data",[]):
                if section.get("name", "") == "Appendice C : Le Tirage des Destinées Planaire":
                    extract_entries(section.get("entries",[]))
        except Exception as e:
            print(f"[Tarokka] Erreur de parsing JSON : {e}")
        return data_map

    def get_card_name(self, filename):
        name = filename.replace('.jpg', '')
        name = name.replace('gylphs', 'Glyphes').replace('glyphs', 'Glyphes')
        name = name.replace('swords', 'Épées').replace('stars', 'Étoiles').replace('coins', 'Deniers')
        
        num_map = {'1': 'As', '2': 'Deux', '3': 'Trois', '4': 'Quatre', '5': 'Cinq', '6': 'Six', '7': 'Sept', '8': 'Huit', '9': 'Neuf'}
        if " - " in name:
            parts = name.split(" - ")
            if parts[0] in num_map:
                suit = parts[1]
                if suit in["Épées", "Étoiles"]:
                    name = f"{num_map[parts[0]]} d'{suit}"
                else:
                    name = f"{num_map[parts[0]]} de {suit}"

        name = name.replace('Warrior', 'Maître d\'Épées—Guerrier')
        name = name.replace('Wizard', 'Maître d\'Étoiles—Sorcier')
        name = name.replace('Rogue', 'Maître de Deniers—Gredin')
        name = name.replace('Priest', 'Maître de Glyphes—Prêtre')

        translations = {
            'Artifact': 'Artefact (Joker 1)', 'Beast': 'La Bête (Valet de Carreau)', 'Broken One': 'Le Brisé (Roi de Carreau)',
            'Dark Lord': 'Le Seigneur des Ténèbres (Roi de Pique)', 'Donjon': 'Le Donjon (Roi de Trèfle)',
            'Executioner': 'Le Bourreau (Valet de Pique)', 'Ghost': 'Le Fantôme (Roi de Cœur)', 'Horseman': 'Le Cavalier (Joker 2)',
            'Innocent': 'L\'Innocent (Reine de Cœur)', 'Marionette': 'La Marionnette (Valet de Cœur)', 'Mists': 'Les Brumes (Reine de Pique)',
            'Raven': 'Le Corbeau (Reine de Trèfle)', 'Seer': 'Le Voyant (Valet de Trèfle)', 'Tempter': 'Le Tentateur (Reine de Carreau)'
        }
        return translations.get(name, name)

    def get_description_from_data(self, step, card_name):
        target_context = self.meanings[step].lower().replace("’", "'")
        search_key = card_name.split("—")[0].strip().lower()
        if "(" in search_key:
            search_key = search_key.split("(")[0].strip()
        search_key = search_key.replace("’", "'")

        for key, desc_dict in self.tarokka_data.items():
            key_lower = key.lower().replace("’", "'")
            if key_lower.startswith(target_context) and search_key in key_lower:
                return desc_dict
        
        fallback = "La carte demeure muette... aucune donnée trouvée dans le grimoire pour ce tirage."
        return {"player": fallback, "full": fallback}

    def show_card_details(self, step_idx, card_name):
        desc_dict = self.current_card_descriptions[step_idx]
        full_desc = desc_dict["full"]
        player_desc = desc_dict["player"]
        intro_text = self.step_intros[step_idx]
        
        popout = tk.Toplevel(self.top)
        popout.title(f"Le Destin Révélé : {card_name}")
        popout.geometry("700x750")
        popout.configure(bg="#2a2a2a")
        
        lbl_desc_title = tk.Label(popout, text=self.meanings[step_idx], bg="#2a2a2a", fg="#ffcc00", font=("Arial", 12, "bold"))
        lbl_desc_title.pack(pady=10)
        
        txt_desc = tk.Text(popout, bg="#1e1e1e", fg="#e0e0e0", font=("Consolas", 11), wrap=tk.WORD)
        txt_desc.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)
        
        txt_desc.insert(tk.END, f"{intro_text}\n\n[{card_name}]\n\n{full_desc}")
        txt_desc.config(state=tk.DISABLED)

        clean_player_desc = player_desc.replace('*', '')
        text_to_send = f"🎴 {self.meanings[step_idx]} : {card_name}\n\n{intro_text}\n\n{clean_player_desc}"

        def send_and_close():
            self.msg_queue.put({
                "action": "tarokka_speak",
                "text": text_to_send,
                "color": "#9b8fc7"
            })
            popout.destroy()

        btn_send_chat = tk.Button(popout, text="📢 Révéler au groupe (Murmures de Madam Eva)",
                                  bg="#5d3d7d", fg="white", font=("Arial", 11, "bold"),
                                  command=send_and_close, relief="flat", pady=8)
        btn_send_chat.pack(fill=tk.X, padx=15, pady=15)

    def _render_card(self, step_index, card_file):
        """Affiche physiquement la carte sur le canvas."""
        card_path = os.path.join("images", "tarokka", card_file)
        name = self.get_card_name(card_file)
        
        # On ajoute TOUJOURS la description pour garder les index synchronisés
        desc_dict = self.get_description_from_data(step_index, name)
        self.current_card_descriptions.append(desc_dict)

        try:
            if os.path.exists(card_path):
                img = Image.open(card_path)
                img = img.resize((140, 215), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.drawn_cards_refs.append(photo) # Protège l'image du Garbage Collector

                x, y = self.positions[step_index]
                
                self.canvas.create_rectangle(x-68, y-106, x+72, y+110, fill="#111111", outline="", tags="card_element")
                img_id = self.canvas.create_image(x, y, image=photo, anchor="center", tags="card_element")
                
                txt_y = y + 125
                self.canvas.create_rectangle(x-110, txt_y-10, x+110, txt_y+10, fill="#1e1e1e", stipple="gray50", outline="", tags="card_element")
                self.canvas.create_text(x, txt_y, text=name, fill="#ffcc00", font=("Arial", 10, "bold"), tags="card_element")
                
                self.canvas.tag_bind(img_id, "<Button-1>", lambda e, s=step_index, n=name: self.show_card_details(s, n))
                self.canvas.tag_bind(img_id, "<Enter>", lambda e: self.canvas.config(cursor="hand2"))
                self.canvas.tag_bind(img_id, "<Leave>", lambda e: self.canvas.config(cursor=""))

                self.lbl_info.config(text=f"{self.meanings[step_index]} : {name} (Cliquez pour révéler)")
                print(f"[Tarokka] Dessin de la carte '{name}' réussi sur l'interface.")
            else:
                print(f"[Tarokka] Image manquante sur le disque: {card_path}")
        except Exception as e:
            print(f"[Tarokka] Erreur de chargement de la carte {card_file}: {e}")

    def draw_next_card(self):
        if self.step >= 5:
            return

        if self.step < 3:
            card_file = self.common_deck.pop()
        else:
            card_file = self.high_deck.pop()

        self._render_card(self.step, card_file)
        self.drawn_cards_files.append(card_file)
        self.step += 1

        if self.step >= 5:
            self.btn_draw.config(state=tk.DISABLED, text="Tirage Terminé", bg="#333333", fg="#888888")
            
        self._notify_save()

    def _notify_save(self):
        # 1. On modifie directement le dictionnaire d'origine en mémoire
        # C'est souvent indispensable si le script parent sauvegarde son état global
        if isinstance(self.initial_state, dict):
            self.initial_state["drawn_cards"] = list(self.drawn_cards_files)
        elif self.initial_state is None:
            self.initial_state = {"drawn_cards": list(self.drawn_cards_files)}

        # 2. On déclenche la sauvegarde du parent
        if self.save_callback:
            try:
                self.save_callback({"drawn_cards": list(self.drawn_cards_files)})
                print(f"[Tarokka DEBUG] Sauvegarde déclenchée avec {len(self.drawn_cards_files)} cartes.")
            except Exception as e:
                print(f"[Tarokka ERREUR] Échec de la fonction de sauvegarde du parent : {e}")
        else:
            print("[Tarokka AVERTISSEMENT] Aucun save_callback fourni par le parent.")

    def reset_tarokka(self):
        """Réinitialise complètement le plateau de jeu et les paquets"""
        self.canvas.delete("card_element")
        
        self.step = 0
        self.drawn_cards_refs.clear()
        self.current_card_descriptions.clear()
        self.drawn_cards_files.clear()
        
        self.common_deck = self.base_common_deck.copy()
        self.high_deck = self.base_high_deck.copy()
        random.shuffle(self.common_deck)
        random.shuffle(self.high_deck)
        
        self.btn_draw.config(state=tk.NORMAL, text="🃏 Tirer la carte suivante", bg="#4a1e3a", fg="white")
        self.lbl_info.config(text="Tirage réinitialisé avec succès.")
        
        self._notify_save()