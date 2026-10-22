"""Download the club crests for the UEFA cups and add them to the frontend map.

Crests come from two free sources, in this order:

* ``github.com/luukhopman/football-logos`` -- one folder per domestic league for
  the 25 leagues it covers, which is where the domestic clubs of the cups live.
* Wikimedia Commons (``File:`` namespace) for the clubs from the small
  associations that repository does not cover.  Commons only accepts free
  content, so a file with a free licence is the only kind taken.

A club with no crest in either place keeps the monogram the UI already draws,
so nothing is invented.  Run from ``backend``:

    python -m scripts.fetch_team_logos              # report only
    python -m scripts.fetch_team_logos --download
"""

from __future__ import annotations

import difflib
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models import League, Match, Team

ROOT = Path(__file__).resolve().parents[2]
LOGOS = ROOT / "frontend" / "public" / "logos"
TEAM_LOGOS_TS = ROOT / "frontend" / "src" / "data" / "teamLogos.ts"
CACHE = Path(__import__("os").environ.get("TEMP", ".")) / "opencode"
USER_AGENT = "sky-football/1.0 (analysis app; crest import)"

CUPS = ["europe-uefa-europa-league", "europe-uefa-conference-league"]

#: country folder in the logo repository -> the three-letter code in a file name
REPO_FOLDERS = {
    "Austria - Bundesliga": "aut",
    "Belgium - Jupiler Pro League": "bel",
    "Bulgaria - efbet Liga": "bul",
    "Croatia - SuperSport HNL": "cro",
    "Czech Republic - Chance Liga": "cze",
    "Denmark - Superliga": "den",
    "England - Premier League": "eng",
    "France - Ligue 1": "fra",
    "Germany - Bundesliga": "ger",
    "Greece - Super League 1": "gre",
    "Israel - Ligat ha'Al": "isr",
    "Italy - Serie A": "ita",
    "Netherlands - Eredivisie": "ned",
    "Norway - Eliteserien": "nor",
    "Poland - PKO BP Ekstraklasa": "pol",
    "Portugal - Liga Portugal": "por",
    "Romania - SuperLiga": "rou",
    "Russia - Premier Liga": "rus",
    "Scotland - Scottish Premiership": "sco",
    "Serbia - Super liga Srbije": "srb",
    "Spain - LaLiga": "esp",
    "Sweden - Allsvenskan": "swe",
    "Switzerland - Super League": "sui",
    "Turkey - Süper Lig": "tur",
    "Ukraine - Premier Liga": "ukr",
}

#: the stored "country" of a club -> the same three-letter code
COUNTRY_CODES = {
    "austria": "aut", "belgium": "bel", "bulgaria": "bul", "croatia": "cro",
    "czechia": "cze", "czech republic": "cze", "denmark": "den", "england": "eng",
    "france": "fra", "germany": "ger", "greece": "gre", "israel": "isr",
    "italy": "ita", "netherlands": "ned", "norway": "nor", "poland": "pol",
    "portugal": "por", "romania": "rou", "russia": "rus", "scotland": "sco",
    "serbia": "srb", "spain": "esp", "sweden": "swe", "switzerland": "sui",
    "turkey": "tur", "ukraine": "ukr",
}

#: clubs added from a fixture list carry no country, so name them here
COUNTRY_OVERRIDES = {
    "agf": "den", "copenhagen": "den", "jablonec": "cze", "kairat": "kaz",
    "kups": "fin", "lillestrom": "nor", "mjallby aif": "swe",
    "nordsjaelland": "den", "red star belgrade": "srb", "thun": "sui",
    "torreense": "por",
}


@dataclass(frozen=True)
class Club:
    team_id: int
    name: str
    code: str | None


FOLD = str.maketrans({"ø": "o", "ł": "l", "đ": "d", "ð": "d", "þ": "th", "ß": "ss", "ı": "i", "æ": "ae"})


def norm(name: str) -> str:
    text = unicodedata.normalize("NFKD", name.translate(FOLD))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s*\([a-z]{2,4}\)\s*$", "", text.lower())
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def country_code(team: Team) -> str | None:
    match = re.search(r"\(([A-Z]{2,4})\)\s*$", team.name)
    known = norm(team.name)
    if known in COUNTRY_OVERRIDES:
        return COUNTRY_OVERRIDES[known]
    if team.country and team.country.lower() != "europe":
        return COUNTRY_CODES.get(team.country.lower())
    if match:
        return match.group(1).lower()
    return None


def cup_clubs() -> list[Club]:
    with SessionLocal() as db:
        ids = [
            league.id
            for league in db.execute(select(League).where(League.slug.in_(CUPS))).scalars()
        ]
        seen: dict[int, Club] = {}
        for match in db.execute(select(Match).where(Match.league_id.in_(ids))).scalars():
            for team_id in (match.home_team_id, match.away_team_id):
                if team_id in seen:
                    continue
                team = db.get(Team, team_id)
                seen[team_id] = Club(team.id, team.name, country_code(team))
    return list(seen.values())


