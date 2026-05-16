import queue
import tkinter as tk

from voice_interface import play_voice, prefetch_voice, play_prefetched
from agent_logger    import log_tts_end
from chat_log_writer import strip_mechanical_blocks

class ChatMixinQueue:
    """Mixin pour DnDApp — file audio, traitement de la file de messages, et dialogues PNJ/Tarokka."""

    # ─── Worker audio (thread daemon) ─────────────────────────────────────────

    def audio_worker(self):
        """
        Consomme audio_queue et joue les voix séquentiellement.
        Pendant la lecture de l'entrée N, on pré-génère les chunks TTS
        de l'entrée N+1 en parallèle → latence réduite entre personnages.
        """
        import threading as _th

        # Entrée courante : (text, name, prefetched_files | None)
        current_text  = None
        current_name  = None
        current_files = None   # None = pas encore préfetchée

        def _prefetch_worker(text, name, result_holder):
            result_holder[0] = prefetch_voice(text, name)

        prefetch_thread: _th.Thread | None = None
        next_text  = None
        next_name  = None
        next_holder: list = [None]   # [files] rempli par le thread

        while True:
            # ── Obtenir la prochaine entrée ────────────────────────────────
            if next_text is not None:
                # On a déjà préfetché la suivante pendant la lecture
                current_text  = next_text
                current_name  = next_name
                # Attendre que le prefetch soit fini s'il ne l'est pas encore
                if prefetch_thread and prefetch_thread.is_alive():
                    prefetch_thread.join()
                current_files = next_holder[0] or []
                next_text = next_name = None
                next_holder = [None]
                prefetch_thread = None
            else:
                try:
                    current_text, current_name = self.audio_queue.get(timeout=1.0)
                    current_files = None   # sera généré ci-dessous si la queue est vide
                except queue.Empty:
                    continue

            # ── Préfetch immédiat si pas encore fait ──────────────────────
            if current_files is None:
                # Pas de prochaine entrée connue — générer maintenant (bloquant)
                current_files = prefetch_voice(current_text, current_name)

            # ── Peek : y a-t-il une prochaine entrée dans la queue ? ──────
            try:
                next_text, next_name = self.audio_queue.get_nowait()
                # Lancer le prefetch en parallèle pendant qu'on joue
                next_holder = [None]
                prefetch_thread = _th.Thread(
                    target=_prefetch_worker,
                    args=(next_text, next_name, next_holder),
                    daemon=True,
                )
                prefetch_thread.start()
            except queue.Empty:
                next_text = next_name = None

            # ── Lecture ───────────────────────────────────────────────────
            face = self.face_windows.get(current_name)
            if face:
                # DÉLÉGATION AU THREAD PRINCIPAL (Sécurité Tkinter)
                self.root.after(0, lambda f=face: f.set_talking(True))
            try:
                if current_files:
                    success = play_prefetched(current_files)
                else:
                    # Fallback : prefetch a échoué → lecture directe
                    success = play_voice(current_text, current_name)
                
                # log_tts_end modifie probablement l'UI, il DOIT être dans root.after
                self.root.after(0, lambda c=current_name, s=bool(success): log_tts_end(c, success=s))
            except Exception as e:
                self.root.after(0, lambda c=current_name: log_tts_end(c, success=False))
                print(f"Erreur audio de {current_name}: {e}")
            finally:
                if face:
                    self.root.after(0, lambda f=face: f.set_talking(False))
            self.audio_queue.task_done()

    # ─── Pompe de messages (appelée par root.after) ───────────────────────────

    def process_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                action = msg.get("action")
                if action == "relay_button":
                    self._append_relay_button(msg["char_name"], msg["reply_text"])
                elif action == "ui_callback":
                    cb = msg.get("callback")
                    delay = msg.get("delay", 0)
                    if callable(cb):
                        if delay > 0:
                            self.root.after(delay, cb)
                        else:
                            cb()
                elif action == "map_pointer":
                    self._append_map_pointer(
                        msg.get("img_bytes"),
                        msg.get("comment", ""),
                        msg.get("sender", "🗺️ MJ"),
                    )
                elif action == "map_pointer_broadcast":
                    # Diffusion image + commentaire aux agents joueurs (hors thread Tk)
                    import threading as _th_ptr
                    _th_ptr.Thread(
                        target=self._broadcast_pointer_image,
                        args=(
                            msg.get("img_bytes"),
                            msg.get("comment", ""),
                            msg.get("col", 0),
                            msg.get("row", 0),
                            msg.get("map_name", ""),
                            msg.get("notes_txt", ""),
                        ),
                        daemon=True,
                    ).start()
                elif action == "spell_confirm":
                    self._append_spell_confirm(
                        msg["char_name"], msg["spell_name"],
                        msg["spell_level"], msg["target"],
                        msg["resume_callback"]
                    )
                elif action == "result_confirm":
                    self._append_result_confirm(
                        msg["char_name"],
                        msg["type_label"],
                        msg["results_text"],
                        msg["resume_callback"],
                        mode=msg.get("mode", "damage"),
                        target=msg.get("target"),
                        damage=msg.get("damage"),
                    )
                elif action == "action_confirm":
                    self._append_action_confirm(
                        msg["char_name"],
                        msg.get("type_label", "Action"),
                        msg["intention"],
                        msg["regle"],
                        msg["cible"],
                        msg["resume_callback"],
                        sub_index=msg.get("sub_index"),
                        sub_total=msg.get("sub_total"),
                        chain_abort_callback=msg.get("chain_abort_callback"),
                    )
                elif action == "skill_check_confirm":
                    try:
                        # ── INTERCEPTION : Forcer la cible et le bonus via la carte ──
                        try:
                            map_win = getattr(self, "_combat_map_win", None)
                            if map_win and hasattr(map_win, "_selected_tokens") and map_win._selected_tokens:
                                # Récupère l'ID du premier token sélectionné
                                sel_id = next(iter(map_win._selected_tokens))
                                token = next((t for t in getattr(map_win, "tokens",[]) if id(t) == sel_id), None)
                                
                                if token and "name" in token:
                                    target_name = token["name"]
                                    tracker = getattr(self, "_combat_tracker_win", None)
                                    combatant = next((c for c in getattr(tracker, "combatants",[]) if c.name == target_name), None) if tracker else None
                                    
                                    if combatant:
                                        # 1. On remplace le nom ciblé par celui du token
                                        msg["char_name"] = combatant.name
                                        
                                        # 2. Déduction de la statistique/compétence demandée
                                        s_lbl  = msg.get("skill_label", "").lower()
                                        st_lbl = msg.get("stat_label", "").lower()
                                        comb   = s_lbl + " " + st_lbl
                                        
                                        stat_map = {
                                            "force": "str", "str": "str", "strength": "str", "athlétisme": "str", "athletics": "str",
                                            "dextérité": "dex", "dex": "dex", "acrobaties": "dex", "discrétion": "dex", "escamotage": "dex", "stealth": "dex", "sleight of hand": "dex",
                                            "constitution": "con", "con": "con",
                                            "intelligence": "int", "int": "int", "arcanes": "int", "histoire": "int", "investigation": "int", "nature": "int", "religion": "int", "arcana": "int", "history": "int",
                                            "sagesse": "wis", "wis": "wis", "dressage": "wis", "médecine": "wis", "perception": "wis", "perspicacité": "wis", "survie": "wis", "animal handling": "wis", "medicine": "wis", "insight": "wis", "survival": "wis",
                                            "charisme": "cha", "cha": "cha", "intimidation": "cha", "persuasion": "cha", "représentation": "cha", "tromperie": "cha", "performance": "cha", "deception": "cha"
                                        }
                                        
                                        stat_key = next((v for k, v in stat_map.items() if k in comb), None)
                                        is_save  = "sauvegarde" in comb or "save" in comb
                                        
                                        if stat_key:
                                            if combatant.is_pc:
                                                # Pour les PJs, l'état global ne stocke que la Constitution (pour les jets de concentration)
                                                if stat_key == "con" and is_save:
                                                    from state_manager import load_state
                                                    st = load_state()
                                                    c_data = st.get("characters", {}).get(combatant.name, {})
                                                    if "con_mod" in c_data:
                                                        msg["bonus"] = c_data["con_mod"]
                                            else:
                                                # Pour un PNJ, on va extraire dynamiquement les infos du bestiaire
                                                b_name = combatant.bestiary_name
                                                if b_name:
                                                    from npc_bestiary_panel import get_monster
                                                    import re
                                                    monster = get_monster(b_name)
                                                    if monster:
                                                        # Calcul du modificateur brut de la caractéristique
                                                        bonus = (monster.get(stat_key, 10) - 10) // 2
                                                        
                                                        if is_save:
                                                            # Vérifie s'il y a maîtrise sur le jet de sauvegarde
                                                            saves = monster.get("save", {})
                                                            if stat_key in saves:
                                                                m = re.search(r'([+-]?\d+)', str(saves[stat_key]))
                                                                if m: bonus = int(m.group(1))
                                                        else:
                                                            # Vérifie s'il y a maîtrise sur la compétence
                                                            skills = monster.get("skill", {})
                                                            skill_en_keys = {
                                                                "athlétisme": "athletics", "acrobaties": "acrobatics", "discrétion": "stealth", "escamotage": "sleight of hand",
                                                                "arcanes": "arcana", "histoire": "history", "investigation": "investigation", "nature": "nature", "religion": "religion",
                                                                "dressage": "animal handling", "médecine": "medicine", "perception": "perception", "perspicacité": "insight", "survie": "survival",
                                                                "intimidation": "intimidation", "persuasion": "persuasion", "représentation": "performance", "tromperie": "deception"
                                                            }
                                                            match_k = None
                                                            for fr_k, en_k in skill_en_keys.items():
                                                                if fr_k in comb or en_k in comb:
                                                                    match_k = next((k for k in skills if k.lower().replace(" ", "") == en_k.replace(" ", "")), None)
                                                                    break
                                                            
                                                            if match_k:
                                                                m = re.search(r'([+-]?\d+)', str(skills[match_k]))
                                                                if m: bonus = int(m.group(1))
                                                        
                                                        # 3. Écrasement du bonus final !
                                                        msg["bonus"] = bonus
                        except Exception as override_err:
                            print(f"[Token Override] Erreur d'interception de la carte : {override_err}")
                        # ── FIN DE L'INTERCEPTION ──

                        self._append_skill_check_confirm(
                            msg["char_name"],
                            msg["skill_label"],
                            msg.get("stat_label", ""),
                            msg.get("bonus", 0),
                            msg.get("dc"),
                            msg.get("has_advantage", False),
                            msg.get("has_disadvantage", False),
                            msg.get("intention", ""),
                            msg["resume_callback"],
                        )
                    except Exception as _e_sc:
                        import traceback as _tb_sc
                        print(f"[process_queue] Erreur skill_check_confirm : {_e_sc}")
                        _tb_sc.print_exc()
                        try:
                            msg["resume_callback"](False, 0, "")
                        except Exception:
                            pass
                elif action == "tool_confirm":
                    self._append_tool_confirm_link(
                        msg["sender"],
                        msg["tool_name"],
                        msg.get("tool_args", {}),
                        msg["resume_callback"],
                    )
                elif action == "set_llm_running":
                    running = msg["value"]
                    self._llm_running = running
                    active = running and not self._waiting_for_mj
                    if hasattr(self, "btn_stop"):
                        self.btn_stop.config(state=tk.NORMAL if active else tk.DISABLED,
                                             bg="#cc0000" if active else "#880000")
                elif action == "set_waiting_for_mj":
                    waiting = msg["value"]
                    self._waiting_for_mj = waiting
                    active = getattr(self, "_llm_running", False) and not waiting
                    if hasattr(self, "btn_stop"):
                        self.btn_stop.config(state=tk.NORMAL if active else tk.DISABLED,
                                             bg="#cc0000" if active else "#880000")
                elif action == "npc_turn_tools":
                    self._append_npc_turn_tools(
                        msg["combatant"],
                        msg["monster"],
                        msg["targets"],
                    )
                elif action == "damage_link":
                    self._handle_damage_link(msg)
                elif action == "npc_speak":
                    self._append_npc_speak(msg)
                elif action == "tarokka_speak":
                    self._append_tarokka_speak(msg)
                else:
                    if hasattr(self, "append_message"):
                        self.append_message(msg["sender"], msg["text"], msg["color"])
        except queue.Empty:
            pass
        self.root.after(100, self.process_queue)

    def _append_npc_speak(self, msg: dict):
        import re
        text = msg["text"]
        
        # Remplacer les astérisques de narration du LLM PNJ par des guillemets
        text = re.sub(r'\*{1,2}([^*]+?)\*{1,2}', r'« \1 »', text)
        
        npc_name = msg["sender"]
        color = msg.get("color", "#c77dff")
        
        # 1. Update UI
        if hasattr(self, "append_message"):
            self.append_message(f"🎭 {npc_name}", text, color)
        
        # 2. Feed to agents (bypass if paused)
        if getattr(self, '_session_paused', False):
            return
            
        formatted = f"[{npc_name}] : {text}"

    def _append_tarokka_speak(self, msg: dict):
        text = msg["text"]
        color = msg.get("color", "#9b8fc7")
        
        # 1. Update UI
        if hasattr(self, "append_message"):
            self.append_message("Madam Eva", text, color)
        
        # 2. Audio TTS (si activé)
        try:
            from agent_logger import log_tts_start
            tts_text = strip_mechanical_blocks(text)
            if tts_text:
                log_tts_start("Madam Eva", tts_text)
                self.audio_queue.put((tts_text, "Madam Eva"))
        except Exception:
            pass

        # 3. Feed to agents (bypass if paused)
        if getattr(self, '_session_paused', False):
            return
            
        formatted = f"[Madam Eva] : {text}"
        
        # Interrompt les agents s'ils parlent pour forcer la prophétie, ou l'envoie de suite
        if getattr(self, "_llm_running", False) and not getattr(self, "_waiting_for_mj", False):
            self._pending_interrupt_input = formatted
            self._pending_interrupt_display = None
            if hasattr(self, "_inject_stop"):
                self._inject_stop()
        else:
            self.user_input = formatted
            self.input_event.set()