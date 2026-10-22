"""Load the current season's UEFA league-phase fixtures from Wikipedia.

This exists because the live fixture provider (API-Football) is down and
openfootball publishes a cup season only after it finishes, so without it the
cups hold their history and nothing to predict.  A fixture list is factual data
(dates, grounds and pairings, not a creative work); the page each row came from
is stored as its provenance, and the page is attributed in the README.

It is an operator tool, not a provider: the wiki markup is parsed once here, and
every row is written through the same upsert path (and provenance table) as an
ingest.  Run it without ``--apply`` first to see what it would change.

    python -m scripts.import_cup_fixtures            # dry run
    python -m scripts.import_cup_fixtures --apply
"""

from __future__ import annotations

import difflib
import json
import re
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis import identity
from app.db.base import SessionLocal
from app.db.models import League, Match, Team
from app.ingest.service import _provenance, _upsert_season, slugify

SEASON = "2026/27"
PROVIDER = "wikipedia"
USER_AGENT = "sky-football/1.0 (analysis app; fixture import)"
PAGES = {
    "uefa.cl": "2026-27 UEFA Champions League league phase",
    "uefa.el": "2026-27 UEFA Europa League league phase",
    "uefa.ecl": "2026-27 UEFA Conference League league phase",
}
LEAGUE_SLUGS = {
    "uefa.cl": "europe-uefa-champions-league",
    "uefa.el": "europe-uefa-europa-league",
    "uefa.ecl": "europe-uefa-conference-league",
}

BOX = "{{#invoke:Football box|main"
MATCHDAY_RE = re.compile(r"^===\s*Matchday\s+(\d+)\s*===\s*$", re.M)
DATE_RE = re.compile(r"\{\{Start date\|(\d{4})\|(\d{1,2})\|(\d{1,2})")
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")
LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
SCORE_RE = re.compile(r"^\s*(\d+)\s*[-\u2013\u2212:]\s*(\d+)\s*$")


@dataclass(frozen=True)
class Fixture:
    code: str
    matchday: int | None
    kickoff: datetime
    home: str
    away: str
    home_goals: int | None
    away_goals: int | None
    venue: str


def source_url(code: str) -> str:
    return "https://en.wikipedia.org/wiki/" + PAGES[code].replace(" ", "_")


def fetch_wikitext(title: str) -> str:
    query = urllib.parse.urlencode(
        {
            "action": "parse",
            "page": title,
            "prop": "wikitext",
            "format": "json",
            "formatversion": "2",
            "redirects": "1",
        }
    )
    url = "https://en.wikipedia.org/w/api.php?" + query
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.load(response)
    if "parse" not in payload:
        raise RuntimeError(f"wikipedia did not return {title!r}: {payload.get('error')}")
    return payload["parse"]["wikitext"]


def _field(body: str, name: str) -> str:
    found = re.search(rf"^[ \t]*\|[ \t]*{name}[ \t]*=[ \t]*([^\n]*)", body, re.M)
    return found.group(1).strip() if found else ""


def _name(raw: str) -> str:
    raw = re.sub(r"\{\{fbaicon\|[A-Z]{3}\}\}", "", raw)
    raw = re.sub(r"<ref.*?</ref>", "", raw, flags=re.S)
    raw = re.sub(r"<ref[^/]*/>", "", raw)
    raw = raw.replace("&nbsp;", " ").strip().rstrip("}").strip()
    links = LINK_RE.findall(raw)
    if links:
        target, display = links[-1]
        return (display or target).strip()
    return re.sub(r"\{\{[^{}]*\}\}", "", raw).strip()


def _cet_to_utc(day: date, hours: int, minutes: int) -> datetime:
    """The box states Central European Time; convert it for the given date."""
    start = date(day.year, 3, 31)
    while start.weekday() != 6:
        start -= timedelta(days=1)
    end = date(day.year, 10, 31)
    while end.weekday() != 6:
        end -= timedelta(days=1)
    offset = 2 if start <= day < end else 1
    naive = datetime(day.year, day.month, day.day, hours, minutes)
    return (naive - timedelta(hours=offset)).replace(tzinfo=UTC)


def parse_fixtures(code: str, text: str) -> list[Fixture]:
    headings = [(m.start(), int(m.group(1))) for m in MATCHDAY_RE.finditer(text)]
    out: list[Fixture] = []
    for start in (m.start() for m in re.finditer(re.escape(BOX), text)):
        end = text.find("\n}}", start)
        body = text[start + len(BOX) : end if end != -1 else len(text)]
        when = DATE_RE.search(_field(body, "date"))
        clock = TIME_RE.search(_field(body, "time"))
        home, away = _name(_field(body, "team1")), _name(_field(body, "team2"))
        if not (when and clock and home and away):
            continue
        day = date(int(when.group(1)), int(when.group(2)), int(when.group(3)))
        score = SCORE_RE.match(_field(body, "score"))
        matchday = None
        for position, number in headings:
            if position < start:
                matchday = number
        out.append(
            Fixture(
                code=code,
                matchday=matchday,
                kickoff=_cet_to_utc(day, int(clock.group(1)), int(clock.group(2))),
                home=home,
                away=away,
                home_goals=int(score.group(1)) if score else None,
                away_goals=int(score.group(2)) if score else None,
                venue=_name(_field(body, "stadium")),
            )
        )
    return out


def _same_club(a: str, b: str) -> bool:
    """One spelling's token set sits inside the other's (``Lille`` / ``Lille OSC``)."""
    left, right = set(identity.team_merge_key(a)), set(identity.team_merge_key(b))
    return bool(left) and bool(right) and (left <= right or right <= left)


