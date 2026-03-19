"""
llm_config.py — Routeur LLM multi-fournisseurs, constantes D&D 5e, exception autogen.

Préfixes reconnus dans le champ "llm" de campaign_state.json :
  gemini-*               → Google Gemini  (GEMINI_API_KEY)
  groq/*                 → Groq            (GROQ_API_KEY)    gratuit, très rapide
  openrouter/*           → OpenRouter      (OPENROUTER_API_KEY) modèles :free disponibles

Exemples de valeurs :
  "gemini-2.5-pro"
  "gemini-2.5-flash"
  "groq/llama-3.3-70b-versatile"
  "openrouter/meta-llama/llama-3.3-70b-instruct:free"
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Endpoint OpenAI-compatible de Google Gemini.
# CRITIQUE : AutoGen's config_list fallback ne fonctionne QUE pour les erreurs OpenAI-style.
# En utilisant api_type="google", les erreurs 429 Gemini ne déclenchent PAS le fallback.
# Solution : utiliser l'endpoint OpenAI-compatible de Gemini pour que le retry marche vraiment.
_GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"


def build_llm_config(model_name: str, temperature: float = 0.4) -> dict:
    """
    Construit le llm_config AutoGen avec un système de fallback automatique.

    Ordre de fallback (après le modèle principal demandé) :
      1. gemini-3.1-pro-preview
      2. gemini-3.1-flash-lite-preview
      3. gemini-2.5-pro
      4. groq/meta-llama/llama-4-scout-17b-16e-instruct
      5. gemini-2.5-flash
      6. OpenRouter (llama + arcee trinity — fallbacks JDR-friendly)

    NOTE IMPORTANTE : Tous les modèles Gemini utilisent l'endpoint OpenAI-compatible
    de Google afin que le mécanisme de retry config_list d'AutoGen se déclenche
    correctement sur les erreurs 429 RESOURCE_EXHAUSTED.
    """
    m = model_name.strip()
    config_list = []

    gemini_key = os.getenv("GEMINI_API_KEY", "")
    groq_key   = os.getenv("GROQ_API_KEY", "")
    router_key = os.getenv("OPENROUTER_API_KEY", "")

    def _gemini(model: str) -> dict:
        return {
            "model":    model,
            "api_key":  gemini_key,
            "base_url": _GEMINI_OPENAI_BASE,
            "api_type": "openai",
        }

    def _groq(model: str) -> dict:
        return {
            "model":    model,
            "api_key":  groq_key,
            "base_url": "https://api.groq.com/openai/v1",
            "api_type": "openai",
        }

    def _openrouter(model: str) -> dict:
        return {
            "model":    model,
            "api_key":  router_key,
            "base_url": "https://openrouter.ai/api/v1",
            "api_type": "openai",
            "default_headers": {
                "HTTP-Referer": "https://dnd-moteur-aube-brisee",
                "X-Title":      "Moteur de l Aube Brisee",
            },
        }

    # ── 1. Modèle principal demandé ───────────────────────────────────────────
    if m.startswith("groq/"):
        if groq_key:
            config_list.append(_groq(m[len("groq/"):]))
    elif m.startswith("openrouter/"):
        if router_key:
            config_list.append(_openrouter(m[len("openrouter/"):]))
    else:  # Gemini (ex: "gemini-2.5-pro", "gemini-3.1-pro-preview"…)
        if gemini_key:
            config_list.append(_gemini(m))

    # ── Fallbacks : comportement différent selon le fournisseur primaire ──────
    #
    # RÈGLE : quand le modèle principal est Groq, on N'AJOUTE PAS de fallbacks
    # Gemini/OpenRouter. AutoGen cache le dernier index de config_list ayant
    # réussi (comportement "sticky") — une seule erreur transitoire Groq suffit
    # à faire basculer silencieusement TOUS les appels suivants vers Gemini.
    # Pour les agents Groq on préfère un vrai échec visible plutôt qu'un switch
    # invisible de fournisseur.
    #
    # Pour les modèles Gemini/OpenRouter, on conserve la chaîne de fallback
    # complète (même fournisseur ou équivalent).

    if m.startswith("groq/"):
        # Pas de fallback Groq secondaire.
        # llama-3.3-70b-versatile (free tier) n'a que 12 000 TPM vs ~30 000 pour
        # llama-4-scout. Quand le contexte du groupchat grossit, ce fallback
        # échoue systématiquement avec HTTP 413 — pire que de ne pas en avoir.
        # Un vrai échec visible vaut mieux qu'un fallback silencieux sous-capacitaire.
        pass

    elif m.startswith("openrouter/"):
        # Fallbacks OpenRouter uniquement (rester dans le même fournisseur).
        if router_key:
            config_list.append(_openrouter("meta-llama/llama-3.3-70b-instruct:free"))
            config_list.append(_openrouter("mistralai/mistral-small-3.1-24b-instruct:free"))
            config_list.append(_openrouter("arcee-ai/trinity-large-preview:free"))

    else:
        # Modèle Gemini : chaîne de fallback complète.
        _GEMINI_FALLBACK_ORDER = [
            "gemini-3.1-pro-preview",
            "gemini-3.1-flash-lite-preview",
            "gemini-2.5-pro",
        ]
        if gemini_key:
            for fb in _GEMINI_FALLBACK_ORDER:
                if m != fb:
                    config_list.append(_gemini(fb))

        # Fallback Groq inter-fournisseur (si Groq disponible)
        if groq_key:
            config_list.append(_groq("meta-llama/llama-4-scout-17b-16e-instruct"))

        # Dernier recours Gemini Flash
        if gemini_key and m != "gemini-2.5-flash":
            config_list.append(_gemini("gemini-2.5-flash"))

        # Fallbacks OpenRouter en ultime recours
        if router_key:
            config_list.append(_openrouter("meta-llama/llama-3.3-70b-instruct:free"))
            config_list.append(_openrouter("mistralai/mistral-small-3.1-24b-instruct:free"))
            config_list.append(_openrouter("arcee-ai/trinity-large-preview:free"))

    # ── Sécurité : au cas où aucune clé n'est configurée ─────────────────────
    if not config_list:
        config_list.append({
            "model":    m,
            "api_key":  "DUMMY_KEY",
            "base_url": _GEMINI_OPENAI_BASE,
            "api_type": "openai",
        })

    print("🛠️ DEBUG CONFIG LLM:", [c.get("model") for c in config_list])

    return {
        "config_list": config_list,
        "temperature":  temperature,
    }


# Config par défaut (utilisée pour le résumé de session et le GroupChatManager)
_default_model = os.getenv("DEFAULT_LLM_MODEL", "gemini-2.5-pro")
llm_config = build_llm_config(_default_model)


# ─── Exception pour interrompre proprement le thread autogen ─────────────────
class StopLLMRequested(BaseException):
    """Injectée via ctypes dans le thread autogen pour l'interrompre proprement."""
    pass


