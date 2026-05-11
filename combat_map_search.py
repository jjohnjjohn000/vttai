# combat_map_search.py

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import json
import os
import re
import threading
import urllib.request
import urllib.error

class AdventureSearchWindow:
    def __init__(self, parent, adventure_dir="adventure", map_app=None, book_dir="book"):
        self.top = tk.Toplevel(parent)
        self.top.title("📖 Moteur de Recherche d'Aventure et Règles")
        self.top.geometry("1150x700")
        self.top.configure(bg="#0d1018")
        
        self.adventure_dir = adventure_dir
        self.book_dir = book_dir
        self.records =[]
        self.map_app = map_app
        
        self._load_data()
        self._build_ui()

    def _load_data(self):
        """Parcourt tous les fichiers JSON des dossiers adventure/ et book/ et les indexe."""
        self._index_directory(self.adventure_dir, "Aventure")
        self._index_directory(self.book_dir, "Livre")

        if not self.records:
            messagebox.showwarning(
                "Données introuvables", 
                f"Aucun fichier JSON n'a été trouvé dans '{self.adventure_dir}' ou '{self.book_dir}'.",
                parent=self.top
            )

    def _index_directory(self, directory, source_type):
        if not os.path.exists(directory):
            return

        for filename in os.listdir(directory):
            if filename.endswith(".json"):
                filepath = os.path.join(directory, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    
                    # Le point d'entrée varie souvent, mais c'est généralement dans 'data' ou la racine
                    root_node = data.get("data", data)
                    self._traverse(root_node, filename,[], None, None, source_type)
                except Exception as e:
                    print(f"[AdventureSearch] Erreur de lecture de {filepath}: {e}")

    def _traverse(self, node, file_name, context_names, chap_node, sec_node, source_type="Aventure"):
        """Parcourt récursivement le JSON pour extraire le texte et son contexte."""
        if isinstance(node, dict):
            name = node.get("name", "")
            
            # Détection de Chapitre ou Section selon le nom ou le niveau d'arborescence
            is_chapter = False
            if name and any(k in name for k in["Chapter", "Appendix", "Introduction", "Foreword", "Epilogue"]):
                chap_node = node
                is_chapter = True
            elif name:
                sec_node = node
                
            new_contexts = context_names + [name] if name else context_names
            
            if node.get("type") == "image":
                href = node.get("href", {})
                path = href.get("path") if isinstance(href, dict) else href if isinstance(href, str) else ""
                title = node.get("title", "Image")
                if path:
                    self.records.append({
                        "source_type": source_type,
                        "file": file_name,
                        "chapter": chap_node.get("name", "General") if chap_node else "General",
                        "section": sec_node.get("name", "General") if sec_node else "General",
                        "text": f"[Image] {title}",
                        "chapter_node": chap_node,
                        "section_node": {"name": title, "type": "image", "href": path},
                        "node": node
                    })
            
            # Extraire le texte immédiat de ce noeud
            immediate_text = ""
            if "entries" in node:
                for entry in node["entries"]:
                    if isinstance(entry, str):
                        immediate_text += entry + "\n"
                    elif isinstance(entry, dict) and entry.get("type") in["quote", "inset", "insetReadaloud"]:
                        # Aplatir directement ces sous-blocs textuels
                        immediate_text += self._render_node(entry) + "\n"
                    else:
                        self._traverse(entry, file_name, new_contexts, chap_node, sec_node, source_type)
                        
            if immediate_text.strip() and sec_node:
                # Nettoyage des balises {@tag...} pour la recherche
                clean_text = self._render_node(immediate_text)
                clean_text = re.sub(r'<img src=".*?" />', '', clean_text)
                
                self.records.append({
                    "source_type": source_type,
                    "file": file_name,
                    "chapter": chap_node.get("name", "Unknown") if chap_node else "General",
                    "section": sec_node.get("name", "Unknown") if sec_node else "General",
                    "text": clean_text.strip(),
                    "chapter_node": chap_node,
                    "section_node": sec_node,
                    "node": node
                })
                
        elif isinstance(node, list):
            for item in node:
                self._traverse(item, file_name, context_names, chap_node, sec_node, source_type)

    def _render_node(self, node, indent=0):
        """Transforme un noeud JSON D&D en texte formaté lisible."""
        ind = " " * indent
        text = ""
        
        if isinstance(node, str):
            # Enlève {@tag Texte|Source} -> Texte
            cleaned = re.sub(r'{@\w+\s+([^|}]+)[^}]*}', r'\1', node)
            # Enlève {@b Texte} -> Texte
            cleaned = re.sub(r'{@\w+\s+([^}]+)}', r'\1', cleaned)
            text += ind + cleaned + "\n\n"
            
        elif isinstance(node, list):
            for item in node:
                text += self._render_node(item, indent)
                
        elif isinstance(node, dict):
            name = node.get("name", "")
            if name:
                text += "\n" + ind + "■ " + name.upper() + " ■\n\n"
                
            node_type = node.get("type", "")
            
            if node_type == "table":
                if "caption" in node:
                    text += ind + f"Table: {node['caption']}\n"
                if "rows" in node:
                    for row in node["rows"]:
                        clean_cells =[]
                        for cell in row:
                            if isinstance(cell, dict) and "entry" in cell:
                                clean_cells.append(self._render_node(cell["entry"], 0).strip())
                            elif isinstance(cell, str):
                                clean_cells.append(self._render_node(cell, 0).strip())
                        text += ind + " | ".join(clean_cells) + "\n"
                text += "\n"
                
            elif node_type == "list":
                for item in node.get("items",[]):
                    if isinstance(item, dict) and "name" in item and "entry" in item:
                        text += ind + f"• {item['name']}: {self._render_node(item['entry'], 0).strip()}\n"
                    else:
                        text += ind + f"• {self._render_node(item, 0).strip()}\n"
                text += "\n"
                
            elif node_type in ["quote", "inset", "insetReadaloud"]:
                text += ind + ">>>\n"
                if "entries" in node:
                    text += self._render_node(node["entries"], indent + 2)
                if "by" in node:
                    text += ind + f"  - {node['by']}\n"
                text += ind + "<<<\n\n"
                
            elif node_type == "image":
                href = node.get("href", {})
                path = href.get("path") if isinstance(href, dict) else href if isinstance(href, str) else ""
                if path:
                    text += f"\n{ind}<img src=\"{path}\" />\n\n"
                
            else:
                if "entries" in node:
                    text += self._render_node(node["entries"], indent)
                if "items" in node:
                    text += self._render_node(node["items"], indent)
                    
        return text

    def _build_ui(self):
        # --- PANNEAU DE RECHERCHE ---
        search_frame = tk.Frame(self.top, bg="#1a1a2e", padx=10, pady=10)
        search_frame.pack(fill=tk.X, side=tk.TOP)
        
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TLabel", background="#1a1a2e", foreground="#dde0e8", font=("Consolas", 10))
        
        # Options de source
        source_frame = tk.Frame(search_frame, bg="#1a1a2e")
        source_frame.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 15))
        ttk.Label(source_frame, text="Rechercher dans :").pack(anchor="w", pady=(0, 2))
        
        self.search_adv_var = tk.BooleanVar(value=True)
        tk.Checkbutton(source_frame, text="Aventures", variable=self.search_adv_var, 
                       bg="#1a1a2e", fg="#dde0e8", selectcolor="#252538", 
                       activebackground="#1a1a2e", activeforeground="white").pack(anchor="w")
        
        self.search_book_var = tk.BooleanVar(value=True)
        tk.Checkbutton(source_frame, text="Livres", variable=self.search_book_var, 
                       bg="#1a1a2e", fg="#dde0e8", selectcolor="#252538", 
                       activebackground="#1a1a2e", activeforeground="white").pack(anchor="w")

        # Mots Clés
        ttk.Label(search_frame, text="Phrase Exacte :").grid(row=0, column=1, sticky="w", pady=2)
        self.exact_var = tk.StringVar()
        tk.Entry(search_frame, textvariable=self.exact_var, width=28, bg="#252538", fg="white", insertbackground="white").grid(row=0, column=2, padx=5)

        ttk.Label(search_frame, text="ET (requis, par virgule) :").grid(row=1, column=1, sticky="w", pady=2)
        self.and_var = tk.StringVar()
        tk.Entry(search_frame, textvariable=self.and_var, width=28, bg="#252538", fg="white", insertbackground="white").grid(row=1, column=2, padx=5)

        ttk.Label(search_frame, text="OU (au moins un) :").grid(row=0, column=3, sticky="w", padx=10, pady=2)
        self.or_var = tk.StringVar()
        tk.Entry(search_frame, textvariable=self.or_var, width=28, bg="#252538", fg="white", insertbackground="white").grid(row=0, column=4, padx=5)

        ttk.Label(search_frame, text="SAUF (exclus) :").grid(row=1, column=3, sticky="w", padx=10, pady=2)
        self.except_var = tk.StringVar()
        tk.Entry(search_frame, textvariable=self.except_var, width=28, bg="#252538", fg="white", insertbackground="white").grid(row=1, column=4, padx=5)

        # Bouton Recherche
        btn_frame = tk.Frame(search_frame, bg="#1a1a2e")
        btn_frame.grid(row=0, column=5, rowspan=2, padx=20)
        tk.Button(btn_frame, text="Lancer la Recherche", bg="#2c1a00", fg="#ffb74d", 
                  font=("Consolas", 10, "bold"), relief="flat", command=self._do_search).pack(fill=tk.BOTH, expand=True)

        # --- PANNEAU DE RÉSULTATS ---
        res_frame = tk.Frame(self.top, bg="#0d1018")
        res_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        columns = ("Type", "File", "Chapter", "Section", "Snippet")
        self.tree = ttk.Treeview(res_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("Type", text="Type")
        self.tree.heading("File", text="Fichier source")
        self.tree.heading("Chapter", text="Chapitre")
        self.tree.heading("Section", text="Section")
        self.tree.heading("Snippet", text="Extrait de Texte")
        
        self.tree.column("Type", width=80, stretch=False)
        self.tree.column("File", width=130, stretch=False)
        self.tree.column("Chapter", width=180, stretch=False)
        self.tree.column("Section", width=220, stretch=False)
        self.tree.column("Snippet", width=400, stretch=True)
        
        scroll = ttk.Scrollbar(res_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scroll.set)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.tree.bind("<Double-1>", self._on_double_click)
        
        # Info bottom
        tk.Label(self.top, text="ℹ Double-cliquez sur un résultat pour l'ouvrir. Vous pourrez ensuite le traduire ou afficher le chapitre complet.",
                 bg="#0d1018", fg="#555577", font=("Consolas", 9, "italic")).pack(side=tk.BOTTOM, pady=5)
                 
        # Bind Return key to search
        self.top.bind("<Return>", lambda e: self._do_search())

    def _do_search(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        exact = self.exact_var.get().strip().lower()
        kws_and =[k.strip().lower() for k in self.and_var.get().split(',') if k.strip()]
        kws_or =[k.strip().lower() for k in self.or_var.get().split(',') if k.strip()]
        kws_exc =[k.strip().lower() for k in self.except_var.get().split(',') if k.strip()]
        
        search_adv = self.search_adv_var.get()
        search_book = self.search_book_var.get()
        
        for idx, rec in enumerate(self.records):
            # Filtrer selon la source sélectionnée (Aventure / Livre)
            if rec["source_type"] == "Aventure" and not search_adv:
                continue
            if rec["source_type"] == "Livre" and not search_book:
                continue

            text_pool = f"{rec['chapter']} {rec['section']} {rec['text']}".lower()
            
            if exact and exact not in text_pool:
                continue
            if kws_and and not all(k in text_pool for k in kws_and):
                continue
            if kws_or and not any(k in text_pool for k in kws_or):
                continue
            if kws_exc and any(k in text_pool for k in kws_exc):
                continue
                
            snippet = rec["text"].replace('\n', ' ')
            if len(snippet) > 120:
                snippet = snippet[:120] + "..."
                
            self.tree.insert("", tk.END, iid=str(idx), values=(rec["source_type"], rec["file"], rec["chapter"], rec["section"], snippet))

    def _on_double_click(self, event):
        selected = self.tree.selection()
        if not selected: return
        idx = int(selected[0])
        record = self.records[idx]
        
        self._open_detail_window(record, is_chapter_view=False)
        
    def _open_detail_window(self, record, is_chapter_view=False):
        node_to_render = record["chapter_node"] if is_chapter_view else record["section_node"]
        title_prefix = "CHAPITRE" if is_chapter_view else "SECTION"
        name = node_to_render.get("name", "Unknown") if node_to_render else "Unknown"
        
        detail_win = tk.Toplevel(self.top)
        detail_win.title(f"📖 {title_prefix} : {name}")
        detail_win.geometry("850x650")
        detail_win.configure(bg="#0d1018")
        
        # Toolbar en haut
        tool_frame = tk.Frame(detail_win, bg="#1a1a2e", pady=5, padx=5)
        tool_frame.pack(fill=tk.X, side=tk.TOP)
        
        if not is_chapter_view and record["chapter_node"]:
            tk.Button(tool_frame, text="📖 Voir le Chapitre Complet", bg="#0e2010", fg="#81c784",
                      font=("Consolas", 10, "bold"), relief="flat",
                      command=lambda: self._open_detail_window(record, True)).pack(side=tk.LEFT, padx=5)

        # Bouton Traduction DeepL
        btn_translate = tk.Button(tool_frame, text="🌍 Traduire en Français (DeepL)", bg="#1e3a5f", fg="#81d4fa",
                                  font=("Consolas", 10, "bold"), relief="flat")
        btn_translate.pack(side=tk.RIGHT, padx=5)

        # Bouton Épingler sur la carte
        if self.map_app is not None:
            btn_pin = tk.Button(tool_frame, text="📌 Épingler sur la carte", bg="#4a148c", fg="#e1bee7",
                                font=("Consolas", 10, "bold"), relief="flat",
                                command=lambda: self._pin_to_map(text_widget, f"{title_prefix} : {name}"))
            btn_pin.pack(side=tk.RIGHT, padx=5)

            def _share_to_agents():
                if not hasattr(text_widget, "raw_image_paths") or not text_widget.raw_image_paths:
                    messagebox.showinfo("Partage", "Aucune image détectée dans cette vue.", parent=detail_win)
                    return
                img_path = text_widget.raw_image_paths[0]
                full_path = os.path.join("images", img_path)
                if os.path.exists(full_path):
                    with open(full_path, "rb") as f:
                        img_bytes = f.read()
                    current_text = text_widget.get("1.0", tk.END).strip()
                    main_app = getattr(self.map_app, "app", self.map_app)
                    if hasattr(main_app, "_broadcast_shared_image"):
                        main_app._broadcast_shared_image(img_bytes, f"{title_prefix} : {name}", current_text)
                        messagebox.showinfo("Succès", "Image transmise en interne aux agents multimodaux.", parent=detail_win)
                    else:
                        messagebox.showerror("Erreur", "L'interface principale n'est pas accessible pour le partage d'images.", parent=detail_win)
                else:
                    messagebox.showerror("Erreur", "Image non trouvée sur le disque.", parent=detail_win)

            btn_share = tk.Button(tool_frame, text="📸 Partager Image(s) aux Agents", bg="#004d40", fg="#b2dfdb",
                                  font=("Consolas", 10, "bold"), relief="flat", command=_share_to_agents)
            btn_share.pack(side=tk.RIGHT, padx=5)
        
        # Zone de texte formatée
        text_widget = tk.Text(detail_win, wrap=tk.WORD, bg="#151520", fg="#e0e0e0", font=("Georgia", 11),
                              padx=15, pady=15, selectbackground="#4a4a75", undo=True)
        text_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,10))
        
        scroll = ttk.Scrollbar(text_widget, command=text_widget.yview)
        text_widget.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Remplissage du texte initial
        full_text = self._render_node(node_to_render)
        self._insert_text_with_images(text_widget, full_text)
        
        # Surlignage (seulement pour la version originale anglaise)
        self._highlight_keywords(text_widget)
        # On ne désactive plus le widget pour permettre l'édition manuelle avant d'épingler

        # Raccordement de l'action de traduction au bouton
        btn_translate.configure(command=lambda: self._translate_text(text_widget, btn_translate, full_text))

    def _insert_text_with_images(self, text_widget, content):
        from PIL import Image, ImageTk
        if not hasattr(text_widget, "image_refs"):
            text_widget.image_refs = []
        if not hasattr(text_widget, "raw_image_paths"):
            text_widget.raw_image_paths = []
            
        pattern = r'<img src="(.*?)" />'
        last_index = 0
        for match in re.finditer(pattern, content):
            text_before = content[last_index:match.start()]
            if text_before:
                text_widget.insert(tk.END, text_before)
                
            img_path = match.group(1)
            full_path = os.path.join("images", img_path)
            
            if os.path.exists(full_path):
                try:
                    text_widget.raw_image_paths.append(img_path)
                    img = Image.open(full_path)
                    max_w, max_h = 750, 550
                    img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                    text_widget.image_refs.append(photo)
                    text_widget.insert(tk.END, "\n")
                    text_widget.image_create(tk.END, image=photo)
                    text_widget.insert(tk.END, "\n")
                except Exception as e:
                    print(f"[SearchApp] Erreur chargement image {full_path}: {e}")
                    text_widget.insert(tk.END, f"\n[Image manquante: {img_path}]\n")
            else:
                text_widget.insert(tk.END, f"\n[Image introuvable: {img_path}]\n")
                
            last_index = match.end()
            
        text_remaining = content[last_index:]
        if text_remaining:
            text_widget.insert(tk.END, text_remaining)

    def _highlight_keywords(self, text_widget):
        # Utilisation de parenthèses pour permettre le retour à la ligne proprement
        all_kws = (
            [self.exact_var.get().strip()] +[k.strip() for k in self.and_var.get().split(',') if k.strip()] +[k.strip() for k in self.or_var.get().split(',') if k.strip()]
        )
                  
        for kw in all_kws:
            if not kw: continue
            start_pos = "1.0"
            while True:
                start_pos = text_widget.search(kw, start_pos, stopindex=tk.END, nocase=True)
                if not start_pos:
                    break
                end_pos = f"{start_pos}+{len(kw)}c"
                text_widget.tag_add("highlight", start_pos, end_pos)
                start_pos = end_pos
                
        text_widget.tag_config("highlight", background="#b8860b", foreground="black", font=("Georgia", 11, "bold"))

    def _translate_text(self, text_widget, btn, source_text):
        """Lance l'appel API DeepL dans un thread séparé pour ne pas geler l'interface."""
        # 1. Essayer de récupérer la clé API
        api_key = os.getenv("TRANSLATE_DEEPL_API_KEY")
        if not api_key:
            try:
                from dotenv import load_dotenv
                load_dotenv()
                api_key = os.getenv("TRANSLATE_DEEPL_API_KEY")
            except ImportError:
                pass
        
        if not api_key:
            messagebox.showerror("Clé API manquante", 
                                 "La variable d'environnement TRANSLATE_DEEPL_API_KEY est introuvable.\n\n"
                                 "Ajoutez-la dans votre fichier .env.")
            return

        # 2. Préparation UI
        btn.configure(state="disabled", text="⏳ Traduction en cours...")
        
        # 3. Fonction exécutée dans un thread de fond
        def fetch_translation():
            try:
                # Identification de l'URL selon le type de clé DeepL (Free vs Pro)
                if api_key.endswith(":fx"):
                    endpoint = "https://api-free.deepl.com/v2/translate"
                else:
                    endpoint = "https://api.deepl.com/v2/translate"

                import html
                
                # 1. Isoler les images pour ne pas les échapper
                images =[]
                def extract_img(m):
                    images.append(m.group(0))
                    return f"__IMG_{len(images)-1}__"
                
                text_no_img = re.sub(r'<img src=".*?" />', extract_img, source_text)
                
                # 2. Échapper le texte brut pour le parseur XML de DeepL (<, >, &...)
                xml_safe_text = html.escape(text_no_img)
                
                # 3. Réinsérer les images sous forme de balises XML valides
                for i in range(len(images)):
                    xml_safe_text = xml_safe_text.replace(f"__IMG_{i}__", f'<img id="{i}"/>')

                # Pour DeepL, on configure la gestion des balises XML pour protéger les images
                payload = json.dumps({
                    "text": [xml_safe_text],
                    "target_lang": "FR",
                    "tag_handling": "xml",
                    "ignore_tags": ["img"]
                }).encode('utf-8')

                req = urllib.request.Request(endpoint, data=payload, method="POST")
                req.add_header("Authorization", f"DeepL-Auth-Key {api_key}")
                req.add_header("Content-Type", "application/json")

                with urllib.request.urlopen(req) as response:
                    res_data = json.loads(response.read().decode('utf-8'))
                    translated_xml = res_data["translations"][0]["text"]
                
                # 4. Restauration du texte brut et des balises d'images originales
                translated_text = html.unescape(translated_xml)
                for i in range(len(images)):
                    # DeepL peut altérer légèrement la balise XML (espacements, guillemets)
                    translated_text = re.sub(fr'<img id=[\'"]?{i}[\'"]?\s*/>', images[i], translated_text)

                # Succès -> Mettre à jour l'UI dans le Main Thread
                text_widget.after(0, lambda: apply_translation(translated_text))

            except urllib.error.URLError as e:
                err_msg = str(e)
                if hasattr(e, 'read'):
                    err_msg += f"\n{e.read().decode('utf-8')}"
                text_widget.after(0, lambda: on_error(f"Erreur HTTP: {err_msg}"))
            except Exception as e:
                text_widget.after(0, lambda: on_error(str(e)))

        # 4. Callbacks d'update UI
        def apply_translation(translated_text):
            text_widget.configure(state="normal")
            text_widget.delete("1.0", tk.END)
            if hasattr(text_widget, "image_refs"):
                text_widget.image_refs.clear()
            self._insert_text_with_images(text_widget, translated_text)
            # Reste éditable pour permettre de corriger la traduction
            
            btn.configure(text="🌍 Traduit en Français", bg="#2e7d32", fg="white")

        def on_error(err_msg):
            messagebox.showerror("Erreur de Traduction", f"Impossible de traduire le texte :\n{err_msg}")
            btn.configure(state="normal", text="🌍 Réessayer la traduction")

        # Lancement asynchrone
        threading.Thread(target=fetch_translation, daemon=True).start()

    def _pin_to_map(self, text_widget, full_title):
        """Épingle le texte actuel (traduit ou non) sur la carte de combat."""
        label = simpledialog.askstring("Épingler sur la carte", "Nom affiché sur la note :", parent=self.top)
        if not label or not label.strip():
            return
        
        # Récupérer le texte actuellement affiché
        current_text = text_widget.get("1.0", tk.END).strip()
        if hasattr(text_widget, "raw_image_paths") and text_widget.raw_image_paths:
            for img_path in text_widget.raw_image_paths:
                current_text += f'\n<img src="{img_path}" />\n'
        
        # Appeler la méthode sur la carte de combat pour créer le hotlink
        self.map_app.add_hotlink_note(label.strip(), full_title, current_text)
        messagebox.showinfo("Succès", f"La note '{label}' a été épinglée au centre de la carte !", parent=self.top)