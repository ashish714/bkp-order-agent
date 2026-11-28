"""Smart cuts from one continuous clip (dance, talking, performance).

The clip plays in real time from start to finish, so the voice and music
recorded with it stay perfectly in sync. The "edit" is how it's framed:
cuts on the beat switch between a wide shot, a medium shot and a close-up,
each centred on where the movement is (the dancers / the speaker).

1. Read the clip once at low resolution and find, for every moment, the box
   around the movement (frame differences).
2. Find the beat in the clip's own sound (or the uploaded music).
3. Cut every few beats; pick a framing for each piece that keeps everyone who
   is moving inside the frame, and never repeats the previous framing.
"""
from __future__ import annotations

import subprocess

import numpy as np

from .beats import Beats, detect_beats
from .media import Asset, Shot

SAMPLE_FPS = 4
SAMPLE_W = 160

FRAMINGS = {"wide": 1.0, "medium": 1.3, "close": 1.7}
PATTERN = ["wide", "medium", "close", "medium", "wide", "close"]


def motion_track(path, start: float, seconds: float, aspect: float) -> tuple[np.ndarray, float, float]:
    """Per sample: (cx, cy, w, h) of the moving area, normalised 0..1 in the *output-cropped* frame.

    aspect = output width / height. The source is first cover-cropped to that
    aspect (what the wide shot shows), so coordinates match the wide shot.
    """
    res = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", str(path),
         "-vf", f"fps={SAMPLE_FPS},scale={SAMPLE_W}:-2,format=gray", "-f", "rawvideo", "-"],
        capture_output=True,
    )
    probe = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", str(path), "-frames:v", "1",
         "-vf", f"scale={SAMPLE_W}:-2", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True,
    )
    fw = SAMPLE_W
    fh = max(2, len(probe.stdout) // fw)
    raw = np.frombuffer(res.stdout, dtype=np.uint8)
    n = len(raw) // (fw * fh)
    if n < 2:
        return np.tile([0.5, 0.45, 1.0, 1.0], (1, 1)), fw, fh
    frames = raw[:n * fw * fh].reshape(n, fh, fw).astype(np.float32)

    # crop columns/rows to the output aspect (centre cover-crop, like the wide shot)
    if fw / fh > aspect:
        cw = int(fh * aspect)
        x0 = (fw - cw) // 2
        frames = frames[:, :, x0:x0 + cw]
    else:
        ch = int(fw / aspect)
        y0 = (fh - ch) // 2
        frames = frames[:, y0:y0 + ch, :]
    _, H, W = frames.shape

    track = []
    for i in range(n):
        a, b = frames[max(i - 1, 0)], frames[min(i + 1, n - 1)]
        diff = np.abs(b - a)
        diff[diff < 12] = 0
        total = diff.sum()
        if total < 50:
            track.append(track[-1] if track else [0.5, 0.45, 1.0, 1.0])
            continue
        cols, rows = diff.sum(axis=0), diff.sum(axis=1)

        def span(v):
            c = np.cumsum(v) / v.sum()
            lo, hi = np.searchsorted(c, 0.05), np.searchsorted(c, 0.95)
            return lo / len(v), (hi + 1) / len(v)

        x_lo, x_hi = span(cols)
        y_lo, y_hi = span(rows)
        track.append([(x_lo + x_hi) / 2, (y_lo + y_hi) / 2, x_hi - x_lo, y_hi - y_lo])
    return np.array(track), W, H


def _window(track: np.ndarray, a: float, b: float, offset: float) -> tuple[float, float, float, float]:
    i0 = max(0, int((a - offset) * SAMPLE_FPS))
    i1 = max(i0 + 1, int(np.ceil((b - offset) * SAMPLE_FPS)))
    seg = track[i0:i1] if i0 < len(track) else track[-1:]
    # union-ish box over the window: median centre, generous extent
    cx, cy = float(np.median(seg[:, 0])), float(np.median(seg[:, 1]))
    w = float(np.percentile(seg[:, 2], 85))
    h = float(np.percentile(seg[:, 3], 85))
    return cx, cy, w, h


def framing_for(name: str, box: tuple[float, float, float, float]) -> tuple[float, tuple[float, float]]:
    """Zoom and focus for a framing, shrinking the zoom so the moving area stays in frame."""
    cx, cy, w, h = box
    zoom = FRAMINGS[name]
    if name != "wide":
        # keep the moving box (plus 15% margin) inside the crop
        fit = min(1 / max(w * 1.15, 1e-3), 1 / max(h * 1.15, 1e-3))
        zoom = max(1.0, min(zoom, fit))
    # aim a little above the centre of the movement (faces / upper bodies read better)
    fy = cy - (0.08 * h if name == "close" else 0.03 * h)
    half = 0.5 / zoom
    fx = min(max(cx, half), 1 - half)
    fy = min(max(fy, half), 1 - half)
    return round(zoom, 3), (round(fx, 3), round(fy, 3))


def continuous_shots(asset: Asset, plan, music: str | None = None) -> tuple[list[Shot], float, Beats | None]:
    """Shots covering one continuous stretch of the clip. Returns (shots, clip_start, beats)."""
    W, H = plan.size
    clip_start = 0.3 if asset.duration > plan.duration + 0.6 else 0.0   # skip the record-button tap
    total = min(plan.duration, asset.duration - clip_start)
    total = max(total, 1.0)

    # beats: the uploaded music if there is one, otherwise the clip's own sound
    beats = None
    try:
        beats = detect_beats(music if music else asset.path)
    except Exception:
        beats = None
    if beats and beats.confidence < 0.4:
        beats = None

    shot_len = plan.shot_length
    if beats:
        k = max(1, round(shot_len / beats.period))
        # beat times are on the song's clock; with the clip's own sound they're clip times
        base = 0.0 if music else clip_start
        cuts = [t - base for t in beats.times[::k] if 0.8 < t - base < total - 0.8]
    else:
        cuts = list(np.arange(shot_len, total - 0.8, shot_len))
    bounds = [0.0] + [float(c) for c in cuts] + [total]

    track, _, _ = motion_track(asset.path, clip_start, total, W / H)

    shots: list[Shot] = []
    prev = None
    for i, (a, b) in enumerate(zip(bounds[:-1], bounds[1:])):
        name = PATTERN[i % len(PATTERN)]
        if i == 0 or i == len(bounds) - 2:
            name = "wide"                        # open and close wide
        if name == prev:
            name = "medium" if name != "medium" else "wide"
        box = _window(track, clip_start + a, clip_start + b, clip_start)
        zoom, focus = framing_for(name, box)
        s = Shot(asset, round(b - a, 3), round(clip_start + a, 3), 1.0, zoom, focus)
        shots.append(s)
        prev = name
    return shots, clip_start, beats