# ─── Compétences D&D 5e classées par caractéristique ─────────────────────────
DND_SKILLS = {
    "Force":        [("Athlétisme", "STR")],
    "Dextérité":    [("Acrobaties", "DEX"), ("Escamotage", "DEX"), ("Discrétion", "DEX")],
    "Constitution": [],
    "Intelligence": [("Arcanes", "INT"), ("Histoire", "INT"), ("Investigation", "INT"),
                     ("Nature", "INT"), ("Religion", "INT")],
    "Sagesse":      [("Dressage", "WIS"), ("Perspicacité", "WIS"), ("Médecine", "WIS"),
                     ("Perception", "WIS"), ("Survie", "WIS")],
    "Charisme":     [("Tromperie", "CHA"), ("Intimidation", "CHA"),
                     ("Représentation", "CHA"), ("Persuasion", "CHA")],
}

ABILITY_COLORS = {
    "Force":        "#e57373",
    "Dextérité":    "#81c784",
    "Constitution": "#ffb74d",
    "Intelligence": "#64b5f6",
    "Sagesse":      "#ce93d8",
    "Charisme":     "#f06292",
}


# ─── Verrou global SSL/httpx ──────────────────────────────────────────────────
# Python 3.10 / Linux : OpenSSL n'est pas thread-safe quand plusieurs threads
# partagent le même pool de connexions httpx (segfault dans ssl.py:read).
# Ce verrou UNIQUE est importé par autogen_engine.py ET llm_control_mixin.py
# pour sérialiser TOUS les appels au réseau LLM, qu'ils viennent du groupchat
# autogen, des messages privés MJ ou des votes.
# Impact perf : négligeable (autogen est déjà séquentiel par agent).
import threading as _threading_ssl
_SSL_LOCK = _threading_ssl.Lock()