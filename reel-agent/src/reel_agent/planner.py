"""Turn a free-text description into an EditPlan.

simple_plan()  - keyword rules, no API key, free.
claude_plan()  - Claude reads the description (and the list of files) and
                 fills in the plan, so "cozy autumn vibe for our new candle,
                 $24, keep it short" just works.
make_plan()    - picks Claude when ANTHROPIC_API_KEY is set (or REEL_AGENT_MODE says so).
"""
from __future__ import annotations

import json
import os
import re

from .plan import (CUSTOM_TRANSITIONS, EFFECTS, FORMATS, LOOKS, NATIVE_TRANSITIONS, PACES, POSITIONS,
                   TEXT_ANIMATIONS, TEXT_STYLES, TRANSITIONS, EditPlan)

MODEL = "claude-opus-5-5"

COLOR_NAMES = {
    "black": "#111111", "white": "#FFFFFF", "red": "#D7263D", "pink": "#F06292",
    "orange": "#F28C28", "yellow": "#F4C430", "green": "#2E7D32", "teal": "#00897B",
    "blue": "#1E5EFF", "navy": "#1B2A49", "purple": "#6A1B9A", "gold": "#C9A227",
    "brown": "#6D4C41", "beige": "#D8C3A5", "cream": "#F3E9D2", "grey": "#555555", "gray": "#555555",
}


# ---------------------------------------------------------------- simple mode
def simple_plan(prompt: str) -> EditPlan:
    return EditPlan.from_dict(simple_fields(prompt))


