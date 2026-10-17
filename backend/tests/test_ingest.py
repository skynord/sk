"""Tests for provider registration and the ingestion service."""

from __future__ import annotations

import csv
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select

from app.config import get_settings
from app.db.models import League, Match, MatchOdd, ProviderRecord, Team
from app.ingest.service import (
    _dataset_from_provider,
    ingest_fixture,
    ingest_provider,
    seed_data_sources,
)
from app.providers import available_provider_names, get_provider
from app.providers.base import Provider, ProviderCapabilities, ProviderError, register_provider
from app.providers.dto import NormalizedMatch


def test_expected_providers_are_registered():
    names = set(available_provider_names())
    assert {
        "football_data_co_uk",
        "manual",
        "openfootball",
        "sample",
        "api_football",
        "football_data_org",
        "sportmonks",
        "besoccer",
    } <= names


def test_provider_describe_has_capabilities():
    caps = get_provider("football_data_co_uk").describe()
    assert caps.requires_key is False
    assert "results" in caps.capabilities


def test_api_providers_report_unavailable_without_key():
    available, reason = get_provider("api_football").is_available()
    assert available is False
    assert "key" in reason.lower() or "FA_API_FOOTBALL_KEY" in reason


def test_seed_data_sources_is_idempotent(db):
    seed_data_sources(db)
    seed_data_sources(db)
    from app.db.models import DataSource

    count = db.execute(select(func.count()).select_from(DataSource)).scalar_one()
    assert count == len(available_provider_names())


class TestSampleIngest:
    def test_creates_synthetic_league_and_matches(self, db):
        result = ingest_provider(db, "sample")
        assert result["status"] == "success"
        assert any("SYNTHETIC" in w for w in result["warnings"])
        league = db.execute(
            select(League).where(League.country.like("%SYNTHETIC%"))
        ).scalars().first()
        assert league is not None
        n_matches = db.execute(
            select(func.count()).select_from(Match).where(Match.league_id == league.id)
        ).scalar_one()
        assert n_matches > 100

    def test_reingest_is_idempotent(self, db):
        first = ingest_provider(db, "sample")
        total_after_first = db.execute(select(func.count()).select_from(Match)).scalar_one()
        second = ingest_provider(db, "sample")
        total_after_second = db.execute(select(func.count()).select_from(Match)).scalar_one()
        assert first["matches_upserted"] == second["matches_upserted"]
        assert total_after_first == total_after_second


