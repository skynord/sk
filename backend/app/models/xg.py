"""Expected-goals helpers.

When xG data is available we can build expected-goal rates instead of (or
blended with) raw goals.  Two xG figures are combined multiplicatively - the
home team's attacking xG against the away team's defensive xG - using a
geometric mean, then optionally blended with the goals-based model.
"""

from __future__ import annotations

import math

EPS = 1e-6


def combine_attack_defence(attack_rate: float, defence_rate: float) -> float:
    """Geometric mean of an attacking rate and an opposing defensive rate."""
    a = max(attack_rate, EPS)
    d = max(defence_rate, EPS)
    return math.sqrt(a * d)


def xg_expected_goals(
    *,
    home_xg_for: float,
    home_xg_against: float,
    away_xg_for: float,
    away_xg_against: float,
    home_advantage_factor: float = 1.0,
) -> tuple[float, float]:
    """Home and away expected goals from per-team xG for/against averages."""
    home_lambda = combine_attack_defence(home_xg_for, away_xg_against)
    away_lambda = combine_attack_defence(away_xg_for, home_xg_against)
    return home_lambda * home_advantage_factor, away_lambda


def blend_lambdas(goal_lambda: float, xg_lambda: float, xg_weight: float) -> float:
    """Log-linear blend of a goals-based and an xG-based rate.

    ``xg_weight`` in [0, 1]; 0 uses goals only, 1 uses xG only.
    """
    if not 0.0 <= xg_weight <= 1.0:
        raise ValueError("xg_weight must be between 0 and 1")
    g = max(goal_lambda, EPS)
    x = max(xg_lambda, EPS)
    return math.exp((1.0 - xg_weight) * math.log(g) + xg_weight * math.log(x))
