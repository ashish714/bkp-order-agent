"""A chat session around the Reel agent.

The user uploads photos/clips, then chats: "15 sec warm reel for our candle,
$24" -> video. "Make it shorter" / "price is $19 now" / "drop the shaky clip"
-> new video. Each turn is one Claude call: Claude either replies in words
(e.g. asks a question) or calls the edit_video tool, which updates the plan
and re-renders.

Without an API key the session falls back to simple mode: keywords in each
message are merged into the current plan.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .media import find_media, pick_shots
from .plan import EditPlan
from .planner import MODEL, PLAN_SCHEMA, simple_fields
from .render import Timeline, build_timeline, render

AUDIO_EXT = {".mp3", ".m4a", ".wav", ".aac", ".ogg"}

EDIT_TOOL = {
    "name": "edit_video",
    "description": (
        "Create or update the video. Pass only the plan fields that should change; "
        "everything else keeps its current value. The video is re-rendered after the call."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            **{k: v for k, v in PLAN_SCHEMA["properties"].items()},
            "exclude_files": {"type": "array", "items": {"type": "string"},
                              "description": "File names to leave out of the video."},
            "include_files": {"type": "array", "items": {"type": "string"},
                              "description": "File names to bring back in (undo an exclude)."},
            "order": {"type": "array", "items": {"type": "string"},
                      "description": "File names in the order they should appear. Omit to keep the current order."},
        },
    },
}

SYSTEM = """You are Reel Agent, a friendly video editor for small shop owners, like CapCut \
but driven by chat. They upload product photos and clips and say what they want; you edit \
by calling edit_video, which re-renders the video.

What you can do (all through edit_video):
- Formats 9:16 / 1:1 / 4:5 / 16:9; length; pace; speed (whole video or per clip); reverse or \
mirror clips; drop, restore or reorder files.
- 45+ transitions: fade, dissolve, flash, fade_black, slide/push/wipe/cover/reveal in any \
direction, zoom, circle, radial, pixelize, blur, squeeze, slice, wind, doors, and drawn ones: \
spin, spin_left, flip, flip_up, cube_left/right, zoom_in, zoom_out, whip_left/right/up, glitch, \
shake. 'mixed' varies them. Set one for every cut with `transition`, or single cuts with \
`cut_transitions` (use the cut numbers and times in the state).
- Beat sync: cuts land on the music's beats (needs a music file; the state says if there is one).
- Filters (look), adjustments (brightness, contrast, saturation, warmth), effects (vignette, \
grain, letterbox, shake, retro, glow, sharpen, pulse_on_beat, flash_on_beat).
- Blur-fill canvas (fit=blur) so nothing gets cropped.
- Text: title, captions, timed texts, styles (shadow, outline, box, neon), animations (fade, \
slide, words, typewriter), position. Price tag and branded end card with call to action.
- Audio: music track (they upload it), keep clips' own sound, music volume.

