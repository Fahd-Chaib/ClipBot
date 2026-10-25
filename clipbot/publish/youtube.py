"""YouTube Shorts : upload + programmation native (privacyStatus=private + publishAt).

Quota API par défaut : 10 000 unités/jour, un upload coûte 1 600 → ~6 uploads/jour.
⚠ Un projet Google non audité (créé après juillet 2020) voit ses vidéos forcées en « privé » :
   remplis le formulaire d'audit « YouTube API Services – Audit and Quota Extension ».
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..util import env, log

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class QuotaExceeded(Exception):
    pass


def _service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    raw = env("YOUTUBE_TOKEN_JSON")
    if not raw:
        raise RuntimeError("YOUTUBE_TOKEN_JSON manquant (python -m clipbot auth youtube)")
    creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload(file: Path, title: str, description: str, tags: list[str], publish_at: datetime | None,
           category_id: str = "24") -> str:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    yt = _service()
    future = publish_at is not None and publish_at > datetime.now(timezone.utc) + timedelta(minutes=3)
    status = {"privacyStatus": "private" if future else "public", "selfDeclaredMadeForKids": False}
    if future:
        status["publishAt"] = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    body = {
        "snippet": {"title": title[:100], "description": description[:5000], "tags": tags[:15], "categoryId": category_id},
        "status": status,
    }
    media = MediaFileUpload(str(file), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    try:
        req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        resp = None
        while resp is None:
            _, resp = req.next_chunk()
    except HttpError as e:
        txt = str(e)
        if e.resp.status == 403 and ("quota" in txt.lower() or "uploadLimitExceeded" in txt):
            raise QuotaExceeded(txt[:200]) from e
        raise
    vid = resp["id"]
    log(f"  ▶ YouTube {vid} ({'programmé ' + status['publishAt'] if future else 'publié'})")
    return vid
