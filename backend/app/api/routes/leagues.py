"""League, season and team listing."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_db
from app.db.models import League, Match, Season, Team

router = APIRouter(tags=["reference"])


@router.get("/leagues")
def list_leagues(db: Session = Depends(get_db)) -> dict:
    leagues = list(db.execute(select(League).order_by(League.name)).scalars())
    counts = dict(
        db.execute(
            select(Match.league_id, func.count(Match.id)).group_by(Match.league_id)
        ).all()
    )
    return {
        "leagues": [
            {
                "id": lg.id,
                "slug": lg.slug,
                "name": lg.name,
                "country": lg.country,
                "match_count": counts.get(lg.id, 0),
            }
            for lg in leagues
        ]
    }


@router.get("/leagues/{league_id}")
def league_detail(league_id: int, db: Session = Depends(get_db)) -> dict:
    league = db.get(League, league_id)
    if league is None:
        raise HTTPException(status_code=404, detail="league not found")
    seasons = list(
        db.execute(select(Season).where(Season.league_id == league_id).order_by(Season.label)).scalars()
    )
    team_count = db.execute(
        select(func.count(Team.id)).where(Team.league_id == league_id)
    ).scalar_one()
    return {
        "league": {"id": league.id, "name": league.name, "country": league.country,
                   "slug": league.slug, "provider_refs": league.provider_refs},
        "seasons": [
            {"id": s.id, "label": s.label, "is_current": s.is_current}
            for s in seasons
        ],
        "team_count": team_count,
    }


@router.get("/teams")
def list_teams(
    league_id: int | None = Query(default=None),
    pagination: Pagination = Depends(),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(Team)
    if league_id is not None:
        stmt = stmt.where(Team.league_id == league_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = list(
        db.execute(stmt.order_by(Team.name).offset(pagination.offset).limit(pagination.limit)).scalars()
    )
    return {
        "total": total,
        "limit": pagination.limit,
        "offset": pagination.offset,
        "teams": [
            {"id": t.id, "name": t.name, "slug": t.slug, "country": t.country,
             "league_id": t.league_id, "provider_refs": t.provider_refs}
            for t in rows
        ],
    }
