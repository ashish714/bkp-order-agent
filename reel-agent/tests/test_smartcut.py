import subprocess
import threading
import time

import numpy as np
import pytest

from reel_agent import procs
from reel_agent.media import find_media, pick_shots
from reel_agent.plan import EditPlan
from reel_agent.render import prepare_timeline, render
from reel_agent.smartcut import framing_for


@pytest.fixture(scope="module")
def dance_dir(tmp_path_factory):
    """20 s portrait clip: two figures moving on a static room, with a 120 BPM beat as its sound."""
    d = tmp_path_factory.mktemp("dance")
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=c=0xe1d2be:s=360x640:r=30",
        "-f", "lavfi", "-i", "color=c=0x7a5c9c:s=55x200:r=30",
        "-f", "lavfi", "-i", "color=c=0xb03a3a:s=55x200:r=30",
        "-f", "lavfi", "-i", "aevalsrc='0.8*sin(2*PI*55*t)*exp(-mod(t-0.25,0.5)*18)+0.05*sin(2*PI*330*t)':s=44100:d=20",
        "-filter_complex",
        "[0:v][1:v]overlay=x='85+30*sin(2*PI*t)':y='280-20*abs(sin(2*PI*t))':shortest=1[a];"
        "[a][2:v]overlay=x='210-30*sin(2*PI*t)':y='280-20*abs(cos(2*PI*t))':shortest=1,format=yuv420p[v]",
        "-map", "[v]", "-map", "3:a", "-t", "20", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
        str(d / "IMG_0001.MOV")], check=True)
    return d


def test_single_clip_uses_smart_cuts(dance_dir):
    plan = EditPlan(duration=12)
    tl = prepare_timeline(find_media(dance_dir), plan)
    assert tl.continuous and tl.audio_clip is not None
    assert not plan.show_end_card and tl.total == pytest.approx(12, abs=0.05)
    # one clip in real time: every piece starts where the output clock says
    clip_start = tl.shots[0].start
    for s, st in zip(tl.shots, tl.starts):
        assert s.start == pytest.approx(clip_start + st, abs=0.002)
    # framing changes, opens and closes wide, never repeats back to back
    zooms = [s.zoom for s in tl.shots]
    assert zooms[0] == 1.0 and zooms[-1] == 1.0 and max(zooms) > 1.2
    assert all(a != b for a, b in zip(zooms, zooms[1:]))
    # hard cuts by default, on the clip's own beat
    assert all(k == "cut" for k in tl.kinds[1:])
    assert tl.beats is not None
    for c in tl.cuts:
        clip_t = c["at"] + clip_start
        assert min(abs(clip_t - b) for b in tl.beats.times) < 0.04


def test_framing_keeps_everyone_in_frame():
    zoom, (fx, fy) = framing_for("close", (0.5, 0.55, 0.7, 0.3))
    assert zoom == pytest.approx(1 / (0.7 * 1.15), abs=0.01)   # limited by the 70%-wide group
    assert framing_for("close", (0.5, 0.5, 0.2, 0.2))[0] == 1.7
    z, (fx, fy) = framing_for("medium", (0.95, 0.5, 0.1, 0.1))
    assert fx <= 1 - 0.5 / z + 1e-6                             # crop never leaves the frame


def test_smart_cut_render_keeps_sound_in_sync(dance_dir, tmp_path):
    plan = EditPlan(duration=8, title="Dance Day")
    tl = prepare_timeline(find_media(dance_dir), plan)
    out = render(tl, plan, tmp_path / "dance.mp4")

    def audio(path, ss, d):
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(ss), "-t", str(d), "-i", str(path),
                            "-ac", "1", "-ar", "8000", "-f", "f32le", "-"], capture_output=True)
        return np.frombuffer(r.stdout, np.float32)

    o = audio(out, 3, 3)
    src = dance_dir / "IMG_0001.MOV"
    scores = {}
    for ms in range(0, 601, 10):
        s = audio(src, 3 + ms / 1000, 3)
        n = min(len(o), len(s))
        scores[ms] = float(np.dot(o[:n], s[:n]) / np.sqrt(np.dot(o[:n], o[:n]) * np.dot(s[:n], s[:n]) + 1e-9))
    best = max(scores, key=scores.get)
    assert abs(best / 1000 - tl.shots[0].start) < 0.02 and scores[best] > 0.95


def test_several_pieces_from_one_long_clip(tmp_path):
    from conftest import make_clip, make_photo
    make_clip(tmp_path / "a_long.mp4", 14)
    make_photo(tmp_path / "b.jpg", 90)
    plan = EditPlan(duration=20, drop_blurry=False)          # 2 files, ~11 shots wanted
    shots = pick_shots(find_media(tmp_path), plan)
    long_starts = [s.start for s in shots if s.asset.name == "a_long.mp4"]
    assert len(long_starts) >= 4                               # several pieces from the long clip
    assert long_starts == sorted(long_starts)                  # in time order
    ends = [s.start + s.length for s in shots if s.asset.name == "a_long.mp4"]
    assert all(e <= n + 1e-6 for e, n in zip(ends, long_starts[1:]))  # no overlap


def test_end_card_only_when_selling():
    assert not EditPlan().show_end_card
    assert EditPlan(price="$5").show_end_card and EditPlan(handle="@x").show_end_card
    assert not EditPlan(price="$5", end_card=False).show_end_card


def test_stop_all_kills_running_ffmpeg():
    t = threading.Thread(target=lambda: procs.run(["sleep", "30"]), daemon=True)
    t.start()
    time.sleep(0.3)
    assert procs.stop_all() == 1
    t.join(timeout=5)
    assert not t.is_alive()
