"""Optional language-model phrasing for the chat.

The numbers never come from the model.  The route computes the grounded facts
first and the model is only asked to put those facts into sentences, so what it
"knows" about football cannot leak into an answer.  With no endpoint configured
the deterministic composer in :mod:`app.chat.engine` is used instead.

Free-tier providers retire and rotate model ids fairly often, and which models
an account can reach differs between accounts, so each provider carries an
ordered list of candidates rather than a single name: the first that answers is
remembered for the life of the process.
"""

from __future__ import annotations

import httpx

from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger(__name__)

# Free-tier-friendly, OpenAI-compatible chat endpoints.  A user only has to
# paste a free API key for one of these (plus the provider name) -- no install,
# no local model, and every other setting falls back to a working default.
PROVIDERS: dict[str, tuple[str, list[str]]] = {
    "groq": (
        "https://api.groq.com/openai/v1",
        [
            "qwen/qwen3.8-27b",
            "openai/gpt-oss-120b",
            "llama-3.3-70b-versatile",
        ],
    ),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai",
        ["gemini-2.0-flash", "gemini-2.5-flash"],
    ),
    "openrouter": (
        "https://openrouter.ai/api/v1",
        ["meta-llama/llama-3.3-70b-instruct:free", "qwen/qwen3-32b:free"],
    ),
    "openai": ("https://api.openai.com/v1", ["gpt-4o-mini", "gpt-4.1-mini"]),
}

#: Model id each provider last answered with, so a later call does not pay the
#: cost of rediscovering which candidate still exists on this account.
_WORKING_MODEL: dict[str, str] = {}

#: Room for a reasoning model's own reasoning before its visible answer.
MAX_TOKENS = 800

SYSTEM_PROMPT = (
    "You are Sky, a football analysis assistant. You are given a question and a "
    "set of FACTS computed by a statistical model. Answer using only those facts. "
    "Never invent a number, team, fixture or price that is not in the facts. Never "
    "advise the user to place a bet and never promise a profit; you may say the app "
    "does not place bets. Keep it to a few short sentences. If the facts do not "
    "answer the question, say so plainly."
)


def _candidates() -> tuple[str | None, list[str]]:
    """Effective ``(base_url, models)``: explicit settings win, else the provider's."""
    settings = get_settings()
    default_url, default_models = PROVIDERS.get(settings.llm_provider, ("", []))
    base = settings.llm_base_url or default_url or None
    models = [settings.llm_model] if settings.llm_model else list(default_models)
    if not base or not models:
        return None, []
    return base, models


def _endpoint() -> tuple[str | None, str | None]:
    """The primary ``(base_url, model)`` that a call would try first."""
    base, models = _candidates()
    return base, (models[0] if models else None)


def is_enabled() -> bool:
    """True when a language-model endpoint is fully configured."""
    base, models = _candidates()
    return bool(get_settings().llm_api_key) and bool(base) and bool(models)


def _call(
    base: str,
    model: str,
    question: str,
    facts: str,
) -> str | None:
    """One completion attempt; ``None`` on any trouble or an empty answer."""
    settings = get_settings()
    url = base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Question: {question}\n\nFACTS:\n{facts}"},
        ],
        "temperature": 0.2,
        "max_tokens": MAX_TOKENS,
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    try:
        with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "llm phrasing failed", extra={"model": model, "error": str(exc)}
        )
        return None
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return text.strip() if isinstance(text, str) and text.strip() else None


def phrase(question: str, facts: str) -> str | None:
    """Ask the configured model to reword the facts, or ``None`` on any trouble."""
    settings = get_settings()
    base, models = _candidates()
    if not settings.llm_api_key or not base or not models:
        return None
    provider = settings.llm_provider
    remembered = _WORKING_MODEL.get(provider)
    order = models if remembered not in models else [remembered]
    order += [model for model in models if model != remembered]
    for model in order:
        text = _call(base, model, question, facts)
        if text:
            _WORKING_MODEL[provider] = model
            return text
    return None
