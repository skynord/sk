"""Prediction bot.

Generates model predictions for upcoming (and optionally in-play) fixtures.
It reuses the same analysis engine as the rest of the app, so every number it
reports is a genuine model output from stored results.  It never places a bet,
never talks to a bookmaker account, and reports missing inputs honestly.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis import features
from app.analysis.engine import AnalysisOptions, build_analysis
from app.db.models import Match

UPCOMING_STATUSES = ("scheduled", "live")
LIVE_GRACE_HOURS = 6


def upcoming_matches(
    db: Session,
    *,
    league_id: int | None = None,
    days_ahead: int = 14,
    limit: int = 50,
    include_live: bool = True,
) -> list[Match]:
    """Scheduled (and optionally in-play) matches inside the forward window."""
    now = datetime.now(tz=UTC)
    horizon = now + timedelta(days=max(1, days_ahead))
    floor = now - timedelta(hours=LIVE_GRACE_HOURS)
    statuses = list(UPCOMING_STATUSES) if include_live else ["scheduled"]
    stmt = (
        select(Match)
        .where(
            Match.status.in_(statuses),
            Match.kickoff_utc >= floor,
            Match.kickoff_utc <= horizon,
        )
        .order_by(Match.kickoff_utc.asc())
        .limit(max(1, limit))
    )
    if league_id is not None:
        stmt = stmt.where(Match.league_id == league_id)
    return list(db.execute(stmt).scalars())


def _team_names(db: Session) -> dict[int, str]:
    return features.team_names_map(db)


def _top(probabilities: dict[str, float]) -> tuple[str, float]:
    selection = max(probabilities, key=lambda k: probabilities[k])
    return selection, probabilities[selection]


def _normalise_status(status: str | None) -> str:
    if not status:
        return "scheduled"
    s = str(status).lower()
    if s in {"finished", "full_time", "ft", "ended", "complete", "completed"}:
        return "finished"
    if s in {"live", "in_play", "inplay", "started"}:
        return "live"
    if s in {"postponed", "suspended", "cancelled", "canceled", "abandoned"}:
        return s
    return s


def _score_is_set(home: int | None, away: int | None) -> bool:
    return home is not None and away is not None


def _normalise_status(status: str | None) -> str:
    if not status:
        return "scheduled"
    s = str(status).lower()
    if s in {"finished", "full_time", "ft", "ended", "complete", "completed", "aet", "pen", "after penalties"}:
        return "finished"
    if s in {"live", "in_play", "inplay", "started", "ht", "1h", "2h"}:
        return "live"
    if s in {"postponed", "suspended", "cancelled", "canceled", "abandoned"}:
        return s
    return s


def _score_is_set(home: int | None, away: int | None) -> bool:
    return home is not None and away is not None


def summarise_report(report: dict[str, Any]) -> dict[str, Any]:
    """Condense a full analysis report into a one-line prediction record."""
    match = report["match"]
    markets = report["markets"]
    one_x_two: dict[str, float] = markets["one_x_two"]
    totals = markets["totals"].get("2.5", {})
    btts: dict[str, float] = markets["btts"]
    pick_selection, pick_probability = _top(one_x_two)
    # A side with no completed match in the training window is only a
    # league-average prior, so no team is actually favoured: publishing a "pick"
    # there would dress a guess up as a prediction.
    missing = list(report["model"].get("missing_teams") or [])

    value = report.get("value") or {}
    best_value = None
    if not missing and value.get("available") and value.get("assessments"):
        best = max(value["assessments"], key=lambda a: a["expected_value"])
        best_value = {
            "market": best["market"],
            "selection": best["selection"],
            "bookmaker": best["bookmaker"],
            "decimal_odds": best["decimal_odds"],
            "expected_value": best["expected_value"],
            "edge": best["edge"],
            "is_positive_ev": best["is_positive_ev"],
        }

    return {
        "match_id": match["id"],
        "kickoff_utc": match["kickoff_utc"],
        "status": match["status"],
        "league": (match.get("league") or {}).get("name"),
        "league_id": (match.get("league") or {}).get("id"),
        "home_team": match["home_team"]["name"],
        "away_team": match["away_team"]["name"],
        "probabilities": {
            "one_x_two": one_x_two,
            "double_chance": markets.get("double_chance"),
            "over_under_2_5": totals,
            "btts": btts,
        },
        "pick": None if missing else {
            "market": "1x2",
            "selection": pick_selection,
            "probability": pick_probability,
        },
        "predicted_score": report["predicted_score"],
        "expected_goals": report["expected_goals"],
        "best_value": best_value,
        "insufficient_data": missing,
        "confidence": report["confidence"]["level"],
        "confidence_score": report["confidence"].get("score"),
        "form_signal": report["confidence"].get("form_signal"),
        "n_training_matches": report["model"]["n_training_matches"],
        "evidence": report["model"].get("evidence"),
        "home_goals": match.get("home_goals"),
        "away_goals": match.get("away_goals"),
    }


def predict_upcoming(
    db: Session,
    *,
    league_id: int | None = None,
    days_ahead: int = 14,
    limit: int = 50,
    include_live: bool = True,
    persist: bool = True,
    options: AnalysisOptions | None = None,
) -> dict[str, Any]:
    """Run the model over every upcoming fixture in the window.

    Fixtures without enough training history are reported as skipped with the
    reason, never guessed.
    """
    names = _team_names(db)
    matches = upcoming_matches(
        db, league_id=league_id, days_ahead=days_ahead, limit=limit, include_live=include_live
    )
    base_options = options or AnalysisOptions(persist=persist)
    # One cutoff for the whole batch: every fixture in a competition then shares
    # the same training set (and the same fitted model), so the batch costs one
    # fit per competition rather than one per fixture.
    if base_options.as_of is None:
        base_options = replace(base_options, as_of=datetime.now(tz=UTC))
    predictions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for match in matches:
        try:
            report = build_analysis(db, match.id, base_options)
        except (ValueError, LookupError) as exc:
            skipped.append(
                {
                    "match_id": match.id,
                    "kickoff_utc": match.kickoff_utc.isoformat(),
                    "status": match.status,
                    "league_id": match.league_id,
                    "home_team": names.get(match.home_team_id, str(match.home_team_id)),
                    "away_team": names.get(match.away_team_id, str(match.away_team_id)),
                    "reason": str(exc),
                }
            )
            continue
        predictions.append(summarise_report(report))

    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "window_days": days_ahead,
        "include_live": include_live,
        "league_id": league_id,
        "considered": len(matches),
        "predicted": len(predictions),
        "skipped": skipped,
        "predictions": predictions,
    }
