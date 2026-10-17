"""API routers."""

from app.api.routes import (
    analysis,
    chat,
    health,
    ingest,
    leagues,
    matches,
    models,
    predictions,
    value,
)

routers = [
    health.router,
    leagues.router,
    matches.router,
    analysis.router,
    models.router,
    value.router,
    predictions.router,
    ingest.router,
    chat.router,
]

__all__ = [
    "routers",
    "analysis",
    "chat",
    "health",
    "ingest",
    "leagues",
    "matches",
    "models",
    "predictions",
    "value",
]
