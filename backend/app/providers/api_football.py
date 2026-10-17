"""API-Football (API-Sports) provider.

Official REST API.  Requires an API key from https://dashboard.api-football.com.
Free plan: 100 requests/day, 10 requests/minute, limited historical seasons.
Paid plans from $19/month.  We only ever call documented GET endpoints.

Docs: https://www.api-football.com/documentation-v3
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import UTC, datetime

from app.config import get_settings
from app.logging_config import get_logger
from app.providers.base import (
    Capability,
    Provider,
    ProviderCapabilities,
    ProviderError,
    ProviderUnavailable,
    register_provider,
)
from app.providers.dto import NormalizedMatch, NormalizedOdd, NormalizedStat
from app.providers.http import get_http_client

logger = get_logger(__name__)


def _is_rate_limit(errors: object) -> bool:
    """API-Football returns rate limits in a 200 body, e.g. {'rateLimit': '...'}."""
    if isinstance(errors, dict):
        return any(
            key == "rateLimit" or "rate limit" in str(value).lower()
            for key, value in errors.items()
        )
    if isinstance(errors, list):
        return any("rate limit" in str(value).lower() for value in errors)
    return "rate limit" in str(errors).lower()


STATUS_MAP = {
    "TBD": "scheduled",
    "NS": "scheduled",
    "1H": "live",
    "HT": "live",
    "2H": "live",
    "ET": "live",
    "P": "live",
    "BT": "live",
    "SUSP": "postponed",
    "INT": "postponed",
    "FT": "finished",
    "AET": "finished",
    "PEN": "finished",
    "PST": "postponed",
    "CANC": "cancelled",
    "ABD": "cancelled",
    "AWD": "finished",
    "WO": "finished",
    "LIVE": "live",
}

STAT_TYPE_MAP = {
    "Total Shots": "shots",
    "Shots on Goal": "shots_on_target",
    "Shots off Goal": "shots_off_target",
    "Ball Possession": "possession",
    "Corner Kicks": "corners",
    "Fouls": "fouls",
    "Yellow Cards": "yellow_cards",
    "Red Cards": "red_cards",
    "expected_goals": "xg",
    "Expected Goals": "xg",
}


@register_provider
class ApiFootballProvider(Provider):
    def describe(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="api_football",
            kind="api",
            capabilities={
                Capability.FIXTURES,
                Capability.RESULTS,
                Capability.STATS,
                Capability.XG,
                Capability.ODDS,
                Capability.LINEUPS,
                Capability.INJURIES,
            },
            base_url="https://v3.football.api-sports.io",
            requires_key=True,
            license_note="Commercial API; usage governed by API-Sports terms.",
            cost_note="Free 100 req/day; paid from $19/month.",
            terms_url="https://www.api-football.com/terms",
            notes="Set FA_API_FOOTBALL_KEY. RapidAPI users override FA_API_FOOTBALL_BASE_URL.",
        )

    def is_available(self) -> tuple[bool, str]:
        settings = get_settings()
        if not settings.api_football_key:
            return False, "API key not configured (set FA_API_FOOTBALL_KEY)"
        return True, "API key configured"

    def _headers(self) -> dict[str, str]:
        settings = get_settings()
        if not settings.api_football_key:
            raise ProviderUnavailable("API-Football key is not configured")
        return {"x-apisports-key": settings.api_football_key}

    def _get(self, path: str, params: dict, *, max_attempts: int = 3) -> dict:
        settings = get_settings()
        url = f"{settings.api_football_base_url.rstrip('/')}{path}"
        for attempt in range(1, max_attempts + 1):
            response = get_http_client().get(url, params=params, headers=self._headers())
            if response.status_code == 403:
                raise ProviderUnavailable("API-Football rejected the key (HTTP 403)")
            if response.status_code >= 400:
                raise ProviderError(f"API-Football error HTTP {response.status_code}")
            payload = response.json()
            errors = payload.get("errors")
            if errors and _is_rate_limit(errors):
                wait = min(5.0 * attempt, 15.0)
                logger.warning(
                    "api-football rate limited; backing off",
                    extra={"sleep": wait, "attempt": attempt},
                )
                time.sleep(wait)
                get_http_client().purge(url, params=params)
                continue
            if errors and isinstance(errors, (dict, list)) and errors:
                raise ProviderError(f"API-Football reported errors: {errors}")
            return payload
        raise ProviderError("API-Football rate limit: gave up after repeated 10/minute limits")

    def fetch_matches(
        self,
        *,
        league_name: str | None = None,
        season_label: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        league_id: int | None = None,
        season: int | None = None,
        date: str | None = None,
        live: bool = False,
    ) -> Iterable[NormalizedMatch]:
        params: dict[str, object] = {}
        if live:
            params["live"] = "all"
        elif date or (date_from and date_from == date_to):
            params["date"] = date or date_from
        elif date_from or date_to:
            if date_from:
                params["from"] = date_from
            if date_to:
                params["to"] = date_to
        if league_id and season:
            params["league"] = league_id
            params["season"] = season
        if not params:
            raise ProviderError(
                "API-Football needs live, date, from/to or league+season"
            )
        payload = self._get("/fixtures", params)
        for item in payload.get("response", []):
            yield self._to_match(item)

    def _to_match(self, item: dict) -> NormalizedMatch:
        fixture = item["fixture"]
        league = item.get("league", {})
        teams = item.get("teams", {})
        goals = item.get("goals", {})
        score = item.get("score", {}).get("halftime", {})
        status = STATUS_MAP.get(fixture.get("status", {}).get("short", ""), "scheduled")
        return NormalizedMatch(
            provider_id=str(fixture["id"]),
            kickoff_utc=datetime.fromisoformat(fixture["date"].replace("Z", "+00:00")).astimezone(UTC),
            home_name=teams["home"]["name"],
            away_name=teams["away"]["name"],
            league_name=league.get("name", "Unknown"),
            country=league.get("country"),
            season_label=str(league.get("season")) if league.get("season") else None,
            round_label=league.get("round"),
            venue=(fixture.get("venue") or {}).get("name"),
            status=status,
            home_goals=goals.get("home"),
            away_goals=goals.get("away"),
            ht_home_goals=score.get("home"),
            ht_away_goals=score.get("away"),
            source_url=f"https://www.api-football.com/fixtures/{fixture['id']}",
        )

    def fetch_stats(self, match: NormalizedMatch) -> Iterable[NormalizedStat]:
        payload = self._get("/fixtures/statistics", {"fixture": match.provider_id})
        for team_block in payload.get("response", []):
            team_name = team_block.get("team", {}).get("name", "")
            is_home = team_name == match.home_name
            metrics: dict[str, object] = {}
            for stat in team_block.get("statistics", []):
                key = STAT_TYPE_MAP.get(stat.get("type", ""))
                if not key:
                    continue
                value = stat.get("value")
                if isinstance(value, str) and value.endswith("%"):
                    value = float(value.rstrip("%"))
                metrics[key] = value
            yield NormalizedStat(
                match_provider_id=match.provider_id,
                team_name=team_name,
                is_home=is_home,
                metrics=metrics,
                source_url=match.source_url,
            )

    def fetch_odds(self, match: NormalizedMatch) -> Iterable[NormalizedOdd]:
        payload = self._get("/odds", {"fixture": match.provider_id})
        for item in payload.get("response", []):
            for bookmaker in item.get("bookmakers", []):
                book_name = bookmaker.get("name", "unknown")
                for bet in bookmaker.get("bets", []):
                    market_name = bet.get("name", "")
                    if market_name == "Match Winner":
                        for value in bet.get("values", []):
                            selection = {"Home": "home", "Draw": "draw", "Away": "away"}.get(value.get("value"))
                            if selection:
                                yield NormalizedOdd(
                                    match.provider_id, "1x2", selection,
                                    float(value["odd"]), book_name,
                                    source_url=match.source_url,
                                )
                    elif market_name == "Both Teams Score":
                        for value in bet.get("values", []):
                            selection = {"Yes": "yes", "No": "no"}.get(value.get("value"))
                            if selection:
                                yield NormalizedOdd(
                                    match.provider_id, "btts", selection,
                                    float(value["odd"]), book_name,
                                    source_url=match.source_url,
                                )
                    elif market_name == "Goals Over/Under":
                        for value in bet.get("values", []):
                            label = value.get("value", "")
                            if "2.5" in label:
                                selection = "over" if label.startswith("Over") else "under"
                                yield NormalizedOdd(
                                    match.provider_id, "o/u_2.5", selection,
                                    float(value["odd"]), book_name, line=2.5,
                                    source_url=match.source_url,
                                )
