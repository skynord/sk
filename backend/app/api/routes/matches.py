"""Match listing, detail and manual entry."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from app.api.deps import Pagination, get_db
from app.db.models import League, Match, MatchOdd, MatchStat, Season, Team
from app.ingest.service import import_manual_match
from app.schemas import ManualMatchInput
from app.timezones import iso_utc

router = APIRouter(tags=["matches"])


def _serialize(match: Match, home_name: str, away_name: str, league_name: str,
               season_label: str | None) -> dict:
    return {
        "id": match.id,
        "league_id": match.league_id,
        "league": league_name,
        "season": season_label,
        "kickoff_utc": iso_utc(match.kickoff_utc),
        "status": match.status,
        "home_team": {"id": match.home_team_id, "name": home_name},
        "away_team": {"id": match.away_team_id, "name": away_name},
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
        "round": match.round_label,
    }


@router.get("/matches")
def list_matches(
    league_id: int | None = None,
    team_id: int | None = None,
    status: str | None = Query(default=None, pattern="^(scheduled|finished|live|postponed|cancelled)$"),
    on_date: date | None = Query(default=None, description="Matches on this UTC date"),
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = Query(default=None, description="Search by team name"),
    order: str = Query(default="kickoff_asc", pattern="^(kickoff_asc|kickoff_desc)$"),
    pagination: Pagination = Depends(),
    db: Session = Depends(get_db),
) -> dict:
    Home = aliased(Team)
    Away = aliased(Team)
    stmt = (
        select(Match, Home.name, Away.name, League.name, Season.label)
        .join(Home, Match.home_team_id == Home.id)
        .join(Away, Match.away_team_id == Away.id)
        .join(League, Match.league_id == League.id)
        .outerjoin(Season, Match.season_id == Season.id)
    )
    if league_id is not None:
        stmt = stmt.where(Match.league_id == league_id)
    if team_id is not None:
        stmt = stmt.where(or_(Match.home_team_id == team_id, Match.away_team_id == team_id))
    if status is not None:
        stmt = stmt.where(Match.status == status)
    if on_date is not None:
        start = datetime.combine(on_date, time.min, tzinfo=UTC)
        end = datetime.combine(on_date, time.max, tzinfo=UTC)
        stmt = stmt.where(Match.kickoff_utc >= start, Match.kickoff_utc <= end)
    if date_from is not None:
        stmt = stmt.where(Match.kickoff_utc >= datetime.combine(date_from, time.min, tzinfo=UTC))
    if date_to is not None:
        stmt = stmt.where(Match.kickoff_utc <= datetime.combine(date_to, time.max, tzinfo=UTC))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Home.name).like(like), func.lower(Away.name).like(like)))

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    order_col = Match.kickoff_utc.asc() if order == "kickoff_asc" else Match.kickoff_utc.desc()
    rows = db.execute(stmt.order_by(order_col).offset(pagination.offset).limit(pagination.limit)).all()
    return {
        "total": total,
        "limit": pagination.limit,
        "offset": pagination.offset,
        "matches": [_serialize(m, h, a, lg, se) for m, h, a, lg, se in rows],
    }


@router.get("/matches/{match_id}")
def match_detail(match_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.execute(
        select(Match, Team.name, League.name, Season.label)
        .join(Team, Match.home_team_id == Team.id)
        .join(League, Match.league_id == League.id)
        .outerjoin(Season, Match.season_id == Season.id)
        .where(Match.id == match_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="match not found")
    match, home_name, league_name, season_label = row
    away_name = db.get(Team, match.away_team_id).name
    stats = list(
        db.execute(select(MatchStat).where(MatchStat.match_id == match_id)).scalars()
    )
    odds = list(db.execute(select(MatchOdd).where(MatchOdd.match_id == match_id)).scalars())
    payload = _serialize(match, home_name, away_name, league_name, season_label)
    payload["stats"] = [
        {
            "team_id": s.team_id,
            "is_home": s.is_home,
            "xg": s.xg,
            "shots": s.shots,
            "shots_on_target": s.shots_on_target,
            "corners": s.corners,
            "fouls": s.fouls,
            "yellow_cards": s.yellow_cards,
            "red_cards": s.red_cards,
            "possession": s.possession,
            "source_provider": s.source_provider,
        }
        for s in stats
    ]
    payload["odds"] = [
        {
            "bookmaker": o.bookmaker,
            "market": o.market,
            "selection": o.selection,
            "decimal_odds": o.decimal_odds,
            "is_closing": o.is_closing,
            "line": o.line,
            "source_provider": o.source_provider,
            "captured_at": o.captured_at.isoformat() if o.captured_at else None,
        }
        for o in odds
    ]
    return payload


@router.post("/matches/manual", status_code=201)
def create_manual_match(payload: ManualMatchInput, db: Session = Depends(get_db)) -> dict:
    """Create a match (and optional stats/odds) by hand, for manual analysis."""
    match_id = import_manual_match(db, payload.model_dump())
    return {"match_id": match_id, "detail": "manual match stored"}
