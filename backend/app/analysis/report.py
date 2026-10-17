"""Report assembly helpers: data-driven findings and explicit limitations.

Findings are only emitted when a numeric threshold is crossed, and each one
carries the numbers that support it.  Limitations always list what is missing so
the reader knows what could change the assessment.
"""

from __future__ import annotations

import math
from typing import Any


def derive_findings(
    *,
    home_name: str,
    away_name: str,
    lambda_home: float,
    lambda_away: float,
    home_advantage: float,
    one_x_two: dict[str, float],
    totals: dict[str, dict[str, float]],
    btts: dict[str, float],
    home_form: dict[str, Any],
    away_form: dict[str, Any],
    ratings: dict[str, dict[str, float]] | None = None,
    xg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    # Largest 1X2 outcome.
    best_outcome = max(one_x_two, key=one_x_two.get)
    label = {"home": f"{home_name} win", "draw": "Draw", "away": f"{away_name} win"}[best_outcome]
    findings.append(
        {
            "title": "Most likely result",
            "detail": f"The model makes {label} the single most likely 1X2 outcome.",
            "evidence": [
                f"P(home win)={one_x_two['home']:.3f}",
                f"P(draw)={one_x_two['draw']:.3f}",
                f"P(away win)={one_x_two['away']:.3f}",
            ],
        }
    )

    # Expected goals split.
    findings.append(
        {
            "title": "Expected goals",
            "detail": f"Model expected goals: {home_name} {lambda_home:.2f}, {away_name} {lambda_away:.2f}.",
            "evidence": [
                f"total expected goals={lambda_home + lambda_away:.2f}",
                f"fitted home advantage={home_advantage:+.3f} (log scale)",
            ],
        }
    )

    # Total goals lean versus a balanced 50/50.
    over25 = totals.get("2.5", {}).get("over")
    if over25 is not None and (over25 >= 0.58 or over25 <= 0.42):
        lean = "over" if over25 > 0.5 else "under"
        findings.append(
            {
                "title": "Total-goals lean",
                "detail": f"The model leans {lean} 2.5 goals.",
                "evidence": [
                    f"P(over 2.5)={over25:.3f}",
                    f"P(under 2.5)={totals['2.5']['under']:.3f}",
                ],
            }
        )

    # BTTS.
    if btts["yes"] >= 0.6 or btts["yes"] <= 0.4:
        findings.append(
            {
                "title": "Both teams to score",
                "detail": f"Model probability that both teams score is {btts['yes']:.3f}.",
                "evidence": [f"P(BTTS yes)={btts['yes']:.3f}", f"P(BTTS no)={btts['no']:.3f}"],
            }
        )

    # Form contrast.
    hp = home_form.get("points_per_match")
    ap = away_form.get("points_per_match")
    if hp is not None and ap is not None and abs(hp - ap) >= 0.7:
        better = home_name if hp > ap else away_name
        findings.append(
            {
                "title": "Recent form contrast",
                "detail": f"{better} has the stronger recent points-per-match return.",
                "evidence": [
                    f"{home_name} points per match={hp:.2f}",
                    f"{away_name} points per match={ap:.2f}",
                ],
            }
        )

    # Attack/defence rating edge.
    if ratings:
        h = ratings.get("home", {})
        a = ratings.get("away", {})
        h_attack, a_attack = h.get("attack_multiplier"), a.get("attack_multiplier")
        h_def, a_def = h.get("defence_multiplier"), a.get("defence_multiplier")
        if h_attack and a_attack and abs(h_attack - a_attack) >= 0.2:
            stronger = home_name if h_attack > a_attack else away_name
            findings.append(
                {
                    "title": "Attacking strength",
                    "detail": f"{stronger} has the higher attack multiplier relative to league average.",
                    "evidence": [
                        f"{home_name} attack multiplier={h_attack:.3f}",
                        f"{away_name} attack multiplier={a_attack:.3f}",
                    ],
                }
            )
        if h_def and a_def and abs(h_def - a_def) >= 0.2:
            stronger = home_name if h_def > a_def else away_name
            findings.append(
                {
                    "title": "Defensive strength",
                    "detail": f"{stronger} has the higher defence multiplier (concedes less than average).",
                    "evidence": [
                        f"{home_name} defence multiplier={h_def:.3f}",
                        f"{away_name} defence multiplier={a_def:.3f}",
                    ],
                }
            )

    # xG divergence (only when xG is present for both).
    if xg and xg.get("home") and xg.get("away"):
        hx = xg["home"].get("xg_for_avg")
        ax = xg["away"].get("xg_for_avg")
        if hx is not None and ax is not None:
            findings.append(
                {
                    "title": "Expected-goals baseline",
                    "detail": "Recent xG per match is available and is shown alongside actual goals.",
                    "evidence": [
                        f"{home_name} xG for/match={hx:.2f}",
                        f"{away_name} xG for/match={ax:.2f}",
                    ],
                }
            )

    return findings


def _fair_odds(probability: float) -> float | None:
    if probability <= 0:
        return None
    return 1.0 / probability


def derive_opportunities(
    *,
    home_name: str,
    away_name: str,
    one_x_two: dict[str, float],
    double_chance: dict[str, float],
    totals: dict[str, dict[str, float]],
    btts: dict[str, float],
    half_time: dict[str, Any] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Rank the model's clearest statistical leans (highest probabilities).

    Every entry is a plain probability the score model produced, paired with its
    fair (margin-free) price.  It is a ranked reading of the model's output, not
    a tip and not a claim of an edge over any real market price.
    """
    candidates: list[dict[str, Any]] = []

    def add(market: str, selection: str, label: str, probability: float) -> None:
        candidates.append(
            {
                "market": market,
                "selection": selection,
                "label": label,
                "probability": probability,
                "fair_odds": _fair_odds(probability),
            }
        )

    for key, probability in one_x_two.items():
        label = {"home": f"{home_name} win", "draw": "Draw", "away": f"{away_name} win"}[key]
        add("1x2", key, label, probability)

    dc_labels = {
        "home_or_draw": f"{home_name} or draw",
        "home_or_away": "No draw",
        "draw_or_away": f"{away_name} or draw",
    }
    for key, probability in double_chance.items():
        add("double_chance", key, dc_labels.get(key, key), probability)

    for line, entry in totals.items():
        add("o/u", f"over_{line}", f"Over {line} goals", entry["over"])
        add("o/u", f"under_{line}", f"Under {line} goals", entry["under"])

    add("btts", "yes", "Both teams to score — Yes", btts["yes"])
    add("btts", "no", "Both teams to score — No", btts["no"])

    if half_time and half_time.get("available"):
        ht_one_x_two = half_time.get("one_x_two") or {}
        for key, probability in ht_one_x_two.items():
            label = {
                "home": f"{home_name} leading at HT",
                "draw": "Draw at half-time",
                "away": f"{away_name} leading at HT",
            }[key]
            add("ht_1x2", key, label, probability)

    candidates.sort(key=lambda c: c["probability"], reverse=True)
    return candidates[:limit]


CONFIDENCE_LEVELS = ("very low", "low", "moderate", "high", "very high")

#: Weight (of 100) given to how clear-cut the prediction is versus how well the
#: model is supported by data.  A confident call needs both a well-fed model and
#: a decisive match; neither on its own can push the grade up.
_CLARITY_WEIGHT = 0.5


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _decisiveness(one_x_two: dict[str, float] | None) -> float:
    """How one-sided a 1X2 prediction is, in ``[0, 1]``.

    ``0`` is a near coin-flip (the most likely outcome barely above a third) and
    ``1`` is a lopsided match (a strong favourite).  When the market is unknown
    the term is neutral rather than assumed confident.
    """
    if not one_x_two:
        return 0.5
    top = max(one_x_two.values())
    even = 1.0 / 3.0
    return _clamp((top - even) / (0.80 - even), 0.0, 1.0)


def _level_for(score: int) -> str:
    if score >= 85:
        return "very high"
    if score >= 66:
        return "high"
    if score >= 48:
        return "moderate"
    if score >= 30:
        return "low"
    return "very low"


def derive_confidence(
    *,
    n_training_matches: int,
    min_matches: int,
    monte_carlo_std_error: float,
    converged: bool,
    counters: dict[str, int],
    has_odds: bool = False,
    one_x_two: dict[str, float] | None = None,
    has_secondary_stats: bool = False,
) -> dict[str, Any]:
    """Grade how well-supported a prediction is, from "very low" to "very high".

    The grade is built from two halves:

    * **Support** (0-70) -- how much the model can be trusted: data volume
      (sub-linear, so a huge history adds little beyond a good one), fit
      convergence, Monte-Carlo precision, and data coverage (form, head-to-head,
      xG, half-time scores, corners/cards).
    * **Clarity** (0-70, from the 1X2) -- how decisive the match itself is.  A
      finely balanced fixture caps the grade however much data we hold, so a
      busy league no longer reports "high" for every game.

    Hard caveats still cap the level, and every reason is named in ``notes``.
    """
    notes: list[str] = []

    floor = max(min_matches, 1)

    # 1. Data support (0-30): sub-linear, saturating at roughly six times the
    #    minimum sample so extra history keeps helping but with diminishing value.
    volume_ratio = n_training_matches / floor
    data_points = 30.0 * _clamp(
        math.log1p(volume_ratio) / math.log1p(6.0), 0.0, 1.0
    )

    # 2. Model reliability (0-15): convergence plus Monte-Carlo precision.
    convergence_points = 9.0 if converged else 0.0
    mc_points = 6.0 * _clamp((0.012 - monte_carlo_std_error) / 0.009, 0.0, 1.0)
    reliability_points = convergence_points + mc_points

    # 3. Data coverage (0-25): which complementary inputs are actually present.
    coverage = 0.0
    if counters.get("home_form", 0) >= 5:
        coverage += 5.0
    if counters.get("away_form", 0) >= 5:
        coverage += 5.0
    if counters.get("h2h", 0) > 0:
        coverage += 5.0
    if counters.get("has_xg"):
        coverage += 5.0
    if counters.get("has_halftime"):
        coverage += 3.0
    if has_secondary_stats:
        coverage += 2.0

    support = data_points + reliability_points + coverage  # 0-70
    support_norm = _clamp(support / 70.0, 0.0, 1.0)

    decisiveness = _decisiveness(one_x_two)
    clarity_points = 70.0 * decisiveness

    score = int(round(100.0 * ((1.0 - _CLARITY_WEIGHT) * support_norm + _CLARITY_WEIGHT * decisiveness)))
    score = int(_clamp(float(score), 0.0, 100.0))
    level = _level_for(score)

    if n_training_matches < min_matches:
        if level not in ("very low", "low"):
            level = "low"
        notes.append(
            f"Only {n_training_matches} completed matches were available (fewer than the "
            f"recommended {min_matches}); the grade is capped."
        )
    if not converged:
        if level in ("very high", "high"):
            level = "moderate"
        notes.append("The fit did not reach its convergence tolerance; the grade is capped.")
    if not counters.get("has_xg"):
        if level == "very high":
            level = "high"
        notes.append(
            "No expected-goals (xG) data is available, so the grade is held below the top band."
        )
    if counters.get("no_history_teams"):
        if level not in ("very low", "low"):
            level = "low"
        notes.append(
            "At least one team has no completed match in the fitted window, so its strength "
            "is only a league-average prior; the grade is capped."
        )

    if one_x_two is not None:
        top = max(one_x_two.values())
        if decisiveness >= 0.5:
            notes.append(
                f"One outcome is clearly favoured (most likely {top:.0%}), which raises the grade."
            )
        elif decisiveness <= 0.15:
            notes.append(
                f"The 1X2 outcome is finely balanced (most likely {top:.0%}), which lowers the grade."
            )

    notes.append(
        "Confidence combines data support with how decisive the match is — not a guarantee."
    )
    notes.append(
        "Monte-Carlo intervals widen with fewer simulations; the underlying model "
        "uncertainty is larger."
    )

    return {
        "level": level,
        "score": score,
        "n_training_matches": n_training_matches,
        "monte_carlo_std_error": round(monte_carlo_std_error, 6),
        "components": {
            "data_volume": round(data_points, 1),
            "convergence": round(convergence_points, 1),
            "monte_carlo": round(mc_points, 1),
            "coverage": round(coverage, 1),
            "clarity": round(clarity_points, 1),
        },
        "notes": notes,
    }


def derive_limitations(
    *,
    n_training_matches: int,
    min_matches: int,
    counters: dict[str, int],
    backtest_warnings: list[str] | None = None,
    has_odds: bool = False,
    has_secondary_stats: bool = False,
) -> list[str]:
    limitations: list[str] = []
    if n_training_matches < min_matches:
        limitations.append(
            f"Only {n_training_matches} completed matches were available to fit the model "
            f"(minimum recommended is {min_matches}); parameter estimates are unstable."
        )
    if counters.get("home_form", 0) < 5:
        limitations.append("Home team has fewer than 5 recent completed matches; form is thin.")
    if counters.get("away_form", 0) < 5:
        limitations.append("Away team has fewer than 5 recent completed matches; form is thin.")
    no_history = counters.get("no_history_teams", 0)
    if no_history:
        who = "Both teams have" if no_history >= 2 else "One team has"
        limitations.append(
            f"{who} no completed match in the fitted window; their strength is set to the "
            "league average (a prior with no evidence), so this estimate is weak."
        )
    if counters.get("h2h", 0) == 0:
        limitations.append("No head-to-head history available.")
    if not counters.get("has_xg"):
        limitations.append(
            "No expected-goals (xG) data for this competition; the model uses actual goals only."
        )
    if not counters.get("has_halftime"):
        limitations.append("No half-time scores; first-half goal markets are not modelled.")
    if not has_secondary_stats:
        limitations.append(
            "No corner or card data is populated for this competition; corner and card "
            "markets are not modelled."
        )
    limitations.append(
        "Player availability, confirmed lineups and injuries are not included unless a "
        "provider that supplies them is enabled and populated."
    )
    limitations.append(
        "The model assumes team strengths are stable over the fitted window (with time decay); "
        "transfers, managerial changes and mid-season slumps are only captured indirectly."
    )
    if not has_odds:
        limitations.append("No market odds stored for this match; value analysis is unavailable.")
    for warning in backtest_warnings or []:
        limitations.append(warning)
    return limitations
