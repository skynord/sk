"""Synthetic sample provider - for offline demos and tests ONLY.

This provider generates made-up results.  It is *deliberately* impossible to
mistake for real data: the league is named "Synthetic Sample League", the
country is "SYNTHETIC", and every ingested record is flagged ``is_estimate``.
It must never be used to present statistics as if they were real.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from app.providers.base import (
    Capability,
    Provider,
    ProviderCapabilities,
    register_provider,
)
from app.providers.dto import NormalizedMatch, NormalizedStat

SAMPLE_LEAGUE = "Synthetic Sample League"
SAMPLE_COUNTRY = "SYNTHETIC (generated - not real data)"


@register_provider
class SampleProvider(Provider):
    def __init__(self, teams: int = 12, weeks: int = 90, seed: int = 20240101) -> None:
        super().__init__()
        self._teams = teams
        self._weeks = weeks
        self._seed = seed

    def describe(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="sample",
            kind="csv",
            capabilities={
                Capability.FIXTURES,
                Capability.RESULTS,
                Capability.STATS,
                Capability.ODDS,
            },
            requires_key=False,
            license_note="Synthetic data generated locally. NOT real football data.",
            cost_note="Free.",
            notes="Demo/testing only. Clearly labelled synthetic; never present as real.",
        )

    def is_available(self) -> tuple[bool, str]:
        return True, "synthetic generator available (labelled SYNTHETIC)"

    def iter_dataset(self) -> Iterable[tuple[NormalizedMatch, list[NormalizedStat], list]]:
        rng = random.Random(self._seed)
        names = [f"Sample City {i}" for i in range(self._teams)]
        strength = {name: rng.uniform(-0.4, 0.4) for name in names}
        base = datetime(2022, 8, 6, 15, 0, tzinfo=UTC)
        for week in range(self._weeks):
            order = names[:]
            rng.shuffle(order)
            for i in range(0, len(order) - 1, 2):
                home, away = order[i], order[i + 1]
                lambda_home = math.exp(0.25 + strength[home] - strength[away] + 0.22)
                lambda_away = math.exp(0.25 + strength[away] - strength[home])
                hg = _poisson(rng, lambda_home)
                ag = _poisson(rng, lambda_away)
                kickoff = base + timedelta(days=7 * week)
                provider_id = f"sample:{week}:{home}:{away}"
                match = NormalizedMatch(
                    provider_id=provider_id,
                    kickoff_utc=kickoff,
                    home_name=home,
                    away_name=away,
                    league_name=SAMPLE_LEAGUE,
                    country=SAMPLE_COUNTRY,
                    season_label=_season(kickoff),
                    round_label=f"Matchday {week + 1}",
                    status="finished",
                    home_goals=hg,
                    away_goals=ag,
                    ht_home_goals=hg // 2,
                    ht_away_goals=ag // 2,
                    source_url="synthetic://sample",
                )
                stats = [
                    NormalizedStat(provider_id, home, True, {
                        "shots": hg * 3 + rng.randint(2, 8),
                        "shots_on_target": hg * 2 + rng.randint(1, 4),
                        "corners": rng.randint(2, 9),
                        "possession": round(50 + (strength[home] - strength[away]) * 30 + rng.randint(-5, 5), 1),
                        "xg": round(lambda_home + rng.uniform(-0.4, 0.4), 2),
                    }),
                    NormalizedStat(provider_id, away, False, {
                        "shots": ag * 3 + rng.randint(2, 8),
                        "shots_on_target": ag * 2 + rng.randint(1, 4),
                        "corners": rng.randint(1, 8),
                        "possession": round(50 + (strength[away] - strength[home]) * 30 + rng.randint(-5, 5), 1),
                        "xg": round(lambda_away + rng.uniform(-0.4, 0.4), 2),
                    }),
                ]
                yield match, stats, []


def _poisson(rng: random.Random, lam: float) -> int:
    limit = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1
        if k > 12:
            return k


def _season(kickoff: datetime) -> str:
    start = kickoff.year if kickoff.month >= 7 else kickoff.year - 1
    return f"{start}/{str(start + 1)[2:]}"
