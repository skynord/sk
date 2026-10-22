"""openfootball JSON provider (public-domain data, no API key).

openfootball publishes free, explicitly public-domain football fixtures and
results as JSON on GitHub:

    https://github.com/openfootball/football.json

The UEFA club competitions that mirror does not carry -- the Europa League and
the Conference League, in any season -- are published by the same project as
plain-text files in its cup repository:

    https://github.com/openfootball/champions-league

so those codes are read from there instead (see :data:`CUP_TEXT_SOURCES`).
Ties settled in extra time or on penalties are stored with the score the match
ended on; the file's extra annotations (leg results, half-time pairs) are not
guessed at, so a half-time score the file does not state stays missing.

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

import re
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from app.config import get_settings
from app.logging_config import get_logger
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

logger = get_logger(__name__)

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


#: Competitions the JSON mirror does not carry at all, read from openfootball's
#: cup repository instead: code -> file stems to look for in each season folder
#: (the qualifying rounds are part of the same competition).
CUP_TEXT_SOURCES: dict[str, tuple[str, ...]] = {
    "uefa.el": ("el", "elq"),
    "uefa.ecl": ("conf", "confq"),
}

#: A stage header inside a cup file, e.g. "? League phase" / "\u25aa Round of 16".
_CUP_STAGE_RE = re.compile(r"^\s*[?\u25aa]\s+(?P<label>\S.*?)\s*$")
#: A date line, e.g. "  Wed Sep 25 2024"; the year is written on the first one
#: of a season only.
_CUP_DATE_RE = re.compile(
    r"^\s+(?P<weekday>Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})(?:\s+(?P<year>\d{4}))?\s*$"
)
#: A match line: an optional "18:45", the home club, " v ", then the rest.
_CUP_MATCH_RE = re.compile(
    r"^\s+(?:(?P<time>\d{1,2}:\d{2})\s+)?(?P<home>\S.*?)\s+v\s+(?P<rest>\S.*?)\s*$"
)

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

#: How a cup file writes a result, most annotated first.  ``ft`` is the score
#: this match ended on, ``ht`` the half-time score where the file states it;
#: everything else the line carries is left alone rather than guessed at.
_CUP_SCORE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "3-2 pen. 0-2 a.e.t. (0-2, 0-1)": decided on penalties, this leg ended 0-2
    re.compile(r"\d{1,2}-\d{1,2}\s+pen\.\s+(?P<ft>\d{1,2}-\d{1,2})\s+a\.e\.t\."),
    # "5-2 a.e.t. (3-2, 0-1)": settled in extra time
    re.compile(r"(?P<ft>\d{1,2}-\d{1,2})\s+a\.e\.t\."),
    # "1-1 (1-0)": full time and half time
    re.compile(r"(?P<ft>\d{1,2}-\d{1,2})\s+\((?P<ht>\d{1,2}-\d{1,2})\)"),
    # "0-0", or "0-3    [awarded]"
    re.compile(r"(?P<ft>\d{1,2}-\d{1,2})\s*(?:\[awarded\])?$"),
)
#: What may follow the score: the file's own detail in brackets, nothing else.
_CUP_TAIL_OK_RE = re.compile(r"^(?:\[[^\]]*\])?$|^(?:\([^)]*\))?$")
#: A score shape we have not seen (a pair of numbers, however written) -- never
#: guess at it.
_CUP_UNKNOWN_SCORE_RE = re.compile(r"\b\d{1,2}\s*[:\-]\s*\d{1,2}\b")


def _cup_result(rest: str) -> tuple[str, int | None, int | None, int | None, int | None] | None:
    """Split ``"IF Elfsborg (SWE)        3-2 (1-1)"`` into club and score.

    Returns ``(away, home_goals, away_goals, ht_home, ht_away)`` with the goals
    ``None`` for a fixture the file lists as unplayed, or ``None`` when the line
    carries a score shape this parser does not understand.
    """
    for pattern in _CUP_SCORE_PATTERNS:
        found = pattern.search(rest)
        if not found:
            continue
        if not _CUP_TAIL_OK_RE.match(rest[found.end():].strip()):
            continue
        away = rest[: found.start()].strip()
        if not away:
            continue
        home_goals, away_goals = (int(v) for v in found.group("ft").split("-"))
        ht_group = found.groupdict().get("ht")
        ht_home = ht_away = None
        if ht_group:
            ht_home, ht_away = (int(v) for v in ht_group.split("-"))
        return away, home_goals, away_goals, ht_home, ht_away
    if _CUP_UNKNOWN_SCORE_RE.search(rest):
        return None
    return rest, None, None, None, None


def parse_cup_text(
    text: str, *, code: str, stem: str, season: str, url: str
) -> list[NormalizedMatch]:
    """Turn one openfootball plain-text cup file into normalised matches.

    The format is a title line, stage headers, a date line, then one line per
    match.  Two things the JSON mirror does not have to be handled here: only
    the first date line of a season names the year (later ones belong to the
    season's first year until the calendar turns over), and a tie settled in
    extra time or on penalties is written as one line
    (``0-2 pen. 3-1 a.e.t. (2-0, 1-0)``) -- the stored score is this match's
    own, after extra time.
    """
    country, league_name = LEAGUES.get(code, (None, code))
    season_start = int(season.split("-", 1)[0])
    matches: list[NormalizedMatch] = []
    stage: str | None = None
    is_quali = False
    kickoff_date: date | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("="):
            is_quali = "- Quali" in stripped
            continue
        if stripped.startswith("#"):
            continue
        stage_match = _CUP_STAGE_RE.match(line)
        if stage_match:
            stage = stage_match.group("label")
            continue
        date_match = _CUP_DATE_RE.match(line)
        if date_match:
            month = _MONTHS.get(date_match.group("month"))
            if month is None:
                continue
            year_text = date_match.group("year")
            year = int(year_text) if year_text else (season_start if month >= 7 else season_start + 1)
            kickoff_date = date(year, month, int(date_match.group("day")))
            continue
        match_line = _CUP_MATCH_RE.match(line)
        if not match_line or kickoff_date is None:
            continue
        home = match_line.group("home").strip()
        parsed = _cup_result(match_line.group("rest").strip())
        if parsed is None:
            logger.warning(
                "openfootball cup line not understood",
                extra={"url": url, "line": stripped[:120]},
            )
            continue
        away, home_goals, away_goals, ht_home, ht_away = parsed
        if not home or not away:
            continue
        kickoff_local = _parse_local_kickoff(
            kickoff_date.isoformat(), match_line.group("time")
        )
        if kickoff_local is None:
            continue
        round_label = f"Quali {stage}" if stage and is_quali else stage
        matches.append(
            NormalizedMatch(
                # The file is part of the identity: the main and the qualifying
                # file of one competition must never collide on a fixture.
                provider_id=(
                    f"openfootball:{code}:{season}:{stem}:"
                    f"{kickoff_date.isoformat()}:{home}:{away}"
                ),
                kickoff_utc=local_to_utc(kickoff_local, country),
                home_name=home,
                away_name=away,
                league_name=league_name,
                country=country,
                season_label=_season_label(season),
                round_label=round_label,
                status="finished" if home_goals is not None else "scheduled",
                home_goals=home_goals,
                away_goals=away_goals,
                ht_home_goals=ht_home,
                ht_away_goals=ht_away,
                source_url=url,
            )
        )
    return matches


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

    def _cup_url(self, season: str, stem: str) -> str:
        base = get_settings().openfootball_cup_base_url.rstrip("/")
        return f"{base}/{season}/{stem}.txt"

    def _iter_cup_files(
        self, code: str, season: str
    ) -> Iterable[tuple[NormalizedMatch, list[NormalizedStat], list[NormalizedOdd]]]:
        """Read one competition's plain-text files for a season, oldest first."""
        client = get_http_client()
        for stem in CUP_TEXT_SOURCES.get(code, ()):
            url = self._cup_url(season, stem)
            response = client.get(url, ttl=6 * 3600)
            if response.status_code == 404:
                # A season folder that does not hold this file yet: the cup files
                # appear as the season is written up, so this is normal.
                continue
            if response.status_code >= 400:
                raise ProviderError(f"openfootball {code} {season} {stem}: HTTP {response.status_code}")
            for match in parse_cup_text(
                response.text, code=code, stem=stem, season=season, url=url
            ):
                yield match, [], []

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
                if code in CUP_TEXT_SOURCES:
                    yield from self._iter_cup_files(code, season)
                    continue
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
