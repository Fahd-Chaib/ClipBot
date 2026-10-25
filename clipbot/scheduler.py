"""Répartition des clips sur la semaine suivante.

- démarre le prochain lundi (ou demain), ou après le dernier créneau déjà réservé ;
- répartit au plus juste entre les jours, heures étalées dans la fenêtre avec un peu de jitter ;
- entrelace les vidéos sources (jamais deux clips de la même vidéo à la suite quand c'est évitable),
  les meilleurs clips de chaque vidéo passant en premier.
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import queue as Q
from .config import get


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _start_day(cfg: dict, q: dict, tz: ZoneInfo, now: datetime) -> date:
    today = now.astimezone(tz).date()
    if get(cfg, "schedule.start", "next_monday") == "tomorrow":
        d = today + timedelta(days=1)
    else:
        d = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    last = Q.last_slot(q)
    if last:
        after = last.astimezone(tz).date() + timedelta(days=1)
        d = max(d, after)
    return d


def _day_counts(n: int, days: int, cap: int) -> list[int]:
    n = min(n, days * cap)
    base, extra = divmod(n, days)
    counts = [base] * days
    # les jours « en plus » sont répartis régulièrement dans la semaine
    for k in range(extra):
        counts[int((k + 0.5) * days / extra)] += 1
    return counts


def plan_slots(n: int, cfg: dict, q: dict, now: datetime | None = None, seed: int | None = None) -> list[datetime]:
    now = now or datetime.now(timezone.utc)
    rnd = random.Random(seed)
    tz = ZoneInfo(get(cfg, "channel.timezone", "UTC"))
    days = int(get(cfg, "schedule.days", 7))
    cap = int(get(cfg, "schedule.max_per_day", 6))
    w0, w1 = (_hm(x) for x in get(cfg, "schedule.window", ["11:00", "22:30"]))
    jitter = int(get(cfg, "schedule.jitter_minutes", 20))
    gap = int(get(cfg, "schedule.min_gap_minutes", 45))

    start = _start_day(cfg, q, tz, now)
    slots: list[datetime] = []
    for di, count in enumerate(_day_counts(n, days, cap)):
        if not count:
            continue
        day = start + timedelta(days=di)
        a = datetime.combine(day, w0, tzinfo=tz)
        b = datetime.combine(day, w1, tzinfo=tz)
        span = (b - a).total_seconds() / 60
        mins = []
        for k in range(count):
            m = span * (k + 0.5) / count + rnd.uniform(-jitter, jitter)
            m = min(max(m, 0), span)
            mins.append(round(m))
        mins.sort()
        for i in range(1, len(mins)):
            if mins[i] - mins[i - 1] < gap:
                mins[i] = min(mins[i - 1] + gap, round(span))
        for m in mins:
            slots.append((a + timedelta(minutes=m)).astimezone(timezone.utc))
    return sorted(slots)


def interleave(clips: list[dict]) -> list[dict]:
    """Ordre de publication : tour de rôle entre vidéos sources, meilleurs clips d'abord."""
    groups: dict[str, list[dict]] = {}
    for c in clips:
        groups.setdefault(c["video_id"], []).append(c)
    for g in groups.values():
        g.sort(key=lambda c: -c["score"])
    order, last = [], None
    pool = {k: list(v) for k, v in groups.items()}
    while any(pool.values()):
        keys = sorted((k for k in pool if pool[k]), key=lambda k: (k == last, -len(pool[k])))
        k = keys[0]
        order.append(pool[k].pop(0))
        last = k
    return order


def assign(clips: list[dict], cfg: dict, q: dict, now: datetime | None = None) -> tuple[list[dict], list[dict]]:
    """Renvoie (clips planifiés avec 'slot', clips écartés faute de place)."""
    ordered = interleave(clips)
    slots = plan_slots(len(ordered), cfg, q, now)
    kept = ordered[: len(slots)]
    # les moins bons sont écartés si la semaine est pleine
    if len(ordered) > len(slots):
        ranked = sorted(ordered, key=lambda c: -c["score"])[: len(slots)]
        ids = {id(c) for c in ranked}
        kept = [c for c in ordered if id(c) in ids]
        dropped = [c for c in ordered if id(c) not in ids]
    else:
        dropped = []
    for c, s in zip(kept, slots):
        c["slot"] = s.isoformat()
    return kept, dropped
