#!/usr/bin/env python3
"""
test_gemini_key.py — Vérifie la clé Gemini active et l'accès aux modèles preview.
Usage : python test_gemini_key.py
"""
import os, requests, time
from dotenv import load_dotenv

load_dotenv(override=True)  # override=True force le rechargement même si déjà chargé

key = os.getenv("GEMINI_API_KEY", "")
if not key:
    print("GEMINI_API_KEY absente du .env")
    exit(1)

print(f"Clé active : ...{key[-6:]}")
print()

BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
]

for model in MODELS:
    url = BASE.rstrip("/") + "/chat/completions"
    t0  = time.perf_counter()
    r   = requests.post(url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": "Dis juste OK."}], "max_tokens": 5},
        timeout=15)
    ms = int((time.perf_counter() - t0) * 1000)

    if r.status_code == 200:
        print(f"  \033[92m✓\033[0m {model:<35} {ms}ms")
    else:
        try:    err = r.json().get("error", {}).get("message", r.text[:120])
        except: err = r.text[:120]
        icon = "\033[91m✗\033[0m" if r.status_code != 429 else "\033[93m⏱\033[0m"
        print(f"  {icon} {model:<35} HTTP {r.status_code} — {err[:80]}")
