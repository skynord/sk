"""openfootball JSON provider (public-domain data, no API key).

openfootball publishes free, explicitly public-domain football fixtures and
results as JSON on GitHub:

    https://github.com/openfootball/football.json

There is no API key, no sign-up and no rate-limit beyond ordinary politeness, so
this provider reads the files directly over HTTPS through the shared
rate-limited, on-disk-cached HTTP client.  Only fixtures/results (and half-time
scores) are available; there are no shots, cards or odds, and those metrics are
left missing rather than invented.

Which competitions/seasons to read is configured with::

    FA_OPENFOOTBALL_LEAGUES=en.1,es.1,de.1,it.1,fr.1
    FA_OPENFOOTBALL_SEASONS=2024-25,2025-26,2026-27
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from app.config import get_settings
from app.providers.base import (
    Capability,
    Provider,
    ProviderCapabilities,
    ProviderError,
    register_provider,
)
from app.providers.dto import NormalizedMatch, NormalizedOdd, NormalizedStat
from app.providers.http import get_http_client
from app.timezones import local_to_utc

# openfootball competition codes -> (country, league name)
LEAGUES: dict[str, tuple[str, str]] = {
    "en.1": ("England", "Premier League"),
    "en.2": ("England", "Championship"),
    "en.3": ("England", "League One"),
    "en.4": ("England", "League Two"),
    "es.1": ("Spain", "La Liga"),
    "es.2": ("Spain", "Segunda Division"),
    "de.1": ("Germany", "Bundesliga"),
    "de.2": ("Germany", "2. Bundesliga"),
    "it.1": ("Italy", "Serie A"),
    "it.2": ("Italy", "Serie B"),
    "fr.1": ("France", "Ligue 1"),
    "fr.2": ("France", "Ligue 2"),
    "nl.1": ("Netherlands", "Eredivisie"),
    "pt.1": ("Portugal", "Primeira Liga"),
    "be.1": ("Belgium", "First Division A"),
    "at.1": ("Austria", "Bundesliga"),
    "tr.1": ("Turkey", "Super Lig"),
    "gr.1": ("Greece", "Super League"),
    "sco.1": ("Scotland", "Premiership"),
    "uefa.cl": ("Europe", "UEFA Champions League"),
    "uefa.el": ("Europe", "UEFA Europa League"),
    "uefa.ecl": ("Europe", "UEFA Conference League"),
}


def _parse_local_kickoff(date_value: str, time_value: str | None) -> datetime | None:
    """Parse the *local wall-clock* kick-off openfootball publishes.

    openfootball stores the competition's local time with no offset, so this
    returns a naive datetime.  ``parse_payload`` converts it to UTC using the
    competition's timezone.
    """
    date_value = (date_value or "").strip()
    if not date_value:
        return None
    time_value = (time_value or "").strip() or "12:00"
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            text = f"{date_value} {time_value}" if "%H" in fmt else date_value
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError:
        return None


def _season_label(season: str) -> str:
    return season.replace("-", "/")


@register_provider
class OpenFootballProvider(Provider):
    def describe(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="openfootball",
            kind="api",
            capabilities={Capability.FIXTURES, Capability.RESULTS},
            base_url="https://github.com/openfootball/football.json",
            requires_key=False,
            license_note="Public domain (openfootball). Free to use, no key required.",
            cost_note="Free.",
            terms_url="https://github.com/openfootball/football.json",
            notes="Reads match fixtures/results (no stats or odds) from the public JSON mirror.",
        )

    def _leagues(self) -> list[str]:
        return get_settings().openfootball_league_list

    def _seasons(self) -> list[str]:
        return get_settings().openfootball_season_list

    def is_available(self) -> tuple[bool, str]:
        leagues = self._leagues()
        if not leagues:
            return False, "no FA_OPENFOOTBALL_LEAGUES configured"
        return True, f"{len(leagues)} league(s) x {len(self._seasons())} season(s) configured"

    def _url(self, season: str, code: str) -> str:
        base = get_settings().openfootball_base_url.rstrip("/")
        return f"{base}/{season}/{code}.json"

    def parse_payload(self, payload: dict[str, Any], code: str, season: str, url: str) -> list[NormalizedMatch]:
        """Turn one openfootball JSON document into normalised matches."""
        country, league_name = LEAGUES.get(code, (None, code))
        matches: list[NormalizedMatch] = []
        for raw in payload.get("matches", []) or []:
            home = (raw.get("team1") or "").strip()
            away = (raw.get("team2") or "").strip()
            kickoff_local = _parse_local_kickoff(str(raw.get("date", "")), raw.get("time"))
            if not home or not away or kickoff_local is None:
                continue
            score = raw.get("score")
            ft: Any = None
            ht: Any = None
            if isinstance(score, dict):
                ft = score.get("ft")
                ht = score.get("ht")
            elif isinstance(score, (list, tuple)) and len(score) == 2:
                # some rows use a bare [home, away] full-time pair
                ft = list(score)
            home_goals = away_goals = None
            status = "scheduled"
            if isinstance(ft, list) and len(ft) == 2 and ft[0] is not None and ft[1] is not None:
                home_goals, away_goals = int(ft[0]), int(ft[1])
                status = "finished"
            ht_home = ht_away = None
            if isinstance(ht, list) and len(ht) == 2 and ht[0] is not None and ht[1] is not None:
                ht_home, ht_away = int(ht[0]), int(ht[1])
            matches.append(
                NormalizedMatch(
                    # Identity is keyed on the venue's local date, which is how
                    # the fixture is listed, so re-parsing stays stable even
                    # though the kick-off is stored as UTC.
                    provider_id=f"openfootball:{code}:{season}:{kickoff_local.date().isoformat()}:{home}:{away}",
                    kickoff_utc=local_to_utc(kickoff_local, country),
                    home_name=home,
                    away_name=away,
                    league_name=league_name,
                    country=country,
                    season_label=_season_label(season),
                    round_label=(raw.get("round") or None),
                    status=status,
                    home_goals=home_goals,
                    away_goals=away_goals,
                    ht_home_goals=ht_home,
                    ht_away_goals=ht_away,
                    source_url=url,
                )
            )
        return matches

    def iter_dataset(self) -> Iterable[tuple[NormalizedMatch, list[NormalizedStat], list[NormalizedOdd]]]:
        client = get_http_client()
        for season in self._seasons():
            for code in self._leagues():
                url = self._url(season, code)
                response = client.get(url, ttl=6 * 3600)
                if response.status_code == 404:
                    continue
                if response.status_code >= 400:
                    raise ProviderError(f"openfootball {code} {season}: HTTP {response.status_code}")
                payload = response.json()
                for match in self.parse_payload(payload, code, season, url):
                    yield match, [], []

    def fetch_matches(
        self,
        *,
        league_name: str | None = None,
        season_label: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> Iterable[NormalizedMatch]:
        for match, _stats, _odds in self.iter_dataset():
            yield match
