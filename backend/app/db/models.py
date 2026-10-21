"""ORM models.

Every dataset we ingest carries provenance (see :class:`ProviderRecord`) so an
analysis can always be traced back to the source URL, retrieval time and data
freshness.  We never store invented values: missing metrics are ``NULL``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------
class League(TimestampMixin, Base):
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Map of provider name -> provider specific id for cross-referencing.
    provider_refs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    seasons: Mapped[list[Season]] = relationship(back_populates="league")
    teams: Mapped[list[Team]] = relationship(back_populates="league")


class Season(TimestampMixin, Base):
    __tablename__ = "seasons"
    __table_args__ = (UniqueConstraint("league_id", "label", name="uq_season_label"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    label: Mapped[str] = mapped_column(String(40))  # e.g. "2024/25"
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)

    league: Mapped[League] = relationship(back_populates="seasons")
    matches: Mapped[list[Match]] = relationship(back_populates="season")


class Team(TimestampMixin, Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    short_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    league_id: Mapped[int | None] = mapped_column(
        ForeignKey("leagues.id"), nullable=True, index=True
    )
    provider_refs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    league: Mapped[League | None] = relationship(back_populates="teams")


class Match(TimestampMixin, Base):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("season_id", "home_team_id", "away_team_id", "kickoff_utc",
                         name="uq_match_fixture"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    season_id: Mapped[int | None] = mapped_column(
        ForeignKey("seasons.id"), nullable=True, index=True
    )
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(24), default="scheduled", index=True)
    round_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    venue: Mapped[str | None] = mapped_column(String(200), nullable=True)
    referee: Mapped[str | None] = mapped_column(String(120), nullable=True)

    home_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ht_home_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ht_away_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)

    source_provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)

    season: Mapped[Season | None] = relationship(back_populates="matches")
    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])
    stats: Mapped[list[MatchStat]] = relationship(back_populates="match")
    odds: Mapped[list[MatchOdd]] = relationship(back_populates="match")


class MatchStat(TimestampMixin, Base):
    """Team-level statistics for a single match.  All metrics are optional."""

    __tablename__ = "match_stats"
    __table_args__ = (UniqueConstraint("match_id", "team_id", name="uq_stat_match_team"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    is_home: Mapped[bool] = mapped_column(Boolean, default=False)

    goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    xg: Mapped[float | None] = mapped_column(Float, nullable=True)
    shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots_on_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    big_chances: Mapped[int | None] = mapped_column(Integer, nullable=True)
    possession: Mapped[float | None] = mapped_column(Float, nullable=True)
    passes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pass_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    corners: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fouls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    yellow_cards: Mapped[int | None] = mapped_column(Integer, nullable=True)
    red_cards: Mapped[int | None] = mapped_column(Integer, nullable=True)

    source_provider: Mapped[str | None] = mapped_column(String(60), nullable=True)

    match: Mapped[Match] = relationship(back_populates="stats")


class MatchOdd(TimestampMixin, Base):
    """A single quoted price for a market selection."""

    __tablename__ = "match_odds"
    __table_args__ = (
        UniqueConstraint(
            "match_id", "bookmaker", "market", "selection", "is_closing",
            name="uq_odd",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    bookmaker: Mapped[str] = mapped_column(String(80), default="manual")
    market: Mapped[str] = mapped_column(String(60))  # 1x2, o/u_2.5, btts, ah, ...
    selection: Mapped[str] = mapped_column(String(60))  # home, draw, away, over, ...
    decimal_odds: Mapped[float] = mapped_column(Float)
    is_closing: Mapped[bool] = mapped_column(Boolean, default=False)
    line: Mapped[float | None] = mapped_column(Float, nullable=True)  # e.g. 2.5
    source_provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    match: Mapped[Match] = relationship(back_populates="odds")


# --------------------------------------------------------------------------
# Provenance, source registry and ingestion bookkeeping
# --------------------------------------------------------------------------
class ProviderRecord(TimestampMixin, Base):
    """Provenance row: links a local entity to where its data came from."""

    __tablename__ = "provider_records"
    __table_args__ = (
        UniqueConstraint(
            "provider", "entity_type", "entity_id", name="uq_provider_entity"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str] = mapped_column(String(40))  # league/team/match/stat/odds
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    source_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    competition: Mapped[str | None] = mapped_column(String(200), nullable=True)
    season: Mapped[str | None] = mapped_column(String(40), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    data_as_of: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checksum: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_estimate: Mapped[bool] = mapped_column(Boolean, default=False)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class DataSource(TimestampMixin, Base):
    """Registry describing each configured data source and its status."""

    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # api | csv | html | manual
    base_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    requires_key: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    license_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class IngestRun(Base):
    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(60), index=True)
    scope: Mapped[str | None] = mapped_column(String(200), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="running")
    records_in: Mapped[int] = mapped_column(Integer, default=0)
    records_upserted: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


# --------------------------------------------------------------------------
# Model runs, predictions and backtests
# --------------------------------------------------------------------------
class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_name: Mapped[str] = mapped_column(String(80))
    model_version: Mapped[str] = mapped_column(String(40))
    code_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    league_id: Mapped[int | None] = mapped_column(
        ForeignKey("leagues.id"), nullable=True
    )
    season_id: Mapped[int | None] = mapped_column(
        ForeignKey("seasons.id"), nullable=True
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    training_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    training_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    n_matches: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_run_id: Mapped[int] = mapped_column(ForeignKey("model_runs.id"), index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    market: Mapped[str] = mapped_column(String(60))
    selection: Mapped[str] = mapped_column(String(60))
    probability: Mapped[float] = mapped_column(Float)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class PredictionBatch(Base):
    """A whole window's predictions, stored so a page needs no on-demand fit.

    Fitting one model per competition is CPU-bound: on a small server it takes
    far longer than a visitor will wait.  The background loop builds the batch
    and keeps it here; the API serves the stored payload (with the time it was
    computed) instead of making every visit pay for a fresh fit.
    """

    __tablename__ = "prediction_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(120), index=True)
    window_days: Mapped[int] = mapped_column(Integer, default=14)
    include_live: Mapped[bool] = mapped_column(Boolean, default=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class BacktestResult(Base):
    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_run_id: Mapped[int] = mapped_column(ForeignKey("model_runs.id"), index=True)
    league_id: Mapped[int | None] = mapped_column(ForeignKey("leagues.id"), nullable=True)
    market: Mapped[str] = mapped_column(String(60))
    n_train: Mapped[int] = mapped_column(Integer, default=0)
    n_eval: Mapped[int] = mapped_column(Integer, default=0)
    brier_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    log_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_brier: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_log_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class CalibrationBin(Base):
    __tablename__ = "calibration_bins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    backtest_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_results.id"), index=True
    )
    bin_lower: Mapped[float] = mapped_column(Float)
    bin_upper: Mapped[float] = mapped_column(Float)
    n: Mapped[int] = mapped_column(Integer)
    mean_predicted: Mapped[float | None] = mapped_column(Float, nullable=True)
    observed_frequency: Mapped[float | None] = mapped_column(Float, nullable=True)
