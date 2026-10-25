"""Choix des moments forts : le LLM lit la transcription par tranches et note chaque moment.

Aucun quota fixe : on garde tout ce qui dépasse `selection.min_score`, dans la limite de
`max_clips_per_video`. Les bornes d'un clip sont toujours calées sur des phrases entières.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

from .config import get
from .llm import chat_json
from .util import log, mmss

SYSTEM = """You are a senior short-form video editor (YouTube Shorts / TikTok / Reels).
You read a transcript of a long video and pick the moments that would perform best as standalone vertical clips.

A GREAT clip:
- opens with a hook in its first sentence (a bold claim, a question, a surprise, a conflict);
- is fully self-contained: a stranger understands it with no prior context;
- contains ONE clear payoff: a punchline, a revelation, a strong emotion, a surprising fact, a powerful story beat, or a precise useful tip;
- ends cleanly right after the payoff (never mid-thought, never on filler);
- lasts between {min_s} and {max_s} seconds in total (use the timestamps to estimate); aim for 25-50 seconds when the story needs it, never pad with filler.

Rules:
- Be a harsh judge. Most moments are average. Score 1-10; 7 = good, 9+ = exceptional. Only report moments you would genuinely post.
- Use ONLY the segment ids shown. A clip is a continuous run of segments from start_segment to end_segment (inclusive).
- Write "reason" and "hook" in the SAME language as the transcript.
- Do not return overlapping clips. Return at most {n} clips; returning an empty list is fine.
- Never pick pure sponsor reads, intros/outros, or housekeeping ("subscribe", "link in description").

Reply with JSON only:
{{"clips":[{{"start_segment":int,"end_segment":int,"score":number,"reason":"short why","hook":"the opening idea in <= 10 words"}}]}}"""


@dataclass
class Clip:
    video_id: str
    url: str
    start: float
    end: float
    score: float
    reason: str
    hook: str
    seg_start: int
    seg_end: int

    def to_dict(self) -> dict:
        return asdict(self)


def _windows(segments: list[dict], win_s: float, overlap_s: float) -> list[list[dict]]:
    if not segments:
        return []
    out, t0, end = [], segments[0]["start"], segments[-1]["end"]
    step = max(win_s - overlap_s, 60)
    while t0 < end:
        w = [s for s in segments if t0 <= s["start"] < t0 + win_s]
        if len(w) >= 2:
            out.append(w)
        t0 += step
    return out


def _fit(segments: list[dict], a: int, b: int, min_s: float, max_s: float):
    """Cale (a, b) sur des phrases entières et dans [min_s, max_s]. Renvoie (start, end, a, b) ou None."""
    if not (0 <= a < len(segments) and 0 <= b < len(segments)) or b < a:
        return None
    start = segments[a]["words"][0]["start"]
    k = b
    while k > a and segments[k]["words"][-1]["end"] - start > max_s:
        k -= 1
    end = segments[k]["words"][-1]["end"]
    if end - start > max_s:  # une seule phrase trop longue → on coupe sur un mot
        ws = [w for w in segments[a]["words"] if w["end"] - start <= max_s]
        if not ws:
            return None
        end = ws[-1]["end"]
    while end - start < min_s and k + 1 < len(segments):  # trop court → on prolonge d'une phrase
        nxt = segments[k + 1]["words"][-1]["end"]
        if nxt - start > max_s:
            break
        k += 1
        end = nxt
    if end - start < min_s:
        return None
    return start, end, a, k


def _overlap(a0, a1, b0, b1) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    return inter / max(min(a1 - a0, b1 - b0), 1e-6)


def find_clips(transcript: dict, cfg: dict, info: dict, url: str) -> list[Clip]:
    sel = cfg.get("selection", {})
    min_s = float(sel.get("min_seconds", 15))
    max_s = float(sel.get("max_seconds", 58)) - 1.0  # marge pour les respirations ajoutées au montage
    min_score = float(sel.get("min_score", 7.0))
    win_s = float(sel.get("window_minutes", 8)) * 60
    ov_s = float(sel.get("overlap_minutes", 1)) * 60
    cap = int(sel.get("max_clips_per_video", 12))
    segs = transcript["segments"]

    # les coupes de silence raccourcissent le clip au montage : on vise plus long pour rester ≥ min_seconds
    min_s = min(min_s + 6.0, max_s - 4.0)
    system = SYSTEM.format(min_s=int(min_s), max_s=int(max_s), n=3)
    found: list[Clip] = []
    failed = 0
    wins = _windows(segs, win_s, ov_s)
    log(f"  🧠 analyse en {len(wins)} tranches")
    for wi, w in enumerate(wins, 1):
        lines = "\n".join(f"[{s['id']}] {mmss(s['start'])} {s['text']}" for s in w)
        user = (
            f"Video: {info.get('title', '')}\nChannel: {info.get('channel') or info.get('uploader', '')}\n"
            f"Transcript window {wi}/{len(wins)} (segment id, timestamp, text):\n{lines}"
        )
        try:
            data = chat_json(system, user, cfg, max_tokens=1500)
        except Exception as e:  # noqa: BLE001
            log(f"  ⚠ tranche {wi} non analysée : {e}")
            failed += 1
            continue
        items = data.get("clips", []) if isinstance(data, dict) else data
        valid_ids = {s["id"] for s in w}
        for it in items or []:
            try:
                a, b = int(it["start_segment"]), int(it["end_segment"])
                score = float(it["score"])
            except (KeyError, TypeError, ValueError):
                continue
            if a not in valid_ids or b not in valid_ids or score < min_score:
                continue
            fit = _fit(segs, a, b, min_s, max_s)
            if not fit:
                continue
            start, end, a2, b2 = fit
            found.append(
                Clip(
                    video_id=info["id"], url=url, start=start, end=end, score=score,
                    reason=str(it.get("reason", ""))[:200], hook=str(it.get("hook", ""))[:120],
                    seg_start=a2, seg_end=b2,
                )
            )

    if failed:
        raise RuntimeError(
            f"quota LLM gratuit atteint : {failed} tranche(s) sur {len(wins)} non analysées. "
            "Rien n'est enregistré pour cette vidéo : relance la même commande plus tard (demain). "
            "La transcription est gardée, elle ne sera pas refaite."
        )

    # dédoublonnage (les tranches se chevauchent) : on garde les mieux notés
    found.sort(key=lambda c: -c.score)
    kept: list[Clip] = []
    for c in found:
        if all(_overlap(c.start, c.end, k.start, k.end) < 0.15 for k in kept):
            kept.append(c)
        if len(kept) >= cap:
            break
    kept.sort(key=lambda c: c.start)
    log(f"  ✓ {len(kept)} moment(s) retenu(s) (score ≥ {min_score})")
    return kept
