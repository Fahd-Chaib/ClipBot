"""Liste blanche : le bot ne travaille QUE sur les créateurs déclarés dans config.yaml."""
from __future__ import annotations

import re

DEFAULT_CREDIT = (
    "🎬 Vidéo originale : {video_title}\n"
    "📺 Créateur : {creator} {mention}\n"
    "🔗 {url}"
)


class NotAuthorized(Exception):
    pass


def _norm_handle(h: str | None) -> str:
    return (h or "").strip().lstrip("@").lower()


def _handles_from_info(info: dict) -> set[str]:
    out = set()
    for key in ("uploader_id", "channel_handle"):
        v = info.get(key)
        if v:
            out.add(_norm_handle(v))
    for key in ("channel_url", "uploader_url"):
        v = info.get(key) or ""
        m = re.search(r"/@([^/?#]+)", v)
        if m:
            out.add(_norm_handle(m.group(1)))
    return {h for h in out if h}


def describe(info: dict) -> str:
    return (
        f"chaîne « {info.get('channel') or info.get('uploader')} » "
        f"(channel_id={info.get('channel_id')}, handle={sorted(_handles_from_info(info)) or '?'})"
    )


def authorize(cfg: dict, info: dict) -> dict:
    """Retourne la fiche du créateur (règles de crédit).

    - créateur présent dans `creators` → sa fiche (règles de crédit personnalisées) ;
    - sinon, si `access.require_whitelist` est faux (défaut) → fiche générique construite depuis la chaîne ;
    - sinon (mode strict) → NotAuthorized.
    """
    strict = bool((cfg.get("access") or {}).get("require_whitelist", False))
    creators = [c for c in (cfg.get("creators") or []) if c.get("enabled", True)]
    cid = info.get("channel_id")
    handles = _handles_from_info(info)
    for c in creators:
        ids = {str(x) for x in (c.get("channel_ids") or [])}
        hs = {_norm_handle(x) for x in (c.get("handles") or [])}
        if (cid and cid in ids) or (handles & hs):
            if strict and not str(c.get("permission") or "").strip():
                raise NotAuthorized(
                    f"« {c.get('name')} » est dans la liste mais `permission` est vide : "
                    "indique le lien du programme de clipping ou la preuve de l'accord."
                )
            return c
    if strict:
        raise NotAuthorized(f"Créateur non autorisé (mode strict) : {describe(info)}")
    handle = sorted(handles)[0] if handles else ""
    return {
        "name": info.get("channel") or info.get("uploader") or "Créateur",
        "credit": {"mention": f"@{handle}" if handle else ""},
        "generic": True,
    }


def credit_text(creator: dict, info: dict, url: str) -> str:
    credit = creator.get("credit") or {}
    tpl = credit.get("description") or DEFAULT_CREDIT
    return tpl.format(
        creator=creator.get("name", ""),
        mention=credit.get("mention", ""),
        url=url,
        video_title=info.get("title", ""),
    ).strip()
