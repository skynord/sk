"""Ingestion service: provider DTOs -> database, with provenance.

Every ingested entity records where it came from and when.  Upserts are
idempotent so re-running an import does not duplicate rows.  Failures are
captured on the :class:`IngestRun` row and reported, never hidden.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    DataSource,
    IngestRun,
    League,
    Match,
    MatchOdd,
    MatchStat,
    ProviderRecord,
    Season,
    Team,
)
from app.logging_config import get_logger
from app.providers import Provider, available_provider_names, build_providers, get_provider
from app.providers.base import ProviderError
from app.providers.dto import NormalizedMatch, NormalizedOdd, NormalizedStat

logger = get_logger(__name__)


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:150] or "unknown"


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


# --------------------------------------------------------------------------
# Data source registry
# --------------------------------------------------------------------------
def seed_data_sources(db: Session) -> None:
    settings = get_settings()
    enabled = set(settings.provider_list)
    for name in available_provider_names():
        try:
            provider = get_provider(name)
        except ProviderError:
            continue
        caps = provider.describe()
        available, reason = provider.is_available()
        row = db.execute(select(DataSource).where(DataSource.name == name)).scalars().first()
        if row is None:
            row = DataSource(name=name)
            db.add(row)
        row.kind = caps.kind
        row.base_url = caps.base_url
        row.requires_key = caps.requires_key
        row.enabled = name in enabled
        if row.enabled or name in enabled:
            row.status = "available" if available else "unavailable"
        else:
            row.status = "disabled"
        row.last_checked_at = _utcnow()
        row.message = reason if (row.enabled or name in enabled) else "not enabled"
        row.license_note = caps.license_note
        row.cost_note = caps.cost_note
    db.commit()


# --------------------------------------------------------------------------
# Upsert helpers
# --------------------------------------------------------------------------
def _provenance(
    db: Session,
    provider: str,
    entity_type: str,
    entity_id: int,
    *,
    source_url: str | None,
    competition: str | None,
    season: str | None,
    is_estimate: bool = False,
    checksum: str | None = None,
) -> None:
    row = db.execute(
        select(ProviderRecord).where(
            ProviderRecord.provider == provider,
            ProviderRecord.entity_type == entity_type,
            ProviderRecord.entity_id == entity_id,
        )
    ).scalars().first()
    if row is None:
        row = ProviderRecord(provider=provider, entity_type=entity_type, entity_id=entity_id)
        db.add(row)
        # autoflush is off, so a sibling call for the same entity later in this
        # run wouldn't see this pending row and would insert a duplicate that
        # blows up the unique constraint at the next commit. Flush it now.
        db.flush()
    row.source_url = source_url
    row.competition = competition
    row.season = season
    row.retrieved_at = _utcnow()
    row.data_as_of = _utcnow()
    row.is_estimate = is_estimate
    if checksum:
        row.checksum = checksum


def _upsert_league(db: Session, provider: str, match: NormalizedMatch, is_estimate: bool) -> League:
    slug = slugify(f"{match.country or 'intl'}-{match.league_name}")
    league = db.execute(select(League).where(League.slug == slug)).scalars().first()
    if league is None:
        league = League(slug=slug, name=match.league_name, country=match.country)
        db.add(league)
        db.flush()
    league.name = match.league_name
    league.country = league.country or match.country
    refs = dict(league.provider_refs or {})
    refs[provider] = match.league_name
    league.provider_refs = refs
    _provenance(db, provider, "league", league.id, source_url=match.source_url,
                competition=match.league_name, season=match.season_label, is_estimate=is_estimate)
    return league


def _upsert_season(db: Session, league: League, label: str | None) -> Season | None:
    if not label:
        return None
    season = db.execute(
        select(Season).where(Season.league_id == league.id, Season.label == label)
    ).scalars().first()
    if season is None:
        season = Season(league_id=league.id, label=label)
        db.add(season)
        db.flush()
    return season


def _upsert_team(db: Session, provider: str, name: str, country: str | None, league: League,
                 is_estimate: bool, source_url: str | None) -> Team:
    slug = slugify(name)
    team = db.execute(select(Team).where(Team.slug == slug)).scalars().first()
    if team is None:
        team = Team(slug=slug, name=name, country=country, league_id=league.id)
        db.add(team)
        db.flush()
    refs = dict(team.provider_refs or {})
    refs[provider] = name
    team.provider_refs = refs
    if team.league_id is None:
        team.league_id = league.id
    _provenance(db, provider, "team", team.id, source_url=source_url, competition=league.name,
                season=None, is_estimate=is_estimate)
    return team


def _match_checksum(match: NormalizedMatch) -> str:
    payload = f"{match.provider_id}|{match.status}|{match.home_goals}|{match.away_goals}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _upsert_match(db: Session, provider: str, dto: NormalizedMatch, league: League,
                  season: Season | None, home: Team, away: Team, is_estimate: bool) -> Match:
    match = db.execute(
        select(Match).where(Match.source_provider == provider, Match.source_ref == dto.provider_id)
    ).scalars().first()
    if match is None:
        match = Match(
            league_id=league.id,
            season_id=season.id if season else None,
            home_team_id=home.id,
            away_team_id=away.id,
            kickoff_utc=dto.kickoff_utc,
            source_provider=provider,
            source_ref=dto.provider_id,
        )
        db.add(match)
        db.flush()
    match.league_id = league.id
    match.season_id = season.id if season else match.season_id
    match.home_team_id = home.id
    match.away_team_id = away.id
    match.kickoff_utc = dto.kickoff_utc
    match.status = dto.status
    match.round_label = dto.round_label
    match.venue = dto.venue
    match.home_goals = dto.home_goals
    match.away_goals = dto.away_goals
    match.ht_home_goals = dto.ht_home_goals
    match.ht_away_goals = dto.ht_away_goals
    _provenance(db, provider, "match", match.id, source_url=dto.source_url,
                competition=league.name, season=dto.season_label, is_estimate=is_estimate,
                checksum=_match_checksum(dto))
    return match


def _upsert_stat(db: Session, provider: str, match: Match, stat: NormalizedStat,
                 team: Team, is_estimate: bool) -> None:
    row = db.execute(
        select(MatchStat).where(MatchStat.match_id == match.id, MatchStat.team_id == team.id)
    ).scalars().first()
    if row is None:
        row = MatchStat(match_id=match.id, team_id=team.id)
        db.add(row)
    row.is_home = stat.is_home
    row.goals = match.home_goals if stat.is_home else match.away_goals
    metrics = stat.metrics
    row.shots = metrics.get("shots")
    row.shots_on_target = metrics.get("shots_on_target")
    row.big_chances = metrics.get("big_chances")
    row.possession = metrics.get("possession")
    row.passes = metrics.get("passes")
    row.pass_accuracy = metrics.get("pass_accuracy")
    row.corners = metrics.get("corners")
    row.fouls = metrics.get("fouls")
    row.yellow_cards = metrics.get("yellow_cards")
    row.red_cards = metrics.get("red_cards")
    row.xg = metrics.get("xg")
    row.source_provider = provider


def _upsert_odds(db: Session, provider: str, match: Match, odd: NormalizedOdd,
                 is_estimate: bool) -> None:
    row = db.execute(
        select(MatchOdd).where(
            MatchOdd.match_id == match.id,
            MatchOdd.bookmaker == odd.bookmaker,
            MatchOdd.market == odd.market,
            MatchOdd.selection == odd.selection,
            MatchOdd.is_closing == odd.is_closing,
        )
    ).scalars().first()
    if row is None:
        row = MatchOdd(
            match_id=match.id,
            bookmaker=odd.bookmaker,
            market=odd.market,
            selection=odd.selection,
            is_closing=odd.is_closing,
        )
        db.add(row)
    row.decimal_odds = odd.decimal_odds
    row.line = odd.line
    row.source_provider = provider
    row.captured_at = _utcnow()


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def _details_for(
    provider: Provider, match: NormalizedMatch, include_details: bool
) -> tuple[list[NormalizedStat], list[NormalizedOdd]]:
    """Fetch per-match stats and odds, tolerating a provider that lacks them.

    Stats are only requested for matches that have been played (finished/live);
    odds are requested for any match.  A failure on one match is logged and
    treated as "no data", so a single bad fixture cannot abort an import.
    """
    if not include_details:
        return [], []
    name = provider.describe().name
    stats: list[NormalizedStat] = []
    odds: list[NormalizedOdd] = []
    if match.status in ("finished", "live"):
        try:
            stats = list(provider.fetch_stats(match))
        except ProviderError as exc:
            logger.warning("stats fetch failed", extra={"provider": name,
                                                        "match": match.provider_id, "error": str(exc)})
    try:
        odds = list(provider.fetch_odds(match))
    except ProviderError as exc:
        logger.warning("odds fetch failed", extra={"provider": name,
                                                   "match": match.provider_id, "error": str(exc)})
    return stats, odds


def _store_match(
    db: Session,
    provider: str,
    dto: NormalizedMatch,
    stats: list[NormalizedStat],
    odds: list[NormalizedOdd],
    is_estimate: bool,
    result: dict[str, Any],
) -> Match:
    """Upsert one match together with its stats and odds. Returns the row."""
    league = _upsert_league(db, provider, dto, is_estimate)
    season = _upsert_season(db, league, dto.season_label)
    home = _upsert_team(db, provider, dto.home_name, dto.country, league, is_estimate, dto.source_url)
    away = _upsert_team(db, provider, dto.away_name, dto.country, league, is_estimate, dto.source_url)
    match = _upsert_match(db, provider, dto, league, season, home, away, is_estimate)
    team_by_name = {dto.home_name: home, dto.away_name: away}
    for stat in stats:
        team = team_by_name.get(stat.team_name)
        if team is not None:
            _upsert_stat(db, provider, match, stat, team, is_estimate)
    for odd in odds:
        _upsert_odd_guarded(db, provider, match, odd, is_estimate, result)
    return match


def _dataset_from_provider(
    provider: Provider,
    *,
    include_details: bool = True,
    league_filter: set[tuple[str, str]] | None = None,
    **kwargs: Any,
) -> Iterable[tuple[NormalizedMatch, list[NormalizedStat], list[NormalizedOdd]]]:
    if hasattr(provider, "iter_dataset"):
        yield from provider.iter_dataset()  # type: ignore[attr-defined]
        return
    for match in provider.fetch_matches(**kwargs):
        if league_filter is not None and _league_key(match) not in league_filter:
            continue
        stats, odds = _details_for(provider, match, include_details)
        yield match, stats, odds


def _league_key(match: NormalizedMatch) -> tuple[str, str]:
    return (match.league_name.strip().lower(), (match.country or "").strip().lower())


def ingest_provider(
    db: Session,
    provider_name: str,
    *,
    include_details: bool = True,
    leagues: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    provider = get_provider(provider_name)
    available, reason = provider.is_available()
    scope_parts = [f"{k}={v}" for k, v in kwargs.items()]
    if leagues:
        scope_parts.append("leagues=" + ",".join(leagues))
    run = IngestRun(provider=provider_name, scope=", ".join(scope_parts) or "all")
    db.add(run)
    db.commit()

    league_filter: set[tuple[str, str]] | None = None
    if leagues:
        league_filter = set()
        for spec in leagues:
            name, _, country = spec.partition("|")
            league_filter.add((name.strip().lower(), country.strip().lower()))

    result: dict[str, Any] = {
        "provider": provider_name,
        "available": available,
        "reason": reason,
        "records_in": 0,
        "matches_upserted": 0,
        "status": "running",
        "warnings": [],
    }

    if not available:
        run.status = "unavailable"
        run.finished_at = _utcnow()
        run.message = reason
        db.commit()
        result["status"] = "unavailable"
        return result

    is_estimate = provider_name == "sample"
    if is_estimate:
        result["warnings"].append(
            "SYNTHETIC data: this provider generates made-up results for demos/tests only."
        )

    try:
        for dto, stats, odds in _dataset_from_provider(
            provider, include_details=include_details, league_filter=league_filter, **kwargs
        ):
            result["records_in"] += 1
            _store_match(db, provider_name, dto, stats, odds, is_estimate, result)
            result["matches_upserted"] += 1
            if result["records_in"] % 200 == 0:
                db.commit()
        db.commit()
        run.status = "success"
        run.records_in = result["records_in"]
        run.records_upserted = result["matches_upserted"]
        run.message = "; ".join(result["warnings"]) or "ok"
        result["status"] = "success"
    except ProviderError as exc:
        db.rollback()
        run = db.get(IngestRun, run.id)
        run.status = "error"
        run.message = str(exc)
        run.finished_at = _utcnow()
        db.commit()
        result["status"] = "error"
        result["reason"] = str(exc)
        return result
    finally:
        run = db.get(IngestRun, run.id)
        if run.finished_at is None:
            run.finished_at = _utcnow()
            run.records_in = result["records_in"]
            run.records_upserted = result["matches_upserted"]
            run.message = run.message or "; ".join(result["warnings"]) or "ok"
        db.commit()

    seed_data_sources(db)
    return result


def ingest_fixture(
    db: Session,
    provider_name: str,
    *,
    provider_id: str,
    date: str | None = None,
    include_details: bool = True,
) -> dict[str, Any]:
    """Import a single provider fixture (with stats/odds) and return its id.

    Makes a live or upcoming fixture analysable: the provider is asked for the
    day's fixtures (or the currently in-play set) and only the requested one is
    stored, so the analysis engine can then be run on it.
    """
    provider = get_provider(provider_name)
    available, reason = provider.is_available()
    if not available:
        raise ProviderError(reason)

    candidates: list[NormalizedMatch] = []
    if date:
        candidates.extend(provider.fetch_matches(date=date))
    else:
        try:
            candidates.extend(provider.fetch_matches(live=True))
        except (ProviderError, TypeError):
            pass
        candidates.extend(provider.fetch_matches(date=datetime.now(tz=UTC).date().isoformat()))

    dto = next((m for m in candidates if m.provider_id == provider_id), None)
    if dto is None:
        raise ProviderError(
            f"{provider_name} has no fixture {provider_id} for {date or 'today'}"
        )

    result: dict[str, Any] = {"provider": provider_name, "warnings": []}
    stats, odds = _details_for(provider, dto, include_details)
    match = _store_match(db, provider_name, dto, stats, odds, provider_name == "sample", result)
    db.commit()
    seed_data_sources(db)
    return {
        "provider": provider_name,
        "status": "success",
        "match_id": match.id,
        "records_in": 1,
        "matches_upserted": 1,
        "warnings": result["warnings"],
    }


def enrich_stats(
    db: Session,
    provider_name: str,
    *,
    league_id: int | None = None,
    season_label: str | None = None,
    limit: int = 90,
    force: bool = False,
) -> dict[str, Any]:
    """Backfill per-match statistics (including xG) for already-stored matches.

    Only fixtures that came from ``provider_name`` are touched, and each is
    looked up by the provider id stored at ingest time, so no identity guessing
    is needed.  One provider request returns both teams' stats for a match.

    The run is idempotent and resumable: a match that already has statistics is
    skipped unless ``force``, and ``limit`` keeps a single run inside an
    API-Football Free key's daily allowance, so re-running continues where it
    stopped.
    """
    provider = get_provider(provider_name)
    available, reason = provider.is_available()
    if not available:
        raise ProviderError(reason)

    stmt = (
        select(Match)
        .where(Match.source_provider == provider_name, Match.status == "finished")
        .order_by(Match.kickoff_utc.desc())
    )
    if league_id is not None:
        stmt = stmt.where(Match.league_id == league_id)
    if season_label is not None:
        stmt = stmt.join(Season, Season.id == Match.season_id).where(Season.label == season_label)
    matches = list(db.execute(stmt).scalars())

    already: set[int] = set()
    if matches:
        already = set(
            db.execute(
                select(MatchStat.match_id).where(
                    MatchStat.match_id.in_([m.id for m in matches])
                )
            ).scalars()
        )

    from app.analysis.identity import normalize_name

    result: dict[str, Any] = {
        "provider": provider_name,
        "status": "success",
        "candidates": len(matches),
        "enriched": 0,
        "skipped": 0,
        "errors": 0,
        "warnings": [],
    }
    budget = max(0, limit)
    for match in matches:
        if result["enriched"] >= budget:
            break
        if match.id in already and not force:
            result["skipped"] += 1
            continue
        home = db.get(Team, match.home_team_id)
        away = db.get(Team, match.away_team_id)
        if home is None or away is None:
            result["errors"] += 1
            continue
        league = db.get(League, match.league_id) if match.league_id else None
        season = db.get(Season, match.season_id) if match.season_id else None
        dto = NormalizedMatch(
            provider_id=match.source_ref,
            kickoff_utc=match.kickoff_utc,
            home_name=home.name,
            away_name=away.name,
            league_name=league.name if league else "Unknown",
            country=league.country if league else None,
            season_label=season.label if season else None,
            status="finished",
            home_goals=match.home_goals,
            away_goals=match.away_goals,
            ht_home_goals=match.ht_home_goals,
            ht_away_goals=match.ht_away_goals,
            source_url=f"https://www.api-football.com/fixtures/{match.source_ref}",
        )
        try:
            stats = list(provider.fetch_stats(dto))
        except ProviderError as exc:
            result["errors"] += 1
            result["warnings"].append(f"{match.source_ref}: {exc}")
            if result["errors"] >= 5:
                break
            continue

        team_by_name = {home.name: home, away.name: away}
        team_by_key = {normalize_name(home.name): home, normalize_name(away.name): away}
        for stat in stats:
            team = team_by_name.get(stat.team_name) or team_by_key.get(
                normalize_name(stat.team_name)
            )
            if team is not None:
                _upsert_stat(db, provider_name, match, stat, team, False)
        db.commit()
        result["enriched"] += 1

    db.commit()
    seed_data_sources(db)
    return result


def _upsert_odd_guarded(db: Session, provider: str, match: Match, odd: NormalizedOdd,
                        is_estimate: bool, result: dict[str, Any]) -> None:
    if odd.decimal_odds <= 1.0:
        result["warnings"].append(f"ignored invalid odds {odd.market}:{odd.selection}={odd.decimal_odds}")
        return
    _upsert_odds(db, provider, match, odd, is_estimate)


def import_manual_match(db: Session, payload: dict[str, Any]) -> int:
    """Create a match from a manual payload (API manual-entry path).

    Returns the local match id.  Stats and odds are optional.
    """
    kickoff = payload["kickoff_utc"]
    if isinstance(kickoff, str):
        kickoff = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=UTC)

    league_name = payload["league"]
    country = payload.get("country")
    season_label = payload.get("season")
    provider = "manual"
    dto = NormalizedMatch(
        provider_id=f"manual-api:{kickoff.date().isoformat()}:{payload['home']}:{payload['away']}",
        kickoff_utc=kickoff,
        home_name=payload["home"],
        away_name=payload["away"],
        league_name=league_name,
        country=country,
        season_label=season_label,
        round_label=payload.get("round"),
        venue=payload.get("venue"),
        status=payload.get("status", "scheduled"),
        home_goals=payload.get("home_goals"),
        away_goals=payload.get("away_goals"),
        ht_home_goals=payload.get("ht_home_goals"),
        ht_away_goals=payload.get("ht_away_goals"),
        source_url="manual://api",
    )
    league = _upsert_league(db, provider, dto, False)
    season = _upsert_season(db, league, season_label)
    home = _upsert_team(db, provider, dto.home_name, country, league, False, dto.source_url)
    away = _upsert_team(db, provider, dto.away_name, country, league, False, dto.source_url)
    match = _upsert_match(db, provider, dto, league, season, home, away, False)

    for side, team in (("home", home), ("away", away)):
        metrics = payload.get(f"{side}_stats")
        if metrics:
            metrics = {k: v for k, v in metrics.items() if v is not None}
            if metrics:
                _upsert_stat(
                    db, provider, match,
                    NormalizedStat(dto.provider_id, team.name, side == "home", metrics, dto.source_url),
                    team, False,
                )
    for odd in payload.get("odds", []) or []:
        _upsert_odds(
            db, provider, match,
            NormalizedOdd(
                match_provider_id=dto.provider_id,
                market=odd["market"],
                selection=odd["selection"],
                decimal_odds=float(odd["decimal_odds"]),
                bookmaker=odd.get("bookmaker", "manual"),
                is_closing=bool(odd.get("is_closing", False)),
                line=odd.get("line"),
                source_url=dto.source_url,
            ),
            False,
        )
    db.commit()
    seed_data_sources(db)
    return match.id


def ingest_all_enabled(db: Session, **kwargs: Any) -> list[dict[str, Any]]:
    providers = build_providers(get_settings().provider_list)
    results = []
    for name, provider in providers.items():
        if hasattr(provider, "iter_dataset") or not getattr(provider, "requires_params", False):
            results.append(ingest_provider(db, name, **kwargs))
        else:
            results.append({"provider": name, "status": "skipped",
                            "reason": "requires explicit parameters"})
    seed_data_sources(db)
    return results
