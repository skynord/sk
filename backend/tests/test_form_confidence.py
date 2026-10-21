"""Form only moves the grade, never the probabilities it is grading."""

from __future__ import annotations

from app.analysis import bot
from app.analysis.engine import _form_signal
from app.analysis.report import derive_confidence

HOME_PICK = {"home": 0.55, "draw": 0.25, "away": 0.20}
DRAW_PICK = {"home": 0.30, "draw": 0.40, "away": 0.30}


def _form(ppg: float | None, n: int = 10) -> dict:
    return {"points_per_match": ppg, "n_matches": n}


def test_a_favourite_in_better_form_firms_the_grade():
    signal = _form_signal(one_x_two=HOME_PICK, home_form=_form(2.4), away_form=_form(0.9))
    assert signal["available"] is True
    assert signal["direction"] == "agrees"
    assert signal["delta"] == 4.0
    assert signal["note"]


def test_a_favourite_in_worse_form_cuts_the_grade_harder():
    signal = _form_signal(one_x_two=HOME_PICK, home_form=_form(0.9), away_form=_form(2.4))
    assert signal["available"] is True
    assert signal["direction"] == "contradicts"
    assert signal["delta"] == -6.0


def test_the_away_favourite_is_judged_on_its_own_form():
    signal = _form_signal(one_x_two={"home": 0.20, "draw": 0.25, "away": 0.55},
                          home_form=_form(0.7), away_form=_form(2.2))
    assert signal["direction"] == "agrees"
    assert signal["delta"] == 4.0


def test_close_form_leaves_the_grade_alone():
    signal = _form_signal(one_x_two=HOME_PICK, home_form=_form(1.6), away_form=_form(1.4))
    assert signal["available"] is True
    assert signal["direction"] == "neutral"
    assert signal["delta"] == 0.0


def test_similar_form_supports_a_draw_being_favoured():
    signal = _form_signal(one_x_two=DRAW_PICK, home_form=_form(1.5), away_form=_form(1.6))
    assert signal["direction"] == "agrees"
    assert signal["delta"] == 1.5


def test_form_is_unavailable_without_five_recent_matches():
    signal = _form_signal(one_x_two=HOME_PICK, home_form=_form(2.4, n=4), away_form=_form(0.9))
    assert signal["available"] is False
    assert signal["delta"] == 0.0
    assert signal["note"] is None


def test_the_grade_moves_but_stays_inside_its_bounds():
    base = dict(
        n_training_matches=1200,
        min_matches=100,
        monte_carlo_std_error=0.004,
        converged=True,
        counters={"home_form": 10, "away_form": 10, "h2h": 3, "has_xg": 1, "has_halftime": 1},
        has_odds=True,
        one_x_two=HOME_PICK,
        has_secondary_stats=True,
    )
    plain = derive_confidence(**base)
    punished = derive_confidence(**base, form_delta=-6.0, form_note="cut for form")
    rewarded = derive_confidence(**base, form_delta=4.0, form_note="firmed by form")

    assert punished["score"] == plain["score"] - 6
    assert rewarded["score"] == plain["score"] + 4
    assert "cut for form" in punished["notes"]
    assert "firmed by form" in rewarded["notes"]


def _report(*, missing: list[str], value_available: bool = True) -> dict:
    return {
        "match": {
            "id": 1,
            "kickoff_utc": "2026-10-22T19:00:00+00:00",
            "status": "scheduled",
            "league": {"id": 8, "name": "UEFA Champions League"},
            "home_team": {"id": 10, "name": "Lille"},
            "away_team": {"id": 11, "name": "Galatasaray"},
        },
        "markets": {
            "one_x_two": {"home": 0.576, "draw": 0.234, "away": 0.19},
            "double_chance": {"1X": 0.81, "12": 0.766, "X2": 0.424},
            "totals": {"2.5": {"over": 0.55, "under": 0.45}},
            "btts": {"yes": 0.51, "no": 0.49},
        },
        "predicted_score": {"score": "1-0", "probability": 0.113},
        "expected_goals": {"home": 1.81, "away": 0.94, "total": 2.75},
        "value": {
            "available": value_available,
            "assessments": [
                {
                    "market": "1x2",
                    "selection": "home",
                    "bookmaker": "Pinnacle",
                    "decimal_odds": 1.95,
                    "expected_value": 0.12,
                    "edge": 0.04,
                    "is_positive_ev": True,
                }
            ],
        },
        "confidence": {"level": "low", "score": 64, "form_signal": None},
        "model": {
            "n_training_matches": 1154,
            "missing_teams": missing,
            "evidence": {"home_matches": 145, "away_matches": 0},
        },
    }


def test_a_side_with_no_history_gets_no_pick_and_no_value():
    summary = bot.summarise_report(_report(missing=["Galatasaray"]))
    assert summary["pick"] is None
    assert summary["insufficient_data"] == ["Galatasaray"]
    assert summary["best_value"] is None
    # The market numbers are still reported: they are the model's honest priors.
    assert summary["probabilities"]["one_x_two"]["home"] > 0
    assert summary["evidence"]["away_matches"] == 0


def test_a_fixture_with_history_keeps_its_pick():
    summary = bot.summarise_report(_report(missing=[]))
    assert summary["insufficient_data"] == []
    assert summary["pick"]["selection"] == "home"
    assert summary["best_value"] is not None
