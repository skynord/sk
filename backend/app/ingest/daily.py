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

from app.analysis import batches
from app.config import get_settings
from app.db.base import SessionLocal
from app.db.models import IngestRun
from app.ingest import maintenance
from app.ingest.service import ingest_provider
from app.logging_config import get_logger

logger = get_logger(__name__)

#: The competitions a daily refresh pulls, as API-Football name|country specs.
#: The Free plan only reaches a +/-1-day date window, so these are the ones that
#: matter on a server nobody logs into.  The Champions League is here because its
#: fixtures are the ones a reader asks about midweek and no other provider of ours
#: carries them; the "World" country is API-Football's own spelling and is folded
#: to the stored "Europe" one on ingest, so it lands on the league row that
#: already holds its history.
REFRESH_LEAGUES = [
    "Premier League|England",
    "La Liga|Spain",
    "Serie A|Italy",
    "Bundesliga|Germany",
    "Ligue 1|France",
    "UEFA Champions League|World",
]

_lock = threading.Lock()
_attempted: set[str] = set()


def _today() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%d")


def already_imported(db: Session, date: str, leagues: list[str] | None = None) -> bool:
    """True when a successful import of ``date`` is already stored.

    With ``leagues``, only a run whose scope covers every one of them counts, so
    widening the set (a competition added since, say) pulls that date again
    instead of being skipped because *something* was imported for it.
    """
    scopes = db.execute(
        select(IngestRun.scope).where(
            IngestRun.provider == "api_football",
            IngestRun.status == "success",
            IngestRun.scope.like(f"%date={date}%"),
        )
    ).scalars().all()
    if not scopes:
        return False
    if leagues is None:
        return True
    return any(all(spec in scope for spec in leagues) for scope in scopes)


def refresh_today() -> dict[str, Any] | None:
    """Import today's fixtures and odds, then reconcile duplicates.

    Returns the ingest result, or ``None`` when it was skipped: no key, the
    date already imported, or this process has already attempted it.
    """
    settings = get_settings()
    if not settings.api_football_key:
        return None

    date = _today()
    leagues = list(REFRESH_LEAGUES)
    with SessionLocal() as db:
        with _lock:
            if date in _attempted or already_imported(db, date, leagues):
                return None
            _attempted.add(date)
        result = ingest_provider(
            db,
            "api_football",
            date=date,
            leagues=leagues,
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
    interval = max(60, int(settings.bot_loop_seconds))

    def _loop() -> None:
        while True:
            try:
                refresh_today()
            except Exception as exc:  # noqa: BLE001 - a daemon loop must not die
                logger.warning("daily odds refresh failed", extra={"error": str(exc)})
            # The Predictions page reads a stored batch: fitting the model is
            # CPU-bound, so it happens here rather than while somebody waits.
            try:
                batches.refresh_if_stale(max_age_hours=settings.batch_max_age_hours)
            except Exception as exc:  # noqa: BLE001 - a daemon loop must not die
                logger.warning("prediction batch refresh failed", extra={"error": str(exc)})
            time.sleep(interval)

    threading.Thread(target=_loop, name="daily-odds-refresh", daemon=True).start()
    logger.info("daily odds refresh started", extra={"interval_seconds": interval})
