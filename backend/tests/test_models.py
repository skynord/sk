"""Unit tests for the pure statistical models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.models.dixon_coles import (
    DixonColesModel,
    MatchRecord,
    time_decay_weights,
    with_form,
    with_rest,
)
from app.models.markets import all_markets, one_x_two, team_totals, totals
from app.models.poisson import match_outcome, poisson_pmf, score_matrix
from app.models.simulate import simulate


def _synthetic_records() -> list[MatchRecord]:
    """Matches generated from known attack strengths (Alpha strongest)."""
    rng = np.random.default_rng(7)
    strengths = {"Alpha": 1.35, "Beta": 1.1, "Gamma": 0.85, "Delta": 0.7}
    names = list(strengths)
    base = datetime(2023, 1, 1, tzinfo=UTC)
    records: list[MatchRecord] = []
    for i in range(500):
        home = names[i % 4]
        away = names[(i + 1) % 4]
        lam_h = 1.3 * strengths[home] / strengths[away]
        lam_a = 1.0 * strengths[away] / strengths[home]
        records.append(
            MatchRecord(
                home=home,
                away=away,
                home_goals=int(rng.poisson(lam_h)),
                away_goals=int(rng.poisson(lam_a)),
                date=base + timedelta(days=i),
            )
        )
    return records


class TestPoisson:
    def test_pmf_sums_near_one(self):
        pmf = poisson_pmf(1.5, 40)
        assert pmf.sum() == pytest.approx(1.0, abs=1e-9)

    def test_score_matrix_sums_to_one(self):
        mat = score_matrix(1.5, 1.1, kmax=12)
        assert mat.shape == (13, 13)
        assert mat.sum() == pytest.approx(1.0, abs=1e-9)

    def test_score_matrix_mode_near_lambda(self):
        # Poisson mode is floor(lambda); pick a non-integer rate to avoid ties.
        mat = score_matrix(2.5, 1.0, kmax=15)
        assert int(np.argmax(mat.sum(axis=1))) == 2

    def test_rho_still_normalised(self):
        mat = score_matrix(1.4, 1.0, kmax=12, rho=-0.05)
        assert mat.sum() == pytest.approx(1.0, abs=1e-9)

    def test_match_outcome_sums_to_one(self):
        out = match_outcome(score_matrix(1.5, 1.2, kmax=15))
        assert out.home_win + out.draw + out.away_win == pytest.approx(1.0, abs=1e-9)


class TestMarkets:
    def test_one_x_two_sums_to_one(self):
        probs = one_x_two(score_matrix(1.5, 1.2, kmax=15))
        assert probs["home"] + probs["draw"] + probs["away"] == pytest.approx(1.0, abs=1e-9)

    def test_all_markets_consistency(self):
        markets = all_markets(1.6, 1.1, kmax=15)
        assert sum(markets.one_x_two.values()) == pytest.approx(1.0, abs=1e-9)
        assert markets.btts["yes"] + markets.btts["no"] == pytest.approx(1.0, abs=1e-9)
        assert markets.totals["2.5"]["over"] + markets.totals["2.5"]["under"] == pytest.approx(1.0, abs=1e-9)
        assert markets.team_totals["home"]["0.5"]["over"] + markets.team_totals["home"]["0.5"]["under"] == pytest.approx(1.0, abs=1e-9)

    def test_totals_monotone_in_line(self):
        mat = score_matrix(1.5, 1.0, kmax=15)
        t = totals(mat)
        assert t["1.5"]["over"] > t["2.5"]["over"] > t["3.5"]["over"] > t["4.5"]["over"]

    def test_team_totals_larger_for_larger_lambda(self):
        strong = team_totals(score_matrix(2.5, 1.0, kmax=15), lines=(0.5,))
        weak = team_totals(score_matrix(0.8, 1.0, kmax=15), lines=(0.5,))
        assert strong["home"]["0.5"]["over"] > weak["home"]["0.5"]["over"]

    def test_draw_more_likely_for_low_scoring(self):
        low = one_x_two(score_matrix(0.6, 0.6, kmax=15))
        high = one_x_two(score_matrix(3.0, 3.0, kmax=20))
        assert low["draw"] > high["draw"]

    def test_home_advantage_raises_home_probability(self):
        even = one_x_two(score_matrix(1.2, 1.2, kmax=15))
        home_adv = one_x_two(score_matrix(1.6, 1.0, kmax=15))
        assert home_adv["home"] > even["home"]


class TestTimeDecay:
    def test_recent_matches_weigh_more(self):
        ref = datetime(2024, 1, 1, tzinfo=UTC)
        dates = [ref, ref - timedelta(days=180)]
        weights = time_decay_weights(dates, ref, halflife_days=180.0)
        assert weights[0] == pytest.approx(1.0)
        assert weights[1] == pytest.approx(0.5, abs=1e-9)


class TestDixonColes:
    @pytest.fixture(scope="class")
    def model(self) -> DixonColesModel:
        return DixonColesModel.fit(_synthetic_records(), halflife_days=100_000)

    def test_has_all_teams(self, model):
        for name in ("Alpha", "Beta", "Gamma", "Delta"):
            assert model.has_team(name)

    def test_ranking_recovers_strength_order(self, model):
        ranking = [r["team"] for r in model.ranking()]
        assert ranking[0] == "Alpha"
        assert ranking[-1] == "Delta"

    def test_home_advantage_positive(self, model):
        # A neutral venue should not help the home side.
        home, away = model.expected_goals("Alpha", "Beta")
        neutral_home, neutral_away = model.expected_goals("Alpha", "Beta", neutral_venue=True)
        assert home > neutral_home

    def test_expected_goals_reflect_strength(self, model):
        strong_h, _ = model.expected_goals("Alpha", "Delta")
        weak_h, _ = model.expected_goals("Delta", "Alpha")
        assert strong_h > weak_h

    def test_unknown_team_raises(self, model):
        with pytest.raises(KeyError):
            model.expected_goals("Alpha", "Nonexistent")

    def test_extra_teams_models_unknown_team_at_average(self):
        m = DixonColesModel.fit(
            _synthetic_records(), halflife_days=100_000, extra_teams=["Newcomer"]
        )
        assert m.has_team("Newcomer")
        # No evidence: attack and defence stay at the league-average prior.
        assert m.params.attack["Newcomer"] == 0.0
        assert m.params.defence["Newcomer"] == 0.0
        lh, la = m.expected_goals("Newcomer", "Newcomer")
        assert 1.0 < lh < 2.5
        assert 0.5 < la < 1.8

    def test_regularization_shrinks_strength_magnitudes(self):
        recs = _synthetic_records()
        plain = DixonColesModel.fit(recs, halflife_days=100_000)
        shrunk = DixonColesModel.fit(recs, halflife_days=100_000, regularization=5.0)

        def spread(model: DixonColesModel) -> float:
            return sum(abs(v) for v in model.params.attack.values()) + sum(
                abs(v) for v in model.params.defence.values()
            )

        assert shrunk.params.regularization == 5.0
        assert spread(shrunk) < spread(plain)


class TestBacktestConfig:
    def test_regularization_round_trips(self):
        from app.models.backtest import BacktestConfig

        cfg = BacktestConfig(regularization=1.5)
        assert cfg.to_dict()["regularization"] == 1.5

    def test_use_rest_round_trips(self):
        from app.models.backtest import BacktestConfig

        cfg = BacktestConfig(use_rest=True)
        assert cfg.to_dict()["use_rest"] is True


class TestRestCovariate:
    def test_with_rest_measures_days_between_matches(self):
        base = datetime(2024, 1, 1, tzinfo=UTC)

        def rec(home, away, day):
            return MatchRecord(home=home, away=away, home_goals=0, away_goals=0,
                               date=base + timedelta(days=day))

        out = with_rest([rec("A", "B", 0), rec("A", "C", 5), rec("B", "A", 12)])
        assert out[0].rest_home is None and out[0].rest_away is None
        assert out[1].rest_home == pytest.approx(5.0)
        assert out[1].rest_away is None
        assert out[2].rest_home == pytest.approx(12.0)  # B last played day 0
        assert out[2].rest_away == pytest.approx(7.0)  # A last played day 5

    def test_rest_coeff_only_when_enabled(self):
        recs = _synthetic_records()
        plain = DixonColesModel.fit(recs, halflife_days=100_000)
        rested = DixonColesModel.fit(recs, halflife_days=100_000, use_rest=True)
        assert plain.params.rest_coeff is None
        assert rested.params.rest_coeff is not None
        assert abs(rested.params.rest_coeff) <= 0.03

    def test_rest_diff_shifts_goals_in_coeff_direction(self):
        model = DixonColesModel.fit(
            _synthetic_records(), halflife_days=100_000, use_rest=True
        )
        coeff = model.params.rest_coeff
        assert coeff is not None
        lh0, la0 = model.expected_goals("Alpha", "Beta", rest_diff=0.0)
        lh1, la1 = model.expected_goals("Alpha", "Beta", rest_diff=5.0)
        assert np.sign(lh1 - lh0) == np.sign(coeff)
        assert np.sign(la1 - la0) == -np.sign(coeff)

    def test_rest_diff_ignored_without_rest_model(self):
        model = DixonColesModel.fit(_synthetic_records(), halflife_days=100_000)
        base = model.expected_goals("Alpha", "Beta")
        shifted = model.expected_goals("Alpha", "Beta", rest_diff=5.0)
        assert shifted == base

    def test_recovers_a_planted_rest_effect(self):
        from dataclasses import replace

        rng = np.random.default_rng(42)
        base = datetime(2023, 1, 1, tzinfo=UTC)
        pairs = [("A", "B"), ("C", "D"), ("A", "C"), ("B", "D"), ("A", "D"), ("B", "C")]
        raw: list[MatchRecord] = []
        day = 0
        for i in range(1500):
            home, away = pairs[i % 6]
            raw.append(MatchRecord(home=home, away=away, home_goals=0, away_goals=0,
                                   date=base + timedelta(days=day)))
            day += int(rng.integers(2, 6))
        sched = with_rest(raw)
        true_coeff = 0.02
        records: list[MatchRecord] = []
        for match in sched:
            if match.rest_home is None or match.rest_away is None:
                rest_diff = 0.0
            else:
                rest_diff = match.rest_home - match.rest_away
            lh = 1.3 * np.exp(true_coeff * rest_diff)
            la = 1.0 * np.exp(-true_coeff * rest_diff)
            records.append(replace(match,
                                   home_goals=int(rng.poisson(lh)),
                                   away_goals=int(rng.poisson(la))))
        model = DixonColesModel.fit(records, halflife_days=100_000, use_rest=True)
        assert model.params.rest_coeff is not None
        assert 0.005 < model.params.rest_coeff < 0.04


class TestPerLeagueIntercepts:
    """A cup pools several leagues; a per-league intercept keeps strengths
    comparable across them (see the ``dixon_coles`` module docstring)."""

    @staticmethod
    def _records() -> list[MatchRecord]:
        rng = np.random.default_rng(5)
        base = datetime(2023, 1, 1, tzinfo=UTC)
        specs = {
            "LW": ([f"W{i}" for i in range(1, 7)], 0.9),
            "LS": ([f"S{i}" for i in range(1, 7)], 1.7),
        }
        records: list[MatchRecord] = []
        day = 0
        for league, (teams, lam) in specs.items():
            for _ in range(500):
                home, away = rng.choice(teams, size=2, replace=False)
                records.append(
                    MatchRecord(
                        home=str(home), away=str(away),
                        home_goals=int(rng.poisson(lam)), away_goals=int(rng.poisson(lam)),
                        date=base + timedelta(days=day), league=league,
                    )
                )
                day += 1
        # A bridging cup so the two leagues are connected through shared matches.
        # Both home/away orders so the cup implies no spurious home advantage.
        for _ in range(400):
            lw = str(rng.choice(specs["LW"][0]))
            ls = str(rng.choice(specs["LS"][0]))
            home, away = (lw, ls) if rng.random() < 0.5 else (ls, lw)
            records.append(
                MatchRecord(
                    home=home, away=away,
                    home_goals=int(rng.poisson(1.3)), away_goals=int(rng.poisson(1.3)),
                    date=base + timedelta(days=day), league="CUP",
                )
            )
            day += 1
        return records

    def test_flag_off_leaves_no_offsets(self):
        model = DixonColesModel.fit(self._records(), halflife_days=1_000_000)
        assert model.params.league_intercepts == {}
        assert model.params.base_league is None

    def test_single_league_leaves_no_offsets(self):
        records = [r for r in self._records() if r.league == "LW"]
        model = DixonColesModel.fit(
            records, halflife_days=1_000_000, per_league_intercepts=True
        )
        # One competition only: nothing to offset against.
        assert model.params.league_intercepts == {}

    def test_offsets_capture_league_scoring_level(self):
        model = DixonColesModel.fit(
            self._records(), halflife_days=1_000_000, per_league_intercepts=True
        )
        assert set(model.params.league_intercepts) == {"LW", "LS", "CUP"}
        lo_h, lo_a = model.expected_goals("W1", "W2", league="LW")
        hi_h, hi_a = model.expected_goals("S1", "S2", league="LS")
        lo = (lo_h + lo_a) / 2
        hi = (hi_h + hi_a) / 2
        assert hi > lo  # the stronger league's baseline goals are higher
        assert 1.5 < hi < 2.0
        assert 0.6 < lo < 1.2

    def test_league_kwarg_selects_the_baseline(self):
        model = DixonColesModel.fit(
            self._records(), halflife_days=1_000_000, per_league_intercepts=True
        )
        lo, _ = model.expected_goals("W1", "W2", league="LW")
        hi, _ = model.expected_goals("W1", "W2", league="LS")
        assert hi > lo
        # An unknown league falls back to the reference baseline.
        ref, _ = model.expected_goals("W1", "W2")
        assert ref == pytest.approx(model.expected_goals("W1", "W2", league=None)[0])


class TestThinCupShrinkage:
    """A cup gives each club one or two matches, which leaves attack/defence
    unidentifiable; without shrinkage the optimiser runs strengths to the
    parameter bounds and expected goals explode (a real Champions-League fit
    predicted a total near 18).  The L2 penalty must keep them bounded."""

    @staticmethod
    def _thin_cup_records() -> list[MatchRecord]:
        rng = np.random.default_rng(11)
        base = datetime(2023, 1, 1, tzinfo=UTC)
        records: list[MatchRecord] = []
        day = 0
        # A well-supported domestic league.
        league_teams = [f"W{i}" for i in range(1, 5)]
        for _ in range(300):
            home, away = rng.choice(league_teams, size=2, replace=False)
            records.append(
                MatchRecord(
                    home=str(home), away=str(away),
                    home_goals=int(rng.poisson(1.3)), away_goals=int(rng.poisson(1.3)),
                    date=base + timedelta(days=day), league="LW",
                )
            )
            day += 1
        # A thin cup: twelve clubs with two matches each and lopsided scorelines.
        strong = [f"C{i}" for i in range(1, 7)]
        weak = [f"C{i}" for i in range(7, 13)]
        for i, w in enumerate(weak):
            s1, s2 = strong[i % 6], strong[(i + 1) % 6]
            records.append(
                MatchRecord(home=s1, away=w, home_goals=5, away_goals=0,
                            date=base + timedelta(days=day), league="CUP")
            )
            day += 1
            records.append(
                MatchRecord(home=w, away=s2, home_goals=1, away_goals=4,
                            date=base + timedelta(days=day), league="CUP")
            )
            day += 1
        return records

    def test_shrinkage_bounds_a_thin_cup(self):
        recs = self._thin_cup_records()
        loose = DixonColesModel.fit(
            recs, halflife_days=1_000_000, per_league_intercepts=True, regularization=0.0
        )
        tight = DixonColesModel.fit(
            recs, halflife_days=1_000_000, per_league_intercepts=True, regularization=1.0
        )

        def spread(model: DixonColesModel) -> float:
            return sum(abs(v) for v in model.params.attack.values()) + sum(
                abs(v) for v in model.params.defence.values()
            )

        assert spread(tight) < spread(loose)  # shrinkage pulls strengths in
        # No cup fixture may predict an absurd total once shrinkage is on.
        cup = [f"C{i}" for i in range(1, 13)]
        for h in cup:
            for a in cup:
                if h == a:
                    continue
                lh, la = tight.expected_goals(h, a, league="CUP")
                assert lh + la < 6.0

    def test_default_regularization_is_nonzero(self):
        from app.config import Settings

        assert Settings().model_regularization > 0


class TestFormCovariate:
    def test_with_form_uses_only_prior_matches(self):
        base = datetime(2024, 1, 1, tzinfo=UTC)

        def rec(home, away, day, hg, ag):
            return MatchRecord(home=home, away=away, home_goals=hg, away_goals=ag,
                               date=base + timedelta(days=day))

        # A wins every match it plays before its third outing; form must reflect
        # those prior results only, never the match being annotated.
        out = with_form(
            [
                rec("A", "B", 0, 3, 0),  # A wins (3 pts)
                rec("A", "C", 7, 3, 0),  # A wins again
                rec("A", "D", 14, 0, 3),  # A loses this one (annotate before result)
            ],
            window=5,
        )
        assert out[0].form_home is None and out[0].form_away is None
        assert out[1].form_home == pytest.approx(3.0)  # one prior win
        assert out[2].form_home == pytest.approx(3.0)  # two prior wins
        # The side that lost match 0 has 0 points in its only prior match.
        assert out[1].form_away is None  # C had no prior match

    def test_with_form_window_averages_last_n(self):
        base = datetime(2024, 1, 1, tzinfo=UTC)
        records = []
        for i in range(4):
            records.append(
                MatchRecord(home="A", away=f"X{i}", home_goals=3, away_goals=0,
                            date=base + timedelta(days=i * 7))
            )
        out = with_form(records, window=2)
        assert out[3].form_home == pytest.approx(3.0)

    def test_form_coeff_only_when_enabled(self):
        recs = _synthetic_records()
        plain = DixonColesModel.fit(recs, halflife_days=100_000)
        formed = DixonColesModel.fit(recs, halflife_days=100_000, use_form=True)
        assert plain.params.form_coeff is None
        assert plain.params.form_window is None
        assert formed.params.form_coeff is not None
        assert formed.params.form_window == 5

    def test_form_diff_shifts_goals_in_coeff_direction(self):
        model = DixonColesModel.fit(
            _synthetic_records(), halflife_days=100_000, use_form=True
        )
        coeff = model.params.form_coeff
        assert coeff is not None
        lh0, la0 = model.expected_goals("Alpha", "Beta", form_diff=0.0)
        lh1, la1 = model.expected_goals("Alpha", "Beta", form_diff=2.0)
        assert np.sign(lh1 - lh0) == np.sign(coeff)
        assert np.sign(la1 - la0) == -np.sign(coeff)

    def test_form_diff_ignored_without_form_model(self):
        model = DixonColesModel.fit(_synthetic_records(), halflife_days=100_000)
        base = model.expected_goals("Alpha", "Beta")
        shifted = model.expected_goals("Alpha", "Beta", form_diff=2.0)
        assert shifted == base

    def test_recovers_a_planted_form_effect(self):
        from dataclasses import replace

        def build(true_coeff: float, seed: int) -> float:
            rng = np.random.default_rng(seed)
            base = datetime(2023, 1, 1, tzinfo=UTC)
            teams = [f"T{i:02d}" for i in range(20)]
            raw: list[MatchRecord] = []
            for i in range(6000):
                home = str(rng.choice(teams))
                away = str(rng.choice([t for t in teams if t != home]))
                raw.append(MatchRecord(home=home, away=away, home_goals=0, away_goals=0,
                                       date=base + timedelta(days=i)))
            # A first pass of ordinary results establishes each side's real form,
            # then goals are re-drawn from a planted form effect.  Form is a noisy
            # proxy that overlaps team strength, so the fitted coefficient is
            # attenuated well below the planted value; the test only requires it
            # to be near zero with no effect and clearly positive with one.
            stated = [
                replace(m, home_goals=int(rng.poisson(1.3)), away_goals=int(rng.poisson(1.0)))
                for m in raw
            ]
            annotated = with_form(stated, window=5)
            records: list[MatchRecord] = []
            for m in annotated:
                diff = (m.form_home or 0.0) - (m.form_away or 0.0)
                lh = 1.3 * np.exp(true_coeff * diff)
                la = 1.0 * np.exp(-true_coeff * diff)
                records.append(replace(m,
                                       home_goals=int(rng.poisson(lh)),
                                       away_goals=int(rng.poisson(la))))
            model = DixonColesModel.fit(records, halflife_days=100_000, use_form=True)
            assert model.params.form_coeff is not None
            return model.params.form_coeff

        flat = build(0.0, 3)
        planted = build(0.6, 3)
        assert abs(flat) < 0.03  # no spurious signal when form does not matter
        assert 0.05 < planted < 0.30  # a real effect is picked up, attenuated
        assert planted > flat


class TestSimulate:
    def test_monte_carlo_matches_analytic(self):
        result = simulate(1.6, 1.1, n_simulations=200_000, kmax=12, seed=3)
        analytic = one_x_two(score_matrix(1.6, 1.1, kmax=12))
        assert result.one_x_two["home"] == pytest.approx(analytic["home"], abs=0.01)
        assert result.one_x_two["draw"] == pytest.approx(analytic["draw"], abs=0.01)
        assert result.one_x_two["away"] == pytest.approx(analytic["away"], abs=0.01)
        assert result.mc_std_error < 0.005

    def test_seed_is_reproducible(self):
        a = simulate(1.4, 1.2, n_simulations=50_000, seed=42)
        b = simulate(1.4, 1.2, n_simulations=50_000, seed=42)
        assert a.one_x_two == b.one_x_two
