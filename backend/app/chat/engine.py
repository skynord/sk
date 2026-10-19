"""Grounded question answering for the "Ask Sky" chat.

Every figure in a reply is read from the prediction batch the app has just
produced (the same batch the Predictions page runs) or from stored backtest
results.  The assistant never invents a number, never places a bet for the
user, and says so plainly when it lacks the data to answer.

With no language model configured the replies are composed here from
templates; when an OpenAI-compatible endpoint is configured the route hands the
same grounded facts to that model to phrase (see :mod:`app.chat.llm`).  The
numbers are identical either way -- the model only rewords them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.analysis import bot_jobs, identity

# The chat reuses the Predictions page's default batch so the two share one
# model fit rather than each fitting their own.
CHAT_DAYS = 14
CHAT_LIMIT = 50
CHAT_INCLUDE_LIVE = True
CHAT_KEY = f"None|{CHAT_DAYS}|{CHAT_LIMIT}|{int(CHAT_INCLUDE_LIVE)}"

DEFAULT_HOURS = 24
LIVE_GRACE_HOURS = 3
MAX_CARDS = 6

SUGGESTIONS = [
    "What can I bet today?",
    "Where's the best value?",
    "Who's most likely to win?",
    "How reliable are your predictions?",
]

DISCLAIMER = (
    "These are model probabilities from stored results, not guarantees, and the "
    "app never places a bet for you."
)

_GREETINGS = {
    "hi",
    "hello",
    "hey",
    "yo",
    "hiya",
    "sup",
    "greetings",
    "good morning",
    "good afternoon",
    "good evening",
}
_HELP_PHRASES = (
    "help",
    "what can you do",
    "what do you do",
    "how do i use",
    "how does this work",
    "who are you",
    "what are you",
)
_BET_WORDS = {
    "bet",
    "bets",
    "betting",
    "back",
    "backing",
    "wager",
    "punt",
    "acca",
    "accumulator",
    "tip",
    "tips",
    "play",
}
_VALUE_WORDS = {"value", "edge", "overpriced", "underpriced", "ev"}
_TODAY_PHRASES = (
    "today",
    "tonight",
    "this evening",
    "this afternoon",
    "next few hours",
    "right now",
    "later today",
)
_PICK_PHRASES = (
    "best pick",
    "best picks",
    "most likely",
    "strongest",
    "safest",
    "who wins",
    "who will win",
    "who s going to win",
    "favourite",
    "favorite",
    "most confident",
    "best bet",
    "sure thing",
)
_RELIABILITY_WORDS = (
    "accurate",
    "accuracy",
    "reliable",
    "trust",
    "calibration",
    "calibrated",
    "brier",
    "how good",
    "how sure",
)
_COUNT_PHRASES = ("how many", "number of", "how much")
_LEAGUE_DECORATION = {"uefa"}

#: Intents answered without touching the model batch.
SIMPLE_INTENTS = {"greeting", "help"}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _norm(text: str | None) -> str:
    return identity.normalize_name(text)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _pct(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.{digits}f}%"


def _signed_pct(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{'+' if value > 0 else ''}{value * 100:.{digits}f}%"


def _relative(when: datetime | None, now: datetime) -> str:
    if when is None:
        return ""
    seconds = (when - now).total_seconds()
    if seconds < 0:
        return "in play" if seconds > -2 * 3600 else "recently played"
    if seconds < 3600:
        return f"in {int(seconds // 60)}m"
    if seconds < 86400:
        return f"in {int(seconds // 3600)}h"
    return f"in {int(seconds // 86400)}d"


def _fixture_label(prediction: dict[str, Any], now: datetime, *, with_league: bool = True) -> str:
    head = f"{prediction['home_team']} vs {prediction['away_team']}"
    bits = []
    if with_league and prediction.get("league"):
        bits.append(prediction["league"])
    when = _relative(_parse_iso(prediction.get("kickoff_utc")), now)
    if when:
        bits.append(when)
    return f"{head} ({', '.join(bits)})" if bits else head


def _selection_label(prediction: dict[str, Any], selection: str) -> str:
    if selection == "home":
        return prediction["home_team"]
    if selection == "away":
        return prediction["away_team"]
    if selection == "draw":
        return "the draw"
    return selection


def _value(prediction: dict[str, Any]) -> dict[str, Any] | None:
    value = prediction.get("best_value")
    return value if isinstance(value, dict) else None


def _priced(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The subset a bookmaker price was actually attached to.

    Fixtures with no stored odds carry no ``best_value`` at all, which is a
    different situation from being priced and not beaten: an answer has to say
    which one it is rather than talk about prices that are not there.
    """
    return [p for p in predictions if (_value(p) or {}).get("decimal_odds")]