def simple_fields(prompt: str) -> dict:
    """Only the plan fields the text actually mentions (used for chat edits too)."""
    p = prompt.lower()
    plan: dict = {}
    timed = re.findall(r"(?:at|@|around)\s*(?:the\s*)?(\d{1,2}(?:\.\d)?)\s*(?:s|sec|secs|second|seconds)?\b(?:\s*mark)?", p)
    p_dur = re.sub(r"(?:at|@|around)\s*(?:the\s*)?\d{1,2}(?:\.\d)?\s*(?:s|sec|secs|second|seconds)?\b(?:\s*mark)?", " ", p)

    if re.search(r"\b(16:9|youtube|landscape|horizontal)\b", p):
        plan["format"] = "youtube"
    elif re.search(r"\b(1:1|square)\b", p):
        plan["format"] = "square"
    elif re.search(r"\b(4:5|feed post|portrait post)\b", p):
        plan["format"] = "portrait"
    elif re.search(r"\b(9:16|reels?|tiktok|shorts|vertical|story)\b", p):
        plan["format"] = "reel"

    m = re.search(r"(\d{1,2})\s*(?:-|\s)?\s*(s|sec|secs|second|seconds)\b", p_dur)
    if m:
        plan["duration"] = float(m.group(1))
    elif re.search(r"\b(1|one)\s*(min|minute)\b", p_dur):
        plan["duration"] = 60.0

    looks = {
        "vintage": r"vintage|old school|retro film",
        "cinematic": r"cinematic|teal and orange|movie look",
        "film": r"\bfilm look|\bfilmic|analog",
        "golden": r"golden hour|golden",
        "pastel": r"pastel|soft colou?rs",
        "noir": r"\bnoir\b",
        "faded": r"faded|matte",
        "vivid": r"vivid",
        "warm": r"\bwarm\b|cozy|cosy|autumn|sunset",
        "cool": r"\bcool tones?\b|\bcool look\b|icy|winter|blue tones?",
        "moody": r"moody|dark|dramatic",
        "vibrant": r"vibrant|colorful|colourful|\bbright\b|\bpop\b(?!-)|punchy|summer",
        "bw": r"black and white|black & white|b&w|monochrome|grayscale|greyscale",
        "clean": r"clean|minimal|crisp|studio",
    }
    for look, pat in looks.items():
        if re.search(pat, p):
            plan["look"] = look
            break

    if re.search(r"upbeat|\bfast\b(?! transitions?)|energetic|\bquick\b(?! transitions?)|hype|snappy(?! transitions?)", p):
        plan["pace"] = "fast"
    elif re.search(r"\bslow\b|calm|relaxed|elegant|luxury|soft", p):
        plan["pace"] = "slow"

    if re.search(r"slow[- ]?mo|slow motion", p):
        plan["speed"] = 0.5
    if re.search(r"hard cuts?|no transitions?|jump cuts?", p):
        plan["transition"] = "cut"
    elif re.search(r"slide|swipe", p):
        plan["transition"] = "slide"
    elif re.search(r"zoom transition", p):
        plan["transition"] = "zoom"

    m = re.search(r"([$₹€£])\s?(\d+(?:,\d{2,3})*(?:\.\d{1,2})?)", prompt) or \
        re.search(r"\b(rs\.?|inr|usd)\s?(\d+(?:,\d{2,3})*(?:\.\d{1,2})?)", prompt, re.I)
    if m:
        sym = {"rs": "₹", "rs.": "₹", "inr": "₹", "usd": "$"}.get(m.group(1).lower(), m.group(1))
        plan["price"] = f"{sym}{m.group(2)}"

    m = re.search(r"[\"“']([^\"”']{2,40})[\"”']", prompt)
    if m:
        plan["title"] = m.group(1)

    m = re.search(r"(?:for (?:our|my|the) (?:new )?|launch(?:ing)? (?:our|my|the)? ?(?:new )?)"
                  r"([a-z0-9][a-z0-9 \-&']{1,30}?)(?=[,.!]| at | for | in | with |$)", p)
    if m:
        plan["product"] = m.group(1).strip().title()

    m = re.search(r"(@[a-z0-9_.]{2,30})", p)
    if m:
        plan["handle"] = m.group(1)

    for cta in ("shop now", "order now", "dm to order", "link in bio", "buy now", "visit us", "book now"):
        if cta in p:
            plan["cta"] = cta.title().replace("Dm", "DM")
            break

    m = re.search(r"#([0-9a-f]{6}|[0-9a-f]{3})\b", p)
    if m:
        plan["brand_color"] = "#" + m.group(1)
    else:
        m = re.search(r"\b(" + "|".join(COLOR_NAMES) + r")\b\s*(?:brand|colou?r|theme)", p)
        if m:
            plan["brand_color"] = COLOR_NAMES[m.group(1)]

    if re.search(r"no end ?card|no outro", p):
        plan["end_card"] = False

    if not plan.get("title") and plan.get("product"):
        plan["title"] = ("New: " + plan["product"]) if re.search(r"\bnew\b|launch", p) else plan["product"]

    # transitions: "spin transitions", "use flip", "mixed transitions", "spin at 15 sec"
    names = sorted(set(TRANSITIONS) | {"slide", "wipe", "whip", "cube", "zoom blur", "turn", "white flash",
                                       "dip to black", "crossfade", "push"}, key=len, reverse=True)
    found = []
    for name in names:
        pat = name.replace("_", "[ _-]?")
        for m in re.finditer(rf"\b{pat}\b", p):
            if not any(a <= m.start() < b for a, b, _ in found):
                found.append((m.start(), m.end(), name))
    found.sort()
    tr_words = [n for _, _, n in found if n not in ("cut", "zoom", "blur", "flash", "slice", "shake", "radial")
                or re.search(rf"\b{n}\s+(transition|effect)s?", p)]
    if re.search(r"mix(ed)?\s+transitions|different transitions|variety of transitions|all (kinds of )?transitions", p):
        plan["transition"] = "mixed"
    if timed and tr_words:
        plan["cut_transitions"] = [{"at": float(t), "transition": tr_words[min(k, len(tr_words) - 1)]}
                                   for k, t in enumerate(timed)]
    elif tr_words and "transition" not in plan:
        plan["transition"] = tr_words[0]
    if re.search(r"transitions? (?:of |at )?(\d(?:\.\d)?)\s*s", p):
        plan["transition_duration"] = float(re.search(r"transitions? (?:of |at )?(\d(?:\.\d)?)\s*s", p).group(1))
    elif re.search(r"(quick|fast|snappy) transitions?", p):
        plan["transition_duration"] = 0.3

    if re.search(r"\bbeats?\b|on beat|beat[- ]?sync|sync(ed)? to (the )?music|to the music", p):
        plan["beat_sync"] = True

    effect_words = {
        "vignette": r"vignette", "grain": r"\bgrain|grainy|film grain", "letterbox": r"letterbox|black bars|cinema bars",
        "shake": r"camera shake|shaky effect|shake effect", "retro": r"\bvhs\b|retro|glitchy look",
        "glow": r"\bglow(ing)? effect|dreamy|soft glow", "sharpen": r"sharpen|sharper",
        "pulse_on_beat": r"pulse|zoom on (the )?beat|bounce on (the )?beat",
        "flash_on_beat": r"flash on (the )?beat|beat flash|strobe",
    }
    fx = [e for e, pat in effect_words.items() if re.search(pat, p)]
    if fx:
        plan["effects"] = fx
    if re.search(r"no effects|remove (the |all )?effects", p):
        plan["effects"] = []

    if re.search(r"blur(red)? (background|bg)|don'?t crop|no crop|fit (the )?whole|show the whole", p):
        plan["fit"] = "blur"
    if re.search(r"original (sound|audio)|keep (the )?(sound|audio)|with sound|clip audio", p):
        plan["keep_audio"] = True
    if re.search(r"mute|no (original )?sound|remove (the )?(clip )?audio", p):
        plan["keep_audio"] = False

    for word, key, val in (("brighter", "brightness", 0.35), ("darker", "brightness", -0.35),
                           ("more contrast", "contrast", 0.4), ("less contrast", "contrast", -0.3),
                           ("more saturated", "saturation", 0.4), ("more colou?r", "saturation", 0.4),
                           ("less saturated", "saturation", -0.4), ("warmer", "warmth", 0.5),
                           ("cooler", "warmth", -0.5)):
        if re.search(rf"\b{word}\b", p):
            plan[key] = val

    for style, pat in (("outline", r"outlined?|stroke"), ("box", r"boxed|text box|box(ed)? text|label"),
                       ("neon", r"\bneon\b"), ("shadow", r"shadow text|drop shadow")):
        if re.search(pat, p):
            plan["text_style"] = style
            break
    for anim, pat in (("words", r"word[- ]by[- ]word|pop[- ]?in words|tiktok captions"),
                      ("typewriter", r"typewriter|typing (effect|text)"),
                      ("slide", r"slide[- ]in text|text slides?|sliding text")):
        if re.search(pat, p):
            plan["text_animation"] = anim
            break
    m = re.search(r"(?:text|captions?) (?:at|on) (?:the )?(top|bottom|center|middle)", p)
    if m:
        plan["caption_position"] = "center" if m.group(1) == "middle" else m.group(1)

    return plan


