"""Cross-provider identity resolution and the sibling-competition fallback.

The same competition/club can arrive from two providers under different names
(``"Liga Profesional Argentina"`` vs ``"Liga Profesional"``; ``"Huracan"`` vs
``"Hurac\u00e1n"``).  When a fixture's own competition holds too little history the
engine fits on a sibling competition in the same country, matching the clubs by
name.  These tests cover the name matching itself and the engine fallback.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.analysis import identity
from app.analysis.engine import AnalysisOptions, build_analysis
from app.ingest.service import import_manual_match


# --------------------------------------------------------------------------
# Name normalisation and matching
# --------------------------------------------------------------------------
def test_normalize_name_strips_diacritics_and_punctuation():
    assert identity.normalize_name("Hurac\u00e1n") == "huracan"
    assert identity.normalize_name("V\u00e9lez Sarsfield") == "velez sarsfield"
    assert identity.normalize_name("Newell's Old Boys") == "newells old boys"
    assert identity.normalize_name("Atl\u00e9tico   Tucum\u00e1n") == "atletico tucuman"
    assert identity.normalize_name(None) == ""


def test_observed_provider_alias_is_applied():
    assert identity.canonical_team_key("Gimnasia L.P.") == "gimnasia de la plata"
    assert identity.canonical_team_key("Gimnasia de La Plata") == "gimnasia de la plata"


def test_normalize_name_drops_trailing_association_code():
    # openfootball labels cup clubs with the association in brackets.
    assert identity.normalize_name("Manchester City FC (ENG)") == "manchester city fc"
    assert identity.normalize_name("FC Bayern M\u00fcnchen (GER)") == "fc bayern munchen"
    assert identity.normalize_name("AC Sparta Praha (CZE)") == "ac sparta praha"
    # A club name without a code is untouched.
    assert identity.normalize_name("Barcelona") == "barcelona"


def test_cup_club_key_links_to_domestic_club():
    # With the association code gone, a cup row folds to the same club key as
    # the domestic row (ignoring provider decoration words).
    assert identity.team_merge_key("Manchester City FC (ENG)") == identity.team_merge_key(
        "Manchester City"
    )


def test_observed_club_alias_folds_a_qualifier_spelling():
    # "Brighton" and "Brighton & Hove Albion FC" are one club; the alias key is
    # written in normalised form (the ampersand folds to a space), so both spell
    # to the same merge key.
    assert identity.team_merge_key("Brighton & Hove Albion FC") == identity.team_merge_key(
        "Brighton"
    )
    assert identity.team_merge_key("Angers SCO") == identity.team_merge_key("Angers")


def test_observed_club_alias_does_not_fold_distinct_short_names():
    # A bare name is never collapsed into a longer one that only shares a word:
    # Paris FC is not Paris Saint-Germain, and Independiente is not Independiente
    # Rivadavia.
    assert identity.team_merge_key("Paris FC") != identity.team_merge_key(
        "Paris Saint Germain"
    )
    assert identity.team_merge_key("Independiente") != identity.team_merge_key(
        "Independiente Rivadavia"
    )


def test_resolve_key_prefers_exact_normalised_match():
    keys = ["huracan", "gimnasia de la plata", "velez sarsfield"]
    assert identity.resolve_key("Hurac\u00e1n", keys) == "huracan"
    assert identity.resolve_key("Velez Sarsfield", keys) == "velez sarsfield"
    assert identity.resolve_key("Gimnasia L.P.", keys) == "gimnasia de la plata"


def test_resolve_key_is_none_when_ambiguous_or_unknown():
    keys = ["independiente", "independiente rivadavia"]
    assert identity.resolve_key("Independiente", keys) == "independiente"
    assert identity.resolve_key("Totally Unknown Club", keys) is None


def test_resolve_key_matches_a_dropped_qualifier_when_unique():
    keys = ["belgrano de cordoba", "instituto de cordoba", "huracan"]
    assert identity.resolve_key("Belgrano", keys) == "belgrano de cordoba"
    assert identity.resolve_key("Instituto", keys) == "instituto de cordoba"


def test_resolve_key_will_not_guess_between_two_short_names():
    keys = ["belgrano de cordoba", "belgrano mendoza"]
    assert identity.resolve_key("Belgrano", keys) is None


def test_token_similarity_handles_connectors_and_initials():
    assert identity.token_similarity("gimnasia l p", "gimnasia de la plata") >= 0.8


# --------------------------------------------------------------------------
# Engine fallback
# --------------------------------------------------------------------------
HISTORY_TEAMS = ["R\u00edo Verde", "Costa Azul", "Monte Claro", "Bah\u00eda Norte"]


def _finished_match(db, league, country, home, away, days_ago, home_goals, away_goals) -> int:
    kickoff = (datetime.now(tz=UTC) - timedelta(days=days_ago)).replace(microsecond=0)
    return import_manual_match(
        db,
        {
            "league": league,
            "country": country,
            "kickoff_utc": kickoff.isoformat(),
            "home": home,
            "away": away,
            "status": "finished",
            "home_goals": home_goals,
            "away_goals": away_goals,
        },
    )


def _seed_history(db, league, country, n_matches=24) -> None:
    pairs = [(0, 1), (1, 2), (2, 3), (3, 0), (0, 2), (1, 3)]
    for i in range(n_matches):
        home, away = pairs[i % len(pairs)]
        _finished_match(db, league, country, HISTORY_TEAMS[home], HISTORY_TEAMS[away],
                        days_ago=30 + i, home_goals=i % 3, away_goals=(i + 1) % 3)


def test_analysis_uses_sibling_competition_when_own_history_is_thin(db):
    country = "Testland-argentina-fallback"
    history_league = "Testland Premier"
    sibling_league = "Testland Premier Apertura"
    _seed_history(db, history_league, country)

    kickoff = (datetime.now(tz=UTC) + timedelta(days=2)).replace(microsecond=0)
    fixture_id = import_manual_match(
        db,
        {
            "league": sibling_league,  # a separate competition with no results of its own
            "country": country,
            "kickoff_utc": kickoff.isoformat(),
            "home": "Rio Verde",  # unaccented spelling of "R\u00edo Verde"
            "away": "costa azul",
            "status": "scheduled",
        },
    )

    report = build_analysis(db, fixture_id, AnalysisOptions(persist=False))
    scope = report["scope"]
    assert scope["cross_competition"] is True
    assert scope["own_league_matches"] == 0
    assert scope["n_training_matches"] >= 20
    assert scope["model_home_team"] == "R\u00edo Verde"
    assert scope["model_away_team"] == "Costa Azul"
    assert report["model"]["converged"] is True
    assert report["form"]["home"]["last5"]["n_matches"] == 5
    assert report["limitations"][0].startswith("This competition's own history is too thin")


def test_analysis_stays_within_competition_when_it_has_history(db):
    country = "Testland-own-history"
    league = "Testland Primera"
    _seed_history(db, league, country)

    kickoff = (datetime.now(tz=UTC) + timedelta(days=2)).replace(microsecond=0)
    fixture_id = import_manual_match(
        db,
        {
            "league": league,
            "country": country,
            "kickoff_utc": kickoff.isoformat(),
            "home": HISTORY_TEAMS[0],
            "away": HISTORY_TEAMS[1],
            "status": "scheduled",
        },
    )
    report = build_analysis(db, fixture_id, AnalysisOptions(persist=False))
    assert report["scope"]["cross_competition"] is False
    assert report["scope"]["n_training_matches"] >= 20


def test_peer_competitions_can_be_disabled(db):
    country = "Testland-no-peers"
    history_league = "Testland First Division"
    sibling_league = "Testland Second Division"
    _seed_history(db, history_league, country)

    kickoff = (datetime.now(tz=UTC) + timedelta(days=2)).replace(microsecond=0)
    fixture_id = import_manual_match(
        db,
        {
            "league": sibling_league,
            "country": country,
            "kickoff_utc": kickoff.isoformat(),
            "home": "Rio Verde",
            "away": "Costa Azul",
            "status": "scheduled",
        },
    )
    with pytest.raises(ValueError, match="only 0 completed matches"):
        build_analysis(
            db, fixture_id, AnalysisOptions(persist=False, allow_peer_competitions=False)
        )
