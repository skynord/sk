"""Database engine, session factory and declarative base."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_settings = get_settings()

_connect_args: dict[str, object] = {}
if _settings.is_sqlite:
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    _settings.database_url,
    echo=_settings.sql_echo,
    future=True,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session context for scripts and background work."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Create every table. Safe to call repeatedly."""
    # Import side-effect: registers the mappers.
    from app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
