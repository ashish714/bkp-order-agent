import json
import subprocess

from reel_agent.cli import main
from reel_agent.media import find_media, pick_shots
from reel_agent.plan import EditPlan
from reel_agent.render import render, timeline


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,width,height:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_find_media_ignores_other_files(media_dir):
    names = [a.name for a in find_media(media_dir)]
    assert names == ["01_front.jpg", "02_unbox.mp4", "03_detail.jpg", "04_blurry.jpg", "05_lifestyle.mp4"]


def test_blurry_photo_dropped_when_too_many(media_dir):
    plan = EditPlan(duration=8, pace="medium")  # wants ~4 shots, has 5
    names = [s.asset.name for s in pick_shots(find_media(media_dir), plan)]
    assert "04_blurry.jpg" not in names
    assert names == sorted(names)  # story order kept


def test_render_reel_end_to_end(media_dir, tmp_path):
    plan = EditPlan(duration=10, look="warm", title="New drop", product="Soy Candle", price="$24",
                    captions=["Hand-poured", "Clean burn"], handle="@glowco", brand_color="#6D4C41")
    shots = pick_shots(find_media(media_dir), plan)
    out = render(shots, plan, tmp_path / "reel.mp4")
    info = probe(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert abs(float(info["format"]["duration"]) - timeline(shots, plan)["total"]) < 0.3


def test_cli_square_hard_cuts_no_end_card(media_dir, tmp_path):
    out = tmp_path / "sq.mp4"
    rc = main([str(media_dir), "square, hard cuts, no end card, 6 seconds", "-o", str(out), "--mode", "simple"])
    assert rc == 0
    video = next(s for s in probe(out)["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1080)


def test_cli_dry_run_and_saved_plan(media_dir, tmp_path):
    plan_file = tmp_path / "plan.json"
    assert main([str(media_dir), "warm reel $10", "--dry-run", "--save-plan", str(plan_file), "--mode", "simple"]) == 0
    assert json.loads(plan_file.read_text())["price"] == "$10"
    assert not (media_dir / "reel.mp4").exists()


def test_blurry_photo_dropped_even_when_short_on_shots(media_dir):
    plan = EditPlan(duration=30)  # wants more shots than the folder has
    names = [s.asset.name for s in pick_shots(find_media(media_dir), plan)]
    assert "04_blurry.jpg" not in names and len(names) == 4
