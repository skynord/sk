"""Venue timezone helpers.

Data sources disagree about what a kick-off timestamp means:

* football-data.co.uk publishes the *local wall-clock* time at the venue;
* openfootball publishes the local wall-clock time for its domestic
  competitions, but uses Central European Time for the cross-border UEFA
  competitions (its "21:00" for a game at Anfield is 21:00 CET, i.e. the usual
  20:00 local start);
* API-Football publishes true UTC.

Providers convert a local time to UTC here before a match leaves the ingest
layer, and the API serialises every ``kickoff_utc`` with an explicit ``Z``, so a
browser never has to guess whether a naive timestamp is UTC or its own local
time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

# Country (as used by our providers) -> IANA timezone of its venues.
COUNTRY_TZ: dict[str, str] = {
    "England": "Europe/London",
    "Scotland": "Europe/London",
    "Wales": "Europe/London",
    "Spain": "Europe/Madrid",
    "Germany": "Europe/Berlin",
    "Italy": "Europe/Rome",
    "France": "Europe/Paris",
    "Monaco": "Europe/Monaco",
    "Netherlands": "Europe/Amsterdam",
    "Portugal": "Europe/Lisbon",
    "Belgium": "Europe/Brussels",
    "Austria": "Europe/Vienna",
    "Switzerland": "Europe/Zurich",
    "Czech Republic": "Europe/Prague",
    "Croatia": "Europe/Zagreb",
    "Serbia": "Europe/Belgrade",
    "Ukraine": "Europe/Kyiv",
    "Slovakia": "Europe/Bratislava",
    "Turkey": "Europe/Istanbul",
    "Greece": "Europe/Athens",
    "Denmark": "Europe/Copenhagen",
    "Norway": "Europe/Oslo",
    "Sweden": "Europe/Stockholm",
    "Poland": "Europe/Warsaw",
    "Romania": "Europe/Bucharest",
    "Bulgaria": "Europe/Sofia",
    "Israel": "Asia/Jerusalem",
    "Russia": "Europe/Moscow",
}

#: The provider labels a cross-border competition's country as "Europe"; UEFA
#: publishes those kick-offs in Central European Time regardless of the venue.
CROSS_BORDER_TZ = "Europe/Berlin"


def tz_for_country(country: str | None) -> ZoneInfo | None:
    """The IANA timezone for a country name, or ``None`` when unknown."""
    name = COUNTRY_TZ.get((country or "").strip())
    return ZoneInfo(name) if name else None


def competition_tz(country: str | None) -> ZoneInfo | None:
    """The timezone a competition's kick-off times are published in.

    A domestic competition uses its country's timezone; a cross-border one
    (``"Europe"``) uses Central European Time.  ``None`` when it cannot be
    placed, so the caller leaves the timestamp untouched rather than shifting
    it wrongly.
    """
    name = (country or "").strip()
    if name.lower() == "europe":
        return ZoneInfo(CROSS_BORDER_TZ)
    return tz_for_country(name)


def local_to_utc(local_naive: datetime, country: str | None) -> datetime:
    """Read a naive wall-clock time in the competition's timezone; return UTC.

    Treats the value as UTC when the competition cannot be placed, so an
    unrecognised country leaves the timestamp untouched rather than shifting it
    wrongly.
    """
    if local_naive.tzinfo is not None:
        local_naive = local_naive.replace(tzinfo=None)
    tz = competition_tz(country)
    if tz is None:
        return local_naive.replace(tzinfo=UTC)
    return local_naive.replace(tzinfo=tz).astimezone(UTC)


def iso_utc(value: datetime | None) -> str | None:
    """ISO-8601 UTC string with an explicit ``Z``.

    A naive value is taken to be UTC (that is how SQLite hands back a
    ``DateTime(timezone=True)`` column), so clients always receive an
    unambiguous instant.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