# ---------------------------------------------------------------- Claude mode
_TRANSITION_ENUM = {"type": "string", "enum": list(TRANSITIONS)}
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "format": {"type": "string", "enum": list(FORMATS)},
        "duration": {"type": "number", "description": "Montage length in seconds, 5-90. Reels default 15."},
        "look": {"type": "string", "enum": list(LOOKS), "description": "Color filter."},
        "transition": {**_TRANSITION_ENUM, "description":
                       "Default transition for every cut. 'mixed' varies them (slide, zoom, whip, flash, spin...). "
                       "'cut' = hard cut."},
        "transition_duration": {"type": "number", "description": "Seconds per transition, 0.2-1.2 (default 0.5)."},
        "cut_transitions": {"type": "array", "description":
                            "Override single cuts. Use 'cut' (number from the cut list in the state) or 'at' "
                            "(seconds; the nearest cut is used).",
                            "items": {"type": "object", "properties": {
                                "cut": {"type": "integer"}, "at": {"type": "number"}, "transition": _TRANSITION_ENUM},
                                "required": ["transition"]}},
        "pace": {"type": "string", "enum": list(PACES)},
        "speed": {"type": "number", "description": "Speed for all video clips: 1 normal, 0.5 slow motion, 2 fast."},
        "beat_sync": {"type": "boolean", "description": "Land every cut on the music's beat. Needs a music file."},
        "brightness": {"type": "number", "description": "-1..1, 0 = unchanged."},
        "contrast": {"type": "number", "description": "-1..1, 0 = unchanged."},
        "saturation": {"type": "number", "description": "-1..1, 0 = unchanged."},
        "warmth": {"type": "number", "description": "-1 (cooler) .. 1 (warmer), 0 = unchanged."},
        "effects": {"type": "array", "items": {"type": "string", "enum": list(EFFECTS)},
                    "description": "Full list of effects to apply (send the whole list; [] removes all). "
                                   "*_on_beat effects need music."},
        "fit": {"type": "string", "enum": ["crop", "blur"],
                "description": "crop = fill the frame; blur = show the whole shot over a blurred copy."},
        "clip_speeds": {"type": "object", "additionalProperties": {"type": "number"},
                        "description": "Per-file speed, e.g. {\"02_pour.mp4\": 0.5}."},
        "reverse_files": {"type": "array", "items": {"type": "string"}, "description": "Video files to play backwards."},
        "mirror_files": {"type": "array", "items": {"type": "string"}, "description": "Files to flip horizontally."},
        "keep_audio": {"type": "boolean", "description": "Keep the clips' own sound (music is turned down under it)."},
        "music_volume": {"type": "number", "description": "0..1, default 0.8."},
        "title": {"type": "string", "description": "Short hook over the first shot, max ~5 words."},
        "product": {"type": "string"},
        "price": {"type": "string", "description": "With currency symbol, exactly as the user gave it. Empty if not given."},
        "cta": {"type": "string", "description": "2-3 word call to action."},
        "handle": {"type": "string"},
        "captions": {"type": "array", "items": {"type": "string"},
                     "description": "0-4 very short overlay lines (max ~5 words each), spread over the middle shots."},
        "texts": {"type": "array", "description": "Text at exact times, e.g. a 'Sale ends Sunday' at 10 s.",
                  "items": {"type": "object", "properties": {
                      "text": {"type": "string"}, "at": {"type": "number"}, "duration": {"type": "number"},
                      "position": {"type": "string", "enum": list(POSITIONS)}}, "required": ["text", "at"]}},
        "text_style": {"type": "string", "enum": list(TEXT_STYLES)},
        "text_animation": {"type": "string", "enum": list(TEXT_ANIMATIONS),
                           "description": "words = word-by-word pop-in, typewriter = letter by letter."},
        "caption_position": {"type": "string", "enum": list(POSITIONS)},
        "brand_color": {"type": "string", "description": "Hex like #1B2A49."},
        "text_color": {"type": "string", "description": "Hex with good contrast on brand_color."},
        "end_card": {"type": "boolean"},
        "drop_blurry": {"type": "boolean"},
    },
    "required": ["format", "duration", "look", "transition", "pace", "title", "cta", "brand_color", "text_color"],
}

