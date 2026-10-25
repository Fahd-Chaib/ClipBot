"""Instagram Reels : API « connexion Instagram », envoi résumable du fichier (pas besoin d'URL publique)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from .. import vault
from ..config import get
from ..util import env, log


def _token() -> str:
    """Jeton longue durée, renouvelé automatiquement (~tous les 20 jours) et gardé dans le coffre."""
    raw = vault.get("instagram_token", "INSTAGRAM_ACCESS_TOKEN")
    if not raw:
        raise RuntimeError("INSTAGRAM_ACCESS_TOKEN manquant")
    tok = json.loads(raw) if raw.strip().startswith("{") else {"token": raw.strip(), "refreshed": 0}
    if time.time() - tok.get("refreshed", 0) > 20 * 86400:
        try:
            r = requests.get(
                "https://graph.instagram.com/refresh_access_token",
                params={"grant_type": "ig_refresh_token", "access_token": tok["token"]}, timeout=60,
            ).json()
            if "access_token" in r:
                tok = {"token": r["access_token"], "refreshed": time.time()}
                vault.put("instagram_token", json.dumps(tok), "INSTAGRAM_ACCESS_TOKEN")
                log("  🔄 jeton Instagram renouvelé")
        except Exception as e:  # noqa: BLE001
            log(f"  ⚠ renouvellement Instagram impossible : {e}")
    return tok["token"]


def post(file: Path, caption: str, cfg: dict) -> str:
    host = get(cfg, "platforms.instagram.api_host", "graph.instagram.com")
    ver = get(cfg, "platforms.instagram.api_version", "v23.0")
    uid = env("INSTAGRAM_USER_ID")
    if not uid:
        raise RuntimeError("INSTAGRAM_USER_ID manquant")
    token = _token()
    base = f"https://{host}/{ver}"

    c = requests.post(
        f"{base}/{uid}/media",
        data={"media_type": "REELS", "upload_type": "resumable", "caption": caption[:2200],
              "share_to_feed": "true", "access_token": token},
        timeout=60,
    ).json()
    if "id" not in c:
        raise RuntimeError(f"Instagram container refusé : {str(c)[:300]}")
    cid = c["id"]
    uri = c.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{ver}/{cid}"

    size = file.stat().st_size
    with open(file, "rb") as f:
        up = requests.post(
            uri, data=f, timeout=900,
            headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)},
        )
    if up.status_code >= 400 or '"success":false' in up.text.replace(" ", ""):
        raise RuntimeError(f"Instagram upload {up.status_code}: {up.text[:300]}")

    for _ in range(60):
        time.sleep(10)
        st = requests.get(f"{base}/{cid}", params={"fields": "status_code,status", "access_token": token}, timeout=60).json()
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram traitement échoué : {st.get('status')}")
    else:
        raise RuntimeError("Instagram : traitement trop long")

    pub = requests.post(f"{base}/{uid}/media_publish", data={"creation_id": cid, "access_token": token}, timeout=120).json()
    if "id" not in pub:
        raise RuntimeError(f"Instagram publication refusée : {str(pub)[:300]}")
    log(f"  ▶ Instagram Reel {pub['id']} publié")
    return pub["id"]
