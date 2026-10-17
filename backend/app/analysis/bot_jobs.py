"""Background runner for the prediction bot.

Fitting the model over a whole window of fixtures can take longer than an HTTP
request is allowed to, so the API starts the run here and the page polls until
it finishes.  One run runs at a time; the last finished result is kept so
repeat views return instantly.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.analysis import bot
from app.db.base import SessionLocal


@dataclass
class _Job:
    key: str | None = None
    status: str = "idle"  # idle | running | ready | error
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result: dict[str, Any] | None = None


_lock = threading.Lock()
_job = _Job()


def _run(kwargs: dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        result = bot.predict_upcoming(db, **kwargs)
        with _lock:
            _job.result = result
            _job.status = "ready"
            _job.error = None
            _job.finished_at = datetime.now(tz=UTC)
    except Exception as exc:  # noqa: BLE001 - reported to the caller as text
        with _lock:
            _job.status = "error"
            _job.error = str(exc)
            _job.finished_at = datetime.now(tz=UTC)
    finally:
        db.close()


def _snapshot() -> dict[str, Any]:
    return {
        "status": _job.status,
        "key": _job.key,
        "started_at": _job.started_at.isoformat() if _job.started_at else None,
        "finished_at": _job.finished_at.isoformat() if _job.finished_at else None,
        "error": _job.error,
        "result": _job.result,
    }


def reset() -> None:
    """Forget the current and last run (used by tests to isolate runs)."""
    global _job
    with _lock:
        _job = _Job()


def ensure(key: str, kwargs: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """Return the current job snapshot, starting a run when one is needed.

    A run starts when nothing is running and either the requested ``key``
    differs from the finished result or ``force`` is set.
    """
    global _job
    with _lock:
        if _job.status == "running":
            return _snapshot()
        if _job.status == "ready" and _job.key == key and not force:
            return _snapshot()
        _job = _Job(key=key, status="running", started_at=datetime.now(tz=UTC))
        snapshot = _snapshot()
    threading.Thread(target=_run, args=(kwargs,), daemon=True).start()
    return snapshot