class TestFootballDataCoUkIngest:
    def _write_csv(self) -> None:
        raw_dir = get_settings().raw_data_dir
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = raw_dir / "e0_unit_test.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "HTHG", "HTAG",
                "HS", "HST", "HC", "HF", "HY", "HR", "AS", "AST", "AC", "AF", "AY", "AR",
                "B365H", "B365D", "B365A", "B365>2.5", "B365<2.5",
            ])
            writer.writerow([
                "E0", "01/09/2023", "Test United", "Test Rovers", "2", "1", "1", "0",
                "12", "6", "7", "10", "2", "0", "8", "3", "4", "12", "3", "1",
                "1.8", "3.9", "4.5", "1.7", "2.1",
            ])

    def test_imports_csv_match_with_stats_and_odds(self, db):
        self._write_csv()
        result = ingest_provider(db, "football_data_co_uk")
        assert result["status"] == "success"
        home = db.execute(select(Team).where(Team.name == "Test United")).scalars().first()
        away = db.execute(select(Team).where(Team.name == "Test Rovers")).scalars().first()
        assert home is not None and away is not None
        match = db.execute(
            select(Match).where(Match.home_team_id == home.id, Match.away_team_id == away.id)
        ).scalars().first()
        assert match is not None
        assert (match.home_goals, match.away_goals) == (2, 1)
        assert match.status == "finished"
        n_odds = db.execute(
            select(func.count()).select_from(MatchOdd).where(MatchOdd.match_id == match.id)
        ).scalar_one()
        assert n_odds >= 5  # 3x 1X2 (Bet365) + over/under 2.5

    def test_rerun_does_not_duplicate(self, db):
        self._write_csv()
        ingest_provider(db, "football_data_co_uk")
        before = db.execute(
            select(func.count()).select_from(Match).where(Match.kickoff_utc.is_not(None))
        ).scalar_one()
        ingest_provider(db, "football_data_co_uk")
        after = db.execute(
            select(func.count()).select_from(Match).where(Match.kickoff_utc.is_not(None))
        ).scalar_one()
        assert before == after

    def test_reingest_after_provenance_pruned_does_not_collide(self, db):
        # A dedupe pass can delete a team's provider record while its matches
        # remain. Re-ingesting then re-adds that record once per row; with
        # autoflush off, the second row used to insert a duplicate and blow up
        # the unique (provider, entity_type, entity_id) constraint. Two matches
        # here share the same two teams, so one row collides with the previous.
        raw_dir = get_settings().raw_data_dir
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = raw_dir / "e2_prov_unit_test.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"])
            writer.writerow(["E2", "01/09/2023", "Shared Town", "Common City", "1", "0"])
            writer.writerow(["E2", "08/09/2023", "Shared Town", "Common City", "2", "2"])
        ingest_provider(db, "football_data_co_uk")
        home = db.execute(select(Team).where(Team.name == "Shared Town")).scalars().first()
        assert home is not None
        db.execute(
            delete(ProviderRecord).where(
                ProviderRecord.provider == "football_data_co_uk",
                ProviderRecord.entity_type == "team",
                ProviderRecord.entity_id == home.id,
            )
        )
        db.flush()

        result = ingest_provider(db, "football_data_co_uk")

        assert result["status"] == "success"
        n = db.execute(
            select(func.count())
            .select_from(ProviderRecord)
            .where(
                ProviderRecord.provider == "football_data_co_uk",
                ProviderRecord.entity_type == "team",
                ProviderRecord.entity_id == home.id,
            )
        ).scalar_one()
        assert n == 1

    def test_local_time_is_converted_to_utc(self, db):
        # The file lists the venue's local kick-off: a 15:00 BST start in
        # England on 2023-09-02 is 14:00 UTC.
        raw_dir = get_settings().raw_data_dir
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = raw_dir / "e1_time_unit_test.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Div", "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG"])
            writer.writerow(["E1", "02/09/2023", "15:00", "Alpha Town", "Beta City", "1", "1"])
        ingest_provider(db, "football_data_co_uk")
        home = db.execute(select(Team).where(Team.name == "Alpha Town")).scalars().first()
        away = db.execute(select(Team).where(Team.name == "Beta City")).scalars().first()
        assert home is not None and away is not None
        match = db.execute(
            select(Match).where(Match.home_team_id == home.id, Match.away_team_id == away.id)
        ).scalars().first()
        assert match is not None
        # SQLite hands back a naive datetime; the stored value is 14:00 UTC.
        assert match.kickoff_utc == datetime(2023, 9, 2, 14, 0)


def _dto(pid: str, league: str, country: str, status: str = "finished") -> NormalizedMatch:
    return NormalizedMatch(
        provider_id=pid,
        kickoff_utc=datetime(2026, 10, 16, 18, 0, tzinfo=UTC),
        home_name=f"{pid} home",
        away_name=f"{pid} away",
        league_name=league,
        country=country,
        status=status,
    )


class _CountingProvider(Provider):
    """Records which matches had stats/odds fetched, to pin the filter order."""

    def __init__(self) -> None:
        super().__init__()
        self.stats_calls: list[str] = []
        self.odds_calls: list[str] = []

    def describe(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="counting", kind="api", capabilities={"fixtures", "stats", "odds"}
        )

    def fetch_matches(self, **_: object) -> list[NormalizedMatch]:
        return [
            _dto("a", "League A", "CountryA"),
            _dto("b", "League A", "CountryA"),
            _dto("c", "League B", "CountryB"),
            _dto("d", "League A", "CountryA", status="scheduled"),
        ]

    def fetch_stats(self, match: NormalizedMatch) -> list:
        self.stats_calls.append(match.provider_id)
        return []

    def fetch_odds(self, match: NormalizedMatch) -> list:
        self.odds_calls.append(match.provider_id)
        return []


