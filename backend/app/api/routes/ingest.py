"""Ingestion endpoints: run imports and inspect history."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import IngestRun
from app.ingest import service
from app.providers import available_provider_names
from app.providers.base import ProviderError
from app.schemas import IngestFixtureRequest, IngestRequest

router = APIRouter(tags=["ingest"])


@router.get("/ingest/providers")
def ingest_providers() -> dict:
    return {"providers": available_provider_names()}


@router.post("/ingest/run")
def run_ingest(payload: IngestRequest, db: Session = Depends(get_db)) -> dict:
    try:
        params = dict(payload.params)
        params.pop("include_details", None)
        return service.ingest_provider(
            db, payload.provider, include_details=payload.include_details, **params
        )
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ingest/fixture")
def ingest_fixture(payload: IngestFixtureRequest, db: Session = Depends(get_db)) -> dict:
    try:
        return service.ingest_fixture(
            db,
            payload.provider,
            provider_id=payload.reference,
            date=payload.date,
            include_details=payload.include_details,
        )
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ingest/runs")
def ingest_runs(
    provider: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(IngestRun).order_by(IngestRun.started_at.desc())
    if provider:
        stmt = stmt.where(IngestRun.provider == provider)
    rows = list(db.execute(stmt.limit(limit)).scalars())
    return {
        "runs": [
            {
                "id": r.id,
                "provider": r.provider,
                "scope": r.scope,
                "status": r.status,
                "records_in": r.records_in,
                "records_upserted": r.records_upserted,
                "message": r.message,
                "started_at": r.started_at.isoformat(),
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in rows
        ]
    }
