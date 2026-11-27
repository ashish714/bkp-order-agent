"""Custom transitions drawn frame by frame (spin, flip, cube, whip, zoom, glitch, shake).

ffmpeg's xfade covers fades, slides, wipes and so on. CapCut favourites like
spin, 3D flip and whip pan aren't in xfade, and xfade's per-pixel custom
expressions are far too slow at 1080x1920. So for those we decode the last
few frames of shot A and the first few of shot B, draw the transition with
Pillow/numpy (a second or two), and encode it as a short clip that the
renderer splices between the shots.
"""
from __future__ import annotations

import math
import random
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

FPS = 30


# ---------------------------------------------------------------- frame IO
def read_frames(path: Path, start: float, n: int, size: tuple[int, int]) -> list[Image.Image]:
    W, H = size
    res = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{max(start, 0):.4f}", "-i", str(path), "-frames:v", str(n),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
    )
    raw, step = res.stdout, W * H * 3
    frames = [Image.frombytes("RGB", (W, H), raw[i:i + step]) for i in range(0, len(raw) - step + 1, step)]
    if not frames:
        frames = [Image.new("RGB", size)]
    while len(frames) < n:  # clip ended early: hold the last frame
        frames.append(frames[-1])
    return frames[:n]


def write_frames(frames: list[Image.Image], out: Path) -> None:
    W, H = frames[0].size
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", str(out)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    for f in frames:
        proc.stdin.write(f.convert("RGB").tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("encoding transition failed: " + proc.stderr.read().decode()[-500:])


# ---------------------------------------------------------------- helpers
def _ease(t: float) -> float:
    return t * t * (3 - 2 * t)


def _affine(img: Image.Image, angle_deg: float = 0.0, scale: float = 1.0,
            dx: float = 0.0, dy: float = 0.0) -> Image.Image:
    """Rotate/scale about the centre and shift, in one resample."""
    W, H = img.size
    a = math.radians(angle_deg)
    c, s = math.cos(a) / scale, math.sin(a) / scale
    cx, cy = W / 2, H / 2
    # output -> input mapping
    ox, oy = cx + dx, cy + dy
    coeffs = (c, s, cx - c * ox - s * oy, -s, c, cy + s * ox - c * oy)
    return img.transform(img.size, Image.AFFINE, coeffs, resample=Image.BILINEAR)


def _cover_scale(angle_deg: float, W: int, H: int) -> float:
    a = math.radians(angle_deg)
    r = max(W, H) / min(W, H)
    return abs(math.cos(a)) + r * abs(math.sin(a))


def _persp_coeffs(dst: list[tuple[float, float]], src: list[tuple[float, float]]) -> list[float]:
    """Coefficients for Image.PERSPECTIVE mapping output quad `dst` onto input quad `src`."""
    A, b = [], []
    for (x, y), (u, v) in zip(dst, src):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y]); b.append(v)
    return np.linalg.solve(np.array(A, float), np.array(b, float)).tolist()


def _quad(img: Image.Image, dst: list[tuple[float, float]], shade: float = 0.0) -> Image.Image:
    """Draw img into the quad dst (tl, tr, br, bl) on black."""
    W, H = img.size
    src = [(0, 0), (W, 0), (W, H), (0, H)]
    if abs(dst[1][0] - dst[0][0]) < 2 and abs(dst[2][0] - dst[3][0]) < 2:
        return Image.new("RGB", img.size)
    if abs(dst[3][1] - dst[0][1]) < 2:
        return Image.new("RGB", img.size)
    out = img.transform(img.size, Image.PERSPECTIVE, _persp_coeffs(dst, src), resample=Image.BILINEAR)
    mask = Image.new("L", img.size, 255).transform(img.size, Image.PERSPECTIVE, _persp_coeffs(dst, src))
    bg = Image.new("RGB", img.size)
    bg.paste(out, (0, 0), mask)
    if shade > 0:
        bg = ImageEnhance.Brightness(bg).enhance(1 - shade)
    return bg


