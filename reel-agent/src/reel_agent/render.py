"""Render an EditPlan + shots into an MP4 with ffmpeg.

1. build_timeline(): decide each cut's transition and overlap, optionally move
   cuts onto the music's beats, and work out where every shot starts.
2. render():
   a. each shot becomes a short normalised clip (size, 30 fps, look, speed,
      reverse, mirror, blur-fill, its own audio if kept);
   b. custom transitions (spin, flip, whip...) are drawn as short clips;
   c. one final ffmpeg pass joins everything, applies effects, lays the text
      on top and mixes the audio.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from . import overlays
from .beats import Beats, detect_beats, fit_lengths_to_beats
from .effects import final_effects, glow_graph, look_and_adjust, pulse_graph
from .media import WORKERS, Shot
from .plan import CUSTOM_TRANSITIONS, NATIVE_TRANSITIONS, EditPlan
from .transitions import render_custom

FPS = 30
END_CARD_SECONDS = 2.6
END_TRANS = 0.4
MIX = ["slide_left", "zoom_in", "whip_left", "flash", "spin", "push_up", "glitch", "cube_left", "fade"]

# kept for older imports
from .effects import LOOK_FILTERS  # noqa: E402,F401


class RenderError(RuntimeError):
    pass


def _log(msg: str) -> None:
    print(f"[reel] {msg}", flush=True)


def _run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        tail = "\n".join(res.stderr.strip().splitlines()[-15:])
        raise RenderError(f"ffmpeg failed:\n{tail}")


def _frames(seconds: float) -> int:
    return max(1, round(seconds * FPS))


# ---------------------------------------------------------------- timeline
@dataclass
class Timeline:
    shots: list[Shot]
    kinds: list[str | None]          # kinds[i] = transition into shot i (kinds[0] is None)
    overlaps: list[float]            # overlaps[i] = seconds shared by shot i-1 and shot i
    starts: list[float]
    montage: float
    total: float
    beats: Beats | None = None
    end_card: bool = True

    @property
    def cuts(self) -> list[dict]:
        """Every cut: number (1-based), time on the video, transition, files either side."""
        return [{"cut": i, "at": round(self.starts[i] + self.overlaps[i] / 2, 2),
                 "transition": self.kinds[i], "from": self.shots[i - 1].asset.name,
                 "to": self.shots[i].asset.name} for i in range(1, len(self.shots))]

    def __getitem__(self, key):  # backwards compatible: timeline(...)["total"]
        return {"starts": self.starts, "montage": self.montage, "total": self.total,
                "trans": max(self.overlaps[1:] or [0.0])}[key]


def _resolve_kinds(n: int, plan: EditPlan, approx_cut_times: list[float]) -> list[str | None]:
    kinds: list[str | None] = [None]
    for i in range(1, n):
        k = plan.transition
        if k == "mixed":
            k = MIX[(i - 1) % len(MIX)]
        kinds.append(k)
    for c in plan.cut_transitions:
        if "cut" in c and 1 <= c["cut"] < n:
            kinds[c["cut"]] = c["transition"]
        elif "at" in c and n > 1:
            i = min(range(1, n), key=lambda j: abs(approx_cut_times[j - 1] - c["at"]))
            kinds[i] = c["transition"]
    return [("fade" if k == "mixed" else k) for k in kinds]


def build_timeline(shots: list[Shot], plan: EditPlan, music: str | None = None) -> Timeline:
    n = len(shots)
    T = _frames(plan.transition_duration) / FPS

    # approximate cut times with the default transition, to resolve "at 15s" requests
    approx, pos = [], 0.0
    for s in shots[:-1]:
        pos += s.length - (0 if plan.transition == "cut" else T)
        approx.append(pos + (0 if plan.transition == "cut" else T / 2))
    kinds = _resolve_kinds(n, plan, approx)
    overlaps = [0.0] + [0.0 if k == "cut" else T for k in kinds[1:]]

    lengths = [s.length for s in shots]
    min_len = []
    for i in range(n):
        need = overlaps[i] + (overlaps[i + 1] if i + 1 < n else (END_TRANS if plan.end_card else 0)) + 0.25
        min_len.append(need)
        lengths[i] = max(lengths[i], need)

    beats = None
    wants_beats = plan.beat_sync or {"pulse_on_beat", "flash_on_beat"} & set(plan.effects)
    if music and wants_beats:
        try:
            beats = detect_beats(music)
        except Exception:
            beats = None
        if beats and beats.confidence < 0.4:  # no clear beat: keep normal pacing
            beats = None
    if beats and plan.beat_sync and n > 1:
        lengths = fit_lengths_to_beats(lengths, overlaps, beats, min_len)

    shots = [Shot(s.asset, round(L, 3), s.start, getattr(s, "speed", 1.0)) for s, L in zip(shots, lengths)]
    starts, pos = [], 0.0
    for i, s in enumerate(shots):
        starts.append(round(pos, 3))
        nxt = overlaps[i + 1] if i + 1 < n else 0.0
        pos += s.length - nxt
    montage = pos
    total = montage + (END_CARD_SECONDS - END_TRANS if plan.end_card else 0.0)
    return Timeline(shots, kinds, overlaps, starts, round(montage, 3), round(total, 3), beats, plan.end_card)


def timeline(shots: list[Shot], plan: EditPlan, music: str | None = None) -> Timeline:
    return build_timeline(shots, plan, music)


# ---------------------------------------------------------------- per-shot clips
def _cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(img, size, Image.LANCZOS, centering=(0.5, 0.45))


def _blur_fill(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    bg = _cover(img, size).filter(ImageFilter.GaussianBlur(40))
    bg = ImageEnhance.Brightness(bg).enhance(0.7)
    fg = ImageOps.contain(img, size, Image.LANCZOS)
    bg.paste(fg, ((size[0] - fg.width) // 2, (size[1] - fg.height) // 2))
    return bg


def _photo_segment(shot: Shot, idx: int, plan: EditPlan, out: Path, tmp: Path) -> None:
    W, H = plan.size
    n = _frames(shot.length)
    big = tmp / f"photo_{idx}.png"
    with Image.open(shot.asset.path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        if shot.asset.name in plan.mirror_files:
            im = ImageOps.mirror(im)
        canvas = _blur_fill(im, (W * 2, H * 2)) if plan.fit == "blur" else _cover(im, (W * 2, H * 2))
        canvas.save(big)
    motions = [
        f"z='1+0.12*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
        f"z='1.15':x='(iw-iw/zoom)*on/{n}':y='ih/2-(ih/zoom/2)'",
        f"z='1.14-0.12*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
        f"z='1.15':x='(iw-iw/zoom)*(1-on/{n})':y='(ih-ih/zoom)*(0.6-0.2*on/{n})'",
    ]
    vf = f"zoompan={motions[idx % len(motions)]}:d={n}:s={W}x{H}:fps={FPS},{look_and_adjust(plan)},setsar=1,format=yuv420p"
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(big), "-vf", vf, "-frames:v", str(n),
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)])


def _atempo(speed: float) -> str:
    parts, s = [], speed
    while s < 0.5:
        parts.append("atempo=0.5"); s /= 0.5
    while s > 2.0:
        parts.append("atempo=2.0"); s /= 2.0
    parts.append(f"atempo={s:.4f}")
    return ",".join(parts)


def _has_audio(path: Path) -> bool:
    res = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return bool(res.stdout.strip())


def _video_segment(shot: Shot, plan: EditPlan, out: Path) -> bool:
    """Returns True if the segment carries the clip's own audio."""
    W, H = plan.size
    n = _frames(shot.length)
    speed = plan.speed_for(shot.asset.name)
    src_len = shot.length * speed + 0.15
    pre = []
    if shot.asset.name in plan.reverse_files:
        pre.append("reverse")
    if shot.asset.name in plan.mirror_files:
        pre.append("hflip")
    pre.append(f"setpts=PTS/{speed}")
    pre = ",".join(pre)
    tail = f"fps={FPS},{look_and_adjust(plan)},setsar=1,format=yuv420p,tpad=stop_mode=clone:stop_duration=2"
    if plan.fit == "blur":
        fc = (f"[0:v]{pre},split[a][b];"
              f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=30,eq=brightness=-0.12[bg];"
              f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{tail}[v]")
    else:
        fc = f"[0:v]{pre},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},{tail}[v]"
    keep = plan.keep_audio and _has_audio(shot.asset.path)
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{shot.start:.3f}", "-t", f"{src_len:.3f}",
           "-i", str(shot.asset.path), "-filter_complex", fc, "-map", "[v]", "-frames:v", str(n)]
    if keep:
        af = ("areverse," if shot.asset.name in plan.reverse_files else "") + \
             f"{_atempo(speed)},apad,atrim=0:{shot.length:.3f},aresample=44100"
        cmd += ["-map", "0:a:0", "-af", af, "-c:a", "aac", "-b:a", "160k"]
    else:
        cmd += ["-an"]
    _run(cmd + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)])
    return keep


