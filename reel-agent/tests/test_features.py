import json
import subprocess

import pytest
from PIL import Image

from reel_agent.beats import detect_beats
from reel_agent.media import find_media, pick_shots
from reel_agent.plan import CUSTOM_TRANSITIONS, NATIVE_TRANSITIONS, EditPlan
from reel_agent.planner import simple_fields
from reel_agent.render import build_timeline, render
from reel_agent.transitions import DRAW


@pytest.fixture(scope="module")
def beat_track(tmp_path_factory):
    """30 s synthetic track: a kick on every beat at 120 BPM, first beat at 0.25 s."""
    out = tmp_path_factory.mktemp("music") / "beat120.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "aevalsrc='0.9*sin(2*PI*55*t)*exp(-mod(t-0.25,0.5)*18)+0.03*sin(2*PI*330*t)':s=44100:d=30",
                    "-ac", "2", str(out)], check=True)
    return out


def test_plan_normalises_transitions_and_lists():
    p = EditPlan.from_dict({"transition": "Whip", "cut_transitions": [
        {"cut": 2, "transition": "turn"}, {"at": "15", "transition": "dip to black"},
        {"transition": "spin"}, "junk"], "effects": ["grain", "nope", "grain"],
        "clip_speeds": {"a.mp4": 9}, "texts": [{"text": " Sale ", "at": 3, "position": "side"}, {"text": ""}]})
    assert p.transition == "whip_left"
    assert p.cut_transitions == [{"cut": 2, "transition": "flip"}, {"at": 15.0, "transition": "fade_black"}]
    assert p.effects == ["grain"] and p.clip_speeds == {"a.mp4": 3.0}
    assert p.texts == [{"text": "Sale", "at": 3.0, "duration": 2.5, "position": "center"}]


def test_every_custom_transition_draws_frames():
    A = [Image.new("RGB", (90, 160), (200, 60, 60)) for _ in range(9)]
    B = [Image.new("RGB", (90, 160), (60, 60, 200)) for _ in range(9)]
    assert set(DRAW) == set(CUSTOM_TRANSITIONS)
    for name, fn in DRAW.items():
        frames = fn(A, B, 9)
        assert len(frames) == 9 and all(f.size == (90, 160) for f in frames), name
        # starts on A, ends on B
        first, last = frames[0].getpixel((45, 80)), frames[-1].getpixel((45, 80))
        assert first[0] > first[2] and last[2] > last[0], name


def test_timeline_resolves_per_cut_transitions(media_dir):
    plan = EditPlan(duration=10, transition="mixed", cut_transitions=[{"cut": 1, "transition": "cut"},
                                                                       {"at": 100, "transition": "spin"}],
                    drop_blurry=False)
    tl = build_timeline(pick_shots(find_media(media_dir), plan), plan)
    kinds = [c["transition"] for c in tl.cuts]
    assert kinds[0] == "cut" and kinds[-1] == "spin"          # "at 100s" -> the last cut
    assert tl.overlaps[1] == 0.0 and tl.overlaps[2] == pytest.approx(0.5)
    assert all(k in NATIVE_TRANSITIONS or k in CUSTOM_TRANSITIONS or k == "cut" for k in kinds)


def test_beat_detection_and_sync(media_dir, beat_track):
    b = detect_beats(beat_track)
    assert b.bpm == pytest.approx(120, abs=1.5) and b.confidence >= 0.4
    assert b.first == pytest.approx(0.25, abs=0.04)
    plan = EditPlan(duration=10, beat_sync=True, drop_blurry=False)
    tl = build_timeline(pick_shots(find_media(media_dir), plan), plan, str(beat_track))
    assert tl.beats is not None
    for c in tl.cuts:
        nearest = min(tl.beats.times, key=lambda t: abs(t - c["at"]))
        assert abs(c["at"] - nearest) < 0.04


def test_render_with_new_features(media_dir, tmp_path, beat_track):
    plan = EditPlan(duration=7, transition="mixed",
                    cut_transitions=[{"cut": 1, "transition": "flip"}, {"cut": 2, "transition": "cut"}],
                    beat_sync=True, effects=["vignette", "grain", "letterbox", "flash_on_beat", "glow"],
                    fit="blur", look="cinematic", warmth=0.3, keep_audio=True,
                    clip_speeds={"02_unbox.mp4": 0.5}, reverse_files=["05_lifestyle.mp4"],
                    mirror_files=["03_detail.jpg"], title="New drop", captions=["Hand made"],
                    texts=[{"text": "Only 50", "at": 1, "duration": 2, "position": "top"}],
                    text_style="outline", text_animation="words", price="$9", drop_blurry=False)
    shots = pick_shots(find_media(media_dir), plan)
    tl = build_timeline(shots, plan, str(beat_track))
    out = render(tl, plan, tmp_path / "fx.mp4", music=str(beat_track))
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
         "-of", "json", str(out)], capture_output=True, text=True, check=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert abs(float(info["format"]["duration"]) - tl.total) < 0.15


def test_simple_keywords_for_new_features():
    f = simple_fields("20 sec reel, spin transitions, on the beat, vignette, blur background, "
                      "keep original sound, outlined word by word captions, warmer")
    assert f["duration"] == 20 and f["transition"] == "spin" and f["beat_sync"] is True
    assert f["effects"] == ["vignette"] and f["fit"] == "blur" and f["keep_audio"] is True
    assert f["text_style"] == "outline" and f["text_animation"] == "words" and f["warmth"] == 0.5
    assert "look" not in f   # "warmer" adjusts warmth, it doesn't switch the filter

    f = simple_fields("add a flip at 15 sec and a glitch at 8s")
    assert f["cut_transitions"] == [{"at": 15.0, "transition": "flip"}, {"at": 8.0, "transition": "glitch"}]
    assert "duration" not in f
    assert simple_fields("mixed transitions")["transition"] == "mixed"
    assert simple_fields("quick transitions") == {"transition_duration": 0.3}
