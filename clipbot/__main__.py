"""CLI : python -m clipbot <commande>"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import sys
import time
from pathlib import Path

from . import config, vault
from .util import ROOT, die, env, load_dotenv, log


def cmd_run(a, cfg):
    from .pipeline import parse_urls, run

    urls = parse_urls(a.urls + [os.environ.get("CLIPBOT_URLS", "")])
    if not urls:
        die("Aucun lien. Exemple : python -m clipbot run https://www.youtube.com/watch?v=XXXX")
    code = run(urls, cfg, force=a.force, analyze_only=a.analyze_only, deadline_min=a.deadline, preview=a.preview)
    if not a.analyze_only and not a.preview and not a.no_publish:
        from .publisher import publish_pass

        publish_pass(cfg)
    return code


def cmd_go(a, cfg):
    """Tout sur ton PC (téléchargement, montage), puis envoi des clips à GitHub qui programme et publie."""
    from . import queue as Q
    from . import sync
    from .pipeline import parse_urls, run

    urls = parse_urls(a.urls + [os.environ.get("CLIPBOT_URLS", "")])
    if not urls:
        die("Aucun lien. Exemple : python -m clipbot go https://www.youtube.com/watch?v=XXXX")
    repo = sync.repo_name()
    os.environ["GITHUB_REPOSITORY"] = repo
    os.environ["GH_REPO"] = repo
    tok = sync.token()
    if not tok:
        die("GitHub CLI non connecté. Installe-le (winget install GitHub.cli) puis lance : gh auth login")
    os.environ["GH_TOKEN"] = tok
    log(f"Dépôt : {repo}")
    sync.restore(repo)
    before = {i["id"] for i in Q.load()["items"]}
    run(urls, cfg, force=a.force, analyze_only=a.analyze_only, deadline_min=a.deadline, preview=a.preview)
    if not a.analyze_only and not a.preview:
        n = sync.push_new_items(repo, before)
        if n:
            log(f"\n✅ {n} clip(s) programmé(s). GitHub s'occupe de la suite : ton PC peut s'éteindre.")
    return 0


def cmd_publish(a, cfg):
    from . import queue as Q
    from .publisher import publish_pass

    if not Q.load()["items"]:
        log("file d'attente vide")
        return 0
    publish_pass(cfg)
    return 0


def cmd_demo(a, cfg):
    from .demo import run_demo

    run_demo(cfg)
    return 0


def cmd_status(a, cfg):
    from . import queue as Q

    q = Q.load()
    log(f"{len(q['items'])} clip(s) en attente, {len(q['history'])} dans l'historique")
    for i in sorted(q["items"], key=lambda i: i["slot"]):
        s = " ".join(f"{p[:2]}={i['status'][p]['state']}" for p in Q.PLATFORMS)
        log(f"  {i['slot'][:16]}  [{s}]  {i['title']}")
    return 0


def cmd_check(a, cfg):
    from . import creators, download

    info = download.fetch_info(a.url, cfg)
    log(creators.describe(info))
    handle = info.get("uploader_id") or info.get("channel_handle") or ""
    log("\nÀ coller dans config.yaml → creators :\n")
    log(f"  - name: \"{info.get('channel') or info.get('uploader')}\"")
    log(f"    channel_ids: [\"{info.get('channel_id')}\"]")
    log(f"    handles: [\"{handle}\"]")
    log("    permission: \"lien du programme de clipping / preuve de l'accord\"")
    try:
        c = creators.authorize(cfg, info)
        log("\n✓ accepté" + (" (crédit générique, aucune fiche dans config.yaml)" if c.get("generic") else " (fiche trouvée dans config.yaml)"))
    except creators.NotAuthorized as e:
        log(f"\n🚫 {e}")
    return 0


def cmd_keygen(a, cfg):
    print(vault.keygen())
    return 0


def cmd_cookies(a, cfg):
    p = Path(a.file)
    if not p.exists():
        die(f"fichier introuvable : {p}")
    keep = []
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("#") or not line.strip():
            keep.append(line)
        elif any(d in line.split("\t")[0] for d in ("youtube.com", "google.com", "youtube-nocookie.com")):
            keep.append(line)
    b64 = base64.b64encode(gzip.compress("\n".join(keep).encode(), 9)).decode()
    log(f"{len(keep)} lignes conservées, secret de {len(b64)} caractères (limite GitHub : 48 000)")
    if len(b64) > 48000:
        log("⚠ trop gros : exporte uniquement les cookies de youtube.com avec ton extension.")
    print(b64)
    return 0


def cmd_auth(a, cfg):
    if a.target == "youtube":
        from google_auth_oauthlib.flow import InstalledAppFlow

        from .publish.youtube import SCOPES

        path = env("YOUTUBE_CLIENT_SECRETS", "secrets/youtube_client_secret.json")
        flow = InstalledAppFlow.from_client_secrets_file(path, SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
        print("\n=== copie ceci dans le secret YOUTUBE_TOKEN_JSON ===")
        print(creds.to_json())
    elif a.target == "tiktok":
        import requests

        key, secret, redir = env("TIKTOK_CLIENT_KEY"), env("TIKTOK_CLIENT_SECRET"), env("TIKTOK_REDIRECT_URI")
        if not (key and secret and redir):
            die("Renseigne TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET et TIKTOK_REDIRECT_URI dans .env")
        from urllib.parse import quote, urlparse, parse_qs

        url = (
            "https://www.tiktok.com/v2/auth/authorize/?client_key=" + key
            + "&scope=user.info.basic,video.publish&response_type=code&redirect_uri=" + quote(redir, safe="")
            + "&state=clipbot"
        )
        print("Ouvre ce lien, autorise, puis colle l'URL sur laquelle tu atterris :\n" + url + "\n")
        back = input("URL de retour : ").strip()
        code = parse_qs(urlparse(back).query).get("code", [None])[0]
        if not code:
            die("Pas de paramètre `code` dans cette URL.")
        r = requests.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={"client_key": key, "client_secret": secret, "code": code,
                  "grant_type": "authorization_code", "redirect_uri": redir},
            timeout=60,
        ).json()
        if "access_token" not in r:
            die(f"TikTok a refusé : {r}")
        tok = {"access_token": r["access_token"], "refresh_token": r["refresh_token"],
               "expires_at": time.time() + int(r.get("expires_in", 86400)), "open_id": r.get("open_id")}
        print("\n=== copie ceci dans le secret TIKTOK_TOKEN_JSON ===")
        print(json.dumps(tok))
    return 0


def main(argv=None):
    load_dotenv()
    ap = argparse.ArgumentParser(prog="clipbot", description="Shorts automatiques à partir de créateurs partenaires")
    ap.add_argument("--config", help="chemin d'un autre config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="traiter des liens YouTube")
    p.add_argument("urls", nargs="*")
    p.add_argument("--force", action="store_true", help="retraiter une vidéo déjà vue")
    p.add_argument("--analyze-only", action="store_true", help="choisir les moments sans rien monter ni publier")
    p.add_argument("--preview", action="store_true", help="monter les clips pour les voir : rien en file, rien publié")
    p.add_argument("--no-publish", action="store_true", help="ne pas lancer l'envoi YouTube juste après")
    p.add_argument("--deadline", type=float, default=330.0, help="minutes max avant d'arrêter de lancer de nouvelles vidéos")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("go", help="tout faire sur ce PC puis envoyer les clips à GitHub (programmation + publication)")
    p.add_argument("urls", nargs="*")
    p.add_argument("--force", action="store_true")
    p.add_argument("--analyze-only", action="store_true")
    p.add_argument("--preview", action="store_true", help="monter les clips sans rien envoyer")
    p.add_argument("--deadline", type=float, default=600.0)
    p.set_defaults(fn=cmd_go)

    sub.add_parser("publish", help="envoyer / publier ce qui est dû").set_defaults(fn=cmd_publish)
    sub.add_parser("demo", help="rendre une vidéo de démo sans réseau").set_defaults(fn=cmd_demo)
    sub.add_parser("status", help="voir la file d'attente").set_defaults(fn=cmd_status)
    sub.add_parser("keygen", help="générer STATE_KEY").set_defaults(fn=cmd_keygen)

    p = sub.add_parser("check-creator", help="lire l'identité d'une chaîne et vérifier la liste blanche")
    p.add_argument("url")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("cookies", help="préparer les cookies YouTube")
    p.add_argument("action", choices=["pack"])
    p.add_argument("file")
    p.set_defaults(fn=cmd_cookies)

    p = sub.add_parser("auth", help="connecter une plateforme (une fois, sur ton PC)")
    p.add_argument("target", choices=["youtube", "tiktok"])
    p.set_defaults(fn=cmd_auth)

    a = ap.parse_args(argv)
    cfg = config.load(a.config)
    sys.exit(a.fn(a, cfg) or 0)


if __name__ == "__main__":
    main()
