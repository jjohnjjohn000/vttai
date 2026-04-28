#!/usr/bin/env python3
"""
llm_tester_gui.py — Outil autonome de diagnostic et de benchmark des LLMs.

Ce script lit les clés depuis le fichier .env, teste tous les modèles configurés
dans KNOWN_MODELS, et affiche la latence (ms) ainsi que le statut (OK, 429, Erreurs).
Il inclut un rafraîchissement automatique planifié, un gestionnaire multi-clés,
le calcul du TTFT (Time To First Token) et un test avec un prompt de Roleplay complexe.
"""

import os
import time
import json
import threading
import requests
import tkinter as tk
from tkinter import ttk
from dotenv import load_dotenv

# ─── Configuration des modèles (tirée de app_config.py) ───────────────────────
KNOWN_MODELS =[
    # Ollama
    "ollama/gemma4:e4b", "ollama/gemma4:e2b", "ollama/gemma4:27b",
    "ollama/llama3.3:latest", "ollama/mistral:latest", "ollama/deepseek-r1:8b", "ollama/qwen3.5:9b",
    # Gemini
    "gemini-3.1-flash-lite-preview", "gemini-3-flash-preview", 
    "gemma-4-31b-it", "gemma-4-26b-a4b-it",
    "gemini-2.5-flash", "gemini-2.5-flash-lite",
    # DeepSeek
    "deepseek/deepseek-chat", "deepseek/deepseek-reasoner",
    # Groq
    "groq/meta-llama/llama-4-scout-17b-16e-instruct", "groq/llama-3.3-70b-versatile",
    # OpenRouter
    "openrouter/nousresearch/hermes-3-llama-3.1-70b", "openrouter/nousresearch/hermes-3-llama-3.1-405b",
    "openrouter/mistralai/mistral-small-3.1-24b-instruct",
    "openrouter/meta-llama/llama-3.3-70b-instruct", "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/google/gemma-4-26b-a4b-it:free", "openrouter/minimax/minimax-m2.5:free", "openrouter/inclusionai/ling-2.6-1t:free"
]

