"""Optional language-model phrasing for the chat.

The numbers never come from the model.  The route computes the grounded facts
first and the model is only asked to put those facts into sentences, so what it
"knows" about football cannot leak into an answer.  With no endpoint configured
the deterministic composer in :mod:`app.chat.engine` is used instead.
"""

from __future__ import annotations

import httpx

from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = (
    "You are Sky, a football analysis assistant. You are given a question and a "
    "set of FACTS computed by a statistical model. Answer using only those facts. "
    "Never invent a number, team, fixture or price that is not in the facts. Never "
    "advise the user to place a bet and never promise a profit; you may say the app "
    "does not place bets. Keep it to a few short sentences. If the facts do not "
    "answer the question, say so plainly."
)


def is_enabled() -> bool:
    """True when a language-model endpoint is configured."""
    return bool(get_settings().llm_api_key)


def phrase(question: str, facts: str) -> str | None:
    """Ask the configured model to reword the facts, or ``None`` on any trouble."""
    settings = get_settings()
    if not settings.llm_api_key:
        return None
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Question: {question}\n\nFACTS:\n{facts}"},
        ],
        "temperature": 0.2,
        "max_tokens": 400,
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    try:
        with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("llm phrasing failed", extra={"error": str(exc)})
        return None
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return text.strip() if isinstance(text, str) and text.strip() else None
