"""Import today's fixtures and odds once a day, from inside the app.

Nothing else pulls fresh prices: the API is on demand, so on a server nobody
logs into, "where's the best value?" would keep answering from the last import
anybody ran by hand -- or from none at all.  This does that import in the
background, once per UTC date, and then folds the newly arrived fixtures into
the ones already stored.

Guards that keep it inside a Free API-Football key's daily allowance: a date
that is already imported successfully is left alone, a date is attempted at
most once per process, and nothing runs when no key is configured.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import SessionLocal
from app.db.models import IngestRun
from app.ingest import maintenance
from app.ingest.service import ingest_provider
from app.logging_config import get_logger

logger = get_logger(__name__)

#: The five big European leagues, as API-Football name|country specs.  The Free
#: plan only reaches a +/-1-day date window, so this is what a daily refresh pulls.
BIG_FIVE = [
    "Premier League|England",
    "La Liga|Spain",
    "Serie A|Italy",
    "Bundesliga|Germany",
    "Ligue 1|France",
]

_lock = threading.Lock()
_attempted: set[str] = set()


def _today() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%d")


def already_imported(db: Session, date: str) -> bool:
    """True when a successful import of ``date`` is already stored."""
    row = db.execute(
        select(IngestRun.id)
        .where(
            IngestRun.provider == "api_football",
            IngestRun.status == "success",
            IngestRun.scope.like(f"%date={date}%"),
        )
        .limit(1)
    ).first()
    return row is not None


def refresh_today() -> dict[str, Any] | None:
    """Import today's fixtures and odds, then reconcile duplicates.

    Returns the ingest result, or ``None`` when it was skipped: no key, the
    date already imported, or this process has already attempted it.
    """
    settings = get_settings()
    if not settings.api_football_key:
        return None

    date = _today()
    with SessionLocal() as db:
        with _lock:
            if date in _attempted or already_imported(db, date):
                return None
            _attempted.add(date)
        result = ingest_provider(
            db,
            "api_football",
            date=date,
            leagues=list(BIG_FIVE),
            include_details=True,
        )

    if result["status"] != "success":
        logger.warning(
            "daily refresh did not import",
            extra={"date": date, "status": result["status"], "reason": result.get("reason")},
        )
        return result

    # A provider spells clubs differently from us ("Cagliari" vs "Cagliari
    # Calcio"), which lands today's fixtures beside rows already stored and
    # leaves the duplicates carrying no odds.  Fold them together.
    with SessionLocal() as db:
        try:
            report = maintenance.run_dedupe(db, apply=True)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - a background pass never takes the app down
            db.rollback()
            logger.warning("daily dedupe skipped", extra={"error": str(exc)})
        else:
            if report.matches_removed or report.teams_removed:
                logger.info(
                    "daily refresh merged duplicates",
                    extra={"fixtures": report.matches_removed, "clubs": report.teams_removed},
                )

    logger.info(
        "daily refresh imported",
        extra={"date": date, "matches": result["matches_upserted"]},
    )
    return result


def start() -> None:
    """Start the background loop, logging why it is off when it is off."""
    settings = get_settings()
    if not settings.api_football_key:
        logger.info("daily odds refresh is off: no API-Football key configured")
        return
    interval = max(60, int(settings.bot_loop_seconds))

    def _loop() -> None:
        while True:
            try:
                refresh_today()
            except Exception as exc:  # noqa: BLE001 - a daemon loop must not die
                logger.warning("daily odds refresh failed", extra={"error": str(exc)})
            time.sleep(interval)

    threading.Thread(target=_loop, name="daily-odds-refresh", daemon=True).start()
    logger.info("daily odds refresh started", extra={"interval_seconds": interval})
