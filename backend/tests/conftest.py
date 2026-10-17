"""Pytest configuration and fixtures.

Environment variables are set *before* any ``app`` import so that the settings
object and SQLAlchemy engine are built against a throwaway database and cache.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="fa_test_"))
os.environ["FA_DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["FA_CACHE_DIR"] = (_TMP / "cache").as_posix()
os.environ["FA_RAW_DATA_DIR"] = (_TMP / "raw").as_posix()
os.environ["FA_ENABLED_PROVIDERS"] = "sample,manual,football_data_co_uk"
os.environ["FA_MONTE_CARLO_SIMULATIONS"] = "2000"
os.environ["FA_MIN_MATCHES_FOR_MODEL"] = "20"
# Blank out any real API keys from a local .env so tests are deterministic.
os.environ["FA_API_FOOTBALL_KEY"] = ""
os.environ["FA_FOOTBALL_DATA_ORG_KEY"] = ""
os.environ["FA_SPORTMONKS_TOKEN"] = ""
os.environ["FA_LIVE_PROVIDER"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.base import SessionLocal, init_db  # noqa: E402
from app.ingest.service import ingest_provider, seed_data_sources  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def seeded() -> Path:
    """Initialise the schema once and load the SYNTHETIC sample season."""
    init_db()
    with SessionLocal() as db:
        seed_data_sources(db)
        ingest_provider(db, "sample")
    return _TMP


@pytest.fixture()
def db(seeded):
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def client(seeded):
    with TestClient(app) as test_client:
        yield test_client
