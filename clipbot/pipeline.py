"""Un passage complet : liens → transcription → moments forts → clips montés → planning → file d'attente."""
from __future__ import annotations

import json
import re
import shutil
import time
import traceback
from pathlib import Path

from . import creators, download, highlights, meta as meta_mod, queue as Q, scheduler, storage, transcribe
from .config import get
from .render import render_clip
from .util import OUT, ROOT, WORK, ensure, log, mmss, slug


def parse_urls(raw: list[str]) -> list[str]:
    urls = []
    for chunk in raw:
        for u in re.split(r"[\s,]+", chunk or ""):
            if u.startswith("http") and u not in urls:
                urls.append(u)
    return urls


def _clip_words(transcript: dict, clip: highlights.Clip, dl_start: float):
    words, texts = [], []
    for seg in transcript["segments"][clip.seg_start : clip.seg_end + 1]:
        texts.append(seg["text"])
        for k, w in enumerate(seg["words"]):
            if w["end"] < clip.start - 1e-3 or w["start"] > clip.end + 1e-3:
                continue
            words.append({"w": w["w"], "start": w["start"] - dl_start, "end": w["end"] - dl_start, "sf": k == 0})
    return words, " ".join(texts)


def _write_report(info: dict, url: str, clips: list[highlights.Clip]):
    ensure(OUT)
    data = {"video": info.get("title"), "url": url, "clips": [c.to_dict() for c in clips]}
    (OUT / f"analysis_{info['id']}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"# {info.get('title')}", f"{url}", ""]
    for c in clips:
        lines.append(
            f"- **{c.score:.1f}/10** · {mmss(c.start)} → {mmss(c.end)} ({c.end - c.start:.0f}s) · "
            f"[ouvrir](https://youtu.be/{info['id']}?t={int(c.start)}) — {c.hook} _({c.reason})_"
        )
    (OUT / f"analysis_{info['id']}.md").write_text("\n".join(lines), encoding="utf-8")


def process_video(url: str, cfg: dict, q: dict, force: bool, analyze_only: bool) -> list[dict]:
    log(f"\n━━━ {url}")
    info = download.fetch_info(url, cfg)
    creator = creators.authorize(cfg, info)  # NotAuthorized seulement en mode strict (access.require_whitelist)
    vid = info["id"]
    log(f"  ✓ {creator['name']}{' (crédit générique)' if creator.get('generic') else ''} — « {info.get('title')} »")

    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        log("  ⏭ direct / à venir : ignoré")
        return []
    max_min = float(get(cfg, "selection.max_video_minutes", 240))
    if (info.get("duration") or 0) > max_min * 60:
        log(f"  ⏭ trop longue (> {max_min:.0f} min)")
        return []
    if vid in Q.known_video_ids(q) and not force:
        log("  ⏭ déjà traitée (utilise « force » pour la refaire)")
        return []

    work = WORK / vid
    ensure(work)
    cache = WORK / "transcripts" / f"{vid}.json"
    if cache.exists():
        tr = json.loads(cache.read_text(encoding="utf-8"))
        log("  ♻ transcription déjà faite, on la réutilise")
    else:
        audio = download.download_audio(url, work, cfg)
        tr = transcribe.transcribe(audio, work, cfg, creator.get("language"))
        audio.unlink(missing_ok=True)
        ensure(cache.parent)
        cache.write_text(json.dumps(tr, ensure_ascii=False), encoding="utf-8")
    clips = highlights.find_clips(tr, cfg, info, url)
    _write_report(info, url, clips)
    if analyze_only or not clips:
        for c in clips:
            log(f"  · {c.score:.1f}  {mmss(c.start)}→{mmss(c.end)}  {c.hook}")
        return []

    pad = float(get(cfg, "download.pad_seconds", 0.5))
    results = []
    for n, clip in enumerate(clips, 1):
        log(f"  ✂ clip {n}/{len(clips)} · {mmss(clip.start)}→{mmss(clip.end)} · score {clip.score:.1f}")
        try:
            dl_start = max(0.0, clip.start - pad)
            dl_end = min(float(info.get("duration") or clip.end + pad), clip.end + pad + 0.5)
            raw = download.download_section(url, dl_start, dl_end, work / f"clip{n}", cfg)
            words, text = _clip_words(tr, clip, dl_start)
            if len(words) < 5:
                log("  ⏭ trop peu de mots, ignoré")
                continue
            m = meta_mod.make_meta([w["w"] for w in words], text, tr["language"], clip.hook, cfg)
            name = f"{slug(vid, 20)}_{int(clip.start)}.mp4"
            out = OUT / "clips" / name
            res = render_clip(raw, words, m, out, cfg, tr["language"], work / f"r{n}")
            raw.unlink(missing_ok=True)
            texts = meta_mod.compose(m, creator, info, url)
            log(f"  ✓ « {texts['youtube']['title']} » — {res['duration']:.1f}s")
            results.append({
                "id": f"{vid}-{int(clip.start)}", "video_id": vid, "source_url": url, "creator": creator["name"],
                "title": texts["youtube"]["title"], "score": clip.score, "asset": name, "texts": texts,
                "file": str(out),
            })
        except Exception as e:  # noqa: BLE001
            log(f"  ✗ clip ignoré : {e}\n{traceback.format_exc(limit=3)}")
    shutil.rmtree(work, ignore_errors=True)
    cache.unlink(missing_ok=True)
    return results


def run(urls: list[str], cfg: dict, force=False, analyze_only=False, deadline_min: float = 330.0, preview=False) -> int:
    t0 = time.time()
    ensure(OUT, WORK)
    q = Q.load()
    produced: list[dict] = []
    refused = 0
    try:
        for url in urls:
            if (time.time() - t0) / 60 > deadline_min:
                log(f"⏱ limite de temps atteinte, non traité : {url}")
                continue
            try:
                produced += process_video(url, cfg, q, force, analyze_only)
            except creators.NotAuthorized as e:
                refused += 1
                log(f"🚫 REFUSÉ — {e}")
            except Exception as e:  # noqa: BLE001
                log(f"✗ {url} : {e}\n{traceback.format_exc(limit=3)}")
    finally:
        if produced and preview:
            _write_preview(produced)
        elif produced:
            _enqueue(produced, cfg, q)
    log(f"\nTerminé : {len(produced)} clip(s) prêt(s), {refused} lien(s) refusé(s), {(time.time() - t0) / 60:.1f} min")
    return 0


def _write_preview(clips: list[dict]):
    """Aperçu : clips montés dans out/clips + leurs textes dans out/preview_*.md (rien n'est mis en file ni publié)."""
    ensure(OUT)
    lines = ["# Aperçu des clips (rien n'a été publié)", ""]
    for c in clips:
        t = c["texts"]
        lines += [f"## {c['title']}", f"- fichier : `{c['asset']}` · score {c['score']:.1f}", "",
                  "**Description YouTube**", "", t["youtube"]["description"], "",
                  f"**Légende TikTok** : {t['tiktok']['caption']}", ""]
    (OUT / "preview_clips.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "preview_clips.json").write_text(json.dumps(clips, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"  👀 aperçu : {len(clips)} clip(s) dans out/clips — rien mis en file, rien publié")


def _enqueue(clips: list[dict], cfg: dict, q: dict):
    from datetime import datetime, timezone

    kept, dropped = scheduler.assign(clips, cfg, q)
    for c in dropped:
        log(f"  ⏭ écarté (semaine pleine) : {c['title']} ({c['score']:.1f})")
    tag = get(cfg, "queue.release_tag", "clipbot-queue")
    remote = storage.available()
    for c in kept:
        item = {
            "id": c["id"], "video_id": c["video_id"], "source_url": c["source_url"], "creator": c["creator"],
            "title": c["title"], "score": c["score"], "slot": c["slot"], "asset": c["asset"], "texts": c["texts"],
            "status": Q.new_status(), "created": datetime.now(timezone.utc).isoformat(),
        }
        if remote:
            storage.upload(tag, Path(c["file"]))
        else:
            item["local_path"] = str(Path(c["file"]).relative_to(ROOT)) if str(c["file"]).startswith(str(ROOT)) else c["file"]
        q["items"].append(item)
        log(f"  📅 {item['slot'][:16]}  {item['title']}")
    Q.save(q)
