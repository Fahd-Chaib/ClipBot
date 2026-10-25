"""Habillage dessiné avec Pillow : sous-titres mot par mot (mot actif en couleur), emojis en couleur,
titre accrocheur, filigrane. Gère l'arabe (liaison des lettres + sens droite→gauche)."""
from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .config import get

ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def is_rtl(text: str) -> bool:
    return bool(ARABIC_RE.search(text))


def _has_raqm() -> bool:
    from PIL import features

    return bool(features.check("raqm"))


def shape(text: str) -> str:
    """Avec raqm, Pillow gère lui-même liaison des lettres + sens RTL : on ne touche à rien.
    Sans raqm, on fait le travail avec arabic-reshaper + python-bidi."""
    if not is_rtl(text) or _has_raqm():
        return text
    import arabic_reshaper

    try:
        from bidi.algorithm import get_display
    except ImportError:  # python-bidi >= 0.6
        from bidi import get_display
    return get_display(arabic_reshaper.reshape(text))


def _fc(pattern: str, family: str) -> str | None:
    try:
        p = subprocess.run(
            ["fc-match", "-f", "%{family}|%{file}", pattern], capture_output=True, text=True, timeout=10
        )
        fam, _, file = p.stdout.partition("|")
        if family.lower() in fam.lower() and Path(file).exists():
            return file
    except Exception:  # noqa: BLE001
        pass
    return None


@lru_cache(maxsize=8)
def find_font(custom: str, rtl: bool) -> str:
    if custom and Path(custom).exists():
        return custom
    prefs = (
        [("Noto Sans Arabic:bold", "Noto Sans Arabic"), ("Noto Naskh Arabic:bold", "Noto Naskh"), ("Cairo:bold", "Cairo")]
        if rtl
        else [("Montserrat:extrabold", "Montserrat"), ("Poppins:bold", "Poppins"), ("Noto Sans:black", "Noto Sans"),
              ("Inter:extrabold", "Inter"), ("DejaVu Sans:bold", "DejaVu Sans")]
    )
    for pattern, fam in prefs:
        f = _fc(pattern, fam)
        if f:
            return f
    fallbacks = [
        "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf", DEJAVU,
    ]
    for cand in fallbacks:
        if Path(cand).exists():
            return cand
    return ""


@lru_cache(maxsize=1)
def find_emoji_font() -> str | None:
    f = _fc("Noto Color Emoji", "Noto Color Emoji")
    if f:
        return f
    # Windows / macOS : on télécharge une fois la police d'emojis en couleur (Noto, licence libre)
    from .util import WORK

    dest = WORK / "fonts" / "NotoColorEmoji.ttf"
    if dest.exists() and dest.stat().st_size > 1_000_000:
        return str(dest)
    try:
        import requests

        r = requests.get("https://github.com/googlefonts/noto-emoji/raw/main/fonts/NotoColorEmoji.ttf", timeout=120)
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return str(dest)
    except Exception:  # noqa: BLE001
        return None


@lru_cache(maxsize=32)
def _font(path: str, size: int):
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _to_arrays(img: Image.Image):
    arr = np.asarray(img.convert("RGBA"))
    bgr = arr[..., [2, 1, 0]].astype(np.float32)
    alpha = arr[..., 3].astype(np.float32) / 255.0
    return bgr, alpha


def blend(frame: np.ndarray, bgr: np.ndarray, alpha: np.ndarray, x: int, y: int, gain: float = 1.0):
    h, w = alpha.shape
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + w, frame.shape[1]), min(y + h, frame.shape[0])
    if x1 <= x0 or y1 <= y0 or gain <= 0:
        return
    a = (alpha[y0 - y:y1 - y, x0 - x:x1 - x] * gain)[..., None]
    src = bgr[y0 - y:y1 - y, x0 - x:x1 - x]
    roi = frame[y0:y1, x0:x1]
    roi[:] = (roi * (1.0 - a) + src * a).astype(np.uint8)


