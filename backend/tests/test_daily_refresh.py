"""The once-a-day background refresh and the batch key that follows it."""

from __future__ import annotations

import threading

from sqlalchemy import func, select

from app import cli
from app.chat import engine
from app.config import get_settings
from app.db.models import IngestRun
from app.ingest import daily


def test_cli_and_the_loop_share_one_big_five_list():
    assert cli.BIG_FIVE is daily.BIG_FIVE


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


def test_refresh_without_a_key_attempts_nothing(db):
    """No key means no request, and no ingest run left behind in the database."""
    assert get_settings().api_football_key is None

    def _runs() -> int:
        return db.execute(select(func.count(IngestRun.id))).scalar() or 0

    before = _runs()
    assert daily.refresh_today() is None
    assert _runs() == before


def test_start_without_a_key_starts_no_thread():
    assert get_settings().api_football_key is None
    named = lambda: [t for t in threading.enumerate() if t.name == "daily-odds-refresh"]  # noqa: E731
    assert named() == []
    daily.start()
    assert named() == []


def test_batch_key_changes_when_new_data_is_imported(db):
    """A finished batch must refit after an import, or the chat keeps old prices."""
    before = engine._batch_key()

    db.add(IngestRun(provider="api_football", scope="date=2099-01-01", status="success"))
    db.commit()

    assert engine._batch_key() != before