SYSTEM = """You are the planning step of a video editor that makes short product videos \
for small retail shops (Instagram Reels, TikTok, Shorts). Read the shop owner's description \
and the list of media files, then call make_plan with the edit plan.

Rules:
- Never invent a price, discount, handle or product claim the user didn't give. Leave it empty.
- Titles and captions are punchy and short. No hashtags, no emojis unless the user used them.
- Default to a 15 second 9:16 reel unless the user says otherwise.
- Choose the look and pace that match the mood they describe."""


def claude_plan(prompt: str, media_names: list[str] | None = None, client=None) -> EditPlan:
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    files = "\n".join(f"- {n}" for n in (media_names or [])) or "(not listed)"
    msg = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=SYSTEM,
        tools=[{"name": "make_plan", "description": "Submit the edit plan.", "input_schema": PLAN_SCHEMA}],
        tool_choice={"type": "tool", "name": "make_plan"},
        messages=[{"role": "user", "content": f"Description:\n{prompt}\n\nMedia files:\n{files}"}],
    )
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use":
            data = block.input if isinstance(block.input, dict) else json.loads(block.input)
            return EditPlan.from_dict(data)
    raise RuntimeError("Claude did not return an edit plan")


def make_plan(prompt: str, media_names: list[str] | None = None, mode: str | None = None) -> tuple[EditPlan, str]:
    mode = (mode or os.environ.get("REEL_AGENT_MODE") or "").lower()
    if not mode:
        mode = "claude" if os.environ.get("ANTHROPIC_API_KEY") else "simple"
    if mode == "claude":
        return claude_plan(prompt, media_names), "claude"
    return simple_plan(prompt), "simple"
