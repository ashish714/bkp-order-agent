"""Command line: reel-agent <folder> "<description>" [options]"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .media import find_media, pick_shots
from .plan import EditPlan
from .planner import make_plan
from .render import render, timeline


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="reel-agent",
        description="Turn a folder of raw photos and clips into an edited product Reel.",
        epilog='Example: reel-agent ./candle "15 sec warm reel for our new soy candle, $24, shop now, @glowco"',
    )
    ap.add_argument("folder", help="Folder with your photos and videos (used in filename order)")
    ap.add_argument("prompt", nargs="?", default="", help="Describe the video you want")
    ap.add_argument("-o", "--out", help="Output file (default: <folder>/reel.mp4)")
    ap.add_argument("--music", help="Background music file (mp3/m4a/wav)")
    ap.add_argument("--logo", help="Logo image for the end card (PNG with transparency works best)")
    ap.add_argument("--font", help="Path to a .ttf/.otf font for your brand")
    ap.add_argument("--brand-color", help="Override brand color, e.g. '#1B2A49'")
    ap.add_argument("--plan", help="Use a saved plan JSON instead of the description")
    ap.add_argument("--save-plan", help="Write the plan to this JSON file (edit it and re-run with --plan)")
    ap.add_argument("--mode", choices=["simple", "claude"], help="Planner (default: claude if ANTHROPIC_API_KEY is set)")
    ap.add_argument("--keep-all", action="store_true", help="Don't drop blurry shots")
    ap.add_argument("--dry-run", action="store_true", help="Show the plan and shot list, don't render")
    args = ap.parse_args(argv)

    folder = Path(args.folder)
    try:
        assets = find_media(folder)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    if not assets:
        print(f"Error: no photos or videos in {folder}", file=sys.stderr)
        return 2

    if args.plan:
        plan, mode = EditPlan.from_dict(json.loads(Path(args.plan).read_text())), "file"
    else:
        plan, mode = make_plan(args.prompt, [a.name for a in assets], args.mode)
    overrides = {}
    if args.brand_color:
        overrides["brand_color"] = args.brand_color
    if args.keep_all:
        overrides["drop_blurry"] = False
    if overrides:
        plan = EditPlan.from_dict({**plan.to_dict(), **overrides})

    print(f"Planner: {mode}")
    print(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
    if args.save_plan:
        Path(args.save_plan).write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))

    print(f"\nScanning {len(assets)} files...")
    shots = pick_shots(assets, plan)
    skipped = [a.name for a in assets if a.name not in {s.asset.name for s in shots}]
    for s in shots:
        extra = f" from {s.start:.1f}s" if s.asset.kind == "video" else ""
        print(f"  {s.asset.name:<32} {s.asset.kind:<5} {s.length:.1f}s{extra}")
    if skipped:
        print(f"  skipped (blurry or not needed): {', '.join(skipped)}")
    tl = timeline(shots, plan)
    print(f"Length: {tl['total']:.1f}s")

    if args.dry_run:
        return 0
    out = Path(args.out) if args.out else folder / "reel.mp4"
    t0 = time.time()
    render(shots, plan, out, music=args.music, logo=args.logo, font_path=args.font)
    print(f"\nDone in {time.time() - t0:.0f}s -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
