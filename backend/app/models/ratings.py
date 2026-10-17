"""Strength ratings derived from a fitted Dixon-Coles model.

Attack and defence multipliers are expressed relative to the league average so
they are directly comparable between teams: an attack multiplier of 1.2 means
the team creates 20% more than an average side, a defence multiplier of 1.2
means it concedes 20% fewer than average.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.models.dixon_coles import DixonColesModel, MatchRecord


@dataclass
class TeamRating:
    team: str
    attack_multiplier: float
    defence_multiplier: float
    net_strength: float


def base_rates(model: DixonColesModel) -> dict[str, float]:
    p = model.params
    return {
        "home": math.exp(p.intercept + p.home_advantage),
        "away": math.exp(p.intercept),
    }


def team_ratings(model: DixonColesModel) -> list[TeamRating]:
    p = model.params
    ratings = []
    for team in p.teams:
        attack = math.exp(p.attack[team])
        defence = math.exp(-p.defence[team])
        ratings.append(
            TeamRating(
                team=team,
                attack_multiplier=attack,
                defence_multiplier=defence,
                net_strength=attack * defence,
            )
        )
    ratings.sort(key=lambda r: r.net_strength, reverse=True)
    return ratings


def strength_of_schedule(
    model: DixonColesModel, records: list[MatchRecord]
) -> dict[str, float]:
    """Average net strength of the opponents each team has actually faced."""
    p = model.params
    strengths: dict[str, float] = {t: math.exp(p.attack[t]) * math.exp(-p.defence[t])
                                   for t in p.teams}
    accum: dict[str, list[float]] = {t: [] for t in p.teams}
    for r in records:
        if r.home in accum and r.away in accum:
            accum[r.home].append(strengths[r.away])
            accum[r.away].append(strengths[r.home])
    return {
        team: (sum(vals) / len(vals) if vals else 0.0) for team, vals in accum.items()
    }
