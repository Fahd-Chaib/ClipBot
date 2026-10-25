"""Téléchargement via yt-dlp.

Stratégie économe : on récupère d'abord l'AUDIO seul (transcription), puis seulement les SECTIONS
vidéo des clips retenus — jamais la vidéo entière de 2 heures.
"""
from __future__ import annotations

import os
from pathlib import Path

import yt_dlp
from yt_dlp.utils import download_range_func

from .config import get
from .util import log


def _cookies(cfg: dict) -> str | None:
    p = Path(get(cfg, "download.cookies_file", "cookies.txt"))
    return str(p) if p.exists() and p.stat().st_size > 0 else None


def _opts(cfg: dict, **extra) -> dict:
    o = {
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": 4,
    }
    ck = _cookies(cfg)
    if ck:
        o["cookiefile"] = ck
    o.update(extra)
    return o


def _explain(e: Exception) -> Exception:
    msg = str(e)
    if "Sign in to confirm" in msg or "not a bot" in msg:
        return RuntimeError(
            "YouTube bloque le téléchargement depuis ce serveur (« confirm you're not a bot »). "
            "Ajoute / renouvelle tes cookies : python -m clipbot cookies pack cookies.txt → secret YT_COOKIES_B64."
        )
    return e


def fetch_info(url: str, cfg: dict) -> dict:
    try:
        with yt_dlp.YoutubeDL(_opts(cfg, skip_download=True)) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        raise _explain(e)


def download_audio(url: str, dest_dir: Path, cfg: dict) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    for old in dest_dir.glob("audio.*"):
        old.unlink()
    opts = _opts(cfg, format="bestaudio[ext=m4a]/bestaudio/best", outtmpl=str(dest_dir / "audio.%(ext)s"))
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as e:  # noqa: BLE001
        raise _explain(e)
    files = sorted(dest_dir.glob("audio.*"))
    if not files:
        raise RuntimeError("audio non téléchargé")
    return files[0]


def download_section(url: str, start: float, end: float, dest_stem: Path, cfg: dict) -> Path:
    """Télécharge uniquement [start, end] (secondes) en MP4 ≤ max_height."""
    h = int(get(cfg, "download.max_height", 1080))
    dest_stem.parent.mkdir(parents=True, exist_ok=True)
    for old in dest_stem.parent.glob(dest_stem.name + ".*"):
        old.unlink()
    opts = _opts(
        cfg,
        format=(
            f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]/b"
        ),
        outtmpl=str(dest_stem) + ".%(ext)s",
        merge_output_format="mp4",
        download_ranges=download_range_func(None, [(max(0.0, start), end)]),
        force_keyframes_at_cuts=True,
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as e:  # noqa: BLE001
        raise _explain(e)
    files = [p for p in dest_stem.parent.glob(dest_stem.name + ".*") if p.suffix in (".mp4", ".mkv", ".webm")]
    if not files:
        raise RuntimeError("section vidéo non téléchargée")
    log(f"  ⬇ section {start:.1f}-{end:.1f}s → {files[0].name} ({os.path.getsize(files[0]) / 1e6:.1f} Mo)")
    return files[0]
