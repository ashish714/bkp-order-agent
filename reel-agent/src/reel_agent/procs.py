"""Track the ffmpeg processes we start, so quitting the app stops them too.

Without this, Ctrl+C on the web app could leave an ffmpeg render running in
the background for hours, slowing the whole computer.
"""
from __future__ import annotations

import atexit
import subprocess
import threading

_live: set[subprocess.Popen] = set()
_lock = threading.Lock()


def run(cmd: list[str], text: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            text=text)
    with _lock:
        _live.add(proc)
    try:
        out, err = proc.communicate()
    finally:
        with _lock:
            _live.discard(proc)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def track(proc: subprocess.Popen) -> subprocess.Popen:
    with _lock:
        _live.add(proc)
    return proc


def untrack(proc: subprocess.Popen) -> None:
    with _lock:
        _live.discard(proc)


def stop_all() -> int:
    """Kill every ffmpeg we started that is still running. Returns how many."""
    with _lock:
        procs = list(_live)
        _live.clear()
    n = 0
    for p in procs:
        if p.poll() is None:
            p.kill()
            n += 1
    return n


atexit.register(stop_all)