def _no_odds_reply(predictions: list[dict[str, Any]]) -> str | None:
    """Honest text when the comparison the user wants cannot be made at all."""
    if _priced(predictions):
        return None
    return (
        "I have no bookmaker prices stored for any of these fixtures, so I "
        "cannot compare the model against the market yet — this is not a case "
        "of the model failing to beat prices. The probabilities below are the "
        "model's own estimates; I will assess expected value once prices are "
        "imported for these fixtures."
    )


def _card(prediction: dict[str, Any]) -> dict[str, Any]:
    pick = prediction.get("pick") or {}
    selection = pick.get("selection", "")
    value = _value(prediction)
    score = prediction.get("predicted_score") or {}
    goals = prediction.get("expected_goals") or {}
    return {
        "match_id": prediction.get("match_id"),
        "kickoff_utc": prediction.get("kickoff_utc"),
        "league": prediction.get("league"),
        "home_team": prediction.get("home_team"),
        "away_team": prediction.get("away_team"),
        "pick_label": _selection_label(prediction, selection),
        "pick_selection": selection,
        "probability": pick.get("probability"),
        "score": score.get("score"),
        "expected_goals": {
            "home": goals.get("home"),
            "away": goals.get("away"),
        },
        "confidence": prediction.get("confidence"),
        "confidence_score": prediction.get("confidence_score"),
        "value": (
            {
                "market": value.get("market"),
                "selection": value.get("selection"),
                "decimal_odds": value.get("decimal_odds"),
                "expected_value": value.get("expected_value"),
                "is_positive_ev": value.get("is_positive_ev"),
            }
            if value
            else None
        ),
    }


