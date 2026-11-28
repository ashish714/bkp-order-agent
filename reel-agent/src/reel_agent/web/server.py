"""Web chat for the Reel agent.

Run:  reel-agent-web            (then open http://localhost:8000)
  or: uvicorn reel_agent.web.server:app --port 8000

Each browser tab gets its own session folder under REEL_AGENT_DATA
(default ~/.reel-agent/sessions). Rendering happens on this machine.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
import threading
import uuid
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from .. import procs
from ..chat import ReelChat

HERE = Path(__file__).parent
DATA = Path(os.environ.get("REEL_AGENT_DATA", Path.home() / ".reel-agent" / "sessions"))
MAX_UPLOAD_MB = int(os.environ.get("REEL_AGENT_MAX_UPLOAD_MB", "500"))

@asynccontextmanager
async def _lifespan(app):
    yield
    n = procs.stop_all()          # app is quitting: don't leave renders running
    if n:
        print(f"[reel] stopped {n} running render step(s)", flush=True)


app = FastAPI(title="Reel Agent", lifespan=_lifespan)
_sessions: dict[str, ReelChat] = {}
_locks: dict[str, threading.Lock] = {}


def _get(sid: str) -> ReelChat:
    chat = _sessions.get(sid)
    if chat is None:
        raise HTTPException(404, "Session not found. Reload the page to start a new one.")
    return chat


class ChatIn(BaseModel):
    message: str


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (HERE / "index.html").read_text(encoding="utf-8")


@app.post("/api/session")
def new_session() -> dict:
    sid = uuid.uuid4().hex[:12]
    _sessions[sid] = ReelChat(DATA / sid)
    _locks[sid] = threading.Lock()
    return {"session": sid, "mode": _sessions[sid].mode}


@app.post("/api/session/{sid}/upload")
async def upload(sid: str, files: list[UploadFile] = File(...)) -> dict:
    chat = _get(sid)
    added = []
    for f in files:
        data = await f.read()
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"{f.filename} is larger than {MAX_UPLOAD_MB} MB.")
        kind = chat.add_file(f.filename or "file", data)
        added.append({"name": f.filename, "kind": kind})
    return {"added": added, "files": chat.files(),
            "music": chat.music.name if chat.music else None,
            "logo": chat.logo.name if chat.logo else None}


@app.post("/api/session/{sid}/chat")
def chat(sid: str, body: ChatIn) -> dict:
    session = _get(sid)
    lock = _locks[sid]
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "Still working on the last request. One moment.")
    try:
        turn = session.send(body.message)
    finally:
        lock.release()
    out = asdict(turn)
    if turn.video:
        out["video_url"] = f"/api/session/{sid}/video/{turn.video}"
    return out


@app.get("/api/session/{sid}/video/{name}")
def video(sid: str, name: str) -> FileResponse:
    session = _get(sid)
    path = (session.out_dir / name).resolve()
    if path.parent != session.out_dir.resolve() or not path.exists():
        raise HTTPException(404, "Video not found.")
    return FileResponse(path, media_type="video/mp4", filename=name)


@app.get("/api/session/{sid}/thumb/{name}")
def thumb(sid: str, name: str) -> FileResponse:
    session = _get(sid)
    path = (session.media_dir / name).resolve()
    if path.parent != session.media_dir.resolve() or not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


def main() -> None:
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    print(f"Reel Agent: open http://localhost:{port}")
    # Ctrl+C: give requests 2 s, then stop (and stop any render still going)
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=port, timeout_graceful_shutdown=2)
