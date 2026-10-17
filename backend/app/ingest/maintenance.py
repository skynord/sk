"""Data-hygiene pass: merge club rows and duplicate fixtures.

The same real club reaches us from two providers as two ``Team`` rows (an
openfootball ``"FC Barcelona"`` and an api_football ``"Barcelona"``), and its
2024/25 fixtures are stored twice, once per provider.  That splits one club's
history in two, so its model strengths are estimated from half the evidence, and
the expected-goals (xG) statistics attached to one provider's fixtures never
reach the club's form window.

This module finds those duplicates and can merge them (``apply=False`` plans,
it does not write).  Identity is decided only by names we can see -- a club key
that ignores provider decoration words, scoped to one competition -- so nothing
is invented.  When two clusters collide in a way the merge cannot satisfy, the
pass stops rather than guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.analysis.identity import team_merge_key
from app.db.models import (
    BacktestResult,
    CalibrationBin,
    League,
    Match,
    MatchOdd,
    MatchStat,
    ModelRun,
    Prediction,
    ProviderRecord,
    Season,
    Team,
)
from app.timezones import competition_tz

#: Stat columns copied across when two rows describe the same (match, team).
_STAT_FIELDS = (
    "goals",
    "xg",
    "shots",
    "shots_on_target",
    "big_chances",
    "possession",
    "passes",
    "pass_accuracy",
    "corners",
    "fouls",
    "yellow_cards",
    "red_cards",
)

#: Match columns copied across when two rows describe the same fixture.
_MATCH_FIELDS = (
    "home_goals",
    "away_goals",
    "ht_home_goals",
    "ht_away_goals",
    "venue",
    "referee",
    "round_label",
)


@dataclass
class TeamMerge:
    league_id: int | None
    keeper_id: int
    keeper_name: str
    duplicate_ids: list[int]
    duplicate_names: list[str]
    matches_moved: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "league_id": self.league_id,
            "keeper_id": self.keeper_id,
            "keeper_name": self.keeper_name,
            "duplicate_ids": self.duplicate_ids,
            "duplicate_names": self.duplicate_names,
            "matches_moved": self.matches_moved,
        }


@dataclass
class MatchMerge:
    league_id: int
    keeper_id: int
    duplicate_ids: list[int]
    kickoff_date: str
    home: str
    away: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "league_id": self.league_id,
            "keeper_id": self.keeper_id,
            "duplicate_ids": self.duplicate_ids,
            "kickoff_date": self.kickoff_date,
            "home": self.home,
            "away": self.away,
        }


@dataclass
class DedupeReport:
    team_merges: list[TeamMerge] = field(default_factory=list)
    match_merges: list[MatchMerge] = field(default_factory=list)
    teams_removed: int = 0
    matches_removed: int = 0
    stats_moved: int = 0
    odds_moved: int = 0
    applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "teams_removed": self.teams_removed,
            "matches_removed": self.matches_removed,
            "stats_moved": self.stats_moved,
            "odds_moved": self.odds_moved,
            "team_merges": [m.to_dict() for m in self.team_merges],
            "match_merges": [m.to_dict() for m in self.match_merges],
        }


def _team_match_counts(db: Session) -> dict[int, int]:
    counts: dict[int, int] = {}
    home = db.execute(
        select(Match.home_team_id, func.count()).group_by(Match.home_team_id)
    ).all()
    away = db.execute(
        select(Match.away_team_id, func.count()).group_by(Match.away_team_id)
    ).all()
    for team_id, n in (*home, *away):
        counts[team_id] = counts.get(team_id, 0) + int(n)
    return counts


def plan_team_merges(db: Session) -> list[TeamMerge]:
    """Clusters of ``Team`` rows that are the same club, scoped per competition."""
    counts = _team_match_counts(db)
    teams = list(db.execute(select(Team)).scalars())
    clusters: dict[tuple[int | None, tuple[str, ...]], list[Team]] = {}
    for team in teams:
        key = team_merge_key(team.name)
        if not key:
            continue
        clusters.setdefault((team.league_id, key), []).append(team)

    merges: list[TeamMerge] = []
    for (league_id, _key), members in clusters.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda t: (-counts.get(t.id, 0), t.id))
        keeper = members[0]
        duplicates = members[1:]
        merges.append(
            TeamMerge(
                league_id=league_id,
                keeper_id=keeper.id,
                keeper_name=keeper.name,
                duplicate_ids=[t.id for t in duplicates],
                duplicate_names=[t.name for t in duplicates],
                matches_moved=sum(counts.get(t.id, 0) for t in duplicates),
            )
        )
    merges.sort(key=lambda m: -m.matches_moved)
    return merges


def _richness(match: Match, stats: int, odds: int) -> int:
    score = 0
    for value in (
        match.home_goals,
        match.away_goals,
        match.ht_home_goals,
        match.ht_away_goals,
        match.venue,
        match.referee,
    ):
        if value is not None:
            score += 1
    return score + 2 * stats + 2 * odds


def plan_match_merges(db: Session) -> list[MatchMerge]:
    """Duplicate fixtures: same competition, clubs and calendar day."""
    team_names = {t.id: t.name for t in db.execute(select(Team)).scalars()}

    stats_counts = dict(
        db.execute(select(MatchStat.match_id, func.count()).group_by(MatchStat.match_id)).all()
    )
    odds_counts = dict(
        db.execute(select(MatchOdd.match_id, func.count()).group_by(MatchOdd.match_id)).all()
    )

    all_matches = list(db.execute(select(Match)).scalars())

    clusters: dict[tuple[int, tuple[str, ...], tuple[str, ...], date], list[Match]] = {}
    for match in all_matches:
        home = team_names.get(match.home_team_id)
        away = team_names.get(match.away_team_id)
        if home is None or away is None:
            continue
        hk, ak = team_merge_key(home), team_merge_key(away)
        if not hk or not ak:
            continue
        day = match.kickoff_utc.date()
        clusters.setdefault((match.league_id, hk, ak, day), []).append(match)

    merges: list[MatchMerge] = []
    for (league_id, _hk, _ak, day), members in clusters.items():
        if len(members) < 2:
            continue
        members.sort(
            key=lambda m: (
                -_richness(m, stats_counts.get(m.id, 0), odds_counts.get(m.id, 0)),
                m.id,
            )
        )
        keeper = members[0]
        duplicates = members[1:]
        merges.append(
            MatchMerge(
                league_id=league_id,
                keeper_id=keeper.id,
                duplicate_ids=[m.id for m in duplicates],
                kickoff_date=day.isoformat(),
                home=team_names[keeper.home_team_id],
                away=team_names[keeper.away_team_id],
            )
        )
    merges.sort(key=lambda m: (m.kickoff_date, m.keeper_id))
    return merges


def _repoint_provider_records(db: Session, entity_type: str, src: int, dst: int) -> None:
    # Flush pending repoints first: when several duplicates feed one keeper, an
    # earlier call's ``entity_id = dst`` change must be visible to this call's
    # clash check, or two rows for the same provider land on the keeper and trip
    # the ``(provider, entity_type, entity_id)`` unique constraint.
    db.flush()
    rows = list(
        db.execute(
            select(ProviderRecord).where(
                ProviderRecord.entity_type == entity_type,
                ProviderRecord.entity_id == src,
            )
        ).scalars()
    )
    for row in rows:
        clash = db.execute(
            select(ProviderRecord.id).where(
                ProviderRecord.provider == row.provider,
                ProviderRecord.entity_type == row.entity_type,
                ProviderRecord.entity_id == dst,
            )
        ).first()
        if clash:
            db.delete(row)
        else:
            row.entity_id = dst


def _merge_stat_into(keeper: MatchStat, duplicate: MatchStat) -> None:
    for name in _STAT_FIELDS:
        if getattr(keeper, name) is None and getattr(duplicate, name) is not None:
            setattr(keeper, name, getattr(duplicate, name))


def _merge_match_into(keeper: Match, duplicate: Match) -> None:
    for name in _MATCH_FIELDS:
        if getattr(keeper, name) is None and getattr(duplicate, name) is not None:
            setattr(keeper, name, getattr(duplicate, name))
    if keeper.status != "finished" and duplicate.status == "finished":
        keeper.status = duplicate.status


def apply_match_merges(db: Session, merges: list[MatchMerge], report: DedupeReport) -> None:
    for merge in merges:
        keeper = db.get(Match, merge.keeper_id)
        if keeper is None:
            continue
        for dup_id in merge.duplicate_ids:
            duplicate = db.get(Match, dup_id)
            if duplicate is None:
                continue
            for stat in list(duplicate.stats):
                clash = db.execute(
                    select(MatchStat).where(
                        MatchStat.match_id == keeper.id,
                        MatchStat.team_id == stat.team_id,
                    )
                ).scalar_one_or_none()
                if clash is not None:
                    _merge_stat_into(clash, stat)
                    db.delete(stat)
                else:
                    # Move through the relationship, not the FK column: a raw
                    # ``stat.match_id = keeper.id`` leaves the stale collection
                    # and SQLAlchemy nulls the FK when the duplicate is deleted.
                    stat.match = keeper
                    report.stats_moved += 1
            for odd in list(duplicate.odds):
                clash = db.execute(
                    select(MatchOdd).where(
                        MatchOdd.match_id == keeper.id,
                        MatchOdd.bookmaker == odd.bookmaker,
                        MatchOdd.market == odd.market,
                        MatchOdd.selection == odd.selection,
                        MatchOdd.is_closing == odd.is_closing,
                    )
                ).scalar_one_or_none()
                if clash is not None:
                    db.delete(odd)
                else:
                    odd.match = keeper
                    report.odds_moved += 1
            db.execute(
                update(Prediction)
                .where(Prediction.match_id == duplicate.id)
                .values(match_id=keeper.id)
            )
            _repoint_provider_records(db, "match", duplicate.id, keeper.id)
            _merge_match_into(keeper, duplicate)
            db.delete(duplicate)
            report.matches_removed += 1


def apply_team_merges(db: Session, merges: list[TeamMerge], report: DedupeReport) -> None:
    for merge in merges:
        keeper = db.get(Team, merge.keeper_id)
        if keeper is None:
            continue
        for dup_id in merge.duplicate_ids:
            duplicate = db.get(Team, dup_id)
            if duplicate is None:
                continue
            for match in db.execute(
                select(Match).where(Match.home_team_id == duplicate.id)
            ).scalars():
                match.home_team_id = keeper.id
            for match in db.execute(
                select(Match).where(Match.away_team_id == duplicate.id)
            ).scalars():
                match.away_team_id = keeper.id
            for stat in db.execute(
                select(MatchStat).where(MatchStat.team_id == duplicate.id)
            ).scalars():
                clash = db.execute(
                    select(MatchStat).where(
                        MatchStat.match_id == stat.match_id,
                        MatchStat.team_id == keeper.id,
                    )
                ).scalar_one_or_none()
                if clash is not None:
                    _merge_stat_into(clash, stat)
                    db.delete(stat)
                else:
                    stat.team_id = keeper.id
            keeper.provider_refs = {**(duplicate.provider_refs or {}), **(keeper.provider_refs or {})}
            _repoint_provider_records(db, "team", duplicate.id, keeper.id)
            db.delete(duplicate)
            report.teams_removed += 1


def run_dedupe(db: Session, *, apply: bool = False) -> DedupeReport:
    """Plan (and optionally apply) the team and match merges.

    Matches are merged first so the club merge cannot trip the fixture unique
    constraint; the club plan is then computed on the already-merged state.  The
    caller owns the transaction (and should have backed the database up).
    """
    report = DedupeReport(applied=apply)

    match_merges = plan_match_merges(db)
    report.match_merges = match_merges
    if apply:
        apply_match_merges(db, match_merges, report)
        db.flush()

    team_merges = plan_team_merges(db)
    report.team_merges = team_merges
    if apply:
        apply_team_merges(db, team_merges, report)
        db.flush()

    return report


@dataclass
class LeagueRemovalReport:
    """What a ``remove_league`` pass deletes, per competition."""

    leagues: list[dict[str, Any]] = field(default_factory=list)
    matches_removed: int = 0
    teams_removed: int = 0
    seasons_removed: int = 0
    stats_removed: int = 0
    odds_removed: int = 0
    predictions_removed: int = 0
    provider_records_removed: int = 0
    model_runs_removed: int = 0
    backtests_removed: int = 0
    applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "leagues": self.leagues,
            "matches_removed": self.matches_removed,
            "teams_removed": self.teams_removed,
            "seasons_removed": self.seasons_removed,
            "stats_removed": self.stats_removed,
            "odds_removed": self.odds_removed,
            "predictions_removed": self.predictions_removed,
            "provider_records_removed": self.provider_records_removed,
            "model_runs_removed": self.model_runs_removed,
            "backtests_removed": self.backtests_removed,
        }


def remove_league(db: Session, league_ids: list[int], *, apply: bool = False) -> LeagueRemovalReport:
    """Delete one or more competitions and every row that belongs to them.

    A competition that should not be in the store (a stray ingest, a league we
    decided not to support) is removed whole: its fixtures, the statistics and
    odds attached to them, their provenance, its seasons and model runs, and the
    clubs that have no fixture left anywhere.  A club still appearing in another
    competition is kept -- only genuinely orphaned rows go.  The caller owns the
    transaction (and should have backed the database up first).
    """
    report = LeagueRemovalReport(applied=apply)
    ids = sorted(set(league_ids))
    leagues = list(db.execute(select(League).where(League.id.in_(ids))).scalars())
    report.leagues = [{"id": lg.id, "name": lg.name, "slug": lg.slug} for lg in leagues]
    found = [lg.id for lg in leagues]
    if not found:
        return report

    match_ids = [
        int(mid)
        for (mid,) in db.execute(select(Match.id).where(Match.league_id.in_(found)))
    ]
    season_ids = [
        int(sid)
        for (sid,) in db.execute(select(Season.id).where(Season.league_id.in_(found)))
    ]
    run_conditions = [ModelRun.league_id.in_(found)]
    if season_ids:
        run_conditions.append(ModelRun.season_id.in_(season_ids))
    run_ids = [
        int(rid)
        for (rid,) in db.execute(select(ModelRun.id).where(or_(*run_conditions)))
    ]
    if not apply:
        report.matches_removed = len(match_ids)
        report.seasons_removed = len(season_ids)
        report.model_runs_removed = len(run_ids)
        candidate_ids = [
            int(tid)
            for (tid,) in db.execute(select(Team.id).where(Team.league_id.in_(found)))
        ]
        if candidate_ids:
            referenced = {
                int(tid)
                for (tid,) in db.execute(
                    select(Match.home_team_id).where(
                        Match.home_team_id.in_(candidate_ids), Match.league_id.notin_(found)
                    )
                )
            } | {
                int(tid)
                for (tid,) in db.execute(
                    select(Match.away_team_id).where(
                        Match.away_team_id.in_(candidate_ids), Match.league_id.notin_(found)
                    )
                )
            }
            report.teams_removed = len([tid for tid in candidate_ids if tid not in referenced])
        return report

    # Backtests and their calibration bins hang off model runs.
    bt_conditions = [BacktestResult.league_id.in_(found)]
    if run_ids:
        bt_conditions.append(BacktestResult.model_run_id.in_(run_ids))
    backtest_ids = [
        int(bid)
        for (bid,) in db.execute(select(BacktestResult.id).where(or_(*bt_conditions)))
    ]
    if backtest_ids:
        db.execute(
            delete(CalibrationBin).where(CalibrationBin.backtest_id.in_(backtest_ids))
        )
        report.backtests_removed = int(
            db.execute(delete(BacktestResult).where(BacktestResult.id.in_(backtest_ids))).rowcount
            or 0
        )

    # Predictions reference both a match and a model run; clear them first.
    pred_where = Prediction.match_id.in_(match_ids) if match_ids else None
    if run_ids:
        run_clause = Prediction.model_run_id.in_(run_ids)
        pred_where = run_clause if pred_where is None else (pred_where | run_clause)
    if pred_where is not None:
        report.predictions_removed = int(db.execute(delete(Prediction).where(pred_where)).rowcount or 0)

    if run_ids:
        report.model_runs_removed = int(
            db.execute(delete(ModelRun).where(ModelRun.id.in_(run_ids))).rowcount or 0
        )

    if match_ids:
        report.stats_removed = int(
            db.execute(delete(MatchStat).where(MatchStat.match_id.in_(match_ids))).rowcount or 0
        )
        report.odds_removed = int(
            db.execute(delete(MatchOdd).where(MatchOdd.match_id.in_(match_ids))).rowcount or 0
        )
        report.provider_records_removed += int(
            db.execute(
                delete(ProviderRecord).where(
                    ProviderRecord.entity_type == "match",
                    ProviderRecord.entity_id.in_(match_ids),
                )
            ).rowcount
            or 0
        )
        report.matches_removed = int(
            db.execute(delete(Match).where(Match.id.in_(match_ids))).rowcount or 0
        )

    if season_ids:
        report.provider_records_removed += int(
            db.execute(
                delete(ProviderRecord).where(
                    ProviderRecord.entity_type == "season",
                    ProviderRecord.entity_id.in_(season_ids),
                )
            ).rowcount
            or 0
        )
        report.seasons_removed = int(
            db.execute(delete(Season).where(Season.id.in_(season_ids))).rowcount or 0
        )

    # Clubs owned by the removed competitions that no longer appear in any
    # fixture.  A club that still has a fixture elsewhere is left untouched.
    candidate_ids = [
        int(tid)
        for (tid,) in db.execute(select(Team.id).where(Team.league_id.in_(found)))
    ]
    if candidate_ids:
        referenced = {
            int(tid)
            for (tid,) in db.execute(
                select(Match.home_team_id).where(Match.home_team_id.in_(candidate_ids))
            )
        } | {
            int(tid)
            for (tid,) in db.execute(
                select(Match.away_team_id).where(Match.away_team_id.in_(candidate_ids))
            )
        }
        orphan_ids = [tid for tid in candidate_ids if tid not in referenced]
        if orphan_ids:
            db.execute(
                delete(ProviderRecord).where(
                    ProviderRecord.entity_type == "team",
                    ProviderRecord.entity_id.in_(orphan_ids),
                )
            )
            report.teams_removed = int(
                db.execute(delete(Team).where(Team.id.in_(orphan_ids))).rowcount or 0
            )
        # A club kept because it still plays elsewhere must not be left pointing
        # at the competition we just deleted: repoint it at a league it still
        # appears in (or clear it if somehow none).
        for tid in (t for t in candidate_ids if t in referenced):
            new_league = db.execute(
                select(Match.league_id)
                .where(or_(Match.home_team_id == tid, Match.away_team_id == tid))
                .limit(1)
            ).scalar()
            db.execute(update(Team).where(Team.id == tid).values(league_id=new_league))

    report.provider_records_removed += int(
        db.execute(
            delete(ProviderRecord).where(
                ProviderRecord.entity_type == "league",
                ProviderRecord.entity_id.in_(found),
            )
        ).rowcount
        or 0
    )
    db.execute(delete(League).where(League.id.in_(found)))
    return report


#: Providers that hand us the venue's local wall-clock kick-off time.  Rows
#: from these providers, ingested before conversion existed, hold that local
#: time in ``kickoff_utc`` and need re-interpreting.  football-data.co.uk is
#: absent because its historical rows carry no time of day at all (only a
#: date); re-importing the CSVs repairs those.
LOCAL_TIME_PROVIDERS = ("openfootball",)


@dataclass
class KickoffFixReport:
    """What a ``fix_kickoff_utc`` pass reinterprets, per provider."""

    candidates: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_unknown_venue: int = 0
    by_provider: dict[str, int] = field(default_factory=dict)
    samples: list[dict[str, Any]] = field(default_factory=list)
    applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "candidates": self.candidates,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "skipped_unknown_venue": self.skipped_unknown_venue,
            "by_provider": self.by_provider,
            "samples": self.samples,
        }


def fix_kickoff_utc(
    db: Session,
    *,
    apply: bool = False,
    providers: tuple[str, ...] = LOCAL_TIME_PROVIDERS,
) -> KickoffFixReport:
    """Reinterpret venue-local kick-off times from selected providers as UTC.

    Some providers publish the local wall-clock time of a fixture (openfootball
    and football-data.co.uk; the cross-border UEFA competitions are published
    in Central European Time).  Rows ingested before that was handled stored the
    local time as if it were UTC, which shifts every kick-off by the
    competition's offset.  This pass reads each row's stored value back in the
    competition's timezone and rewrites it as true UTC.

    Rows whose competition cannot be placed are left untouched and counted.
    Dry-run by default; the caller owns the transaction and should back the
    database up before applying.
    """
    report = KickoffFixReport(applied=apply)
    league_country = {lg.id: lg.country for lg in db.execute(select(League)).scalars()}

    rows = db.execute(
        select(Match).where(Match.source_provider.in_(providers)).order_by(Match.kickoff_utc)
    ).scalars()
    for match in rows:
        report.candidates += 1
        country = league_country.get(match.league_id)
        tz = competition_tz(country)
        if tz is None:
            report.skipped_unknown_venue += 1
            continue
        current = match.kickoff_utc
        if current.tzinfo is not None:
            current = current.replace(tzinfo=None)
        corrected = current.replace(tzinfo=tz).astimezone(UTC)
        if corrected.replace(tzinfo=None) == current:
            report.unchanged += 1
            continue
        report.updated += 1
        report.by_provider[match.source_provider] = (
            report.by_provider.get(match.source_provider, 0) + 1
        )
        if len(report.samples) < 8:
            report.samples.append(
                {
                    "id": match.id,
                    "provider": match.source_provider,
                    "competition_country": country,
                    "timezone": str(tz),
                    "before": current.isoformat(),
                    "after": corrected.replace(tzinfo=None).isoformat(),
                }
            )
        if apply:
            match.kickoff_utc = corrected

    if apply:
        db.flush()
    return report
