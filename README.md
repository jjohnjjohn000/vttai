# 🎲 Moteur de l'Aube Brisée

> **Un moteur de campagne D&D 5e propulsé par l'IA** — quatre agents joueurs incarnés par des LLMs, un MJ humain, et une table virtuelle complète avec combat, voix, cartes et mémoire narrative.

---

## Présentation

Le **Moteur de l'Aube Brisée** est une application de bureau (Tkinter) conçue pour animer une campagne D&D 5e en solo. Le MJ (vous) dirige la narration ; quatre personnages-joueurs sont incarnés en temps réel par des LLMs via [AutoGen](https://github.com/microsoft/autogen) :

| Personnage | Modèle par défaut | Rôle |
|---|---|---|
| **Kaelen** | Gemini 2.5 Pro | — |
| **Elara** | Gemini 2.5 Pro | — |
| **Thorne** | Groq / Llama 4 Scout | — |
| **Lyra** | Gemini 2.5 Pro | — |

Un cinquième agent, le **Chroniqueur**, génère automatiquement le résumé de chaque session et tient le journal de campagne à jour.

---

## Fonctionnalités

### Moteur narrative
- **Multi-agents AutoGen** : les quatre PJ dialoguent entre eux et avec le MJ dans un GroupChat orchestré.
- **Système de mémoire** : mémoires persistantes classées par importance, injectées contextuellement selon les tags de la scène active.
- **Journal de session** : résumé automatique généré par le Chroniqueur IA à chaque fin de session.
- **Journal de quêtes** : suivi des quêtes actives, complétées et échouées.
- **Calendrier narratif** : avancement des jours, phases lunaires, mois baroviens.

### Combat D&D 5e
- **Tracker de combat** : initiative, PV, conditions, concentration — avec callbacks automatiques vers les agents lors du tour d'un PJ.
- **Carte de combat** : carte tactique avec placement des tokens, notes du MJ (post-its), pointeur interactif envoyé aux agents.
- **Simulateur de combat** : résolution mécanique des actions (attaques, sorts, sauvegardes) avec jets de dés intégrés.
- **Gestion des sorts** : données complètes D&D 5e (SRD), slots de sort par niveau, récupération courte/longue.

### Voix (TTS)
- **edge-tts** (en ligne) : voix neurales Microsoft, support fr-CA, accent québécois.
- **Piper** (hors-ligne) : modèles `.onnx` locaux, pitch configurable par personnage.
- Chaque PJ et PNJ a sa propre voix et ses propres paramètres de vitesse/pitch.
- Pause/reprise globale de l'audio, vidage de file d'attente à la pause.

### Vision multimodale (Gemini)
- **Image de lieu** : envoyez une illustration à tous les agents Gemini — chaque PJ décrit ce que son personnage perçoit.
- **Pointeur MJ** : cliquez sur la carte de combat pour envoyer une capture annotée aux agents ; Thorne (Groq, sans vision) reçoit une description textuelle.

### Gestion de session
- **Pause/Reprise** : stoppe l'audio, interrompt les LLMs, préserve l'historique AutoGen.
- **Fin de session** : résumé Chroniqueur → archivage → réinitialisation du chat sans fermer l'app.
- **Sauvegarde rapide** : résumé intermédiaire en un clic.

### Interface
- **Popouts de personnage** : fiche complète (stats, PV, sorts, conditions, équipement) pour chaque PJ.
- **Avatars animés** : bulle de pensée visible pendant la génération LLM.
- **Panneau NPC/Bestiaire** : création, édition, voix personnalisées, conversion en combatant.
- **Panneau de configuration** : modèles LLM, température, voix, mémoires — sans redémarrage.
- **Persistance de géométrie** : toutes les fenêtres se souviennent de leur position/taille.

---

## Architecture

