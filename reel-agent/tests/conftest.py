import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFilter


def make_photo(path: Path, hue: int, blur: float = 0, size=(1600, 1200)) -> Path:
    im = Image.new("RGB", size, (hue, 120, 255 - hue))
    d = ImageDraw.Draw(im)
    for i in range(0, size[0], 80):
        d.line([(i, 0), (i, size[1])], fill=(255, 255, 255), width=4)
    d.ellipse([size[0] * 0.3, size[1] * 0.25, size[0] * 0.7, size[1] * 0.75], fill=(240, 220, 180))
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    im.save(path, quality=90)
    return path


def make_clip(path: Path, seconds: float = 4, src: str = "testsrc2") -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"{src}=size=1280x720:rate=30", "-t", str(seconds),
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


@pytest.fixture
def media_dir(tmp_path):
    d = tmp_path / "media"
    d.mkdir()
    make_photo(d / "01_front.jpg", 40)
    make_clip(d / "02_unbox.mp4", 4)
    make_photo(d / "03_detail.jpg", 160)
    make_photo(d / "04_blurry.jpg", 220, blur=25)
    make_clip(d / "05_lifestyle.mp4", 3, "mandelbrot")
    (d / "notes.txt").write_text("ignored")
    return d