def repo_index() -> dict[str, list[tuple[str, str]]]:
    """code -> [(club name in the repository, raw download url)]."""
    cache_file = CACHE / "logo_repo.json"
    if not cache_file.exists():
        raise SystemExit("missing logo_repo.json: run the folder listing first")
    folders = json.loads(cache_file.read_text(encoding="utf-8"))
    index: dict[str, list[tuple[str, str]]] = {}
    for folder, code in REPO_FOLDERS.items():
        files = folders.get(folder)
        if files is None:
            for name in folders:
                if code == REPO_FOLDERS.get(name):
                    files = folders[name]
        if not files:
            continue
        index[code] = [
            (
                Path(f["name"]).stem,
                "https://raw.githubusercontent.com/luukhopman/football-logos/master/logos/"
                + urllib.parse.quote(folder)
                + "/"
                + urllib.parse.quote(f["name"]),
            )
            for f in files
        ]
    return index


#: words that carry no club identity, so "SS Lazio" still matches "Lazio Roma"
NOISE = {
    "fc", "cf", "sc", "ac", "ss", "as", "us", "afc", "cd", "ca", "ud", "sd", "ce", "ec",
    "sk", "fk", "nk", "hnk", "hsk", "bk", "if", "ik", "ff", "sv", "tsg", "vfl", "vfb",
    "vfr", "fsv", "spvgg", "ksc", "club", "calcio", "sport", "sports", "sporting",
    "football", "futbol", "fussball", "de", "do", "da", "di", "del", "the", "team",
}


def significant(name: str) -> set[str]:
    return {word for word in norm(name).split() if len(word) > 1 and word not in NOISE}


#: clubs the database names differently from the crest repository
CLUB_ALIASES = {
    "agf": "Aarhus GF",
    "fc kobenhavn": "FC Copenhagen",
    "ofi heraklion": "OFI Crete",
    "fc twente 65": "FC Twente Enschede",
    "istanbul basaksehir": "Istanbul Basaksehir FK",
    "tsc backa topola": "TSC Backa Topola",
    "olympiakos piraeus": "Olympiacos Piraeus",
    "aek athens": "AEK Athens",
    "aris thessaloniki": "Aris Thessaloniki",
}


def best_repo(club: Club, index: dict[str, list[tuple[str, str]]]) -> tuple[float, str, str] | None:
    """The repository crest that best fits the club, with its score."""
    if not club.code:
        return None
    entries = index.get(club.code, [])
    wanted = norm(club.name)
    wanted_key = significant(club.name)
    alias = CLUB_ALIASES.get(wanted)
    first = wanted_key and sorted(wanted_key, key=wanted.split().index)[0]
    best: tuple[float, str, str] | None = None
    for name, url in entries:
        candidate = norm(name)
        if candidate == wanted or (alias and name == alias):
            return (1.0, name, url)
        candidate_key = significant(name)
        if not candidate_key or not wanted_key:
            continue
        smaller, larger = (
            (candidate_key, wanted_key)
            if len(candidate_key) <= len(wanted_key)
            else (wanted_key, candidate_key)
        )
        if smaller <= larger:
            score = 0.93
        else:
            score = difflib.SequenceMatcher(
                None, " ".join(sorted(candidate_key)), " ".join(sorted(wanted_key))
            ).ratio()
        if first and first in candidate_key:
            score += 0.04
        if best is None or score > best[0]:
            best = (score, name, url)
    return best

def _commons_pages(query: str) -> list[dict]:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "generator": "search",
            "gsrnamespace": "6",
            "gsrsearch": query,
            "gsrlimit": "8",
            "prop": "imageinfo",
            "iiprop": "url|mime|extmetadata",
            "iiurlwidth": "180",
        }
    )
    request = urllib.request.Request(
        "https://commons.wikimedia.org/w/api.php?" + params, headers={"User-Agent": USER_AGENT}
    )
    for attempt in range(4):
        time.sleep(1.1)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response).get("query", {}).get("pages", [])
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 3:
                return []
            time.sleep(15 * (attempt + 1))
    return []


def commons_image(club: Club) -> tuple[str, str] | None:
    """A freely licensed crest for the club on Wikimedia Commons, if one exists."""
    title = re.sub(r"\s*\([A-Z]{2,4}\)\s*$", "", club.name)
    tokens = {word for word in significant(title) if len(word) > 3}
    if not tokens:
        return None
    for query in (
        f'intitle:"{title}" logo',
        f'intitle:"{title}" crest',
        f'"{title}" club logo filetype:drawing',
    ):
        for page in _commons_pages(query):
            name = page.get("title", "")
            if not tokens & {word for word in significant(name) if len(word) > 3}:
                continue
            info = (page.get("imageinfo") or [{}])[0]
            mime = info.get("mime", "")
            if mime not in {"image/png", "image/svg+xml"}:
                continue
            licence = str(info.get("extmetadata", {}).get("LicenseShortName", {}).get("value", ""))
            if re.search(r"fair use|non-free|copyright", licence, re.I):
                continue
            url = info.get("url") if mime == "image/svg+xml" else info.get("thumburl") or info.get("url")
            if url:
                return (name, url)
    return None

