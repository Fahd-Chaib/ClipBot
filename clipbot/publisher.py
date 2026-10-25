"""Passage de publication (toutes les heures) :

- YouTube : envoie dès que possible, EN PROGRAMMÉ (publishAt = créneau du clip), dans la limite du quota
  d'uploads par jour ; les clips restants partent les jours suivants ;
- TikTok / Instagram : pas de programmation par API → le bot publie lui-même quand le créneau arrive.
"""
from __future__ import annotations

import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import queue as Q
from . import storage
from .config import get
from .util import ROOT, WORK, log


def _local_file(item: dict, tag: str) -> Path:
    if item.get("local_path"):
        p = ROOT / item["local_path"]
        if p.exists():
            return p
    return storage.download(tag, item["asset"], WORK / "publish")


def _fail(st: dict, e: Exception, max_tries: int):
    st["tries"] = st.get("tries", 0) + 1
    st["error"] = str(e)[:300]
    if st["tries"] >= max_tries:
        st["state"] = "failed"
        log(f"  ✗ abandon après {st['tries']} essais : {e}")
    else:
        log(f"  ⚠ échec (essai {st['tries']}/{max_tries}) : {e}")


def publish_pass(cfg: dict) -> dict:
    from .publish import instagram, tiktok, youtube

    q = Q.load()
    now = datetime.now(timezone.utc)
    tag = get(cfg, "queue.release_tag", "clipbot-queue")
    max_tries = int(get(cfg, "queue.max_tries", 3))
    enabled = [p for p in Q.PLATFORMS if get(cfg, f"platforms.{p}.enabled", False)]
    stats = {"youtube": 0, "tiktok": 0, "instagram": 0, "left": 0}

    # quota YouTube : fenêtre glissante de 24 h
    yt_log = [t for t in q.get("yt_log", []) if Q.parse_dt(t) > now - timedelta(hours=24)]
    yt_cap = int(get(cfg, "platforms.youtube.max_uploads_per_day", 5))
    yt_blocked = False

    for item in sorted(q["items"], key=lambda i: i["slot"]):
        slot = Q.parse_dt(item["slot"])
        st = item["status"]
        texts = item["texts"]
        f = None

        # ---- YouTube : envoi anticipé, publication programmée
        y = st.get("youtube", {})
        if "youtube" in enabled and y.get("state") == "pending" and not yt_blocked:
            if len(yt_log) >= yt_cap:
                yt_blocked = True
            else:
                try:
                    f = f or _local_file(item, tag)
                    vid = youtube.upload(
                        f, texts["youtube"]["title"], texts["youtube"]["description"], texts["youtube"]["tags"],
                        slot, str(get(cfg, "platforms.youtube.category_id", "24")),
                    )
                    y.update(state="done", video_id=vid)
                    yt_log.append(now.isoformat())
                    stats["youtube"] += 1
                except youtube.QuotaExceeded as e:
                    log(f"  ⏸ quota YouTube atteint, on reprend plus tard ({e})")
                    yt_blocked = True
                except Exception as e:  # noqa: BLE001
                    log(f"✗ YouTube — {item['title']}\n{traceback.format_exc(limit=2)}")
                    _fail(y, e, max_tries)

        # ---- TikTok / Instagram : à l'heure du créneau
        if slot <= now:
            for name, fn in (
                ("tiktok", lambda p: tiktok.post(p, texts["tiktok"]["caption"])),
                ("instagram", lambda p: instagram.post(p, texts["instagram"]["caption"], cfg)),
            ):
                s = st.get(name, {})
                if name in enabled and s.get("state") == "pending":
                    try:
                        f = f or _local_file(item, tag)
                        s.update(state="done", post_id=fn(f))
                        stats[name] += 1
                    except Exception as e:  # noqa: BLE001
                        log(f"✗ {name} — {item['title']}\n{traceback.format_exc(limit=2)}")
                        _fail(s, e, max_tries)
        Q.save({**q, "yt_log": yt_log})

    q["yt_log"] = yt_log
    for done in Q.archive_finished(q, enabled):
        if done.get("asset") and not done.get("local_path") and storage.available():
            storage.delete(tag, done["asset"])
    stats["left"] = len(q["items"])
    Q.save(q)
    log(f"✓ publication : {stats}")
    return stats
