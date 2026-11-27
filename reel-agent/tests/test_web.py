import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from reel_agent.web import server  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA", tmp_path / "data")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("REEL_AGENT_MODE", raising=False)
    return TestClient(server.app)


def test_page_loads(client):
    r = client.get("/")
    assert r.status_code == 200 and "Reel Agent" in r.text


def test_upload_chat_and_download(client, media_dir):
    sid = client.post("/api/session").json()["session"]
    files = [("files", (p.name, p.read_bytes())) for p in sorted(media_dir.iterdir())
             if p.name in {"01_front.jpg", "03_detail.jpg"}]
    files.append(("files", ("track.mp3", b"not really audio")))
    up = client.post(f"/api/session/{sid}/upload", files=files).json()
    assert up["files"] == ["01_front.jpg", "03_detail.jpg"] and up["music"] == "track.mp3"

    # the fake mp3 makes ffmpeg fail: the error comes back as a chat reply, not a crash
    bad = client.post(f"/api/session/{sid}/chat", json={"message": "6 sec reel"}).json()
    assert bad["video"] is None and "render failed" in bad["reply"]

    server._sessions[sid].music = None
    r = client.post(f"/api/session/{sid}/chat", json={"message": "6 sec square post, $9"}).json()
    assert r["video_url"] and r["plan"]["format"] == "square"
    v = client.get(r["video_url"])
    assert v.status_code == 200 and v.headers["content-type"] == "video/mp4" and len(v.content) > 10_000


def test_bad_session_and_path_traversal(client):
    assert client.post("/api/session/nope/chat", json={"message": "hi"}).status_code == 404
    sid = client.post("/api/session").json()["session"]
    assert client.get(f"/api/session/{sid}/video/..%2F..%2Fetc%2Fpasswd").status_code == 404
