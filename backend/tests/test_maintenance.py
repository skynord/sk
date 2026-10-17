"""Dedupe pass: merging a club/fixture split across two providers.

The same fixture reaches us twice, once per provider, with the providers
spelling the clubs differently.  The merge must keep a single fixture row and
carry across both the statistics one row held and the odds the other held, and
it must reconcile provenance rows without tripping their unique constraint.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import models  # noqa: F401  (registers the mappers)
from app.db.base import Base
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
from app.ingest import maintenance


@pytest.fixture()
def db():
    """A private, empty database so the pass only ever sees this test's rows."""
    tmp = Path(tempfile.mkdtemp(prefix="fa_maint_"))
    engine = create_engine(f"sqlite:///{(tmp / 'm.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _split_fixture(db):
    """Two providers' rows for one fixture, spelled differently."""
    league = League(slug="test-land", name="Test Land", country="Testland")
    db.add(league)
    db.flush()

    home_a = Team(slug="testland-fc-barca", name="FC Barcelona", league_id=league.id)
    home_b = Team(slug="testland-barcelona", name="Barcelona", league_id=league.id)
    away_a = Team(slug="testland-real-madrid", name="Real Madrid", league_id=league.id)
    away_b = Team(slug="testland-real-madrid-fc", name="Real Madrid FC", league_id=league.id)
    db.add_all([home_a, home_b, away_a, away_b])
    db.flush()

    kickoff = datetime(2024, 3, 10, 20, 0, tzinfo=UTC)
    first = Match(
        league_id=league.id, home_team_id=home_a.id, away_team_id=away_a.id,
        kickoff_utc=kickoff, status="finished", home_goals=2, away_goals=1,
        source_provider="football_data_co_uk",
    )
    second = Match(
        league_id=league.id, home_team_id=home_b.id, away_team_id=away_b.id,
        kickoff_utc=kickoff + timedelta(minutes=30), status="finished",
        source_provider="api_football",
    )
    db.add_all([first, second])
    db.flush()

    # The first holds odds (no stats); the second holds the xG stats.
    db.add(MatchOdd(match_id=first.id, bookmaker="Bet365", market="1x2",
                    selection="home", decimal_odds=1.9))
    db.add(MatchStat(match_id=second.id, team_id=home_b.id, is_home=True, xg=1.7))
    db.add(MatchStat(match_id=second.id, team_id=away_b.id, is_home=False, xg=0.8))
    db.flush()
    return league, (home_a, home_b, away_a, away_b)


def test_dedupe_keeps_one_fixture_with_everything_attached(db):
    league, _teams = _split_fixture(db)

    report = maintenance.run_dedupe(db, apply=True)

    assert report.applied is True
    assert report.matches_removed == 1
    assert report.stats_moved == 2  # the duplicate's xG stats moved across
    assert report.odds_moved == 0   # odds were already on the keeper

    survivors = db.query(Match).filter(Match.league_id == league.id).all()
    assert len(survivors) == 1
    survivor = survivors[0]

    # The xG statistics were carried across (not dropped, not nulled) and the
    # odds are still attached.
    xg = {s.xg for s in db.query(MatchStat).filter(MatchStat.match_id == survivor.id)}
    assert xg == {1.7, 0.8}
    assert db.query(MatchOdd).filter(MatchOdd.match_id == survivor.id).count() == 1


def test_dedupe_folds_the_two_club_spellings_into_one(db):
    league, (home_a, home_b, _away_a, _away_b) = _split_fixture(db)

    maintenance.run_dedupe(db, apply=True)

    # "FC Barcelona" and "Barcelona" are one club row now, and the surviving
    # fixture points at it, so the club's history is no longer split.
    assert db.get(Team, home_b.id) is None
    names = {t.name for t in db.query(Team).filter(Team.league_id == league.id).all()}
    assert names == {"FC Barcelona", "Real Madrid"}
    survivor = db.query(Match).filter(Match.league_id == league.id).one()
    assert survivor.home_team_id == home_a.id


def test_dedupe_reconciles_provider_records_for_several_duplicates(db):
    """Two duplicates from one provider must not both claim the keeper."""
    league = League(slug="prov-land", name="Prov Land")
    db.add(league)
    db.flush()
    keeper = Team(slug="prov-a", name="FC Barcelona", league_id=league.id)
    dup_b = Team(slug="prov-b", name="Barcelona", league_id=league.id)
    dup_c = Team(slug="prov-c", name="CF Barcelona", league_id=league.id)
    db.add_all([keeper, dup_b, dup_c])
    db.flush()
    db.add(ProviderRecord(provider="alpha", entity_type="team", entity_id=keeper.id))
    db.add(ProviderRecord(provider="beta", entity_type="team", entity_id=dup_b.id))
    db.add(ProviderRecord(provider="beta", entity_type="team", entity_id=dup_c.id))
    db.flush()

    # Without a flush before the clash check, both "beta" rows move onto the
    # keeper and the unique constraint fires.
    maintenance.run_dedupe(db, apply=True)

    kept = {
        r.provider
        for r in db.query(ProviderRecord).filter(
            ProviderRecord.entity_type == "team",
            ProviderRecord.entity_id == keeper.id,
        )
    }
    assert kept == {"alpha", "beta"}
    assert db.query(Team).filter(Team.league_id == league.id).count() == 1


def _league_with_data(db, slug, name):
    """One competition with a fixture, stats, odds, provenance and model output."""
    league = League(slug=slug, name=name, country="Testland")
    db.add(league)
    db.flush()
    one = Team(slug=f"{slug}-one", name=f"{name} One", league_id=league.id)
    two = Team(slug=f"{slug}-two", name=f"{name} Two", league_id=league.id)
    db.add_all([one, two])
    db.flush()

    match = Match(
        league_id=league.id, home_team_id=one.id, away_team_id=two.id,
        kickoff_utc=datetime(2024, 3, 10, 20, 0, tzinfo=UTC), status="finished",
        home_goals=1, away_goals=0, source_provider="test",
    )
    db.add(match)
    db.flush()
    db.add(MatchStat(match_id=match.id, team_id=one.id, is_home=True, xg=1.2))
    db.add(MatchOdd(match_id=match.id, bookmaker="B", market="1x2",
                    selection="home", decimal_odds=2.0))

    season = Season(league_id=league.id, label="2024", is_current=True)
    db.add(season)
    db.flush()
    run = ModelRun(model_name="dixon_coles", model_version="1.0.0", league_id=league.id,
                   season_id=season.id, params={}, n_matches=1)
    db.add(run)
    db.flush()
    db.add(Prediction(model_run_id=run.id, match_id=match.id, market="1x2",
                      selection="home", probability=0.5, extra={}))
    backtest = BacktestResult(model_run_id=run.id, league_id=league.id, market="1x2",
                              n_train=1, n_eval=1, details={})
    db.add(backtest)
    db.flush()
    db.add(CalibrationBin(backtest_id=backtest.id, bin_lower=0.0, bin_upper=0.5, n=1))

    for entity_type, entity_id in (
        ("match", match.id), ("team", one.id), ("team", two.id),
        ("season", season.id), ("league", league.id),
    ):
        db.add(ProviderRecord(provider="test", entity_type=entity_type, entity_id=entity_id))
    db.flush()
    return league, one, two, match, season, run, backtest


def test_remove_league_dry_run_changes_nothing(db):
    league, *_ = _league_with_data(db, "dry-land", "Dry Land")

    report = maintenance.remove_league(db, [league.id], apply=False)

    assert report.applied is False
    assert report.matches_removed == 1
    assert report.seasons_removed == 1
    assert report.model_runs_removed == 1
    # Nothing was written.
    assert db.get(League, league.id) is not None
    assert db.query(Match).count() == 1
    assert db.query(Team).count() == 2


def test_remove_league_deletes_only_that_competition(db):
    gone, gone_one, gone_two, gone_match, gone_season, gone_run, gone_bt = _league_with_data(
        db, "gone-land", "Gone Land"
    )
    keep, *_ = _league_with_data(db, "keep-land", "Keep Land")

    report = maintenance.remove_league(db, [gone.id], apply=True)

    assert report.applied is True
    assert report.matches_removed == 1
    assert report.seasons_removed == 1
    assert report.model_runs_removed == 1
    assert report.backtests_removed == 1
    assert report.teams_removed == 2  # both of Gone Land's clubs are now orphaned
    assert report.predictions_removed == 1
    assert report.stats_removed == 1
    assert report.odds_removed == 1

    db.expunge_all()  # read the rows as they now stand, not the session's copies

    # The removed competition is gone, row by row...
    assert db.get(League, gone.id) is None
    assert db.get(Match, gone_match.id) is None
    assert db.get(Team, gone_one.id) is None
    assert db.get(Team, gone_two.id) is None
    assert db.get(Season, gone_season.id) is None
    assert db.get(ModelRun, gone_run.id) is None
    assert db.get(BacktestResult, gone_bt.id) is None
    assert db.query(CalibrationBin).filter(
        CalibrationBin.backtest_id == gone_bt.id
    ).count() == 0
    assert db.query(ProviderRecord).filter(
        ProviderRecord.entity_type == "match", ProviderRecord.entity_id == gone_match.id
    ).count() == 0
    assert db.query(ProviderRecord).filter(
        ProviderRecord.entity_type == "league", ProviderRecord.entity_id == gone.id
    ).count() == 0

    # ...and the other competition is untouched.
    assert db.get(League, keep.id) is not None
    assert db.query(Match).filter(Match.league_id == keep.id).count() == 1
    assert db.query(Team).filter(Team.league_id == keep.id).count() == 2
    assert db.query(ModelRun).count() == 1
    assert db.query(BacktestResult).count() == 1


def test_remove_league_keeps_a_club_that_plays_on_elsewhere(db):
    gone, gone_one, gone_two, gone_match, *_ = _league_with_data(db, "share-a", "Share A")
    other, other_one, other_two, other_match, *_ = _league_with_data(db, "share-b", "Share B")

    # Move "Share B"'s fixture onto one of Share A's clubs: it now plays in both
    # competitions, so it must survive when Share A is removed.
    other_match.home_team_id = gone_one.id
    db.flush()

    report = maintenance.remove_league(db, [gone.id], apply=True)

    assert report.teams_removed == 1  # only the club with no fixture left

    db.expunge_all()  # read the rows as they now stand, not the session's copies

    assert db.get(Team, gone_one.id) is not None  # still plays in Share B
    assert db.get(Team, gone_two.id) is None

    survivor = db.get(Match, other_match.id)
    assert survivor is not None
    assert survivor.home_team_id == gone_one.id
    # The kept club no longer points at the deleted competition.
    assert db.get(Team, gone_one.id).league_id == other.id
    # Its provenance survives too, since the club was kept.
    assert db.query(ProviderRecord).filter(
        ProviderRecord.entity_type == "team", ProviderRecord.entity_id == gone_one.id
    ).count() == 1


def _local_kickoff_match(db, *, provider, country, home_name, when, slug):
    league = League(slug=slug, name=f"{slug} cup", country=country)
    db.add(league)
    db.flush()
    home = Team(slug=f"{slug}-home", name=home_name, league_id=league.id)
    away = Team(slug=f"{slug}-away", name=f"{slug} Away", league_id=league.id)
    db.add_all([home, away])
    db.flush()
    match = Match(
        league_id=league.id, home_team_id=home.id, away_team_id=away.id,
        kickoff_utc=when, status="scheduled", source_provider=provider,
    )
    db.add(match)
    db.flush()
    return match


def test_fix_kickoffs_shifts_openfootball_local_time_to_utc(db):
    # openfootball's 20:00 for an English fixture is 20:00 BST = 19:00 UTC.
    match = _local_kickoff_match(
        db, provider="openfootball", country="England", home_name="Liverpool FC",
        when=datetime(2025, 8, 15, 20, 0), slug="epl",
    )

    report = maintenance.fix_kickoff_utc(db, apply=True)

    assert report.applied is True
    assert report.updated == 1
    db.expunge_all()
    assert db.get(Match, match.id).kickoff_utc == datetime(2025, 8, 15, 19, 0)


def test_fix_kickoffs_uses_central_european_time_for_cross_border(db):
    # openfootball lists UEFA kick-offs in CET/CEST regardless of the venue: its
    # 21:00 for a Champions League game is 21:00 CEST = 19:00 UTC.
    match = _local_kickoff_match(
        db, provider="openfootball", country="Europe", home_name="Celtic FC (SCO)",
        when=datetime(2024, 9, 17, 21, 0), slug="ucl",
    )

    report = maintenance.fix_kickoff_utc(db, apply=True)

    assert report.updated == 1
    db.expunge_all()
    assert db.get(Match, match.id).kickoff_utc == datetime(2024, 9, 17, 19, 0)


def test_fix_kickoffs_dry_run_and_leaves_other_providers_alone(db):
    of = _local_kickoff_match(
        db, provider="openfootball", country="England", home_name="Liverpool FC",
        when=datetime(2025, 8, 15, 20, 0), slug="epl",
    )
    api = _local_kickoff_match(
        db, provider="api_football", country="England", home_name="Arsenal FC",
        when=datetime(2025, 8, 15, 19, 0), slug="epl2",
    )

    report = maintenance.fix_kickoff_utc(db, apply=False)

    assert report.applied is False
    assert report.updated == 1  # only the openfootball row is a candidate
    db.expunge_all()
    # Nothing was written, and api_football (already UTC) is never touched.
    assert db.get(Match, of.id).kickoff_utc == datetime(2025, 8, 15, 20, 0)
    assert db.get(Match, api.id).kickoff_utc == datetime(2025, 8, 15, 19, 0)


def test_fix_kickoffs_skips_a_venue_it_cannot_place(db):
    match = _local_kickoff_match(
        db, provider="openfootball", country="Testland", home_name="Nowhere FC",
        when=datetime(2025, 8, 15, 20, 0), slug="mystery",
    )

    report = maintenance.fix_kickoff_utc(db, apply=True)

    assert report.updated == 0
    assert report.skipped_unknown_venue == 1
    db.expunge_all()
    assert db.get(Match, match.id).kickoff_utc == datetime(2025, 8, 15, 20, 0)
