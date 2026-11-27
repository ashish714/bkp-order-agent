"""Find the beat in a music track (numpy only, no extra installs).

1. Decode to mono 11 kHz with ffmpeg.
2. Onset strength = spectral flux (how much the spectrum jumps frame to frame).
3. Tempo = strongest autocorrelation peak between 70 and 180 BPM.
4. Phase = the offset whose beat grid lines up with the most onset energy.

Good enough for pop / hip-hop / EDM / most Reels music. Free-tempo or
ambient tracks give a weak beat; callers fall back to normal pacing then.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SR = 11025
HOP = 128
FRAME = 512


@dataclass
class Beats:
    bpm: float
    period: float          # seconds per beat
    first: float           # time of the first beat
    times: list[float]     # every beat time in the track
    confidence: float      # 0..1, how clearly the rhythm repeats (< 0.4 = no usable beat)

    def grid(self, until: float) -> list[float]:
        return [t for t in self.times if t <= until]


def load_mono(path: str | Path, max_seconds: float = 180) -> np.ndarray:
    res = subprocess.run(
        ["ffmpeg", "-v", "error", "-t", str(max_seconds), "-i", str(path), "-ac", "1", "-ar", str(SR),
         "-f", "f32le", "-"],
        capture_output=True,
    )
    return np.frombuffer(res.stdout, dtype=np.float32)


def onset_envelope(y: np.ndarray) -> np.ndarray:
    if len(y) < FRAME * 4:
        return np.zeros(1)
    n = 1 + (len(y) - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    frames = y[idx] * np.hanning(FRAME)[None, :]
    mag = np.log1p(10 * np.abs(np.fft.rfft(frames, axis=1)))
    flux = np.maximum(np.diff(mag, axis=0), 0).sum(axis=1)
    flux = np.concatenate([[0], flux])
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")  # remove slow loudness changes
    return np.maximum(flux, 0)


def detect_beats(path: str | Path) -> Beats | None:
    y = load_mono(path)
    if len(y) < SR * 3:
        return None
    y = y / (np.abs(y).max() + 1e-9)          # loudness-independent
    env = onset_envelope(y)
    fps = SR / HOP
    if env.max() <= 0:
        return None
    strength = float(np.percentile(env, 95))  # drones/pads ~1, music with drums 10+
    env = env / env.max()

    centred = env - env.mean()
    nfft = 1 << (2 * len(centred) - 1).bit_length()
    spec = np.fft.rfft(centred, nfft)
    ac = np.fft.irfft(spec * np.conj(spec), nfft)[:len(centred)]
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 70)
    if hi >= len(ac):
        return None
    lags = np.arange(lo, hi + 1)
    # prefer tempos near 120 BPM a little (common for Reels music, avoids half/double errors)
    bpms = 60 * fps / lags
    weight = np.exp(-0.5 * (np.log2(bpms / 120) / 0.9) ** 2)
    scores = ac[lags] * weight
    lag = lags[int(np.argmax(scores))]
    # refine to sub-frame with a parabola
    if lo < lag < hi:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        denom = a - 2 * b + c
        lag = lag + (0.5 * (a - c) / denom if denom else 0)
    period = lag / fps
    # periodicity: how strongly the onset pattern repeats at this tempo (0..1)
    confidence = float(np.clip(ac[int(round(lag))] / (ac[0] + 1e-9), 0, 1)) * min(1.0, strength / 5)

    # phase: try offsets within one period
    best_phase, best = 0.0, -1.0
    for ph in np.linspace(0, period, 48, endpoint=False):
        idx = ((np.arange(ph, len(env) / fps, period)) * fps).astype(int)
        idx = idx[idx < len(env)]
        s = env[idx].sum()
        if s > best:
            best, best_phase = s, float(ph)
    best_phase += FRAME / 4 / SR  # onset frames are stamped at the window start; nudge to the hit
    duration = len(y) / SR
    times = [round(float(t), 3) for t in np.arange(best_phase, duration, period)]
    return Beats(bpm=round(float(60 / period), 1), period=float(period), first=best_phase, times=times,
                 confidence=round(confidence, 2))


def fit_lengths_to_beats(lengths: list[float], overlaps: list[float], beats: Beats,
                         min_len: list[float]) -> list[float]:
    """Adjust shot lengths so every cut (middle of each transition) lands on a beat.

    lengths[i]   current length of shot i
    overlaps[i]  overlap of the transition *before* shot i (overlaps[0] = 0)
    min_len[i]   shortest each shot may be (room for its transitions)
    """
    n = len(lengths)
    if n < 2 or not beats.times:
        return lengths
    # beats per shot: closest whole number of beats to the planned length (1, 2, 4...)
    out = list(lengths)
    start = 0.0
    for i in range(n - 1):
        ov_next = overlaps[i + 1]
        planned_cut = start + lengths[i] - ov_next / 2
        candidates = [t for t in beats.times if t >= start + min_len[i] - ov_next / 2 - 1e-6]
        if not candidates:
            break
        cut = min(candidates, key=lambda t: abs(t - planned_cut))
        out[i] = round(cut - start + ov_next / 2, 3)
        start = start + out[i] - ov_next
    return out
