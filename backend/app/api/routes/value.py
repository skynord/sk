"""Value / expected-value endpoints (manual odds, no bet placement)."""

from __future__ import annotations

from fastapi import APIRouter

from app.analysis import value as value_mod
from app.schemas import ValueAssessRequest

router = APIRouter(tags=["value"])


@router.post("/value/assess")
def assess(payload: ValueAssessRequest) -> dict:
    assessment = value_mod.assess_selection(
        selection=payload.selection,
        model_probability=payload.model_probability,
        decimal_odds=payload.decimal_odds,
        market_fair_probability=payload.market_fair_probability,
    )
    sensitivity = value_mod.ev_sensitivity(payload.model_probability, payload.decimal_odds)
    return {
        "selection": assessment.selection,
        "model_probability": assessment.model_probability,
        "decimal_odds": assessment.decimal_odds,
        "implied_probability": assessment.implied_probability,
        "market_fair_probability": assessment.market_fair_probability,
        "expected_value": assessment.expected_value,
        "break_even_probability": assessment.break_even_probability,
        "edge": assessment.edge,
        "is_positive_ev": assessment.is_positive_ev,
        "confidence": assessment.confidence,
        "sensitivity": sensitivity,
        "caveats": assessment.caveats,
    }


@router.post("/value/fair-probabilities")
def fair_probabilities(decimal_odds: list[float]) -> dict:
    try:
        fair = value_mod.fair_probabilities(decimal_odds)
    except ValueError as exc:
        return {"error": str(exc)}
    return {
        "decimal_odds": decimal_odds,
        "overround": value_mod.overround(decimal_odds),
        "implied": [value_mod.implied_probability(o) for o in decimal_odds],
        "fair_probabilities": fair,
    }
