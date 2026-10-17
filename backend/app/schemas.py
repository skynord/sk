"""Pydantic request/response schemas for the API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ManualOddInput(BaseModel):
    market: str = Field(..., examples=["1x2", "o/u_2.5", "btts"])
    selection: str = Field(..., examples=["home", "draw", "away", "over", "under", "yes", "no"])
    decimal_odds: float = Field(..., gt=1.0)
    bookmaker: str = "manual"
    is_closing: bool = False
    line: float | None = None


class AnalysisOptionsRequest(BaseModel):
    monte_carlo_simulations: int | None = Field(default=None, ge=100, le=1_000_000)
    xg_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    seed: int | None = None
    kmax: int | None = Field(default=None, ge=6, le=20)
    halflife_days: float | None = Field(default=None, gt=0, le=3650)
    regularization: float | None = Field(default=None, ge=0.0, le=10.0)
    use_rest: bool = False
    lookback_days: int = Field(default=1200, ge=30, le=7300)
    include_asian_handicap: bool = True
    manual_odds: list[ManualOddInput] | None = None
    persist: bool = True


class ManualStatsInput(BaseModel):
    shots: int | None = None
    shots_on_target: int | None = None
    big_chances: int | None = None
    possession: float | None = None
    corners: int | None = None
    fouls: int | None = None
    yellow_cards: int | None = None
    red_cards: int | None = None
    xg: float | None = None


class ManualMatchInput(BaseModel):
    league: str
    country: str | None = None
    season: str | None = None
    kickoff_utc: datetime
    home: str
    away: str
    status: str = "scheduled"
    round: str | None = None
    venue: str | None = None
    home_goals: int | None = None
    away_goals: int | None = None
    ht_home_goals: int | None = None
    ht_away_goals: int | None = None
    home_stats: ManualStatsInput | None = None
    away_stats: ManualStatsInput | None = None
    odds: list[ManualOddInput] = Field(default_factory=list)

    @field_validator("status")
    @classmethod
    def _valid_status(cls, value: str) -> str:
        allowed = {"scheduled", "finished", "postponed", "cancelled", "live"}
        if value not in allowed:
            raise ValueError(f"status must be one of {sorted(allowed)}")
        return value


class ValueAssessRequest(BaseModel):
    model_probability: float = Field(..., ge=0.0, le=1.0)
    decimal_odds: float = Field(..., gt=1.0)
    market_fair_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    selection: str = "selection"


class BacktestRequest(BaseModel):
    league_id: int
    min_train: int = Field(default=40, ge=10, le=2000)
    retrain_every: int = Field(default=5, ge=1, le=200)
    halflife_days: float = Field(default=180.0, gt=0, le=3650)
    regularization: float = Field(default=1.0, ge=0.0, le=10.0)
    use_rest: bool = False
    totals_line: float = Field(default=2.5, gt=0, le=10)


class IngestRequest(BaseModel):
    provider: str
    params: dict[str, Any] = Field(default_factory=dict)
    include_details: bool = True


class IngestFixtureRequest(BaseModel):
    """Import a single provider fixture (to make a live/upcoming one analysable)."""

    reference: str = Field(..., description="the provider's fixture id")
    provider: str = "api_football"
    date: str | None = Field(default=None, description="UTC date YYYY-MM-DD of the fixture")
    include_details: bool = True


class MessageResponse(BaseModel):
    detail: str
    extra: dict[str, Any] | None = None