class TestLeagueFilterBeforeDetails:
    """The ``leagues`` filter must run before per-match stats/odds calls.

    Fetching details first burned the whole API quota on ignored fixtures.
    """

    def test_filtered_out_matches_never_fetch_details(self):
        provider = _CountingProvider()
        kept = {
            m.provider_id
            for m, _, _ in _dataset_from_provider(
                provider, league_filter={("league a", "countrya")}
            )
        }
        assert kept == {"a", "b", "d"}
        assert provider.odds_calls == ["a", "b", "d"]  # odds for kept matches only
        assert provider.stats_calls == ["a", "b"]  # scheduled match has no stats

    def test_no_filter_fetches_everything(self):
        provider = _CountingProvider()
        rows = list(_dataset_from_provider(provider))
        assert {m.provider_id for m, _, _ in rows} == {"a", "b", "c", "d"}
        assert provider.odds_calls == ["a", "b", "c", "d"]

    def test_include_details_false_skips_network_calls(self):
        provider = _CountingProvider()
        list(_dataset_from_provider(provider, include_details=False))
        assert provider.stats_calls == []
        assert provider.odds_calls == []


class _FixtureProvider(Provider):
    """A tiny registered provider serving two fixtures, for ingest_fixture."""

    stats_calls: list[str] = []
    odds_calls: list[str] = []

    def describe(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="unit_fixture", kind="api", capabilities={"fixtures", "stats", "odds"}
        )

    def fetch_matches(self, **_: object) -> list[NormalizedMatch]:
        return [
            _dto("fx1", "League A", "CountryA", status="scheduled"),
            _dto("fx2", "League A", "CountryA", status="live"),
        ]

    def fetch_stats(self, match: NormalizedMatch) -> list:
        type(self).stats_calls.append(match.provider_id)
        return []

    def fetch_odds(self, match: NormalizedMatch) -> list:
        type(self).odds_calls.append(match.provider_id)
        return []


class TestIngestFixture:
    """Importing a single provider fixture makes a live/upcoming one analysable."""

    def setup_method(self) -> None:
        register_provider(_FixtureProvider)
        _FixtureProvider.stats_calls = []
        _FixtureProvider.odds_calls = []

    def test_stores_one_fixture_with_details(self, db):
        result = ingest_fixture(db, "unit_fixture", provider_id="fx2")
        assert result["status"] == "success"
        assert result["matches_upserted"] == 1
        match = db.get(Match, result["match_id"])
        assert match is not None
        assert (match.source_provider, match.source_ref) == ("unit_fixture", "fx2")
        assert _FixtureProvider.stats_calls == ["fx2"]  # live: stats fetched
        assert _FixtureProvider.odds_calls == ["fx2"]

    def test_scheduled_fixture_skips_stats(self, db):
        result = ingest_fixture(db, "unit_fixture", provider_id="fx1")
        match = db.get(Match, result["match_id"])
        assert match.status == "scheduled"
        assert _FixtureProvider.stats_calls == []  # not played yet
        assert _FixtureProvider.odds_calls == ["fx1"]

    def test_unknown_fixture_raises(self, db):
        with pytest.raises(ProviderError):
            ingest_fixture(db, "unit_fixture", provider_id="missing")

    def test_local_ids_map_provider_refs(self, db):
        from app.api.routes.predictions import _local_ids

        result = ingest_fixture(db, "unit_fixture", provider_id="fx1")
        mapping = _local_ids(db, "unit_fixture", {"fx1", "missing"})
        assert mapping == {"fx1": result["match_id"]}

