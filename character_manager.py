import tkinter as tk
from tkinter import ttk, messagebox
from state_manager import load_state, save_state

class CharacterManagerWindow:
    """Fenêtre de gestion des personnages (Liste, Ajout, Suppression)."""
    
    def __init__(self, parent, app):
        self.top = tk.Toplevel(parent)
        self.top.title("👥 Gestion des Personnages")
        self.top.geometry("650x400")
        self.top.configure(bg="#1e1e2e")
        self.app = app
        
        self._build_ui()
        self.refresh_list()
        
        # Met à jour la liste automatiquement quand la fenêtre reprend le focus
        # (utile quand on revient du créateur de personnage)
        self.top.bind("<FocusIn>", lambda e: self.refresh_list())
        
    def _build_ui(self):
        style = ttk.Style(self.top)
        style.theme_use("default")
        style.configure("Treeview", background="#252535", fieldbackground="#252535", foreground="white", borderwidth=0)
        style.configure("Treeview.Heading", background="#3d3d5c", foreground="white", font=("Arial", 10, "bold"))
        style.map("Treeview", background=[("selected", "#4CAF50")])
        
        # Barre d'outils haut
        toolbar = tk.Frame(self.top, bg="#1e1e2e")
        toolbar.pack(fill=tk.X, padx=10, pady=10)
        
        tk.Button(toolbar, text="➕ Créer un Personnage", bg="#4CAF50", fg="black", font=("Arial", 10, "bold"),
                  relief="flat", padx=10, pady=4, command=self._create_character).pack(side=tk.LEFT)
                  
        # Liste (Treeview)
        columns = ("name", "race", "class", "level", "hp", "in_party")
        self.tree = ttk.Treeview(self.top, columns=columns, show="headings", height=12)
        
        self.tree.heading("name", text="Nom")
        self.tree.heading("race", text="Race")
        self.tree.heading("class", text="Classe")
        self.tree.heading("level", text="Niv.")
        self.tree.heading("hp", text="PV Max")
        self.tree.heading("in_party", text="Groupe")
        
        self.tree.column("name", width=150)
        self.tree.column("race", width=150)
        self.tree.column("class", width=120)
        self.tree.column("level", width=50, anchor=tk.CENTER)
        self.tree.column("hp", width=60, anchor=tk.CENTER)
        self.tree.column("in_party", width=60, anchor=tk.CENTER)
        
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10)
        self.tree.bind("<Double-1>", lambda e: self._open_sheet())
        self.tree.bind("<Button-3>", self._show_context_menu)
        
        # Barre de boutons bas
        btn_fr = tk.Frame(self.top, bg="#1e1e2e")
        btn_fr.pack(fill=tk.X, padx=10, pady=10)
        
        tk.Button(btn_fr, text="👁️ Ouvrir la Fiche", bg="#2196F3", fg="white", font=("Arial", 9, "bold"),
                  relief="flat", padx=10, pady=4, command=self._open_sheet).pack(side=tk.LEFT, padx=(0, 10))

        tk.Button(btn_fr, text="👥 Groupe (On/Off)", bg="#9C27B0", fg="white", font=("Arial", 9, "bold"),
                  relief="flat", padx=10, pady=4, command=self._toggle_party_status).pack(side=tk.LEFT, padx=(0, 10))
                  
        tk.Button(btn_fr, text="🗑️ Supprimer", bg="#F44336", fg="white", font=("Arial", 9, "bold"),
                  relief="flat", padx=10, pady=4, command=self._delete_character).pack(side=tk.RIGHT)

    def refresh_list(self):
        """Recharge la liste des personnages depuis l'état."""
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        state = load_state()
        chars = state.get("characters", {})
        
        for name, data in chars.items():
            race = data.get("race", "Inconnu")
            subrace = data.get("subrace", "")
            race_display = f"{race} ({subrace})" if subrace else race
            
            cls = data.get("class", "Inconnu").title()
            lvl = data.get("level", 1)
            hp = data.get("max_hp", 0)
            
            in_party = "Oui" if data.get("in_party", True) else "Non"
            self.tree.insert("", tk.END, iid=name, values=(name, race_display, cls, lvl, hp, in_party))
            
    def _create_character(self):
        """Ouvre l'assistant de création (qui gère l'ajout dans l'état)."""
        if hasattr(self.app, "open_character_creator"):
            self.app.open_character_creator()

    def _show_context_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            menu = tk.Menu(self.top, tearoff=0, bg="#252535", fg="white", activebackground="#4CAF50")
            menu.add_command(label="👁️ Ouvrir la Fiche", command=self._open_sheet)
            menu.add_command(label="✏️ Gérer le personnage", command=self._edit_character)
            menu.add_command(label="👥 Ajouter/Retirer du Groupe", command=self._toggle_party_status)
            menu.add_separator()
            menu.add_command(label="🗑️ Supprimer", command=self._delete_character, foreground="#F44336")
            menu.tk_popup(event.x_root, event.y_root)

    def _toggle_party_status(self):
        sel = self.tree.selection()
        if not sel: return
        char_name = sel[0]
        
        state = load_state()
        if char_name in state.get("characters", {}):
            char_data = state["characters"][char_name]
            current_status = char_data.get("in_party", True)
            new_status = not current_status
            char_data["in_party"] = new_status
            
            if not new_status:
                char_data["active"] = False
            else:
                char_data["active"] = True
                
            save_state(state)
            self.refresh_list()
            
            if hasattr(self.app, "rebuild_char_cards_ui"):
                self.app.rebuild_char_cards_ui()
                
            status_text = "ajouté au" if new_status else "retiré du"
            if hasattr(self.app, "msg_queue"):
                self.app.msg_queue.put({
                    "sender": "⚙️ Système",
                    "text": f"👥 {char_name} a été {status_text} groupe actif.",
                    "color": "#9C27B0"
                })

    def _edit_character(self):
        sel = self.tree.selection()
        if not sel: return
        char_name = sel[0]
        if hasattr(self.app, "open_character_creator"):
            self.app.open_character_creator(edit_char_name=char_name)

    def _open_sheet(self):
        """Ouvre la fiche détaillée du personnage sélectionné."""
        sel = self.tree.selection()
        if not sel: return
        char_name = sel[0]
        if hasattr(self.app, "open_char_popout"):
            self.app.open_char_popout(char_name)
            
    def _delete_character(self):
        """Supprime le personnage sélectionné de l'état global."""
        sel = self.tree.selection()
        if not sel: return
        char_name = sel[0]
        
        confirm = messagebox.askyesno(
            "Confirmer la suppression", 
            f"Voulez-vous vraiment supprimer définitivement le personnage '{char_name}' ?\nCette action est irréversible.",
            parent=self.top
        )
        if confirm:
            state = load_state()
            if char_name in state.get("characters", {}):
                del state["characters"][char_name]
                save_state(state)
                self.refresh_list()
                
                # Force la mise à jour de la barre latérale du MJ
                if hasattr(self.app, "rebuild_char_cards_ui"):
                    self.app.rebuild_char_cards_ui()