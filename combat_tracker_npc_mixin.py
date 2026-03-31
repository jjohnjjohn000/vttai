"""
combat_tracker_npc_mixin.py
───────────────────────────
Fichier 8/10 : Mixin pour la gestion des PNJ (ajout, retrait et Kill Pool).
"""

import tkinter as tk
from tkinter import messagebox

try:
    from combat_tracker_constants import C, _BESTIARY_OK
    from combat_tracker_combatant import Combatant
except ImportError:
    pass

def _darken(hex_color: str, factor: float) -> str:
    try:
        h = hex_color.lstrip("#")
        if len(h) == 6:
            r = min(255, int(int(h[0:2], 16) * factor))
            g = min(255, int(int(h[2:4], 16) * factor))
            b = min(255, int(int(h[4:6], 16) * factor))
            return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        pass
    return hex_color


class CombatTrackerNPCMixin:
    """Mixin regroupant la logique de création, gestion et mort des PNJ."""

    def _add_npc(self):
        try:
            name    = self._npc_name.get().strip() or "Ennemi"
            max_hp  = int(self._npc_hp.get()  or 15)
            ac      = int(self._npc_ac.get()   or 13)
            dex_b   = int(self._npc_dex.get()  or 1)
            qty     = max(1, int(self._npc_qty.get() or 1))
            fixed   = self._npc_init_fixed.get().strip()
        except ValueError:
            messagebox.showwarning("Ajout PNJ", "Vérifiez les valeurs numériques.")
            return

        NPC_COLORS =["#ff9966","#ffcc66","#99ddff","#cc99ff",
                      "#99ffcc","#ff99bb","#ddbbff","#aaffaa"]

        bname = getattr(self, "_current_bestiary_name", "")

        # ── Résolution du portrait (une fois pour tous les clones) ───────────
        # On utilise bestiary_name en priorité car c'est la clé exacte du
        # fichier dans images/portraits/.  Si absent, on essaie avec le nom.
        portrait_path = ""
        try:
            from portrait_resolver import resolve_portrait
            portrait_path = resolve_portrait(bname or name)
        except Exception as _e:
            print(f"[CombatTracker] portrait_resolver introuvable : {_e}")

        for i in range(qty):
            n    = f"{name} {i+1}" if qty > 1 else name
            init = int(fixed) if fixed.lstrip("-").isdigit() else 0
            col  = NPC_COLORS[(len(self.combatants)) % len(NPC_COLORS)]
            c    = Combatant(name=n, is_pc=False,
                             max_hp=max_hp, ac=ac,
                             initiative=init, dex_bonus=dex_b,
                             color=col)
            c.bestiary_name = bname
            # Portrait pré-résolu — partagé entre tous les clones (même image)
            c.portrait = portrait_path

            # Alignement choisi dans le sélecteur H/N/A du panel
            try:
                c.alignment = self._npc_alignment.get() or "hostile"
            except Exception:
                c.alignment = "hostile"
            if not fixed.lstrip("-").isdigit():
                c.roll_initiative()
            self.combatants.append(c)

        # Reset bestiary state
        self._current_bestiary_name = ""
        if _BESTIARY_OK and hasattr(self, "_ct_status"):
            self._ct_status.config(text="")

        self._sort_and_refresh()
        self._log(f"+ {qty}x {name} ajoute(s) au combat.")

    # ─── Outils MJ au début du tour PNJ ──────────────────────────────────────

    def _show_npc_turn_tools(self, combatant):
        """
        À appeler au début du tour d'un combattant PNJ.
        Si le PNJ a une fiche de monstre (bestiary_name), affiche dans le chat
        un bloc interactif : attaques cliquables, sélecteur de cible, vitesse,
        actions bonus, réactions et traits. Ne fait rien si pas de fiche.
        """
        bname = getattr(combatant, "bestiary_name", "") or ""
        if not bname:
            return

        try:
            from npc_bestiary_panel import get_monster
        except ImportError:
            return

        monster = get_monster(bname)
        if not monster:
            return

        # Toutes les cibles possibles (les autres combattants encore actifs)
        targets = [c for c in self.combatants if c is not combatant]

        # On cherche d'abord chat_queue (tracker) puis msg_queue (app)
        queue = getattr(self, "chat_queue", None)
        if queue is None and hasattr(self, "app"):
            queue = getattr(self.app, "msg_queue", None)
            
        if queue is not None:
            queue.put({
                "action":    "npc_turn_tools",
                "combatant": combatant,
                "monster":   monster,
                "targets":   targets,
            })

    def _remove_combatant(self, c: Combatant):
        if c in self.combatants:
            idx = self.combatants.index(c)
            self.combatants.remove(c)
            if self.combat_active and self.current_idx >= idx:
                self.current_idx = max(0, self.current_idx - 1)
            self._refresh_list()

    def _add_to_kill_pool(self, c: Combatant):
        """Retire le combatant de l'initiative et l'ajoute au kill pool."""
        if c not in self.combatants:
            return
        idx = self.combatants.index(c)
        self.combatants.remove(c)
        if self.combat_active and self.current_idx >= idx:
            self.current_idx = max(0, self.current_idx - 1)
        self.kill_pool.append(c)
        self._refresh_list()
        self._refresh_kill_pool()
        self._log(f"[Kill Pool] {c.name} ({c.max_hp} PV max) retiré du combat.")
        if self.chat_queue:
            self.chat_queue.put({
                "sender": "⚔️ Combat",
                "text":   f"☠️ {c.name} est hors combat (Kill Pool).",
                "color":  "#9b59b6",
            })

    def _refresh_kill_pool(self):
        """Met à jour l'affichage du kill pool dans le bottom panel."""
        if not hasattr(self, "_kill_pool_inner"):
            return
        for w in self._kill_pool_inner.winfo_children():
            w.destroy()
        if not self.kill_pool:
            tk.Label(self._kill_pool_inner, text="— vide —",
                     bg="#0d1018", fg=C["fg_dim"],
                     font=("Consolas", 8)).pack(anchor="w")
            return
        for c in self.kill_pool:
            row = tk.Frame(self._kill_pool_inner, bg="#0d1018")
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=f"☠ {c.name}",
                     bg="#0d1018", fg="#cc66cc",
                     font=("Consolas", 8, "bold"), anchor="w"
                     ).pack(side=tk.LEFT, padx=(0, 6))
            tk.Label(row, text=f"{c.max_hp} PV",
                     bg="#0d1018", fg=C["fg_dim"],
                     font=("Consolas", 8)).pack(side=tk.LEFT)
            # Bouton Annuler (remettre dans l'initiative)
            def _restore(cb=c):
                if cb in self.kill_pool:
                    self.kill_pool.remove(cb)
                    cb.hp = max(1, cb.max_hp // 4)  # remet à 25% PV
                    self.combatants.append(cb)
                    self._sort_and_refresh()
                    self._refresh_kill_pool()
            tk.Button(row, text="Annuler",
                      bg=_darken(C["gold"], 0.55), fg=C["gold"],
                      font=("Consolas", 7), bd=0, relief="flat",
                      cursor="hand2", padx=3,
                      command=_restore).pack(side=tk.RIGHT)

    def _combat_tracker(self):
        """Ouvre la fenêtre CombatTracker ou la ramène au premier plan si déjà ouverte."""
        from combat_tracker import CombatTracker
        from state_manager import load_state  # Assure-toi que load_state est toujours là

        tracker = getattr(self, "_combat_tracker_win", None)
        if tracker is not None:
            try:
                # tracker est l'objet Python, tracker.win est le widget Tkinter
                if tracker.win.winfo_exists():
                    tracker.win.lift()
                    tracker.win.focus_force()
                    return tracker
            except Exception:
                pass  # fenêtre détruite, on en recrée une

        # Instanciation avec l'argument state_loader corrigé précédemment
        tracker = CombatTracker(self.root, app=self, state_loader=load_state)
        
        # FIX : Connecter explicitement le tracker à la file de messages du chat principal
        tracker.chat_queue = getattr(self, "msg_queue", None)
        
        self._combat_tracker_win = tracker

        # ── Connecter le callback de tour héros ──────────────────────────────
        # _on_pc_turn reconstruit les prompts et injecte le trigger AutoGen
        # quand c'est le tour d'un PJ vivant.
        tracker.pc_turn_callback = self._on_pc_turn

        # IMPORTANT : On passe tracker.win (le vrai Toplevel) à _track_window
        self._track_window("combat_tracker", tracker.win)
        
        return tracker

    def open_combat_tracker(self):
        """Alias public appelé par ui_setup_mixin."""
        return self._combat_tracker()

# Alias pour compatibilité avec les imports existants (main.py)
CombatTrackerMixin = CombatTrackerNPCMixin