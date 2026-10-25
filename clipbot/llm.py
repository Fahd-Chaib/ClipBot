"""Routeur LLM gratuit : gpt-oss-120b via Groq, puis Cerebras en relais."""
from __future__ import annotations

import time
from collections import deque

import requests

from .config import get
from .util import env, log, parse_json_loose

ENDPOINTS = {
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", "groq_model"),
    # même clé Groq, autre modèle = autre quota gratuit (relais quand gpt-oss-120b a atteint sa limite du jour)
    "groq_small": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", "groq_fallback_model"),
    "cerebras": ("https://api.cerebras.ai/v1/chat/completions", "CEREBRAS_API_KEY", "cerebras_model"),
}
_window: dict[str, deque] = {}  # provider -> deque[(timestamp, tokens)]


def _pace(provider: str, est_tokens: int, budget: int):
    """Reste sous le quota « tokens / minute » du palier gratuit."""
    dq = _window.setdefault(provider, deque())
    while True:
        now = time.time()
        while dq and now - dq[0][0] > 60:
            dq.popleft()
        used = sum(t for _, t in dq)
        if used + est_tokens <= budget or not dq:
            return
        wait = 60 - (now - dq[0][0]) + 0.5
        log(f"  ⏳ pause LLM {wait:.0f}s (quota tokens/minute)")
        time.sleep(max(wait, 1))


def _call(provider: str, cfg: dict, messages: list[dict], max_tokens: int) -> str:
    url, key_env, model_key = ENDPOINTS[provider]
    key = env(key_env)
    if not key:
        raise RuntimeError(f"{key_env} manquante")
    model = get(cfg, f"llm.{model_key}")
    est = sum(len(m["content"]) for m in messages) // 3 + max_tokens
    _pace(provider, est, int(get(cfg, "llm.tpm_budget", 6500)))
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.4,
        "max_completion_tokens": max_tokens,
        "reasoning_effort": get(cfg, "llm.reasoning_effort", "low"),
    }
    for attempt in range(4):
        r = requests.post(url, json=payload, headers={"Authorization": f"Bearer {key}"}, timeout=180)
        if r.status_code == 429:
            wait = float(r.headers.get("retry-after", "20"))
            if wait > 90 or attempt == 3:
                raise RuntimeError(f"{provider}: quota atteint (retry dans {wait:.0f}s)")
            log(f"  ⏳ {provider} 429, pause {wait:.0f}s")
            time.sleep(wait + 1)
            continue
        if r.status_code == 400 and "reasoning_effort" in payload:
            payload.pop("reasoning_effort")  # paramètre non supporté → on réessaie sans
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"{provider} {r.status_code}: {r.text[:300]}")
        j = r.json()
        used = j.get("usage", {}).get("total_tokens", est)
        _window.setdefault(provider, deque()).append((time.time(), used))
        return j["choices"][0]["message"]["content"] or ""
    raise RuntimeError(f"{provider}: échec")


def chat_json(system: str, user: str, cfg: dict, max_tokens: int = 1800):
    """Appelle le LLM et renvoie du JSON déjà parsé (essaie chaque fournisseur, 2 tentatives de parsing)."""
    providers = get(cfg, "llm.providers", ["groq", "cerebras"])
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    errors = []
    for p in providers:
        if not env(ENDPOINTS[p][1]):
            continue
        for attempt in range(2):
            try:
                text = _call(p, cfg, messages, max_tokens)
                return parse_json_loose(text)
            except ValueError as e:  # JSON invalide → on redemande une fois
                errors.append(f"{p}: {e}")
                messages = messages + [
                    {"role": "assistant", "content": "(réponse invalide)"},
                    {"role": "user", "content": "Réponds UNIQUEMENT avec un JSON valide, sans texte autour."},
                ]
            except Exception as e:  # noqa: BLE001
                errors.append(f"{p}: {e}")
                break
    raise RuntimeError("LLM indisponible : " + " | ".join(errors) if errors else "aucune clé LLM (GROQ_API_KEY / CEREBRAS_API_KEY)")