def _positive_value(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found = [p for p in predictions if (_value(p) or {}).get("is_positive_ev")]
    return sorted(
        found, key=lambda p: (_value(p) or {}).get("expected_value", 0.0), reverse=True
    )


# --------------------------------------------------------------------------
# intent detection and entity resolution
# --------------------------------------------------------------------------


def detect_intent(text: str) -> str:
    """Classify a message.  Names are resolved later, once the batch is known."""
    normalized = _norm(text)
    if not normalized:
        return "fallback"
    words = normalized.split()
    word_set = set(words)

    if normalized in _GREETINGS or (len(words) <= 3 and words[0] in _GREETINGS):
        return "greeting"
    if any(phrase in normalized for phrase in _HELP_PHRASES):
        return "help"
    if any(word in normalized for word in _RELIABILITY_WORDS):
        return "reliability"
    if any(phrase in normalized for phrase in _COUNT_PHRASES):
        return "count"
    if " vs " in f" {normalized} " or " versus " in normalized or " against " in normalized:
        return "match"

    has_bet = bool(word_set & _BET_WORDS)
    has_today = any(phrase in normalized for phrase in _TODAY_PHRASES)
    if word_set & _VALUE_WORDS or "expected value" in normalized or "positive ev" in normalized:
        return "best_value"
    if has_bet and has_today:
        return "bet_today"
    if any(phrase in normalized for phrase in _PICK_PHRASES):
        return "top_picks"
    if "score" in normalized or "scoreline" in normalized:
        return "scores"
    if has_bet:
        return "bet_today"
    return "fixtures"


def _find_league(message: str, leagues: list[str]) -> str | None:
    normalized = _norm(message)
    message_tokens = set(normalized.split())
    best: str | None = None
    best_size = 0
    for league in leagues:
        league_norm = _norm(league)
        if not league_norm:
            continue
        if league_norm in normalized:
            return league
        core = {t for t in league_norm.split() if t not in _LEAGUE_DECORATION}
        if core and core <= message_tokens and len(core) > best_size:
            best, best_size = league, len(core)
    return best


def _find_teams(message: str, names: list[str]) -> list[str]:
    normalized = _norm(message)
    if not normalized:
        return []
    message_tokens = set(normalized.split())
    hits: list[tuple[int, str, str, set[str]]] = []
    for name in names:
        name_norm = _norm(name)
        if not name_norm:
            continue
        tokens = set(name_norm.split())
        if name_norm in normalized:
            hits.append((normalized.find(name_norm), name_norm, name, tokens))
        elif tokens and tokens <= message_tokens:
            first = min((normalized.find(t) for t in tokens if t in normalized), default=-1)
            hits.append((first if first >= 0 else 10**6, name_norm, name, tokens))

    # A shorter hit that is contained in a longer one ("United" inside
    # "Manchester United") is dropped in favour of the longer name.
    pruned = [h for h in hits if not any(h[3] < other[3] for other in hits)]
    pruned.sort(key=lambda h: h[0])
    out: list[str] = []
    seen: set[str] = set()
    for _, name_norm, name, _ in pruned:
        if name_norm not in seen:
            seen.add(name_norm)
            out.append(name)
    return out


def _scope(
    message: str, predictions: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], str, list[str], str | None]:
    """Narrow the batch to what the message names.

    Returns ``(subset, label, teams_found, league_found)``.
    """
    leagues = sorted({p.get("league") for p in predictions if p.get("league")})
    names: list[str] = []
    for prediction in predictions:
        for key in ("home_team", "away_team"):
            name = prediction.get(key)
            if name and name not in names:
                names.append(name)

    league = _find_league(message, leagues)
    teams = _find_teams(message, names)

    # A named fixture wins over a named competition, so "Arsenal vs Chelsea in
    # the Premier League" resolves to that one match rather than the league list.
    if len(teams) >= 2:
        first, second = teams[0], teams[1]
        head_to_head = [
            p
            for p in predictions
            if {p.get("home_team"), p.get("away_team")} == {first, second}
        ]
        return head_to_head, f"{first} vs {second}", teams, None

    if league is not None:
        subset = [p for p in predictions if p.get("league") == league]
        return subset, f"in the {league}", teams, league

    if teams:
        chosen = set(teams)
        subset = [
            p
            for p in predictions
            if p.get("home_team") in chosen or p.get("away_team") in chosen
        ]
        return subset, f"for {', '.join(teams)}", teams, None

    return predictions, "across all competitions", [], None


# --------------------------------------------------------------------------
# answers
# --------------------------------------------------------------------------


def simple_answer(intent: str) -> dict[str, Any]:
    if intent == "greeting":
        reply = (
            "Hi. I answer questions from the app's own model output — the same "
            "probabilities the Predictions page shows. Try asking what the model "
            "fancies today, where it disagrees with the stored odds, or how a "
            "particular fixture looks."
        )
    else:
        reply = (
            "I read the model's predictions and the stored odds and answer in "
            "plain language. Things I can do:\n"
            "• What can I bet today? — how the model reads the next 24 hours\n"
            "• Where's the best value? — where its probability beats the price\n"
            "• Who's most likely to win? — the strongest picks in the window\n"
            "• Tell me about Arsenal vs Chelsea — one fixture, in detail\n"
            "• How reliable are your predictions? — the walk-forward backtest\n\n"
            f"{DISCLAIMER}"
        )
    return {
        "reply": reply,
        "intent": intent,
        "cards": [],
        "suggestions": SUGGESTIONS,
        "pending": False,
        "engine": "rules",
    }


def _no_fixture(message: str, window_days: int) -> dict[str, Any]:
    reply = (
        f"I couldn't find a fixture matching that in the current batch. The model "
        f"covers the next {window_days} days of stored fixtures, so a match further "
        f"out — or one not imported yet — won't appear. Try a team already in the "
        f"window, or ask what's on today."
    )
    return {
        "reply": reply,
        "intent": "match",
        "cards": [],
        "suggestions": SUGGESTIONS,
        "pending": False,
        "engine": "rules",
    }


