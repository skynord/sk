"""Cross-provider identity resolution for analysis.

The same real-world competition or club reaches us from more than one provider
with a different spelling (``"Huracan"`` vs ``"Hurac\u00e1n"``, ``"Gimnasia L.P."``
vs ``"Gimnasia de La Plata"``).  Local rows are merged across providers by slug,
so identical names already line up; the quirks that survive are the few
competitions left split into two local leagues.

When a fixture's own competition holds too little completed history to fit the
model, the engine lines its two clubs up with the rows of a sibling competition
in the same country so the model can still be fitted.  Matching is name based
and conservative -- an exact normalised match first, then a small table of
provider spelling differences we have actually observed, and only then a scored
token comparison with a high bar.  Nothing here invents data; an unresolved club
is reported, not guessed.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

#: Provider spelling quirks we have really seen, keyed by the normalised name a
#: provider uses, mapped to the normalised canonical name.  Extended as cases
#: are observed; never guessed.
TEAM_ALIASES: dict[str, str] = {
    "gimnasia l p": "gimnasia de la plata",  # API-Football -> openfootball
}

#: Tokens that carry no identifying weight when comparing club names.
_STOPWORDS = {"de", "del", "el", "y", "e", "fc", "cf", "cd", "club"}

#: Club-type / legal tokens a provider tacks on (``"AC Monza"`` vs ``"Monza"``,
#: ``"1. FC K\u00f6ln"`` vs a bare name) plus pure year/number tokens (``"1846"``,
#: ``"1907"``).  None of these distinguish one club from another inside a single
#: competition, so they are dropped from the club-identity key.  Identity words
#: such as ``united``, ``city``, ``real``, ``union``, ``racing`` and ``sporting``
#: are deliberately *not* here.
_FORM_TOKENS = {
    "ac", "acf", "ad", "afc", "aj", "as", "bc", "ca", "cd", "cf", "cfc",
    "ec", "es", "fc", "fk", "fsv", "gd", "hsc", "kv", "ogc", "osc",
    "rc", "rcd", "rsc", "sc", "sd", "sk", "ss", "ssc", "sv", "tsg",
    "ud", "us", "vfb", "vfl",
    "balompie", "calcio", "foot", "futbol", "fussball", "stade",
    # Articles kept out of ``_STOPWORDS`` so name similarity still sees them,
    # but dropped from the merge key ("La Coru\u00f1a", "Le Havre").
    "la", "las", "le", "los",
} | _STOPWORDS

#: A single provider token that is just one club's name abbreviated, expanded to
#: the tokens it stands for.  ``munich``/``münchen`` and ``wolves`` are the same
#: club under two providers; ``inter`` is ``Internazionale Milano``.
_TOKEN_ALIASES: dict[str, tuple[str, ...]] = {
    "inter": ("internazionale", "milano"),
    "munich": ("munchen",),
    "rennais": ("rennes",),
    "wolves": ("wolverhampton", "wanderers"),
}

#: Whole-name provider quirks that a token view cannot reconcile, keyed by the
#: normalised name and mapped to the normalised canonical name.  Every entry is a
#: pair actually observed in the ingested data (never guessed), so applying them
#: cannot merge two clubs that a provider really keeps distinct.
_CLUB_ALIASES: dict[str, str] = {
    # Spain
    "alaves": "deportivo alaves",
    "ath bilbao": "athletic bilbao",
    "athletic club": "athletic bilbao",
    "ath madrid": "atletico madrid",
    "betis": "real betis",
    "real betis balompie": "real betis",
    "celta": "celta vigo",
    "la coruna": "deportivo la coruna",
    "espanol": "espanyol",
    "rcd espanyol de barcelona": "espanyol",
    "real sociedad de futbol": "real sociedad",
    "sociedad": "real sociedad",
    "rayo vallecano de madrid": "rayo vallecano",
    "vallecano": "rayo vallecano",
    "oviedo": "real oviedo",
    "valladolid": "real valladolid",
    "real valladolid cf": "real valladolid",
    "santander": "racing santander",
    "real racing club de santander": "racing santander",
    # England
    "afc bournemouth": "bournemouth",
    "brighton hove albion fc": "brighton",
    "coventry city fc": "coventry",
    "ipswich town fc": "ipswich",
    "leeds united fc": "leeds",
    "leicester city fc": "leicester",
    "newcastle united fc": "newcastle",
    "tottenham hotspur fc": "tottenham",
    "west ham united fc": "west ham",
    # France
    "angers sco": "angers",
    "estac troyes": "troyes",
    "olympique de marseille": "marseille",
    "olympique lyonnais": "lyon",
    "racing club de lens": "lens",
    "rc strasbourg alsace": "strasbourg",
    # Portugal / Netherlands / Austria: the cup row is short, the league row long.
    "sporting cp": "sporting clube de portugal",
    "sporting lisbon": "sporting clube de portugal",
    "feyenoord rotterdam": "feyenoord",
    "lask linz": "lask",
    # The English cup spellings of clubs our result providers file under the
    # native name, so a fixture list written in English lands on their history.
    "sparta prague": "ac sparta praha",
    "slavia prague": "slavia praha",
    "olympiacos": "olympiakos piraeus",
    "pafos": "paphos fc",
    "ferencvaros": "ferencvarosi tc",
    "sint truiden": "sint truidense vv",
    # "ø" is not a letter NFKD knows how to expand, so an exporter that drops
    # the accent leaves "Bodø/Glimt" as "Bod Glimt".
    "bod glimt": "bodo glimt",
    "bodoglimt": "bodo glimt",
}


#: Competition "country" spellings that providers disagree on.  Leagues are
#: merged across providers by ``slugify(f"{country}-{name}")``, so a provider
#: filing the UEFA club competitions under another country would open a second
#: local league (with no history) instead of adding to the one we already have.
#: API-Football says "World" for these; the stored rows say "Europe".
LEAGUE_COUNTRY_ALIASES: dict[str, str] = {
    "uefa champions league": "Europe",
    "uefa europa league": "Europe",
    "uefa europa conference league": "Europe",
    "uefa conference league": "Europe",
    "uefa super cup": "Europe",
}

#: Competition *names* providers spell differently, mapped to the name the
#: stored row uses.  UEFA renamed the third competition in 2024 (dropping
#: "Europa"); API-Football still files it under both spellings, and a second
#: league row would carry no history at all.
LEAGUE_NAME_ALIASES: dict[str, str] = {
    "uefa europa conference league": "UEFA Conference League",
}


def canonical_league_name(name: str | None) -> str:
    """The competition name the stored row uses, across providers."""
    if not name:
        return name or ""
    return LEAGUE_NAME_ALIASES.get(normalize_name(name), name)


def normalize_name(value: str | None) -> str:
    """Fold a provider name to a comparable key.

    Diacritics are stripped, apostrophes removed and every other run of
    non-alphanumeric characters collapsed to a single space, so ``"Hurac\u00e1n"``
    and ``"Huracan"`` share a key and ``"Newell's Old Boys"`` matches
    ``"Newells Old Boys"``.
    """
    if not value:
        return ""
    # openfootball labels cross-border cups with the club's association in
    # brackets -- "Manchester City FC (ENG)", "FC Bayern M\u00fcnchen (GER)".  The
    # suffix carries no club identity, so drop a trailing parenthetical code
    # before folding.
    value = re.sub(r"\s*\([A-Za-z]{2,4}\)\s*$", "", value.strip())
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = stripped.casefold()
    for quote in ("'", "\u2019", "\u2018", "`"):
        lowered = lowered.replace(quote, "")
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


def canonical_team_key(name: str | None) -> str:
    """Normalised key with the observed provider aliases applied."""
    key = normalize_name(name)
    return TEAM_ALIASES.get(key, key)


#: Short forms readers type for a club, mapped to the words the stored name
#: uses.  A question says "psg"; the row says "Paris Saint Germain".  Only
#: forms we have really seen asked for are listed, as with the team aliases.
CLUB_ABBREVIATIONS: dict[str, str] = {
    "psg": "paris saint germain",
    "barca": "barcelona",
    "man city": "manchester city",
    "man utd": "manchester united",
    "juve": "juventus",
}


def expand_abbreviations(text: str) -> set[str]:
    """The words of ``text``, plus those that any short form in it stands for.

    Keeps a plain containment test working when the reader and the stored row
    spell the same club differently.  A short form only counts as a whole word,
    and longer ones are applied first so "man city" is not eaten by "man".
    """
    padded = f" {text} "
    tokens = set(padded.split())
    for short in sorted(CLUB_ABBREVIATIONS, key=len, reverse=True):
        if f" {short} " in padded:
            tokens.update(CLUB_ABBREVIATIONS[short].split())
    return tokens


def canonical_league_country(name: str | None, country: str | None) -> str | None:
    """The country the stored row for a competition already uses.

    Only cross-border competitions whose providers disagree need this; every
    national league keeps whatever country its provider reported.
    """
    alias = LEAGUE_COUNTRY_ALIASES.get(normalize_name(name))
    if alias and normalize_name(country) in ("", "world", "intl"):
        return alias
    return country


def _tokens(name: str) -> set[str]:
    return {t for t in normalize_name(name).split() if t and t not in _STOPWORDS}


def _merge_tokens(name: str | None) -> set[str]:
    """Tokens for the club-identity key: stop-forms, year tokens and the
    observed per-token aliases applied."""
    expanded: set[str] = set()
    for token in normalize_name(name).split():
        if not token or token in _FORM_TOKENS or token.isdigit():
            continue
        expanded.update(_TOKEN_ALIASES.get(token, (token,)))
    return expanded


def team_merge_key(name: str | None) -> tuple[str, ...]:
    """A club-identity key that ignores provider decoration and known quirks.

    ``"FC Barcelona"`` and ``"Barcelona"`` collapse to the same key, as do
    ``"1. FC K\u00f6ln"``/``"1. FC K\u00f6ln 1"`` and, through the observed alias
    table, ``"Bayern Munich"``/``"FC Bayern M\u00fcnchen"`` and ``"Wolves"``/
    ``"Wolverhampton Wanderers FC"``.  Used by the maintenance pass that merges a
    club split across providers into one row; an empty cluster (a name made only
    of form words) returns ``()`` and is left untouched.
    """
    key = canonical_team_key(name)
    key = _CLUB_ALIASES.get(key, key)
    return tuple(sorted(_merge_tokens(key)))


def token_similarity(a: str, b: str) -> float:
    """Score how alike two club names are, in ``[0, 1]``.

    Exact token sets score 1.  Remaining tokens are matched exactly, by single
    initial (``"L.P."`` -> ``"La Plata"``) or by a shared prefix, and the total
    is divided by the larger token count so surplus words lower the score.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    matched = 0.0
    for token in ta:
        if token in tb:
            matched += 1.0
        elif len(token) == 1 and any(other.startswith(token) for other in tb):
            matched += 0.8
        elif len(token) >= 4 and any(
            other.startswith(token) or token.startswith(other) for other in tb
        ):
            matched += 0.85
    return matched / max(len(ta), len(tb))


