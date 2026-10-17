"""Poisson goal model primitives.

A football match is modelled as two independent Poisson counts (home and away
goals).  The bivariate score matrix returned by :func:`score_matrix` is the
foundation for every market probability and the Monte-Carlo simulation.  The
Dixon-Coles low-score correction is applied here when ``rho != 0``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

EPS = 1e-12


def poisson_pmf(lam: float, kmax: int) -> np.ndarray:
    """Probability mass function of ``Poisson(lam)`` for k = 0..kmax."""
    if lam < 0:
        raise ValueError("Poisson rate must be non-negative")
    k = np.arange(kmax + 1)
    return stats.poisson.pmf(k, lam)


def dixon_coles_tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    """Dixon-Coles correction factor for a single scoreline."""
    if x == 0 and y == 0:
        return 1.0 - lam * mu * rho
    if x == 0 and y == 1:
        return 1.0 + lam * rho
    if x == 1 and y == 0:
        return 1.0 + mu * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def score_matrix(
    lambda_home: float,
    lambda_away: float,
    kmax: int = 10,
    rho: float = 0.0,
) -> np.ndarray:
    """Return a normalised ``(kmax+1, kmax+1)`` score probability matrix.

    Element ``M[i, j]`` is the probability of the home team scoring ``i`` and
    the away team scoring ``j``.
    """
    ph = poisson_pmf(lambda_home, kmax)
    pa = poisson_pmf(lambda_away, kmax)
    matrix = np.outer(ph, pa)
    if rho != 0.0:
        matrix[0, 0] *= 1.0 - lambda_home * lambda_away * rho
        matrix[0, 1] *= 1.0 + lambda_home * rho
        matrix[1, 0] *= 1.0 + lambda_away * rho
        matrix[1, 1] *= 1.0 - rho
        np.clip(matrix, EPS, None, out=matrix)
    total = float(matrix.sum())
    if total <= 0.0:
        raise ValueError("score matrix has zero mass; check the goal rates")
    return matrix / total


@dataclass(frozen=True)
class OutcomeCounts:
    """Aggregate outcome probabilities implied by a score matrix."""

    home_win: float
    draw: float
    away_win: float


def match_outcome(matrix: np.ndarray) -> OutcomeCounts:
    """Marginal 1X2 probabilities from a score matrix."""
    kmax = matrix.shape[0] - 1
    x, y = np.meshgrid(np.arange(kmax + 1), np.arange(kmax + 1), indexing="ij")
    home = float(matrix[x > y].sum())
    draw = float(matrix[x == y].sum())
    away = float(matrix[x < y].sum())
    return OutcomeCounts(home_win=home, draw=draw, away_win=away)


def outcome_from_score(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home"
    if home_goals < away_goals:
        return "away"
    return "draw"