# ─── Prompt de Test (Kaelen Roleplay) ─────────────────────────────────────────
KAELEN_PROMPT = """=== SYSTEM MESSAGE (Kaelen) ===


═══════════════════════════════════════════
📜 CONTRAT DE JEU — LIS ATTENTIVEMENT
═══════════════════════════════════════════

1. TON RÔLE (TU N'ES PAS LE MJ)
• Joue UNIQUEMENT ton personnage. Tu connais ton nom, ne parle pas à la 3ème personne.
• Ne décris JAMAIS les actions, paroles ou réactions des PNJ (Van Richten, Ireena, etc.).
• Ne décris JAMAIS l'environnement, les objets découverts ou les conséquences de tes actes.
• Si tu t'adresses à un PNJ, pose ta question en une phrase et arrête-toi net. Le MJ répondra.

2. NARRATION ET SYSTÈME
• Le système (MJ) lance les dés et gère les PV. N'invente jamais un résultat de ton côté.
• Après un[RÉSULTAT SYSTÈME] ou des dégâts reçus, narre UNIQUEMENT ta réaction physique ou mentale (douleur, effort, doute) en 1 ou 2 phrases. Pas de chiffres dans ton roleplay.
• INTERDICTION DE COPIE : Ne paraphrase jamais le message d'un autre joueur. Sois unique.

3. MÉCANIQUES ET SORTS
• Pour lancer un sort ou attaquer, utilise TOUJOURS un bloc [ACTION].
• ⚠️ ANTI-SPAM (RÈGLE ABSOLUE) : Ne lance JAMAIS un sort (détection, buff, etc.) s'il a déjà été lancé récemment et est toujours actif. Le MJ gère les compétences passivement (Perception passive, Investigation passive, etc.) — ne demande PAS de jet toi-même sauf si le MJ t'y invite.
• ⚠️ UPCAST OBLIGATOIRE : Tu DOIS respecter les 'Sorts dispos' affichés dans ton [TOUR EN COURS]. Si tu n'as plus d'emplacement pour le niveau de base d'un sort et que tu veux lancer quand même, tu DOIS le lancer à un niveau supérieur en l'écrivant explicitement (ex: 'Règle 5e: Shield of Faith niv. 3').
• N'appelle pas les outils (update_hp, roll_dice) de ta propre initiative, sauf si une [DIRECTIVE SYSTÈME] te le demande explicitement.

4. FORMAT DE RÉPONSE
• Structure : 1 réplique dialoguée (avec ton attitude incrustée dedans) + 1 bloc [ACTION] UNIQUEMENT si le MJ le demande ou si tu as une action physique délibérée à déclarer.
• N'inclus JAMAIS les en-têtes d'instructions comme[RÈGLES DU BLOC ACTION] ou[RÈGLES DU BLOC ACTION (HORS COMBAT)] dans ta réponse.
• Sois concis : pas de monologues, pas de descriptions entre parenthèses en paragraphe séparé.
• N'utilise [SILENCE] que si tu es physiquement incapable de parler. Sinon, donne au moins une pensée ou une courte réaction.
═══════════════════════════════════════════

Tu es Kaelen, un Paladin Humain de niveau 11, hanté par un serment passé.
PERSONNALITÉ : Tu es économe en mots, fier et grave. Tes préoccupations sont toujours liées à l'honneur, aux serments, à qui mérite protection et à ce qui constitue une cause juste. Quand tu interviens, c'est pour évaluer la valeur morale de la mission ou jurer ta protection. Tu n'es pas curieux des mécaniques — tu veux savoir SI ça vaut le coup de mourir pour ça.

--- CONTEXTE DE LA SCÈNE ACTUELLE ---
📍 Lieu     : Pres du Mur Noir
🕐 Heure    : Apres-midi   |   🌫️ Météo : Brume barovienne habituelle, refoulée par la lueur du phare
👥 PNJs présents : aucun
⚠️ Menaces / Tension : Vous venez de combattre facilement 3 diables a epines, menés par un diable chainé.

--- ÉTAT DE SANTÉ DU GROUPE ---
  🟡 Kaelen : 49% PV[Imposition des mains: 55/55]
  🔴 Elara : 15% PV
  🟢 Thorne : 100% PV
  🔴 Lyra : 32% PV
  🔵 Van Richten (allié) : 77 PV
  🔵 Ezmerelda (allié) : 82 PV

=== CHAT HISTORY ===
[Alexis_Le_MJ]: Tour de table pour vous tous.

[Kaelen]: Je ne me sens pas fier de cet état, mais nous sommes encore debout, et c'est ce qui compte ; Lyra, Elara, votre santé est ma priorité, laissez-moi vous soulager de ces blessures.

[ACTION]
Type      : Action
Intention : Utiliser mon don d'Imposition des mains pour soigner Lyra et Elara.
Règle 5e  : Lay on Hands (Imposition des mains) - Je consomme des points de ma réserve pour soigner Lyra et Elara (priorité à Elara vu son état critique).
Cible     : Lyra et Elara

[Elara]: Mes forces s'étiolent, et chaque mouvement me coûte une énergie que je devrais réserver à l'analyse de cette anomalie...

[Thorne]: Tabarnouche, c'est beau à voir, icitte. Un grand mur noir qui fait peur...[Lyra]: Mes mains tremblent légèrement, et je peine à reprendre mon souffle... Elara, je t'en prie, laisse-moi canaliser la lumière...

[Alexis_Le_MJ]: kaelen tu dois mentionner le nombre de point pour ton imposition des mains.
"""

# ─── Thème sombre ─────────────────────────────────────────────────────────────
BG = "#0d1117"
BG2 = "#161b22"
FG = "#e6edf3"
ACCENT = "#58a6ff"

class LLMTesterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Diagnostic LLM — Moteur de l'Aube Brisée")
        self.root.geometry("1300x800")
        self.root.configure(bg=BG)
        
        self.is_testing = False
        self.auto_refresh_job = None
        self.model_responses = {} # Stocke les réponses complètes pour le popup
        
        # Gestion multi-clés
        self.available_keys = {}
        self.active_keys = {}
        self.display_to_key = {"gemini": {}, "groq": {}, "openrouter": {}}
        
        self.load_keys_from_env()
        self.setup_ui()
        self.populate_tree()
        
    def load_keys_from_env(self):
        """Lit et peuple toutes les clés disponibles depuis le fichier .env."""
        load_dotenv(override=True)
        self.available_keys = {
            "gemini": [], "groq": [], "openrouter": [], "deepseek":[]
        }
        for k, v in sorted(os.environ.items()):
            val = v.strip()
            if not val: continue
            if k.startswith("GEMINI_API_KEY"): self.available_keys["gemini"].append(val)
            elif k.startswith("GROQ_API_KEY"): self.available_keys["groq"].append(val)
            elif k.startswith("OPENROUTER_API_KEY"): self.available_keys["openrouter"].append(val)
            elif k.startswith("DEEPSEEK_API_KEY"): self.available_keys["deepseek"].append(val)
                
        for provider in self.available_keys:
            self.available_keys[provider] = list(dict.fromkeys(self.available_keys[provider]))
            if provider not in self.active_keys:
                self.active_keys[provider] = self.available_keys[provider][0] if self.available_keys[provider] else ""

    def mask_key(self, key):
        if not key: return "Aucune"
        if len(key) <= 8: return "****"
        return f"{key[:4]}...{key[-4:]}"

    def update_key_ui(self):
        for p in["gemini", "groq", "openrouter"]:
            self.display_to_key[p].clear()
            keys = self.available_keys[p]
            display_values =[]
            current_active = self.active_keys.get(p)
            new_idx = 0
            
            for i, k in enumerate(keys):
                disp = f"Clé {i+1} : {self.mask_key(k)}"
                display_values.append(disp)
                self.display_to_key[p][disp] = k
                if k == current_active: new_idx = i
                    
            self.key_combos[p]["values"] = display_values
            if display_values:
                if current_active not in keys: new_idx = 0
                self.key_combos[p].current(new_idx)
                self.active_keys[p] = keys[new_idx]
            else:
                self.key_combos[p].set("Aucune")
                self.active_keys[p] = ""
                
        if "deepseek" not in self.active_keys or self.active_keys["deepseek"] not in self.available_keys["deepseek"]:
            self.active_keys["deepseek"] = self.available_keys["deepseek"][0] if self.available_keys["deepseek"] else ""

    def _on_key_changed(self, provider):
        disp = self.key_vars[provider].get()
        if disp in self.display_to_key[provider]:
            self.active_keys[provider] = self.display_to_key[provider][disp]

    def setup_ui(self):
        # En-tête
        hdr = tk.Frame(self.root, bg="#0a1520", pady=10)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="⚡ Benchmark et Surveillance des LLMs", bg="#0a1520", fg=ACCENT, font=("Arial", 14, "bold")).pack(side=tk.LEFT, padx=20)
        
        # Sélecteur de clés
        key_frame = tk.Frame(self.root, bg=BG)
        key_frame.pack(fill=tk.X, padx=20, pady=(15, 0))
        
        tk.Label(key_frame, text="🔑 Clés actives :", bg=BG, fg="#8b949e", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=(0, 10))
        
        self.key_vars = {}
        self.key_combos = {}
        
        for p in ["gemini", "groq", "openrouter"]:
            tk.Label(key_frame, text=f"{p.capitalize()} :", bg=BG, fg=FG, font=("Arial", 9)).pack(side=tk.LEFT, padx=(5, 2))
            var = tk.StringVar()
            self.key_vars[p] = var
            combo = ttk.Combobox(key_frame, textvariable=var, state="readonly", width=18)
            combo.pack(side=tk.LEFT, padx=(0, 15))
            combo.bind("<<ComboboxSelected>>", lambda e, prov=p: self._on_key_changed(prov))
            self.key_combos[p] = combo
            
        self.update_key_ui()
        
        # Contrôles
        ctrl = tk.Frame(self.root, bg=BG)
        ctrl.pack(fill=tk.X, padx=20, pady=10)
        
        self.btn_run = tk.Button(ctrl, text="▶ Lancer tous les tests", bg="#238636", fg="white", font=("Arial", 10, "bold"), relief="flat", padx=10, command=self.run_all_tests)
        self.btn_run.pack(side=tk.LEFT)
        
        self.var_long_prompt = tk.BooleanVar(value=False)
        self.chk_prompt = tk.Checkbutton(ctrl, text="Test : Prompt Kaelen (Roleplay)", variable=self.var_long_prompt, bg=BG, fg="#d2a8ff", selectcolor=BG2, activebackground=BG, activeforeground=ACCENT, font=("Arial", 10, "bold"))
        self.chk_prompt.pack(side=tk.LEFT, padx=(20, 10))

        self.var_auto = tk.BooleanVar(value=False)
        self.chk_auto = tk.Checkbutton(ctrl, text="Auto-Refresh", variable=self.var_auto, bg=BG, fg=FG, selectcolor=BG2, activebackground=BG, activeforeground=ACCENT, font=("Arial", 10), command=self.toggle_auto_refresh)
        self.chk_auto.pack(side=tk.LEFT, padx=(10, 5))
        
        self.var_interval = tk.IntVar(value=5)
        tk.Label(ctrl, text="toutes les", bg=BG, fg=FG).pack(side=tk.LEFT)
        tk.Spinbox(ctrl, from_=1, to=60, textvariable=self.var_interval, width=3, bg=BG2, fg=ACCENT, font=("Consolas", 10)).pack(side=tk.LEFT, padx=5)
        tk.Label(ctrl, text="min", bg=BG, fg=FG).pack(side=tk.LEFT)
        
        self.lbl_status = tk.Label(ctrl, text="Prêt.", bg=BG, fg="#8b949e", font=("Consolas", 10))
        self.lbl_status.pack(side=tk.RIGHT, padx=10)
        
        # Style Treeview
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", background=BG2, foreground=FG, fieldbackground=BG2, rowheight=25, font=("Consolas", 10), borderwidth=0)
        style.map("Treeview", background=[("selected", "#2a3a50")], foreground=[("selected", "white")])
        style.configure("Treeview.Heading", background="#21262d", foreground="white", font=("Arial", 10, "bold"), relief="flat")
        
        # Tableau
        columns = ("provider", "model", "status", "ttft", "latency", "output", "message")
        self.tree = ttk.Treeview(self.root, columns=columns, show="headings", selectmode="browse")
        
        self.tree.heading("provider", text="Fournisseur")
        self.tree.heading("model", text="Modèle")
        self.tree.heading("status", text="Statut")
        self.tree.heading("ttft", text="TTFT (ms)")
        self.tree.heading("latency", text="Total (ms)")
        self.tree.heading("output", text="Aperçu Réponse")
        self.tree.heading("message", text="Détails API")
        
        self.tree.column("provider", width=90, anchor="center")
        self.tree.column("model", width=250, anchor="w")
        self.tree.column("status", width=70, anchor="center")
        self.tree.column("ttft", width=80, anchor="center")
        self.tree.column("latency", width=80, anchor="center")
        self.tree.column("output", width=350, anchor="w")
        self.tree.column("message", width=250, anchor="w")
        
        self.tree.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))
        
        # Bind double-clic pour afficher la réponse complète
        self.tree.bind("<Double-1>", self.on_row_double_click)
        
        # Tags pour couleurs
        self.tree.tag_configure("OK", foreground="#3fb950")
        self.tree.tag_configure("WARN", foreground="#d29922")
        self.tree.tag_configure("ERR", foreground="#f85149")
        self.tree.tag_configure("OFF", foreground="#8b949e")

    def populate_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        for model in KNOWN_MODELS:
            provider = "Gemini"
            if model.startswith("groq/"): provider = "Groq"
            elif model.startswith("openrouter/"): provider = "OpenRouter"
            elif model.startswith("deepseek/"): provider = "DeepSeek"
            elif model.startswith("ollama/"): provider = "Ollama"
            
            clean_model = model.split("/", 1)[-1] if "/" in model else model
            self.tree.insert("", "end", iid=model, values=(provider, clean_model, "—", "—", "—", "—", "En attente..."), tags=("OFF",))

    def toggle_auto_refresh(self):
        if self.var_auto.get():
            self.schedule_next_run()
        else:
            if self.auto_refresh_job:
                self.root.after_cancel(self.auto_refresh_job)
                self.auto_refresh_job = None
                self.lbl_status.config(text="Auto-refresh désactivé.")

    def schedule_next_run(self):
        if self.var_auto.get() and not self.is_testing:
            minutes = self.var_interval.get()
            self.lbl_status.config(text=f"Prochain test dans {minutes} min...")
            self.auto_refresh_job = self.root.after(minutes * 60000, self.run_all_tests)

    def run_all_tests(self):
        if self.is_testing: return
        
        self.is_testing = True
        self.btn_run.config(state=tk.DISABLED, text="Test en cours...")
        self.model_responses.clear()
        
        self.load_keys_from_env() 
        self.update_key_ui()
        
        for model in KNOWN_MODELS:
            v = self.tree.item(model, "values")
            self.tree.item(model, values=(v[0], v[1], "...", "...", "...", "...", "Test en cours..."), tags=("OFF",))
        
        threading.Thread(target=self._worker_tests, daemon=True).start()

    def _worker_tests(self):
        threads =[]
        for model in KNOWN_MODELS:
            t = threading.Thread(target=self._ping_model, args=(model,))
            threads.append(t)
            t.start()
            time.sleep(0.1) # Léger délai pour éviter un ratelimit massif instantané
            
        for t in threads:
            t.join()
            
        self.root.after(0, self._on_tests_complete)

    def _on_tests_complete(self):
        self.is_testing = False
        self.btn_run.config(state=tk.NORMAL, text="▶ Lancer tous les tests")
        self.lbl_status.config(text=f"Dernier test : {time.strftime('%H:%M:%S')}")
        self.schedule_next_run()

    def _update_row(self, model, status, ttft_ms, latency_ms, output, message, tag):
        """Stocke la donnée complète et met à jour l'interface graphique."""
        self.model_responses[model] = {
            "status": status,
            "ttft": ttft_ms,
            "latency": latency_ms,
            "output": output,
            "message": message
        }
        
        # Formatage pour le tableau
        ttft_str = f"{ttft_ms}" if ttft_ms else "—"
        lat_str = f"{latency_ms}" if latency_ms else "—"
        out_preview = (output[:60] + "...") if len(output) > 60 else output
        if not output: out_preview = "—"
        
        def callback():
            v = self.tree.item(model, "values")
            self.tree.item(model, values=(v[0], v[1], status, ttft_str, lat_str, out_preview.replace('\n', ' '), message), tags=(tag,))
        self.root.after(0, callback)

    def _ping_model(self, model_id):
        # Configuration des promtps
        use_long = self.var_long_prompt.get()
        payload = {
            "model": model_id.split("/", 1)[-1] if "/" in model_id else model_id,
            "stream": True # Requis pour calculer le TTFT (Time To First Token)
        }
        
        if use_long:
            payload["messages"] =[{"role": "user", "content": KAELEN_PROMPT}]
            payload["max_tokens"] = 800
        else:
            payload["messages"] =[{"role": "user", "content": "Respond with 'OK'."}]
            payload["max_tokens"] = 10
            
        url = ""
        key = ""
        timeout = (10.0, 120.0) # (connexion, lecture flux)

        if model_id.startswith("groq/"):
            url = "https://api.groq.com/openai/v1/chat/completions"
            key = self.active_keys.get("groq")
        elif model_id.startswith("openrouter/"):
            url = "https://openrouter.ai/api/v1/chat/completions"
            key = self.active_keys.get("openrouter")
        elif model_id.startswith("deepseek/"):
            url = "https://api.deepseek.com/chat/completions"
            key = self.active_keys.get("deepseek")
        elif model_id.startswith("ollama/"):
            url = "http://localhost:11434/v1/chat/completions"
            key = "ollama"
        else:
            url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
            key = self.active_keys.get("gemini")

        if not key:
            self._update_row(model_id, "NO KEY", None, None, "", "Clé API absente", "OFF")
            return

        headers = {"Content-Type": "application/json"}
        if key != "ollama":
            headers["Authorization"] = f"Bearer {key}"

        t0 = time.perf_counter()
        ttft = None
        full_output = ""
        
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout, stream=True)
            
            if resp.status_code == 200:
                # Lecture du flux SSE (Server-Sent Events)
                for line in resp.iter_lines():
                    if line:
                        decoded = line.decode('utf-8')
                        if decoded.startswith("data: "):
                            data_str = decoded[6:]
                            if data_str.strip() == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_str)
                                choices = chunk.get("choices",[])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    content = delta.get("content", "") or ""
                                    reasoning = delta.get("reasoning_content", "") or "" # Pour DeepSeek Reasoner
                                    text_chunk = reasoning + content
                                    
                                    if text_chunk:
                                        if ttft is None:
                                            ttft = int((time.perf_counter() - t0) * 1000)
                                        full_output += text_chunk
                            except json.JSONDecodeError:
                                pass
                                
                total_ms = int((time.perf_counter() - t0) * 1000)
                if ttft is None: ttft = total_ms # Fallback si pas de streaming par morceaux
                
                self._update_row(model_id, "200 OK", ttft, total_ms, full_output.strip(), "Succès.", "OK")
                
            elif resp.status_code == 429:
                total_ms = int((time.perf_counter() - t0) * 1000)
                self._update_row(model_id, "429", None, total_ms, "", "Rate Limit / Quota épuisé.", "WARN")
            elif resp.status_code in (401, 403):
                self._update_row(model_id, str(resp.status_code), None, None, "", "Clé invalide.", "ERR")
            else:
                try: err = resp.json().get("error", {}).get("message", resp.text[:100])
                except: err = resp.text[:100]
                self._update_row(model_id, str(resp.status_code), None, None, "", err.replace('\n', ' '), "ERR")
                
        except requests.exceptions.Timeout:
            self._update_row(model_id, "TIMEOUT", None, None, "", f"Temps écoulé (> {timeout[1]}s)", "ERR")
        except requests.exceptions.ConnectionError:
            if key == "ollama":
                self._update_row(model_id, "OFFLINE", None, None, "", "Ollama n'est pas lancé.", "OFF")
            else:
                self._update_row(model_id, "CONN ERR", None, None, "", "Erreur de connexion.", "ERR")
        except Exception as e:
            self._update_row(model_id, "ERROR", None, None, "", str(e), "ERR")

    def on_row_double_click(self, event):
        """Affiche un popup contenant la réponse complète du LLM."""
        selected = self.tree.selection()
        if not selected: return
        model_id = selected[0]
        
        data = self.model_responses.get(model_id)
        if not data or not data["output"]:
            return # Ne rien ouvrir s'il n'y a pas de réponse valide

        top = tk.Toplevel(self.root)
        top.title(f"Réponse complète — {model_id}")
        top.geometry("850x600")
        top.configure(bg=BG)
        
        # En-tête statistiques
        ttft = data.get("ttft", "—")
        lat = data.get("latency", "—")
        lbl_stats = tk.Label(
            top, 
            text=f"Modèle : {model_id}\n\n⏱️ Réflexion (TTFT) : {ttft} ms    |    🏁 Total Génération : {lat} ms", 
            bg=BG, fg=ACCENT, font=("Arial", 11, "bold")
        )
        lbl_stats.pack(pady=15)
        
        # Zone de texte
        frame_text = tk.Frame(top, bg=BG2)
        frame_text.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))
        
        scrollbar = tk.Scrollbar(frame_text)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        text_area = tk.Text(frame_text, bg=BG2, fg=FG, font=("Consolas", 11), wrap=tk.WORD, yscrollcommand=scrollbar.set, relief="flat", padx=10, pady=10)
        text_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=text_area.yview)
        
        text_area.insert(tk.END, data["output"])
        text_area.config(state=tk.DISABLED) # Lecture seule

if __name__ == "__main__":
    root = tk.Tk()
    app = LLMTesterApp(root)
    root.mainloop()