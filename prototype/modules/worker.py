"""The background worker.

A serverless host has no long-running process, so on Vercel this module is
deliberately inert: `start()` refuses and `running()` returns False. Locally it
runs a daemon thread that polls replies on an interval. Either way `tick()`
does exactly one pass and returns.
Never started automatically at import.
"""

from __future__ import annotations

import threading
import time

from . import notify, replies
from .settings import is_serverless

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()

DEFAULT_INTERVAL = 120.0


class WorkerUnavailable(RuntimeError):
    """Raised when a worker is asked for on a host that cannot run one."""


def tick() -> dict:
    """One pass: poll replies, report what came back. Safe to call any time."""
    started = time.monotonic()
    result = replies.poll()
    result["took_s"] = round(time.monotonic() - started, 1)
    if result.get("ok") and result.get("processed"):
        classes = [p.get("classification") for p in result["processed"] if isinstance(p, dict)]
        notify.send(
            f"MarketPilot: {len(result['processed'])} reply(ies) processed "
            f"({', '.join(str(c) for c in classes)})",
            silent=True,
        )
    return result


def _loop(interval: float) -> None:
    while not _stop.wait(interval):
        try:
            tick()
        except Exception:
            continue


def start(interval: float = DEFAULT_INTERVAL) -> dict:
    """Start the poller. Refuses on a serverless host, and never runs twice."""
    global _thread
    if is_serverless():
        raise WorkerUnavailable(
            "no background worker on a serverless host - press 'Check replies' instead"
        )
    with _lock:
        if _thread is not None and _thread.is_alive():
            return {"started": False, "reason": "already running", "running": True}
        _stop.clear()
        _thread = threading.Thread(target=_loop, args=(interval,), daemon=True)
        _thread.start()
    return {"started": True, "interval_s": interval, "running": True}


def stop() -> dict:
    global _thread
    with _lock:
        _stop.set()
        was_running = _thread is not None and _thread.is_alive()
        _thread = None
    return {"stopped": was_running, "running": False}


def running() -> bool:
    return _thread is not None and _thread.is_alive()


def status() -> dict:
    return {
        "running": running(),
        "serverless": is_serverless(),
        "imap_configured": replies.configured(),
        "telegram_configured": notify.configured(),
        "supported": not is_serverless(),
    }
