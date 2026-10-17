"""API routers."""

from app.api.routes import analysis, health, ingest, leagues, matches, models, predictions, value

routers = [
    health.router,
    leagues.router,
    matches.router,
    analysis.router,
    models.router,
    value.router,
    predictions.router,
    ingest.router,
]

__all__ = [
    "routers",
    "analysis",
    "health",
    "ingest",
    "leagues",
    "matches",
    "models",
    "predictions",
    "value",
]
