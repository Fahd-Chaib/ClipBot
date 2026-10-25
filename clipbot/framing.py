"""Recadrage vertical avec suivi du visage ET du locuteur.

- détection de visages (Haar, OpenCV) à ~5 images/s sur une image réduite ;
- plusieurs visages : le locuteur est celui dont la bouche bouge le plus (différence d'image sur la zone
  de la bouche), avec hystérésis pour ne pas sauter de l'un à l'autre ;
- trajectoire lissée puis caméra « virtuelle » avec zone morte (pas de tremblement) et rattrapage rapide
  quand le locuteur change.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

_CASCADE = None


def _cascade():
    global _CASCADE
    if _CASCADE is None:
        _CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return _CASCADE


def probe(path: Path) -> dict:
    cap = cv2.VideoCapture(str(path))
    info = {
        "w": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "h": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": cap.get(cv2.CAP_PROP_FPS) or 30.0,
    }
    cap.release()
    return info


def _mouth(gray, x, y, w, h):
    y0, y1 = int(y + 0.62 * h), int(y + 0.95 * h)
    x0, x1 = int(x + 0.22 * w), int(x + 0.78 * w)
    roi = gray[max(y0, 0):max(y1, 1), max(x0, 0):max(x1, 1)]
    if roi.size == 0:
        return None
    return cv2.resize(roi, (24, 12)).astype(np.float32)


def track_faces(path: Path, sample_fps: float = 5.0, det_w: int = 960):
    """Renvoie (times, xs, ys) en pixels de la vidéo source ; NaN = aucun visage."""
    cap = cv2.VideoCapture(str(path))
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(fps / sample_fps))
    scale = det_w / W
    det_h = max(1, int(H * scale))
    min_face = max(24, int(det_w * 0.05))
    casc = _cascade()

    tracks: list[dict] = []
    speaker = None
    times, xs, ys = [], [], []
    idx = 0
    next_id = 0
    while True:
        if not cap.grab():
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            gray = cv2.cvtColor(cv2.resize(frame, (det_w, det_h)), cv2.COLOR_BGR2GRAY)
            faces = casc.detectMultiScale(gray, 1.1, 5, minSize=(min_face, min_face))
            for t in tracks:
                t["seen"] = False
            used = set()
            for (x, y, w, h) in faces:
                cx, cy = x + w / 2, y + h / 2
                best, bd = None, det_w * 0.2
                for t in tracks:
                    d = abs(t["cx"] - cx)
                    if d < bd and id(t) not in used:
                        best, bd = t, d
                m = _mouth(gray, x, y, w, h)
                if best is None:
                    best = {"id": next_id, "cx": cx, "cy": cy, "act": 0.0, "mouth": m, "miss": 0}
                    next_id += 1
                    tracks.append(best)
                else:
                    if m is not None and best["mouth"] is not None:
                        diff = float(np.mean(np.abs(m - best["mouth"])))
                        best["act"] = 0.6 * best["act"] + 0.4 * diff
                    best["mouth"] = m
                    best["cx"] = 0.5 * best["cx"] + 0.5 * cx
                    best["cy"] = 0.5 * best["cy"] + 0.5 * cy
                best["seen"] = True
                best["miss"] = 0
                used.add(id(best))
            for t in tracks:
                if not t.get("seen"):
                    t["miss"] += 1
            tracks = [t for t in tracks if t["miss"] <= sample_fps * 2]

            vis = [t for t in tracks if t.get("seen")]
            cur = next((t for t in vis if t["id"] == speaker), None)
            if vis:
                top = max(vis, key=lambda t: t["act"])
                if cur is None or (top is not cur and top["act"] > 1.4 * cur["act"] + 1.0):
                    cur = top
                    speaker = cur["id"]
            else:
                cur = None
            times.append(idx / fps)
            xs.append(cur["cx"] / scale if cur else np.nan)
            ys.append(cur["cy"] / scale if cur else np.nan)
        idx += 1
    cap.release()
    return np.array(times), np.array(xs, dtype=float), np.array(ys, dtype=float)


def _ffill(a: np.ndarray, default: float) -> np.ndarray:
    a = a.copy()
    if a.size == 0:
        return a
    if np.all(np.isnan(a)):
        a[:] = default
        return a
    first = a[~np.isnan(a)][0]
    a[0] = first if np.isnan(a[0]) else a[0]
    for i in range(1, len(a)):
        if np.isnan(a[i]):
            a[i] = a[i - 1]
    return a


def _smooth(a: np.ndarray) -> np.ndarray:
    if len(a) < 5:
        return a
    pad = np.pad(a, 2, mode="edge")
    med = np.array([np.median(pad[i:i + 5]) for i in range(len(a))])
    pad2 = np.pad(med, 1, mode="edge")
    return (pad2[:-2] + pad2[1:-1] + pad2[2:]) / 3


def face_path(times, xs, ys, W: int, H: int):
    if len(times) == 0:
        return np.array([0.0, 1.0]), np.array([W / 2, W / 2]), np.array([0.42 * H] * 2)
    xs = _smooth(_ffill(xs, W / 2))
    ys = _smooth(_ffill(ys, 0.42 * H))
    return times, xs, ys


class Camera:
    """Caméra virtuelle : zone morte + rattrapage proportionnel (plus rapide quand l'écart est grand)."""

    def __init__(self, x: float, y: float):
        self.x, self.y = x, y

    def update(self, tx: float, ty: float, crop_w: float) -> tuple[float, float]:
        dx = tx - self.x
        dead = 0.05 * crop_w
        if abs(dx) > 0.30 * crop_w:
            self.x += dx * 0.28
        elif abs(dx) > dead:
            self.x += (dx - np.sign(dx) * dead) * 0.10
        self.y += (ty - self.y) * 0.06
        return self.x, self.y
