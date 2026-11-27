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
from .render import render, timeline

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

SYSTEM = """You are Reel Agent, a friendly video editor for small shop owners. They upload \
product photos and clips and tell you what they want; you make short videos for Instagram \
Reels, TikTok and Shorts by calling edit_video.

How to work:
- If the files are uploaded and the request is clear enough, call edit_video right away. \
Don't ask for details you can sensibly default (15 s, 9:16 reel, a look that fits the mood).
- For follow-ups ("shorter", "price is $19", "remove the blurry one", "more energy"), call \
edit_video with just the fields that change.
- Never invent prices, discounts, handles or product claims. Leave them empty if not given.
- Titles and captions: short and punchy. No hashtags or emojis unless the user uses them.
- If no files are uploaded yet, ask them to add photos or clips first.
- Keep replies to one or two short sentences. Say what you changed. Don't describe the \
whole plan back; they can watch the video.
- You can't add beat-synced cuts, auto-captions from speech, new AI footage or object \
removal. Say so briefly if asked."""


@dataclass
class Turn:
    reply: str
    video: str | None = None        # file name of the new render, if any
    plan: dict | None = None
    shots: list[dict] = field(default_factory=list)
    length: float = 0.0
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
        lines.append("Music: " + (self.music.name if self.music else "none"))
        lines.append("Logo: " + (self.logo.name if self.logo else "none"))
        lines.append("Current plan: " + (json.dumps(self.plan.to_dict()) if self.plan else "no video yet"))
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
            turn.reply = text or "Here's your video."
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
            stem = Path(name).stem.lower()
            if re.search(rf"\b(remove|drop|delete|without|skip)\b.*\b{re.escape(stem)}\b", m):
                changes.setdefault("exclude_files", []).append(name)
        if not changes and self.plan is not None:
            return Turn("I couldn't tell what to change. Try things like: \"shorter\", \"warm look\", "
                        "\"price $19\", \"square\", \"hard cuts\", or \"remove 02_pour\". "
                        "(Add an API key for full conversation.)", plan=base)
        turn = self._apply_and_render(changes)
        if not turn.error:
            turn.reply = "Here's your video." if self.renders == 1 else "Updated."
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
        try:
            shots = pick_shots(assets, self.plan)
            self.renders += 1
            name = f"reel_{self.renders:02d}_{int(time.time())}.mp4"
            render(shots, self.plan, self.out_dir / name, music=str(self.music) if self.music else None,
                   logo=str(self.logo) if self.logo else None)
        except Exception as e:
            return Turn("", plan=self.plan.to_dict(), error=f"The render failed: {e}")
        return Turn("", video=name, plan=self.plan.to_dict(),
                    shots=[{"file": s.asset.name, "seconds": s.length} for s in shots],
                    length=round(timeline(shots, self.plan)["total"], 1))
