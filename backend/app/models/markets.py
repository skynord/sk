"""Market probabilities derived from a bivariate score matrix.

Every number here is the exact probability implied by the score model - no
sampling, no hardcoded rules.  Markets that need more information than the
score matrix carries (e.g. corners, cards, first-half only) are handled
elsewhere and only when the data supports them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.models.poisson import match_outcome, score_matrix

#: Standard over/under goals lines quoted by bookmakers.
TOTAL_LINES: tuple[float, ...] = (0.5, 1.5, 2.5, 3.5, 4.5)


def _goals_grid(kmax: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x, y = np.meshgrid(np.arange(kmax + 1), np.arange(kmax + 1), indexing="ij")
    return x, y, x + y


def one_x_two(matrix: np.ndarray) -> dict[str, float]:
    outcome = match_outcome(matrix)
    return {
        "home": outcome.home_win,
        "draw": outcome.draw,
        "away": outcome.away_win,
    }


def double_chance(matrix: np.ndarray) -> dict[str, float]:
    o = match_outcome(matrix)
    return {
        "home_or_draw": o.home_win + o.draw,
        "home_or_away": o.home_win + o.away_win,
        "draw_or_away": o.draw + o.away_win,
    }


def totals(matrix: np.ndarray, lines: tuple[float, ...] = TOTAL_LINES) -> dict[str, dict[str, float]]:
    """Over/under probabilities for each whole-number line."""
    kmax = matrix.shape[0] - 1
    _, _, total = _goals_grid(kmax)
    result: dict[str, dict[str, float]] = {}
    for line in lines:
        over = float(matrix[total > line].sum())
        result[f"{line}"] = {"over": over, "under": 1.0 - over}
    return result


def btts(matrix: np.ndarray) -> dict[str, float]:
    kmax = matrix.shape[0] - 1
    x, y = np.meshgrid(np.arange(kmax + 1), np.arange(kmax + 1), indexing="ij")
    yes = float(matrix[(x >= 1) & (y >= 1)].sum())
    return {"yes": yes, "no": 1.0 - yes}


def team_totals(matrix: np.ndarray, lines: tuple[float, ...] = (0.5, 1.5, 2.5)) -> dict[str, dict[str, float]]:
    kmax = matrix.shape[0] - 1
    x, y = np.meshgrid(np.arange(kmax + 1), np.arange(kmax + 1), indexing="ij")
    out: dict[str, dict[str, float]] = {"home": {}, "away": {}}
    for line in lines:
        over_home = float(matrix[x > line].sum())
        over_away = float(matrix[y > line].sum())
        out["home"][f"{line}"] = {"over": over_home, "under": 1.0 - over_home}
        out["away"][f"{line}"] = {"over": over_away, "under": 1.0 - over_away}
    return out


def correct_scores(matrix: np.ndarray, top: int = 8) -> list[dict[str, float]]:
    kmax = matrix.shape[0] - 1
    flat = matrix.reshape(-1)
    idx = np.argsort(flat)[::-1][:top]
    scores: list[dict[str, float]] = []
    for i in idx:
        h, a = divmod(int(i), kmax + 1)
        scores.append({"score": f"{h}-{a}", "home_goals": h, "away_goals": a,
                       "probability": float(flat[i])})
    return scores


def asian_handicap(matrix: np.ndarray, line: float, home: bool = True) -> dict[str, float]:
    """Outcome probabilities for an Asian handicap line applied to the home team.

    ``line`` is added to the home score (e.g. ``-1.0`` means home starts a goal
    down, ``+0.5`` means home starts half a goal up).  Quarter lines (x.25 /
    x.75) are split across the two adjacent half lines and averaged, matching
    how a real quarter-ball stake is settled.
    """
    kmax = matrix.shape[0] - 1
    x, y = np.meshgrid(np.arange(kmax + 1), np.arange(kmax + 1), indexing="ij")
    diff = x - y

    def single_leg(hl: float) -> tuple[float, float, float]:
        margin = diff + (hl if home else -hl)
        win = float(matrix[margin > 0].sum())
        push = float(matrix[margin == 0].sum())
        lose = float(matrix[margin < 0].sum())
        return win, push, lose

    frac = round(line * 2) % 2
    if frac == 1:  # quarter line -> average of two legs
        lower, upper = line - 0.25, line + 0.25
        w1, p1, l1 = single_leg(lower)
        w2, p2, l2 = single_leg(upper)
        win = 0.5 * (w1 + w2)
        push = 0.5 * (p1 + p2)
        lose = 0.5 * (l1 + l2)
    else:
        win, push, lose = single_leg(line)
    return {"win": win, "push": push, "lose": lose}


@dataclass
class MarketProbabilities:
    """Complete set of market probabilities for one match."""

    one_x_two: dict[str, float]
    double_chance: dict[str, float]
    totals: dict[str, dict[str, float]]
    btts: dict[str, float]
    team_totals: dict[str, dict[str, float]]
    correct_scores: list[dict[str, float]]
    expected_goals: dict[str, float]
    total_goals_distribution: list[dict[str, float]] = field(default_factory=list)


def all_markets(
    lambda_home: float,
    lambda_away: float,
    kmax: int = 10,
    rho: float = 0.0,
    total_goal_max: int = 8,
) -> MarketProbabilities:
    """Compute every market that the score matrix supports in one pass."""
    matrix = score_matrix(lambda_home, lambda_away, kmax=kmax, rho=rho)
    _, _, total = _goals_grid(kmax)
    dist: list[dict[str, float]] = []
    for g in range(total_goal_max + 1):
        dist.append({"goals": g, "probability": float(matrix[total == g].sum())})
    tail = float(matrix[total > total_goal_max].sum())
    if tail > 0:
        dist.append({"goals": total_goal_max + 1, "probability": tail, "is_open_ended": True})

    return MarketProbabilities(
        one_x_two=one_x_two(matrix),
        double_chance=double_chance(matrix),
        totals=totals(matrix),
        btts=btts(matrix),
        team_totals=team_totals(matrix),
        correct_scores=correct_scores(matrix),
        expected_goals={"home": lambda_home, "away": lambda_away,
                        "total": lambda_home + lambda_away},
        total_goals_distribution=dist,
    )
