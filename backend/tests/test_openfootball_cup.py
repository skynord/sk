"""The Europa/Conference League reader for openfootball's plain-text cup files.

The JSON mirror carries no Europa or Conference League at all, so those two
competitions come from the project's cup repository as text.  These tests pin
the format quirks that matter: the year is written once per season, the stage
headers carry the round, and a tie settled in extra time or on penalties is one
line whose score has to be read without guessing at the rest of it.
"""

from __future__ import annotations

from app.providers.openfootball import parse_cup_text

URL = "https://raw.githubusercontent.com/openfootball/champions-league/master/2024-25/el.txt"

SAMPLE = """\
= UEFA Europa League 2024/25

# Date       Wed Sep 25 2024 - Wed May 21 2025 (238d)
# Teams      36
# Matches    189

? League phase
  Wed Sep 25 2024
    18:45  AZ Alkmaar (NED)        v IF Elfsborg (SWE)        3-2 (1-1)
           FK Bodo/Glimt (NOR)     v FC Porto (POR)           3-2 (2-1)
  Thu Sep 26
    21:35  Tottenham Hotspur (ENG) v Qarabag FK (AZE)         3-0 (1-0)

  Thu Oct 3
    21:00  Union Saint-Gilloise (BEL) v FK Bodo/Glimt (NOR)      0-0
  Wed Jan 29 2025
    21:00  Aston Villa (ENG)       v Celtic FC (SCO)           4-2 (1-1)

▪ Quarterfinals
  Thu Apr 10
    18:45  FK Bodo/Glimt (NOR)     v Lazio Roma (ITA)         2-0 (0-0)
  Thu Apr 17
    21:00  Lazio Roma (ITA)        v FK Bodo/Glimt (NOR)      2-3 pen. 3-1 a.e.t. (2-0, 1-0)
           Manchester United (ENG) v Olympique Lyonnais (FRA)  5-4 a.e.t. (2-2, 2-0)

▪ Final
  Wed May 21
    21:00  Tottenham Hotspur (ENG) v Manchester United (ENG)  1-0 (1-0)
"""


def test_cup_text_reads_scores_rounds_and_kickoffs():
    matches = parse_cup_text(SAMPLE, code="uefa.el", stem="el", season="2024-25", url=URL)
    assert len(matches) == 9
    assert {m.league_name for m in matches} == {"UEFA Europa League"}
    assert {m.country for m in matches} == {"Europe"}
    assert {m.season_label for m in matches} == {"2024/25"}

    first = matches[0]
    assert first.home_name == "AZ Alkmaar (NED)"
    assert first.away_name == "IF Elfsborg (SWE)"
    assert (first.home_goals, first.away_goals) == (3, 2)
    assert (first.ht_home_goals, first.ht_away_goals) == (1, 1)
    assert first.status == "finished"
    assert first.round_label == "League phase"
    # 18:45 Central European Time is 16:45 UTC in September.
    assert first.kickoff_utc.isoformat() == "2024-09-25T16:45:00+00:00"
    assert first.source_url == URL


def test_cup_text_carries_the_year_into_the_next_calendar_year():
    matches = parse_cup_text(SAMPLE, code="uefa.el", stem="el", season="2024-25", url=URL)
    # "Thu Oct 3" is still 2024; "Wed Jan 29 2025" is written out in full.
    january = [m for m in matches if m.kickoff_utc.date().isoformat() == "2025-01-29"]
    assert len(january) == 1
    assert january[0].home_name == "Aston Villa (ENG)"
    october = [m for m in matches if m.kickoff_utc.month == 10]
    assert {m.kickoff_utc.year for m in october} == {2024}


def test_cup_text_reads_a_penalty_tie_as_the_score_it_ended_on():
    matches = parse_cup_text(SAMPLE, code="uefa.el", stem="el", season="2024-25", url=URL)
    tie = next(m for m in matches if m.away_name == "FK Bodo/Glimt (NOR)" and m.home_goals == 3)
    # "2-3 pen. 3-1 a.e.t. (2-0, 1-0)": this match ended 3-1 after extra time,
    # and the parenthesised pairs are not read as a half-time score.
    assert (tie.home_goals, tie.away_goals) == (3, 1)
    assert tie.ht_home_goals is None and tie.ht_away_goals is None
    assert tie.round_label == "Quarterfinals"


def test_cup_text_reads_extra_time_and_a_missing_score():
    matches = parse_cup_text(SAMPLE, code="uefa.el", stem="el", season="2024-25", url=URL)
    extra_time = next(m for m in matches if m.home_name == "Manchester United (ENG)" and m.round_label == "Quarterfinals")
    assert (extra_time.home_goals, extra_time.away_goals) == (5, 4)
    assert extra_time.ht_home_goals is None

    goalless = next(m for m in matches if m.home_name == "Union Saint-Gilloise (BEL)")
    assert (goalless.home_goals, goalless.away_goals) == (0, 0)
    assert goalless.status == "finished"


def test_cup_text_marks_an_unplayed_fixture_as_scheduled():
    text = """\
= UEFA Europa League 2026/27

? League phase
  Thu Oct 22 2026
    18:45  AS Roma (ITA)           v FC Porto (POR)
    21:00  Galatasaray (TUR)       v Feyenoord (NED)
           Aston Villa (ENG)       v Celtic FC (SCO)
"""
    matches = parse_cup_text(text, code="uefa.el", stem="el", season="2026-27", url=URL)
    assert len(matches) == 3
    assert [m.status for m in matches] == ["scheduled", "scheduled", "scheduled"]
    assert matches[1].home_name == "Galatasaray (TUR)"
    assert matches[1].home_goals is None
    # 21:00 Central European Time is 19:00 UTC while summer time is on.
    assert matches[1].kickoff_utc.isoformat() == "2026-10-22T19:00:00+00:00"
    # A kick-off the file does not state is left at the shared 12:00 default.
    assert matches[2].home_name == "Aston Villa (ENG)"
    assert matches[2].kickoff_utc.isoformat() == "2026-10-22T10:00:00+00:00"


def test_cup_text_labels_qualifying_files_and_keeps_them_apart():
    text = """\
= UEFA Europa League - Quali 2025/26

? 1. Round
  Thu Jul 10 2025
    18:30  CFR Cluj (ROU)          v FC Lugano (SUI)          1-0 a.e.t. (0-0)
    20:00  Hapoel Beer Sheva (ISR) v Viktoria Plzen (CZE)     0-3    [awarded]
"""
    matches = parse_cup_text(text, code="uefa.el", stem="elq", season="2025-26", url=URL)
    assert len(matches) == 2
    assert {m.round_label for m in matches} == {"Quali 1. Round"}
    assert all(m.provider_id.startswith("openfootball:uefa.el:2025-26:elq:") for m in matches)
    # An awarded result is a result: real goals, no half-time score stated.
    awarded = matches[1]
    assert (awarded.home_goals, awarded.away_goals) == (0, 3)
    assert awarded.ht_home_goals is None


def test_cup_text_refuses_a_score_shape_it_does_not_understand():
    text = """\
= UEFA Conference League 2024/25

? League phase
  Thu Oct 3 2024
    21:00  Some Club (BEL)         v Other Club (NED)         2:1 (1:0)
"""
    assert parse_cup_text(text, code="uefa.ecl", stem="conf", season="2024-25", url=URL) == []


def test_cup_text_files_are_configured_for_both_cups():
    from app.providers.openfootball import CUP_TEXT_SOURCES

    assert CUP_TEXT_SOURCES["uefa.el"] == ("el", "elq")
    assert CUP_TEXT_SOURCES["uefa.ecl"] == ("conf", "confq")