def _still_segment(png: Path, seconds: float, out: Path) -> None:
    _run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(png),
          "-frames:v", str(_frames(seconds)), "-vf", "setsar=1,format=yuv420p",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)])


# ---------------------------------------------------------------- text layers
def _text_layers(tl: Timeline, plan: EditPlan, tmp: Path, font_path) -> list[tuple[Path, float, float, str]]:
    size = plan.size
    layers: list[tuple[Path, float, float, str]] = []
    montage = tl.montage
    anim = plan.text_animation

    def add_text(text, a, b, role, position, key):
        b = min(b, tl.total - 0.05)
        if b - a < 0.4:
            return
        if anim in ("words", "typewriter"):
            words = text.split()
            steps = len(words) if anim == "words" else min(len(text), 14)
            reveal = min(0.9, (b - a) * 0.5)
            for k in range(1, steps + 1):
                kw = {"visible_words": k} if anim == "words" else {"visible_chars": round(len(text) * k / steps)}
                png = overlays.text_png(size, text, tmp / f"{key}_{k}.png", role, plan.text_style, position,
                                        plan.brand_color, font_path, **kw)
                start = a + reveal * (k - 1) / steps
                end = a + reveal * k / steps if k < steps else b
                layers.append((png, start, end, "fadeout" if k == steps else "none"))
        else:
            png = overlays.text_png(size, text, tmp / f"{key}.png", role, plan.text_style, position,
                                    plan.brand_color, font_path)
            layers.append((png, a, b, anim))

    if plan.title:
        add_text(plan.title, 0.15, min(2.6, montage - 0.3), "title", "center", "title")
    if plan.price or (plan.product and not plan.title):
        start = tl.starts[1] if len(tl.shots) > 1 else 1.0
        png = overlays.price_tag(size, plan.product, plan.price, plan.brand_color, plan.text_color,
                                 tmp / "price.png", font_path)
        layers.append((png, max(start, 2.4), montage - 0.1, "fade"))
    n = len(tl.shots)
    if plan.captions and n > 2:
        middle = list(range(1, n - 1))
        caps = plan.captions[:len(middle)]
        for k, text in enumerate(caps):
            i = middle[round(k * (len(middle) - 1) / max(len(caps) - 1, 1))]
            a = tl.starts[i] + tl.overlaps[i] + 0.1
            b = tl.starts[i] + tl.shots[i].length - (tl.overlaps[i + 1] if i + 1 < n else 0) - 0.15
            add_text(text, a, b, "caption", plan.caption_position, f"cap{k}")
    for k, t in enumerate(plan.texts):
        add_text(t["text"], t["at"], t["at"] + t["duration"], "caption", t["position"], f"txt{k}")
    return layers


