"""Draw text overlays (title, price tag, captions, end card) as PNGs with Pillow.

Drawing them as images avoids ffmpeg drawtext escaping problems and gives
nicer results: rounded price tags, shadows, automatic text wrapping.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",      # macOS
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",                            # Windows
]
FONT_REGULAR = [
    "/usr/share/fonts/truetype/google-fonts/Poppins-Medium.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def font(size: int, bold: bool = True, custom: str | None = None) -> ImageFont.FreeTypeFont:
    for path in ([custom] if custom else []) + (FONT_CANDIDATES if bold else FONT_REGULAR):
        if path and Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _wrap(draw, text: str, fnt, max_w: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for w in words:
        test = f"{line} {w}".strip()
        if draw.textlength(test, font=fnt) <= max_w or not line:
            line = test
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def _shadow_text(canvas: Image.Image, xy, lines, fnt, fill, spacing: int, align="center"):
    W = canvas.width
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd, d = ImageDraw.Draw(shadow), ImageDraw.Draw(canvas)
    x0, y = xy
    for ln in lines:
        w = d.textlength(ln, font=fnt)
        x = (W - w) / 2 if align == "center" else x0
        sd.text((x + 3, y + 4), ln, font=fnt, fill=(0, 0, 0, 170))
        y += fnt.size + spacing
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(6)))
    y = xy[1]
    for ln in lines:
        w = d.textlength(ln, font=fnt)
        x = (W - w) / 2 if align == "center" else x0
        d.text((x, y), ln, font=fnt, fill=fill)
        y += fnt.size + spacing


def title_card(size, text: str, out: Path, font_path=None) -> Path:
    W, H = size
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    # soft dark gradient behind the text so it reads on any footage
    grad = Image.new("L", (1, H))
    for y in range(H):
        rel = abs(y - H * 0.42) / (H * 0.25)
        grad.putpixel((0, y), int(max(0, 1 - rel) * 120))
    im.paste((0, 0, 0, 255), (0, 0), grad.resize(size))
    fnt = font(int(W * 0.105), custom=font_path)
    d = ImageDraw.Draw(im)
    lines = _wrap(d, text.upper(), fnt, int(W * 0.84))
    block = len(lines) * (fnt.size + 12)
    _shadow_text(im, (0, int(H * 0.42 - block / 2)), lines, fnt, (255, 255, 255, 255), 12)
    im.save(out)
    return out


def caption(size, text: str, out: Path, font_path=None) -> Path:
    W, H = size
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    fnt = font(int(W * 0.062), custom=font_path)
    d = ImageDraw.Draw(im)
    lines = _wrap(d, text, fnt, int(W * 0.8))
    block = len(lines) * (fnt.size + 10)
    y0 = int(H * 0.72 - block / 2)
    pad = int(W * 0.03)
    widest = max(d.textlength(ln, font=fnt) for ln in lines)
    box = [(W - widest) / 2 - pad, y0 - pad, (W + widest) / 2 + pad, y0 + block + pad * 0.6]
    d.rounded_rectangle(box, radius=pad, fill=(0, 0, 0, 140))
    _shadow_text(im, (0, y0), lines, fnt, (255, 255, 255, 255), 10)
    im.save(out)
    return out


def price_tag(size, product: str, price: str, bg: str, fg: str, out: Path, font_path=None) -> Path:
    W, H = size
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    big, small = font(int(W * 0.075), custom=font_path), font(int(W * 0.04), bold=False)
    pad = int(W * 0.035)
    pw = d.textlength(price, font=big) if price else 0
    nw = d.textlength(product, font=small) if product else 0
    bw = max(pw, nw) + pad * 2
    bh = pad * 2 + (big.size if price else 0) + (small.size + 8 if product else 0)
    x0, y0 = W - bw - int(W * 0.05), int(H * 0.08)
    d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=int(pad * 0.9), fill=_rgb(bg) + (235,))
    y = y0 + pad
    if product:
        d.text((x0 + pad, y), product, font=small, fill=_rgb(fg) + (255,))
        y += small.size + 8
    if price:
        d.text((x0 + pad, y), price, font=big, fill=_rgb(fg) + (255,))
    im.save(out)
    return out


def end_card(size, plan, out: Path, logo: str | None = None, font_path=None) -> Path:
    W, H = size
    bg, fg = _rgb(plan.brand_color), _rgb(plan.text_color)
    im = Image.new("RGBA", size, bg + (255,))
    d = ImageDraw.Draw(im)
    y = int(H * 0.28)
    if logo and Path(logo).exists():
        with Image.open(logo) as lg:
            lg = ImageOps.contain(lg.convert("RGBA"), (int(W * 0.4), int(H * 0.15)))
            im.alpha_composite(lg, ((W - lg.width) // 2, y))
            y += lg.height + int(H * 0.05)
    for text, f in ((plan.product, font(int(W * 0.085), custom=font_path)),
                    (plan.price, font(int(W * 0.12), custom=font_path))):
        if not text:
            continue
        for ln in _wrap(d, text, f, int(W * 0.86)):
            d.text(((W - d.textlength(ln, font=f)) / 2, y), ln, font=f, fill=fg)
            y += f.size + 14
        y += int(H * 0.02)
    if plan.cta:
        f = font(int(W * 0.06), custom=font_path)
        tw = d.textlength(plan.cta.upper(), font=f)
        pad = int(W * 0.045)
        y += int(H * 0.03)
        box = [(W - tw) / 2 - pad, y - pad * 0.6, (W + tw) / 2 + pad, y + f.size + pad * 0.8]
        d.rounded_rectangle(box, radius=int(f.size), fill=fg)
        d.text(((W - tw) / 2, y), plan.cta.upper(), font=f, fill=bg)
    if plan.handle:
        f = font(int(W * 0.045), bold=False)
        d.text(((W - d.textlength(plan.handle, font=f)) / 2, int(H * 0.86)), plan.handle, font=f, fill=fg)
    im.convert("RGB").save(out)
    return out
