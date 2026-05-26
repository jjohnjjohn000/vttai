"""
character_manager_mixin.py

Contient le mixin permettant d'ouvrir la fenêtre de gestion globale des personnages,
ainsi que de forcer le rafraîchissement des cartes de la barre latérale.
"""

from character_manager import CharacterManagerWindow

class CharacterManagerMixin:
    """Mixin pour gérer la liste des personnages et leur UI dans la barre latérale."""
    
    def open_character_manager(self):
        """Ouvre le panneau de gestion des personnages."""
        if getattr(self, "_char_manager_win", None) and self._char_manager_win.top.winfo_exists():
            self._char_manager_win.top.lift()
            return
            
        self._char_manager_win = CharacterManagerWindow(self.root, self)
        if hasattr(self, "_track_window"):
            self._track_window("character_manager", self._char_manager_win.top)

    def rebuild_char_cards_ui(self):
        """Détruit et reconstruit les cartes de personnages dans la barre latérale droite.
        Appelé après la création ou la suppression d'un personnage."""
        if not hasattr(self, "_char_card_frame"):
            return
            
        # Vider le frame contenant les cartes
        for w in self._char_card_frame.winfo_children():
            w.destroy()
            
        # Vider le dictionnaire de référence
        if hasattr(self, "_char_cards"):
            self._char_cards.clear()
            
        # Reconstruire avec l'état actuel
        if hasattr(self, "_build_char_cards"):
            self._build_char_cards()