"""Titres, accroche à l'écran, description, hashtags, emojis et punchlines (une requête LLM par clip),
puis composition des textes par plateforme avec le crédit exigé par le créateur."""
from __future__ import annotations

import re

from .creators import credit_text
from .llm import chat_json
from .transcribe import LANG_NAMES

SYSTEM = """You write the packaging for one short vertical video clip (YouTube Shorts / TikTok / Reels).
Write ALL text in {lang_name}, the language spoken in the clip. Style: VERY clickbait — curiosity gap, strong emotion, tension, a number or a bold claim — to maximise clicks.
HARD RULE: it must stay TRUE to the clip. Never promise something the clip does not deliver, never invent facts or quotes, never attribute to the speaker anything they did not say. Clickbait = framing, not lying.

You receive the clip's words numbered like "0:Hello 1:world". Return JSON only:
{{
 "title": "max 80 chars, no hashtags",
 "overlay_hook": "max 7 words shown on screen during the first seconds (punchy; ALL CAPS if Latin script)",
 "description": "1-2 short sentences that tease the clip, no hashtags",
 "hashtags": ["5 to 8 relevant tags without the # sign"],
 "emojis": [{{"i": word index where the emoji pops up, "emoji": "one emoji"}}],   // 3 to 6, spaced out, matching the emotion at that word
 "punch": [word indexes of 2 to 4 punchline / emphasis words where the video zooms in]
}}"""


def _clean_tags(tags, extra=()) -> list[str]:
    out, seen = [], set()
    for t in list(tags or []) + list(extra or []):
        t = re.sub(r"[\s#]+", "", str(t))
        if not t or t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append("#" + t)
    return out[:10]


def make_meta(words: list[str], text: str, lang: str, hook_hint: str, cfg: dict) -> dict:
    numbered = " ".join(f"{i}:{w}" for i, w in enumerate(words))
    system = SYSTEM.format(lang_name=LANG_NAMES.get(lang, lang))
    user = f"Clip words:\n{numbered}\n\nWhy this clip was picked: {hook_hint}"
    n = len(words)
    try:
        d = chat_json(system, user, cfg, max_tokens=900)
        if not isinstance(d, dict):
            raise ValueError("format inattendu")
    except Exception:  # noqa: BLE001
        d = {}
    title = str(d.get("title") or hook_hint or text[:70]).strip()
    emojis = []
    for e in d.get("emojis") or []:
        try:
            i, em = int(e["i"]), str(e["emoji"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= i < n and em and len(em) <= 8:
            emojis.append({"i": i, "emoji": em})
    punch = sorted({int(i) for i in (d.get("punch") or []) if str(i).lstrip("-").isdigit() and 0 <= int(i) < n})
    hook = str(d.get("overlay_hook") or hook_hint or title).strip()
    return {
        "title": title[:95],
        "overlay_hook": hook[:70],
        "description": str(d.get("description") or "").strip()[:300],
        "hashtags": _clean_tags(d.get("hashtags")),
        "emojis": emojis[:6],
        "punch": punch[:4],
    }


def compose(meta: dict, creator: dict, info: dict, url: str) -> dict:
    """Textes finaux par plateforme (crédit exigé par le créateur inclus)."""
    cr = creator.get("credit") or {}
    credit = credit_text(creator, info, url)
    tags = _clean_tags(meta["hashtags"], cr.get("hashtags"))
    suffix = cr.get("title_suffix") or ""
    title = (meta["title"] + suffix)[:100]

    yt_desc = "\n\n".join(x for x in [meta["description"], credit, " ".join(tags + ["#Shorts"])] if x)
    tt_caption = " ".join([meta["title"] + suffix] + tags[:6] + ([cr["mention"]] if cr.get("mention") else []))
    ig_caption = "\n\n".join(x for x in [meta["title"] + suffix, meta["description"], credit, " ".join(tags[:15])] if x)
    return {
        "youtube": {
            "title": title,
            "description": yt_desc[:4900],
            "tags": [t.lstrip("#") for t in tags][:15] + ["Shorts"],
        },
        "tiktok": {"caption": tt_caption[:2100]},
        "instagram": {"caption": ig_caption[:2100]},
    }
