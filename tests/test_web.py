from types import SimpleNamespace as NS

from fastapi.testclient import TestClient

from bkp_agent.agent import SupportAgent
from bkp_agent.simple import SimpleAgent
from bkp_agent.web import server


class Echo:
    def __init__(self):
        self.beta = NS(messages=NS(create=lambda **kw: NS(
            stop_reason="end_turn",
            content=[NS(type="text", text=f"echo {len(kw['messages'])}")])))


def test_chat_keeps_session(monkeypatch, store):
    monkeypatch.setattr(server, "_store", store)
    monkeypatch.setattr(server, "make_agent", lambda s: SupportAgent(s, client=Echo()))
    server._sessions.clear()
    c = TestClient(server.app)
    assert "BKP Support" in c.get("/").text
    r1 = c.post("/api/chat", json={"message": "hi"}).json()
    r2 = c.post("/api/chat", json={"message": "again", "session_id": r1["session_id"]}).json()
    assert r1["reply"] == "echo 1"
    assert r2["reply"] == "echo 3"  # user, assistant, user
    assert r2["session_id"] == r1["session_id"]
    assert c.post("/api/reset", json={"session_id": r1["session_id"]}).json() == {"ok": True}
    assert c.post("/api/chat", json={"message": ""}).status_code == 422


def test_simple_mode_without_api_key(monkeypatch, store):
    monkeypatch.setattr(server, "_store", store)
    monkeypatch.setattr(server, "make_agent", SimpleAgent)
    server._sessions.clear()
    c = TestClient(server.app)
    r = c.post("/api/chat", json={"message": "track BKP-10002 rahul@example.com"})
    assert r.status_code == 200
    assert "on its way" in r.json()["reply"]
