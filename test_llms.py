#!/usr/bin/env python3
"""
test_llms.py — Teste chaque modèle LLM du projet.

Couvre :
  1. Les agents (app_config.json → agents.*)
  2. Le chroniqueur (app_config.json → chronicler)
  3. Tous les modèles hardcodés dans llm_config.py (fallbacks Gemini, Groq, OpenRouter)

Usage :
    python test_llms.py              # tout tester
    python test_llms.py --agents     # seulement agents + chroniqueur
    python test_llms.py --llms       # seulement les modèles de llm_config.py
    python test_llms.py Kaelen       # un agent spécifique
    python test_llms.py gemini-2.5-flash  # un modèle spécifique
    python test_llms.py --raw        # affiche la réponse complète
    python test_llms.py --parallel   # tests en parallèle (plus rapide)

Ne dépend PAS d'autogen — appelle les APIs directement via requests.
Lancer depuis le dossier du projet (lit .env et app_config.json).
"""

import os
import sys
import json
import time
import threading
import concurrent.futures
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ─── Couleurs ANSI ────────────────────────────────────────────────────────────
R       = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
GREEN   = "\033[92m"
RED     = "\033[91m"
YELLOW  = "\033[93m"
CYAN    = "\033[96m"
GREY    = "\033[90m"

AGENT_COLORS = {
    "Kaelen":     "\033[91m",
    "Elara":      "\033[94m",
    "Thorne":     "\033[95m",
    "Lyra":       "\033[92m",
    "chronicler": "\033[93m",
}

def _provider_color(model: str) -> str:
    if model.startswith("groq/"):       return "\033[95m"
    if model.startswith("openrouter/"): return "\033[96m"
    if model.startswith("deepseek/"):   return "\033[93m"  # jaune
    return "\033[94m"

# ─── Prompt de test ───────────────────────────────────────────────────────────
TEST_SYSTEM = "Tu es un assistant de test. Reponds toujours en francais, tres brievement."
TEST_USER   = "Dis uniquement 'Modele operationnel.' et rien d'autre."

# ─── Endpoints ────────────────────────────────────────────────────────────────
_GEMINI_BASE  = "https://generativelanguage.googleapis.com/v1beta/openai/"
_GROQ_BASE    = "https://api.groq.com/openai/v1"
_ROUTER_BASE  = "https://openrouter.ai/api/v1"
_DEEPSEEK_BASE = "https://api.deepseek.com"

def _resolve(model_name: str) -> tuple:
    m = model_name.strip()
    if m.startswith("groq/"):
        return _GROQ_BASE, os.getenv("GROQ_API_KEY", ""), m[len("groq/"):], {}
    if m.startswith("openrouter/"):
        k = os.getenv("OPENROUTER_API_KEY", "")
        if not k:
            for i in range(1, 10):
                k = os.getenv(f"OPENROUTER_API_KEY_{i}", "")
                if k: break
        return _ROUTER_BASE, k, m[len("openrouter/"):], {
            "HTTP-Referer": "https://dnd-moteur-aube-brisee",
            "X-Title": "Moteur de l Aube Brisee",
        }
    if m.startswith("deepseek/"):
        return _DEEPSEEK_BASE, os.getenv("DEEPSEEK_API_KEY", ""), m[len("deepseek/"):], {}
    return _GEMINI_BASE, os.getenv("GEMINI_API_KEY", ""), m, {}

def _provider(model: str) -> str:
    if model.startswith("groq/"):       return "Groq"
    if model.startswith("openrouter/"): return "OpenRouter"
    if model.startswith("deepseek/"):   return "DeepSeek"
    return "Gemini"