def _resolve(db: Session, names: Iterable[str]) -> dict[str, Team]:
    """Map a fixture name onto the stored club, preferring the row with history."""
    teams = list(db.execute(select(Team)).scalars())
    played: dict[int, int] = dict(
        db.execute(select(Match.home_team_id, func.count(Match.id)).group_by(Match.home_team_id)).all()
    )
    for team_id, count in db.execute(
        select(Match.away_team_id, func.count(Match.id)).group_by(Match.away_team_id)
    ).all():
        played[team_id] = played.get(team_id, 0) + count

    def richest(group: list[Team]) -> Team:
        return sorted(group, key=lambda team: (-played.get(team.id, 0), -team.id))[0]

    by_key: dict[tuple[str, ...], list[Team]] = {}
    for team in teams:
        by_key.setdefault(identity.team_merge_key(team.name), []).append(team)

    resolved: dict[str, Team] = {}
    for name in names:
        exact = by_key.get(identity.team_merge_key(name))
        if exact:
            resolved[name] = richest(exact)
            continue
        mine = set(identity.team_merge_key(name))
        best: tuple[float, Team | None] = (0.0, None)
        for key, group in by_key.items():
            theirs = set(key)
            if mine and theirs and (mine <= theirs or theirs <= mine):
                score = 0.9 + len(mine & theirs) / max(len(mine | theirs), 1) * 0.08
            else:
                score = difflib.SequenceMatcher(
                    None, identity.canonical_team_key(name), identity.canonical_team_key(group[0].name)
                ).ratio()
            if score > best[0]:
                best = (score, richest(group))
        if best[1] is not None and best[0] >= 0.86:
            resolved[name] = best[1]
    return resolved


def import_fixtures(db: Session, fixtures: list[Fixture], *, apply: bool) -> dict[str, int]:
    leagues: dict[str, League] = {}
    for code, slug in LEAGUE_SLUGS.items():
        league = db.execute(select(League).where(League.slug == slug)).scalars().first()
        if league is None:
            raise SystemExit(f"league {slug} is not stored yet: ingest the cup history first")
        leagues[code] = league

    stored: dict[str, list[tuple[Match, str, str]]] = {}
    for code, league in leagues.items():
        rows = db.execute(select(Match).where(Match.league_id == league.id)).scalars().all()
        stored[code] = [
            (row, db.get(Team, row.home_team_id).name, db.get(Team, row.away_team_id).name) for row in rows
        ]

    resolved = _resolve(db, {name for fixture in fixtures for name in (fixture.home, fixture.away)})
    counts = {"created": 0, "updated": 0, "unchanged": 0, "teams": 0}

    def team_for(name: str, league: League, code: str) -> Team:
        team = resolved.get(name)
        if team is not None:
            return team
        team = db.execute(select(Team).where(Team.slug == slugify(name))).scalars().first()
        if team is None:
            team = Team(slug=slugify(name), name=name, country="Europe", league_id=league.id)
            db.add(team)
            db.flush()
            counts["teams"] += 1
        _provenance(
            db, PROVIDER, "team", team.id, source_url=source_url(code),
            competition=league.name, season=SEASON,
        )
        resolved[name] = team
        return team

    for fixture in fixtures:
        league = leagues[fixture.code]
        home = team_for(fixture.home, league, fixture.code)
        away = team_for(fixture.away, league, fixture.code)
        known = [
            row
            for row, home_name, away_name in stored[fixture.code]
            if abs((row.kickoff_utc - fixture.kickoff).total_seconds()) <= 3 * 3600
            and _same_club(home_name, fixture.home)
            and _same_club(away_name, fixture.away)
        ]
        finished = fixture.home_goals is not None
        status = "finished" if finished else "scheduled"
        if known:
            row = known[0]
            if (
                row.status == status
                and row.home_goals == fixture.home_goals
                and row.away_goals == fixture.away_goals
            ):
                counts["unchanged"] += 1
                continue
            row.status = status
            row.home_goals = fixture.home_goals
            row.away_goals = fixture.away_goals
            row.round_label = row.round_label or f"League phase, matchday {fixture.matchday}"
            counts["updated"] += 1
            match = row
        else:
            match = Match(
                league_id=league.id,
                season_id=_upsert_season(db, league, SEASON).id,
                home_team_id=home.id,
                away_team_id=away.id,
                kickoff_utc=fixture.kickoff,
                status=status,
                home_goals=fixture.home_goals,
                away_goals=fixture.away_goals,
                round_label=f"League phase, matchday {fixture.matchday}",
                venue=fixture.venue or None,
                source_provider=PROVIDER,
                source_ref=f"{fixture.code}:{SEASON}:{slugify(fixture.home)}:{slugify(fixture.away)}",
            )
            db.add(match)
            db.flush()
            stored[fixture.code].append((match, fixture.home, fixture.away))
            counts["created"] += 1
        _provenance(
            db, PROVIDER, "match", match.id, source_url=source_url(fixture.code),
            competition=league.name, season=SEASON,
        )

    if apply:
        db.commit()
    else:
        db.rollback()
    return counts


def main() -> None:
    import sys

    apply = "--apply" in sys.argv
    fixtures: list[Fixture] = []
    for code, title in PAGES.items():
        parsed = parse_fixtures(code, fetch_wikitext(title))
        played = sum(1 for fixture in parsed if fixture.home_goals is not None)
        print(f"{code}: {len(parsed)} fixtures ({played} with a result) from {source_url(code)}")
        fixtures.extend(parsed)
    with SessionLocal() as db:
        counts = import_fixtures(db, fixtures, apply=apply)
    print(
        f"{'applied' if apply else 'dry run'}: {counts['created']} created, "
        f"{counts['updated']} updated, {counts['unchanged']} unchanged, "
        f"{counts['teams']} new clubs"
    )


if __name__ == "__main__":
    main()
