"""Match analysis endpoint - the heart of the application."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.analysis import engine
from app.api.deps import get_db
from app.providers.base import ProviderError
from app.schemas import AnalysisOptionsRequest

router = APIRouter(tags=["analysis"])


def _options(payload: AnalysisOptionsRequest | None) -> engine.AnalysisOptions:
    if payload is None:
        return engine.AnalysisOptions()
    return engine.AnalysisOptions(
        monte_carlo_simulations=payload.monte_carlo_simulations,
        xg_weight=payload.xg_weight,
        seed=payload.seed,
        kmax=payload.kmax,
        halflife_days=payload.halflife_days,
        regularization=payload.regularization,
        use_rest=payload.use_rest,
        use_form=payload.use_form,
        form_window=payload.form_window,
        lookback_days=payload.lookback_days,
        include_asian_handicap=payload.include_asian_handicap,
        manual_odds=[o.model_dump() for o in payload.manual_odds] if payload.manual_odds else None,
        persist=payload.persist,
    )


@router.post("/analysis/{match_id}")
def analyse_match(
    match_id: int,
    payload: AnalysisOptionsRequest | None = None,
    db: Session = Depends(get_db),
) -> dict:
    try:
        return engine.build_analysis(db, match_id, _options(payload))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/analysis/{match_id}")
def analyse_match_default(match_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        return engine.build_analysis(db, match_id, engine.AnalysisOptions(persist=False))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
