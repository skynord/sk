"""Tests for the Ask Sky chat: intent routing and grounded composition.

The composers are exercised with hand-built prediction dicts (the same shape the
analysis engine emits) so the assertions never depend on the model's numbers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.chat import engine


def _prediction(
    *,
    match_id: int = 1,
    home: str = "Arsenal",
    away: str = "Chelsea",
    league: str = "Premier League",
    kickoff: datetime | None = None,
    selection: str = "home",
    probability: float = 0.5,
    score: str = "1-0",
    confidence: str = "medium",
    odds: float | None = None,
    ev: float | None = None,
    positive: bool = False,
) -> dict[str, Any]:
    kickoff = kickoff or (datetime.now(tz=UTC) + timedelta(hours=6))
    prediction: dict[str, Any] = {
        "match_id": match_id,
        "kickoff_utc": kickoff.isoformat(),
        "league": league,
        "home_team": home,
        "away_team": away,
        "pick": {"market": "1x2", "selection": selection, "probability": probability},
        "predicted_score": {"score": score, "probability": 0.14},
        "expected_goals": {"home": 1.4, "away": 1.0},
        "confidence": confidence,
        "confidence_score": 0.5,
        "n_training_matches": 60,
    }
    if odds is not None:
        prediction["best_value"] = {
            "market": "1x2",
            "selection": selection,
            "decimal_odds": odds,
            "expected_value": ev,
            "is_positive_ev": positive,
        }
    return prediction


def _result(*predictions: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    result = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "window_days": engine.CHAT_DAYS,
        "considered": len(predictions) or 50,
        "predicted": len(predictions),
        "skipped": [],
        "predictions": list(predictions),
    }
    result.update(overrides)
    return result


# --------------------------------------------------------------------------
# intent detection
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("hello", "greeting"),
        ("Hi!", "greeting"),
        ("good morning", "greeting"),
        ("what can you do", "help"),
        ("who are you?", "help"),
        ("how accurate are you", "reliability"),
        ("how many matches are you predicting", "count"),
        ("Arsenal vs Chelsea", "match"),
        ("Arsenal versus Chelsea", "match"),
        ("Spurs against Chelsea", "match"),
        ("what can I bet today?", "bet_today"),
        ("any bets tonight", "bet_today"),
        ("where's the best value?", "best_value"),
        ("what's your edge", "best_value"),
        ("who's most likely to win?", "top_picks"),
        ("most confident pick", "top_picks"),
        ("what score in the Bayern game", "scores"),
        ("what's on this week", "fixtures"),
        ("", "fallback"),
    ],
)
def test_detect_intent(message: str, expected: str) -> None:
    assert engine.detect_intent(message) == expected


# --------------------------------------------------------------------------
# composition
# --------------------------------------------------------------------------


def test_simple_greeting_has_no_cards() -> None:
    reply = engine.simple_answer("greeting")
    assert reply["pending"] is False
    assert reply["cards"] == []
    assert "model" in reply["reply"].lower()


def test_bet_today_surfaces_positive_value() -> None:
    strong = _prediction(
        home="Arsenal",
        away="Chelsea",
        selection="home",
        probability=0.62,
        odds=2.1,
        ev=0.302,
        positive=True,
    )
    weak = _prediction(
        match_id=2,
        home="Bayern",
        away="Dortmund",
        league="Bundesliga",
        selection="away",
        probability=0.4,
        odds=1.8,
        ev=-0.28,
        positive=False,
    )
    reply = engine.answer("what can I bet today?", "bet_today", _result(strong, weak))
    assert reply["pending"] is False
    assert "Arsenal" in reply["reply"]
    assert reply["cards"]
    assert reply["cards"][0]["value"]["is_positive_ev"] is True
    assert reply["cards"][0]["pick_label"] == "Arsenal"


def test_bet_today_says_when_no_value() -> None:
    flat = _prediction(probability=0.4, odds=1.5, ev=-0.4, positive=False)
    reply = engine.answer("what can I bet today?", "bet_today", _result(flat))
    assert "no positive estimated value" in reply["reply"]


def test_bet_today_without_odds_is_honest() -> None:
    """No stored odds at all must not be reported as prices left unbeaten."""
    plain = _prediction(probability=0.55)
    reply = engine.answer("what can I bet today?", "bet_today", _result(plain))
    text = reply["reply"].lower()
    assert "no bookmaker prices" in text
    assert "at or above the model" not in text
    assert reply["cards"]


def test_best_value_without_odds_is_honest() -> None:
    plain = _prediction(probability=0.55)
    reply = engine.answer("where's the best value?", "best_value", _result(plain))
    text = reply["reply"].lower()
    assert "no bookmaker prices" in text
    assert "below the price" not in text
    assert reply["cards"] == []


def test_best_value_with_odds_but_no_edge_reports_odds_count() -> None:
    priced = _prediction(
        probability=0.4, odds=1.5, ev=-0.4, positive=False,
    )
    reply = engine.answer("where's the best value?", "best_value", _result(priced))
    assert "I have odds for" in reply["reply"]
    assert "no bookmaker prices" not in reply["reply"]


def test_match_lookup_by_team_names() -> None:
    fixture = _prediction(
        home="Arsenal",
        away="Chelsea",
        selection="home",
        probability=0.55,
        odds=1.9,
        ev=0.045,
        positive=True,
    )
    other = _prediction(match_id=9, home="Bayern", away="Dortmund", league="Bundesliga")
    reply = engine.answer("Arsenal vs Chelsea", "match", _result(fixture, other))
    assert "Arsenal" in reply["reply"]
    assert "Chelsea" in reply["reply"]
    assert [card["match_id"] for card in reply["cards"]] == [1]


def test_match_not_in_batch_is_reported_honestly() -> None:
    only = _prediction(home="Arsenal", away="Chelsea")
    reply = engine.answer("Barcelona vs Real Madrid", "match", _result(only))
    assert "couldn't find" in reply["reply"].lower()
    assert reply["cards"] == []


def test_scope_prefers_named_fixture_over_league() -> None:
    fixture = _prediction(home="Arsenal", away="Chelsea")
    other = _prediction(match_id=2, home="Liverpool", away="Everton")
    reply = engine.answer(
        "Arsenal vs Chelsea in the Premier League", "match", _result(fixture, other)
    )
    assert [card["match_id"] for card in reply["cards"]] == [1]


def test_reliability_without_backtests_is_honest() -> None:
    reply = engine.answer("how accurate are you", "reliability", _result())
    assert reply["pending"] is False
    assert "backtest" in reply["reply"].lower()


def test_reliability_quotes_brier_scores() -> None:
    backtests = [
        {"brier_score": 0.58, "baseline_brier": 0.63, "accuracy": 0.52, "n_eval": 380},
        {"brier_score": 0.60, "baseline_brier": 0.64, "accuracy": 0.50, "n_eval": 300},
    ]
    reply = engine.answer(
        "how accurate are you", "reliability", _result(_prediction()), backtests=backtests
    )
    assert "Brier" in reply["reply"]
    assert "0.590" in reply["reply"]


def test_count_reports_per_league_breakdown() -> None:
    a = _prediction(match_id=1, league="Premier League")
    b = _prediction(match_id=2, league="Premier League", home="Liverpool", away="Everton")
    c = _prediction(match_id=3, league="Bundesliga", home="Bayern", away="Dortmund")
    reply = engine.answer("how many matches", "count", _result(a, b, c))
    assert "3 fixtures" in reply["reply"]
    assert "Premier League: 2" in reply["reply"]


def test_empty_batch_is_reported_not_invented() -> None:
    reply = engine.answer("what can I bet today?", "bet_today", _result())
    assert reply["cards"] == []
    assert "nothing" in reply["reply"].lower() or "no predicted" in reply["reply"].lower()


# --------------------------------------------------------------------------
# HTTP surface
# --------------------------------------------------------------------------


def test_chat_greeting_endpoint(client) -> None:
    response = client.post("/api/chat", json={"message": "hello"})
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "greeting"
    assert body["pending"] is False
    assert body["reply"]


def test_chat_help_endpoint(client) -> None:
    response = client.post("/api/chat", json={"message": "what can you do?"})
    assert response.status_code == 200
    assert response.json()["intent"] == "help"


def test_chat_rejects_empty_message(client) -> None:
    response = client.post("/api/chat", json={"message": ""})
    assert response.status_code == 422


def test_chat_data_intent_uses_ready_batch(client, monkeypatch) -> None:
    fixture = _prediction(
        home="Arsenal",
        away="Chelsea",
        selection="home",
        probability=0.62,
        odds=2.1,
        ev=0.302,
        positive=True,
    )
    monkeypatch.setattr(
        engine, "batch_state", lambda: ("ready", _result(fixture), None)
    )
    response = client.post("/api/chat", json={"message": "what can I bet today?"})
    assert response.status_code == 200
    body = response.json()
    assert body["pending"] is False
    assert body["job_status"] == "ready"
    assert "Arsenal" in body["reply"]
    assert body["cards"][0]["match_id"] == 1


def test_chat_reports_pending_when_batch_running(client, monkeypatch) -> None:
    monkeypatch.setattr(engine, "batch_state", lambda: ("running", None, None))
    response = client.post("/api/chat", json={"message": "what can I bet today?"})
    assert response.status_code == 200
    body = response.json()
    assert body["pending"] is True
    assert body["intent"] == "pending"
    assert body["cards"] == []


def test_chat_reports_error_when_batch_failed(client, monkeypatch) -> None:
    monkeypatch.setattr(engine, "batch_state", lambda: ("error", None, "boom"))
    response = client.post("/api/chat", json={"message": "where's the value?"})
    assert response.status_code == 200
    body = response.json()
    assert body["pending"] is False
    assert "boom" in body["reply"]


def test_chat_status_endpoint(client, monkeypatch) -> None:
    monkeypatch.setattr(engine, "batch_state", lambda: ("running", None, None))
    response = client.get("/api/chat")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["window_days"] == engine.CHAT_DAYS
    assert "suggestions" in body


def _use_llm_settings(monkeypatch, **overrides: Any) -> None:
    """Point ``app.chat.llm`` at a fresh Settings built from ``overrides``."""
    from app.chat import llm
    from app.config import Settings

    monkeypatch.setattr(llm, "get_settings", lambda: Settings(**overrides))


def test_llm_disabled_without_api_key(monkeypatch) -> None:
    from app.chat import llm

    _use_llm_settings(monkeypatch, llm_api_key=None, llm_provider="groq")
    assert llm.is_enabled() is False


def test_groq_provider_defaults_apply(monkeypatch) -> None:
    from app.chat import llm

    _use_llm_settings(
        monkeypatch, llm_api_key="gsk_free_key", llm_provider="groq",
        llm_base_url=None, llm_model=None,
    )
    assert llm.is_enabled() is True
    assert llm._endpoint() == (
        "https://api.groq.com/openai/v1",
        llm.PROVIDERS["groq"][1][0],
    )
    assert len(llm._candidates()[1]) > 1


def test_provider_defaults_cover_free_tiers(monkeypatch) -> None:
    from app.chat import llm

    for provider in ("groq", "gemini", "openrouter", "openai"):
        _use_llm_settings(
            monkeypatch, llm_api_key="k", llm_provider=provider,
            llm_base_url=None, llm_model=None,
        )
        base, models = llm._candidates()
        assert base and models, provider


def test_phrase_falls_back_to_next_model_candidate(monkeypatch) -> None:
    from app.chat import llm

    _use_llm_settings(
        monkeypatch, llm_api_key="k", llm_provider="groq",
        llm_base_url=None, llm_model=None,
    )
    llm._WORKING_MODEL.clear()
    tried: list[str] = []

    def fake_call(base: str, model: str, question: str, facts: str) -> str | None:
        tried.append(model)
        return None if len(tried) == 1 else "rephrased"

    monkeypatch.setattr(llm, "_call", fake_call)
    assert llm.phrase("q", "f") == "rephrased"
    assert len(tried) == 2


def test_phrase_retries_the_remembered_model_first(monkeypatch) -> None:
    from app.chat import llm

    _use_llm_settings(
        monkeypatch, llm_api_key="k", llm_provider="groq",
        llm_base_url=None, llm_model=None,
    )
    llm._WORKING_MODEL.clear()
    models = llm._candidates()[1]
    llm._WORKING_MODEL["groq"] = models[-1]
    tried: list[str] = []

    def fake_call(base: str, model: str, question: str, facts: str) -> str | None:
        tried.append(model)
        return "ok"

    monkeypatch.setattr(llm, "_call", fake_call)
    assert llm.phrase("q", "f") == "ok"
    assert tried == [models[-1]]


def test_phrase_returns_none_when_every_candidate_fails(monkeypatch) -> None:
    from app.chat import llm

    _use_llm_settings(
        monkeypatch, llm_api_key="k", llm_provider="groq",
        llm_base_url=None, llm_model=None,
    )
    monkeypatch.setattr(llm, "_call", lambda *a: None)
    assert llm.phrase("q", "f") is None


def test_explicit_base_url_and_model_override_provider(monkeypatch) -> None:
    from app.chat import llm

    _use_llm_settings(
        monkeypatch, llm_api_key="k", llm_provider="groq",
        llm_base_url="https://example.test/v1", llm_model="my-model",
    )
    assert llm._endpoint() == ("https://example.test/v1", "my-model")


def test_unknown_provider_without_url_is_disabled(monkeypatch) -> None:
    from app.chat import llm

    _use_llm_settings(
        monkeypatch, llm_api_key="k", llm_provider="not-a-real-one",
        llm_base_url=None, llm_model=None,
    )
    assert llm.is_enabled() is False


def test_provider_name_is_normalised_and_blank_falls_back() -> None:
    from app.config import Settings

    assert Settings(llm_provider="  GROQ ").llm_provider == "groq"
    assert Settings(llm_provider="").llm_provider == "openai"
    assert Settings(llm_provider=None).llm_provider == "openai"
