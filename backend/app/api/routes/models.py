"""Model registry, backtesting and calibration endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis import engine
from app.api.deps import get_db
from app.db.models import BacktestResult, CalibrationBin, League, ModelRun
from app.models import backtest as backtest_mod
from app.models.dixon_coles import MODEL_NAME, MODEL_VERSION
from app.providers import available_provider_names, get_provider
from app.schemas import BacktestRequest

router = APIRouter(tags=["models"])

MODEL_REGISTRY = [
    {
        "name": MODEL_NAME,
        "version": MODEL_VERSION,
        "description": "Dixon-Coles Poisson model with exponential time decay and home advantage.",
        "markets": ["1x2", "double_chance", "o/u_1.5", "o/u_2.5", "o/u_3.5", "o/u_4.5",
                    "btts", "team_totals", "correct_score", "asian_handicap"],
        "requires": ["historical results"],
        "optional_inputs": ["xG (blended with configurable weight)"],
    }
]


@router.get("/models/registry")
def registry() -> dict:
    providers = []
    for name in available_provider_names():
        provider = get_provider(name)
        caps = provider.describe()
        available, reason = provider.is_available()
        providers.append(
            {
                "name": caps.name,
                "kind": caps.kind,
                "capabilities": sorted(caps.capabilities),
                "available": available,
                "reason": reason,
                "cost_note": caps.cost_note,
                "requires_key": caps.requires_key,
            }
        )
    return {"models": MODEL_REGISTRY, "providers": providers}


@router.post("/models/backtest")
def run_backtest(payload: BacktestRequest, db: Session = Depends(get_db)) -> dict:
    league = db.get(League, payload.league_id)
    if league is None:
        raise HTTPException(status_code=404, detail="league not found")
    now = datetime.now(tz=UTC)
    records = engine.training_records(db, payload.league_id, now, lookback_days=3650)
    if len(records) <= payload.min_train:
        raise HTTPException(
            status_code=422,
            detail=f"only {len(records)} completed matches for this league; "
                   f"need more than min_train={payload.min_train}",
        )
    config = backtest_mod.BacktestConfig(
        min_train=payload.min_train,
        retrain_every=payload.retrain_every,
        halflife_days=payload.halflife_days,
        totals_line=payload.totals_line,
        regularization=payload.regularization,
        use_rest=payload.use_rest,
        use_form=payload.use_form,
        form_window=payload.form_window,
    )
    report = backtest_mod.walk_forward(records, config)

    model_run = ModelRun(
        model_name=MODEL_NAME,
        model_version=MODEL_VERSION,
        league_id=payload.league_id,
        params={"backtest": config.to_dict()},
        training_start=records[0].date,
        training_end=records[-1].date,
        n_matches=len(records),
    )
    db.add(model_run)
    db.flush()

    stored_ids: list[int] = []
    for metrics in report.markets:
        row = BacktestResult(
            model_run_id=model_run.id,
            league_id=payload.league_id,
            market=metrics.market,
            n_train=report.n_train,
            n_eval=metrics.n,
            brier_score=metrics.brier,
            log_loss=metrics.log_loss,
            accuracy=metrics.accuracy,
            baseline_brier=metrics.baseline_brier,
            baseline_log_loss=metrics.baseline_log_loss,
            details={
                "baseline_accuracy": metrics.baseline_accuracy,
                "improvement_brier": metrics.improvement_brier,
            },
        )
        db.add(row)
        db.flush()
        stored_ids.append(row.id)
        for b in metrics.reliability:
            db.add(
                CalibrationBin(
                    backtest_id=row.id,
                    bin_lower=b.lower,
                    bin_upper=b.upper,
                    n=b.n,
                    mean_predicted=b.mean_predicted,
                    observed_frequency=b.observed_frequency,
                )
            )
    db.commit()

    return {
        "model_run_id": model_run.id,
        "league_id": payload.league_id,
        "league_name": league.name,
        "n_train": report.n_train,
        "n_eval": report.n_eval,
        "warnings": report.warnings,
        "backtest_ids": stored_ids,
        "markets": [_metrics_dict(m) for m in report.markets],
    }


def _metrics_dict(m: backtest_mod.MarketMetrics) -> dict:
    return {
        "market": m.market,
        "n": m.n,
        "brier": m.brier,
        "log_loss": m.log_loss,
        "accuracy": m.accuracy,
        "baseline_brier": m.baseline_brier,
        "baseline_log_loss": m.baseline_log_loss,
        "baseline_accuracy": m.baseline_accuracy,
        "improvement_brier": m.improvement_brier,
        "reliability": [
            {
                "lower": b.lower,
                "upper": b.upper,
                "n": b.n,
                "mean_predicted": b.mean_predicted,
                "observed_frequency": b.observed_frequency,
            }
            for b in m.reliability
        ],
    }


@router.get("/models/backtests")
def list_backtests(
    league_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(BacktestResult).order_by(BacktestResult.created_at.desc())
    if league_id is not None:
        stmt = stmt.where(BacktestResult.league_id == league_id)
    rows = list(db.execute(stmt.limit(limit)).scalars())
    return {
        "backtests": [
            {
                "id": r.id,
                "model_run_id": r.model_run_id,
                "league_id": r.league_id,
                "market": r.market,
                "n_eval": r.n_eval,
                "brier_score": r.brier_score,
                "log_loss": r.log_loss,
                "accuracy": r.accuracy,
                "baseline_brier": r.baseline_brier,
                "baseline_log_loss": r.baseline_log_loss,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.get("/models/backtests/{backtest_id}")
def backtest_detail(backtest_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(BacktestResult, backtest_id)
    if row is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    bins = list(
        db.execute(select(CalibrationBin).where(CalibrationBin.backtest_id == backtest_id)
                   .order_by(CalibrationBin.bin_lower)).scalars()
    )
    return {
        "id": row.id,
        "model_run_id": row.model_run_id,
        "league_id": row.league_id,
        "market": row.market,
        "n_eval": row.n_eval,
        "brier_score": row.brier_score,
        "log_loss": row.log_loss,
        "accuracy": row.accuracy,
        "baseline_brier": row.baseline_brier,
        "baseline_log_loss": row.baseline_log_loss,
        "details": row.details,
        "reliability": [
            {
                "lower": b.bin_lower,
                "upper": b.bin_upper,
                "n": b.n,
                "mean_predicted": b.mean_predicted,
                "observed_frequency": b.observed_frequency,
            }
            for b in bins
        ],
    }