# ─── Appel API direct ─────────────────────────────────────────────────────────
def call_model(model_name: str, timeout: int = 25) -> dict:
    try:
        import requests
    except ImportError:
        return {"ok": False, "error": "pip install requests",
                "model": model_name, "response": "", "latency_ms": 0}

    base_url, api_key, model_id, extra_headers = _resolve(model_name)

    if not api_key:
        provider = _provider(model_name)
        key_name = {"Gemini": "GEMINI_API_KEY", "Groq": "GROQ_API_KEY",
                    "OpenRouter": "OPENROUTER_API_KEY", "DeepSeek": "DEEPSEEK_API_KEY"}[provider]
        return {"ok": False, "error": f"Cle manquante ({key_name})",
                "model": model_name, "response": "", "latency_ms": 0}

    url     = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {api_key}", **extra_headers}
    payload = {
        "model":       model_id,
        "messages":    [{"role": "system", "content": TEST_SYSTEM},
                        {"role": "user",   "content": TEST_USER}],
        "max_tokens":  60,
        "temperature": 0.0,
    }

    t0 = time.perf_counter()
    try:
        resp       = requests.post(url, headers=headers, json=payload, timeout=timeout)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        if resp.status_code != 200:
            try:
                body    = resp.json()
                err_msg = (body.get("error", {}).get("message", "")
                           or body.get("message", "") or resp.text[:200])
            except Exception:
                err_msg = resp.text[:200]
            return {"ok": False, "error": f"HTTP {resp.status_code} - {err_msg}",
                    "model": model_name, "response": "", "latency_ms": latency_ms}

        data         = resp.json()
        actual_model = data.get("model", model_id)
        content      = (data.get("choices", [{}])[0]
                            .get("message", {}).get("content", ""))
        return {"ok": True, "error": "", "model": model_name,
                "actual_model": actual_model,
                "response": content.strip(), "latency_ms": latency_ms}

    except Exception as e:
        name = type(e).__name__
        msg  = f"Timeout ({timeout}s)" if "Timeout" in name else str(e)
        return {"ok": False, "error": msg, "model": model_name,
                "response": "", "latency_ms": int((time.perf_counter() - t0) * 1000)}

# ─── Affichage ────────────────────────────────────────────────────────────────
_print_lock = threading.Lock()

def _ts() -> str:
    return time.strftime("%H:%M:%S")

def print_result(label: str, result: dict, show_raw: bool = False):
    color = AGENT_COLORS.get(label, _provider_color(result["model"]))
    model = result["model"]
    ms    = result["latency_ms"]

    if result["ok"]:
        actual       = result.get("actual_model", model)
        model_id_req = model.split("/", 1)[-1] if "/" in model else model
        drifted      = actual and actual != model and actual != model_id_req
        drift_str    = f"  {YELLOW}! repond par : {actual}{R}" if drifted else ""

        resp         = result["response"]
        resp_display = resp if show_raw else (resp[:80] + "..." if len(resp) > 80 else resp)
        line = (f"{GREY}{_ts()}{R}  {color}{BOLD}{label:<26}{R}  "
                f"{GREEN}OK{R}  {GREY}{model}{R}  {GREY}{ms}ms{R}{drift_str}\n"
                f"          {DIM}-> {resp_display}{R}")
    else:
        line = (f"{GREY}{_ts()}{R}  {color}{BOLD}{label:<26}{R}  "
                f"{RED}KO{R}  {GREY}{model}{R}  {GREY}{ms}ms{R}\n"
                f"          {RED}-> {result['error'][:120]}{R}")

    with _print_lock:
        print(line)

def print_pending(label: str, model: str):
    color = AGENT_COLORS.get(label, _provider_color(model))
    with _print_lock:
        print(f"{GREY}{_ts()}{R}  {color}{BOLD}{label:<26}{R}  {GREY}... {model}{R}", flush=True)

# ─── Extraction des modèles depuis llm_config.py ─────────────────────────────
def get_all_llm_config_models() -> list:
    """
    Retourne la liste complete et dedupliquee de tous les modeles
    hardcodes dans llm_config.py, en fonction des cles API disponibles.
    Synchronise manuellement avec llm_config.py — a mettre a jour si
    la liste des fallbacks change.
    """
    gemini_key   = os.getenv("GEMINI_API_KEY", "")
    groq_key     = os.getenv("GROQ_API_KEY", "")
    router_key   = os.getenv("OPENROUTER_API_KEY", "")
    if not router_key:
        for i in range(1, 10):
            router_key = os.getenv(f"OPENROUTER_API_KEY_{i}", "")
            if router_key: break
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")

    models = []
    seen   = set()

    def _add(m):
        if m not in seen:
            seen.add(m)
            models.append(m)

    # Gemini (ordre = priorite dans la chaine de fallback)
    if gemini_key:
        _add("gemini-3.1-pro-preview")
        _add("gemma-4-31b-it")
        _add("gemma-4-26b-a4b-it")
        _add("gemini-3.1-flash-lite-preview")
        _add("gemini-2.5-flash")
        _add("gemini-2.5-flash-lite")

    # DeepSeek direct
    if deepseek_key:
        _add("deepseek/deepseek-chat")
        _add("deepseek/deepseek-reasoner")

    # Groq
    if groq_key:
        _add("groq/meta-llama/llama-4-scout-17b-16e-instruct")
        _add("groq/llama-3.3-70b-versatile")

    # OpenRouter
    if router_key:
        _add("openrouter/meta-llama/llama-3.3-70b-instruct:free")
        _add("openrouter/mistralai/mistral-small-3.1-24b-instruct:free")
        _add("openrouter/arcee-ai/trinity-large-preview:free")

    return models

