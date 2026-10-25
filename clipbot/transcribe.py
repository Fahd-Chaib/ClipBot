"""Transcription mot à mot.

Moteur par défaut : Whisper large-v3-turbo via l'API gratuite de Groq (rapide, multilingue).
Repli / option : faster-whisper en local (aucune clé, mais lent sur CPU).

Format de sortie commun :
{"language": "en", "duration": 3600.0,
 "segments": [{"id": 0, "start": 0.0, "end": 4.2, "text": "...",
               "words": [{"w": "Hello", "start": 0.1, "end": 0.4}, ...]}]}
"""
from __future__ import annotations

import time
from pathlib import Path

import requests

from .config import get
from .util import env, log, retry, run

LANG_CODES = {
    "english": "en", "french": "fr", "arabic": "ar", "spanish": "es", "german": "de",
    "portuguese": "pt", "italian": "it", "dutch": "nl", "turkish": "tr", "russian": "ru",
    "hindi": "hi", "japanese": "ja", "korean": "ko", "chinese": "zh",
}
LANG_NAMES = {v: k.capitalize() for k, v in LANG_CODES.items()}


def lang_code(name: str | None) -> str:
    if not name:
        return "en"
    n = name.strip().lower()
    return LANG_CODES.get(n, n[:2])


def _split_audio(audio: Path, out_dir: Path, seconds: int) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("chunk_*.mp3"):
        old.unlink()
    run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(audio), "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "libmp3lame", "-b:a", "48k", "-f", "segment", "-segment_time", str(seconds),
        "-reset_timestamps", "1", str(out_dir / "chunk_%03d.mp3"),
    ])
    return sorted(out_dir.glob("chunk_*.mp3"))


def _groq_chunk(path: Path, model: str, language: str | None) -> dict:
    key = env("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY manquante")

    def call():
        with open(path, "rb") as f:
            data = [
                ("model", (None, model)),
                ("response_format", (None, "verbose_json")),
                ("timestamp_granularities[]", (None, "word")),
                ("timestamp_granularities[]", (None, "segment")),
                ("temperature", (None, "0")),
            ]
            if language:
                data.append(("language", (None, language)))
            r = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files=[("file", (path.name, f, "audio/mpeg")), *data],
                timeout=300,
            )
        if r.status_code == 429:
            wait = float(r.headers.get("retry-after", "30"))
            log(f"  ⏳ quota Groq audio atteint, pause {wait:.0f}s")
            time.sleep(min(wait, 900))
            raise RuntimeError("429 Groq")
        if r.status_code >= 400:
            raise RuntimeError(f"Groq {r.status_code}: {r.text[:300]}")
        return r.json()

    return retry(call, tries=5, base=3, what="transcription Groq")


def _attach_words(segments: list[dict], words: list[dict]) -> list[dict]:
    out, wi = [], 0
    words = sorted(words, key=lambda w: w["start"])
    for i, s in enumerate(segments):
        ws = []
        while wi < len(words) and words[wi]["start"] < s["end"] + 0.05:
            ws.append(words[wi])
            wi += 1
        out.append({"id": i, "start": s["start"], "end": s["end"], "text": s["text"].strip(), "words": ws})
    # mots restants (rare) → dernier segment
    if wi < len(words) and out:
        out[-1]["words"].extend(words[wi:])
    return [s for s in out if s["words"]]


def _transcribe_groq(audio: Path, work: Path, cfg: dict, hint: str | None) -> dict:
    model = get(cfg, "transcription.groq_model", "whisper-large-v3-turbo")
    chunk_s = int(get(cfg, "transcription.chunk_seconds", 600))
    chunks = _split_audio(audio, work / "chunks", chunk_s)
    segments, words, lang = [], [], None
    for idx, ch in enumerate(chunks):
        off = idx * chunk_s
        log(f"  🎙 transcription {idx + 1}/{len(chunks)}")
        j = _groq_chunk(ch, model, hint)
        lang = lang or lang_code(j.get("language"))
        for s in j.get("segments", []):
            segments.append({"start": s["start"] + off, "end": s["end"] + off, "text": s["text"]})
        for w in j.get("words", []):
            words.append({"w": w["word"].strip(), "start": w["start"] + off, "end": w["end"] + off})
    segs = _attach_words(segments, words)
    return {"language": hint or lang or "en", "segments": segs}


def _transcribe_local(audio: Path, cfg: dict, hint: str | None) -> dict:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("pip install -r requirements-local.txt pour le moteur local") from e
    model = WhisperModel(get(cfg, "transcription.local_model", "small"), device="cpu", compute_type="int8")
    gen, info = model.transcribe(str(audio), word_timestamps=True, language=hint, vad_filter=True)
    segs = []
    for i, s in enumerate(gen):
        ws = [{"w": w.word.strip(), "start": w.start, "end": w.end} for w in (s.words or [])]
        if ws:
            segs.append({"id": i, "start": s.start, "end": s.end, "text": s.text.strip(), "words": ws})
    return {"language": hint or info.language, "segments": segs}


def transcribe(audio: Path, work: Path, cfg: dict, hint: str | None = None) -> dict:
    engine = get(cfg, "transcription.engine", "groq")
    if engine == "groq" and env("GROQ_API_KEY"):
        tr = _transcribe_groq(audio, work, cfg, hint)
    else:
        if engine == "groq":
            log("  ⚠ GROQ_API_KEY absente → transcription locale (lente)")
        tr = _transcribe_local(audio, cfg, hint)
    # réindexe proprement
    for i, s in enumerate(tr["segments"]):
        s["id"] = i
    tr["duration"] = tr["segments"][-1]["end"] if tr["segments"] else 0.0
    log(f"  ✓ {len(tr['segments'])} segments, langue={tr['language']}, durée≈{tr['duration'] / 60:.0f} min")
    return tr