def _value_lines(predictions: list[dict[str, Any]], now: datetime) -> list[str]:
    lines = []
    for prediction in predictions:
        value = _value(prediction) or {}
        odds = value.get("decimal_odds")
        ev = value.get("expected_value")
        # EV = p * odds - 1, so the model's probability is (EV + 1) / odds.
        model_p = (ev + 1.0) / odds if odds and ev is not None else None
        label = _selection_label(prediction, value.get("selection", ""))
        lines.append(
            f"• {_fixture_label(prediction, now)} — {label}: model {_pct(model_p)}, "
            f"price {odds} ({_pct(1.0 / odds if odds else None)} implied), "
            f"EV {_signed_pct(ev)}"
        )
    return lines


def _answer_bet_today(
    predictions: list[dict[str, Any]], scope: str, now: datetime, hours: int
) -> tuple[str, list[dict[str, Any]]]:
    window = [
        p
        for p in predictions
        if (kickoff := _parse_iso(p.get("kickoff_utc"))) is not None
        and now - timedelta(hours=LIVE_GRACE_HOURS) <= kickoff
        and kickoff <= now + timedelta(hours=hours)
    ]
    if not window:
        window = predictions
        span = "the whole window"
    else:
        span = f"the next {hours} hours"

    if not window:
        return (
            "The model has no predicted fixtures to show yet.",
            [],
        )

    parts = [
        f"I have model output for {len(window)} fixture"
        f"{'s' if len(window) != 1 else ''} in {span}{'' if scope.endswith('competitions') else ' ' + scope}."
    ]

    positive = _positive_value(window)
    no_odds = _no_odds_reply(window)
    if positive:
        parts.append("")
        parts.append("Where the model's probability sits above the stored price:")
        parts.extend(_value_lines(positive[:4], now))
        if len(positive) > 4:
            parts.append(f"…and {len(positive) - 4} more with a smaller edge.")
    elif no_odds:
        parts.append("")
        parts.append(no_odds)
    else:
        priced = _priced(window)
        parts.append("")
        parts.append(
            f"The model does not beat any of the {len(priced)} stored price"
            f"{'s' if len(priced) != 1 else ''} in this window — the quoted "
            f"prices sit at or above the model's probability, so there is no "
            f"positive estimated value right now."
        )

    likely = sorted(
        window, key=lambda p: (p.get("pick") or {}).get("probability", 0.0), reverse=True
    )[:4]
    if likely:
        parts.append("")
        parts.append("The most likely single outcomes:")
        for prediction in likely:
            pick = prediction.get("pick") or {}
            score = prediction.get("predicted_score") or {}
            parts.append(
                f"• {_fixture_label(prediction, now)} — "
                f"{_selection_label(prediction, pick.get('selection', ''))} "
                f"{_pct(pick.get('probability'))}"
                + (f", most likely score {score.get('score')}" if score.get("score") else "")
            )

    parts.append("")
    parts.append(DISCLAIMER)

    cards = [
        _card(p)
        for p in (positive[:MAX_CARDS] if positive else likely[:MAX_CARDS])
    ]
    return "\n".join(parts), cards


def _answer_best_value(
    predictions: list[dict[str, Any]], scope: str, now: datetime
) -> tuple[str, list[dict[str, Any]]]:
    positive = _positive_value(predictions)
    if not positive:
        no_odds = _no_odds_reply(predictions)
        if no_odds:
            return no_odds, []
        priced = _priced(predictions)
        reply = (
            f"I found no positive estimated value {scope}. The model's "
            f"probability is below the price on all {len(priced)} fixture"
            f"{'s' if len(priced) != 1 else ''} I have odds for — the value is "
            f"with the market, not against it. An edge can appear as odds move, "
            f"so it is worth asking again later."
        )
        return reply, []

    lines = _value_lines(positive[:MAX_CARDS], now)
    reply = "\n".join(
        [
            f"Against the stored prices, the model's clearest edges {scope}:",
            "",
            *lines,
            "",
            "A large gap usually means the price is stale or from a thin market, so "
            "treat the biggest numbers with the most suspicion.",
            "",
            DISCLAIMER,
        ]
    )
    return reply, [_card(p) for p in positive[:MAX_CARDS]]


