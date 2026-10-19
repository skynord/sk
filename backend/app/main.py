"""FastAPI application entrypoint."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import routers
from app.config import get_settings
from app.db.base import SessionLocal, init_db
from app.ingest import daily
from app.ingest.service import seed_data_sources
from app.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

#: The built React dashboard (frontend/dist).  When present the backend serves it
#: itself, so a single `uvicorn` run gives the whole app at http://127.0.0.1:8000.
#: Override the location with the ``FRONTEND_DIST`` environment variable (used by
#: the container image, where the working directory differs).
FRONTEND_DIST = Path(
    os.environ.get("FRONTEND_DIST")
    or Path(__file__).resolve().parents[2] / "frontend" / "dist"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    init_db()
    db = SessionLocal()
    try:
        seed_data_sources(db)
    finally:
        db.close()
    logger.info("application started", extra={"providers": get_settings().provider_list})
    daily.start()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Statistical football match analysis. Probabilities come from a "
            "Dixon-Coles Poisson model with Monte-Carlo simulation and walk-forward "
            "validation. No data is invented; missing inputs are reported."
        ),
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=f"{settings.api_prefix}/redoc",
        openapi_url=f"{settings.api_prefix}/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in routers:
        app.include_router(router, prefix=settings.api_prefix)

    api_root = settings.api_prefix.strip("/")

    if not FRONTEND_DIST.is_dir():

        @app.get("/", include_in_schema=False)
        def root() -> dict:
            return {
                "app": settings.app_name,
                "docs": f"{settings.api_prefix}/docs",
                "api_prefix": settings.api_prefix,
            }
    else:
        assets = FRONTEND_DIST / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str) -> Response:
            if full_path == api_root or full_path.startswith(f"{api_root}/"):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            candidate = FRONTEND_DIST / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")

        logger.info("serving built frontend", extra={"path": str(FRONTEND_DIST)})

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"path": request.url.path})
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    return app


app = create_app()
