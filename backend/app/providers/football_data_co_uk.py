"""football-data.co.uk CSV importer.

football-data.co.uk publishes free historical results, match statistics and
betting odds as CSV files.  Its terms permit private, manual use but explicitly
ask people not to scrape the site with automated bots.  We therefore never
download automatically: the operator places the CSV files they have downloaded
by hand into ``data/raw`` and this provider reads them.

Reference: https://www.football-data.co.uk/data.php (notes: data.php, notes.txt)
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.providers.base import Capability, Provider, ProviderCapabilities, register_provider
from app.providers.dto import NormalizedMatch, NormalizedOdd, NormalizedStat
from app.timezones import local_to_utc

LEAGUE_NAMES = {
    "E0": ("England", "Premier League"),
    "E1": ("England", "Championship"),
    "E2": ("England", "League One"),
    "E3": ("England", "League Two"),
    "EC": ("England", "National League"),
    "SC0": ("Scotland", "Premiership"),
    "SC1": ("Scotland", "Championship"),
    "D1": ("Germany", "Bundesliga"),
    "D2": ("Germany", "2. Bundesliga"),
    "I1": ("Italy", "Serie A"),
    "I2": ("Italy", "Serie B"),
    "SP1": ("Spain", "La Liga"),
    "SP2": ("Spain", "Segunda Division"),
    "F1": ("France", "Ligue 1"),
    "F2": ("France", "Ligue 2"),
    "N1": ("Netherlands", "Eredivisie"),
    "B1": ("Belgium", "Jupiler Pro League"),
    "P1": ("Portugal", "Primeira Liga"),
    "T1": ("Turkey", "Super Lig"),
    "G1": ("Greece", "Super League"),
}

ODDS_1X2 = [
    ("B365H", "B365D", "B365A", "Bet365"),
    ("PSH", "PSD", "PSA", "Pinnacle"),
    ("WHH", "WHD", "WHA", "William Hill"),
    ("BbAvH", "BbAvD", "BbAvA", "Betbrain average"),
]


def _parse_date(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_local_kickoff(date_value: str, time_value: str | None) -> datetime | None:
    """Parse the *venue-local* kick-off football-data.co.uk publishes.

    The CSVs carry the local kick-off time of the match (a Spanish fixture at
    21:00 is 21:00 in Spain, not in the UK).  ``Time`` is optional, so when it
    is absent only the date is known and midnight is kept as the placeholder
    the rest of the code has always used.
    """
    local_date = _parse_date(date_value)
    if local_date is None:
        return None
    parsed_time = (time_value or "").strip()
    if not parsed_time:
        return local_date.replace(hour=0, minute=0, second=0, microsecond=0)
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            clock = datetime.strptime(parsed_time, fmt)
        except ValueError:
            continue
        return local_date.replace(hour=clock.hour, minute=clock.minute,
                                  second=clock.second, microsecond=0)
    return local_date.replace(hour=0, minute=0, second=0, microsecond=0)


def _to_int(value: str | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _to_float(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


@register_provider
class FootballDataCoUkProvider(Provider):
    def describe(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="football_data_co_uk",
            kind="csv",
            capabilities={
                Capability.FIXTURES,
                Capability.RESULTS,
                Capability.STATS,
                Capability.ODDS,
            },
            base_url="https://www.football-data.co.uk/data.php",
            requires_key=False,
            license_note="Free for private use. Terms ask that it not be scraped automatically.",
            cost_note="Free.",
            terms_url="https://www.football-data.co.uk/data.php",
            notes="Place downloaded CSV files in data/raw; this provider reads them locally.",
        )

    def _files(self) -> list[Path]:
        raw_dir = get_settings().raw_data_dir
        return sorted(raw_dir.glob("*.csv"))

    def is_available(self) -> tuple[bool, str]:
        files = self._files()
        if not files:
            return False, f"no CSV files found in {get_settings().raw_data_dir}"
        return True, f"{len(files)} CSV file(s) found"

    def _row_to_match(self, row: dict[str, str], source: Path) -> NormalizedMatch | None:
        home = (row.get("HomeTeam") or "").strip()
        away = (row.get("AwayTeam") or "").strip()
        kickoff_local = _parse_local_kickoff(row.get("Date", ""), row.get("Time"))
        if not home or not away or kickoff_local is None:
            return None
        division = (row.get("Div") or "").strip()
        country, league_name = LEAGUE_NAMES.get(division, (None, division or "Unknown"))
        season = _season_label(kickoff_local)
        fthg = _to_int(row.get("FTHG"))
        ftag = _to_int(row.get("FTAG"))
        status = "finished" if fthg is not None and ftag is not None else "scheduled"
        # Identity stays keyed on the venue's local date, so re-importing an
        # existing file keeps matching the same rows.
        provider_id = f"{source.stem}:{kickoff_local.date().isoformat()}:{home}:{away}"
        return NormalizedMatch(
            provider_id=provider_id,
            kickoff_utc=local_to_utc(kickoff_local, country),
            home_name=home,
            away_name=away,
            league_name=league_name,
            country=country,
            season_label=season,
            round_label=None,
            status=status,
            home_goals=fthg,
            away_goals=ftag,
            ht_home_goals=_to_int(row.get("HTHG")),
            ht_away_goals=_to_int(row.get("HTAG")),
            source_url=str(source),
        )

    def _row_to_stat(self, match: NormalizedMatch, row: dict[str, str]) -> list[NormalizedStat]:
        common = {
            "shots": None,
            "shots_on_target": None,
            "corners": None,
            "fouls": None,
            "yellow_cards": None,
            "red_cards": None,
        }
        home_metrics = dict(common)
        away_metrics = dict(common)
        for side, keys in (
            ("H", ("HS", "HST", "HC", "HF", "HY", "HR")),
            ("A", ("AS", "AST", "AC", "AF", "AY", "AR")),
        ):
            values = [_to_int(row.get(k)) for k in keys]
            if all(v is None for v in values):
                continue
            target = home_metrics if side == "H" else away_metrics
            target.update(
                {
                    "shots": values[0],
                    "shots_on_target": values[1],
                    "corners": values[2],
                    "fouls": values[3],
                    "yellow_cards": values[4],
                    "red_cards": values[5],
                }
            )
        stats: list[NormalizedStat] = []
        if any(v is not None for v in home_metrics.values()):
            stats.append(
                NormalizedStat(
                    match_provider_id=match.provider_id,
                    team_name=match.home_name,
                    is_home=True,
                    metrics=home_metrics,
                    source_url=match.source_url,
                )
            )
        if any(v is not None for v in away_metrics.values()):
            stats.append(
                NormalizedStat(
                    match_provider_id=match.provider_id,
                    team_name=match.away_name,
                    is_home=False,
                    metrics=away_metrics,
                    source_url=match.source_url,
                )
            )
        return stats

    def _row_to_odds(self, match: NormalizedMatch, row: dict[str, str]) -> list[NormalizedOdd]:
        odds: list[NormalizedOdd] = []
        for home_key, draw_key, away_key, bookmaker in ODDS_1X2:
            h, d, a = _to_float(row.get(home_key)), _to_float(row.get(draw_key)), _to_float(row.get(away_key))
            if h and d and a and h > 1 and d > 1 and a > 1:
                for selection, price in (("home", h), ("draw", d), ("away", a)):
                    odds.append(
                        NormalizedOdd(
                            match_provider_id=match.provider_id,
                            market="1x2",
                            selection=selection,
                            decimal_odds=price,
                            bookmaker=bookmaker,
                            source_url=match.source_url,
                        )
                    )
        over = _to_float(row.get("B365>2.5"))
        under = _to_float(row.get("B365<2.5"))
        if over and under and over > 1 and under > 1:
            odds.append(NormalizedOdd(match.provider_id, "o/u_2.5", "over", over, "Bet365",
                                      line=2.5, source_url=match.source_url))
            odds.append(NormalizedOdd(match.provider_id, "o/u_2.5", "under", under, "Bet365",
                                      line=2.5, source_url=match.source_url))
        return odds

    def iter_dataset(self) -> Iterable[tuple[NormalizedMatch, list[NormalizedStat], list[NormalizedOdd]]]:
        for path in self._files():
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    match = self._row_to_match(row, path)
                    if match is None:
                        continue
                    yield match, self._row_to_stat(match, row), self._row_to_odds(match, row)

    def fetch_matches(self, **kwargs: object) -> Iterable[NormalizedMatch]:
        for match, _stats, _odds in self.iter_dataset():
            yield match


def _season_label(kickoff: datetime) -> str:
    year = kickoff.year
    month = kickoff.month
    start = year if month >= 7 else year - 1
    return f"{start}/{str(start + 1)[2:]}"
