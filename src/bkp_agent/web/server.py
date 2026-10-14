"""Web chat for the BKP support agent: `uvicorn bkp_agent.web.server:app`."""
from __future__ import annotations

import uuid
from collections import OrderedDict
from pathlib import Path

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from bkp_agent.agent import NO_CREDENTIALS_MSG, SupportAgent, is_missing_credentials
from bkp_agent.config import open_store

MAX_SESSIONS = 500

app = FastAPI(title="BKP Support Agent")
_store = open_store()
# In-memory chat sessions, oldest evicted first. Use a real session store
# (Redis, a DB) if you run more than one server process.
_sessions: OrderedDict[str, SupportAgent] = OrderedDict()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    session_id: str


def _agent_for(session_id: str | None) -> tuple[str, SupportAgent]:
    if session_id and session_id in _sessions:
        _sessions.move_to_end(session_id)
        return session_id, _sessions[session_id]
    session_id = uuid.uuid4().hex
    _sessions[session_id] = SupportAgent(_store)
    while len(_sessions) > MAX_SESSIONS:
        _sessions.popitem(last=False)
    return session_id, _sessions[session_id]


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (Path(__file__).parent / "index.html").read_text()


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    session_id, agent = _agent_for(req.session_id)
    try:
        reply = agent.chat(req.message)
    except anthropic.AuthenticationError:
        raise HTTPException(500, "The server's Anthropic API key was rejected.")
    except TypeError as e:
        if not is_missing_credentials(e):
            raise
        raise HTTPException(500, NO_CREDENTIALS_MSG)
    except anthropic.APIConnectionError:
        raise HTTPException(503, "Couldn't reach the Claude API. Try again.")
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"Claude API error ({e.status_code}).")
    return ChatResponse(reply=reply, session_id=session_id)


class ResetRequest(BaseModel):
    session_id: str | None = None


@app.post("/api/reset")
def reset(req: ResetRequest) -> dict:
    if req.session_id:
        _sessions.pop(req.session_id, None)
    return {"ok": True}
