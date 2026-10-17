"""Tests for the public-domain openfootball provider (parsing only, no network)."""

from __future__ import annotations

from datetime import UTC

from app.providers import get_provider
from app.providers.openfootball import LEAGUES, OpenFootballProvider

PAYLOAD = {
    "name": "English Premier League 2024/25",
    "matches": [
        {
            "round": "Matchday 1",
            "date": "2024-08-16",
            "time": "20:00",
            "team1": "Manchester United FC",
            "team2": "Fulham FC",
            "score": {"ht": [0, 0], "ft": [1, 0]},
        },
        {
            "round": "Matchday 1",
            "date": "2024-08-17",
            "time": "12:30",
            "team1": "Ipswich Town FC",
            "team2": "Liverpool FC",
            "score": {"ft": [0, 2]},
        },
        {
            "round": "Matchday 2",
            "date": "2024-08-24",
            "team1": "Brighton & Hove Albion FC",
            "team2": "Manchester United FC",
        },
    ],
}


def test_provider_is_registered_and_free():
    caps = get_provider("openfootball").describe()
    assert caps.requires_key is False
    assert "results" in caps.capabilities
    assert "fixtures" in caps.capabilities


def test_parse_payload_finished_match_with_halftime():
    matches = OpenFootballProvider().parse_payload(PAYLOAD, "en.1", "2024-25", "http://x")
    first = matches[0]
    assert first.home_name == "Manchester United FC"
    assert first.away_name == "Fulham FC"
    assert (first.home_goals, first.away_goals) == (1, 0)
    assert (first.ht_home_goals, first.ht_away_goals) == (0, 0)
    assert first.status == "finished"
    assert first.league_name == "Premier League"
    assert first.country == "England"
    assert first.season_label == "2024/25"
    # openfootball publishes the venue's local time: 20:00 in England (BST)
    # in August is 19:00 UTC, and it is stored as aware UTC.
    assert first.kickoff_utc.hour == 19
    assert first.kickoff_utc.tzinfo is UTC


def test_parse_payload_finished_match_without_halftime():
    matches = OpenFootballProvider().parse_payload(PAYLOAD, "en.1", "2024-25", "http://x")
    second = matches[1]
    assert (second.home_goals, second.away_goals) == (0, 2)
    assert second.ht_home_goals is None and second.ht_away_goals is None
    assert second.status == "finished"


def test_parse_payload_future_fixture_has_no_score():
    matches = OpenFootballProvider().parse_payload(PAYLOAD, "en.1", "2024-25", "http://x")
    third = matches[2]
    assert third.status == "scheduled"
    assert third.home_goals is None and third.away_goals is None


def test_parse_payload_skips_rows_without_teams_or_date():
    payload = {"matches": [{"team1": "A", "team2": "B"}, {"date": "2024-08-01", "team2": "B"}]}
    assert OpenFootballProvider().parse_payload(payload, "en.1", "2024-25", "http://x") == []


def test_parse_payload_accepts_bare_list_score():
    payload = {
        "matches": [
            {"date": "2025-08-16", "time": "12:30", "team1": "A FC", "team2": "B FC", "score": [3, 1]}
        ]
    }
    (match,) = OpenFootballProvider().parse_payload(payload, "en.1", "2025-26", "http://x")
    assert (match.home_goals, match.away_goals) == (3, 1)
    assert match.status == "finished"
    assert match.ht_home_goals is None


def test_parse_payload_empty_score_dict_is_fixture():
    payload = {"matches": [{"date": "2025-05-23", "team1": "A FC", "team2": "B FC", "score": {}}]}
    (match,) = OpenFootballProvider().parse_payload(payload, "es.1", "2024-25", "http://x")
    assert match.status == "scheduled"
    assert match.home_goals is None and match.away_goals is None


def test_parse_payload_null_score_is_fixture():
    payload = {"matches": [{"date": "2026-05-17", "team1": "A FC", "team2": "B FC", "score": None}]}
    (match,) = OpenFootballProvider().parse_payload(payload, "fr.1", "2024-25", "http://x")
    assert match.status == "scheduled"


def test_provider_ids_are_unique():
    matches = OpenFootballProvider().parse_payload(PAYLOAD, "en.1", "2024-25", "http://x")
    ids = [m.provider_id for m in matches]
    assert len(ids) == len(set(ids))


def test_unknown_code_falls_back_to_code_as_name():
    matches = OpenFootballProvider().parse_payload(PAYLOAD, "zz.9", "2024-25", "http://x")
    assert matches[0].league_name == "zz.9"
    assert matches[0].country is None


def test_league_map_covers_configured_defaults():
    for code in ("en.1", "es.1", "de.1", "it.1", "fr.1"):
        assert code in LEAGUES
