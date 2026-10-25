"""Montage d'un clip : 16:9 → 9:16, silences coupés, zooms dynamiques, sous-titres, titre, filigrane.

Entrée : le MP4 de la section (temps 0 = début du téléchargement) + les mots horodatés relatifs à ce fichier.
Le son est reconstruit à partir des mêmes segments que l'image, donc les coupes restent synchrones.
"""
from __future__ import annotations

import bisect
import math
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np

from . import captions as C
from . import framing
from .config import get
from .util import log, run

HARD_CAP = 58.0


# ---------------------------------------------------------------- découpe des silences
def keep_segments(words: list[dict], clip_dur: float, cfg: dict, lead=0.12, tail=0.30):
    """Segments [a, b] (temps du fichier) à conserver : on retire les pauses > silence_cut_min_gap."""
    r = cfg["render"]
    min_gap, keep = float(r["silence_cut_min_gap"]), float(r["silence_keep"])
    segs = []
    a = max(0.0, words[0]["start"] - lead)
    for i in range(len(words) - 1):
        gap = words[i + 1]["start"] - words[i]["end"]
        if gap > min_gap:
            segs.append([a, words[i]["end"] + keep])
            a = words[i + 1]["start"] - keep
    segs.append([a, min(words[-1]["end"] + tail, clip_dur)])
    # plafond de durée totale
    out, total = [], 0.0
    for a, b in segs:
        if b <= a:
            continue
        if total + (b - a) > HARD_CAP:
            b = a + (HARD_CAP - total)
        out.append((a, b))
        total += b - a
        if total >= HARD_CAP:
            break
    return out


def offsets(segs):
    offs, t = [], 0.0
    for a, b in segs:
        offs.append(t)
        t += b - a
    return offs, t


def to_out(t: float, segs, offs) -> float | None:
    for (a, b), o in zip(segs, offs):
        if a - 1e-3 <= t <= b + 1e-3:
            return o + (t - a)
    return None


# ---------------------------------------------------------------- audio
def build_audio(clip: Path, segs, dest: Path):
    parts, labels = [], ""
    for i, (a, b) in enumerate(segs):
        d = b - a
        f = max(min(0.015, d / 4), 0.001)
        parts.append(
            f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={f:.3f},afade=t=out:st={d - f:.3f}:d={f:.3f}[a{i}]"
        )
        labels += f"[a{i}]"
    flt = ";".join(parts) + f";{labels}concat=n={len(segs)}:v=0:a=1,loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(clip), "-filter_complex", flt,
         "-map", "[aout]", "-ar", "48000", "-ac", "2", str(dest)])


def _duration(path: Path) -> float:
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)])
    return float(p.stdout.strip())


# ---------------------------------------------------------------- image
def _vertical(frame, cx, cy, z, W_out, H_out):
    H, W = frame.shape[:2]
    if W / H < (W_out / H_out) * 0.98:  # source déjà verticale/étroite → fond flou
        bg = cv2.GaussianBlur(cv2.resize(frame, (W_out, H_out)), (0, 0), 28)
        fh = int(H * W_out / W)
        fg = cv2.resize(frame, (W_out, fh), interpolation=cv2.INTER_AREA)
        y0 = (H_out - fh) // 2
        bg[max(y0, 0):max(y0, 0) + min(fh, H_out)] = fg[: min(fh, H_out)]
        return bg
    ch = H / z
    cw = ch * W_out / H_out
    if cw > W:
        cw = W
        ch = cw * H_out / W_out
    x0 = min(max(cx - cw / 2, 0), W - cw)
    y0 = min(max(cy - 0.42 * ch, 0), H - ch)
    s = W_out / cw
    M = np.array([[s, 0, -x0 * s], [0, s, -y0 * s]], dtype=np.float32)
    interp = cv2.INTER_CUBIC if s >= 1 else cv2.INTER_AREA
    if s < 1:  # réduction : pré-filtrage pour éviter l'aliasing
        frame = cv2.resize(frame, (int(W * s) + 1, int(H * s) + 1), interpolation=cv2.INTER_AREA)
        M = np.array([[1, 0, -x0 * s], [0, 1, -y0 * s]], dtype=np.float32)
        interp = cv2.INTER_LINEAR
    return cv2.warpAffine(frame, M, (W_out, H_out), flags=interp, borderMode=cv2.BORDER_REPLICATE)