def sportsdb_image(club: Club) -> tuple[str, str] | None:
    """A badge for the club from TheSportsDB, whose free tier allows this use."""
    title = re.sub(r"\s*\([A-Z]{2,4}\)\s*$", "", club.name)
    wanted = significant(title)
    if not wanted:
        return None
    params = urllib.parse.urlencode({"t": title})
    request = urllib.request.Request(
        "https://www.thesportsdb.com/api/v1/json/123/searchteams.php?" + params,
        headers={"User-Agent": USER_AGENT},
    )
    payload: dict = {}
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 3:
                return None
            time.sleep(20 * (attempt + 1))
    for team in payload.get("teams") or []:
        name = str(team.get("strTeam") or "")
        key = significant(name)
        if not key or not (key <= wanted or wanted <= key):
            continue
        badge = team.get("strBadge") or team.get("strTeamBadge")
        if badge:
            return (name, str(badge))
    return None

def existing_map() -> dict[str, str]:
    text = TEAM_LOGOS_TS.read_text(encoding="utf-8")
    return dict(re.findall(r'^  "([^"]+)": "([^"]+)",$', text, re.M))


def write_map(entries: dict[str, str]) -> None:
    header = (
        "// Generated by scripts (football-logos, Wikimedia Commons). Real club\n"
        "// crests, matched by name. Source: https://github.com/luukhopman/football-logos\n"
        "// and https://commons.wikimedia.org (freely licensed files only); crests are\n"
        "// club trademarks, used here for a local, non-commercial analyst dashboard.\n"
        "\n"
        "export function normTeam(name: string): string {\n"
        "  return name\n"
        "    .normalize('NFKD')\n"
        "    .replace(/[\\u0300-\\u036f]/g, '')\n"
        "    .toLowerCase()\n"
        "    .replace(/\\s*\\([a-z]{2,4}\\)\\s*$/, '')\n"
        "    .replace(/[^a-z0-9]+/g, ' ')\n"
        "    .trim();\n"
        "}\n"
        "\n"
        "export const TEAM_LOGOS: Record<string, string> = {\n"
    )
    body = "".join(f'  "{key}": "{value}",\n' for key, value in sorted(entries.items()))
    TEAM_LOGOS_TS.write_text(header + body + "};\n", encoding="utf-8")


def download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        target.write_bytes(response.read())


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    download_files = "--download" in sys.argv
    clubs = cup_clubs()
    index = repo_index()
    entries = existing_map()
    LOGOS.mkdir(parents=True, exist_ok=True)
    added_repo = added_commons = repaired = 0
    missing: list[Club] = []
    for club in clubs:
        key = norm(club.name)
        current = entries.get(key)
        best = best_repo(club, index)
        if best is not None and best[0] >= 0.86 and club.code:
            score, name, url = best
            target = LOGOS / f"{club.code}-{norm(name).replace(' ', '-')}.png"
            # a confident match replaces an entry that points at another club
            wrong = current is not None and Path(current).name != target.name
            if current is None or (score >= 0.97 and wrong):
                if not download_files:
                    print(f"  repo: {club.name}  <-  {name}  ({score:.2f})")
                elif not target.exists():
                    download(url, target)
                if wrong:
                    repaired += 1
                else:
                    added_repo += 1
                entries[key] = f"/logos/{target.name}"
                continue
        if current is not None:
            continue
        try:
            commons = commons_image(club)
        except Exception as exc:  # network hiccup: leave the club for the next run
            print(f"  commons failed for {club.name!r}: {exc}")
            commons = None
        if commons is None:
            try:
                commons = sportsdb_image(club)
            except Exception as exc:
                print(f"  sportsdb failed for {club.name!r}: {exc}")
                commons = None
            if commons is not None:
                time.sleep(3.0)
        if commons is None:
            missing.append(club)
            continue
        title, url = commons
        if not download_files:
            print(f"  commons: {club.name}  <-  {title}")
        extension = "svg" if url.lower().endswith(".svg") else "png"
        stem = norm(club.name).replace(" ", "-")
        target = LOGOS / f"{club.code or 'eu'}-{stem}.{extension}"
        if download_files:
            download(url, target)
        entries[key] = f"/logos/{target.name}"
        added_commons += 1
        time.sleep(0.4)

    if download_files:
        write_map(entries)
    print(
        f"{'downloaded' if download_files else 'planned'}: {added_repo} from the logo "
        f"repository, {added_commons} from Wikimedia Commons, {repaired} re-pointed, "
        f"{len(missing)} without a crest"
    )
    for club in sorted(missing, key=lambda c: c.name):
        print("   sin logo:", club.name, f"({club.code})")

if __name__ == "__main__":
    main()
