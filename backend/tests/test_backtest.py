"""Tests for walk-forward backtesting (leakage-free evaluation)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from app.models import backtest as bt
from app.models.dixon_coles import MatchRecord


def _records(n_teams: int = 6, rounds: int = 60) -> list[MatchRecord]:
    rng = np.random.default_rng(11)
    strength = {f"T{i}": 0.7 + 0.15 * i for i in range(n_teams)}
    names = list(strength)
    base = datetime(2023, 1, 1, tzinfo=UTC)
    records: list[MatchRecord] = []
    day = 0
    for _ in range(rounds):
        for i in range(n_teams):
            for j in range(n_teams):
                if i == j:
                    continue
                home, away = names[i], names[j]
                lam_h = 1.3 * strength[home] / strength[away]
                lam_a = 1.0 * strength[away] / strength[home]
                records.append(
                    MatchRecord(
                        home=home,
                        away=away,
                        home_goals=int(rng.poisson(lam_h)),
                        away_goals=int(rng.poisson(lam_a)),
                        date=base + timedelta(days=day),
                    )
                )
                day += 1
    rng.shuffle(records)
    return records


class TestWalkForward:
    def test_runs_and_beats_baseline(self):
        records = _records()
        report = bt.walk_forward(records, bt.BacktestConfig(min_train=100, retrain_every=20))
        assert report.n_eval > 100
        markets = {m.market: m for m in report.markets}
        assert {"1x2", "over_under", "btts"} <= set(markets)
        # A model that knows team strengths should beat the base-rate baseline.
        assert markets["1x2"].brier < markets["1x2"].baseline_brier
        assert markets["1x2"].improvement_brier > 0

    def test_no_leakage_uses_only_past(self):
        records = _records()
        report = bt.walk_forward(records, bt.BacktestConfig(min_train=50, retrain_every=10))
        assert report.n_train == 50  # first evaluation trains on exactly min_train matches

    def test_reliability_covers_all_predictions(self):
        records = _records()
        report = bt.walk_forward(records, bt.BacktestConfig(min_train=100, retrain_every=20))
        one_x_two = next(m for m in report.markets if m.market == "1x2")
        assert sum(b.n for b in one_x_two.reliability) == one_x_two.n * 3

    def test_too_few_matches_warns(self):
        records = _records(n_teams=2, rounds=5)
        report = bt.walk_forward(records, bt.BacktestConfig(min_train=100))
        assert report.n_eval == 0
        assert report.warnings

    def test_config_roundtrip(self):
        cfg = bt.BacktestConfig(min_train=42, retrain_every=7)
        assert cfg.to_dict()["min_train"] == 42
        assert cfg.to_dict()["retrain_every"] == 7

    def test_use_form_round_trips(self):
        cfg = bt.BacktestConfig(use_form=True, form_window=8)
        assert cfg.to_dict()["use_form"] is True
        assert cfg.to_dict()["form_window"] == 8

    def test_walk_forward_runs_with_form(self):
        records = _records()
        report = bt.walk_forward(
            records,
            bt.BacktestConfig(min_train=100, retrain_every=20, use_form=True),
        )
        assert report.n_eval > 100
        assert report.config["use_form"] is True
        markets = {m.market: m for m in report.markets}
        assert markets["1x2"].brier < markets["1x2"].baseline_brier
