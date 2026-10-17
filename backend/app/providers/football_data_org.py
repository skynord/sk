"""football-data.org provider (v4 API).

Official API with a free tier (limited competitions, 10 requests/minute) and
paid plans.  Requires an API token from https://www.football-data.org/client/register.

Docs: https://www.football-data.org/documentation/quickstart
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

STATUS_MAP = {
    "SCHEDULED": "scheduled",
    "TIMED": "scheduled",
    "IN_PLAY": "live",
    "PAUSED": "live",
    "FINISHED": "finished",
    "SUSPENDED": "postponed",
    "POSTPONED": "postponed",
    "CANCELLED": "cancelled",
    "AWARDED": "finished",
}


@register_provider
class FootballDataOrgProvider(Provider):
    def describe(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="football_data_org",
            kind="api",
            capabilities={Capability.FIXTURES, Capability.RESULTS},
            base_url="https://api.football-data.org/v4",
            requires_key=True,
            license_note="Usage governed by football-data.org terms.",
            cost_note="Free tier available; live scores from about 12 EUR/month.",
            terms_url="https://www.football-data.org/documentation/api",
            notes="Results and fixtures only; no xG, stats or odds on the free tier.",
        )

    def is_available(self) -> tuple[bool, str]:
        settings = get_settings()
        if not settings.football_data_org_key:
            return False, "API token not configured (set FA_FOOTBALL_DATA_ORG_KEY)"
        return True, "API token configured"

    def _headers(self) -> dict[str, str]:
        token = get_settings().football_data_org_key
        if not token:
            raise ProviderUnavailable("football-data.org token is not configured")
        return {"X-Auth-Token": token}

    def fetch_matches(
        self,
        *,
        competition_code: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        season: int | None = None,
        **kwargs: object,
    ) -> Iterable[NormalizedMatch]:
        settings = get_settings()
        base = settings.football_data_org_base_url.rstrip("/")
        params: dict[str, object] = {}
        if date_from:
            params["dateFrom"] = date_from
        if date_to:
            params["dateTo"] = date_to
        if season:
            params["season"] = season
        if competition_code:
            path = f"/competitions/{competition_code}/matches"
        else:
            path = "/matches"
        response = get_http_client().get(base + path, params=params, headers=self._headers())
        if response.status_code == 403:
            raise ProviderUnavailable("football-data.org rejected the token (HTTP 403)")
        if response.status_code == 429:
            raise ProviderError("football-data.org rate limit reached (HTTP 429)")
        if response.status_code >= 400:
            raise ProviderError(f"football-data.org error HTTP {response.status_code}")
        payload = response.json()
        for item in payload.get("matches", []):
            yield self._to_match(item)

    def _to_match(self, item: dict) -> NormalizedMatch:
        utc_date = item.get("utcDate", "")
        kickoff = datetime.fromisoformat(utc_date.replace("Z", "+00:00")).astimezone(UTC)
        score = item.get("score", {})
        full = score.get("fullTime", {})
        half = score.get("halfTime", {})
        competition = item.get("competition", {})
        return NormalizedMatch(
            provider_id=str(item["id"]),
            kickoff_utc=kickoff,
            home_name=item["homeTeam"].get("name", "Unknown"),
            away_name=item["awayTeam"].get("name", "Unknown"),
            league_name=competition.get("name", "Unknown"),
            country=item.get("area", {}).get("name"),
            season_label=str(item.get("season", {}).get("startDate", ""))[:4] or None,
            round_label=item.get("matchday") and f"Matchday {item['matchday']}",
            status=STATUS_MAP.get(item.get("status", ""), "scheduled"),
            home_goals=full.get("home"),
            away_goals=full.get("away"),
            ht_home_goals=half.get("home"),
            ht_away_goals=half.get("away"),
            source_url=f"https://www.football-data.org/match/{item['id']}",
        )
