"""Normalised data-transfer objects produced by every provider.

Providers translate their own shapes into these DTOs; the ingestion service then
knows exactly one format.  Any metric a provider cannot supply is left ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class NormalizedTeam:
    provider_id: str
    name: str
    country: str | None = None
    short_name: str | None = None


@dataclass
class NormalizedMatch:
    provider_id: str
    kickoff_utc: datetime
    home_name: str
    away_name: str
    league_name: str
    country: str | None = None
    season_label: str | None = None
    round_label: str | None = None
    venue: str | None = None
    status: str = "scheduled"  # scheduled | finished | postponed | cancelled
    home_goals: int | None = None
    away_goals: int | None = None
    ht_home_goals: int | None = None
    ht_away_goals: int | None = None
    source_url: str | None = None


@dataclass
class NormalizedStat:
    match_provider_id: str
    team_name: str
    is_home: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    source_url: str | None = None


@dataclass
class NormalizedOdd:
    match_provider_id: str
    market: str
    selection: str
    decimal_odds: float
    bookmaker: str = "unknown"
    is_closing: bool = False
    line: float | None = None
    source_url: str | None = None