def ease_out(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def make_chunks(words: list[dict], max_words: int, gap: float = 0.45) -> list[list[dict]]:
    chunks, cur = [], []
    for w in words:
        brk = cur and (
            len(cur) >= max_words
            or w["start"] - cur[-1]["end"] > gap
            or w.get("sf")
            or re.search(r"[.!?…،؟]$", cur[-1]["w"])
        )
        if brk:
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


class Overlay:
    def __init__(self, cfg: dict, lang: str, W: int, H: int):
        self.cfg, self.lang, self.W, self.H = cfg, lang, W, H
        self.rtl = lang == "ar"
        self.c = cfg["render"]["captions"]
        self.font_path = find_font(get(cfg, "render.font", "") or "", self.rtl)
        self.upper = bool(self.c.get("uppercase", True)) and not self.rtl
        self._chunk_cache: dict = {}
        self._emoji_cache: dict = {}
        self._title = None
        self._wm = None

    # -------- sous-titres ----------
    def _txt(self, s: str) -> str:
        return shape(s.upper() if self.upper else s)

    def chunk(self, texts: tuple[str, ...], active: int):
        key = (texts, active)
        if key in self._chunk_cache:
            return self._chunk_cache[key]
        size = int(self.c["size"])
        stroke = int(self.c["stroke"])
        shaped = [self._txt(t) for t in texts]
        for _ in range(8):  # réduit la taille si la ligne déborde
            font = _font(self.font_path, size)
            space = size * 0.30
            widths = [font.getlength(s) for s in shaped]
            total = sum(widths) + space * (len(shaped) - 1)
            if total + 2 * stroke <= self.W * 0.90:
                break
            size = int(size * 0.88)
        asc, desc = font.getmetrics()
        pad = stroke + 14
        cw, ch = int(total + 2 * pad), int(asc + desc + 2 * pad)
        img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        white = hex_rgb(self.c["color"]) + (255,)
        accent = hex_rgb(self.c["accent"]) + (255,)
        order = list(range(len(shaped)))
        x = pad if not self.rtl else cw - pad
        pos = {}
        for i in order:  # RTL : le 1er mot (logique) est à droite
            if self.rtl:
                x -= widths[i]
                pos[i] = x
                x -= space
            else:
                pos[i] = x
                x += widths[i] + space
        for i in order:
            d.text((pos[i], pad + 6), shaped[i], font=font, fill=(0, 0, 0, 150), stroke_width=stroke, stroke_fill=(0, 0, 0, 150))
        for i in order:
            d.text(
                (pos[i], pad), shaped[i], font=font, fill=accent if i == active else white,
                stroke_width=stroke, stroke_fill=(0, 0, 0, 255),
            )
        out = _to_arrays(img)
        self._chunk_cache[key] = out
        return out

    def draw_caption(self, frame, texts, active, t_in_chunk: float):
        bgr, alpha = self.chunk(tuple(texts), active)
        s = 0.82 + 0.18 * ease_out(t_in_chunk / 0.12)  # petit « pop » à l'apparition
        if s < 0.999:
            h, w = alpha.shape
            nw, nh = max(1, int(w * s)), max(1, int(h * s))
            bgr = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA)
            alpha = cv2.resize(alpha, (nw, nh), interpolation=cv2.INTER_AREA)
        h, w = alpha.shape
        cx, cy = self.W // 2, int(self.H * float(self.c["y_ratio"]))
        blend(frame, bgr, alpha, cx - w // 2, cy - h // 2)
        return cy - h // 2

    # -------- emojis ----------
    def emoji(self, e: str):
        if e in self._emoji_cache:
            return self._emoji_cache[e]
        res = None
        path = find_emoji_font()
        if path:
            try:
                f = ImageFont.truetype(path, 109)
                img = Image.new("RGBA", (136, 128), (0, 0, 0, 0))
                ImageDraw.Draw(img).text((0, 0), e, font=f, embedded_color=True)
                bbox = img.getbbox()
                if bbox:
                    img = img.crop(bbox)
                size = int(self.c["emoji_size"])
                r = size / max(img.size)
                img = img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))), Image.LANCZOS)
                res = _to_arrays(img)
            except Exception:  # noqa: BLE001
                res = None
        self._emoji_cache[e] = res
        return res

    def draw_emoji(self, frame, e: str, t_in: float, dur: float, caption_top: int):
        arr = self.emoji(e)
        if not arr:
            return
        bgr, alpha = arr
        s = 0.6 + 0.4 * ease_out(t_in / 0.18)
        gain = min(1.0, (dur - t_in) / 0.2)
        h, w = alpha.shape
        nw, nh = max(1, int(w * s)), max(1, int(h * s))
        bgr = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA)
        alpha = cv2.resize(alpha, (nw, nh), interpolation=cv2.INTER_AREA)
        blend(frame, bgr, alpha, self.W // 2 - nw // 2, caption_top - nh - 14, gain)

    # -------- titre ----------
    def _build_title(self, text: str):
        tc = self.cfg["render"]["title"]
        size = int(tc["size"])
        font = _font(self.font_path, size)
        maxw = int(self.W * 0.84)
        words = (text.upper() if self.upper else text).split()
        lines, cur = [], []
        for w in words:
            trial = " ".join(cur + [w])
            if cur and font.getlength(shape(trial)) > maxw:
                lines.append(cur)
                cur = [w]
            else:
                cur.append(w)
        if cur:
            lines.append(cur)
        lines = [" ".join(l) for l in lines[:3]]
        shaped = [shape(l) for l in lines]
        asc, desc = font.getmetrics()
        lh = asc + desc + 6
        tw = int(max(font.getlength(s) for s in shaped))
        padx, pady = 38, 26
        bw, bh = tw + 2 * padx, lh * len(shaped) + 2 * pady
        img = Image.new("RGBA", (bw + 16, bh + 22), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((8, 16, bw + 8, bh + 16), 30, fill=(0, 0, 0, 110))  # ombre
        d.rounded_rectangle((8, 8, bw + 8, bh + 8), 30, fill=hex_rgb(tc["bg"]) + (255,))
        for i, s in enumerate(shaped):
            lw = font.getlength(s)
            d.text((8 + (bw - lw) / 2, 8 + pady + i * lh), s, font=font, fill=hex_rgb(tc["fg"]) + (255,))
        return _to_arrays(img)

    def draw_title(self, frame, text: str, t: float):
        tc = self.cfg["render"]["title"]
        dur = float(tc["seconds"])
        if not text or t < 0 or t > dur:
            return
        if self._title is None:
            self._title = self._build_title(text)
        bgr, alpha = self._title
        h, w = alpha.shape
        slide = (1 - ease_out(t / 0.28)) * -50
        gain = min(1.0, t / 0.2, (dur - t) / 0.3)
        blend(frame, bgr, alpha, self.W // 2 - w // 2, int(tc["y"] + slide), max(gain, 0))

    # -------- filigrane ----------
    def draw_watermark(self, frame):
        wm = self.cfg["render"]["watermark"]
        if not wm.get("enabled"):
            return
        if self._wm is None:
            text = self.cfg["channel"]["handle"]
            font = _font(find_font(get(self.cfg, "render.font", "") or "", is_rtl(text)), int(wm["size"]))
            shaped = shape(text)
            wpx = int(font.getlength(shaped)) + 20
            img = Image.new("RGBA", (wpx, int(wm["size"] * 1.6)), (0, 0, 0, 0))
            ImageDraw.Draw(img).text((10, 6), shaped, font=font, fill=(255, 255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0, 255))
            self._wm = _to_arrays(img)
        bgr, alpha = self._wm
        blend(frame, bgr, alpha, int(wm["x"]), int(wm["y"]), float(wm["opacity"]))
