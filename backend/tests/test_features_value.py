"""Tests for value arithmetic and the calibration scoring rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.analysis.features import MatchSummary, aggregate_xg, form_summary, split_home_away
from app.analysis.value import (
    assess_selection,
    break_even_probability,
    ev_sensitivity,
    expected_value,
    fair_probabilities,
    implied_probability,
    overround,
)
from app.models.calibration import (
    binary_brier,
    binary_log_loss,
    expected_calibration_error,
    multiclass_brier,
    multiclass_log_loss,
    reliability_diagram,
)


class TestValue:
    def test_implied_probability(self):
        assert implied_probability(2.0) == pytest.approx(0.5)
        with pytest.raises(ValueError):
            implied_probability(1.0)

    def test_overround_and_fair(self):
        odds = [1.8, 3.9, 4.5]
        assert overround(odds) > 1.0
        fair = fair_probabilities(odds)
        assert sum(fair) == pytest.approx(1.0)
        assert fair[0] > fair[1] > fair[2]

    def test_expected_value(self):
        assert expected_value(0.5, 2.0) == pytest.approx(0.0)
        assert expected_value(0.5, 2.2) == pytest.approx(0.1)
        assert break_even_probability(4.0) == pytest.approx(0.25)

    def test_assess_positive_ev(self):
        a = assess_selection("home", model_probability=0.55, decimal_odds=2.2)
        assert a.is_positive_ev
        assert a.expected_value == pytest.approx(0.21)
        assert a.confidence.startswith("high")

    def test_assess_negative_ev(self):
        a = assess_selection("away", model_probability=0.15, decimal_odds=4.0)
        assert not a.is_positive_ev
        assert a.edge == pytest.approx(0.15 - 0.25)

    def test_assess_uses_market_fair_probability(self):
        a = assess_selection("home", 0.5, 2.0, market_fair_probability=0.45)
        assert a.edge == pytest.approx(0.05)
        assert a.market_fair_probability == pytest.approx(0.45)

    def test_sensitivity_brackets_the_estimate(self):
        s = ev_sensitivity(0.5, 2.0, delta=0.02)
        assert s["p_minus"] < s["p"] < s["p_plus"]

    def test_engine_fair_probability_is_per_bookmaker(self):
        from app.analysis.engine import _value_analysis

        class Odd:
            def __init__(self, bookmaker, selection, decimal_odds):
                self.bookmaker = bookmaker
                self.market = "1x2"
                self.selection = selection
                self.decimal_odds = decimal_odds

        markets = {"one_x_two": {"home": 0.50, "draw": 0.25, "away": 0.25}}
        odds = [
            Odd("Bet365", "home", 2.10),
            Odd("Bet365", "draw", 3.40),
            Odd("Bet365", "away", 3.60),
            Odd("Pinnacle", "home", 2.12),
            Odd("Pinnacle", "draw", 3.45),
            Odd("Pinnacle", "away", 3.65),
        ]
        assessments = {
            (a["bookmaker"], a["selection"]): a
            for a in _value_analysis(markets, odds, None)["assessments"]
        }
        bet365 = [1 / 2.10, 1 / 3.40, 1 / 3.60]
        expected = bet365[0] / sum(bet365)
        fair = assessments[("Bet365", "home")]["market_fair_probability"]
        assert fair == pytest.approx(expected)
        # Each book's own margin cancels; stacking both would push this below 0.1.
        assert fair > 0.4
        assert fair != assessments[("Pinnacle", "home")]["market_fair_probability"]


class TestCalibration:
    def test_multiclass_brier_perfect_is_zero(self):
        probs = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        assert multiclass_brier(probs, np.array([0, 1]), 3) == pytest.approx(0.0)

    def test_multiclass_brier_known_value(self):
        probs = np.array([[0.5, 0.3, 0.2]])
        # outcome home: (1-0.5)^2 + 0.3^2 + 0.2^2 = 0.25 + 0.09 + 0.04 = 0.38
        assert multiclass_brier(probs, np.array([0]), 3) == pytest.approx(0.38)

    def test_log_loss_decreases_with_confidence(self):
        certain = np.array([[0.9, 0.05, 0.05]])
        unsure = np.array([[0.34, 0.33, 0.33]])
        assert multiclass_log_loss(certain, np.array([0])) < multiclass_log_loss(unsure, np.array([0]))

    def test_binary_scores(self):
        probs = np.array([0.8, 0.2])
        outcomes = np.array([1, 0])
        assert binary_brier(probs, outcomes) == pytest.approx(0.04)
        assert binary_log_loss(probs, outcomes) == pytest.approx(-np.log(0.8))

    def test_reliability_bins_account_for_every_point(self):
        rng = np.random.default_rng(0)
        probs = rng.uniform(0, 1, size=1000)
        outcomes = (rng.uniform(0, 1, size=1000) < probs).astype(float)
        bins = reliability_diagram(probs, outcomes, n_bins=10)
        assert sum(b.n for b in bins) == 1000
        assert len(bins) == 10

    def test_ece_zero_for_perfect_calibration(self):
        # In each bin the mean prediction equals the observed frequency.
        probs = np.array([0.05] * 100 + [0.95] * 100)
        outcomes = np.array([0.05] * 100 + [0.95] * 100)
        assert expected_calibration_error(probs, outcomes, n_bins=10) == pytest.approx(0.0, abs=1e-9)


def _summary(is_home: bool, gf: int, ga: int, day: int, ht=(1, 0)) -> MatchSummary:
    return MatchSummary(
        match_id=day,
        date=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=day),
        is_home=is_home,
        opponent_id=99,
        opponent_name="Opponent",
        goals_for=gf,
        goals_against=ga,
        ht_for=ht[0],
        ht_against=ht[1],
    )


class TestFeatures:
    def test_form_summary_counts(self):
        matches = [_summary(True, 3, 0, 1), _summary(False, 1, 1, 2), _summary(True, 0, 2, 3)]
        stats = form_summary(matches)
        assert (stats.wins, stats.draws, stats.losses) == (1, 1, 1)
        assert stats.points_per_match == pytest.approx(4 / 3)
        assert stats.clean_sheets == 1
        assert stats.failed_to_score == 1
        assert stats.btts_frequency == pytest.approx(1 / 3)

    def test_empty_form_has_none_averages(self):
        stats = form_summary([])
        assert stats.n_matches == 0
        assert stats.avg_goals_for is None
        assert stats.notes == ["no recent matches"]

    def test_over_under_frequency(self):
        matches = [_summary(True, 3, 1, 1), _summary(True, 1, 0, 2)]  # 4 goals, 1 goal
        stats = form_summary(matches)
        assert stats.over_under["2.5"] == pytest.approx(0.5)

    def test_split_home_away(self):
        matches = [_summary(True, 2, 0, 1), _summary(False, 0, 1, 2), _summary(False, 1, 0, 3)]
        splits = split_home_away(matches)
        assert splits["home"].n_matches == 1
        assert splits["away"].n_matches == 2
        assert splits["overall"].n_matches == 3

    def test_aggregate_xg_reports_missing_data(self):
        matches = [_summary(True, 1, 0, 1)]
        agg = aggregate_xg(matches)
        assert agg["n_with_xg"] == 0
        assert agg["xg_for_avg"] is None