def _answer_top_picks(
    predictions: list[dict[str, Any]], scope: str, now: datetime
) -> tuple[str, list[dict[str, Any]]]:
    ranked = sorted(
        predictions,
        key=lambda p: (p.get("pick") or {}).get("probability", 0.0),
        reverse=True,
    )[:MAX_CARDS]
    if not ranked:
        return "I have no predicted fixtures to rank yet.", []

    lines = []
    for prediction in ranked:
        pick = prediction.get("pick") or {}
        lines.append(
            f"• {_fixture_label(prediction, now)} — "
            f"{_selection_label(prediction, pick.get('selection', ''))} "
            f"{_pct(pick.get('probability'))} "
            f"({prediction.get('confidence', 'n/a')} confidence)"
        )
    reply = "\n".join(
        [
            f"The model's most confident calls {scope}:",
            "",
            *lines,
            "",
            "Confidence here is about how clear-cut the estimate is, not how likely "
            "it is to be right.",
            "",
            DISCLAIMER,
        ]
    )
    return reply, [_card(p) for p in ranked]


def _answer_scores(
    predictions: list[dict[str, Any]], scope: str, now: datetime
) -> tuple[str, list[dict[str, Any]]]:
    ranked = sorted(
        predictions,
        key=lambda p: (p.get("predicted_score") or {}).get("probability", 0.0),
        reverse=True,
    )[:MAX_CARDS]
    if not ranked:
        return "I have no predicted scorelines yet.", []
    lines = []
    for prediction in ranked:
        score = prediction.get("predicted_score") or {}
        goals = prediction.get("expected_goals") or {}
        lines.append(
            f"• {_fixture_label(prediction, now)} — {score.get('score')} "
            f"({_pct(score.get('probability'))} for that exact score; "
            f"expected goals {goals.get('home'):.2f}–{goals.get('away'):.2f})"
        )
    reply = "\n".join(
        [
            f"Predicted scorelines {scope}:",
            "",
            *lines,
            "",
            "A single scoreline is rarely likely on its own — the exact-score "
            "probability is low even when one side is a clear favourite.",
            "",
            DISCLAIMER,
        ]
    )
    return reply, [_card(p) for p in ranked]


def _answer_match(
    predictions: list[dict[str, Any]], scope: str, now: datetime
) -> tuple[str, list[dict[str, Any]]]:
    if not predictions:
        return "", []
    prediction = predictions[0]
    one_x_two = (prediction.get("probabilities") or {}).get("one_x_two", {})
    pick = prediction.get("pick") or {}
    score = prediction.get("predicted_score") or {}
    goals = prediction.get("expected_goals") or {}
    value = _value(prediction)

    lines = [
        f"{_fixture_label(prediction, now)}",
        "",
        f"Model probabilities — {prediction['home_team']} "
        f"{_pct(one_x_two.get('home'))}, draw {_pct(one_x_two.get('draw'))}, "
        f"{prediction['away_team']} {_pct(one_x_two.get('away'))}.",
        f"Most likely outcome: {_selection_label(prediction, pick.get('selection', ''))} "
        f"({_pct(pick.get('probability'))}).",
        f"Expected goals {goals.get('home'):.2f}–{goals.get('away'):.2f}; "
        f"most likely scoreline {score.get('score')} ({_pct(score.get('probability'))}).",
        f"Confidence: {prediction.get('confidence', 'n/a')}"
        + (
            f" ({prediction.get('confidence_score')}/100)"
            if prediction.get("confidence_score") is not None
            else ""
        )
        + f", from {prediction.get('n_training_matches', 0)} training matches.",
    ]
    if value:
        lines.append(
            f"Against the stored price, the model's best angle is "
            f"{value.get('market')}:{value.get('selection')} at {value.get('decimal_odds')} "
            f"(EV {_signed_pct(value.get('expected_value'))})."
        )
    else:
        lines.append("No stored odds for this fixture, so no value is shown.")
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines), [_card(prediction)]