def _box1d(arr: np.ndarray, k: int, axis: int) -> np.ndarray:
    """Box blur of length k along one axis, edges clamped (no wrap-around ghosts)."""
    if k < 2:
        return arr
    pad = [(0, 0)] * arr.ndim
    pad[axis] = (k // 2 + 1, k - k // 2 - 1)
    a = np.pad(arr.astype(np.float32), pad, mode="edge")
    c = np.cumsum(a, axis=axis)
    hi = [slice(None)] * arr.ndim
    lo = [slice(None)] * arr.ndim
    hi[axis] = slice(k, None)
    lo[axis] = slice(0, -k)
    return (c[tuple(hi)] - c[tuple(lo)]) / k


def _motion_blur(img: Image.Image, dx: int, dy: int, taps: int = 0) -> Image.Image:
    """Directional blur: dx/dy are blur lengths in pixels (taps kept for compatibility)."""
    arr = np.asarray(img)
    out = arr.astype(np.float32)
    if abs(dx) >= 2:
        out = _box1d(out, min(abs(int(dx)), arr.shape[1] // 2), axis=1)
    if abs(dy) >= 2:
        out = _box1d(out, min(abs(int(dy)), arr.shape[0] // 2), axis=0)
    if out is arr:
        return img
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def _zoom_blur(img: Image.Image, scale: float, strength: float, taps: int = 5) -> Image.Image:
    if strength < 0.01:
        return _affine(img, 0, scale)
    acc = None
    for k in range(taps):
        layer = np.asarray(_affine(img, 0, scale * (1 + strength * k / taps)), dtype=np.uint16)
        acc = layer if acc is None else acc + layer
    return Image.fromarray((acc // taps).astype(np.uint8))


# ---------------------------------------------------------------- transitions
def _spin(A, B, n, direction=1):
    out = []
    W, H = A[0].size
    for j in range(n):
        p = j / max(n - 1, 1)
        if p < 0.5:
            e = (p / 0.5) ** 2                       # accelerate out of A
            ang, img = direction * 180 * e, A[j]
        else:
            e = ((1 - p) / 0.5) ** 2                 # decelerate into B
            ang, img = -direction * 180 * e, B[j]
        zoom = _cover_scale(ang, W, H) * (1 + 0.15 * math.sin(math.pi * p))
        frame = _affine(img, ang, zoom)
        blur = 14 * math.sin(math.pi * p) ** 2
        if blur > 0.5:
            frame = frame.filter(ImageFilter.GaussianBlur(blur))
        out.append(frame)
    return out


def _flip(A, B, n, vertical=False):
    out = []
    W, H = A[0].size
    d = 2.2 * (H if vertical else W)
    for j in range(n):
        p = _ease(j / max(n - 1, 1))
        phi = math.pi * p
        img = A[j] if phi < math.pi / 2 else B[j]
        if phi >= math.pi / 2:
            phi = math.pi - phi
        phi = min(phi, math.radians(80))      # never exactly edge-on (a blank frame)
        c, s = math.cos(phi), math.sin(phi)
        if not vertical:
            hw = W / 2
            near, far = d / (d - hw * s), d / (d + hw * s)
            xl, xr = W / 2 - hw * c * near, W / 2 + hw * c * far
            dst = [(xl, H / 2 - H / 2 * near), (xr, H / 2 - H / 2 * far),
                   (xr, H / 2 + H / 2 * far), (xl, H / 2 + H / 2 * near)]
        else:
            hh = H / 2
            near, far = d / (d - hh * s), d / (d + hh * s)
            yt, yb = H / 2 - hh * c * far, H / 2 + hh * c * near
            dst = [(W / 2 - W / 2 * far, yt), (W / 2 + W / 2 * far, yt),
                   (W / 2 + W / 2 * near, yb), (W / 2 - W / 2 * near, yb)]
        out.append(_quad(img, dst, shade=0.45 * s))
    return out


def _cube(A, B, n, left=True):
    out = []
    W, H = A[0].size
    for j in range(n):
        e = _ease(j / max(n - 1, 1))
        edge = W * (1 - e) if left else W * e
        sa, sb = 0.22 * e, 0.22 * (1 - e)      # how much each receding edge shrinks
        bg = Image.new("RGB", (W, H))
        if left:
            qa = [(0, H * sa / 2), (edge, 0), (edge, H), (0, H - H * sa / 2)]
            qb = [(edge, 0), (W, H * sb / 2), (W, H - H * sb / 2), (edge, H)]
        else:
            qb = [(0, H * sb / 2), (edge, 0), (edge, H), (0, H - H * sb / 2)]
            qa = [(edge, 0), (W, H * sa / 2), (W, H - H * sa / 2), (edge, H)]
        for img, q, shade in ((A[j], qa, 0.5 * e), (B[j], qb, 0.5 * (1 - e))):
            if abs(q[1][0] - q[0][0]) > 2:
                layer = _quad(img, q, shade)
                mask = Image.new("L", (W, H), 0)
                from PIL import ImageDraw
                ImageDraw.Draw(mask).polygon(q, fill=255)
                bg.paste(layer, (0, 0), mask)
        out.append(bg)
    return out


def _zoom_in(A, B, n):
    out = []
    for j in range(n):
        p = j / max(n - 1, 1)
        if p < 0.5:
            e = (p / 0.5) ** 2
            out.append(_zoom_blur(A[j], 1 + 1.6 * e, 0.25 * e))
        else:
            e = ((1 - p) / 0.5) ** 2
            out.append(_zoom_blur(B[j], 1 + 0.8 * e, 0.2 * e))
    # soften the hand-over
    m = n // 2
    if 0 < m < n:
        out[m] = Image.blend(out[m - 1], out[m], 0.5)
    return out


def _zoom_out(A, B, n):
    out = []
    W, H = A[0].size
    for j in range(n):
        e = _ease(j / max(n - 1, 1))
        under = _affine(B[j], 0, 1.3 - 0.3 * e)
        under = under.filter(ImageFilter.GaussianBlur(10 * (1 - e))) if e < 0.95 else under
        scale = max(1 - e, 0.001)
        if scale > 0.02:
            top = _affine(A[j], 0, scale)
            mask = _affine(Image.new("L", (W, H), 255), 0, scale)
            under.paste(top, (0, 0), mask.point(lambda v: int(v * (1 - e))))
        out.append(under)
    return out


def _whip(A, B, n, dx_sign=-1, vertical=False):
    out = []
    W, H = A[0].size
    span = H if vertical else W
    prev = 0.0
    for j in range(n):
        e = _ease(j / max(n - 1, 1))
        off = e * span
        speed = (off - prev) * 1.2
        prev = off
        canvas = Image.new("RGB", (W, H))
        if vertical:
            canvas.paste(A[j], (0, int(-off)))
            canvas.paste(B[j], (0, int(span - off)))
            canvas = _motion_blur(canvas, 0, int(speed))
        else:
            canvas.paste(A[j], (int(dx_sign * off), 0))
            canvas.paste(B[j], (int(dx_sign * off - dx_sign * span), 0))
            canvas = _motion_blur(canvas, int(speed), 0)
        out.append(canvas)
    return out


def _glitch(A, B, n):
    rng = random.Random(7)
    out = []
    W, H = A[0].size
    for j in range(n):
        p = j / max(n - 1, 1)
        base = A[j] if (p < 0.5) ^ (rng.random() < 0.25 and 0.25 < p < 0.75) else B[j]
        arr = np.asarray(base).copy()
        amt = math.sin(math.pi * p)
        shift = int(30 * amt)
        if shift:
            arr[..., 0] = np.roll(arr[..., 0], shift, axis=1)
            arr[..., 2] = np.roll(arr[..., 2], -shift, axis=1)
        for _ in range(int(8 * amt)):
            y = rng.randrange(0, H - 20)
            h = rng.randrange(8, max(9, H // 12))
            arr[y:y + h] = np.roll(arr[y:y + h], rng.randint(-W // 6, W // 6), axis=1)
        out.append(Image.fromarray(arr))
    return out


def _shake(A, B, n):
    rng = random.Random(3)
    out = []
    for j in range(n):
        p = j / max(n - 1, 1)
        W, H = img_size = A[0].size
        amp = 0.04 * min(W, H) * math.sin(math.pi * p)
        img = A[j] if p < 0.5 else B[j]
        frame = _affine(img, rng.uniform(-2, 2) * p * (1 - p) * 4, 1.16,
                        rng.uniform(-amp, amp), rng.uniform(-amp, amp))
        out.append(_motion_blur(frame, int(amp), int(amp * 0.6)))
    m = n // 2
    if 0 < m < n:
        out[m] = Image.blend(out[m - 1], out[m], 0.5)
    return out


DRAW = {
    "spin": lambda A, B, n: _spin(A, B, n, 1),
    "spin_left": lambda A, B, n: _spin(A, B, n, -1),
    "flip": lambda A, B, n: _flip(A, B, n, False),
    "flip_up": lambda A, B, n: _flip(A, B, n, True),
    "cube_left": lambda A, B, n: _cube(A, B, n, True),
    "cube_right": lambda A, B, n: _cube(A, B, n, False),
    "zoom_in": _zoom_in,
    "zoom_out": _zoom_out,
    "whip_left": lambda A, B, n: _whip(A, B, n, -1),
    "whip_right": lambda A, B, n: _whip(A, B, n, 1),
    "whip_up": lambda A, B, n: _whip(A, B, n, -1, vertical=True),
    "glitch": _glitch,
    "shake": _shake,
}


def render_custom(kind: str, seg_a: Path, len_a: float, seg_b: Path, seconds: float,
                  size: tuple[int, int], out: Path) -> Path:
    n = max(3, round(seconds * FPS))
    A = read_frames(seg_a, len_a - n / FPS, n, size)
    B = read_frames(seg_b, 0.0, n, size)
    write_frames(DRAW[kind](A, B, n), out)
    return out
