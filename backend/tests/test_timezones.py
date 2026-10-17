"""Venue timezone helpers: reading a provider's local clock as true UTC."""

from __future__ import annotations

from datetime import UTC, datetime

from app.timezones import (
    competition_tz,
    iso_utc,
    local_to_utc,
    tz_for_country,
)


def test_england_summer_is_bst():
    # 20:00 local on a summer evening in England is 19:00 UTC (BST).
    assert local_to_utc(datetime(2025, 8, 15, 20, 0), "England") == datetime(
        2025, 8, 15, 19, 0, tzinfo=UTC
    )


def test_england_winter_is_gmt():
    assert local_to_utc(datetime(2025, 1, 15, 20, 0), "England") == datetime(
        2025, 1, 15, 20, 0, tzinfo=UTC
    )


def test_spain_summer_is_cest():
    # La Liga's 21:00 local kick-off in August is 19:00 UTC.
    assert local_to_utc(datetime(2025, 8, 15, 21, 0), "Spain") == datetime(
        2025, 8, 15, 19, 0, tzinfo=UTC
    )


def test_cross_border_competitions_use_central_european_time():
    # openfootball lists UEFA kick-offs in CET/CEST, not the venue's local time:
    # its 21:00 for a game at Anfield is 21:00 CEST = 19:00 UTC (the usual 20:00
    # local start), and a winter 21:00 is 20:00 UTC.
    assert local_to_utc(datetime(2024, 9, 18, 21, 0), "Europe") == datetime(
        2024, 9, 18, 19, 0, tzinfo=UTC
    )
    assert local_to_utc(datetime(2025, 1, 22, 21, 0), "Europe") == datetime(
        2025, 1, 22, 20, 0, tzinfo=UTC
    )


def test_unknown_competition_is_left_as_utc():
    assert local_to_utc(datetime(2025, 8, 15, 20, 0), None) == datetime(
        2025, 8, 15, 20, 0, tzinfo=UTC
    )
    assert local_to_utc(datetime(2025, 8, 15, 20, 0), "Testland") == datetime(
        2025, 8, 15, 20, 0, tzinfo=UTC
    )


def test_competition_tz_and_country():
    assert competition_tz("Spain") is not None
    assert competition_tz("Europe") == tz_for_country("Germany")  # CET/CEST
    assert competition_tz("Atlantis") is None
    assert competition_tz(None) is None
    assert tz_for_country("Spain") is not None
    assert tz_for_country("Atlantis") is None


def test_iso_utc_always_marks_utc():
    # A naive value (what SQLite hands back) is taken to be UTC and given a Z.
    assert iso_utc(datetime(2025, 8, 15, 19, 0)) == "2025-08-15T19:00:00Z"
    assert iso_utc(datetime(2025, 8, 15, 21, 0, tzinfo=UTC)) == "2025-08-15T21:00:00Z"
    assert iso_utc(None) is None