def best_match(
    query: str, candidates: Iterable[str], *, threshold: float = 0.8, margin: float = 0.15
) -> str | None:
    """The single closest candidate, only when it clearly wins.

    Returns ``None`` when nothing reaches ``threshold`` or when the runner-up is
    within ``margin`` (an ambiguous call is not made).
    """
    best: str | None = None
    best_score = 0.0
    second = 0.0
    for candidate in candidates:
        score = token_similarity(query, candidate)
        if score > best_score:
            second = best_score
            best_score = score
            best = candidate
        elif score > second:
            second = score
    if best is not None and best_score >= threshold and (best_score - second) >= margin:
        return best
    return None


def subset_match(query: str, candidates: Iterable[str]) -> str | None:
    """The single candidate whose tokens are a strict superset of the query's.

    Catches a provider dropping a qualifier (``"Belgrano"`` for ``"Belgrano de
    C\u00f3rdoba"``).  Only made when exactly one candidate qualifies, so a bare
    name that fits two clubs is left unresolved rather than mis-assigned.
    """
    query_tokens = _tokens(query)
    if not query_tokens or not any(len(t) >= 4 for t in query_tokens):
        return None
    matches = [
        candidate
        for candidate in candidates
        if query_tokens < _tokens(candidate)
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def resolve_key(name: str | None, keys: Iterable[str]) -> str | None:
    """Resolve a provider club name to one of ``keys`` (canonical keys).

    An exact canonical key wins; otherwise a conservative scored match, and
    finally a unique shortened-name match.
    """
    if not name:
        return None
    key = canonical_team_key(name)
    candidate_keys = list(keys)
    if key in candidate_keys:
        return key
    scored = best_match(key, candidate_keys)
    if scored is not None:
        return scored
    return subset_match(key, candidate_keys)
