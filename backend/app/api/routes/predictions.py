"""Prediction bot endpoint and live/upcoming fixture passthrough."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis import bot_jobs
from app.api.deps import get_db
from app.config import get_settings
from app.db.models import Match
from app.providers import Capability, get_provider
from app.providers.base import ProviderError
from app.providers.dto import NormalizedMatch
from app.timezones import iso_utc

router = APIRouter(tags=["predictions"])

LIVE_PROVIDER_PREFERENCE = ("api_football", "football_data_org", "sportmonks")


@router.get("/predictions/upcoming")
def predictions_upcoming(
    league_id: int | None = None,
    days: int = Query(default=14, ge=1, le=120),
    limit: int = Query(default=50, ge=1, le=200),
    include_live: bool = True,
    refresh: bool = False,
) -> dict:
    """Model predictions for every upcoming fixture in the window.

    Fitting the model over the whole window can outlast an HTTP request, so the
    batch runs in the background: the first call starts it and reports
    ``status: "running"``; later calls report the same status until it becomes
    ``"ready"`` (with the results) or ``"error"``.  Pass ``refresh=true`` to
    recompute, otherwise a finished run for the same settings is reused.
    """
    kwargs = {
        "league_id": league_id,
        "days_ahead": days,
        "limit": limit,
        "include_live": include_live,
        "persist": False,
    }
    key = f"{league_id}|{days}|{limit}|{int(include_live)}"
    snap = bot_jobs.ensure(key, kwargs, force=refresh)
    payload: dict = {
        "status": snap["status"],
        "started_at": snap["started_at"],
        "finished_at": snap["finished_at"],
        "error": snap["error"],
    }
    if snap["status"] == "ready" and snap["result"] is not None:
        payload.update(snap["result"])
    return payload


def _live_provider():
    settings = get_settings()
    candidates = (settings.live_provider, *LIVE_PROVIDER_PREFERENCE)
    for name in candidates:
        if not name:
            continue
        try:
            provider = get_provider(name)
        except ProviderError:
            continue
        available, _ = provider.is_available()
        if available and Capability.FIXTURES in provider.describe().capabilities:
            return provider
    return None


def _serialize(match: NormalizedMatch, local_match_id: int | None = None) -> dict:
    return {
        "provider_id": match.provider_id,
        "local_match_id": local_match_id,
        "kickoff_utc": iso_utc(match.kickoff_utc),
        "status": match.status,
        "league": match.league_name,
        "country": match.country,
        "home_team": match.home_name,
        "away_team": match.away_name,
        "score": (
            {"home": match.home_goals, "away": match.away_goals}
            if match.home_goals is not None and match.away_goals is not None
            else None
        ),
        "ht_score": (
            {"home": match.ht_home_goals, "away": match.ht_away_goals}
            if match.ht_home_goals is not None and match.ht_away_goals is not None
            else None
        ),
        "venue": match.venue,
    }


def _local_ids(db: Session, provider: str, provider_ids: set[str]) -> dict[str, int]:
    """Map provider fixture ids to the local Match ids already imported."""
    if not provider_ids:
        return {}
    rows = db.execute(
        select(Match.source_ref, Match.id).where(
            Match.source_provider == provider,
            Match.source_ref.in_(provider_ids),
        )
    ).all()
    return {ref: mid for ref, mid in rows if ref is not None}


@router.get("/live")
def live_matches(
    date: str | None = Query(default=None, description="UTC date YYYY-MM-DD (defaults to today)"),
    db: Session = Depends(get_db),
) -> dict:
    """Live scores and today's fixtures from the configured live provider.

    This is a read-only passthrough; the app never places bets.  Without a
    provider key it says exactly what to set.
    """
    provider = _live_provider()
    if provider is None:
        return {
            "available": False,
            "provider": None,
            "date": date,
            "reason": (
                "No live data provider is configured. Set FA_API_FOOTBALL_KEY "
                "(api-football.com) or FA_FOOTBALL_DATA_ORG_KEY (football-data.org) "
                "to fetch live and upcoming fixtures."
            ),
            "live": [],
            "upcoming": [],
        }

    target = date or datetime.now(tz=UTC).date().isoformat()
    notes: list[str] = []
    fetched: list[NormalizedMatch] = []

    try:
        fetched.extend(provider.fetch_matches(live=True))
    except (ProviderError, TypeError) as exc:
        notes.append(f"live: {exc}")
    try:
        fetched.extend(provider.fetch_matches(date=target))
    except (ProviderError, TypeError) as exc:
        notes.append(f"fixtures: {exc}")

    seen: set[str] = set()
    merged: list[NormalizedMatch] = []
    for match in fetched:
        if match.provider_id in seen:
            continue
        seen.add(match.provider_id)
        merged.append(match)

    local_ids = _local_ids(db, provider.describe().name, seen)

    if not merged and notes:
        return {
            "available": True,
            "provider": provider.describe().name,
            "date": target,
            "reason": " ".join(notes),
            "live": [],
            "upcoming": [],
        }

    live = [_serialize(m, local_ids.get(m.provider_id)) for m in merged if m.status == "live"]
    upcoming = [_serialize(m, local_ids.get(m.provider_id)) for m in merged if m.status == "scheduled"]
    finished = [m for m in merged if m.status == "finished"]
    result = {
        "available": True,
        "provider": provider.describe().name,
        "date": target,
        "counts": {"live": len(live), "upcoming": len(upcoming), "finished": len(finished)},
        "live": live,
        "upcoming": upcoming,
    }
    if notes:
        result["notes"] = notes
    return result
