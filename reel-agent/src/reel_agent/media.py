"""Find the photos and clips in a folder, measure them, and pick the shots.

For each video we sample a few windows and keep the sharpest one (blur and
heavy shake both lower sharpness). Photos that are much blurrier than the
rest are dropped when there are more photos than the reel needs.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

try:  # iPhone photos
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:  # optional
    pass

WORKERS = max(1, min(4, (os.cpu_count() or 2) // 2))

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp"}


@dataclass
class Asset:
    path: Path
    kind: str                 # "photo" | "video"
    duration: float = 0.0     # videos only
    sharpness: float = 0.0
    start: float = 0.0        # chosen in-point for videos

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class Shot:
    asset: Asset
    length: float             # seconds on screen
    start: float = 0.0
    speed: float = 1.0
    zoom: float = 1.0                      # 1 = full frame; 1.3 = medium; 1.7 = close-up
    focus: tuple[float, float] = (0.5, 0.45)  # where to centre a zoomed crop (0..1)


def find_media(folder: str | Path) -> list[Asset]:
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"No such folder: {folder}")
    assets = []
    for p in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
        ext = p.suffix.lower()
        if p.name.startswith("."):
            continue
        if ext in PHOTO_EXT:
            assets.append(Asset(p, "photo"))
        elif ext in VIDEO_EXT:
            assets.append(Asset(p, "video", duration=probe_duration(p)))
    return assets


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(json.loads(out.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return 0.0


def sharpness(img: Image.Image) -> float:
    """Blur score: how much the image changes when blurred again.

    A sharp image loses lots of fine detail when blurred; an already-blurry
    (or motion-smeared) one barely changes. Dividing by the image's contrast
    keeps busy and plain scenes comparable.
    """
    g = ImageOps.grayscale(img)
    g.thumbnail((480, 480))
    diff = ImageChops.difference(g, g.filter(ImageFilter.GaussianBlur(1.5)))
    detail = ImageStat.Stat(diff).mean[0]
    contrast = ImageStat.Stat(g).stddev[0]
    return 1000 * detail / (contrast + 8)


def frame_at(path: Path, t: float) -> Image.Image | None:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "f.jpg"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
             "-vf", "scale=480:-2", "-y", str(out)],
            capture_output=True,
        )
        if not out.exists():
            return None
        with Image.open(out) as im:
            return im.copy()


def score(asset: Asset, window: float) -> None:
    """Fill in sharpness (and, for video, the best start time)."""
    if asset.kind == "photo":
        try:
            with Image.open(asset.path) as im:
                asset.sharpness = sharpness(ImageOps.exif_transpose(im))
        except OSError:
            asset.sharpness = -1  # unreadable
        return
    usable = max(asset.duration - window, 0.0)
    starts = [usable * f for f in (0.15, 0.4, 0.65, 0.9)] if usable > 0.5 else [0.0]
    best = (-1.0, 0.0)
    for s in starts:
        frames = [frame_at(asset.path, s + window * f) for f in (0.25, 0.75)]
        vals = [sharpness(f) for f in frames if f is not None]
        if vals and min(vals) > best[0]:
            best = (min(vals), s)  # min: penalise a window that's blurry at either end
    asset.sharpness, asset.start = best


def pick_shots(assets: list[Asset], plan) -> list[Shot]:
    """Choose shots and their lengths so the montage lands near plan.duration."""
    assets = [a for a in assets if a.kind == "photo" or a.duration >= 0.6]
    if not assets:
        raise ValueError("No usable photos or videos found.")

    shot_len = plan.shot_length
    # score files in parallel (each one is mostly ffmpeg decoding)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(lambda a: score(a, shot_len * plan.speed_for(a.name)), assets))
    assets = [a for a in assets if a.sharpness >= 0]

    trans = 0.0 if plan.transition == "cut" else plan.transition_duration
    wanted = max(1, round((plan.duration - trans) / (shot_len - trans)))

    if plan.drop_blurry and len(assets) > 2:
        # Clearly blurry (far below the folder's median sharpness): always drop,
        # as long as at least two shots remain.
        med = sorted(a.sharpness for a in assets)[len(assets) // 2]
        sharp = [a for a in assets if a.sharpness >= 0.12 * med]
        assets = sharp if len(sharp) >= 2 else assets
    if len(assets) > wanted:
        # More than we need: drop the least sharp, keep filename order for the story.
        worst = sorted(assets, key=lambda a: a.sharpness)[:len(assets) - wanted]
        assets = [a for a in assets if a not in worst]

    # Fewer files than shots wanted: cut several pieces from long videos
    # (in time order, not overlapping) before stretching anything.
    pieces = {a.name: 1 for a in assets}
    base_len = max(1.0, trans * 2 + 0.3, shot_len)

    def room(a: Asset) -> float:
        sp = plan.speed_for(a.name)
        return a.duration / (pieces[a.name] + 1) / sp if a.kind == "video" else 0.0

    while sum(pieces.values()) < wanted:
        best = max(assets, key=room)
        if room(best) < base_len * 1.05:
            break
        pieces[best.name] += 1

    n = sum(pieces.values())
    length = (plan.duration + (n - 1) * trans) / n
    length = min(max(length, max(1.0, trans * 2 + 0.3)), shot_len * 2)

    shots = []
    for a in assets:
        sp = plan.speed_for(a.name)
        k = pieces[a.name]
        if a.kind != "video":
            shots.append(Shot(a, round(length, 3), 0.0, sp))
            continue
        if k == 1:
            L = length
            if a.duration < L * sp:
                L = max(a.duration / sp, trans * 2 + 0.3)
            start = min(a.start, max(a.duration - L * sp, 0.0))
            shots.append(Shot(a, round(L, 3), round(start, 3), sp))
            continue
        seg = a.duration / k
        L = min(length, seg / sp)
        for j in range(k):
            start = j * seg + max(0.0, (seg - L * sp) / 2)
            shots.append(Shot(a, round(L, 3), round(start, 3), sp))
    return shots
