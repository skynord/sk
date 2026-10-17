"""Monte-Carlo simulation of match outcomes.

The simulation samples scorelines from the model's score matrix using the
provided random seed.  A simulation is only a way of reading the model's own
distribution - it is never more accurate than the model it samples.  The result
therefore also reports the Monte-Carlo standard error so the sampling noise is
explicit.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.models.poisson import score_matrix


@dataclass
class SimulationResult:
    n_simulations: int
    seed: int
    one_x_two: dict[str, float]
    totals: dict[str, dict[str, float]]
    btts: dict[str, float]
    mean_home_goals: float
    mean_away_goals: float
    total_goals_distribution: list[dict[str, float]]
    # Monte-Carlo standard error on the home-win probability (reference point).
    mc_std_error: float


def simulate(
    lambda_home: float,
    lambda_away: float,
    *,
    n_simulations: int = 10_000,
    kmax: int = 10,
    rho: float = 0.0,
    seed: int | None = None,
) -> SimulationResult:
    if n_simulations < 1:
        raise ValueError("n_simulations must be >= 1")
    rng = np.random.default_rng(seed)
    matrix = score_matrix(lambda_home, lambda_away, kmax=kmax, rho=rho)

    probabilities = matrix.reshape(-1)
    draws = rng.choice(probabilities.size, size=n_simulations, p=probabilities)
    home_goals, away_goals = np.divmod(draws, kmax + 1)

    home_win = float(np.mean(home_goals > away_goals))
    draw = float(np.mean(home_goals == away_goals))
    away_win = float(np.mean(home_goals < away_goals))
    p_home = home_win
    mc_std_error = float(np.sqrt(p_home * (1 - p_home) / n_simulations))

    total = home_goals + away_goals
    totals: dict[str, dict[str, float]] = {}
    for line in (1.5, 2.5, 3.5, 4.5):
        over = float(np.mean(total > line))
        totals[f"{line}"] = {"over": over, "under": 1.0 - over}

    both = float(np.mean((home_goals >= 1) & (away_goals >= 1)))

    max_goals = int(total.max()) if total.size else 0
    dist = []
    for g in range(max_goals + 1):
        dist.append({"goals": g, "probability": float(np.mean(total == g))})

    return SimulationResult(
        n_simulations=n_simulations,
        seed=int(seed) if seed is not None else -1,
        one_x_two={"home": home_win, "draw": draw, "away": away_win},
        totals=totals,
        btts={"yes": both, "no": 1.0 - both},
        mean_home_goals=float(home_goals.mean()),
        mean_away_goals=float(away_goals.mean()),
        total_goals_distribution=dist,
        mc_std_error=mc_std_error,
    )
