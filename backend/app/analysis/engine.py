"""Analysis engine: DB + model + markets + simulation + value in one place."""

from __future__ import annotations

import threading
from collections import Counter, OrderedDict
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.analysis import features, identity, memo
from app.analysis.report import (
    derive_confidence,
    derive_findings,
    derive_limitations,
    derive_opportunities,
)
from app.analysis.value import assess_selection, decimal_odds_from_probability, fair_probabilities
from app.config import get_settings
from app.db.models import League, Match, MatchOdd, MatchStat, ModelRun, Prediction, Season, Team
from app.models.dixon_coles import (
    MODEL_NAME,
    MODEL_VERSION,
    DixonColesModel,
    MatchRecord,
    with_rest,
)
from app.models.markets import all_markets, asian_handicap
from app.models.ratings import base_rates, strength_of_schedule, team_ratings
from app.models.simulate import simulate
from app.models.xg import blend_lambdas
from app.timezones import iso_utc

FINISHED = "finished"


@dataclass
class AnalysisOptions:
    monte_carlo_simulations: int | None = None
    xg_weight: float = 0.0
    seed: int | None = None
    kmax: int | None = None
    halflife_days: float | None = None
    #: L2 penalty on attack/defence strengths.  A small value shrinks teams with
    #: few matches toward the league average (useful early in a season and for
    #: promoted sides); None uses the configured default.
    regularization: float | None = None
    #: Fit and apply a rest-difference term (how many more days one side has had
    #: off).  The coefficient is estimated from the training matches, not
    #: hand-tuned; it is off by default.
    use_rest: bool = False
    #: Fit and apply a recent-form term: the points-per-game differential between
    #: the two sides over their last ``form_window`` matches.  As with rest, the
    #: coefficient is estimated from the training matches, so the data decides
    #: how much form matters rather than it being weighted by hand.  None follows
    #: the configured default.
    use_form: bool | None = None
    form_window: int | None = None
    lookback_days: int = 1200
    include_asian_handicap: bool = True
    manual_odds: list[dict[str, Any]] | None = None
    #: When the fixture's own competition holds too little completed history,
    #: fall back to sibling competitions in the same country (matching clubs by
    #: name).  Set False to analyse strictly within the competition.
    allow_peer_competitions: bool = True
    #: Override the training cutoff for every fixture in a batch.  The prediction
    #: bot passes one cutoff for the whole batch so fixtures in the same
    #: competition share a single fitted model instead of refitting per kickoff.
    as_of: datetime | None = None
    persist: bool = True


def load_match(db: Session, match_id: int) -> Match:
    match = db.get(Match, match_id)
    if match is None:
        raise LookupError(f"match {match_id} not found")
    return match


def training_records(db: Session, league_id: int, before: datetime, lookback_days: int) -> list[MatchRecord]:
    """Completed matches in one competition, before ``before``, within the window."""
    return records_for_leagues(db, [league_id], before, lookback_days)


def records_for_leagues(
    db: Session, league_ids: list[int], before: datetime, lookback_days: int
) -> list[MatchRecord]:
    """Completed matches across a set of competitions, oldest first."""
    if not league_ids:
        return []
    return memo.memo(
        ("records", tuple(league_ids), before, lookback_days),
        lambda: _load_records_for_leagues(db, league_ids, before, lookback_days),
    )


def _load_records_for_leagues(
    db: Session, league_ids: list[int], before: datetime, lookback_days: int
) -> list[MatchRecord]:
    since = before - timedelta(days=lookback_days)
    team_names = features.team_names_map(db)
    stmt = (
        select(Match)
        .where(
            Match.league_id.in_(league_ids),
            Match.status == FINISHED,
            Match.kickoff_utc < before,
            Match.kickoff_utc >= since,
            Match.home_goals.is_not(None),
            Match.away_goals.is_not(None),
        )
        .order_by(Match.kickoff_utc.asc())
    )
    records: list[MatchRecord] = []
    for m in db.execute(stmt).scalars():
        records.append(
            MatchRecord(
                home=team_names.get(m.home_team_id, str(m.home_team_id)),
                away=team_names.get(m.away_team_id, str(m.away_team_id)),
                home_goals=int(m.home_goals),
                away_goals=int(m.away_goals),
                date=m.kickoff_utc,
                league=str(m.league_id),
            )
        )
    return records


def _peer_league_ids(db: Session, league: League | None) -> list[int]:
    """Other competitions in the same country as ``league``."""
    if league is None or not league.country:
        return []
    stmt = select(League.id).where(
        League.country == league.country, League.id != league.id
    )
    return list(db.execute(stmt).scalars())


