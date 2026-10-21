"""Tests for the stored prediction batch the Predictions page reads."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.analysis import batches
from app.ingest.service import import_manual_match

LEAGUE_A = ("Stored Batch A", "SYNTHETIC (generated - not real data)")
LEAGUE_B = ("Stored Batch B", "SYNTHETIC (generated - not real data)")


def _fixture(db, league, home, away, *, days_ahead=2, status="scheduled") -> int:
    kickoff = (datetime.now(tz=UTC) + timedelta(days=days_ahead)).replace(microsecond=0)
    return import_manual_match(
        db,
        {
            "league": league[0],
            "country": league[1],
            "kickoff_utc": kickoff.isoformat(),
            "home": home,
            "away": away,
            "status": status,
        },
    )


def _payload(*, generated_at=None, predictions=None, skipped=None, window_days=14):
    stamp = (generated_at or datetime.now(tz=UTC)).isoformat()
    return {
        "generated_at": stamp,
        "window_days": window_days,
        "include_live": True,
        "league_id": None,
        "considered": len(predictions or []) + len(skipped or []),
        "predicted": len(predictions or []),
        "skipped": skipped or [],
        "predictions": predictions or [],
    }


def _prediction(match_id, *, league_id, kickoff, status="scheduled", home="A", away="B"):
    one_x_two = {"home": 0.5, "draw": 0.3, "away": 0.2}
    return {
        "match_id": match_id,
        "kickoff_utc": kickoff.isoformat(),
        "status": status,
        "league": f"League {league_id}",
        "league_id": league_id,
        "home_team": home,
        "away_team": away,
        "probabilities": {"one_x_two": one_x_two},
        "pick": {"market": "1x2", "selection": "home", "probability": 0.5},
        "predicted_score": {"score": "1-0", "probability": 0.12},
        "expected_goals": {"home": 1.4, "away": 0.9},
        "best_value": None,
        "confidence": "low",
        "confidence_score": 30,
        "n_training_matches": 120,
    }


def test_store_keeps_only_the_newest_batch(db):
    first = _payload(predictions=[_prediction(1, league_id=1, kickoff=datetime.now(tz=UTC) + timedelta(days=1))])
    second = _payload(predictions=[_prediction(2, league_id=2, kickoff=datetime.now(tz=UTC) + timedelta(days=1))])

    batches.store(db, first)
    db.commit()
    batches.store(db, second)
    db.commit()

    stored = batches.latest(db)
    assert stored is not None
    assert [p["match_id"] for p in stored["predictions"]] == [2]


def test_latest_is_none_before_anything_is_stored(db):
    assert batches.latest(db) is None
    assert batches.is_stale(db, max_age_hours=6.0) is True


def test_batch_goes_stale_after_the_limit(db):
    old = datetime.now(tz=UTC) - timedelta(hours=7)
    batches.store(db, _payload(generated_at=old))
    db.commit()

    assert batches.is_stale(db, max_age_hours=6.0) is True
    assert batches.is_stale(db, max_age_hours=12.0) is False


def test_filter_narrows_by_window_league_and_limit(db):
    soon = datetime.now(tz=UTC) + timedelta(days=1)
    later = datetime.now(tz=UTC) + timedelta(days=10)
    payload = _payload(
        predictions=[
            _prediction(10, league_id=1, kickoff=soon),
            _prediction(11, league_id=2, kickoff=soon),
            _prediction(12, league_id=1, kickoff=later),
        ]
    )

    narrow = batches.filter_payload(payload, days=2)
    assert [p["match_id"] for p in narrow["predictions"]] == [10, 11]
    assert narrow["window_days"] == 2
    assert narrow["considered"] == 2

    one_league = batches.filter_payload(payload, days=14, league_id=1)
    assert [p["match_id"] for p in one_league["predictions"]] == [10, 12]

    capped = batches.filter_payload(payload, days=14, limit=1)
    assert [p["match_id"] for p in capped["predictions"]] == [10]
    assert capped["predicted"] == 1


def test_filter_drops_in_play_fixtures_when_asked(db):
    kickoff = datetime.now(tz=UTC) - timedelta(minutes=30)
    payload = _payload(
        predictions=[
            _prediction(20, league_id=1, kickoff=kickoff, status="live"),
            _prediction(21, league_id=1, kickoff=datetime.now(tz=UTC) + timedelta(days=1)),
        ]
    )

    without_live = batches.filter_payload(payload, days=7, include_live=False)
    assert [p["match_id"] for p in without_live["predictions"]] == [21]

    with_live = batches.filter_payload(payload, days=7, include_live=True)
    assert [p["match_id"] for p in with_live["predictions"]] == [20, 21]


def test_filter_keeps_skipped_fixtures_and_their_reason(db):
    kickoff = datetime.now(tz=UTC) + timedelta(days=1)
    payload = _payload(
        skipped=[
            {
                "match_id": 30,
                "kickoff_utc": kickoff.isoformat(),
                "status": "scheduled",
                "league_id": 1,
                "home_team": "Nobody United",
                "away_team": "Fresh Rovers",
                "reason": "not enough stored history",
            }
        ]
    )

    view = batches.filter_payload(payload, days=7)
    assert view["predicted"] == 0
    assert view["considered"] == 1
    assert view["skipped"][0]["reason"] == "not enough stored history"


def test_stored_batch_is_served_without_running_the_bot(client, db):
    match_id = _fixture(db, LEAGUE_A, "Batch City", "Batch Rovers")
    db.commit()
    kickoff = (datetime.now(tz=UTC) + timedelta(days=2)).replace(microsecond=0)
    batches.store(db, _payload(predictions=[_prediction(match_id, league_id=99, kickoff=kickoff)]))
    db.commit()

    body = client.get(
        "/api/predictions/upcoming", params={"days": 3, "include_live": "false"}
    ).json()

    assert body["status"] == "ready"
    assert body["source"] == "stored"
    assert body["predicted"] == 1
    assert body["predictions"][0]["match_id"] == match_id
    assert body["generated_at"]


def test_stored_batch_is_not_used_for_a_wider_window(client, db):
    kickoff = (datetime.now(tz=UTC) + timedelta(days=2)).replace(microsecond=0)
    batches.store(db, _payload(predictions=[_prediction(1, league_id=1, kickoff=kickoff)]))
    db.commit()

    body = client.get(
        "/api/predictions/upcoming",
        params={"days": 30, "limit": 5, "include_live": "false"},
    ).json()

    assert body["source"] == "live"
    assert body["status"] in {"running", "ready"}


def test_refresh_ignores_the_stored_batch(client, db):
    _fixture(db, LEAGUE_A, "Refresh City", "Refresh Rovers")
    db.commit()
    batches.store(db, _payload(predictions=[]))
    db.commit()

    body = client.get(
        "/api/predictions/upcoming",
        params={"days": 3, "include_live": "false", "refresh": "true"},
    ).json()

    assert body["source"] == "live"


def test_refresh_if_stale_skips_a_young_batch(db, monkeypatch):
    batches.store(db, _payload())
    db.commit()

    def boom(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("the batch was rebuilt although it was still fresh")

    monkeypatch.setattr(batches, "build_and_store", boom)
    assert batches.refresh_if_stale(max_age_hours=6.0) is None


def test_refresh_if_stale_rebuilds_an_old_batch(db, monkeypatch):
    old = datetime.now(tz=UTC) - timedelta(hours=7)
    batches.store(db, _payload(generated_at=old))
    db.commit()

    sentinel = _payload(predictions=[_prediction(42, league_id=1, kickoff=datetime.now(tz=UTC) + timedelta(days=1))])
    calls: list[int] = []

    def fake_build(session):
        calls.append(1)
        batches.store(session, sentinel)
        session.commit()
        return sentinel

    monkeypatch.setattr(batches, "build_and_store", fake_build)
    rebuilt = batches.refresh_if_stale(max_age_hours=6.0)

    assert calls == [1]
    assert rebuilt is not None
    assert [p["match_id"] for p in batches.latest(db)["predictions"]] == [42]
