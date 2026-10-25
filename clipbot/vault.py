"""Coffre chiffré (Fernet) pour les jetons qui se renouvellent (TikTok, Instagram).

Stocké dans state/vault.enc (branche `state`). La clé est le secret STATE_KEY.
Une valeur fournie par un secret GitHub ne remplace celle du coffre que si le secret a changé.
"""
from __future__ import annotations

import hashlib
import json
import os

from cryptography.fernet import Fernet, InvalidToken

from .util import STATE, ensure

_PATH = STATE / "vault.enc"
_cache: dict | None = None


def keygen() -> str:
    return Fernet.generate_key().decode()


def _fernet() -> Fernet | None:
    key = os.environ.get("STATE_KEY")
    return Fernet(key.encode()) if key else None


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    f = _fernet()
    if not f or not _PATH.exists():
        _cache = {}
        return _cache
    try:
        _cache = json.loads(f.decrypt(_PATH.read_bytes()))
    except (InvalidToken, ValueError):
        _cache = {}
    return _cache


def _save():
    f = _fernet()
    if not f:
        return
    ensure(STATE)
    _PATH.write_bytes(f.encrypt(json.dumps(_load()).encode()))


def get(name: str, env_name: str | None = None) -> str | None:
    """Valeur du coffre, sauf si le secret d'environnement a changé depuis la dernière fois."""
    data = _load()
    env_val = os.environ.get(env_name) if env_name else None
    entry = data.get(name)
    if env_val:
        h = hashlib.sha256(env_val.encode()).hexdigest()
        if not entry or entry.get("src") != h:
            return env_val
    return entry.get("value") if entry else env_val


def put(name: str, value: str, env_name: str | None = None):
    data = _load()
    src = None
    if env_name and os.environ.get(env_name):
        src = hashlib.sha256(os.environ[env_name].encode()).hexdigest()
    elif name in data:
        src = data[name].get("src")
    data[name] = {"value": value, "src": src}
    _save()