def _team_key_index(db: Session, league_ids: list[int]) -> dict[str, Team]:
    """Clubs appearing in ``league_ids``, keyed by canonical name."""
    if not league_ids:
        return {}
    team_ids: set[int] = set(
        db.execute(select(Match.home_team_id).where(Match.league_id.in_(league_ids))).scalars()
    )
    team_ids.update(
        db.execute(select(Match.away_team_id).where(Match.league_id.in_(league_ids))).scalars()
    )
    if not team_ids:
        return {}
    index: dict[str, Team] = {}
    for team in db.execute(select(Team).where(Team.id.in_(team_ids))).scalars():
        index.setdefault(identity.canonical_team_key(team.name), team)
    return index


def _club_handle_index(records: list[MatchRecord]) -> dict[tuple[str, ...], str]:
    """One display handle per club, keyed by identity, from a pool of records.

    The same club arrives under a domestic spelling and a cup spelling
    ("Manchester City" and "Manchester City FC (ENG)").  Both fold to the same
    :func:`identity.team_merge_key`, so this returns the most common original
    spelling for each key -- the model then sees a single team per club and can
    pool its domestic and cup matches.
    """
    counts: dict[tuple[str, ...], Counter[str]] = {}
    for record in records:
        for name in (record.home, record.away):
            key = identity.team_merge_key(name)
            if key:
                counts.setdefault(key, Counter())[name] += 1
    return {key: c.most_common(1)[0][0] for key, c in counts.items()}


def _club_handle(name: str, index: dict[tuple[str, ...], str]) -> str:
    return index.get(identity.team_merge_key(name), name)


def _linked_league_ids(db: Session, league_id: int, names: list[str]) -> list[int]:
    """Competitions, other than ``league_id``, that hold the given clubs.

    Used to pool a cross-border cup's clubs' domestic leagues: a cup row
    ("Manchester City FC (ENG)") is matched to the domestic row ("Manchester
    City") by club identity, so the cup fixture trains on both leagues plus the
    cup.  Clubs from a league we have not ingested simply contribute nothing.
    """
    keys = {identity.team_merge_key(n) for n in names}
    keys.discard(())
    if not keys:
        return []
    found: set[int] = set()
    for team in db.execute(select(Team)).scalars():
        if team.league_id is None or team.league_id == league_id:
            continue
        if identity.team_merge_key(team.name) in keys:
            found.add(team.league_id)
    return sorted(found)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


#: Minimum completed matches with a recorded half-time score before half-time
#: markets are modelled at all (a share needs a stable sample).
MIN_HT_MATCHES = 30


def _halftime_goal_share(
    db: Session, league_ids: list[int], before: datetime, lookback_days: int
) -> dict[str, Any] | None:
    """Estimate the first-half share of full-time goals from stored HT scores.

    Returns ``{"home": share, "away": share, "n_matches": n}`` computed directly
    from finished matches that carry a half-time score, or ``None`` when there is
    too little data or the implied share is implausible.  This lets half-time
    markets be modelled as a proportional split of the fitted full-time rates
    rather than an invented constant.
    """
    if not league_ids:
        return None
    return memo.memo(
        ("ht_share", tuple(league_ids), before, lookback_days),
        lambda: _load_halftime_goal_share(db, league_ids, before, lookback_days),
    )


def _load_halftime_goal_share(
    db: Session, league_ids: list[int], before: datetime, lookback_days: int
) -> dict[str, Any] | None:
    since = before - timedelta(days=lookback_days)
    rows = db.execute(
        select(
            Match.ht_home_goals, Match.ht_away_goals, Match.home_goals, Match.away_goals
        ).where(
            Match.league_id.in_(league_ids),
            Match.status == FINISHED,
            Match.kickoff_utc < before,
            Match.kickoff_utc >= since,
            Match.ht_home_goals.is_not(None),
            Match.ht_away_goals.is_not(None),
            Match.home_goals.is_not(None),
            Match.away_goals.is_not(None),
        )
    ).all()
    n = len(rows)
    if n < MIN_HT_MATCHES:
        return None
    ht_home = sum(int(r[0]) for r in rows)
    ht_away = sum(int(r[1]) for r in rows)
    ft_home = sum(int(r[2]) for r in rows)
    ft_away = sum(int(r[3]) for r in rows)
    if ft_home <= 0 or ft_away <= 0:
        return None
    share_home = ht_home / ft_home
    share_away = ht_away / ft_away
    if not (0.2 <= share_home <= 0.8 and 0.2 <= share_away <= 0.8):
        return None
    return {"home": share_home, "away": share_away, "n_matches": n}


