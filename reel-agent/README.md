# Reel Agent

Turn a folder of raw phone photos and clips, plus one sentence, into an edited
product Reel for a small shop.

```bash
reel-agent ./candle "15 sec warm reel for our new soy candle, \$24, shop now, @glowco, brown brand color"
```

Sample output: [`examples/sample-reel.mp4`](examples/sample-reel.mp4) · All features (spin, flip, whip, cube, glitch, zoom on the beat): [`examples/sample-features.mp4`](examples/sample-features.mp4) · Chat page: [`examples/chat-screens.png`](examples/chat-screens.png)

What you get: a 9:16 MP4 with the best shots in order, slow zooms on photos, the
sharpest part of each clip, a color look, transitions, an opening title, a price
tag, short captions, music and a branded end card ("Soy Candle · $24 · SHOP NOW · @glowco").

## What it does

The main CapCut editing tools, driven by a sentence instead of a timeline.

**Editing**
- **Smart cuts from one clip** (dance, talking, performance): upload a single video and it plays in real
  time for the full length, while cuts on the beat switch between wide, medium and close-up framing centred
  on the people moving. The clip's own sound runs unbroken, so voices and music stay in sync.
- **Several pieces per clip:** with few files and a longer video, long clips are cut into several pieces
  (in time order) instead of one.
- **Edits from one sentence**, then takes changes in chat ("shorter", "spin at 15 sec", "add grain").
- **Picks the shots:** files go in filename order, clearly blurry shots are dropped, and each clip's sharpest part is used.
- **Photos come alive** with slow zooms and pans (Ken Burns).
- **Speed:** slow motion or fast for the whole video or one clip. **Reverse** and **mirror** any clip.
- **Canvas:** 9:16, 1:1, 4:5, 16:9. **Blur-fill** shows the whole shot over a blurred copy instead of cropping.

**Transitions (45+)**
- Basic: fade, dissolve, flash (white), fade to black, grayscale fade.
- Movement: slide, push, wipe, cover and reveal, in any direction; doors, diagonal, squeeze, slice, wind.
- Camera: zoom, circle open/close, radial, pixelize, blur.
- CapCut-style (drawn frame by frame): **spin**, **3D flip**, **cube**, **zoom in/out blur**, **whip pan**, **glitch**, **shake**.
- One style for every cut, `mixed` for variety, or a **different transition per cut** ("glitch at 8 s, spin at 15 s").
- **Beat sync:** finds the beat in your music and lands every cut on it.

**Look**
- **15 filters:** natural, warm, cool, moody, vibrant, clean, black & white, vintage, film, cinematic, golden, pastel, noir, faded, vivid.
- **Adjust:** brightness, contrast, saturation, warmth.
- **Effects:** vignette, film grain, letterbox bars, camera shake, retro/VHS, glow, sharpen, **zoom pulse on the beat**, **flash on the beat**.

**Text**
- Opening title, captions, and text at exact times ("Sale ends Sunday" at 10 s).
- **Styles:** shadow, outline, box, neon. **Animations:** fade, slide in, word-by-word, typewriter. Top, center or bottom.
- Price tag in your brand color, plus an end card with product, price, CTA button, logo and @handle
  (only when there's a product, price or handle to show).

**Audio**
- Your music track, fitted and faded. **Keep the clips' own sound** (music turns down under it). Music volume.

**Not possible:** AI-generated footage, object/background removal, voiceover, auto-captions from speech, stickers/emoji, stabilizing shaky footage, keyframing one moment.

Extras that cost render time (effects, drawn transitions, word-by-word text, clip sound, beat sync) are only
used when you ask for them. Each render prints `[reel] using: …` in the Terminal. Quitting the app with
Ctrl+C also stops any render in progress.

## Chat version (easiest)

A web page where you drag in photos and clips, describe the video, watch it, and keep
chatting to change it.

```bash
pip install -e ".[ai,heic,web]"
export ANTHROPIC_API_KEY=sk-ant-...     # optional; without it you get simple (keyword) mode
reel-agent-web
```

Open **http://localhost:8000**, then:

1. Tap **＋ Add photos / videos**, or drag files in. Add an `.mp3` for music, and name your logo `logo.png` to put it on the end card.
2. Type what you want: *"15 sec Reel for our new soy candle, $24, cozy autumn vibe, @glowco"*.
3. Watch the video in the chat. Ask for changes: *"make it shorter"*, *"price is $19 now"*,
   *"drop the shaky clip"*, *"moody look"*, *"put the close-up first"*. Each change re-renders the video (usually under a minute).
4. Tap **Download** for the final MP4. Thumbnails that are dimmed weren't used.

In **AI mode** (API key set), Claude understands normal conversation, edits only what you ask
for, and can reorder or drop files by name. It costs about 2–3 cents per message.
In **simple mode** (no key), each message is read for keywords ("shorter", "warm", "$19",
"square", "remove 02_pour") and merged into the current video.

To open it on your phone over the same Wi-Fi, run `HOST=0.0.0.0 reel-agent-web` and go to
`http://<your-laptop-ip>:8000`. Anyone on that network can then reach it, so only do this on your home Wi-Fi.

While a video renders, the Terminal shows progress lines like `[reel] shot 3/9 …` and `[reel] final pass …`.
A typical Reel takes 1–3 minutes; many 4K clips plus drawn transitions and effects take longer.

Uploads and renders are saved in `~/.reel-agent/sessions/` (change it with `REEL_AGENT_DATA`).

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
   ├─ chat.py      ReelChat: one Claude call per message, edit_video tool → re-render
   ├─ web/         FastAPI server + single-page chat (upload, video, download)
   ├─ smartcut.py  one clip in real time: motion-tracked wide/medium/close framing cut on the beat
   ├─ procs.py     tracks ffmpeg processes so quitting the app stops them
   ├─ beats.py     beat detection (numpy) → cut times snapped to beats
   ├─ transitions.py  spin / flip / cube / whip / zoom / glitch / shake drawn with Pillow
   ├─ effects.py   filters, adjustments, effects as ffmpeg filter strings
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

- Auto captions from speech with Whisper
- Host the chat page so shop owners can use it from their phones (needs login and storage)
- Saved brand kits per shop (colors, font, logo, handle)
