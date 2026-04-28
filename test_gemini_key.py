#!/usr/bin/env python3
"""
test_gemini_key.py — Vérifie la clé Gemini active et l'accès aux modèles preview.
Usage : python test_gemini_key.py
"""
import os, requests, time
from dotenv import load_dotenv

load_dotenv(override=True)  # override=True force le rechargement même si déjà chargé

keys = ["GEMINI_API_KEY", "GEMINI_API_KEY_1", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"]
for key in keys:
    key = os.getenv(key, "")
    if not key:
        print("GEMINI_API_KEY absente du .env")
        continue

    print(f"Clé active : ...{key[-6:]}")
    print()

    BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
    MODELS = [
        "gemini-3.1-pro-preview",
        "gemini-3.1-flash-lite-preview",
        "gemini-3-flash-preview",
        "gemma-4-31b-it",
        "gemma-4-26b-a4b-it",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
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
