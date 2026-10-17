"""Tests for the prediction bot and the live/upcoming endpoints."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from app.analysis import bot
from app.ingest.service import import_manual_match

SAMPLE_LEAGUE = "Synthetic Sample League"
SAMPLE_COUNTRY = "SYNTHETIC (generated - not real data)"


def _ensure_future_match(db) -> int:
    kickoff = (datetime.now(tz=UTC) + timedelta(days=2)).replace(microsecond=0)
    return import_manual_match(
        db,
        {
            "league": SAMPLE_LEAGUE,
            "country": SAMPLE_COUNTRY,
            "kickoff_utc": kickoff.isoformat(),
            "home": "Sample City 0",
            "away": "Sample City 1",
            "status": "scheduled",
        },
    )


def test_upcoming_matches_finds_future_fixture(db):
    match_id = _ensure_future_match(db)
    found = bot.upcoming_matches(db, days_ahead=10, include_live=False)
    assert match_id in [m.id for m in found]


def test_upcoming_matches_respects_window(db):
    _ensure_future_match(db)
    found = bot.upcoming_matches(db, days_ahead=1, include_live=False)
    assert found == [] or all(
        m.kickoff_utc <= datetime.now(tz=UTC) + timedelta(days=1) for m in found
    )


def test_predict_upcoming_produces_prediction(db):
    match_id = _ensure_future_match(db)
    result = bot.predict_upcoming(db, days_ahead=10, include_live=False, persist=False)
    assert result["window_days"] == 10
    assert result["considered"] >= 1
    predicted = {p["match_id"]: p for p in result["predictions"]}
    assert match_id in predicted
    prediction = predicted[match_id]
    one_x_two = prediction["probabilities"]["one_x_two"]
    assert abs(sum(one_x_two.values()) - 1.0) < 1e-6
    assert prediction["pick"]["selection"] in {"home", "draw", "away"}
    assert prediction["pick"]["probability"] == max(one_x_two.values())
    assert prediction["predicted_score"]["score"]
    assert prediction["confidence"] in {"very low", "low", "moderate", "high", "very high"}
    assert 0 <= prediction["confidence_score"] <= 100
    assert prediction["best_value"] is None  # no odds stored for a fresh fixture


def test_predict_upcoming_skips_without_history(db):
    kickoff = (datetime.now(tz=UTC) + timedelta(days=3)).replace(microsecond=0)
    match_id = import_manual_match(
        db,
        {
            "league": "Brand New League",
            "country": "Nowhere",
            "kickoff_utc": kickoff.isoformat(),
            "home": "Nobody United",
            "away": "Fresh Rovers",
            "status": "scheduled",
        },
    )
    result = bot.predict_upcoming(db, days_ahead=10, include_live=False, persist=False)
    skipped = {s["match_id"]: s for s in result["skipped"]}
    assert match_id in skipped
    assert skipped[match_id]["reason"]


def test_summarise_report_includes_value_when_odds_present(db):
    from app.analysis.engine import AnalysisOptions, build_analysis

    match_id = _ensure_future_match(db)
    report = build_analysis(
        db,
        match_id,
        AnalysisOptions(
            persist=False,
            manual_odds=[
                {"market": "1x2", "selection": "home", "decimal_odds": 1.6},
                {"market": "1x2", "selection": "draw", "decimal_odds": 4.0},
                {"market": "1x2", "selection": "away", "decimal_odds": 6.0},
            ],
        ),
    )
    summary = bot.summarise_report(report)
    assert summary["best_value"] is not None
    assert summary["best_value"]["market"] == "1x2"
    assert "expected_value" in summary["best_value"]


def _await_predictions(client, params, timeout=60.0):
    """Start the bot through the endpoint and poll until the run finishes."""
    deadline = time.monotonic() + timeout
    body = client.get(
        "/api/predictions/upcoming", params={**params, "refresh": "true"}
    ).json()
    while body.get("status") == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
        body = client.get("/api/predictions/upcoming", params=params).json()
    if body.get("status") == "error":
        raise AssertionError(f"prediction bot failed: {body.get('error')}")
    return body


def test_predictions_endpoint(client):
    body = _await_predictions(client, {"days": 30, "include_live": "false"})
    assert body["window_days"] == 30
    assert body["include_live"] is False
    assert "predictions" in body and "skipped" in body


def test_live_endpoint_without_key(client):
    body = client.get("/api/live").json()
    assert body["available"] is False
    assert "FA_API_FOOTBALL_KEY" in body["reason"]
    assert body["live"] == []
