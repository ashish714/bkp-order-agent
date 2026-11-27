"""Render an EditPlan + shots into an MP4 with ffmpeg.

Steps:
1. Each shot becomes a short, normalised clip (same size, 30 fps, color look
   applied). Photos get a slow zoom or pan; videos are trimmed (and slowed).
2. The end card is turned into a clip too.
3. One final ffmpeg pass joins everything with transitions, lays the text
   overlays on top, and adds music (or silence, so Instagram accepts it).
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from . import overlays
from .media import Shot
from .plan import EditPlan

FPS = 30
TRANS = 0.4
CUT = 0.04  # "hard cut": a crossfade too short to see
END_CARD_SECONDS = 2.6

LOOK_FILTERS = {
    "natural": "eq=saturation=1.05",
    "warm": "colorbalance=rs=0.07:gs=0.02:bs=-0.08:rm=0.04:bm=-0.04,eq=saturation=1.1:contrast=1.03",
    "cool": "colorbalance=rs=-0.05:bs=0.07:bm=0.04,eq=saturation=0.95:contrast=1.04",
    "moody": "eq=contrast=1.15:brightness=-0.04:saturation=0.8,colorbalance=bs=0.05:bh=0.03",
    "vibrant": "eq=saturation=1.35:contrast=1.06",
    "bw": "hue=s=0,eq=contrast=1.12",
    "clean": "eq=contrast=1.04:brightness=0.02:saturation=1.05",
}
XFADE = {"fade": "fade", "slide": "slideleft", "zoom": "zoomin", "wipe": "wipeleft"}


class RenderError(RuntimeError):
    pass


def _run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        tail = "\n".join(res.stderr.strip().splitlines()[-15:])
        raise RenderError(f"ffmpeg failed:\n{tail}")


def _cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(img, size, Image.LANCZOS, centering=(0.5, 0.45))


def _photo_segment(shot: Shot, idx: int, plan: EditPlan, out: Path, tmp: Path) -> None:
    W, H = plan.size
    frames = max(2, round(shot.length * FPS))
    # Pre-render the photo at 2x with EXIF rotation applied (ffmpeg ignores EXIF on JPEGs).
    big = tmp / f"photo_{idx}.png"
    with Image.open(shot.asset.path) as im:
        _cover(ImageOps.exif_transpose(im).convert("RGB"), (W * 2, H * 2)).save(big)
    n = frames
    motions = [
        # zoom in, centred
        f"z='1+0.12*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
        # pan left to right at a fixed zoom
        f"z='1.15':x='(iw-iw/zoom)*on/{n}':y='ih/2-(ih/zoom/2)'",
        # zoom out
        f"z='1.14-0.12*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
        # pan right to left, slight upward drift
        f"z='1.15':x='(iw-iw/zoom)*(1-on/{n})':y='(ih-ih/zoom)*(0.6-0.2*on/{n})'",
    ]
    vf = (f"zoompan={motions[idx % len(motions)]}:d={n}:s={W}x{H}:fps={FPS},"
          f"{LOOK_FILTERS[plan.look]},setsar=1,format=yuv420p")
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(big), "-vf", vf, "-frames:v", str(frames),
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)])


def _video_segment(shot: Shot, plan: EditPlan, out: Path) -> None:
    W, H = plan.size
    frames = max(2, round(shot.length * FPS))
    src_len = shot.length * plan.speed + 0.2
    vf = (f"setpts=PTS/{plan.speed},scale={W}:{H}:force_original_aspect_ratio=increase,"
          f"crop={W}:{H},fps={FPS},{LOOK_FILTERS[plan.look]},setsar=1,format=yuv420p,"
          f"tpad=stop_mode=clone:stop_duration=1")  # pad if the source runs short
    _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{shot.start:.3f}", "-t", f"{src_len:.3f}",
          "-i", str(shot.asset.path), "-vf", vf, "-an", "-frames:v", str(frames),
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)])


def _still_segment(png: Path, seconds: float, out: Path) -> None:
    _run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(png),
          "-t", f"{seconds:.3f}", "-vf", "setsar=1,format=yuv420p",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)])


def timeline(shots: list[Shot], plan: EditPlan) -> dict:
    """Start time of every shot on the final timeline, plus totals."""
    t = CUT if plan.transition == "cut" else TRANS
    starts, pos = [], 0.0
    for s in shots:
        starts.append(pos)
        pos += s.length - t
    montage = pos + t
    total = montage + (END_CARD_SECONDS - TRANS if plan.end_card else 0.0)
    return {"starts": starts, "montage": montage, "total": total, "trans": t}


def render(shots: list[Shot], plan: EditPlan, out_path: str | Path, music: str | None = None,
           logo: str | None = None, font_path: str | None = None, keep_temp: bool = False) -> Path:
    if not shutil.which("ffmpeg"):
        raise RenderError("ffmpeg is not installed. Mac: `brew install ffmpeg`. Windows: `winget install ffmpeg`.")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="reel_"))
    size = plan.size
    try:
        segs = []
        for i, shot in enumerate(shots):
            seg = tmp / f"seg_{i:03d}.mp4"
            if shot.asset.kind == "photo":
                _photo_segment(shot, i, plan, seg, tmp)
            else:
                _video_segment(shot, plan, seg)
            segs.append((seg, shot.length))

        tl = timeline(shots, plan)
        if plan.end_card:
            png = overlays.end_card(size, plan, tmp / "end.png", logo=logo, font_path=font_path)
            seg = tmp / "seg_end.mp4"
            _still_segment(png, END_CARD_SECONDS, seg)
            segs.append((seg, END_CARD_SECONDS))

        # ---- overlay images and when they show
        ov = []  # (png, start, end)
        montage = tl["montage"]
        if plan.title:
            ov.append((overlays.title_card(size, plan.title, tmp / "title.png", font_path),
                       0.15, min(2.6, montage - 0.3)))
        if plan.price or (plan.product and not plan.title):
            start = tl["starts"][1] if len(shots) > 1 else 1.0
            ov.append((overlays.price_tag(size, plan.product, plan.price, plan.brand_color,
                                          plan.text_color, tmp / "price.png", font_path),
                       max(start, 2.4), montage - 0.1))
        if plan.captions and len(shots) > 2:
            middle = list(range(1, len(shots) - 1)) or [0]
            for k, text in enumerate(plan.captions[:len(middle)]):
                i = middle[round(k * (len(middle) - 1) / max(len(plan.captions[:len(middle)]) - 1, 1))]
                a = tl["starts"][i] + 0.2
                ov.append((overlays.caption(size, text, tmp / f"cap_{k}.png", font_path),
                           a, a + shots[i].length - tl["trans"] - 0.3))

        # ---- build the final filter graph
        cmd = ["ffmpeg", "-v", "error", "-y"]
        for seg, _ in segs:
            cmd += ["-i", str(seg)]
        for png, _, _ in ov:
            cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{tl['total']:.3f}", "-i", str(png)]
        music_idx = None
        if music:
            music_idx = len(segs) + len(ov)
            cmd += ["-stream_loop", "-1", "-i", str(music)]
        else:
            cmd += ["-f", "lavfi", "-t", f"{tl['total']:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
            music_idx = len(segs) + len(ov)

        # Join with xfade. A "cut" is a 1-frame-ish crossfade so one code path covers everything.
        parts = []
        last, acc = "[0:v]", segs[0][1]  # acc = length of the joined video so far
        for i in range(1, len(segs)):
            is_end = plan.end_card and i == len(segs) - 1
            t = TRANS if is_end else tl["trans"]
            kind = "fade" if (is_end or plan.transition == "cut") else XFADE[plan.transition]
            offset = acc - t
            parts.append(f"{last}[{i}:v]xfade=transition={kind}:duration={t}:offset={offset:.3f}[x{i}]")
            last = f"[x{i}]"
            acc = offset + segs[i][1]
        parts.append(f"{last}null[base]")

        last = "[base]"
        for k, (_, a, b) in enumerate(ov):
            idx = len(segs) + k
            d = min(0.3, max((b - a) / 3, 0.05))
            parts.append(f"[{idx}:v]format=rgba,fade=in:st={a:.3f}:d={d}:alpha=1,"
                         f"fade=out:st={b - d:.3f}:d={d}:alpha=1[o{k}]")
            parts.append(f"{last}[o{k}]overlay=0:0:enable='between(t,{a:.3f},{b:.3f})'[v{k}]")
            last = f"[v{k}]"
        parts.append(f"{last}format=yuv420p[vout]")

        total = tl["total"]
        parts.append(f"[{music_idx}:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
                     f"afade=in:st=0:d=0.5,afade=out:st={max(total - 1.5, 0):.3f}:d=1.5,"
                     f"aresample=44100[aout]")

        cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "[aout]",
                "-t", f"{total:.3f}", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                "-movflags", "+faststart", str(out_path)]
        _run(cmd)
        return out_path
    finally:
        if not keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)
