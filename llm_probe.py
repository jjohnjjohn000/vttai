"""
llm_probe.py — Sonde HTTP précoce pour détecter la disponibilité d'un LLM
avant de laisser AutoGen faire son appel complet.

Deux seuils de détection (indépendants) :
  T1 (TIMEOUT_HEADERS ~3s)  : HTTP 200 + headers reçus
                               → le serveur a accepté la requête
  T2 (TIMEOUT_TTFT    ~10s) : Premier token reçu (TTFT)
                               → le modèle a commencé à générer

Si T1 dépasse → serveur surchargé, requête refusée, ou réseau dégradé.
Si T2 dépasse → modèle lent à démarrer (file d'attente Gemini saturée).

Dans les deux cas → LLMProbeTimeout est levée par run_probe_parallel(),
ce qui déclenche le fallback automatique dans autogen_engine.py
(même chemin que LLMTimeoutError existant).

Coût quota :
  La sonde envoie une requête légère séparée (max_tokens=1, historique tronqué).
  Coût réel ≈ input_tokens du contexte court + 1 output token.
  Si le modèle primaire répond dans les délais, la sonde est annulée
  dès la détection du premier token — l'impact est minimal.

Threading :
  La sonde tourne dans son propre thread daemon, HORS du _SSL_LOCK
  d'engine_agents.py. Elle utilise httpx avec son propre contexte SSL —
  aucun conflit avec OpenSSL/AutoGen.
"""

import threading
import time
import httpx


# ─── Seuils configurables ────────────────────────────────────────────────────

TIMEOUT_HEADERS = 3.0    # secondes max pour recevoir le HTTP 200
TIMEOUT_TTFT    = 10.0   # secondes max pour recevoir le 1er token (depuis t=0)


# ─── Exception ───────────────────────────────────────────────────────────────

class LLMProbeTimeout(Exception):
    """
    Levée quand la sonde ne reçoit pas HTTP 200 ou le premier token
    dans les délais configurés.
    Capturée dans make_thinking_wrapper → traitée comme LLMTimeoutError.
    """
    pass


# ─── Helpers internes ────────────────────────────────────────────────────────

def _extract_endpoint(llm_config: dict):
    """
    Extrait (base_url, api_key, model) depuis llm_config d'un agent AutoGen.
    Retourne None si la config est invalide ou incomplète.
    """
    config_list = llm_config.get("config_list", [])
    if not config_list:
        return None
    cfg      = config_list[0]
    base_url = str(cfg.get("base_url", "")).rstrip("/")
    api_key  = str(cfg.get("api_key", ""))
    model    = str(cfg.get("model", ""))
    if not base_url or not api_key or not model:
        return None
    return base_url, api_key, model


def _build_probe_messages(messages: list) -> list:
    """
    Construit un historique court pour la sonde :
    - Conserve le system message s'il est en tête
    - Prend les 2 derniers échanges user/assistant
    But : minimiser les input tokens tout en gardant assez
    de contexte pour que le modèle réponde quelque chose.
    """
    if not messages:
        return [{"role": "user", "content": "Ok."}]

    probe_msgs = []

    # System message en tête
    if messages and messages[0].get("role") == "system":
        # Tronquer le system message à 500 chars pour limiter les tokens
        sys_content = str(messages[0].get("content", ""))[:500]
        probe_msgs.append({"role": "system", "content": sys_content})
        tail = messages[1:]
    else:
        tail = messages

    # Garder uniquement les 4 derniers messages (2 échanges)
    for m in tail[-4:]:
        role    = m.get("role", "user")
        content = str(m.get("content", ""))
        if role in ("user", "assistant"):
            # Tronquer chaque message à 200 chars
            probe_msgs.append({"role": role, "content": content[:200]})

    # S'assurer qu'il y a au moins un message user
    if not any(m["role"] == "user" for m in probe_msgs):
        probe_msgs.append({"role": "user", "content": "Continue."})

    return probe_msgs


# ─── Fonction principale de sonde ────────────────────────────────────────────

