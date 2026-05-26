"""
character_creator_mixin.py

Contient le mixin permettant d'ouvrir la fenêtre de création de personnage.
"""

from character_creator import CharacterCreatorWindow

class CharacterCreatorMixin:
    """Mixin pour lancer le créateur de personnage."""
    
    def open_character_creator(self, edit_char_name=None):
        """Ouvre l'assistant de création ou d'édition de personnage."""
        # Évite d'ouvrir de multiples fenêtres
        if getattr(self, "_char_creator_win", None) and self._char_creator_win.top.winfo_exists():
            # Si on veut éditer, on force la fermeture de l'ancienne fenêtre pour recharger les données
            self._char_creator_win.top.destroy()
            
        def _on_character_created(char_name):
            # Message système de bienvenue pour notifier les LLMs et le chat
            self.msg_queue.put({
                "sender": "⚙️ Système",
                "text": f"🎉 Le personnage {char_name} a été créé (en attente d'ajout au groupe) !",
                "color": "#4CAF50"
            })
            # Ouvre automatiquement la fiche popout du nouveau personnage
            if hasattr(self, "open_char_popout"):
                self.open_char_popout(char_name)
            # Rafraîchit les cartes dans la barre latérale MJ
            if hasattr(self, "rebuild_char_cards_ui"):
                self.rebuild_char_cards_ui()
                
        def _on_character_updated():
            # Rafraîchit l'UI (Barre latérale + Gestionnaire) en direct pendant la création
            if hasattr(self, "rebuild_char_cards_ui"):
                self.rebuild_char_cards_ui()
            if getattr(self, "_char_manager_win", None) and self._char_manager_win.top.winfo_exists():
                self._char_manager_win.refresh_list()
                
        self._char_creator_win = CharacterCreatorWindow(self.root, on_complete=_on_character_created, on_update=_on_character_updated, edit_char_name=edit_char_name)