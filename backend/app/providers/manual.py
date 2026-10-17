"""Manual JSON importer.

For operators who cannot or do not want to use an automated source, matches can
be entered by hand as JSON and dropped into ``data/raw/manual_*.json``.  This is
also the fallback the README documents for the "manual match-analysis input".

Schema (all dates ISO-8601, stats/odds optional):

.. code-block:: json

    {
      "league": "Example League",
      "country": "Exampleland",
      "season": "2024/25",
      "matches": [
        {
          "date": "2024-08-10T14:00:00Z",
          "home": "Alpha FC",
          "away": "Beta FC",
          "status": "finished",
          "home_goals": 2, "away_goals": 1,
          "ht_home_goals": 1, "ht_away_goals": 0,
          "stats": {"home": {"shots": 12, "xg": 1.8}, "away": {"shots": 8, "xg": 0.9}},
          "odds": [{"market": "1x2", "selection": "home", "decimal_odds": 2.10}]
        }
      ]
    }
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.providers.base import (
    Capability,
    Provider,
    ProviderCapabilities,
    ProviderError,
    register_provider,
)
from app.providers.dto import NormalizedMatch, NormalizedOdd, NormalizedStat


@register_provider
class ManualProvider(Provider):
    def describe(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="manual",
            kind="manual",
            capabilities={
                Capability.FIXTURES,
                Capability.RESULTS,
                Capability.STATS,
                Capability.ODDS,
            },
            requires_key=False,
            license_note="Data entered by the operator; the operator is responsible for it.",
            cost_note="Free.",
            notes="Reads data/raw/manual_*.json.",
        )

    def _files(self) -> list[Path]:
        return sorted(get_settings().raw_data_dir.glob("manual_*.json"))

    def is_available(self) -> tuple[bool, str]:
        files = self._files()
        if not files:
            return False, "no manual_*.json files found in data/raw"
        return True, f"{len(files)} manual file(s) found"

    def iter_dataset(self) -> Iterable[tuple[NormalizedMatch, list[NormalizedStat], list[NormalizedOdd]]]:
        for path in self._files():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                raise ProviderError(f"could not read {path.name}: {exc}") from exc
            league = payload.get("league", "Manual league")
            country = payload.get("country")
            season = payload.get("season")
            for raw in payload.get("matches", []):
                yield self._parse_match(raw, league, country, season, path)

    def _parse_match(
        self, raw: dict, league: str, country: str | None, season: str | None, path: Path
    ) -> tuple[NormalizedMatch, list[NormalizedStat], list[NormalizedOdd]]:
        kickoff = datetime.fromisoformat(str(raw["date"]).replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=UTC)
        provider_id = f"manual:{path.stem}:{kickoff.date().isoformat()}:{raw['home']}:{raw['away']}"
        match = NormalizedMatch(
            provider_id=provider_id,
            kickoff_utc=kickoff,
            home_name=raw["home"],
            away_name=raw["away"],
            league_name=league,
            country=country,
            season_label=season,
            round_label=raw.get("round"),
            venue=raw.get("venue"),
            status=raw.get("status", "finished"),
            home_goals=raw.get("home_goals"),
            away_goals=raw.get("away_goals"),
            ht_home_goals=raw.get("ht_home_goals"),
            ht_away_goals=raw.get("ht_away_goals"),
            source_url=str(path),
        )
        stats: list[NormalizedStat] = []
        for side, is_home in (("home", True), ("away", False)):
            metrics = (raw.get("stats") or {}).get(side)
            if metrics:
                stats.append(
                    NormalizedStat(
                        match_provider_id=provider_id,
                        team_name=raw["home"] if is_home else raw["away"],
                        is_home=is_home,
                        metrics=metrics,
                        source_url=str(path),
                    )
                )
        odds: list[NormalizedOdd] = []
        for odd in raw.get("odds", []):
            odds.append(
                NormalizedOdd(
                    match_provider_id=provider_id,
                    market=odd["market"],
                    selection=odd["selection"],
                    decimal_odds=float(odd["decimal_odds"]),
                    bookmaker=odd.get("bookmaker", "manual"),
                    is_closing=bool(odd.get("is_closing", False)),
                    line=odd.get("line"),
                    source_url=str(path),
                )
            )
        return match, stats, odds
