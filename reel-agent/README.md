# Reel Agent

Turn a folder of raw phone photos and clips, plus one sentence, into an edited
product Reel for a small shop.

```bash
reel-agent ./candle "15 sec warm reel for our new soy candle, \$24, shop now, @glowco, brown brand color"
```

Sample output: [`examples/sample-reel.mp4`](examples/sample-reel.mp4)

What you get: a 9:16 MP4 with the best shots in order, slow zooms on photos, the
sharpest part of each clip, a color look, transitions, an opening title, a price
tag, short captions, music and a branded end card ("Soy Candle · $24 · SHOP NOW · @glowco").

## What it does

1. **Edits from one sentence.** You describe the video, and it builds an edit plan.
2. **Picks the shots.** It uses files in filename order (`01_…`, `02_…`) and drops clearly blurry ones.
3. **Finds the best part of each clip.** It samples each video and keeps the sharpest, steadiest window.
4. **Brings photos to life.** Slow zooms and pans (Ken Burns), varied from shot to shot.
5. **Four sizes:** Reel/TikTok/Shorts 9:16, square 1:1, feed 4:5, YouTube 16:9.
6. **Color looks:** natural, warm, cool, moody, vibrant, clean, black and white.
7. **Transitions and pacing:** fade, slide, zoom, wipe or hard cuts; slow, medium or fast pacing; slow motion.
8. **Text:** opening title, a price tag in your brand color, short captions.
9. **End card:** product, price, CTA button, logo and @handle.
10. **Music:** fitted to the video length with fade-in and fade-out. With no music it adds a silent track so Instagram accepts the file.

It doesn't sync cuts to the beat, auto-caption speech, remove objects or generate new footage.

## Two modes

| | Simple mode (default) | AI mode |
|---|---|---|
| Needs an API key | No | Yes, `ANTHROPIC_API_KEY` |
| Understands | Keywords: "warm", "15 sec", "$24", "square", "hard cuts", "@handle", "navy brand color" | Loose descriptions: "cozy autumn vibe for the candle, keep it short". Also writes the title and captions |

It uses AI mode when `ANTHROPIC_API_KEY` is set. Force a mode with `--mode simple|claude`.

## Setup

You need Python 3.10+ and ffmpeg.

```bash
# Mac
brew install ffmpeg
# Windows
winget install ffmpeg

cd reel-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[ai,heic,dev]"     # ai = Claude mode, heic = iPhone photos
```

## Usage

```bash
reel-agent <folder> "<description>" [options]

  -o, --out FILE        output file (default: <folder>/reel.mp4)
  --music FILE          background music (mp3 / m4a / wav)
  --logo FILE           logo for the end card (transparent PNG works best)
  --font FILE           your brand font (.ttf / .otf)
  --brand-color HEX     override the brand color, e.g. '#1B2A49'
  --keep-all            don't drop blurry shots
  --dry-run             show the plan and shot list without rendering
  --save-plan FILE      save the plan as JSON...
  --plan FILE           ...edit it by hand, then render from it
```

Examples:

```bash
reel-agent ./drop "square post, vibrant, fast, Rs 1499, DM to order, @lorven.interio"
reel-agent ./shoot "youtube, moody, slow motion, 30 seconds" --music track.mp3
reel-agent ./candle "cozy autumn vibe, short" --mode claude --save-plan plan.json --dry-run
```

A plan file looks like this (every field is optional):

```json
{
  "format": "reel", "duration": 14, "look": "warm", "transition": "slide", "pace": "medium",
  "title": "Autumn glow", "product": "Soy Candle", "price": "$24", "cta": "Shop now",
  "handle": "@glowco", "captions": ["Hand-poured", "Clean 40-hour burn"],
  "brand_color": "#6D4C41", "text_color": "#FFFFFF", "end_card": true
}
```

`duration` is the length of the montage. The end card adds about 2 seconds.

## How it works

```
folder + description
   │
   ├─ planner.py   simple_plan() keywords  ─┐
   │               claude_plan() Claude    ─┴─► EditPlan (plan.py)
   │
   ├─ media.py     find files → score sharpness → drop blurry → choose shots and lengths
   ├─ overlays.py  draw title / price tag / captions / end card as PNGs (Pillow)
   └─ render.py    ffmpeg: one clip per shot (zoom/pan, look) → xfade chain
                   → overlays → music → MP4 (H.264 + AAC, faststart)
```

Claude mode asks `claude-opus-5-5` to fill in the plan through a forced tool call,
so the output is always valid JSON. It's told never to invent a price or claim
you didn't give.

## Tests

```bash
pytest
```

The tests generate their own photos and clips with ffmpeg and render real videos
(about 90 seconds). Claude mode is tested with a fake client, so no key is needed.

## Ideas for next

- Beat-synced cuts (detect beats in the music track)
- Auto captions from speech with Whisper
- A folder watcher or a WhatsApp/Instagram upload flow for shop owners
- Saved brand kits per shop (colors, font, logo, handle)
