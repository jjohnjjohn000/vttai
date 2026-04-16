"""
llm_config.py — Routeur LLM multi-fournisseurs, constantes D&D 5e, exception autogen.

Préfixes reconnus dans le champ "llm" de campaign_state.json :
  gemini-*               → Google Gemini  (GEMINI_API_KEY)
  groq/*                 → Groq            (GROQ_API_KEY)    gratuit, très rapide
  openrouter/*           → OpenRouter      (OPENROUTER_API_KEY) modèles :free disponibles
  deepseek/*             → DeepSeek direct (DEEPSEEK_API_KEY)  pas de frais OpenRouter
  ollama/*               → Ollama local    (aucune clé requise, localhost:11434)

Exemples de valeurs :
  "gemini-2.5-pro"
  "gemini-2.5-flash"
  "groq/llama-3.3-70b-versatile"
  "openrouter/meta-llama/llama-3.3-70b-instruct:free"
  "deepseek/deepseek-chat"      ← DeepSeek V3.2, supporte tool calls
  "deepseek/deepseek-reasoner"  ← DeepSeek V3.2 mode thinking (pas de temperature)
  "ollama/gemma4:e4b"           ← Gemma 4 local via Ollama (RX 6700 XT, 12 GB VRAM)
  "ollama/gemma4:e2b"           ← Gemma 4 edge local (ultra-léger)

Notes Ollama :
  • Ollama expose une API OpenAI-compatible sur http://localhost:11434/v1
  • La clé API peut être n'importe quelle chaîne non-vide (Ollama l'ignore)
  • Pour les modèles Ollama, il n'y a PAS de fallback automatique vers les
    fournisseurs cloud — Ollama est intentionnellement isolé (usage offline,
    confidentialité, coût zéro).
  • OLLAMA_HOST peut remplacer localhost si Ollama tourne sur une autre machine.
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

# Endpoint Ollama local (OpenAI-compatible).
# Peut être surchargé via la variable d'environnement OLLAMA_HOST.
# Exemple : OLLAMA_HOST=http://192.168.1.50:11434 pour une machine distante.
_OLLAMA_BASE = os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/v1"


def build_llm_config(model_name: str, temperature: float = 0.4) -> dict:
    """
    Construit le llm_config AutoGen avec un système de fallback automatique.

    Rotation multi-comptes Gemini :
      Pour chaque modèle Gemini dans la chaîne, AutoGen essaie toutes les clés
      disponibles (GEMINI_API_KEY, GEMINI_API_KEY_1, GEMINI_API_KEY_2…) avant
      de passer au modèle suivant. Cela maximise le quota disponible sans
      intervention manuelle.

    Ordre de fallback (après le modèle principal demandé) :
      1. gemini-3-flash-preview        (toutes les clés)
      2. gemini-3.1-flash-lite-preview (toutes les clés)
      3. gemini-2.5-pro                (toutes les clés)
      4. gemini-2.5-flash              (toutes les clés)
      5. groq/meta-llama/llama-4-scout-17b-16e-instruct
      6. OpenRouter (llama + mistral + arcee trinity)

    Modèles Ollama (préfixe "ollama/") :
      Pas de fallback cloud — Ollama est intentionnellement isolé.
      Si Ollama n'est pas disponible, l'appel échoue immédiatement (pas de
      basculement silencieux vers un fournisseur payant).

    NOTE IMPORTANTE : Tous les modèles Gemini utilisent l'endpoint OpenAI-compatible
    de Google afin que le mécanisme de retry config_list d'AutoGen se déclenche
    correctement sur les erreurs 429 RESOURCE_EXHAUSTED.
    """
    m = model_name.strip()
    config_list = []

    # ── Collecte de toutes les clés OpenRouter disponibles ───────────────────
    _openrouter_keys: list = []
    _openrouter_legacy = os.getenv("OPENROUTER_API_KEY", "")
    if _openrouter_legacy:
        _openrouter_keys.append(_openrouter_legacy)
    for _i in range(1, 10):
        _k = os.getenv(f"OPENROUTER_API_KEY_{_i}", "")
        if _k and _k not in _openrouter_keys:
            _openrouter_keys.append(_k)
    router_key = _openrouter_keys[0] if _openrouter_keys else ""

    # ── Collecte de toutes les clés Gemini disponibles ────────────────────────
    # Supporte GEMINI_API_KEY (legacy), GEMINI_API_KEY_1, GEMINI_API_KEY_2, etc.
    # AutoGen essaie chaque entrée de config_list dans l'ordre — même modèle,
    # clé différente = quota d'un autre compte.
    _gemini_keys: list = []
    _legacy_key = os.getenv("GEMINI_API_KEY", "")
    if _legacy_key:
        _gemini_keys.append(_legacy_key)
    for _i in range(1, 10):
        _k = os.getenv(f"GEMINI_API_KEY_{_i}", "")
        if _k and _k not in _gemini_keys:
            _gemini_keys.append(_k)
    gemini_key = _gemini_keys[0] if _gemini_keys else ""

    # ── Collecte de toutes les clés Groq disponibles ─────────────────────────
    _groq_keys: list = []
    _groq_legacy = os.getenv("GROQ_API_KEY", "")
    if _groq_legacy:
        _groq_keys.append(_groq_legacy)
    for _i in range(1, 10):
        _k = os.getenv(f"GROQ_API_KEY_{_i}", "")
        if _k and _k not in _groq_keys:
            _groq_keys.append(_k)
    groq_key = _groq_keys[0] if _groq_keys else ""

    def _gemini(model: str, api_key: str = None) -> dict:
        return {
            "model":       model,
            "api_key":     api_key or gemini_key,
            "base_url":    _GEMINI_OPENAI_BASE,
            "api_type":    "openai",
            "http_client": _make_no_keepalive_http_client(),
        }

    def _gemini_all_keys(model: str) -> list:
        """Une entrée config_list par clé Gemini dispo pour ce modèle."""
        if not _gemini_keys:
            return []
        return [_gemini(model, key) for key in _gemini_keys]

    def _groq(model: str, api_key: str = None) -> dict:
        return {
            "model":       model,
            "api_key":     api_key or groq_key,
            "base_url":    "https://api.groq.com/openai/v1",
            "api_type":    "openai",
            "http_client": _make_no_keepalive_http_client(),
        }

    def _groq_all_keys(model: str) -> list:
        """Une entrée config_list par clé Groq dispo pour ce modèle."""
        if not _groq_keys:
            return []
        return [_groq(model, key) for key in _groq_keys]

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

    def _openrouter(model: str, api_key: str = None) -> dict:
        return {
            "model":       model,
            "api_key":     api_key or router_key,
            "base_url":    "https://openrouter.ai/api/v1",
            "api_type":    "openai",
            "default_headers": {
                "HTTP-Referer": "https://dnd-moteur-aube-brisee",
                "X-Title":      "Moteur de l Aube Brisee",
            },
            "http_client": _make_no_keepalive_http_client(),
        }

    def _openrouter_all_keys(model: str) -> list:
        """Une entrée config_list par clé OpenRouter dispo pour ce modèle."""
        if not _openrouter_keys:
            return []
        return [_openrouter(model, key) for key in _openrouter_keys]

    def _ollama(model: str) -> dict:
        """
        Entrée config_list pour un modèle Ollama local.

        Ollama expose une API OpenAI-compatible sur /v1.
        La clé API doit être une chaîne non-vide (AutoGen l'exige même si
        Ollama l'ignore côté serveur) — on utilise "ollama" par convention.

        Timeout augmenté à 300 s : les modèles locaux peuvent être lents
        à la première génération (chargement en VRAM) ou sur des prompts longs.
        Ajustez OLLAMA_TIMEOUT dans .env si nécessaire.
        """
        timeout_s = float(os.getenv("OLLAMA_TIMEOUT", "300"))
        client = _NoKeepaliveHttpClient.__new__(_NoKeepaliveHttpClient)
        httpx.Client.__init__(
            client,
            limits=httpx.Limits(max_keepalive_connections=0, max_connections=10),
            timeout=httpx.Timeout(timeout_s),
        )
        client.__class__ = _NoKeepaliveHttpClient

        return {
            "model":       model,
            "api_key":     "ollama",   # valeur arbitraire — Ollama n'authentifie pas
            "base_url":    _OLLAMA_BASE,
            "api_type":    "openai",
            "http_client": client,
        }

    # ── 1. Modèle principal demandé ───────────────────────────────────────────
    if m.startswith("ollama/"):
        # Modèle local Ollama — pas de fallback cloud
        ollama_model = m[len("ollama/"):]
        config_list.append(_ollama(ollama_model))

    elif m.startswith("groq/"):
        config_list.extend(_groq_all_keys(m[len("groq/"):]))

    elif m.startswith("openrouter/"):
        if _openrouter_keys:
            config_list.extend(_openrouter_all_keys(m[len("openrouter/"):]))

    elif m.startswith("deepseek/"):
        deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        if deepseek_key:
            config_list.append(_deepseek(m[len("deepseek/"):]))

    else:  # Gemini — une entrée par clé disponible (rotation multi-comptes)
        config_list.extend(_gemini_all_keys(m))

    # ── Fallbacks : comportement différent selon le fournisseur primaire ──────
    #
    # RÈGLE : quand le modèle principal est Groq, on N'AJOUTE PAS de fallbacks
    # Gemini/OpenRouter. AutoGen cache le dernier index de config_list ayant
    # réussi (comportement "sticky") — une seule erreur transitoire Groq suffit
    # à faire basculer silencieusement TOUS les appels suivants vers Gemini.
    # Pour les agents Groq on préfère un vrai échec visible plutôt qu'un switch
    # invisible de fournisseur.
    #
    # Pour les modèles Gemini, chaque modèle de la chaîne est ajouté avec
    # TOUTES les clés disponibles — AutoGen épuise tous les comptes pour un
    # modèle avant de passer au suivant.
    #
    # Pour les modèles Ollama, pas de fallback — Ollama est isolé intentionnellement.

    if m.startswith("ollama/"):
        pass  # pas de fallback pour les modèles locaux

    elif m.startswith("groq/"):
        pass  # pas de fallback Groq

    elif m.startswith("openrouter/"):
        pass  # pas de fallback OpenRouter

    elif m.startswith("deepseek/"):
        pass  # pas de fallback DeepSeek

    else:
        # Modèle Gemini : chaîne de fallback complète avec rotation multi-comptes.
        # Ordre : gemini-3-flash-preview → gemini-3.1-flash-lite-preview →
        #         gemini-2.5-pro → gemini-2.5-flash → Groq → OpenRouter
        # ORDRE CRITIQUE : mettre les modèles confirmés disponibles en premier.
        # Si un modèle "preview" n'existe pas sur l'API (404), AutoGen le traite
        # comme un échec et passe au suivant — la rotation de clés est court-circuitée.
        # Vérifiez que chaque nom ici correspond exactement à un modèle Gemini actif.
        _GEMINI_FALLBACK_ORDER = [
            "gemini-2.5-flash",             # stable, très disponible
            "gemini-2.5-pro",               # stable
            "gemini-2.0-flash",             # stable, rapide
            "gemma-4-31b-it",                # preview — peut ne pas exister
            "gemma-4-26b-a4b-it",            # preview — peut ne pas exister
            "gemini-3-flash-preview",        # preview — peut ne pas exister
            "gemini-3.1-flash-lite-preview", # preview — peut ne pas exister
        ]
        for fb in _GEMINI_FALLBACK_ORDER:
            if m != fb:
                config_list.extend(_gemini_all_keys(fb))

        # Fallback Groq inter-fournisseur (toutes les clés)
        config_list.extend(_groq_all_keys("meta-llama/llama-4-scout-17b-16e-instruct"))

        # Fallbacks OpenRouter en ultime recours
        if _openrouter_keys:
            config_list.extend(_openrouter_all_keys("meta-llama/llama-3.3-70b-instruct:free"))
            config_list.extend(_openrouter_all_keys("mistralai/mistral-small-3.1-24b-instruct:free"))
            config_list.extend(_openrouter_all_keys("arcee-ai/trinity-large-preview:free"))

    # ── Sécurité : au cas où aucune clé n'est configurée ─────────────────────
    if not config_list:
        config_list.append({
            "model":    m,
            "api_key":  "DUMMY_KEY",
            "base_url": _GEMINI_OPENAI_BASE,
            "api_type": "openai",
        })

    # Debug désactivé — trop verbeux (se déclenche à chaque appel, y compris les recovery)
    # print("🛠️ DEBUG CONFIG LLM:", [c.get("model") for c in config_list])
    # print(f"🔑 Clés Gemini chargées : {len(_gemini_keys)} | Clés Groq : {len(_groq_keys)}")

    return {
        "config_list": config_list,
        "temperature":  temperature,
        # ── Désactive le cache sticky d'AutoGen ──────────────────────────────
        # Par défaut, AutoGen mémorise le dernier index ayant réussi (_last_config_index)
        # et repart de là au prochain appel → les clés précédentes sont ignorées
        # si l'index mémorisé est > 0, ce qui empêche la rotation multi-clés.
        # cache_seed=None force AutoGen à réévaluer config_list depuis l'index 0
        # à chaque nouvel appel, garantissant que TOUTES les clés sont tentées.
        "cache_seed":   None,
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


# ─── Utilitaires Ollama ────────────────────────────────────────────────────────

def check_ollama_available() -> bool:
    """
    Vérifie rapidement si le serveur Ollama répond sur localhost.
    Utilisé par le panneau de config pour afficher l'état du service.
    Timeout très court (2 s) — ne doit pas bloquer l'UI.
    """
    try:
        import requests as _req
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        r = _req.get(f"{host}/api/tags", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def list_ollama_models() -> list[str]:
    """
    Retourne la liste des modèles installés sur Ollama (noms complets).
    Retourne une liste vide si Ollama n'est pas disponible.
    """
    try:
        import requests as _req
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        r = _req.get(f"{host}/api/tags", timeout=3)
        if r.status_code == 200:
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass
    return []