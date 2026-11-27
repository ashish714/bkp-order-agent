"""Filters (looks), adjustments and effects as ffmpeg filter strings."""
from __future__ import annotations

from .plan import EditPlan

LOOK_FILTERS = {
    "natural": "eq=saturation=1.05",
    "warm": "colorbalance=rs=0.07:gs=0.02:bs=-0.08:rm=0.04:bm=-0.04,eq=saturation=1.1:contrast=1.03",
    "cool": "colorbalance=rs=-0.05:bs=0.07:bm=0.04,eq=saturation=0.95:contrast=1.04",
    "moody": "eq=contrast=1.15:brightness=-0.04:saturation=0.8,colorbalance=bs=0.05:bh=0.03",
    "vibrant": "eq=saturation=1.35:contrast=1.06",
    "bw": "hue=s=0,eq=contrast=1.12",
    "clean": "eq=contrast=1.04:brightness=0.02:saturation=1.05",
    "vintage": "curves=preset=vintage,eq=saturation=0.85",
    "film": "curves=m='0/0.06 0.5/0.5 1/0.94',colorbalance=rs=0.04:bs=-0.03:bh=0.04,eq=saturation=0.9:contrast=1.05",
    "cinematic": "colorbalance=rs=-0.06:bs=0.08:rh=0.08:bh=-0.08,eq=contrast=1.12:saturation=1.05",
    "golden": "colorbalance=rs=0.04:gs=0.03:bs=-0.10:rm=0.05:gm=0.03:bm=-0.09:rh=0.03:gh=0.03:bh=-0.06,eq=saturation=1.08:brightness=0.02",
    "pastel": "eq=saturation=0.75:brightness=0.05:contrast=0.9,colorbalance=rs=0.03:bs=0.04",
    "noir": "hue=s=0,eq=contrast=1.35:brightness=-0.03,curves=preset=strong_contrast",
    "faded": "curves=all='0/0.12 1/0.92',eq=saturation=0.8",
    "vivid": "vibrance=intensity=0.6,eq=contrast=1.08",
}


def look_and_adjust(plan: EditPlan) -> str:
    parts = [LOOK_FILTERS[plan.look]]
    if any((plan.brightness, plan.contrast, plan.saturation)):
        parts.append(f"eq=brightness={plan.brightness * 0.25:.3f}:contrast={1 + plan.contrast * 0.5:.3f}"
                     f":saturation={1 + plan.saturation * 0.8:.3f}")
    if plan.warmth:
        # 6500K is neutral; warmer = lower temperature
        parts.append(f"colortemperature=temperature={6500 - plan.warmth * 2500:.0f}:mix=0.8")
    if "sharpen" in plan.effects:
        parts.append("unsharp=5:5:0.8")
    return ",".join(parts)


def final_effects(plan: EditPlan, montage_end: float, beats=None) -> list[str]:
    """Filters applied to the whole montage (not the end card), in order."""
    W, H = plan.size
    on = f"lt(t,{montage_end:.3f})"
    fx = []
    e = set(plan.effects)
    if "shake" in e:
        fx.append(f"scale={int(W * 1.06) // 2 * 2}:{int(H * 1.06) // 2 * 2},"
                  f"crop={W}:{H}:x='(iw-{W})/2+{on}*14*sin(t*21)':y='(ih-{H})/2+{on}*14*cos(t*17)'")
    if "flash_on_beat" in e and beats:
        ph = f"mod(t-{beats.first:.3f},{beats.period:.4f})"
        fx.append(f"eq=brightness='0.22*exp(-{ph}*16)*{on}':eval=frame")
    if "retro" in e:
        fx.append(f"rgbashift=rh=5:bh=-5:enable='{on}',noise=alls=16:allf=t:enable='{on}',"
                  f"eq=saturation=0.85:enable='{on}'")
    if "grain" in e:
        fx.append(f"noise=alls=10:allf=t+u:enable='{on}'")
    if "vignette" in e:
        fx.append(f"vignette=PI/4.5:enable='{on}'")
    if "letterbox" in e:
        bar = int(H * (0.11 if H > W else 0.08))
        fx.append(f"drawbox=x=0:y=0:w=iw:h={bar}:color=black:t=fill:enable='{on}',"
                  f"drawbox=x=0:y=ih-{bar}:w=iw:h={bar}:color=black:t=fill:enable='{on}'")
    return fx


def glow_graph(label_in: str, label_out: str, montage_end: float, size=(1080, 1920)) -> str:
    """Soft glow: blur a quarter-size copy (16x fewer pixels), scale it back, screen-blend."""
    W, H = size
    return (f"{label_in}split[gA][gB];[gB]scale={W // 4}:{H // 4},gblur=sigma=6,scale={W}:{H}[gC];"
            f"[gA][gC]blend=all_mode=screen:all_opacity=0.35:enable='lt(t,{montage_end:.3f})'{label_out}")


def pulse_graph(label_in: str, label_out: str, size, beats, montage_end: float) -> str:
    """Zoom punch on every beat: a 7% zoomed copy shown for the first 0.12 s after each beat."""
    W, H = size
    zw, zh = int(W * 1.07) // 2 * 2, int(H * 1.07) // 2 * 2
    on = (f"lt(mod(t-{beats.first:.3f}+0.02,{beats.period:.4f}),0.14)*gte(t,{beats.first - 0.02:.3f})"
          f"*lt(t,{montage_end:.3f})")
    return (f"{label_in}split[pA][pB];[pB]scale={zw}:{zh},crop={W}:{H}[pC];"
            f"[pA][pC]overlay=0:0:enable='{on}'{label_out}")
