"""Team form, splits, head-to-head and scheduling features.

Everything here is computed from stored matches.  When a metric cannot be
computed - too few matches, no first-half scores recorded, no xG data - the
field is ``None`` and a note is added rather than a guessed value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Match, MatchStat, Team

FINISHED = "finished"


@dataclass
class MatchSummary:
    match_id: int
    date: datetime
    is_home: bool
    opponent_id: int
    opponent_name: str
    goals_for: int
    goals_against: int
    ht_for: int | None = None
    ht_against: int | None = None
    xg_for: float | None = None
    xg_against: float | None = None
    shots_for: int | None = None
    shots_on_target_for: int | None = None
    corners_for: int | None = None
    cards_for: int | None = None

    @property
    def result(self) -> str:
        if self.goals_for > self.goals_against:
            return "W"
        if self.goals_for < self.goals_against:
            return "L"
        return "D"

    @property
    def total_goals(self) -> int:
        return self.goals_for + self.goals_against


@dataclass
class FormStats:
    n_matches: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    avg_goals_for: float | None
    avg_goals_against: float | None
    clean_sheets: int
    failed_to_score: int
    btts_frequency: float | None
    over_under: dict[str, float] = field(default_factory=dict)
    first_half_goals_for: int | None = None
    second_half_goals_for: int | None = None
    points_per_match: float | None = None
    notes: list[str] = field(default_factory=list)


def _rows_for_team(
    db: Session, team_id: int, before: datetime, limit: int | None = None
) -> list[MatchSummary]:
    stmt = (
        select(Match)
        .where(
            Match.status == FINISHED,
            Match.kickoff_utc < before,
            or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
        )
        .order_by(Match.kickoff_utc.desc())
    )
    if limit:
        stmt = stmt.limit(limit)
    matches = list(db.execute(stmt).scalars())
    summaries: list[MatchSummary] = []
    team_names = {
        t.id: t.name for t in db.execute(select(Team)).scalars()
    }
    stat_map: dict[int, dict[str, MatchStat]] = {}
    if matches:
        stat_rows = db.execute(
            select(MatchStat).where(MatchStat.match_id.in_([m.id for m in matches]))
        ).scalars()
        for s in stat_rows:
            stat_map.setdefault(s.match_id, {})[s.team_id] = s

    for m in matches:
        is_home = m.home_team_id == team_id
        opponent_id = m.away_team_id if is_home else m.home_team_id
        gf = m.home_goals if is_home else m.away_goals
        ga = m.away_goals if is_home else m.home_goals
        if gf is None or ga is None:
            continue
        ht_for = (m.ht_home_goals if is_home else m.ht_away_goals)
        ht_against = (m.ht_away_goals if is_home else m.ht_home_goals)
        stats = stat_map.get(m.id, {})
        own = stats.get(team_id)
        opp = stats.get(opponent_id)
        summaries.append(
            MatchSummary(
                match_id=m.id,
                date=m.kickoff_utc,
                is_home=is_home,
                opponent_id=opponent_id,
                opponent_name=team_names.get(opponent_id, f"Team {opponent_id}"),
                goals_for=int(gf),
                goals_against=int(ga),
                ht_for=int(ht_for) if ht_for is not None else None,
                ht_against=int(ht_against) if ht_against is not None else None,
                xg_for=own.xg if own else None,
                xg_against=opp.xg if opp else None,
                shots_for=own.shots if own else None,
                shots_on_target_for=own.shots_on_target if own else None,
                corners_for=own.corners if own else None,
                cards_for=(own.yellow_cards or 0) + (own.red_cards or 0) if own else None,
            )
        )
    return summaries


def form_summary(matches: list[MatchSummary]) -> FormStats:
    notes: list[str] = []
    n = len(matches)
    if n == 0:
        return FormStats(0, 0, 0, 0, 0, 0, None, None, 0, 0, None, notes=["no recent matches"])
    wins = sum(1 for m in matches if m.result == "W")
    draws = sum(1 for m in matches if m.result == "D")
    losses = sum(1 for m in matches if m.result == "L")
    gf = sum(m.goals_for for m in matches)
    ga = sum(m.goals_against for m in matches)
    clean = sum(1 for m in matches if m.goals_against == 0)
    fts = sum(1 for m in matches if m.goals_for == 0)
    btts = sum(1 for m in matches if m.goals_for >= 1 and m.goals_against >= 1)
    ou: dict[str, float] = {}
    for line in (1.5, 2.5, 3.5, 4.5):
        ou[f"{line}"] = sum(1 for m in matches if m.total_goals > line) / n
    with_ht = [m for m in matches if m.ht_for is not None]
    fh = sum(m.ht_for for m in with_ht) if with_ht else None
    sh = (
        sum(m.goals_for - (m.ht_for or 0) for m in with_ht) if with_ht else None
    )
    if not with_ht:
        notes.append("no half-time scores recorded: first/second-half split unavailable")
    points = (wins * 3 + draws)
    return FormStats(
        n_matches=n,
        wins=wins,
        draws=draws,
        losses=losses,
        goals_for=gf,
        goals_against=ga,
        avg_goals_for=gf / n,
        avg_goals_against=ga / n,
        clean_sheets=clean,
        failed_to_score=fts,
        btts_frequency=btts / n,
        over_under=ou,
        first_half_goals_for=fh,
        second_half_goals_for=sh,
        points_per_match=points / n,
        notes=notes,
    )


def recent_form(db: Session, team_id: int, before: datetime, window: int = 5) -> list[MatchSummary]:
    return _rows_for_team(db, team_id, before, limit=window)


def split_home_away(matches: list[MatchSummary]) -> dict[str, FormStats]:
    return {
        "home": form_summary([m for m in matches if m.is_home]),
        "away": form_summary([m for m in matches if not m.is_home]),
        "overall": form_summary(matches),
    }


def aggregate_xg(matches: list[MatchSummary]) -> dict[str, float | int | None]:
    xg_for = [m.xg_for for m in matches if m.xg_for is not None]
    xg_against = [m.xg_against for m in matches if m.xg_against is not None]
    return {
        "n_with_xg": len(xg_for),
        "xg_for_avg": (sum(xg_for) / len(xg_for)) if xg_for else None,
        "xg_against_avg": (sum(xg_against) / len(xg_against)) if xg_against else None,
    }


def head_to_head(
    db: Session, home_team_id: int, away_team_id: int, before: datetime, limit: int = 10
) -> list[MatchSummary]:
    stmt = (
        select(Match)
        .where(
            Match.status == FINISHED,
            Match.kickoff_utc < before,
            or_(
                (Match.home_team_id == home_team_id) & (Match.away_team_id == away_team_id),
                (Match.home_team_id == away_team_id) & (Match.away_team_id == home_team_id),
            ),
        )
        .order_by(Match.kickoff_utc.desc())
        .limit(limit)
    )
    matches = list(db.execute(stmt).scalars())
    team_names = {t.id: t.name for t in db.execute(select(Team)).scalars()}
    rows: list[MatchSummary] = []
    for m in matches:
        is_home = m.home_team_id == home_team_id
        gf = m.home_goals if is_home else m.away_goals
        ga = m.away_goals if is_home else m.home_goals
        if gf is None or ga is None:
            continue
        rows.append(
            MatchSummary(
                match_id=m.id,
                date=m.kickoff_utc,
                is_home=is_home,
                opponent_id=away_team_id,
                opponent_name=team_names.get(away_team_id, ""),
                goals_for=int(gf),
                goals_against=int(ga),
            )
        )
    return rows


def rest_and_congestion(db: Session, team_id: int, kickoff: datetime) -> dict[str, int | None]:
    last = db.execute(
        select(Match)
        .where(
            Match.status == FINISHED,
            Match.kickoff_utc < kickoff,
            or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
        )
        .order_by(Match.kickoff_utc.desc())
        .limit(1)
    ).scalars().first()
    rest_days = None
    if last is not None:
        kickoff_aware = kickoff if kickoff.tzinfo else kickoff.replace(tzinfo=UTC)
        last_aware = last.kickoff_utc if last.kickoff_utc.tzinfo else last.kickoff_utc.replace(tzinfo=UTC)
        rest_days = max((kickoff_aware - last_aware).days, 0)
    window_start = kickoff - timedelta(days=14)
    congestion = db.execute(
        select(Match).where(
            Match.status == FINISHED,
            Match.kickoff_utc >= window_start,
            Match.kickoff_utc < kickoff,
            or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
        )
    ).scalars().all()
    return {"rest_days": rest_days, "matches_last_14_days": len(congestion)}
