"""Ask Sky chat endpoint."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.chat import engine, llm
from app.db.models import BacktestResult

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)


def _latest_backtests(db: Session) -> list[dict[str, Any]]:
    """The newest stored 1X2 backtest per competition."""
    rows = (
        db.execute(
            select(BacktestResult)
            .order_by(BacktestResult.created_at.desc())
            .limit(300)
        )
        .scalars()
        .all()
    )
    chosen: dict[int | None, BacktestResult] = {}
    for row in rows:
        if row.market != "1x2":
            continue
        if row.league_id in chosen:
            continue
        chosen[row.league_id] = row
    return [
        {
            "brier_score": row.brier_score,
            "baseline_brier": row.baseline_brier,
            "accuracy": row.accuracy,
            "n_eval": row.n_eval,
        }
        for row in chosen.values()
    ]


def _facts(reply: dict[str, Any]) -> str:
    """A compact, literal dump of the numbers behind an answer."""
    lines = [reply.get("reply", ""), "", "Fixture data:"]
    for card in reply.get("cards") or []:
        lines.append(json.dumps(card, default=str))
    return "\n".join(lines)


@router.get("/chat")
def chat_status() -> dict:
    """Open the shared model batch and report what the assistant can answer."""
    return engine.status_payload()


@router.post("/chat")
def chat(request: ChatRequest, db: Session = Depends(get_db)) -> dict:
    """Answer one question from the model's own output.

    Answers are grounded in the current prediction batch.  When no batch is
    ready yet the run is started and the reply says so rather than guessing.
    """
    message = request.message.strip()
    intent = engine.detect_intent(message)

    if intent in engine.SIMPLE_INTENTS:
        return engine.simple_answer(intent)

    status, result, error = engine.batch_state()
    if status == "error":
        return {
            "reply": f"I couldn't run the model: {error}. Ask again in a moment.",
            "intent": intent,
            "cards": [],
            "suggestions": engine.SUGGESTIONS,
            "pending": False,
            "job_status": "error",
            "engine": "rules",
        }
    if status != "ready" or result is None:
        payload = engine.pending_answer()
        payload["job_status"] = status
        return payload

    backtests = _latest_backtests(db) if intent == "reliability" else None
    reply = engine.answer(message, intent, result, backtests=backtests)

    if not reply.get("pending") and llm.is_enabled():
        phrased = llm.phrase(message, _facts(reply))
        if phrased:
            reply["reply"] = phrased
            reply["engine"] = "llm"

    reply["job_status"] = "ready"
    reply["generated_at"] = result.get("generated_at")
    return reply
