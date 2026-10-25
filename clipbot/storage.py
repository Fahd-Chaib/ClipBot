"""Hébergement des clips en attente : assets d'une release GitHub (via la CLI `gh`).

Les clips restent là entre le passage du dimanche et leur heure de publication.
Limite GitHub : 2 Go par fichier — un Short fait quelques dizaines de Mo.
"""
from __future__ import annotations

import os
from pathlib import Path

from .util import log, run


def _gh(args: list[str], check=True):
    return run(["gh", *args], check=check)


def ensure_release(tag: str):
    p = _gh(["release", "view", tag], check=False)
    if p.returncode != 0:
        _gh(
            [
                "release", "create", tag,
                "--title", "ClipBot — file d'attente",
                "--notes", "Clips en attente de publication (géré automatiquement, ne pas supprimer).",
                "--prerelease",
            ]
        )
        log(f"  release « {tag} » créée")


def upload(tag: str, path: Path):
    ensure_release(tag)
    _gh(["release", "upload", tag, str(path), "--clobber"])


def download(tag: str, name: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    _gh(["release", "download", tag, "--pattern", name, "--dir", str(dest_dir), "--clobber"])
    p = dest_dir / name
    if not p.exists():
        raise FileNotFoundError(f"asset introuvable dans la release {tag}: {name}")
    return p


def delete(tag: str, name: str):
    _gh(["release", "delete-asset", tag, name, "--yes"], check=False)


def available() -> bool:
    return bool(os.environ.get("GITHUB_REPOSITORY") or os.environ.get("GH_REPO"))
