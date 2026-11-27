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

from .plan import FORMATS, LOOKS, PACES, TRANSITIONS, EditPlan

MODEL = "claude-opus-5-5"

COLOR_NAMES = {
    "black": "#111111", "white": "#FFFFFF", "red": "#D7263D", "pink": "#F06292",
    "orange": "#F28C28", "yellow": "#F4C430", "green": "#2E7D32", "teal": "#00897B",
    "blue": "#1E5EFF", "navy": "#1B2A49", "purple": "#6A1B9A", "gold": "#C9A227",
    "brown": "#6D4C41", "beige": "#D8C3A5", "cream": "#F3E9D2", "grey": "#555555", "gray": "#555555",
}


# ---------------------------------------------------------------- simple mode
def simple_plan(prompt: str) -> EditPlan:
    p = prompt.lower()
    plan: dict = {}

    if re.search(r"\b(16:9|youtube|landscape|horizontal)\b", p):
        plan["format"] = "youtube"
    elif re.search(r"\b(1:1|square)\b", p):
        plan["format"] = "square"
    elif re.search(r"\b(4:5|feed post|portrait post)\b", p):
        plan["format"] = "portrait"
    else:
        plan["format"] = "reel"

    m = re.search(r"(\d{1,2})\s*(?:-|\s)?\s*(s|sec|secs|second|seconds)\b", p)
    if m:
        plan["duration"] = float(m.group(1))
    elif re.search(r"\b(1|one)\s*(min|minute)\b", p):
        plan["duration"] = 60.0

    looks = {
        "warm": r"warm|golden|cozy|cosy|autumn|sunset",
        "cool": r"\bcool tones?\b|icy|winter|blue tones?",
        "moody": r"moody|dark|cinematic|dramatic",
        "vibrant": r"vibrant|colorful|colourful|bright|pop|punchy|summer",
        "bw": r"black and white|black & white|b&w|monochrome|grayscale|greyscale",
        "clean": r"clean|minimal|crisp|studio",
    }
    for look, pat in looks.items():
        if re.search(pat, p):
            plan["look"] = look
            break

    if re.search(r"upbeat|fast|energetic|quick|hype|snappy", p):
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

    return EditPlan.from_dict(plan)


# ---------------------------------------------------------------- Claude mode
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "format": {"type": "string", "enum": list(FORMATS)},
        "duration": {"type": "number", "description": "Montage length in seconds, 5-90. Reels default 15."},
        "look": {"type": "string", "enum": list(LOOKS)},
        "transition": {"type": "string", "enum": list(TRANSITIONS)},
        "pace": {"type": "string", "enum": list(PACES)},
        "speed": {"type": "number", "description": "1 = normal, 0.5 = slow motion for video clips."},
        "title": {"type": "string", "description": "Short hook over the first shot, max ~5 words."},
        "product": {"type": "string"},
        "price": {"type": "string", "description": "With currency symbol, exactly as the user gave it. Empty if not given."},
        "cta": {"type": "string", "description": "2-3 word call to action."},
        "handle": {"type": "string"},
        "captions": {"type": "array", "items": {"type": "string"},
                     "description": "0-4 very short overlay lines (max ~5 words each) for middle shots."},
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
