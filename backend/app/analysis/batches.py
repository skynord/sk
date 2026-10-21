"""Stored prediction batches, so the Predictions page needs no on-demand fit.

Fitting one model per competition is CPU-bound and, on the small server this app
runs on, outlasts any visitor's patience (minutes, not seconds).  So the batch is
built in the background -- the daily loop warms it once per
``batch_max_age_hours`` -- and kept in the database.  The API serves the stored
payload together with the time it was computed, and only runs a live fit when
nothing is stored (or when the caller asks for `refresh`).

Everything here reports what was actually computed: the stored payload is the
bot's own output, narrowed to one request, never recomputed or invented.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.analysis import bot
from app.db.base import SessionLocal
from app.db.models import PredictionBatch
from app.logging_config import get_logger

logger = get_logger(__name__)

#: The window the background batch covers.  Wide enough for the page's default
#: (14 days), and one stored run answers the chat's default batch too.
BATCH_WINDOW_DAYS = 14
BATCH_LIMIT = 200
BATCH_INCLUDE_LIVE = True

#: One stored batch covers every competition; a request narrows it by league.
SCOPE_KEY = f"all|{int(BATCH_INCLUDE_LIVE)}|{BATCH_WINDOW_DAYS}"

_build_lock = threading.Lock()


def _stamp(value: Any) -> datetime | None:
    """Parse an ISO timestamp from a payload, assuming UTC when it carries none."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def store(db: Session, payload: dict[str, Any]) -> None:
    """Keep ``payload`` as the current batch, dropping older ones.

    The caller commits.
    """
    stamp = _stamp(payload.get("generated_at")) or datetime.now(tz=UTC)
    db.execute(delete(PredictionBatch).where(PredictionBatch.scope_key == SCOPE_KEY))
    db.add(
        PredictionBatch(
            scope_key=SCOPE_KEY,
            window_days=BATCH_WINDOW_DAYS,
            include_live=BATCH_INCLUDE_LIVE,
            generated_at=stamp,
            payload=payload,
        )
    )
    db.flush()


def latest(db: Session) -> dict[str, Any] | None:
    """The stored batch, or ``None`` when the background loop has not built one."""
    row = db.execute(
        select(PredictionBatch)
        .where(PredictionBatch.scope_key == SCOPE_KEY)
        .order_by(PredictionBatch.generated_at.desc())
        .limit(1)
    ).scalars().first()
    return dict(row.payload) if row is not None and row.payload else None


def age_hours(payload: dict[str, Any]) -> float | None:
    stamp = _stamp(payload.get("generated_at"))
    if stamp is None:
        return None
    return (datetime.now(tz=UTC) - stamp).total_seconds() / 3600


def is_stale(db: Session, *, max_age_hours: float) -> bool:
    """True when there is no usable stored batch or it is older than the limit."""
    payload = latest(db)
    if payload is None:
        return True
    age = age_hours(payload)
    return age is None or age >= max_age_hours


def filter_payload(
    payload: dict[str, Any],
    *,
    league_id: int | None = None,
    days: int = BATCH_WINDOW_DAYS,
    limit: int = 50,
    include_live: bool = True,
) -> dict[str, Any]:
    """Narrow a stored batch to one request, without recomputing anything.

    The window and the league are filters over the fixtures the stored run
    covered, so counts describe the request, and ``generated_at`` still says when
    the model actually ran.
    """
    horizon = datetime.now(tz=UTC) + timedelta(days=max(1, days))

    def keep(item: dict[str, Any]) -> bool:
        kickoff = _stamp(item.get("kickoff_utc"))
        if kickoff is None or kickoff > horizon:
            return False
        if not include_live and item.get("status") == "live":
            return False
        return league_id is None or item.get("league_id") == league_id

    predictions = [p for p in payload.get("predictions") or [] if keep(p)]
    skipped = [s for s in payload.get("skipped") or [] if keep(s)]
    ordered = sorted(
        predictions + skipped, key=lambda item: _stamp(item["kickoff_utc"]) or horizon
    )[: max(1, limit)]
    kept_predictions = [item for item in ordered if "probabilities" in item]
    kept_skipped = [item for item in ordered if "probabilities" not in item]

    return {
        "status": "ready",
        "source": "stored",
        "generated_at": payload.get("generated_at"),
        "stored_window_days": payload.get("window_days"),
        "window_days": days,
        "include_live": include_live,
        "league_id": league_id,
        "considered": len(ordered),
        "predicted": len(kept_predictions),
        "skipped": kept_skipped,
        "predictions": kept_predictions,
    }


def build_and_store(db: Session) -> dict[str, Any]:
    """Run the batch (and keep the Prediction rows) then store its payload."""
    payload = bot.predict_upcoming(
        db,
        league_id=None,
        days_ahead=BATCH_WINDOW_DAYS,
        limit=BATCH_LIMIT,
        include_live=BATCH_INCLUDE_LIVE,
        persist=True,
    )
    store(db, payload)
    db.commit()
    return payload


def refresh_if_stale(*, max_age_hours: float) -> dict[str, Any] | None:
    """Rebuild the stored batch when it is missing or older than ``max_age_hours``.

    Called from the background loop, never from a request: one run at a time, and
    a batch that is still young is left alone.
    """
    with _build_lock:
        with SessionLocal() as db:
            if not is_stale(db, max_age_hours=max_age_hours):
                return None
            payload = build_and_store(db)
    logger.info(
        "prediction batch stored",
        extra={
            "predictions": payload.get("predicted"),
            "considered": payload.get("considered"),
            "window_days": payload.get("window_days"),
        },
    )
    return payload