```
main.py                   # Point d'entrée, classe DnDApp (composition par mixins)
│
├── autogen_engine.py     # Moteur AutoGen : agents, GroupChat, interception des messages
├── llm_config.py         # Routeur LLM multi-fournisseurs + fallback automatique
├── app_config.py         # Configuration persistante (app_config.json)
├── state_manager.py      # Persistance campagne (campaign_state.json) + jets de dés
│
├── Mixins UI / Métier
│   ├── ui_setup_mixin.py         # Construction de l'interface Tkinter
│   ├── chat_mixin.py             # Affichage et polling du chat
│   ├── character_mixin.py        # Popouts des personnages, stats, sorts
│   ├── panels_mixin.py           # Panneaux scène, quêtes, mémoires, calendrier, images
│   ├── llm_control_mixin.py      # Stop LLM, envoi texte MJ, votes, jets de compétence
│   ├── session_mixin.py          # Cycle de vie des sessions (save, end, reset)
│   ├── session_pause_mixin.py    # Pause / Reprise globale
│   ├── combat_tracker_mixin.py   # Intégration du tracker de combat
│   └── image_broadcast_mixin.py  # Diffusion multimodale des images
│
├── combat_tracker.py     # Fenêtre de suivi du combat D&D 5e
├── combat_map_panel.py   # Carte de combat interactive
├── combat_simulator.py   # Résolution mécanique des actions
├── npc_bestiary_panel.py # Gestionnaire de PNJs et bestiaire
├── config_panel.py       # Panneau de configuration UI
├── character_faces.py    # Avatars des personnages avec bulles de pensée
├── spell_data.py         # Données de sorts D&D 5e (SRD)
│
├── voice_interface.py    # Interface TTS unifiée (edge-tts / Piper)
├── piper_tts.py          # Backend Piper (local, hors-ligne)
├── chat_log_writer.py    # Journal narratif de session (.log)
├── agent_logger.py       # Logs terminal des agents (timing LLM/TTS)
├── window_state.py       # Persistance de la géométrie des fenêtres
└── tk_widgets.py         # Patches Tk (SafeButton, SafeLabel — fix segfault emoji)
```

### Fournisseurs LLM supportés

Le routeur `llm_config.py` unifie tous les fournisseurs derrière l'API OpenAI-compatible et gère le **fallback automatique** en cas de quota épuisé :

```
Modèle demandé → Gemini 2.5 Flash → Groq Llama → OpenRouter (gratuit)
```

| Préfixe dans la config | Fournisseur | Variable d'environnement |
|---|---|---|
| `gemini-*` | Google Gemini | `GEMINI_API_KEY` |
| `groq/*` | Groq | `GROQ_API_KEY` |
| `openrouter/*` | OpenRouter | `OPENROUTER_API_KEY` |

---

## Installation

### Prérequis

- Python 3.10+
- Linux (testé sur Ubuntu 22.04) — Windows/macOS non testés
- `libX11` (pour le fix XInitThreads)

### Dépendances Python

```bash
pip install pyautogen python-dotenv pillow edge-tts piper-tts
```

> **Note** : `piper-tts` est optionnel si vous utilisez exclusivement `edge-tts`.

### Clés API

Créez un fichier `.env` à la racine :

```env
GEMINI_API_KEY=votre_clé_gemini
GROQ_API_KEY=votre_clé_groq
OPENROUTER_API_KEY=votre_clé_openrouter   # optionnel
```

Au moins une clé est requise. Groq propose un tier gratuit généreux — idéal pour Thorne ou en fallback.

### Modèles Piper (optionnel)

Si vous utilisez le backend TTS local, téléchargez les modèles `.onnx` dans `piper_models/` :

```bash
mkdir piper_models
# Exemple : voix fr_FR-siwis-medium
wget -P piper_models https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx
wget -P piper_models https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx.json
```

---

## Démarrage

```bash
python main.py
```

Au premier lancement, un fichier `campaign_state.json` vide est créé. Configurez vos personnages via le panneau **⚙️ Config** avant de lancer la partie.

---

## Configuration

Tout est éditable via le panneau **⚙️ Config** intégré, ou directement dans `app_config.json` :

```json
{
  "agents": {
    "Kaelen": { "model": "gemini-2.5-pro", "temperature": 0.7 }
  },
  "chronicler": {
    "model": "gemini-2.5-flash",
    "temperature": 0.3
  },
  "voice": {
    "enabled": true,
    "backend": "piper"
  },
  "campaign_name": "aube_brisee"
}
```

Les sauvegardes de campagne sont stockées dans `campagne/<campaign_name>/`.

---

## Notes techniques

- **Thread safety** : un verrou global `_SSL_LOCK` sérialise tous les appels réseau LLM pour éviter les segfaults OpenSSL sur Python 3.10 / Linux multi-threads.
- **Fix Tk emoji** : `tk_widgets.py` remplace les emoji hors-BMP dans `tk.Button` pour éviter les crashes Xft sur Tk 8.6 / Ubuntu.
- **Fix gRPC/Tk** : `GRPC_POLL_STRATEGY=epoll1` et import différé d'`autogen` (après `Tk.mainloop()`) pour éviter les races sur le Display Xlib.
- **Injection StopLLMRequested** : l'interruption du thread AutoGen se fait via `ctypes.pythonapi.PyThreadState_SetAsyncExc` — pas de `threading.Event` perdu, historique préservé.

---

## Licence

Ce projet est distribué à des fins personnelles et éducatives. Les données de sorts sont issues du [SRD 5.1](https://media.wizards.com/2016/downloads/DND/SRD-OGL_V5.1.pdf) sous licence OGL v1.0a.
