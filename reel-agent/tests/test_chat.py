from types import SimpleNamespace

import pytest

from reel_agent.chat import ReelChat


class Block(SimpleNamespace):
    def model_dump(self):
        return dict(self.__dict__)


class ScriptedClaude:
    """Fake Anthropic client: returns the scripted replies in order, records requests."""
    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        # snapshot the messages: the session keeps mutating its list
        self.requests.append({**kw, "messages": [dict(m) for m in kw["messages"]]})
        return SimpleNamespace(content=self.replies.pop(0))


def tool(id_, **inp):
    return Block(type="tool_use", id=id_, name="edit_video", input=inp)


def text(t):
    return Block(type="text", text=t)


def load(chat, media_dir, names=None):
    for p in sorted(media_dir.iterdir()):
        if names is None or p.name in names:
            chat.add_file(p.name, p.read_bytes())


def test_claude_chat_renders_then_revises(media_dir, tmp_path):
    fake = ScriptedClaude(
        [text("Here's a warm 8-second Reel."),
         tool("t1", format="reel", duration=8, look="warm", transition="fade", pace="medium",
              title="New drop", price="$24", cta="Shop now", brand_color="#6D4C41", text_color="#FFFFFF")],
        [text("Price updated and the clip is gone."),
         tool("t2", price="$19", exclude_files=["02_unbox.mp4"])],
    )
    chat = ReelChat(tmp_path / "s", client=fake)
    load(chat, media_dir)
    assert chat.mode == "claude"

    t1 = chat.send("8 sec warm reel, $24")
    assert t1.error is None and t1.video and (chat.out_dir / t1.video).exists()
    assert t1.reply == "Here's a warm 8-second Reel."
    assert "02_unbox.mp4" in [s["file"] for s in t1.shots]

    t2 = chat.send("make it $19 and drop the unboxing clip")
    assert t2.video != t1.video
    assert t2.plan["price"] == "$19" and t2.plan["look"] == "warm"   # unchanged fields kept
    assert "02_unbox.mp4" not in [s["file"] for s in t2.shots]

    # second request carries the tool result for the first call, then the new message
    first = fake.requests[1]["messages"][-1]["content"]
    assert first[0]["type"] == "tool_result" and first[0]["tool_use_id"] == "t1"
    assert "make it $19" in first[1]["text"] and "Current plan" in first[1]["text"]
    roles = [m["role"] for m in fake.requests[1]["messages"]]
    assert roles == ["user", "assistant", "user"]


def test_claude_can_just_reply(tmp_path):
    fake = ScriptedClaude([text("Add some photos or clips first, then tell me what you'd like.")])
    chat = ReelChat(tmp_path / "s", client=fake)
    t = chat.send("make me a reel")
    assert t.video is None and "Add some photos" in t.reply
    assert "none yet" in fake.requests[0]["messages"][0]["content"][-1]["text"]


def test_claude_error_keeps_session_usable(tmp_path):
    def boom(**kw):
        raise RuntimeError("401 invalid x-api-key")
    chat = ReelChat(tmp_path / "s", client=SimpleNamespace(messages=SimpleNamespace(create=boom)))
    t = chat.send("hi")
    assert "API key" in t.reply and chat.messages == []


def test_simple_chat_flow(media_dir, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("REEL_AGENT_MODE", raising=False)
    chat = ReelChat(tmp_path / "s")
    assert chat.mode == "simple"
    load(chat, media_dir, {"01_front.jpg", "02_unbox.mp4", "03_detail.jpg"})

    t1 = chat.send("10 sec warm reel, $24")
    assert t1.video and t1.plan["duration"] == 10 and t1.plan["price"] == "$24"

    t2 = chat.send("shorter please, and remove 02_unbox")
    assert t2.plan["duration"] == pytest.approx(7.0)
    assert t2.plan["look"] == "warm"
    assert "02_unbox.mp4" not in [s["file"] for s in t2.shots]

    t3 = chat.send("hmm")
    assert t3.video is None and "couldn't tell" in t3.reply


def test_uploads_sorted_into_media_music_logo(tmp_path):
    chat = ReelChat(tmp_path / "s", mode="simple")
    assert chat.add_file("../../evil name.jpg", b"x") == "media"
    assert (chat.media_dir / "evil_name.jpg").exists()
    assert chat.add_file("song.mp3", b"x") == "music" and chat.music.name == "song.mp3"
    assert chat.add_file("Logo.png", b"x") == "logo" and chat.logo.name == "Logo.png"
    assert chat.files() == ["evil_name.jpg"]
