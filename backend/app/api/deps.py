"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Query

from app.config import Settings, get_settings
from app.db.base import get_db


def settings_dep() -> Settings:
    return get_settings()


class Pagination:
    def __init__(
        self,
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> None:
        self.limit = limit
        self.offset = offset


__all__ = ["get_db", "settings_dep", "Pagination", "Settings", "Generator"]
