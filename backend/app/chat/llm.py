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

# Free-tier-friendly, OpenAI-compatible chat endpoints.  A user only has to
# paste a free API key for one of these (plus the provider name) -- no install,
# no local model, and every other setting falls back to a working default.
PROVIDERS: dict[str, tuple[str, str]] = {
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash"),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
}

SYSTEM_PROMPT = (
    "You are Sky, a football analysis assistant. You are given a question and a "
    "set of FACTS computed by a statistical model. Answer using only those facts. "
    "Never invent a number, team, fixture or price that is not in the facts. Never "
    "advise the user to place a bet and never promise a profit; you may say the app "
    "does not place bets. Keep it to a few short sentences. If the facts do not "
    "answer the question, say so plainly."
)


def _endpoint() -> tuple[str | None, str | None]:
    """Effective ``(base_url, model)``: explicit settings win, else the provider default."""
    settings = get_settings()
    default_url, default_model = PROVIDERS.get(settings.llm_provider, ("", ""))
    base = settings.llm_base_url or default_url or None
    model = settings.llm_model or default_model or None
    return base, model


def is_enabled() -> bool:
    """True when a language-model endpoint is fully configured."""
    base, model = _endpoint()
    return bool(get_settings().llm_api_key) and bool(base) and bool(model)


def phrase(question: str, facts: str) -> str | None:
    """Ask the configured model to reword the facts, or ``None`` on any trouble."""
    settings = get_settings()
    base, model = _endpoint()
    if not settings.llm_api_key or not base or not model:
        return None
    url = base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
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
