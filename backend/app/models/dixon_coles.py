"""Dixon-Coles model with time decay.

We fit team attack and defence strengths, a global home advantage and the
Dixon-Coles low-score correlation ``rho`` by weighted maximum likelihood, where
each historical match is weighted by an exponential time decay
(``0.5 ** (age_days / halflife)``).  This is the classic Dixon-Coles (1997)
formulation; no hand-tuned rules are involved.

Identifiability: the model is invariant to shifting every attack and defence by
the same constant, so ``attack[0]`` is pinned to zero.

Cross-competition fits: when the training records span more than one
competition (a cup pooling the domestic leagues of the clubs that play in it),
``per_league_intercepts`` fits one intercept offset per competition so a club's
attack/defence is measured relative to its own league's scoring level and is
therefore comparable across leagues.  The first competition's offset is pinned
to zero as the reference; predictions then select the offset of the competition
the fixture is actually played in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime

import numpy as np
from scipy import optimize, stats

from app.models.poisson import score_matrix

MODEL_NAME = "dixon_coles"
MODEL_VERSION = "1.0.0"

EPS = 1e-10


@dataclass
class MatchRecord:
    """Minimal historical match used for fitting."""

    home: str
    away: str
    home_goals: int
    away_goals: int
    date: datetime
    #: Competition key this match belongs to, used to fit a per-competition
    #: intercept so strengths from different leagues are comparable.  ``None``
    #: means "the single reference competition" (the common single-league case).
    league: str | None = None
    weight: float = 1.0
    #: Days since each side last played, filled by :func:`with_rest`.
    rest_home: float | None = None
    rest_away: float | None = None


@dataclass
class DixonColesParams:
    teams: list[str]
    attack: dict[str, float]
    defence: dict[str, float]
    intercept: float
    home_advantage: float
    rho: float
    halflife_days: float
    regularization: float
    n_matches: int
    reference_date: datetime
    #: Fitted effect of a rest advantage (log goals per extra rest day), or
    #: ``None`` when the model was fitted without rest covariates.
    rest_coeff: float | None = None
    #: Per-competition intercept offsets (log scale) relative to the reference
    #: competition, so a team's attack/defence is comparable across leagues.
    #: Empty when the model was fitted on a single competition.
    league_intercepts: dict[str, float] = field(default_factory=dict)
    #: The competition whose offset is pinned to zero (the reference).
    base_league: str | None = None
    fitted_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    converged: bool = True

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["fitted_at"] = self.fitted_at.isoformat()
        payload["reference_date"] = self.reference_date.isoformat()
        return payload


def time_decay_weights(
    dates: Sequence[datetime], reference: datetime, halflife_days: float
) -> np.ndarray:
    """Exponential half-life weights; more recent matches weigh more."""
    if halflife_days <= 0:
        return np.ones(len(dates), dtype=float)
    ages = np.array(
        [max((reference - d).total_seconds() / 86400.0, 0.0) for d in dates],
        dtype=float,
    )
    return np.power(0.5, ages / halflife_days)


def with_rest(records: Sequence[MatchRecord]) -> list[MatchRecord]:
    """Annotate each record with days of rest (``None`` when first seen).

    Rest is measured from each club's own previous match within the given set,
    in chronological order, so no future match leaks into an earlier row.
    """
    ordered = sorted(records, key=lambda r: r.date)
    last: dict[str, datetime] = {}
    out: list[MatchRecord] = []
    for record in ordered:
        rest_home = (
            (record.date - last[record.home]).total_seconds() / 86400.0
            if record.home in last
            else None
        )
        rest_away = (
            (record.date - last[record.away]).total_seconds() / 86400.0
            if record.away in last
            else None
        )
        out.append(replace(record, rest_home=rest_home, rest_away=rest_away))
        last[record.home] = record.date
        last[record.away] = record.date
    return out


def _tau_vector(hg: np.ndarray, ag: np.ndarray, lh: np.ndarray, la: np.ndarray,
                rho: float) -> np.ndarray:
    tau = np.ones_like(lh)
    m = (hg == 0) & (ag == 0)
    tau[m] = 1.0 - lh[m] * la[m] * rho
    m = (hg == 0) & (ag == 1)
    tau[m] = 1.0 + lh[m] * rho
    m = (hg == 1) & (ag == 0)
    tau[m] = 1.0 + la[m] * rho
    m = (hg == 1) & (ag == 1)
    tau[m] = 1.0 - rho
    return np.clip(tau, EPS, None)


class DixonColesModel:
    """Fitted Dixon-Coles model ready to predict expected goals."""

    def __init__(self, params: DixonColesParams) -> None:
        self.params = params
        self._index = {team: i for i, team in enumerate(params.teams)}
        self._attack = np.array([params.attack[t] for t in params.teams])
        self._defence = np.array([params.defence[t] for t in params.teams])

    # -- construction ---------------------------------------------------
    @classmethod
    def fit(
        cls,
        records: Sequence[MatchRecord],
        *,
        halflife_days: float = 180.0,
        regularization: float = 0.0,
        reference_date: datetime | None = None,
        max_iter: int = 500,
        extra_teams: Sequence[str] = (),
        use_rest: bool = False,
        per_league_intercepts: bool = False,
    ) -> DixonColesModel:
        if not records:
            raise ValueError("cannot fit Dixon-Coles model with no matches")

        teams = sorted(
            {r.home for r in records} | {r.away for r in records} | set(extra_teams)
        )
        n = len(teams)
        if n < 2:
            raise ValueError("need at least two distinct teams to fit a model")
        index = {team: i for i, team in enumerate(teams)}

        dates = [r.date for r in records]
        ref = reference_date or max(dates)
        decay = time_decay_weights(dates, ref, halflife_days)
        sample_w = np.array([r.weight for r in records], dtype=float)
        weights = decay * sample_w

        hi = np.array([index[r.home] for r in records])
        ai = np.array([index[r.away] for r in records])
        hg = np.array([r.home_goals for r in records], dtype=float)
        ag = np.array([r.away_goals for r in records], dtype=float)

        mean_home = max(float(np.average(hg, weights=weights)), 0.1)
        mean_away = max(float(np.average(ag, weights=weights)), 0.1)

        # Per-competition intercept offsets: when the records span more than one
        # competition (a cup pooling its clubs' domestic leagues), each league's
        # scoring level is a free offset and the first is pinned to zero.  This
        # keeps a team's attack/defence comparable across leagues.
        league_names = sorted({r.league for r in records if r.league})
        if not per_league_intercepts or len(league_names) < 2:
            league_names = []
        n_league = len(league_names) - 1 if league_names else 0
        league_index = {name: i for i, name in enumerate(league_names)}
        league_of = np.array(
            [league_index.get(r.league, 0) if league_names else 0 for r in records],
            dtype=int,
        )

        # Parameter vector: [intercept, home_adv, rho, attack_1..attack_{n-1},
        #                     defence_0..defence_{n-1}, league_1..league_{L-1},
        #                     (rest_coeff)]
        n_rest = 1 if use_rest else 0
        n_base = 3 + (n - 1) + n
        x0 = np.zeros(n_base + n_league + n_rest)
        x0[0] = np.log(mean_away)
        x0[1] = np.log(mean_home) - np.log(mean_away)

        bounds = (
            [(-3.0, 3.0), (-1.5, 1.5), (-0.2, 0.2)]
            + [(-3.5, 3.5)] * (n - 1)
            + [(-3.5, 3.5)] * n
            + [(-2.5, 2.5)] * n_league
            + ([(-0.03, 0.03)] if n_rest else [])
        )

        rest_diff = np.zeros(len(records))
        if use_rest:
            rest_diff = np.array(
                [
                    (r.rest_home - r.rest_away) if (r.rest_home is not None and r.rest_away is not None) else 0.0
                    for r in records
                ]
            )

        def unpack(
            x: np.ndarray,
        ) -> tuple[np.ndarray, np.ndarray, float, float, float, float, np.ndarray]:
            intercept = x[0]
            home_adv = x[1]
            rho = x[2]
            attack = np.empty(n)
            attack[0] = 0.0
            attack[1:] = x[3 : 3 + (n - 1)]
            defence = x[3 + (n - 1) : 3 + (n - 1) + n]
            league_off = np.zeros(len(league_names))
            if n_league:
                league_off[1:] = x[n_base : n_base + n_league]
            rest_coeff = float(x[-1]) if n_rest else 0.0
            return attack, defence, intercept, home_adv, rho, rest_coeff, league_off

        def neg_log_likelihood(x: np.ndarray) -> float:
            attack, defence, intercept, home_adv, rho, rest_coeff, league_off = unpack(x)
            base = intercept + league_off[league_of] if n_league else intercept
            lh = np.exp(base + attack[hi] - defence[ai] + home_adv + rest_coeff * rest_diff)
            la = np.exp(base + attack[ai] - defence[hi] - rest_coeff * rest_diff)
            log_p = stats.poisson.logpmf(hg, lh) + stats.poisson.logpmf(ag, la)
            log_p += np.log(_tau_vector(hg, ag, lh, la, rho))
            weighted = weights * log_p
            value = -float(weighted.sum())
            if regularization:
                value += regularization * float((attack @ attack) + (defence @ defence))
            return value

        result = optimize.minimize(
            neg_log_likelihood,
            x0,
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": max_iter, "maxfun": 100000},
        )

        attack, defence, intercept, home_adv, rho, rest_coeff, league_off = unpack(result.x)
        params = DixonColesParams(
            teams=teams,
            attack={t: float(attack[i]) for i, t in enumerate(teams)},
            defence={t: float(defence[i]) for i, t in enumerate(teams)},
            intercept=float(intercept),
            home_advantage=float(home_adv),
            rho=float(rho),
            halflife_days=float(halflife_days),
            regularization=float(regularization),
            n_matches=len(records),
            reference_date=ref,
            rest_coeff=(float(rest_coeff) if n_rest else None),
            league_intercepts={
                name: float(league_off[i]) for i, name in enumerate(league_names)
            },
            base_league=(league_names[0] if league_names else None),
            converged=bool(result.success),
        )
        return cls(params)

    # -- prediction -----------------------------------------------------
    def has_team(self, team: str) -> bool:
        return team in self._index

    def expected_goals(
        self, home: str, away: str, *, neutral_venue: bool = False,
        rest_diff: float | None = None, league: str | None = None,
    ) -> tuple[float, float]:
        if not self.has_team(home) or not self.has_team(away):
            missing = [t for t in (home, away) if not self.has_team(t)]
            raise KeyError(f"unknown team(s) in model: {missing}")
        p = self.params
        home_adv = 0.0 if neutral_venue else p.home_advantage
        rest = 0.0
        if p.rest_coeff is not None and rest_diff is not None:
            rest = p.rest_coeff * rest_diff
        # The competition the fixture is played in sets the expected baseline
        # (a cup pools several leagues, so the same two attacks/defences imply
        # different goal totals depending on the league offset).
        league_off = p.league_intercepts.get(league, 0.0) if league else 0.0
        base = p.intercept + league_off
        lh = np.exp(base + p.attack[home] - p.defence[away] + home_adv + rest)
        la = np.exp(base + p.attack[away] - p.defence[home] - rest)
        return float(lh), float(la)

    def score_matrix(
        self, home: str, away: str, *, kmax: int = 10, neutral_venue: bool = False,
        rest_diff: float | None = None, league: str | None = None,
    ) -> np.ndarray:
        lh, la = self.expected_goals(
            home, away, neutral_venue=neutral_venue, rest_diff=rest_diff, league=league
        )
        return score_matrix(lh, la, kmax=kmax, rho=self.params.rho)

    def ranking(self) -> list[dict[str, float | str]]:
        """Teams ranked by net strength (attack + defence edge over average)."""
        rows = []
        for team in self.params.teams:
            attack = self.params.attack[team]
            defence = self.params.defence[team]
            rows.append(
                {
                    "team": team,
                    "attack": attack,
                    "defence": defence,
                    "overall": attack + defence,
                }
            )
        rows.sort(key=lambda r: r["overall"], reverse=True)  # type: ignore[arg-type,return-value]
        return rows
