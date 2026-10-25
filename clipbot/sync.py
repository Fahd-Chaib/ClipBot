"""Pont PC → GitHub : quand le bot tourne sur ton ordinateur (`python -m clipbot go <lien>`),
les clips montés partent dans la release GitHub et la file d'attente est ajoutée à la branche `state`.
Ensuite c'est le workflow horaire de GitHub qui programme/publie (ton PC peut être éteint).

On ne fait QUE AJOUTER les nouveaux éléments à la file d'attente la plus récente de GitHub : si une
publication a eu lieu pendant que ton PC travaillait, rien n'est écrasé.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from .util import STATE, ensure, env, log, run


def repo_name() -> str:
    r = env("CLIPBOT_REPO")
    if r:
        return r
    p = run(["git", "remote", "get-url", "origin"], check=False)
    m = re.search(r"github\.com[:/]+([^/]+/[^/\s]+?)(?:\.git)?\s*$", (p.stdout or "").strip())
    if m:
        return m.group(1)
    raise RuntimeError("Dépôt introuvable : ajoute CLIPBOT_REPO=Fahd-Chaib/ClipBot dans le fichier .env")


def token() -> str | None:
    t = env("GITHUB_TOKEN") or env("GH_TOKEN")
    if t:
        return t
    try:
        p = run(["gh", "auth", "token"], check=False)
        return (p.stdout or "").strip() or None
    except FileNotFoundError:
        return None


def remote_url(repo: str) -> str:
    override = env("CLIPBOT_REMOTE_URL")  # surtout pour les tests
    if override:
        return override
    t = token()
    return f"https://x-access-token:{t}@github.com/{repo}.git" if t else f"https://github.com/{repo}.git"


def _clone_state(url: str, dest: Path) -> bool:
    p = run(["git", "clone", "--quiet", "--depth", "1", "--branch", "state", url, str(dest)], check=False)
    return p.returncode == 0


def restore(repo: str):
    """Récupère la file d'attente actuelle de GitHub dans ./state."""
    ensure(STATE)
    tmp = Path(tempfile.mkdtemp())
    try:
        if _clone_state(remote_url(repo), tmp / "s"):
            for f in (tmp / "s").iterdir():
                if f.name == ".git":
                    continue
                if f.is_dir():
                    shutil.copytree(f, STATE / f.name, dirs_exist_ok=True)
                else:
                    shutil.copy2(f, STATE / f.name)
            log("  ↻ file d'attente récupérée depuis GitHub")
        else:
            log("  ↻ pas encore d'état sur GitHub (premier passage)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def push_new_items(repo: str, ids_before: set[str]) -> int:
    """Ajoute les clips nouvellement planifiés à la file d'attente de GitHub. Renvoie leur nombre."""
    qpath = STATE / "queue.json"
    if not qpath.exists():
        return 0
    local = json.loads(qpath.read_text(encoding="utf-8"))
    new = [i for i in local.get("items", []) if i["id"] not in ids_before]
    if not new:
        log("  (rien de nouveau à envoyer à GitHub)")
        return 0

    url = remote_url(repo)
    for attempt in range(3):
        tmp = Path(tempfile.mkdtemp())
        try:
            work = tmp / "s"
            if _clone_state(url, work):
                shutil.rmtree(work / ".git", ignore_errors=True)
            else:
                work.mkdir(parents=True)
            rq = work / "queue.json"
            remote = json.loads(rq.read_text(encoding="utf-8")) if rq.exists() else {"items": [], "history": []}
            have = {i["id"] for i in remote.get("items", [])}
            remote.setdefault("items", []).extend(i for i in new if i["id"] not in have)
            remote.setdefault("history", [])
            rq.write_text(json.dumps(remote, ensure_ascii=False, indent=1), encoding="utf-8")
            for cmd in (
                ["git", "init", "-q", "-b", "state"],
                ["git", "config", "user.name", "clipbot"],
                ["git", "config", "user.email", "clipbot@users.noreply.github.com"],
                ["git", "add", "-A"],
                ["git", "commit", "-q", "-m", "state (envoi depuis le PC)"],
                ["git", "push", "-q", "-f", url, "state"],
            ):
                run(cmd, cwd=str(work))
            log(f"  ⬆ {len(new)} clip(s) ajouté(s) à la file d'attente GitHub")
            return len(new)
        except Exception as e:  # noqa: BLE001
            log(f"  ⚠ envoi de la file d'attente (essai {attempt + 1}/3) : {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    raise RuntimeError("Impossible d'envoyer la file d'attente à GitHub (les clips sont montés dans out/clips).")