def _has_secondary_stats(db: Session, league_ids: list[int]) -> bool:
    """Whether any stored match stat carries corner or card data."""
    if not league_ids:
        return False
    row = db.execute(
        select(MatchStat.id)
        .join(Match, Match.id == MatchStat.match_id)
        .where(
            Match.league_id.in_(league_ids),
            or_(
                MatchStat.corners.is_not(None),
                MatchStat.yellow_cards.is_not(None),
                MatchStat.red_cards.is_not(None),
            ),
        )
        .limit(1)
    ).first()
    return row is not None


def _fair_odds_map(
    one_x_two: dict[str, float],
    double_chance: dict[str, float],
    totals: dict[str, dict[str, float]],
    btts: dict[str, float],
    team_totals: dict[str, dict[str, dict[str, float]]],
) -> dict[str, Any]:
    """Fair (margin-free) decimal odds = 1 / probability for each market."""

    def flat(probs: dict[str, float]) -> dict[str, float | None]:
        return {k: decimal_odds_from_probability(v) for k, v in probs.items()}

    return {
        "one_x_two": flat(one_x_two),
        "double_chance": flat(double_chance),
        "totals": {line: flat(entry) for line, entry in totals.items()},
        "btts": flat(btts),
        "team_totals": {team: {line: flat(entry) for line, entry in lines.items()}
                        for team, lines in team_totals.items()},
    }


_FIT_CACHE: OrderedDict[tuple[Any, ...], DixonColesModel] = OrderedDict()
_FIT_CACHE_MAX = 8
_FIT_CACHE_LOCK = threading.Lock()


def _fit_signature(records: list[MatchRecord], halflife: float,
                   regularization: float, extra_teams: frozenset[str],
                   use_rest: bool, per_league_intercepts: bool,
                   use_form: bool = False, form_window: int = 5) -> tuple[Any, ...]:
    roster = frozenset([r.home for r in records] + [r.away for r in records])
    goals = sum(r.home_goals + r.away_goals for r in records)
    leagues = frozenset(r.league for r in records if r.league)
    return (
        len(records),
        records[0].date,
        records[-1].date,
        goals,
        halflife,
        regularization,
        hash(roster),
        hash(extra_teams),
        use_rest,
        per_league_intercepts,
        use_form,
        form_window,
        hash(leagues),
    )


def fit_model(records: list[MatchRecord], halflife_days: float | None,
              regularization: float = 0.0,
              extra_teams: frozenset[str] = frozenset(),
              use_rest: bool = False,
              per_league_intercepts: bool = False,
              use_form: bool = False,
              form_window: int = 5) -> DixonColesModel:
    """Fit the Dixon-Coles model, reusing an identical recent fit.

    The prediction bot analyses many fixtures at once; fixtures in the same
    competition share the same training set, so caching by the training data's
    own signature turns fifty fits into one fit per competition.
    """
    settings = get_settings()
    halflife = halflife_days or settings.time_decay_halflife_days
    if use_rest:
        records = with_rest(records)
    if not records:
        return DixonColesModel.fit(
            records, halflife_days=halflife, regularization=regularization,
            extra_teams=sorted(extra_teams), use_rest=use_rest,
            per_league_intercepts=per_league_intercepts,
            use_form=use_form, form_window=form_window,
        )
    key = _fit_signature(records, halflife, regularization, extra_teams, use_rest,
                         per_league_intercepts, use_form, form_window)
    with _FIT_CACHE_LOCK:
        cached = _FIT_CACHE.get(key)
        if cached is not None:
            _FIT_CACHE.move_to_end(key)
            return cached
    model = DixonColesModel.fit(
        records, halflife_days=halflife, regularization=regularization,
        extra_teams=sorted(extra_teams), use_rest=use_rest,
        per_league_intercepts=per_league_intercepts,
        use_form=use_form, form_window=form_window,
    )
    with _FIT_CACHE_LOCK:
        _FIT_CACHE[key] = model
        while len(_FIT_CACHE) > _FIT_CACHE_MAX:
            _FIT_CACHE.popitem(last=False)
    return model


def _window_form(matches: list[features.MatchSummary], window: int) -> dict[str, Any]:
    stats = features.form_summary(matches[:window])
    return _form_to_dict(stats)


def _form_to_dict(stats: features.FormStats) -> dict[str, Any]:
    return {
        "n_matches": stats.n_matches,
        "wins": stats.wins,
        "draws": stats.draws,
        "losses": stats.losses,
        "goals_for": stats.goals_for,
        "goals_against": stats.goals_against,
        "avg_goals_for": stats.avg_goals_for,
        "avg_goals_against": stats.avg_goals_against,
        "clean_sheets": stats.clean_sheets,
        "failed_to_score": stats.failed_to_score,
        "btts_frequency": stats.btts_frequency,
        "over_under": stats.over_under,
        "first_half_goals_for": stats.first_half_goals_for,
        "second_half_goals_for": stats.second_half_goals_for,
        "points_per_match": stats.points_per_match,
        "notes": stats.notes,
    }


