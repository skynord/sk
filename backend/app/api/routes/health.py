"""Health, data-source registry and provider capability endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import get_settings
from app.db.models import DataSource
from app.providers import available_provider_names, get_provider

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.environment,
        "providers_enabled": settings.provider_list,
    }


@router.get("/sources")
def list_sources(db: Session = Depends(get_db)) -> dict:
    """Registered data sources and their configuration status."""
    rows = list(db.execute(select(DataSource)).scalars())
    return {
        "sources": [
            {
                "name": s.name,
                "kind": s.kind,
                "base_url": s.base_url,
                "requires_key": s.requires_key,
                "enabled": s.enabled,
                "status": s.status,
                "message": s.message,
                "license_note": s.license_note,
                "cost_note": s.cost_note,
                "last_checked_at": s.last_checked_at.isoformat() if s.last_checked_at else None,
            }
            for s in rows
        ]
    }


@router.get("/providers")
def list_providers() -> dict:
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
                "base_url": caps.base_url,
                "requires_key": caps.requires_key,
                "license_note": caps.license_note,
                "cost_note": caps.cost_note,
                "terms_url": caps.terms_url,
                "notes": caps.notes,
                "available": available,
                "reason": reason,
            }
        )
    return {"providers": providers}
