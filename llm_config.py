"""
llm_config.py — Routeur LLM multi-fournisseurs, constantes D&D 5e, exception autogen.

Préfixes reconnus dans le champ "llm" de campaign_state.json :
  gemini-*               → Google Gemini  (GEMINI_API_KEY)
  groq/*                 → Groq            (GROQ_API_KEY)    gratuit, très rapide
  openrouter/*           → OpenRouter      (OPENROUTER_API_KEY) modèles :free disponibles
  deepseek/*             → DeepSeek direct (DEEPSEEK_API_KEY)  pas de frais OpenRouter

Exemples de valeurs :
  "gemini-2.5-pro"
  "gemini-2.5-flash"
  "groq/llama-3.3-70b-versatile"
  "openrouter/meta-llama/llama-3.3-70b-instruct:free"
  "deepseek/deepseek-chat"      ← DeepSeek V3.2, supporte tool calls
  "deepseek/deepseek-reasoner"  ← DeepSeek V3.2 mode thinking (pas de temperature)
"""

import os
import httpx
from dotenv import load_dotenv

load_dotenv()


class _NoKeepaliveHttpClient(httpx.Client):
    """
    Sous-classe de httpx.Client sans keepalive.

    AutoGen appelle copy.deepcopy(llm_config) à l'initialisation de chaque agent.
    httpx.Client contient un _thread.RLock qui n'est pas picklable → TypeError.
    __deepcopy__ crée un nouveau client isolé à chaque copie, ce qui est exactement
    le comportement souhaité : chaque agent/thread obtient son propre pool de connexions.
    Doit hériter de httpx.Client pour passer la validation pydantic d'AutoGen.

    max_keepalive_connections=0 : pas de connexions SSL persistentes partagées entre
    threads → élimine la cause du segfault OpenSSL.
    """

    def __init__(self, **kwargs):
        # On ignore les kwargs lors de la copie pour forcer NOS limites
        super().__init__(
            limits=httpx.Limits(
                max_keepalive_connections=0,
                max_connections=10,
            ),
            timeout=httpx.Timeout(120.0),
        )

    def __deepcopy__(self, memo):
        # Crée un client frais à chaque copie — jamais partagé entre threads
        new = _NoKeepaliveHttpClient()
        memo[id(self)] = new
        return new


def _make_no_keepalive_http_client() -> httpx.Client:
    """Retourne un httpx.Client deepcopy-safe sans keepalive."""
    return _NoKeepaliveHttpClient()


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
            "model":       model,
            "api_key":     gemini_key,
            "base_url":    _GEMINI_OPENAI_BASE,
            "api_type":    "openai",
            "http_client": _make_no_keepalive_http_client(),
        }

    def _groq(model: str) -> dict:
        return {
            "model":       model,
            "api_key":     groq_key,
            "base_url":    "https://api.groq.com/openai/v1",
            "api_type":    "openai",
            "http_client": _make_no_keepalive_http_client(),
        }

    def _deepseek(model: str) -> dict:
        # deepseek-reasoner ne supporte pas temperature — AutoGen l'ignore silencieusement
        # mais on le note ici pour clarté.
        return {
            "model":       model,
            "api_key":     os.getenv("DEEPSEEK_API_KEY", ""),
            "base_url":    "https://api.deepseek.com",
            "api_type":    "openai",
            "http_client": _make_no_keepalive_http_client(),
        }

    def _openrouter(model: str) -> dict:
        return {
            "model":       model,
            "api_key":     router_key,
            "base_url":    "https://openrouter.ai/api/v1",
            "api_type":    "openai",
            "default_headers": {
                "HTTP-Referer": "https://dnd-moteur-aube-brisee",
                "X-Title":      "Moteur de l Aube Brisee",
            },
            "http_client": _make_no_keepalive_http_client(),
        }

    # ── 1. Modèle principal demandé ───────────────────────────────────────────
    if m.startswith("groq/"):
        if groq_key:
            config_list.append(_groq(m[len("groq/"):]))
    elif m.startswith("openrouter/"):
        if router_key:
            config_list.append(_openrouter(m[len("openrouter/"):]))
    elif m.startswith("deepseek/"):
        deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        if deepseek_key:
            config_list.append(_deepseek(m[len("deepseek/"):]))
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
        pass  # pas de fallback Groq

    elif m.startswith("openrouter/"):
        pass  # pas de fallback OpenRouter

    elif m.startswith("deepseek/"):
        # Pas de fallback DeepSeek — même raisonnement que Groq/OpenRouter :
        # un fallback silencieux vers un autre fournisseur est pire qu'un vrai échec visible.
        pass

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
#
# STRATÉGIE DOUBLE :
#   1. Chaque entrée config_list reçoit son propre httpx.Client(keepalive=0)
#      → pas de pool de connexions persistent partagé entre threads.
#   2. _SSL_LOCK sérialise les appels réseau pour les appels directs
#      (messages privés, votes) où plusieurs threads pourraient coexister.
#
# max_keepalive_connections=0 : httpx ferme chaque connexion SSL après usage,
# empêchant les routines de cleanup keep-alive de s'exécuter depuis un thread
# différent de celui qui a ouvert la connexion (cause réelle du segfault).
import threading as _threading_ssl
_SSL_LOCK = _threading_ssl.Lock()


# ─── OpenRouter : interrogation du solde et rate limits ───────────────────────

def fetch_openrouter_key_status() -> dict | None:
    """
    Interroge GET /api/v1/key sur OpenRouter et retourne les données brutes,
    ou None si la clé est absente ou que la requête échoue.

    Utilise requests (léger, sans httpx) pour éviter les conflits de pool SSL.
    Timeout court (5 s) — appelé en arrière-plan, ne doit pas bloquer.
    """
    key = os.getenv("OPENROUTER_API_KEY", "")
    if not key:
        return None
    try:
        import requests as _req
        r = _req.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": f"Bearer {key}"},
            timeout=5,
        )
        if r.status_code == 200:
            return r.json().get("data", {})
    except Exception:
        pass
    return None


def format_openrouter_status(data: dict) -> str:
    """
    Formate les données clé OpenRouter en une ligne lisible pour le terminal.

    Exemple :
      💳 OpenRouter  credits: 4.82 $ restants (utilisé: 0.18 $ aujourd'hui)
                     free: 312/1000 req/jour  |  is_free_tier: False
    """
    if not data:
        return ""

    lines = []

    # ── Crédits ───────────────────────────────────────────────────────────────
    limit_rem = data.get("limit_remaining")
    usage_day = data.get("usage_daily", 0)

    if limit_rem is not None:
        lines.append(f"crédits restants : {limit_rem:.4f} $  |  utilisé aujourd'hui : {usage_day:.4f} $")
    else:
        lines.append(f"crédits : illimités  |  utilisé aujourd'hui : {usage_day:.4f} $")

    # ── Tier gratuit ──────────────────────────────────────────────────────────
    is_free = data.get("is_free_tier", True)
    # OpenRouter : 50 req/jour si < 10 $ achetés, 1000 req/jour sinon
    free_daily_limit = 50 if is_free else 1000
    # usage_daily est en crédits ($), pas en nombre de requêtes — OR ne fournit
    # pas directement le compteur de requêtes :free dans cet endpoint.
    # On affiche la limite applicable et le tier.
    tier_label = "free tier (< 10 $ achetés)"  if is_free else "paid tier (≥ 10 $ achetés)"
    lines.append(f"modèles :free — limite : {free_daily_limit} req/jour  |  tier : {tier_label}")

    return "\n                     ".join(lines)