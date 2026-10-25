"""File d'attente persistante (state/queue.json) : clips rendus en attente de publication."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .util import STATE, ensure

_PATH = STATE / "queue.json"
PLATFORMS = ("youtube", "tiktok", "instagram")


def load() -> dict:
    if _PATH.exists():
        try:
            q = json.loads(_PATH.read_text(encoding="utf-8"))
            q.setdefault("items", [])
            q.setdefault("history", [])
            return q
        except json.JSONDecodeError:
            pass
    return {"items": [], "history": []}


def save(q: dict):
    ensure(STATE)
    q["history"] = q["history"][-500:]
    _PATH.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")


def parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def known_video_ids(q: dict) -> set[str]:
    ids = {i.get("video_id") for i in q["items"]}
    ids |= {h.get("video_id") for h in q["history"]}
    return {i for i in ids if i}


def last_slot(q: dict) -> datetime | None:
    slots = [parse_dt(i["slot"]) for i in q["items"]]
    return max(slots) if slots else None


def slots_per_day(q: dict, tz) -> dict:
    out: dict = {}
    for i in q["items"]:
        d = parse_dt(i["slot"]).astimezone(tz).date()
        out[d] = out.get(d, 0) + 1
    return out


def new_status() -> dict:
    return {p: {"state": "pending", "tries": 0} for p in PLATFORMS}


def is_finished(item: dict, enabled: list[str]) -> bool:
    for p in enabled:
        st = item["status"].get(p, {})
        if st.get("state") == "pending":
            return False
    return True


def archive_finished(q: dict, enabled: list[str]) -> list[dict]:
    """Déplace dans l'historique les éléments terminés ; renvoie ceux à nettoyer côté stockage."""
    done, keep = [], []
    for i in q["items"]:
        (done if is_finished(i, enabled) else keep).append(i)
    for i in done:
        q["history"].append(
            {
                "video_id": i.get("video_id"),
                "id": i["id"],
                "title": i.get("title"),
                "slot": i["slot"],
                "status": {p: i["status"].get(p, {}).get("state") for p in enabled},
            }
        )
    q["items"] = keep
    return done
