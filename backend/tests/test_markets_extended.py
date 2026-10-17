"""Tests for the extended market set: O/U half-line, fair odds, half-time
markets and the ranked statistical-opportunities list."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.analysis import engine
from app.analysis.report import derive_opportunities
from app.analysis.value import decimal_odds_from_probability
from app.db.models import League
from app.models.markets import TOTAL_LINES, totals
from app.models.poisson import score_matrix


def test_totals_include_half_line_and_are_monotone():
    t = totals(score_matrix(1.5, 1.0, kmax=15))
    assert "0.5" in t
    assert 0.5 in TOTAL_LINES
    # More goals are more likely over a lower line.
    assert t["0.5"]["over"] > t["1.5"]["over"] > t["2.5"]["over"]
    assert t["0.5"]["over"] + t["0.5"]["under"] == pytest.approx(1.0, abs=1e-9)


def test_fair_odds_from_probability():
    assert decimal_odds_from_probability(0.5) == pytest.approx(2.0)
    assert decimal_odds_from_probability(0.25) == pytest.approx(4.0)
    assert decimal_odds_from_probability(0.0) is None
    assert decimal_odds_from_probability(None) is None


def test_derive_opportunities_ranks_and_prices():
    opps = derive_opportunities(
        home_name="Alpha",
        away_name="Beta",
        one_x_two={"home": 0.6, "draw": 0.25, "away": 0.15},
        double_chance={"home_or_draw": 0.85, "home_or_away": 0.75, "draw_or_away": 0.4},
        totals={"2.5": {"over": 0.55, "under": 0.45}},
        btts={"yes": 0.5, "no": 0.5},
        half_time=None,
        limit=8,
    )
    assert opps
    probs = [o["probability"] for o in opps]
    assert probs == sorted(probs, reverse=True)
    top = opps[0]
    assert top["probability"] == pytest.approx(0.85)
    assert top["fair_odds"] == pytest.approx(1 / 0.85)
    assert top["label"]
    assert top["market"] in {"1x2", "double_chance", "o/u", "btts", "ht_1x2"}


def test_halftime_share_estimated_from_sample(db):
    league = db.execute(
        select(League).where(League.name == "Synthetic Sample League")
    ).scalar_one()
    share = engine._halftime_goal_share(
        db, [league.id], datetime(2030, 1, 1, tzinfo=UTC), lookback_days=5000
    )
    assert share is not None
    assert share["n_matches"] >= engine.MIN_HT_MATCHES
    assert 0.2 <= share["home"] <= 0.8
    assert 0.2 <= share["away"] <= 0.8


def _analysed_match_id(client) -> int:
    return client.get(
        "/api/matches", params={"status": "finished", "limit": 1, "offset": 300}
    ).json()["matches"][0]["id"]


def test_analysis_payload_has_extra_markets(client):
    match_id = _analysed_match_id(client)
    body = client.post(
        f"/api/analysis/{match_id}", json={"monte_carlo_simulations": 2000, "persist": False}
    ).json()
    markets = body["markets"]
    # O/U 0.5 present.
    assert "0.5" in markets["totals"]
    # Fair odds mirror the probabilities as 1/p.
    assert markets["fair_odds"]["one_x_two"]["home"] == pytest.approx(
        1 / markets["one_x_two"]["home"]
    )
    assert markets["fair_odds"]["totals"]["2.5"]["over"] == pytest.approx(
        1 / markets["totals"]["2.5"]["over"]
    )
    # Half-time markets are modelled from the recorded HT scores.
    assert markets["half_time"]["available"] is True
    assert markets["half_time"]["one_x_two"]["home"] > 0
    assert sum(markets["half_time"]["one_x_two"].values()) == pytest.approx(1.0, abs=1e-6)
    # Ranked opportunities with fair prices.
    assert body["opportunities"]
    for o in body["opportunities"]:
        assert 0.0 < o["probability"] <= 1.0
        assert o["fair_odds"] == pytest.approx(1 / o["probability"])


def test_has_secondary_stats_detects_sample_corners(db):
    league = db.execute(
        select(League).where(League.name == "Synthetic Sample League")
    ).scalar_one()
    assert engine._has_secondary_stats(db, [league.id]) is True


def test_limitations_flag_missing_corner_card_data():
    from app.analysis.report import derive_limitations

    counters = {"has_xg": 1, "has_halftime": 1, "home_form": 10, "away_form": 10, "h2h": 1}
    without = derive_limitations(
        n_training_matches=100, min_matches=20, counters=counters, has_secondary_stats=False
    )
    assert any("corner" in line.lower() for line in without)
    with_stats = derive_limitations(
        n_training_matches=100, min_matches=20, counters=counters, has_secondary_stats=True
    )
    assert not any("corner" in line.lower() for line in with_stats)


def _conf(**overrides):
    from app.analysis.report import derive_confidence

    base = dict(
        n_training_matches=300,
        min_matches=20,
        monte_carlo_std_error=0.006,
        converged=True,
        counters={"home_form": 10, "away_form": 10, "h2h": 1, "has_halftime": 1},
    )
    base.update(overrides)
    return derive_confidence(**base)


def test_confidence_varies_with_match_decisiveness():
    even = _conf(one_x_two={"home": 0.35, "draw": 0.33, "away": 0.32})
    lopsided = _conf(one_x_two={"home": 0.80, "draw": 0.12, "away": 0.08})
    assert lopsided["score"] > even["score"]
    assert even["level"] in ("low", "moderate")
    assert lopsided["level"] in ("high", "very high")


def test_confidence_xg_coverage_raises_score():
    without = _conf(counters={"home_form": 10, "away_form": 10, "h2h": 1, "has_halftime": 1},
                    one_x_two={"home": 0.5, "draw": 0.27, "away": 0.23})
    with_xg = _conf(
        counters={"home_form": 10, "away_form": 10, "h2h": 1, "has_halftime": 1, "has_xg": 1},
        one_x_two={"home": 0.5, "draw": 0.27, "away": 0.23},
    )
    assert with_xg["score"] > without["score"]


def test_confidence_thin_sample_is_capped():
    result = _conf(
        n_training_matches=5,
        counters={},
        one_x_two={"home": 0.9, "draw": 0.05, "away": 0.05},
    )
    assert result["level"] in ("very low", "low")
    assert 0 <= result["score"] <= 100


def test_confidence_no_xg_holds_below_top_band():
    result = _conf(
        one_x_two={"home": 0.80, "draw": 0.12, "away": 0.08},
        counters={"home_form": 10, "away_form": 10, "h2h": 1, "has_halftime": 1},
    )
    assert result["level"] != "very high"