# ---------------------------------------------------------------- main
def render(shots: list[Shot] | Timeline, plan: EditPlan, out_path: str | Path, music: str | None = None,
           logo: str | None = None, font_path: str | None = None, keep_temp: bool = False) -> Path:
    if not shutil.which("ffmpeg"):
        raise RenderError("ffmpeg is not installed. Mac: `brew install ffmpeg`. Windows: `winget install ffmpeg`.")
    tl = shots if isinstance(shots, Timeline) else build_timeline(shots, plan, music)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="reel_"))
    size = plan.size
    try:
        # a. per-shot clips
        t0 = time.time()
        _log(f"rendering {len(tl.shots)} shots, {tl.total:.1f}s video")
        def make_seg(i: int) -> tuple[Path, bool]:
            ts = time.time()
            shot = tl.shots[i]
            seg = tmp / f"seg_{i:03d}.mp4"
            if shot.asset.kind == "photo":
                _photo_segment(shot, i, plan, seg, tmp)
                has = False
            else:
                has = _video_segment(shot, plan, seg)
            _log(f"shot {i + 1}/{len(tl.shots)} {shot.asset.name}: {time.time() - ts:.1f}s")
            return seg, has

        # several shots at once: zoompan and decoding are mostly single-threaded
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            done = list(pool.map(make_seg, range(len(tl.shots))))
        segs = [d[0] for d in done]
        seg_audio = [d[1] for d in done]
        _log(f"all shots ready: {time.time() - t0:.1f}s")
        lens = [s.length for s in tl.shots]

        # b. custom transitions
        n = len(segs)
        cut_start = [0.0] * n
        cut_end = [0.0] * n
        tcs: dict[int, Path] = {}
        for i in range(1, n):
            if tl.kinds[i] in CUSTOM_TRANSITIONS:
                T = tl.overlaps[i]
                tc = tmp / f"tc_{i:03d}.mp4"
                ts = time.time()
                render_custom(tl.kinds[i], segs[i - 1], lens[i - 1], segs[i], T, size, tc)
                _log(f"transition {i} ({tl.kinds[i]}): {time.time() - ts:.1f}s")
                tcs[i] = tc
                cut_end[i - 1] += T
                cut_start[i] += T

        # c. inputs
        cmd = ["ffmpeg", "-v", "error", "-y"]
        for seg in segs:
            cmd += ["-i", str(seg)]
        idx = n
        end_idx = None
        if plan.end_card:
            png = overlays.end_card(size, plan, tmp / "end.png", logo=logo, font_path=font_path)
            end_seg = tmp / "seg_end.mp4"
            _still_segment(png, END_CARD_SECONDS, end_seg)
            cmd += ["-i", str(end_seg)]
            end_idx, idx = idx, idx + 1
        tc_idx = {}
        for i, tc in tcs.items():
            cmd += ["-i", str(tc)]
            tc_idx[i], idx = idx, idx + 1
        layers = _text_layers(tl, plan, tmp, font_path)
        layer_idx, layer_pos = [], []
        for png, a, b, _ in layers:
            # crop each full-frame text image to its text, so overlay only touches those pixels
            with Image.open(png) as im:
                box = im.getchannel("A").getbbox() or (0, 0, 2, 2)
                x0, y0 = box[0] // 2 * 2, box[1] // 2 * 2
                x1, y1 = min(im.width, (box[2] + 1) // 2 * 2), min(im.height, (box[3] + 1) // 2 * 2)
                im.crop((x0, y0, max(x1, x0 + 2), max(y1, y0 + 2))).save(png)
            layer_pos.append((x0, y0))
            # the layer only exists for its own seconds, not the whole video
            cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{max(b - a, 0.1) + 0.05:.3f}", "-i", str(png)]
            layer_idx.append(idx); idx += 1
        music_idx = None
        if music:
            cmd += ["-stream_loop", "-1", "-i", str(music)]
            music_idx, idx = idx, idx + 1

        # d. video chain
        parts = []

        def src(k: int, L: float, s0: float, e0: float) -> str:
            lab = f"[s{k}]"
            parts.append(f"[{k}:v]trim=start={s0:.4f}:end={L - e0:.4f},setpts=PTS-STARTPTS,fps={FPS},settb=1/{FPS}{lab}")
            return lab

        cur = src(0, lens[0], cut_start[0], cut_end[0])
        acc = lens[0] - cut_start[0] - cut_end[0]
        for i in range(1, n):
            nxt = src(i, lens[i], cut_start[i], cut_end[i])
            Li = lens[i] - cut_start[i] - cut_end[i]
            kind = tl.kinds[i]
            out = f"[j{i}]"
            if kind == "cut":
                parts.append(f"{cur}{nxt}concat=n=2:v=1:a=0,settb=1/{FPS}{out}")
                acc += Li
            elif kind in CUSTOM_TRANSITIONS:
                tcl = f"[tc{i}]"
                parts.append(f"[{tc_idx[i]}:v]fps={FPS},setpts=PTS-STARTPTS,settb=1/{FPS}{tcl}")
                parts.append(f"{cur}{tcl}{nxt}concat=n=3:v=1:a=0,settb=1/{FPS}{out}")
                acc += tl.overlaps[i] + Li
            else:
                T = tl.overlaps[i]
                parts.append(f"{cur}{nxt}xfade=transition={NATIVE_TRANSITIONS[kind]}:duration={T:.4f}"
                             f":offset={acc - T:.4f}{out}")
                acc = acc - T + Li
            cur = out
        if end_idx is not None:
            parts.append(f"[{end_idx}:v]fps={FPS},setpts=PTS-STARTPTS,settb=1/{FPS}[endv]")
            parts.append(f"{cur}[endv]xfade=transition=fade:duration={END_TRANS}:offset={acc - END_TRANS:.4f}[joined]")
            cur = "[joined]"

        # e. effects (under the text)
        fx = final_effects(plan, tl.montage, tl.beats)
        if fx:
            parts.append(f"{cur}{','.join(fx)}[fx]")
            cur = "[fx]"
        if "pulse_on_beat" in plan.effects and tl.beats:
            parts.append(pulse_graph(cur, "[pulse]", plan.size, tl.beats, tl.montage))
            cur = "[pulse]"
        if "glow" in plan.effects:
            parts.append(glow_graph(cur, "[glow]", tl.montage, plan.size))
            cur = "[glow]"

        # f. text layers
        H = size[1]
        for k, ((_, a, b, anim), li, (lx, ly)) in enumerate(zip(layers, layer_idx, layer_pos)):
            dur = b - a
            d = min(0.3, max(dur / 3, 0.05))
            chain = "format=rgba"
            if anim in ("fade", "slide"):
                chain += f",fade=in:st=0:d={d:.3f}:alpha=1,fade=out:st={dur - d:.3f}:d={d:.3f}:alpha=1"
            elif anim == "fadeout":
                chain += f",fade=out:st={dur - d:.3f}:d={d:.3f}:alpha=1"
            chain += f",setpts=PTS-STARTPTS+{a:.3f}/TB"
            parts.append(f"[{li}:v]{chain}[o{k}]")
            y = (f"'{ly}+if(lt(t,{a + 0.35:.3f}),{H * 0.06:.0f}*pow(1-(t-{a:.3f})/0.35,2),0)'"
                 if anim == "slide" else str(ly))
            parts.append(f"{cur}[o{k}]overlay=x={lx}:y={y}:eof_action=pass:enable='between(t,{a:.3f},{b:.3f})'[v{k}]")
            cur = f"[v{k}]"
        parts.append(f"{cur}format=yuv420p[vout]")

        # g. audio
        total = tl.total
        clip_labels = []
        for i, has in enumerate(seg_audio):
            if not has:
                continue
            s0, e0 = cut_start[i], cut_end[i]
            L = lens[i] - s0 - e0
            start = tl.starts[i] + s0
            fi = min(0.15, L / 4)
            parts.append(f"[{i}:a]atrim=start={s0:.4f}:end={lens[i] - e0:.4f},asetpts=PTS-STARTPTS,"
                         f"afade=in:st=0:d={fi:.3f},afade=out:st={max(L - fi, 0):.3f}:d={fi:.3f},"
                         f"adelay={int(start * 1000)}:all=1[ca{i}]")
            clip_labels.append(f"[ca{i}]")
        mv = plan.music_volume * (0.45 if clip_labels else 1.0)
        audio_in = []
        if music_idx is not None:
            parts.append(f"[{music_idx}:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,volume={mv:.3f},"
                         f"aresample=44100,aformat=channel_layouts=stereo[mus]")
            audio_in.append("[mus]")
        for lab in clip_labels:
            parts.append(f"{lab}aresample=44100,aformat=channel_layouts=stereo{lab[:-1]}s]")
            audio_in.append(f"{lab[:-1]}s]")
        if not audio_in:
            parts.append(f"anullsrc=r=44100:cl=stereo,atrim=0:{total:.3f}[aout0]")
        elif len(audio_in) == 1:
            parts.append(f"{audio_in[0]}apad,atrim=0:{total:.3f}[aout0]")
        else:
            parts.append(f"{''.join(audio_in)}amix=inputs={len(audio_in)}:duration=longest:normalize=0,"
                         f"apad,atrim=0:{total:.3f}[aout0]")
        parts.append(f"[aout0]afade=in:st=0:d=0.4,afade=out:st={max(total - 1.5, 0):.3f}:d=1.5[aout]")

        cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "[aout]",
                "-t", f"{total:.3f}", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                "-movflags", "+faststart", str(out_path)]
        ts = time.time()
        _run(cmd)
        _log(f"final pass ({len(layers)} text layers): {time.time() - ts:.1f}s; total {time.time() - t0:.1f}s")
        return out_path
    finally:
        if not keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)
