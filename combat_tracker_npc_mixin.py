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

            # ── Placer automatiquement sur la carte si elle est ouverte ──
            self._place_on_map(c)

        # Reset bestiary state
        self._current_bestiary_name = ""
        if _BESTIARY_OK and hasattr(self, "_ct_status"):
            self._ct_status.config(text="")

        self._sort_and_refresh()
        self._log(f"+ {qty}x {name} ajoute(s) au combat.")

    def _place_on_map(self, c: "Combatant"):
        """
        Place (ou met à jour) un token sur la carte de combat pour ce Combatant.

        • Appelle map_win.place_token_for_combatant(c) qui est l'API canonique
          du TokenManagerMixin — elle porte bestiary_name, source_name, portrait,
          hp, max_hp, ac, alignment et la taille D&D 5e depuis le bestiaire.
        • Ne fait rien si la carte n'est pas ouverte.
        • Silencieux en cas d'erreur (ne doit jamais bloquer l'ajout au tracker).
        """
        try:
            map_win = getattr(getattr(self, "app", None), "_combat_map_win", None)
            if map_win is None:
                return
            if not hasattr(map_win, "place_token_for_combatant"):
                return
            map_win.place_token_for_combatant(c)
        except Exception as _e:
            print(f"[CombatTracker] _place_on_map({c.name}) : {_e}")

    # ─── Outils MJ au début du tour PNJ ──────────────────────────────────────

    def _show_npc_turn_tools(self, combatant):
        """
        À appeler au début du tour d'un combattant PNJ.
        Affiche le bloc interactif dans le chat. Contient un auto-correcteur
        si le lien avec le bestiaire a été perdu.
        """
        try:
            from npc_bestiary_panel import get_monster
        except ImportError:
            return

        bname = getattr(combatant, "bestiary_name", "") or ""
        
        # ── AUTO-CORRECTEUR (Fallback) ──
        # Si le tracker a perdu le nom du bestiaire, on essaie de le deviner
        # en retirant les numéros à la fin du nom (ex: "Hell Hound 2" -> "Hell Hound")
        if not bname:
            import re
            clean_name = re.sub(r'\s*\d+$', '', combatant.name).strip()
            if get_monster(clean_name):
                bname = clean_name
                combatant.bestiary_name = bname  # On répare la donnée pour la prochaine fois
            else:
                return  # Monstre vraiment introuvable dans 5etools

        monster = get_monster(bname)
        if not monster:
            return

        # Toutes les cibles possibles (les autres combattants encore actifs)
        targets =[c for c in self.combatants if c is not combatant]

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

    def _prompt_npc_death(self, c: "Combatant"):
        """
        Affiche une boîte de dialogue quand un PNJ atteint 0 PV.
        Propose d'ajouter au Kill Pool et/ou de retirer le token de la carte.

        Appelé par CombatTrackerRowMixin.apply_dmg via win.after(150, …)
        pour laisser le redraw se terminer avant d'ouvrir le Toplevel.
        """
        # Garde-fou : le combatant a peut-être reçu des soins entre-temps
        if c.hp > 0 or c not in self.combatants:
            return

        # Vérifier si un token existe sur la carte
        map_win = getattr(getattr(self, "app", None), "_combat_map_win", None)
        has_map_token = False
        if map_win is not None and hasattr(map_win, "tokens"):
            has_map_token = any(
                t.get("name") == c.name and not t.get("is_preview")
                for t in map_win.tokens
            )

        # ── Fenêtre modale ────────────────────────────────────────────────
        dlg = tk.Toplevel(self.win)
        dlg.title("☠️  PNJ à 0 PV")
        dlg.configure(bg="#0d1018")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.transient(self.win)

        # En-tête rouge sang
        hdr = tk.Frame(dlg, bg="#1a0505")
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="☠  PNJ HORS COMBAT",
                 bg="#1a0505", fg="#cc3333",
                 font=("Consolas", 12, "bold"),
                 pady=10, padx=16).pack(side=tk.LEFT)

        # Corps
        body = tk.Frame(dlg, bg="#0d1018", padx=20, pady=14)
        body.pack(fill=tk.X)

        tk.Label(body,
                 text=f"⚔  {c.name}  a été réduit(e) à 0 PV.",
                 bg="#0d1018", fg="#e0e0e0",
                 font=("Consolas", 11, "bold")).pack(anchor="w", pady=(0, 12))

        # Checkbox Kill Pool (cochée par défaut)
        var_kill = tk.BooleanVar(value=True)
        tk.Checkbutton(body,
                       text="☠  Ajouter au Kill Pool  (retire de l'initiative)",
                       variable=var_kill,
                       bg="#0d1018", fg="#cc66cc",
                       activebackground="#0d1018", activeforeground="#cc66cc",
                       selectcolor="#1a1a2a",
                       font=("Consolas", 9)).pack(anchor="w", pady=2)

        # Checkbox carte — active seulement si un token est présent
        var_map = tk.BooleanVar(value=has_map_token)
        map_cb = tk.Checkbutton(body,
                                text="🗺  Retirer le token de la carte de combat",
                                variable=var_map,
                                bg="#0d1018", fg="#99ddff",
                                activebackground="#0d1018", activeforeground="#99ddff",
                                selectcolor="#1a1a2a",
                                font=("Consolas", 9))
        map_cb.pack(anchor="w", pady=2)
        if not has_map_token:
            map_cb.config(state=tk.DISABLED, fg="#3a3a55")
            var_map.set(False)

        # Séparateur
        tk.Frame(dlg, bg=C["border"], height=1).pack(fill=tk.X, padx=16)

        # Boutons
        btn_f = tk.Frame(dlg, bg="#0d1018", pady=12, padx=20)
        btn_f.pack(fill=tk.X)

        def _confirm():
            dlg.destroy()
            if var_kill.get():
                self._add_to_kill_pool(c)
            if var_map.get() and map_win is not None:
                if hasattr(map_win, "remove_token_by_name"):
                    map_win.remove_token_by_name(c.name)

        def _ignore():
            dlg.destroy()

        tk.Button(btn_f, text="✔  Confirmer",
                  bg=_darken("#800080", 0.45), fg="#cc66cc",
                  font=("Consolas", 9, "bold"), relief="flat",
                  padx=10, pady=5, cursor="hand2",
                  command=_confirm).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(btn_f, text="Ignorer",
                  bg="#1a1a2a", fg="#888899",
                  font=("Consolas", 9), relief="flat",
                  padx=10, pady=5, cursor="hand2",
                  command=_ignore).pack(side=tk.LEFT)

        # Centrer sur la fenêtre parente
        dlg.update_idletasks()
        pw = self.win.winfo_x() + self.win.winfo_width()  // 2
        ph = self.win.winfo_y() + self.win.winfo_height() // 2
        dw = dlg.winfo_reqwidth()
        dh = dlg.winfo_reqheight()
        dlg.geometry(f"+{pw - dw // 2}+{ph - dh // 2}")

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

        # ── Notifier les agents IA de la mort (via l'historique de combat) ────
        # Sans ça, un agent dont c'est encore le tour continue de cibler
        # un ennemi déjà retiré du combat (ex: Extra Attack sur une cible morte).
        try:
            from combat_tracker_state import COMBAT_STATE as _CS
            # Construire la liste des ennemis encore en vie pour guider le prochain [ACTION]
            _alive = [x.name for x in self.combatants if not x.is_pc]
            _alive_str = ", ".join(_alive) if _alive else "aucun ennemi restant"
            _CS.setdefault("combat_history", []).append(
                f"☠ MORT : {c.name} est mort(e) et retiré(e) du combat. "
                f"⛔ {c.name} ne peut PLUS être ciblé(e). "
                f"Ennemis encore en vie : {_alive_str}."
            )
        except Exception as _e:
            print(f"[KillPool] Erreur maj combat_history : {_e}")

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