def _answer_count(
    result: dict[str, Any], predictions: list[dict[str, Any]], scope: str, now: datetime
) -> tuple[str, list[dict[str, Any]]]:
    by_league: dict[str, int] = {}
    for prediction in predictions:
        league = prediction.get("league") or "Unknown"
        by_league[league] = by_league.get(league, 0) + 1
    ranked = sorted(by_league.items(), key=lambda kv: kv[1], reverse=True)
    considered = result.get("considered")
    predicted = result.get("predicted", len(predictions))
    skipped = result.get("skipped") or []
    window_days = result.get("window_days", CHAT_DAYS)

    lines = [
        f"The current batch covers {predicted} fixture"
        f"{'s' if predicted != 1 else ''} over the next {window_days} days{scope}.",
    ]
    if considered is not None and considered != predicted:
        lines.append(
            f"It considered {considered} in total; {len(skipped)} were skipped for "
            f"too little stored history."
        )
    if ranked:
        lines.append("")
        lines.extend(f"• {name}: {count}" for name, count in ranked)
    return "\n".join(lines), []


def _answer_reliability(
    result: dict[str, Any],
    predictions: list[dict[str, Any]],
    backtests: list[dict[str, Any]] | None,
) -> tuple[str, list[dict[str, Any]]]:
    lines: list[str] = []
    if backtests:
        briers = [b["brier_score"] for b in backtests if b.get("brier_score") is not None]
        baselines = [
            b["baseline_brier"] for b in backtests if b.get("baseline_brier") is not None
        ]
        accuracies = [b["accuracy"] for b in backtests if b.get("accuracy") is not None]
        if briers:
            mean_brier = sum(briers) / len(briers)
            baseline = sum(baselines) / len(baselines) if baselines else None
            lines.append(
                f"On the {len(briers)} league backtest"
                f"{'s' if len(briers) != 1 else ''} stored, the model's 1X2 "
                f"predictions average a Brier score of {mean_brier:.3f} "
                + (
                    f"against {baseline:.3f} for a base-rate baseline — so it is "
                    f"better than guessing, but only by a modest margin."
                    if baseline is not None
                    else "."
                )
            )
            if accuracies:
                lines.append(
                    f"It picks the most likely outcome correctly "
                    f"{sum(accuracies) / len(accuracies) * 100:.1f}% of the time on average."
                )
    if not lines:
        lines.append(
            "I don't have a stored backtest to quote, so I can't give you a "
            "measured accuracy figure."
        )

    levels: dict[str, int] = {}
    for prediction in predictions:
        level = prediction.get("confidence") or "unknown"
        levels[level] = levels.get(level, 0) + 1
    if levels:
        ordered = sorted(levels.items(), key=lambda kv: kv[1], reverse=True)
        spread = ", ".join(f"{count} {name}" for name, count in ordered)
        lines.append("")
        lines.append(f"In the current batch of {len(predictions)} fixtures, confidence is spread: {spread}.")

    lines.append("")
    lines.append(
        "These are walk-forward results on stored history: the model is checked on "
        "matches it was not trained on, which is the honest way to measure it. "
        "Nothing here is a guarantee about a single match."
    )
    return "\n".join(lines), []


def _answer_fixtures(
    predictions: list[dict[str, Any]], scope: str, now: datetime
) -> tuple[str, list[dict[str, Any]]]:
    ordered = sorted(
        predictions,
        key=lambda p: _parse_iso(p.get("kickoff_utc")) or now,
    )
    if not ordered:
        return "I have no predicted fixtures in the window.", []
    shown = ordered[:MAX_CARDS]
    lines = [f"Coming up {scope}:", ""]
    for prediction in shown:
        pick = prediction.get("pick") or {}
        score = prediction.get("predicted_score") or {}
        lines.append(
            f"• {_fixture_label(prediction, now)} — "
            f"{_selection_label(prediction, pick.get('selection', ''))} "
            f"{_pct(pick.get('probability'))}"
            + (f", score {score.get('score')}" if score.get("score") else "")
        )
    if len(ordered) > len(shown):
        lines.append(f"…and {len(ordered) - len(shown)} more.")
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines), [_card(p) for p in shown]