# ---------------------------------------------------------------- rendu principal
def render_clip(clip: Path, words: list[dict], meta: dict, out_path: Path, cfg: dict, lang: str, work: Path) -> dict:
    """words : [{'w','start','end','sf'(bool)}] relatifs à `clip`. meta : sortie de meta.make_meta."""
    r = cfg["render"]
    Wo, Ho = int(r["width"]), int(r["height"])
    zc = r["zoom"]
    work.mkdir(parents=True, exist_ok=True)

    src = framing.probe(clip)
    fps_src = src["fps"]
    clip_dur = _duration(clip)
    segs = keep_segments(words, clip_dur, cfg)
    offs, total = offsets(segs)

    audio = work / "audio.wav"
    build_audio(clip, segs, audio)
    total = min(_duration(audio), HARD_CAP)

    # mots sur la timeline finale (on garde l'index d'origine pour emojis / punchlines)
    ow = []
    for i, w in enumerate(words):
        t0, t1 = to_out(w["start"], segs, offs), to_out(w["end"], segs, offs)
        if t0 is None or t0 >= total:
            continue
        ow.append({"i": i, "w": w["w"], "start": t0, "end": min(t1 if t1 is not None else t0 + 0.2, total), "sf": w.get("sf", False)})
    by_i = {w["i"]: w for w in ow}

    # scènes (coupes de silence + débuts de phrases espacés) → alternance plan large / plan serré
    bounds = [o for o in offs[1:]]
    last = bounds[-1] if bounds else 0.0
    for w in ow:
        if w["sf"] and w["start"] - last >= float(zc["min_scene_seconds"]) and total - w["start"] > 2.5:
            bounds.append(w["start"])
            last = w["start"]
    bounds = sorted(set(bounds))
    punches = [by_i[i]["start"] for i in meta.get("punch", []) if i in by_i]
    emojis = [(by_i[e["i"]]["start"], e["emoji"]) for e in meta.get("emojis", []) if e["i"] in by_i]

    def zoom_at(t):
        k = bisect.bisect_right(bounds, t)
        s0 = bounds[k - 1] if k > 0 else 0.0
        s1 = bounds[k] if k < len(bounds) else total
        base = 1.0 if k % 2 == 0 else float(zc["jump_zoom"])
        z = base + float(zc["push_in"]) * ((t - s0) / max(s1 - s0, 0.5))
        for tp in punches:
            dt = t - tp
            if 0 <= dt < 1.2:
                z += float(zc["punch"]) * (dt / 0.08 if dt < 0.08 else math.exp(-(dt - 0.08) / 0.30))
        return z

    chunks = C.make_chunks(ow, int(r["captions"]["max_words"]))
    chunk_starts = [c[0]["start"] for c in chunks]
    chunk_ends = []
    for k, c in enumerate(chunks):
        nxt = chunks[k + 1][0]["start"] if k + 1 < len(chunks) else total
        chunk_ends.append(min(nxt, c[-1]["end"] + 0.35) if nxt - c[-1]["end"] > 0.4 else nxt)

    # suivi du visage / locuteur
    times, xs, ys = framing.track_faces(clip, float(r["face_sample_fps"]))
    times, xs, ys = framing.face_path(times, xs, ys, src["w"], src["h"])
    cam = framing.Camera(float(np.interp(segs[0][0], times, xs)), float(np.interp(segs[0][0], times, ys)))

    ov = C.Overlay(cfg, lang, Wo, Ho)

    step = max(1, round(fps_src / float(r["max_fps"])))
    out_fps = fps_src / step
    n_out = int(total * out_fps)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{Wo}x{Ho}",
        "-r", f"{out_fps:.5f}", "-i", "-", "-i", str(audio), "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", str(r["preset"]), "-crf", str(r["crf"]), "-pix_fmt", "yuv420p",
        "-profile:v", "high", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        "-t", f"{total:.3f}", str(out_path),
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    errf = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=errf)

    cap = cv2.VideoCapture(str(clip))
    cur_idx, last_frame = -1, None
    seg_i = 0
    title = (meta.get("overlay_hook") or "").strip()
    emoji_dur = float(r["captions"]["emoji_seconds"])
    try:
        for k in range(n_out):
            t_out = k / out_fps
            while seg_i + 1 < len(segs) and t_out >= offs[seg_i + 1]:
                seg_i += 1
            t_src = segs[seg_i][0] + (t_out - offs[seg_i])
            j = max(int(round(t_src * fps_src)), cur_idx if cur_idx >= 0 else 0)
            while cur_idx < j:
                if not cap.grab():
                    break
                cur_idx += 1
                if cur_idx == j:
                    ok, f = cap.retrieve()
                    if ok:
                        last_frame = f
            if last_frame is None:
                break
            tx = float(np.interp(t_src, times, xs))
            ty = float(np.interp(t_src, times, ys))
            z = zoom_at(t_out)
            crop_w = (src["h"] / z) * Wo / Ho
            cx, cy = cam.update(tx, ty, crop_w)
            frame = _vertical(last_frame, cx, cy, z, Wo, Ho)

            ci = bisect.bisect_right(chunk_starts, t_out) - 1
            cap_top = int(Ho * float(r["captions"]["y_ratio"])) - 90
            if 0 <= ci < len(chunks) and t_out < chunk_ends[ci]:
                ch = chunks[ci]
                active = 0
                for wi, w in enumerate(ch):
                    if t_out >= w["start"]:
                        active = wi
                cap_top = ov.draw_caption(frame, [w["w"] for w in ch], active, t_out - ch[0]["start"])
            for te, em in emojis:
                if te <= t_out < te + emoji_dur:
                    ov.draw_emoji(frame, em, t_out - te, emoji_dur, cap_top)
                    break
            ov.draw_title(frame, title, t_out)
            ov.draw_watermark(frame)
            try:
                proc.stdin.write(frame.tobytes())
            except BrokenPipeError:
                break
            if k and k % max(n_out // 4, 1) == 0:
                log(f"    … rendu {100 * k // n_out}%")
    finally:
        cap.release()
        try:
            proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        proc.wait()
    if proc.returncode != 0:
        errf.seek(0)
        raise RuntimeError("ffmpeg a échoué : " + errf.read().decode(errors="ignore")[-800:])
    dur = _duration(out_path)
    return {"path": str(out_path), "duration": dur, "cuts": max(len(segs) - 1, 0), "scenes": len(bounds) + 1}
