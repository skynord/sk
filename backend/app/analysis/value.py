"""Expected-value / value-betting analysis.

This module only does arithmetic on numbers the user supplies (their own odds)
and probabilities the model produced.  It never places a bet, never touches a
gambling account and never claims that positive EV guarantees profit.
"""

from __future__ import annotations

from dataclasses import dataclass


def implied_probability(decimal_odds: float) -> float:
    if decimal_odds <= 1.0:
        raise ValueError("decimal odds must be greater than 1.0")
    return 1.0 / decimal_odds


def overround(decimal_odds: list[float]) -> float:
    """Sum of implied probabilities across a full market (1.0 = fair)."""
    return sum(implied_probability(o) for o in decimal_odds)


def fair_probabilities(decimal_odds: list[float]) -> list[float]:
    """Overround-adjusted market probabilities for a full market."""
    total = overround(decimal_odds)
    return [implied_probability(o) / total for o in decimal_odds]


def expected_value(model_probability: float, decimal_odds: float) -> float:
    """EV per unit staked: p * odds - 1."""
    return model_probability * decimal_odds - 1.0


def break_even_probability(decimal_odds: float) -> float:
    return 1.0 / decimal_odds


def decimal_odds_from_probability(probability: float | None) -> float | None:
    """Fair decimal odds for a model probability (no bookmaker margin).

    This is the pure break-even price of a probability: ``1 / p``.  It is shown
    so a reader can compare it against a real quoted price; it is not a tip.
    """
    if probability is None or probability <= 0.0:
        return None
    return 1.0 / probability


def edge(model_probability: float, market_fair_probability: float) -> float:
    return model_probability - market_fair_probability


@dataclass
class ValueAssessment:
    selection: str
    model_probability: float
    decimal_odds: float
    implied_probability: float
    market_fair_probability: float | None
    expected_value: float
    break_even_probability: float
    edge: float
    is_positive_ev: bool
    confidence: str
    caveats: list[str]


def assess_selection(
    selection: str,
    model_probability: float,
    decimal_odds: float,
    market_fair_probability: float | None = None,
) -> ValueAssessment:
    implied = implied_probability(decimal_odds)
    ev = expected_value(model_probability, decimal_odds)
    be = break_even_probability(decimal_odds)
    caveats = [
        "A positive estimated EV is a model output, not a guarantee of profit.",
        "Model uncertainty and market movement can erase any apparent edge.",
    ]
    # Confidence is only about the size of the gap, not about being right.
    gap = ev
    if abs(gap) < 0.02:
        confidence = "low"
        caveats.append("Estimated edge is within normal model noise.")
    elif abs(gap) < 0.06:
        confidence = "moderate"
    else:
        confidence = "high (large gap - verify inputs)"
    return ValueAssessment(
        selection=selection,
        model_probability=model_probability,
        decimal_odds=decimal_odds,
        implied_probability=implied,
        market_fair_probability=market_fair_probability,
        expected_value=ev,
        break_even_probability=be,
        edge=edge(model_probability, market_fair_probability)
        if market_fair_probability is not None
        else model_probability - implied,
        is_positive_ev=ev > 0,
        confidence=confidence,
        caveats=caveats,
    )


def ev_sensitivity(
    model_probability: float, decimal_odds: float, delta: float = 0.02
) -> dict[str, float]:
    """EV at p-delta, p and p+delta to show sensitivity to the probability estimate."""
    lo = max(0.0, model_probability - delta)
    hi = min(1.0, model_probability + delta)
    return {
        "p_minus": expected_value(lo, decimal_odds),
        "p": expected_value(model_probability, decimal_odds),
        "p_plus": expected_value(hi, decimal_odds),
    }