How to work:
- If the files are uploaded and the request is clear enough, call edit_video right away. \
Don't ask for details you can sensibly default (15 s, 9:16 reel, a look that fits the mood).
- For follow-ups, call edit_video with only the fields that change. For list fields (effects, \
captions, texts, cut_transitions, clip lists) send the complete new list.
- "At 15 seconds" means the cut nearest 15 s: use cut_transitions with its cut number.
- Never invent prices, discounts, handles or product claims. Leave them empty if not given.
- Titles and captions: short and punchy. No hashtags or emojis unless the user uses them.
- If no files are uploaded yet, ask them to add photos or clips first.
- Replies: one or two short sentences of plain text, no markdown. Say what you changed.
- Not possible here: AI-generated footage, removing objects or backgrounds, voiceover, \
auto-captions from speech, stickers/emoji, keyframing a single moment. Say so briefly and \
offer the closest thing you can do."""


@dataclass
class Turn:
    reply: str
    video: str | None = None        # file name of the new render, if any
    plan: dict | None = None
    shots: list[dict] = field(default_factory=list)
    length: float = 0.0
    cuts: list[dict] = field(default_factory=list)
    error: str | None = None


class ReelChat:
    def __init__(self, workdir: str | Path, client=None, mode: str | None = None):
        self.dir = Path(workdir)
        self.media_dir = self.dir / "media"
        self.extras_dir = self.dir / "extras"
        self.out_dir = self.dir / "renders"
        for d in (self.media_dir, self.extras_dir, self.out_dir):
            d.mkdir(parents=True, exist_ok=True)
        mode = (mode or os.environ.get("REEL_AGENT_MODE") or "").lower()
        if not mode:
            mode = "claude" if (client is not None or os.environ.get("ANTHROPIC_API_KEY")) else "simple"
        self.mode = mode
        self._client = client
        self.plan: EditPlan | None = None
        self.exclude: set[str] = set()
        self.order: list[str] = []
        self.messages: list[dict] = []
        self.pending_tool_result: dict | None = None
        self.music: Path | None = None
        self.logo: Path | None = None
        self.renders = 0
        self.timeline: Timeline | None = None

    # ---------------------------------------------------------------- files
    def add_file(self, name: str, data: bytes) -> str:
        """Save an upload. Audio becomes the music track, logo*.png the end-card logo."""
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._") or "file"
        ext = Path(safe).suffix.lower()
        if ext in AUDIO_EXT:
            path = self.extras_dir / safe
            self.music = path
            kind = "music"
        elif safe.lower().startswith("logo"):
            path = self.extras_dir / safe
            self.logo = path
            kind = "logo"
        else:
            path = self.media_dir / safe
            kind = "media"
        path.write_bytes(data)
        return kind

    def files(self) -> list[str]:
        return [a.name for a in find_media(self.media_dir)]

    # ---------------------------------------------------------------- chat
    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def send(self, message: str) -> Turn:
        message = message.strip()
        if not message:
            return Turn("Tell me what kind of video you'd like.")
        if self.mode == "claude":
            return self._send_claude(message)
        return self._send_simple(message)

    def _state(self) -> str:
        files = self.files()
        lines = [f"Uploaded files ({len(files)}): " + (", ".join(files) or "none yet")]
        if self.exclude:
            lines.append("Excluded: " + ", ".join(sorted(self.exclude)))
        if self.order:
            lines.append("Custom order: " + ", ".join(self.order))
        lines.append("Music: " + (self.music.name if self.music else "none uploaded (beat sync and *_on_beat effects need one)"))
        lines.append("Logo: " + (self.logo.name if self.logo else "none"))
        lines.append("Current plan: " + (json.dumps(self.plan.to_dict()) if self.plan else "no video yet"))
        if self.timeline:
            tl = self.timeline
            lines.append(f"Current video: {tl.total:.1f}s ({tl.montage:.1f}s montage + end card)"
                         + (f", music at {tl.beats.bpm:.0f} BPM" if tl.beats else ""))
            lines.append("Shots: " + "; ".join(f"{i + 1}. {s.asset.name} {tl.starts[i]:.1f}s-"
                                               f"{tl.starts[i] + s.length:.1f}s" for i, s in enumerate(tl.shots)))
            lines.append("Cuts: " + ("; ".join(f"#{c['cut']} at {c['at']:.1f}s {c['transition']} "
                                               f"({c['from']} -> {c['to']})" for c in tl.cuts) or "none"))
        return "\n".join(lines)

    def _send_claude(self, message: str) -> Turn:
        content: list[dict] = []
        if self.pending_tool_result:
            content.append(self.pending_tool_result)
            self.pending_tool_result = None
        content.append({"type": "text", "text": f"[Current state]\n{self._state()}\n\n[User]\n{message}"})
        self.messages.append({"role": "user", "content": content})

        try:
            resp = self.client.messages.create(
                model=MODEL, max_tokens=2000, system=SYSTEM, tools=[EDIT_TOOL], messages=self.messages,
            )
        except Exception as e:  # network / auth problems: keep the session usable
            self.messages.pop()
            return Turn("I couldn't reach Claude just now. Check your API key and connection, then try again.",
                        error=str(e))

        blocks = [b.model_dump() if hasattr(b, "model_dump") else dict(b.__dict__) for b in resp.content]
        blocks = [{k: v for k, v in b.items() if k in ("type", "text", "id", "name", "input")} for b in blocks]
        self.messages.append({"role": "assistant", "content": blocks})

        text = " ".join(b["text"] for b in blocks if b["type"] == "text").strip()
        tool = next((b for b in blocks if b["type"] == "tool_use"), None)
        if not tool:
            return Turn(text or "OK.", plan=self.plan.to_dict() if self.plan else None)

        turn = self._apply_and_render(tool["input"] or {})
        summary = turn.error or f"Rendered {turn.video}: {len(turn.shots)} shots, " \
                                f"{turn.length}s long."
        self.pending_tool_result = {"type": "tool_result", "tool_use_id": tool["id"],
                                    "content": summary, **({"is_error": True} if turn.error else {})}
        if turn.error:
            turn.reply = (text + " " if text else "") + turn.error
        else:
            turn.reply = (text or "Here's your video.") + turn.reply
        return turn

    def _send_simple(self, message: str) -> Turn:
        m = message.lower()
        changes = simple_fields(message)
        base = self.plan.to_dict() if self.plan else {}
        dur = base.get("duration", 15.0)
        if "duration" not in changes:
            if re.search(r"\bshorter\b", m):
                changes["duration"] = max(6.0, dur * 0.7)
            elif re.search(r"\blonger\b", m):
                changes["duration"] = min(60.0, dur * 1.4)
        for name in self.files():
            stem = re.escape(Path(name).stem.lower())
            if re.search(rf"\b(remove|drop|delete|without|skip)\b.*\b{stem}\b", m):
                changes.setdefault("exclude_files", []).append(name)
            if re.search(rf"\b(reverse|backwards?|rewind)\b.*\b{stem}\b|\b{stem}\b.*\b(reversed?|backwards?)\b", m):
                changes["reverse_files"] = sorted(set(base.get("reverse_files", [])) | {name})
            if re.search(rf"\b(mirror|flip horizontally)\b.*\b{stem}\b", m):
                changes["mirror_files"] = sorted(set(base.get("mirror_files", [])) | {name})
            sp = re.search(rf"\b(slow(?:[- ]?mo| motion| down)?|speed up|faster|fast)\b.*\b{stem}\b", m)
            if sp:
                speeds = dict(base.get("clip_speeds", {}))
                speeds[name] = 0.5 if sp.group(1).startswith("slow") else 2.0
                changes["clip_speeds"] = speeds
        # effects and per-cut transitions add to what's there
        if changes.get("effects"):
            changes["effects"] = list(dict.fromkeys(base.get("effects", []) + changes["effects"]))
            for e in list(changes["effects"]):
                if re.search(rf"\b(remove|no|without)\b[^,.]*{e.split('_')[0]}", m):
                    changes["effects"].remove(e)
        if changes.get("cut_transitions"):
            keep = [c for c in base.get("cut_transitions", [])
                    if not any(c.get("at") == n.get("at") for n in changes["cut_transitions"])]
            changes["cut_transitions"] = keep + changes["cut_transitions"]
        if not changes and self.plan is not None:
            return Turn("I couldn't tell what to change. Try things like: \"shorter\", \"warm look\", "
                        "\"price $19\", \"square\", \"hard cuts\", or \"remove 02_pour\". "
                        "(Add an API key for full conversation.)", plan=base)
        turn = self._apply_and_render(changes)
        if not turn.error:
            turn.reply = ("Here's your video." if self.renders == 1 else "Updated.") + turn.reply
        else:
            turn.reply = turn.error
        return turn

    # ---------------------------------------------------------------- edit + render
    def _apply_and_render(self, changes: dict) -> Turn:
        changes = dict(changes)
        files = self.files()
        for f in changes.pop("exclude_files", None) or []:
            if f in files:
                self.exclude.add(f)
        for f in changes.pop("include_files", None) or []:
            self.exclude.discard(f)
        order = changes.pop("order", None)
        if order:
            self.order = [f for f in order if f in files]

        base = self.plan.to_dict() if self.plan else {}
        self.plan = EditPlan.from_dict({**base, **changes})

        assets = [a for a in find_media(self.media_dir) if a.name not in self.exclude]
        if not assets:
            return Turn("", plan=self.plan.to_dict(),
                        error="There are no photos or clips to use yet. Add some files first.")
        if self.order:
            rank = {n: i for i, n in enumerate(self.order)}
            assets.sort(key=lambda a: (rank.get(a.name, len(rank)), a.name.lower()))
        music = str(self.music) if self.music else None
        try:
            shots = pick_shots(assets, self.plan)
            tl = build_timeline(shots, self.plan, music)
            self.renders += 1
            name = f"reel_{self.renders:02d}_{int(time.time())}.mp4"
            render(tl, self.plan, self.out_dir / name, music=music,
                   logo=str(self.logo) if self.logo else None)
        except Exception as e:
            return Turn("", plan=self.plan.to_dict(), error=f"The render failed: {e}")
        self.timeline = tl
        note = ""
        if self.plan.beat_sync and not tl.beats:
            note = (" (Beat sync is on, but there's no music with a clear beat, so normal pacing was used.)"
                    if music else " (Upload a music file to use beat sync.)")
        return Turn(note, video=name, plan=self.plan.to_dict(),
                    shots=[{"file": s.asset.name, "seconds": s.length} for s in tl.shots],
                    length=round(tl.total, 1), cuts=tl.cuts)
