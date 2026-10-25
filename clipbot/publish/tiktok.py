"""TikTok : Content Posting API (Direct Post, envoi de fichier).

⚠ Tant que TikTok n'a pas audité l'app, tout reste privé (SELF_ONLY) : demande l'audit dans le portail.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from .. import vault
from ..util import env, log

API = "https://open.tiktokapis.com"


def _token() -> str:
    raw = vault.get("tiktok_token", "TIKTOK_TOKEN_JSON")
    if not raw:
        raise RuntimeError("TIKTOK_TOKEN_JSON manquant (python -m clipbot auth tiktok)")
    tok = json.loads(raw)
    if tok.get("expires_at", 0) - time.time() < 600:
        r = requests.post(
            f"{API}/v2/oauth/token/",
            data={
                "client_key": env("TIKTOK_CLIENT_KEY"), "client_secret": env("TIKTOK_CLIENT_SECRET"),
                "grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
            },
            timeout=60,
        )
        j = r.json()
        if "access_token" not in j:
            raise RuntimeError(f"TikTok refresh refusé : {str(j)[:200]}")
        tok.update(
            access_token=j["access_token"], refresh_token=j.get("refresh_token", tok["refresh_token"]),
            expires_at=time.time() + int(j.get("expires_in", 86400)),
        )
        vault.put("tiktok_token", json.dumps(tok), "TIKTOK_TOKEN_JSON")
    return tok["access_token"]


def post(file: Path, caption: str) -> str:
    token = _token()
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
    ci = requests.post(f"{API}/v2/post/publish/creator_info/query/", headers=h, timeout=60).json()
    opts = (ci.get("data") or {}).get("privacy_level_options") or ["SELF_ONLY"]
    privacy = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in opts else opts[0]
    if privacy != "PUBLIC_TO_EVERYONE":
        log("  ⚠ TikTok : publication privée (app non auditée ou compte privé)")

    size = file.stat().st_size
    chunk = size if size <= 64 * 1024 * 1024 else 50 * 1024 * 1024
    total = max(size // chunk, 1)
    init = requests.post(
        f"{API}/v2/post/publish/video/init/", headers=h, timeout=60,
        json={
            "post_info": {"title": caption[:2200], "privacy_level": privacy, "disable_duet": False,
                          "disable_comment": False, "disable_stitch": False},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": total},
        },
    ).json()
    data = init.get("data") or {}
    if not data.get("upload_url"):
        raise RuntimeError(f"TikTok init refusé : {str(init)[:300]}")
    pid, url = data["publish_id"], data["upload_url"]

    with open(file, "rb") as f:
        for i in range(total):
            start = i * chunk
            end = size - 1 if i == total - 1 else start + chunk - 1
            blob = f.read(end - start + 1)
            r = requests.put(
                url, data=blob, timeout=600,
                headers={"Content-Type": "video/mp4", "Content-Length": str(len(blob)),
                         "Content-Range": f"bytes {start}-{end}/{size}"},
            )
            if r.status_code not in (200, 201, 206):
                raise RuntimeError(f"TikTok upload {r.status_code}: {r.text[:200]}")

    for _ in range(40):
        time.sleep(8)
        st = requests.post(f"{API}/v2/post/publish/status/fetch/", headers=h, json={"publish_id": pid}, timeout=60).json()
        s = (st.get("data") or {}).get("status", "")
        if s == "PUBLISH_COMPLETE":
            log(f"  ▶ TikTok {pid} publié ({privacy})")
            return pid
        if s == "FAILED":
            raise RuntimeError(f"TikTok a rejeté la vidéo : {(st.get('data') or {}).get('fail_reason')}")
    raise RuntimeError("TikTok : traitement trop long (statut inconnu)")
