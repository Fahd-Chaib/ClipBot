from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
OUT = ROOT / "out"
STATE = ROOT / "state"


def log(*a):
    print(*a, flush=True)


def ensure(*dirs: Path):
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def run(cmd: list[str], check=True, capture=True, **kw) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=capture, text=True, **kw)
    if check and p.returncode != 0:
        tail = (p.stderr or p.stdout or "")[-1500:]
        raise RuntimeError(f"commande échouée ({cmd[0]}): {tail}")
    return p


def slug(text: str, n: int = 40) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return (t or "clip")[:n]


def mmss(sec: float) -> str:
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def retry(fn, tries=3, base=2.0, what="opération"):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if i < tries - 1:
                wait = base * (2 ** i)
                log(f"  ↻ {what}: {e} — nouvel essai dans {wait:.0f}s")
                time.sleep(wait)
    raise last  # type: ignore[misc]


def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def load_dotenv(path: Path | None = None):
    p = path or ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split("  #")[0].strip().strip('"').strip("'")
        if v and not os.environ.get(k.strip()):
            os.environ[k.strip()] = v


def parse_json_loose(text: str):
    """Extrait le premier objet/tableau JSON d'une réponse de LLM (gère les ```json)."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = t.find(opener), t.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(t[i : j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("JSON introuvable dans la réponse du LLM")


def die(msg: str, code: int = 1):
    print(msg, file=sys.stderr, flush=True)
    sys.exit(code)
