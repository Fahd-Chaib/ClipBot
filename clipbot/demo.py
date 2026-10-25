"""Démo sans réseau : fabrique une fausse vidéo + de faux mots et passe tout le montage
(coupe des silences, zooms, sous-titres, emojis, titre, filigrane) — pour vérifier l'installation."""
from __future__ import annotations

from pathlib import Path

from .render import render_clip
from .util import OUT, WORK, ensure, log, run

TEXTS = {
    "en": (
        "This is a demo of the clip bot. Every word lights up as it is spoken. "
        "Then a long pause gets cut automatically. And the video zooms in on the punchline. "
        "Pretty cool right? Now imagine this running every single Sunday."
    ),
    "ar": (
        "هذا عرض تجريبي للبوت. كل كلمة تضيء عندما تُنطق. "
        "ثم يتم قطع الصمت الطويل تلقائياً. والفيديو يقترب عند الجملة المهمة. "
        "رائع أليس كذلك؟ تخيل هذا يعمل كل يوم أحد."
    ),
}
HOOKS = {"en": "WAIT FOR THE END", "ar": "انتظر حتى النهاية"}


def _fake_words(text: str, gap_after: int = 14):
    words, t = [], 0.6
    for i, w in enumerate(text.split()):
        d = 0.18 + 0.045 * len(w)
        words.append({"w": w, "start": round(t, 3), "end": round(t + d, 3), "sf": w[0].isupper() and i > 0 or w == "هذا"})
        t += d + (1.6 if i == gap_after else 0.04)
    return words, t


def run_demo(cfg: dict, langs=("en", "ar")) -> list[Path]:
    ensure(WORK / "demo", OUT)
    outs = []
    for lang in langs:
        words, end = _fake_words(TEXTS[lang])
        words[0]["sf"] = True
        dur = end + 1.0
        clip = WORK / "demo" / f"source_{lang}.mp4"
        run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30:duration={dur:.2f}",
            "-f", "lavfi", "-i", f"sine=frequency=220:duration={dur:.2f}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip),
        ])
        meta = {
            "overlay_hook": HOOKS[lang],
            "emojis": [{"i": 3, "emoji": "🔥"}, {"i": 12, "emoji": "😱"}, {"i": 20, "emoji": "🚀"}],
            "punch": [9, 17],
        }
        out = OUT / f"demo_{lang}.mp4"
        log(f"▶ démo ({lang})")
        info = render_clip(clip, words, meta, out, cfg, lang, WORK / "demo" / lang)
        log(f"  ✓ {out.name} — {info['duration']:.1f}s, {info['cuts']} coupe(s), {info['scenes']} plan(s)")
        outs.append(out)
    return outs
