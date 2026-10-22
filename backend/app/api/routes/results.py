"""Results tracker: compare stored bot predictions against actual scores."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis import batches
from app.api.deps import get_db
from app.db.models import Match, Prediction, Team

router = APIRouter(tags=["results"])


def _res1x2(h: int, a: int) -> str:
    if h > a:
        return "home"
    if h < a:
        return "away"
    return "draw"


def _res_btts(h: int, a: int) -> str:
    if h > 0 and a > 0:
        return "yes"
    return "no"


@router.get("/results")
def results(
    days: int = Query(default=30, ge=1, le=90),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    cutoff = datetime.now(tz=UTC) - timedelta(days=days)
    q = (
        select(Match)
        .where(
            Match.status.in_(["finished", "full_time", "ft"]),
            Match.kickoff_utc >= cutoff,
            Match.home_goals.is_not(None),
            Match.away_goals.is_not(None),
        )
        .order_by(Match.kickoff_utc.desc())
        .limit(limit)
    )
    matches = db.execute(q).scalars().all()

    stored = batches.latest(db)
    pred_map: dict[int, dict] = {}
    if stored:
        for p in stored.get("predictions") or []:
            try:
                pred_map[int(p["match_id"])] = p
            except Exception:
                continue

    # Also load individual predictions from predictions table for finished matches
    mids = [m.id for m in matches]
    if mids:
        pres = db.execute(select(Prediction).where(Prediction.match_id.in_(mids))).scalars().all()
        by_mid: dict[int, list[Prediction]] = {}
        for pr in pres:
            by_mid.setdefault(pr.match_id, []).append(pr)
        for mid, plist in by_mid.items():
            if mid in pred_map:
                continue  # prefer batch
            # build a summary like bot.summarise_report would produce
            probs: dict[str, dict] = {}
            for pr in plist:
                probs.setdefault(pr.market, {})[pr.selection] = pr.probability
            pick = None
            if probs.get("1x2"):
                sel = max(probs["1x2"].items(), key=lambda x: x[1])[0]
                pick = {"market": "1x2", "selection": sel, "probability": probs["1x2"][sel]}
            pred_map[mid] = {
                "match_id": mid,
                "pick": pick,
                "probabilities": {
                    "one_x_two": probs.get("1x2") or {},
                    "btts": probs.get("btts") or {},
                },
                "predicted_score": None,
                "expected_goals": None,
                "confidence": None,
            }
            # find best correct score if stored differently? predictions table doesn't store correct scores directly as market usually
            # but try to infer from distribution if present? skip for now

    rows = []
    for m in matches:
        home = db.get(Team, m.home_team_id)
        away = db.get(Team, m.away_team_id)
        hg = int(m.home_goals or 0)
        ag = int(m.away_goals or 0)
        pred = pred_map.get(m.id)
        actual_1x2 = _res1x2(hg, ag)
        actual_btts = _res_btts(hg, ag)
        actual_score = f"{hg}-{ag}"

        pick_ok = None
        if pred and pred.get("pick"):
            pick_ok = pred["pick"].get("selection") == actual_1x2

        btts_pick = None
        btts_ok = None
        if pred and pred.get("probabilities") and pred["probabilities"].get("btts"):
            b = pred["probabilities"]["btts"]
            if isinstance(b, dict) and b:
                btts_pick = max(b.items(), key=lambda x: x[1])[0]
                btts_ok = btts_pick == actual_btts

        cs_pick = None
        cs_ok = None
        if pred and pred.get("predicted_score"):
            cs_pick = pred["predicted_score"].get("score")
            if cs_pick:
                cs_ok = cs_pick == actual_score

        rows.append(
            {
                "match_id": m.id,
                "kickoff_utc": m.kickoff_utc.isoformat(),
                "home_team": home.name if home else "",
                "away_team": away.name if away else "",
                "league_id": m.league_id,
                "score": {"home": hg, "away": ag},
                "actual": {"1x2": actual_1x2, "btts": actual_btts, "score": actual_score},
                "bot": {
                    "pick": pred.get("pick") if pred else None,
                    "pick_ok": pick_ok,
                    "btts_pick": btts_pick,
                    "btts_ok": btts_ok,
                    "correct_score": cs_pick,
                    "correct_score_ok": cs_ok,
                    "confidence": pred.get("confidence") if pred else None,
                    "expected_goals": pred.get("expected_goals") if pred else None,
                },
            }
        )

    total = len(rows)
    p1 = sum(1 for r in rows if r["bot"]["pick_ok"] is True)
    b1 = sum(1 for r in rows if r["bot"]["btts_ok"] is True)
    c1 = sum(1 for r in rows if r["bot"]["correct_score_ok"] is True)
    p2 = sum(1 for r in rows if r["bot"]["pick_ok"] is not None)
    b2 = sum(1 for r in rows if r["bot"]["btts_ok"] is not None)
    c2 = sum(1 for r in rows if r["bot"]["correct_score_ok"] is not None)
    return {
        "count": total,
        "days": days,
        "stats": {
            "1x2": {"hit": p1, "total": p2},
            "btts": {"hit": b1, "total": b2},
            "correct_score": {"hit": c1, "total": c2},
        },
        "results": rows,
    }
