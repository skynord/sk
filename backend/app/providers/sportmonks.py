"""Sportmonks provider (Football API v3).

Official REST API.  Requires a token from https://my.sportmonks.com.  Free plan
covers two leagues (Danish Superliga, Scottish Premiership); paid plans start at
EUR 29/month.  xG, predictions and odds are paid add-ons.

Docs: https://docs.sportmonks.com/v3
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from app.config import get_settings
from app.providers.base import (
    Capability,
    Provider,
    ProviderCapabilities,
    ProviderError,
    ProviderUnavailable,
    register_provider,
)
from app.providers.dto import NormalizedMatch
from app.providers.http import get_http_client


@register_provider
class SportmonksProvider(Provider):
    def describe(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="sportmonks",
            kind="api",
            capabilities={Capability.FIXTURES, Capability.RESULTS, Capability.STATS},
            base_url="https://api.sportmonks.com/v3/football",
            requires_key=True,
            license_note="Commercial API; usage governed by Sportmonks terms.",
            cost_note="Free plan (2 leagues); paid from EUR 29/month. xG/odds are add-ons.",
            terms_url="https://www.sportmonks.com/terms-and-conditions/",
            notes="Free token covers only the Danish Superliga and Scottish Premiership.",
        )

    def is_available(self) -> tuple[bool, str]:
        settings = get_settings()
        if not settings.sportmonks_token:
            return False, "API token not configured (set FA_SPORTMONKS_TOKEN)"
        return True, "API token configured"

    def _params(self, extra: dict | None = None) -> dict:
        token = get_settings().sportmonks_token
        if not token:
            raise ProviderUnavailable("Sportmonks token is not configured")
        params = {"api_token": token}
        if extra:
            params.update(extra)
        return params

    def fetch_matches(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        **kwargs: object,
    ) -> Iterable[NormalizedMatch]:
        if not (date_from and date_to):
            raise ProviderError("Sportmonks requires date_from and date_to (YYYY-MM-DD)")
        settings = get_settings()
        base = settings.sportmonks_base_url.rstrip("/")
        url = f"{base}/fixtures/between/{date_from}/{date_to}"
        response = get_http_client().get(
            url, params=self._params({"include": "participants;league;scores;state"})
        )
        if response.status_code in (401, 403):
            raise ProviderUnavailable("Sportmonks rejected the token")
        if response.status_code >= 400:
            raise ProviderError(f"Sportmonks error HTTP {response.status_code}")
        payload = response.json()
        for item in payload.get("data", []):
            yield self._to_match(item)

    def _to_match(self, item: dict) -> NormalizedMatch:
        starting = item.get("starting_at") or item.get("starting_at_timestamp")
        if isinstance(starting, str):
            kickoff = datetime.fromisoformat(starting.replace("Z", "+00:00")).astimezone(UTC)
        else:
            kickoff = datetime.fromtimestamp(int(starting), tz=UTC)
        participants = item.get("participants", [])
        home_name = away_name = "Unknown"
        for participant in participants:
            location = (participant.get("meta") or {}).get("location")
            if location == "home":
                home_name = participant.get("name", "Unknown")
            elif location == "away":
                away_name = participant.get("name", "Unknown")
        scores = item.get("scores", [])
        home_goals = away_goals = None
        for score in scores:
            goals = (score.get("score") or {}).get("goals")
            participant = score.get("participant")
            if participant == "home":
                home_goals = goals
            elif participant == "away":
                away_goals = goals
        state = item.get("state", {}) or {}
        state_code = state.get("state") or state.get("short_name")
        status = "finished" if (state_code in ("FT", "FINISHED", "AET", "FT_PEN")) else "scheduled"
        league = item.get("league", {}) or {}
        return NormalizedMatch(
            provider_id=str(item["id"]),
            kickoff_utc=kickoff,
            home_name=home_name,
            away_name=away_name,
            league_name=league.get("name", "Unknown"),
            country=None,
            season_label=str(item.get("season_id")) if item.get("season_id") else None,
            round_label=item.get("name"),
            status=status,
            home_goals=home_goals,
            away_goals=away_goals,
            source_url=f"https://www.sportmonks.com/fixtures/{item['id']}",
        )
