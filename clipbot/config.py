from __future__ import annotations

import os
from pathlib import Path

import yaml

from .util import ROOT


def load(path: str | None = None) -> dict:
    p = Path(path or os.environ.get("CLIPBOT_CONFIG") or ROOT / "config.yaml")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get(cfg: dict, dotted: str, default=None):
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur if cur is not None else default