def answer(
    message: str,
    intent: str,
    result: dict[str, Any],
    *,
    backtests: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compose a grounded reply for one message."""
    now = now or datetime.now(tz=UTC)
    predictions = result.get("predictions") or []
    window_days = result.get("window_days", CHAT_DAYS)

    if intent in SIMPLE_INTENTS:
        return simple_answer(intent)

    scoped, scope, teams, league = _scope(message, predictions)

    # Reliability is answerable from the stored backtest alone, so it survives an
    # empty batch; every other intent needs predicted fixtures to read from.
    if not predictions and intent != "reliability":
        return {
            "reply": (
                "The batch finished but returned no predicted fixtures, so there is "
                "nothing for me to read yet."
            ),
            "intent": intent,
            "cards": [],
            "suggestions": SUGGESTIONS,
            "pending": False,
            "engine": "rules",
        }
    # A named fixture the model has not priced yet is reported honestly rather
    # than answered with an unrelated match.
    if intent == "match" and len(teams) < 2:
        return _no_fixture(message, window_days)
    if not scoped and intent != "reliability":
        return _no_fixture(message, window_days)

    if intent == "match":
        reply, cards = _answer_match(scoped, scope, now)
        follow = [
            "Where's the best value?",
            "Who's most likely to win?",
            "How reliable are your predictions?",
        ]
    elif intent == "bet_today":
        reply, cards = _answer_bet_today(scoped, scope, now, DEFAULT_HOURS)
        follow = [
            "Where's the best value?",
            "Who's most likely to win?",
            "How reliable are your predictions?",
        ]
    elif intent == "best_value":
        reply, cards = _answer_best_value(scoped, scope, now)
        follow = [
            "What can I bet today?",
            "Who's most likely to win?",
            "How reliable are your predictions?",
        ]
    elif intent == "top_picks":
        reply, cards = _answer_top_picks(scoped, scope, now)
        follow = ["What can I bet today?", "Where's the best value?", "Any underdogs?"]
    elif intent == "scores":
        reply, cards = _answer_scores(scoped, scope, now)
        follow = ["Who's most likely to win?", "Where's the best value?"]
    elif intent == "count":
        reply, cards = _answer_count(result, scoped, scope, now)
        follow = SUGGESTIONS
    elif intent == "reliability":
        reply, cards = _answer_reliability(result, scoped, backtests)
        follow = SUGGESTIONS
    elif intent == "fallback":
        reply, cards = _answer_fixtures(scoped, scope, now)
        follow = SUGGESTIONS
    else:
        reply, cards = _answer_fixtures(scoped, scope, now)
        follow = SUGGESTIONS

    if not reply and not cards:
        return _no_fixture(message, window_days)

    return {
        "reply": reply,
        "intent": intent,
        "cards": cards[:MAX_CARDS],
        "suggestions": follow,
        "pending": False,
        "engine": "rules",
    }


# --------------------------------------------------------------------------
# batch access
# --------------------------------------------------------------------------


def _ensure_batch() -> dict[str, Any]:
    return bot_jobs.ensure(
        CHAT_KEY,
        {
            "league_id": None,
            "days_ahead": CHAT_DAYS,
            "limit": CHAT_LIMIT,
            "include_live": CHAT_INCLUDE_LIVE,
            "persist": False,
        },
    )


def batch_state() -> tuple[str, dict[str, Any] | None, str | None]:
    """``(status, result, error)`` for the shared batch.

    ``status`` is ``"ready"`` only when the finished batch is this chat's own
    default batch; a run for another set of filters reports ``"running"``.
    """
    snapshot = _ensure_batch()
    if snapshot["status"] == "ready" and snapshot["key"] == CHAT_KEY:
        return "ready", snapshot["result"] or {}, None
    if snapshot["status"] == "error":
        return "error", None, snapshot["error"]
    return "running", None, None


def pending_answer() -> dict[str, Any]:
    return {
        "reply": (
            "I'm fitting the model over the next "
            f"{CHAT_DAYS} days right now — the first run takes a minute or two on "
            "this server, and later questions are instant. Ask me again in a moment."
        ),
        "intent": "pending",
        "cards": [],
        "suggestions": SUGGESTIONS,
        "pending": True,
        "engine": "rules",
    }


def status_payload() -> dict[str, Any]:
    status, result, error = batch_state()
    payload: dict[str, Any] = {
        "status": status,
        "error": error,
        "suggestions": SUGGESTIONS,
        "window_days": CHAT_DAYS,
    }
    if status == "ready" and result is not None:
        payload["prediction_count"] = len(result.get("predictions") or [])
        payload["generated_at"] = result.get("generated_at")
    return payload
