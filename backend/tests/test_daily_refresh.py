"""The once-a-day background refresh and the batch key that follows it."""

from __future__ import annotations

import threading
import time

from sqlalchemy import func, select

from app import cli
from app.chat import engine
from app.config import get_settings
from app.db.models import IngestRun
from app.ingest import daily


def test_cli_and_the_loop_share_one_league_list():
    assert cli.REFRESH_LEAGUES is daily.REFRESH_LEAGUES


def test_the_daily_refresh_covers_the_champions_league():
    """The cup a reader asks about midweek, which no other provider of ours has."""
    assert "UEFA Champions League|World" in daily.REFRESH_LEAGUES
    assert len(daily.REFRESH_LEAGUES) == len(set(daily.REFRESH_LEAGUES))


def test_already_imported_only_counts_a_successful_run_covering_that_date(db):
    db.add(
        IngestRun(
            provider="api_football",
            scope="date=2099-01-01, leagues=Premier League|England",
            status="error",
        )
    )
    db.add(
        IngestRun(
            provider="api_football",
            scope="date=2099-01-01, leagues=Premier League|England",
            status="success",
        )
    )
    db.commit()

    assert daily.already_imported(db, "2099-01-01") is True
    assert daily.already_imported(db, "2099-01-02") is False
    assert daily.already_imported(db, "2098-12-31") is False


def test_a_widened_league_set_pulls_the_date_again(db):
    """A date imported before a competition joined the list is not "already done"."""
    db.add(
        IngestRun(
            provider="api_football",
            scope="date=2099-03-03, leagues=Premier League|England",
            status="success",
        )
    )
    db.add(
        IngestRun(
            provider="api_football",
            scope="date=2099-03-04, leagues=Premier League|England,UEFA Champions League|World",
            status="success",
        )
    )
    db.commit()

    only_big_five = ["Premier League|England"]
    with_cup = [*only_big_five, "UEFA Champions League|World"]
    # 3 March was covered for the league, but never for the cup: import it again.
    assert daily.already_imported(db, "2099-03-03", only_big_five) is True
    assert daily.already_imported(db, "2099-03-03", with_cup) is False
    # 4 March was imported with the cup in scope.
    assert daily.already_imported(db, "2099-03-04", with_cup) is True


def test_refresh_without_a_key_attempts_nothing(db):
    """No key means no request, and no ingest run left behind in the database."""
    assert get_settings().api_football_key is None

    def _runs() -> int:
        return db.execute(select(func.count(IngestRun.id))).scalar() or 0

    before = _runs()
    assert daily.refresh_today() is None
    assert _runs() == before


def test_start_without_a_key_still_warms_the_stored_batch(monkeypatch):
    """The import needs a key; the stored prediction batch does not.

    The loop is what builds the batch the Predictions page reads, so it has to
    run even when no provider key is configured (it then imports nothing).
    """
    assert get_settings().api_football_key is None

    imported: list[int] = []
    warmed: list[float] = []
    gate = threading.Event()
    monkeypatch.setattr(daily, "refresh_today", lambda: imported.append(1))
    monkeypatch.setattr(
        daily.batches, "refresh_if_stale", lambda **kwargs: warmed.append(kwargs["max_age_hours"])
    )

    def park(_seconds: float) -> bool:
        # Park the daemon loop for the rest of the session instead of letting it
        # sleep for real and rebuild a batch behind the other tests.
        return gate.wait(60)

    monkeypatch.setattr(daily.time, "sleep", park)
    daily.start()

    deadline = time.monotonic() + 5
    while not warmed and time.monotonic() < deadline:
        time.sleep(0.01)

    assert imported == [1]
    assert warmed == [get_settings().batch_max_age_hours]


def test_batch_key_changes_when_new_data_is_imported(db):
    """A finished batch must refit after an import, or the chat keeps old prices."""
    before = engine._batch_key()

    db.add(IngestRun(provider="api_football", scope="date=2099-01-01", status="success"))
    db.commit()

    assert engine._batch_key() != before