# ─── Chargement config ────────────────────────────────────────────────────────
def load_app_config() -> dict:
    cfg_path = Path("app_config.json")
    if not cfg_path.exists():
        print(f"{RED}app_config.json introuvable dans {Path.cwd()}{R}")
        sys.exit(1)
    with open(cfg_path) as f:
        return json.load(f)

# ─── Runner ───────────────────────────────────────────────────────────────────
def run_tests(targets: list, show_raw: bool, parallel: bool) -> tuple:
    results = []

    if parallel:
        def _task(label, model):
            print_pending(label, model)
            r = call_model(model)
            print_result(label, r, show_raw)
            return r["ok"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(_task, lbl, mdl) for lbl, mdl in targets]
            for f in concurrent.futures.as_completed(futs):
                results.append(f.result())
    else:
        for label, model in targets:
            print_pending(label, model)
            r = call_model(model)
            print_result(label, r, show_raw)
            results.append(r["ok"])
            print()

    return sum(results), len(results)

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    args        = sys.argv[1:]
    show_raw    = "--raw"      in args
    parallel    = "--parallel" in args
    only_agents = "--agents"   in args
    only_llms   = "--llms"     in args
    filters     = [a for a in args if not a.startswith("--")]

    cfg         = load_app_config()
    agents_cfg  = cfg.get("agents", {})
    chron_cfg   = cfg.get("chronicler", {})

    # ── Bloc 1 : agents + chroniqueur ─────────────────────────────────────────
    agent_targets = []
    for name, acfg in agents_cfg.items():
        m = acfg.get("model", "")
        if m:
            agent_targets.append((name, m))
    if chron_cfg.get("model"):
        agent_targets.append(("chronicler", chron_cfg["model"]))

    # ── Bloc 2 : llm_config.py (sans doublons avec bloc 1) ────────────────────
    agent_models = {m for _, m in agent_targets}
    llm_targets  = [(m, m) for m in get_all_llm_config_models() if m not in agent_models]

    # ── Filtrage par argument ──────────────────────────────────────────────────
    if filters:
        fl = [f.lower() for f in filters]
        agent_targets = [(l, m) for l, m in agent_targets
                         if l.lower() in fl or m.lower() in fl
                         or any(f in m.lower() for f in fl)]
        llm_targets   = [(l, m) for l, m in llm_targets
                         if l.lower() in fl or m.lower() in fl
                         or any(f in m.lower() for f in fl)]
        if not agent_targets and not llm_targets:
            all_items = ([l for l, _ in agent_targets] +
                         [m for m, _ in llm_targets])
            print(f"{RED}Aucun modele trouve pour : {', '.join(filters)}{R}")
            sys.exit(1)

    if only_agents: llm_targets   = []
    if only_llms:   agent_targets = []

    total_ok = total_n = 0

    # ── Section 1 ─────────────────────────────────────────────────────────────
    if agent_targets:
        print(f"\n{BOLD}{'='*64}{R}")
        print(f"{BOLD}  Agents & Chroniqueur  ({len(agent_targets)} modele(s)){R}")
        print(f"{BOLD}{'='*64}{R}\n")
        ok, n = run_tests(agent_targets, show_raw, parallel)
        total_ok += ok
        total_n  += n

    # ── Section 2 ─────────────────────────────────────────────────────────────
    if llm_targets:
        print(f"\n{BOLD}{'='*64}{R}")
        print(f"{BOLD}  Modeles llm_config.py  ({len(llm_targets)} fallback(s)){R}")
        print(f"{GREY}  (dedupliques — modeles deja testes ci-dessus exclus){R}")
        print(f"{BOLD}{'='*64}{R}\n")
        ok, n = run_tests(llm_targets, show_raw, parallel)
        total_ok += ok
        total_n  += n

    # ── Résumé ────────────────────────────────────────────────────────────────
    if total_n > 0:
        color = GREEN if total_ok == total_n else (YELLOW if total_ok > 0 else RED)
        print(f"\n{BOLD}{'='*64}{R}")
        print(f"{color}{BOLD}  Resultat : {total_ok}/{total_n} modeles operationnels{R}")
        if total_ok < total_n:
            print(f"{YELLOW}  {total_n - total_ok} echec(s) — verifier cles API et quotas{R}")
        print(f"{BOLD}{'='*64}{R}\n")

if __name__ == "__main__":
    main()