def _split_to_dict(split: dict[str, features.FormStats]) -> dict[str, Any]:
    return {key: _form_to_dict(value) for key, value in split.items()}


def _odds_model_probability(markets: dict[str, Any], market: str, selection: str) -> float | None:
    if market == "1x2":
        return markets["one_x_two"].get(selection)
    if market.startswith("o/u_"):
        line = market.split("_", 1)[1]
        entry = markets["totals"].get(line)
        if entry is None:
            return None
        return entry.get(selection)
    if market == "btts":
        return markets["btts"].get(selection)
    if market.startswith("ah_"):
        return None  # handled separately
    return None


def _value_analysis(
    markets: dict[str, Any],
    stored_odds: list[MatchOdd],
    manual_odds: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    if manual_odds:
        odds_rows = [
            {"bookmaker": o.get("bookmaker", "manual"), "market": o["market"],
             "selection": o["selection"], "decimal_odds": float(o["decimal_odds"])}
            for o in manual_odds
        ]
    else:
        odds_rows = [
            {"bookmaker": o.bookmaker, "market": o.market, "selection": o.selection,
             "decimal_odds": o.decimal_odds}
            for o in stored_odds
        ]
    if not odds_rows:
        return {"available": False, "note": "No odds stored or supplied for this match."}

    by_market: dict[str, list[dict[str, Any]]] = {}
    for row in odds_rows:
        by_market.setdefault(row["market"], []).append(row)

    # Fair probabilities must come from one bookmaker's complete market: the
    # margin only cancels inside a book's own 1x2 (or O/U, BTTS) set.  Combining
    # every book's prices sums several overrounds and collapses each fair
    # probability towards zero, which would fake a huge edge for every selection.
    fair_by: dict[tuple[str, str, str], float] = {}
    for market, rows in by_market.items():
        full = {r["selection"] for r in rows}
        by_book: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_book.setdefault(row["bookmaker"], []).append(row)
        for book, brows in by_book.items():
            if len(brows) < 2 or {r["selection"] for r in brows} != full:
                continue
            try:
                fairs = fair_probabilities([r["decimal_odds"] for r in brows])
            except ValueError:
                continue
            for row, value in zip(brows, fairs, strict=True):
                fair_by[(market, book, row["selection"])] = value

    assessments: list[dict[str, Any]] = []
    for market, rows in by_market.items():
        for row in rows:
            model_p = _odds_model_probability(markets, market, row["selection"])
            if model_p is None:
                continue
            assessment = assess_selection(
                selection=f"{market}:{row['selection']}",
                model_probability=model_p,
                decimal_odds=row["decimal_odds"],
                market_fair_probability=fair_by.get(
                    (market, row["bookmaker"], row["selection"])
                ),
            )
            assessments.append(
                {
                    "market": market,
                    "selection": row["selection"],
                    "bookmaker": row["bookmaker"],
                    "model_probability": assessment.model_probability,
                    "decimal_odds": assessment.decimal_odds,
                    "implied_probability": assessment.implied_probability,
                    "market_fair_probability": assessment.market_fair_probability,
                    "expected_value": assessment.expected_value,
                    "break_even_probability": assessment.break_even_probability,
                    "edge": assessment.edge,
                    "is_positive_ev": assessment.is_positive_ev,
                    "confidence": assessment.confidence,
                }
            )
    assessments.sort(key=lambda a: a["expected_value"], reverse=True)
    return {
        "available": True,
        "assessments": assessments,
        "caveats": [
            "Expected value is a model output, not a promise of profit.",
            "Compare like-for-like: fair probabilities need a full market to remove the bookmaker margin.",
            "Odds move; re-check before drawing conclusions.",
        ],
    }


def build_analysis(db: Session, match_id: int, options: AnalysisOptions | None = None) -> dict[str, Any]:
    opts = options or AnalysisOptions()
    settings = get_settings()
    sims = opts.monte_carlo_simulations or settings.monte_carlo_simulations
    kmax = opts.kmax or settings.max_goals_grid

    match = load_match(db, match_id)
    home = db.get(Team, match.home_team_id)
    away = db.get(Team, match.away_team_id)
    league = db.get(League, match.league_id)
    season = db.get(Season, match.season_id) if match.season_id else None
    if home is None or away is None:
        raise LookupError("match is missing one of its teams")

    before = _aware(opts.as_of) if opts.as_of is not None else _aware(match.kickoff_utc)
    records = training_records(db, match.league_id, before, opts.lookback_days)
    training_league_ids = [match.league_id]
    scope: dict[str, Any] = {
        "cross_competition": False,
        "own_league_matches": len(records),
        "peer_league_ids": [],
        "n_training_matches": len(records),
        "note": None,
    }
    model_home, model_away = home.name, away.name
    feat_home_id, feat_away_id = home.id, away.id

    if len(records) < settings.min_matches_for_model and opts.allow_peer_competitions and league:
        peer_ids = _peer_league_ids(db, league)
        peer_records = records_for_leagues(db, peer_ids, before, opts.lookback_days)
        if len(peer_records) >= settings.min_matches_for_model:
            index = _team_key_index(db, peer_ids)
            home_key = identity.resolve_key(home.name, index.keys())
            away_key = identity.resolve_key(away.name, index.keys())
            if home_key and away_key and home_key != away_key:
                records = peer_records
                training_league_ids = peer_ids
                model_home = index[home_key].name
                model_away = index[away_key].name
                feat_home_id = index[home_key].id
                feat_away_id = index[away_key].id
                scope.update(
                    cross_competition=True,
                    peer_league_ids=peer_ids,
                    n_training_matches=len(records),
                    model_home_team=model_home,
                    model_away_team=model_away,
                    note=(
                        "This competition's own history is too thin to fit the model; it was "
                        f"fitted on {len(records)} completed matches from the {league.country} "
                        "sibling competition, matching the clubs by name across providers."
                    ),
                )

    # Cross-border cups (UEFA Champions League, Europa League, ...) draw clubs
    # from many domestic leagues.  Fitting on the cup alone leaves each club with
    # one or two matches and makes strengths meaningless across leagues, so pool
    # the cup with the two clubs' domestic leagues and fit a per-league intercept
    # -- the cup matches bridge the leagues and the intercepts keep every club's
    # attack/defence measured against its own league's scoring level.
    per_league_intercepts = False
    if (
        opts.allow_peer_competitions
        and league is not None
        and league.country
        and league.country.casefold() in settings.international_country_list
    ):
        linked_ids = _linked_league_ids(db, league.id, [home.name, away.name])
        if linked_ids:
            pool_ids = [match.league_id, *linked_ids]
            pool_records = records_for_leagues(db, pool_ids, before, opts.lookback_days)
            handle_index = _club_handle_index(pool_records)
            records = [
                replace(
                    r,
                    home=_club_handle(r.home, handle_index),
                    away=_club_handle(r.away, handle_index),
                )
                for r in pool_records
            ]
            training_league_ids = pool_ids
            model_home = _club_handle(home.name, handle_index)
            model_away = _club_handle(away.name, handle_index)
            per_league_intercepts = True
            scope.update(
                cross_competition=True,
                peer_league_ids=linked_ids,
                n_training_matches=len(records),
                model_home_team=model_home,
                model_away_team=model_away,
                note=(
                    f"{league.name} is a cross-border competition; the model was fitted on "
                    f"{len(records)} completed matches from the cup and the clubs' domestic "
                    "leagues, with a per-league intercept so strengths are comparable "
                    "across leagues. Clubs from a league not ingested carry the cup's own "
                    "matches only."
                ),
            )

    if len(records) < settings.min_matches_for_model:
        raise ValueError(
            f"only {len(records)} completed matches available for this competition; "
            f"need at least {settings.min_matches_for_model} to fit a model. "
            "Ingest more historical results first."
        )

    regularization = (
        settings.model_regularization if opts.regularization is None else opts.regularization
    )
    # Form is a fitted covariate like rest: whether it is on, and the window it
    # averages over, follow the configured defaults unless the request overrides.
    use_form = settings.model_use_form if opts.use_form is None else opts.use_form
    form_window = settings.form_window if opts.form_window is None else opts.form_window
    roster = {r.home for r in records} | {r.away for r in records}
    # Teams with no completed match in the window are still modelled, at the
    # league average, so a promoted or brand-new side can be analysed rather than
    # refused.  They carry no evidence, so the grade is held down and a note says
    # so (see ``derive_limitations``).
    missing_teams = frozenset(t for t in (model_home, model_away) if t not in roster)
    model = fit_model(
        records, opts.halflife_days, regularization,
        extra_teams=missing_teams, use_rest=opts.use_rest,
        per_league_intercepts=per_league_intercepts,
        use_form=use_form, form_window=form_window,
    )
    if not (model.has_team(model_home) and model.has_team(model_away)):
        missing = [name for name in (model_home, model_away) if not model.has_team(name)]
        raise ValueError(
            f"no historical matches for {', '.join(missing)} in this competition; "
            "cannot estimate strengths for them yet."
        )

    rest_diff = None
    if opts.use_rest and model.params.rest_coeff is not None:
        home_rest = features.rest_and_congestion(db, feat_home_id, before).get("rest_days")
        away_rest = features.rest_and_congestion(db, feat_away_id, before).get("rest_days")
        if home_rest is not None and away_rest is not None:
            rest_diff = float(home_rest - away_rest)
    # Recent-form differential (points per game over the fitted window), so the
    # prediction uses the same feature the model was trained with.
    form_diff = None
    if use_form and model.params.form_coeff is not None:
        mean = model.params.form_mean or 0.0
        home_form = features.form_summary(
            features.recent_form(db, feat_home_id, before, window=form_window)
        ).points_per_match
        away_form = features.form_summary(
            features.recent_form(db, feat_away_id, before, window=form_window)
        ).points_per_match
        form_diff = float(
            (home_form if home_form is not None else mean)
            - (away_form if away_form is not None else mean)
        )
    lambda_home, lambda_away = model.expected_goals(
        model_home, model_away, rest_diff=rest_diff, form_diff=form_diff,
        league=str(match.league_id),
    )
    rho = model.params.rho

    # Optional xG blending.
    xg_weight = max(0.0, min(1.0, opts.xg_weight))
    home_recent = features.recent_form(db, feat_home_id, before, window=15)
    away_recent = features.recent_form(db, feat_away_id, before, window=15)
    home_xg = features.aggregate_xg(home_recent)
    away_xg = features.aggregate_xg(away_recent)
    xg_used = False
    if xg_weight > 0 and home_xg["xg_for_avg"] and away_xg["xg_for_avg"]:
        from app.models.xg import xg_expected_goals

        home_adv_factor = __import__("math").exp(model.params.home_advantage)
        xg_home, xg_away = xg_expected_goals(
            home_xg_for=home_xg["xg_for_avg"],
            home_xg_against=home_xg["xg_against_avg"] or home_xg["xg_for_avg"],
            away_xg_for=away_xg["xg_for_avg"],
            away_xg_against=away_xg["xg_against_avg"] or away_xg["xg_for_avg"],
            home_advantage_factor=home_adv_factor,
        )
        lambda_home = blend_lambdas(lambda_home, xg_home, xg_weight)
        lambda_away = blend_lambdas(lambda_away, xg_away, xg_weight)
        xg_used = True

    market_probs = all_markets(lambda_home, lambda_away, kmax=kmax, rho=rho)
    markets_dict = {
        "one_x_two": market_probs.one_x_two,
        "double_chance": market_probs.double_chance,
        "totals": market_probs.totals,
        "btts": market_probs.btts,
        "team_totals": market_probs.team_totals,
        "correct_scores": market_probs.correct_scores,
        "total_goals_distribution": market_probs.total_goals_distribution,
    }

    # Half-time markets: split the fitted full-time rates by the competition's
    # observed first-half goal share.  Only shown when the share is estimable.
    ht_share = _halftime_goal_share(db, training_league_ids, before, opts.lookback_days)
    if ht_share:
        lambda_ht_home = lambda_home * ht_share["home"]
        lambda_ht_away = lambda_away * ht_share["away"]
        ht_probs = all_markets(lambda_ht_home, lambda_ht_away, kmax=kmax, rho=rho,
                               total_goal_max=6)
        markets_dict["half_time"] = {
            "available": True,
            "expected_goals": {
                "home": lambda_ht_home,
                "away": lambda_ht_away,
                "total": lambda_ht_home + lambda_ht_away,
            },
            "one_x_two": ht_probs.one_x_two,
            "double_chance": ht_probs.double_chance,
            "totals": ht_probs.totals,
            "btts": ht_probs.btts,
            "correct_scores": ht_probs.correct_scores,
            "based_on_matches": ht_share["n_matches"],
            "goal_share": {"home": ht_share["home"], "away": ht_share["away"]},
            "note": (
                "Half-time markets split the fitted full-time goal rates by the "
                f"first-half share observed across {ht_share['n_matches']} matches "
                "with recorded half-time scores. They are an approximation of the "
                "first-half process, not a separately fitted model."
            ),
        }
    else:
        markets_dict["half_time"] = {
            "available": False,
            "note": (
                "Not enough recorded half-time scores in this competition's history "
                "to model half-time markets."
            ),
        }

    markets_dict["fair_odds"] = _fair_odds_map(
        market_probs.one_x_two,
        market_probs.double_chance,
        market_probs.totals,
        market_probs.btts,
        market_probs.team_totals,
    )
    if markets_dict["half_time"]["available"]:
        markets_dict["fair_odds"]["half_time"] = {
            "one_x_two": {k: decimal_odds_from_probability(v)
                          for k, v in markets_dict["half_time"]["one_x_two"].items()},
            "totals": {line: {k: decimal_odds_from_probability(v) for k, v in entry.items()}
                       for line, entry in markets_dict["half_time"]["totals"].items()},
            "btts": {k: decimal_odds_from_probability(v)
                     for k, v in markets_dict["half_time"]["btts"].items()},
        }

    simulation = simulate(
        lambda_home, lambda_away, n_simulations=sims, kmax=kmax, rho=rho, seed=opts.seed
    )

    if opts.include_asian_handicap:
        from app.models.poisson import score_matrix as _sm

        matrix = _sm(lambda_home, lambda_away, kmax=kmax, rho=rho)
        markets_dict["asian_handicap"] = {
            f"{line:+}": asian_handicap(matrix, line, home=True)
            for line in (-1.5, -1.0, -0.5, -0.25, 0.0, 0.25, 0.5, 1.0, 1.5)
        }

    ratings_table = team_ratings(model)
    ratings_map = {r.team: r for r in ratings_table}
    ratings_dict = {
        "home": {
            "attack_multiplier": ratings_map[model_home].attack_multiplier,
            "defence_multiplier": ratings_map[model_home].defence_multiplier,
            "net_strength": ratings_map[model_home].net_strength,
        },
        "away": {
            "attack_multiplier": ratings_map[model_away].attack_multiplier,
            "defence_multiplier": ratings_map[model_away].defence_multiplier,
            "net_strength": ratings_map[model_away].net_strength,
        },
        "table": [
            {
                "team": r.team,
                "attack_multiplier": r.attack_multiplier,
                "defence_multiplier": r.defence_multiplier,
                "net_strength": r.net_strength,
            }
            for r in ratings_table
        ],
        "base_rates": base_rates(model),
        "strength_of_schedule": strength_of_schedule(model, records),
    }

    h2h = features.head_to_head(db, feat_home_id, feat_away_id, before, limit=10)

    form = {
        "home": {
            "last5": _window_form(home_recent, 5),
            "last10": _window_form(home_recent, 10),
            "last15": _window_form(home_recent, 15),
            "splits": _split_to_dict(features.split_home_away(home_recent)),
            "schedule": features.rest_and_congestion(db, feat_home_id, before),
            "xg": home_xg,
        },
        "away": {
            "last5": _window_form(away_recent, 5),
            "last10": _window_form(away_recent, 10),
            "last15": _window_form(away_recent, 15),
            "splits": _split_to_dict(features.split_home_away(away_recent)),
            "schedule": features.rest_and_congestion(db, feat_away_id, before),
            "xg": away_xg,
        },
    }

    has_halftime = bool(markets_dict["half_time"]["available"])
    stored_odds = list(
        db.execute(select(MatchOdd).where(MatchOdd.match_id == match.id)).scalars()
    )
    value = _value_analysis(markets_dict, stored_odds, opts.manual_odds)

    findings = derive_findings(
        home_name=home.name,
        away_name=away.name,
        lambda_home=lambda_home,
        lambda_away=lambda_away,
        home_advantage=model.params.home_advantage,
        one_x_two=market_probs.one_x_two,
        totals=market_probs.totals,
        btts=market_probs.btts,
        home_form=_form_to_dict(features.form_summary(home_recent[:10])),
        away_form=_form_to_dict(features.form_summary(away_recent[:10])),
        ratings=ratings_dict,
        xg={"home": home_xg, "away": away_xg} if xg_used else None,
    )

    opportunities = derive_opportunities(
        home_name=home.name,
        away_name=away.name,
        one_x_two=market_probs.one_x_two,
        double_chance=market_probs.double_chance,
        totals=market_probs.totals,
        btts=market_probs.btts,
        half_time=markets_dict["half_time"],
    )

    coverage_counters = {
        "home_form": len(home_recent),
        "away_form": len(away_recent),
        "h2h": len(h2h),
        "has_xg": int(home_xg["n_with_xg"] > 0 and away_xg["n_with_xg"] > 0),
        "has_halftime": int(has_halftime),
        "no_history_teams": len(missing_teams),
    }

    has_secondary_stats = _has_secondary_stats(db, training_league_ids)
    limitations = derive_limitations(
        n_training_matches=len(records),
        min_matches=settings.min_matches_for_model,
        counters=coverage_counters,
        has_odds=value["available"],
        has_secondary_stats=has_secondary_stats,
    )
    if scope["note"]:
        limitations.insert(0, scope["note"])

    top_score = market_probs.correct_scores[0]
    confidence = derive_confidence(
        n_training_matches=len(records),
        min_matches=settings.min_matches_for_model,
        monte_carlo_std_error=simulation.mc_std_error,
        converged=bool(model.params.converged),
        counters=coverage_counters,
        has_odds=value["available"],
        one_x_two=market_probs.one_x_two,
        has_secondary_stats=has_secondary_stats,
    )

    provenance = _match_provenance(db, match, home, away, league)

    model_run_id = None
    if opts.persist:
        model_run = ModelRun(
            model_name=MODEL_NAME,
            model_version=MODEL_VERSION,
            league_id=match.league_id,
            season_id=match.season_id,
            params={
                **model.params.to_dict(),
                "analysis_options": {
                    "monte_carlo_simulations": sims,
                    "xg_weight": xg_weight,
                    "kmax": kmax,
                    "seed": opts.seed,
                    "regularization": regularization,
                    "use_rest": opts.use_rest,
                    "use_form": use_form,
                    "form_window": form_window,
                },
            },
            training_start=records[0].date,
            training_end=records[-1].date,
            n_matches=len(records),
        )
        db.add(model_run)
        db.flush()
        model_run_id = model_run.id
        for market, selection, probability in _prediction_rows(market_probs):
            db.add(
                Prediction(
                    model_run_id=model_run_id,
                    match_id=match.id,
                    market=market,
                    selection=selection,
                    probability=probability,
                )
            )
        db.commit()

    return {
        "match": {
            "id": match.id,
            "league": {"id": league.id, "name": league.name, "slug": league.slug,
                       "country": league.country} if league else None,
            "season": {"id": season.id, "label": season.label} if season else None,
            "home_team": {"id": home.id, "name": home.name, "slug": home.slug},
            "away_team": {"id": away.id, "name": away.name, "slug": away.slug},
            "kickoff_utc": iso_utc(match.kickoff_utc),
            "status": match.status,
            "venue": match.venue,
            "round": match.round_label,
            "data_retrieved_at": datetime.now(tz=UTC).isoformat(),
        },
        "provenance": provenance,
        "scope": scope,
        "model": {
            "name": MODEL_NAME,
            "version": MODEL_VERSION,
            "params": model.params.to_dict(),
            "n_training_matches": len(records),
            "training_start": records[0].date.isoformat(),
            "training_end": records[-1].date.isoformat(),
            "half_life_days": model.params.halflife_days,
            "converged": model.params.converged,
            "regularization": model.params.regularization,
            "rest_coeff": model.params.rest_coeff,
            "xg_weight": xg_weight,
            "xg_used": xg_used,
        },
        "expected_goals": {
            "home": lambda_home,
            "away": lambda_away,
            "total": lambda_home + lambda_away,
        },
        "predicted_score": {"score": top_score["score"],
                            "probability": top_score["probability"]},
        "markets": markets_dict,
        "simulation": {
            "n_simulations": simulation.n_simulations,
            "seed": simulation.seed,
            "one_x_two": simulation.one_x_two,
            "totals": simulation.totals,
            "btts": simulation.btts,
            "mean_home_goals": simulation.mean_home_goals,
            "mean_away_goals": simulation.mean_away_goals,
            "mc_std_error": simulation.mc_std_error,
        },
        "form": form,
        "head_to_head": [
            {
                "date": m.date.isoformat(),
                "is_home": m.is_home,
                "opponent": m.opponent_name,
                "goals_for": m.goals_for,
                "goals_against": m.goals_against,
                "result": m.result,
            }
            for m in h2h
        ],
        "ratings": ratings_dict,
        "findings": findings,
        "opportunities": opportunities,
        "limitations": limitations,
        "value": value,
        "confidence": confidence,
        "model_run_id": model_run_id,
    }


def _match_provenance(db: Session, match: Match, home: Team, away: Team, league: League | None) -> dict[str, Any]:
    from app.db.models import ProviderRecord

    rows = db.execute(
        select(ProviderRecord).where(
            ProviderRecord.entity_type == "match", ProviderRecord.entity_id == match.id
        )
    ).scalars().all()
    return {
        "match_sources": [
            {
                "provider": r.provider,
                "source_url": r.source_url,
                "competition": r.competition or (league.name if league else None),
                "season": r.season,
                "retrieved_at": r.retrieved_at.isoformat() if r.retrieved_at else None,
                "data_as_of": r.data_as_of.isoformat() if r.data_as_of else None,
                "is_estimate": r.is_estimate,
            }
            for r in rows
        ],
        "home_team": {"name": home.name, "provider_refs": home.provider_refs},
        "away_team": {"name": away.name, "provider_refs": away.provider_refs},
    }


def _prediction_rows(market_probs) -> list[tuple[str, str, float]]:
    rows: list[tuple[str, str, float]] = []
    for selection, probability in market_probs.one_x_two.items():
        rows.append(("1x2", selection, probability))
    for line, entry in market_probs.totals.items():
        rows.append((f"o/u_{line}", "over", entry["over"]))
        rows.append((f"o/u_{line}", "under", entry["under"]))
    rows.append(("btts", "yes", market_probs.btts["yes"]))
    rows.append(("btts", "no", market_probs.btts["no"]))
    return rows