def probe_llm_availability(
    llm_config:      dict,
    messages:        list,
    timeout_headers: float = TIMEOUT_HEADERS,
    timeout_ttft:    float = TIMEOUT_TTFT,
    stop_event:      threading.Event | None = None,
) -> dict:
    """
    Sonde la disponibilité du LLM configuré via une requête streaming légère.

    Paramètres :
      llm_config      : agent.llm_config (dict AutoGen avec config_list)
      messages        : historique de messages à envoyer (sera tronqué)
      timeout_headers : délai max (s) pour recevoir HTTP 200
      timeout_ttft    : délai max (s) pour recevoir le 1er token (depuis t=0)
      stop_event      : si levé, la sonde s'arrête immédiatement

    Retourne un dict :
      {"ok": True,  "t_headers": float, "t_ttft": float}   → disponible
      {"ok": False, "reason": str,      "t_elapsed": float} → timeout ou erreur
    """
    result = {"ok": False, "reason": "not_started", "t_elapsed": 0.0}
    t_start = time.monotonic()

    endpoint = _extract_endpoint(llm_config)
    if endpoint is None:
        result["reason"] = "invalid_llm_config"
        return result

    base_url, api_key, model = endpoint
    url = f"{base_url}/chat/completions"

    payload = {
        "model":      model,
        "messages":   _build_probe_messages(messages),
        "max_tokens": 1,
        "stream":     True,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
        "Accept":        "text/event-stream",
    }

    try:
        with httpx.Client(
            timeout=httpx.Timeout(
                connect = timeout_headers,          # échec rapide si serveur injoignable
                read    = timeout_ttft,             # budget total pour le 1er token
                write   = 5.0,
                pool    = 5.0,
            )
        ) as client:

            with client.stream("POST", url, json=payload, headers=headers) as response:

                t_headers = time.monotonic() - t_start
                result["t_elapsed"] = t_headers

                # ── Vérification du status HTTP ──────────────────────────────
                if response.status_code == 429:
                    result["reason"] = "quota_429"
                    return result
                if response.status_code == 401:
                    result["reason"] = "auth_401"
                    return result
                if response.status_code != 200:
                    result["reason"] = f"http_{response.status_code}"
                    return result

                # ── HTTP 200 reçu — vérifier le délai ───────────────────────
                if t_headers > timeout_headers:
                    result["reason"] = (
                        f"headers_timeout ({t_headers:.2f}s > {timeout_headers}s)"
                    )
                    return result

                print(
                    f"[LLM Probe] {model} — HTTP 200 reçu en {t_headers:.2f}s"
                )

                # ── Attendre le premier token ────────────────────────────────
                for chunk in response.iter_text():

                    if stop_event and stop_event.is_set():
                        result["reason"] = "stopped_by_event"
                        return result

                    t_now = time.monotonic() - t_start
                    if t_now > timeout_ttft:
                        result["reason"] = (
                            f"ttft_timeout ({t_now:.2f}s > {timeout_ttft}s)"
                        )
                        return result

                    if chunk.strip():
                        t_ttft = time.monotonic() - t_start
                        print(
                            f"[LLM Probe] {model} — 1er token en {t_ttft:.2f}s"
                        )
                        result.update({
                            "ok":        True,
                            "t_headers": t_headers,
                            "t_ttft":    t_ttft,
                            "t_elapsed": t_ttft,
                        })
                        return result

                # Stream fermé sans aucun token
                result["reason"] = "empty_stream"

    except httpx.ConnectTimeout:
        result["reason"] = f"connect_timeout (>{timeout_headers}s)"
    except httpx.ReadTimeout:
        result["reason"] = f"read_timeout (>{timeout_ttft}s)"
    except httpx.RequestError as e:
        result["reason"] = f"request_error: {type(e).__name__}: {e}"
    except Exception as e:
        result["reason"] = f"unexpected: {type(e).__name__}: {e}"

    result["t_elapsed"] = time.monotonic() - t_start
    return result


# ─── Lancement en parallèle avec l'appel AutoGen réel ────────────────────────

def run_probe_parallel(
    llm_config:   dict,
    messages:     list,
    real_done:    threading.Event,
    stop_event:   threading.Event,
    agent_name:   str = "?",
    timeout_headers: float = TIMEOUT_HEADERS,
    timeout_ttft:    float = TIMEOUT_TTFT,
) -> None:
    """
    Lance la sonde dans le thread courant, EN PARALLÈLE de l'appel AutoGen réel
    (qui tourne dans son propre daemon thread).

    Doit être appelé dans un thread daemon séparé depuis make_thinking_wrapper.

    Logique :
      - Si la sonde échoue (T1 ou T2 dépassé) AVANT que real_done soit levé
        → lève stop_event pour interrompre l'appel AutoGen
        → stocke la raison dans l'attribut .probe_failure_reason sur stop_event

      - Si real_done est levé avant que la sonde échoue
        → la sonde s'arrête silencieusement (succès ou annulation)

    Paramètres :
      real_done   : threading.Event levé quand l'appel AutoGen se termine
      stop_event  : threading.Event levé pour interrompre l'appel AutoGen
                    (le même que app_ref._stop_event dans make_thinking_wrapper)
    """
    result = probe_llm_availability(
        llm_config      = llm_config,
        messages        = messages,
        timeout_headers = timeout_headers,
        timeout_ttft    = timeout_ttft,
        stop_event      = real_done,   # arrête la sonde si l'appel réel est déjà terminé
    )

    # L'appel réel a terminé avant la sonde → rien à faire
    if real_done.is_set():
        return

    if not result["ok"]:
        reason = result.get("reason", "unknown")
        print(
            f"[LLM Probe] {agent_name} — ÉCHEC : {reason} "
            f"({result.get('t_elapsed', 0):.2f}s) → interruption"
        )
        # Attacher la raison sur l'événement pour que make_thinking_wrapper
        # puisse construire un message d'erreur précis
        stop_event.probe_failure_reason = (
            f"LLM probe timeout pour {agent_name} : {reason}"
        )
        stop_event.set()