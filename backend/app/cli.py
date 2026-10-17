"""Command-line interface.

    python -m app.cli initdb
    python -m app.cli sources
    python -m app.cli ingest football_data_co_uk
    python -m app.cli ingest sample
    python -m app.cli ingest api_football --date 2026-10-16 --leagues "Premier League|England,La Liga|Spain"
    python -m app.cli refresh-upcoming
    python -m app.cli enrich-stats api_football --limit 80
    python -m app.cli backtest --league-id 1 --min-train 60
    python -m app.cli tune --league-id 1 --halflives 90,180,365 --regularizations 0,0.1,1
    python -m app.cli dedupe --apply
    python -m app.cli fix-kickoffs --apply
    python -m app.cli predict --days 7 --loop 3600
    python -m app.cli seed-sample --teams 12 --weeks 90
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC

from app.db.base import SessionLocal, init_db
from app.ingest.service import ingest_provider, seed_data_sources


def cmd_initdb(_args: argparse.Namespace) -> int:
    init_db()
    with SessionLocal() as db:
        seed_data_sources(db)
    print("database initialised and data sources registered")
    return 0


def cmd_sources(_args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.db.models import DataSource

    with SessionLocal() as db:
        seed_data_sources(db)
        for row in db.execute(select(DataSource)).scalars():
            print(f"{row.name:22} kind={row.kind:6} enabled={row.enabled!s:5} status={row.status:12} {row.message}")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    init_db()
    kwargs: dict[str, object] = {}
    if args.date:
        kwargs["date"] = args.date
    if args.league_id is not None:
        kwargs["league_id"] = args.league_id
    if args.season is not None:
        kwargs["season"] = args.season
    leagues = [s.strip() for s in (args.leagues.split(",") if args.leagues else []) if s.strip()]
    with SessionLocal() as db:
        result = ingest_provider(
            db,
            args.provider,
            include_details=not args.no_details,
            leagues=leagues or None,
            **kwargs,
        )
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in ("success", "unavailable") else 1


#: The five big European leagues, as API-Football name|country specs.  The Free
#: plan only reaches a ±1-day date window, so this is what a daily refresh pulls.
BIG_FIVE = [
    "Premier League|England",
    "La Liga|Spain",
    "Serie A|Italy",
    "Bundesliga|Germany",
    "Ligue 1|France",
]


def cmd_refresh_upcoming(args: argparse.Namespace) -> int:
    """Import the next day's big-five fixtures (and odds) then run the bot.

    One command for a daily refresh.  Uses a single fixtures request per day plus
    one stats and one odds request per kept fixture, so it fits an API-Football
    Free key's daily allowance when only a handful of fixtures are in range.
    """
    from datetime import datetime

    init_db()
    date = args.date or datetime.now(tz=UTC).strftime("%Y-%m-%d")
    leagues = [
        s.strip() for s in (args.leagues.split(",") if args.leagues else BIG_FIVE) if s.strip()
    ]
    with SessionLocal() as db:
        result = ingest_provider(
            db,
            "api_football",
            date=date,
            leagues=leagues,
            include_details=not args.no_details,
        )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"refresh {date}: status={result['status']} records_in={result['records_in']} "
            f"upserted={result['matches_upserted']}"
        )
        for w in result["warnings"]:
            print(f"  warning: {w}")
    if result["status"] not in ("success", "unavailable"):
        return 1
    if args.no_predict:
        return 0
    return cmd_predict(argparse.Namespace(
        league_id=None, days=args.days, limit=args.limit,
        no_live=False, no_persist=True, json=args.json, loop=0,
    ))


def cmd_enrich_stats(args: argparse.Namespace) -> int:
    """Backfill per-match statistics (e.g. xG) for matches already stored."""
    from app.ingest.service import enrich_stats

    init_db()
    with SessionLocal() as db:
        result = enrich_stats(
            db,
            args.provider,
            league_id=args.league_id,
            season_label=args.season_label,
            limit=args.limit,
            force=args.force,
        )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"enrich {args.provider}: status={result['status']} "
            f"enriched={result['enriched']} skipped={result['skipped']} "
            f"errors={result['errors']} (of {result['candidates']} candidates)"
        )
        for w in result["warnings"]:
            print(f"  warning: {w}")
    return 0 if result["status"] == "success" else 1


def cmd_dedupe(args: argparse.Namespace) -> int:
    """Merge duplicate clubs (and their fixtures) that arrived from two providers.

    Two providers spell a club differently (``"FC Barcelona"`` vs
    ``"Barcelona"``), so each becomes its own row and one club's history is split
    in two.  This merges them, scoped to one competition, and folds the duplicate
    2024/25 fixtures into single rows.  Dry-run by default; ``--apply`` writes,
    copying the SQLite database first.
    """
    import shutil
    from datetime import datetime

    from app.config import get_settings
    from app.ingest import maintenance

    init_db()
    if args.apply:
        settings = get_settings()
        if settings.is_sqlite:
            path = settings.database_url.split("sqlite:///", 1)[-1]
            stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
            backup = f"{path}.backup-{stamp}"
            shutil.copy2(path, backup)
            print(f"database backed up to {backup}")

    with SessionLocal() as db:
        try:
            report = maintenance.run_dedupe(db, apply=args.apply)
            if args.apply:
                db.commit()
            else:
                db.rollback()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            print(f"dedupe failed, no changes written: {exc}")
            return 1

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0

    mode = "applied" if report.applied else "dry-run"
    print(
        f"dedupe ({mode}): {len(report.team_merges)} club clusters, "
        f"{len(report.match_merges)} fixture clusters"
    )
    if report.applied:
        print(
            f"  removed {report.teams_removed} clubs, {report.matches_removed} fixtures; "
            f"moved {report.stats_moved} stats, {report.odds_moved} odds"
        )
    for merge in report.team_merges[:20]:
        print(
            f"  club keep {merge.keeper_name!r} <- "
            f"{', '.join(repr(n) for n in merge.duplicate_names)} "
            f"({merge.matches_moved} matches)"
        )
    if len(report.team_merges) > 20:
        print(f"  ... and {len(report.team_merges) - 20} more club clusters")
    for merge in report.match_merges[:5]:
        print(
            f"  fixture {merge.kickoff_date} {merge.home} v {merge.away}: "
            f"drop {merge.duplicate_ids}"
        )
    if len(report.match_merges) > 5:
        print(f"  ... and {len(report.match_merges) - 5} more fixture clusters")
    if not report.applied:
        print("re-run with --apply to write these merges")
    return 0


def cmd_remove_league(args: argparse.Namespace) -> int:
    """Delete one or more competitions and every row that belongs to them.

    Use this to drop a competition we decided not to support: its fixtures,
    stats, odds, provenance, seasons, model runs and now-orphaned clubs all go.
    A club that still appears in another competition is kept.  Dry-run by
    default; ``--apply`` writes, copying the SQLite database first.
    """
    import shutil
    from datetime import datetime

    from app.config import get_settings
    from app.ingest import maintenance

    init_db()
    if args.apply:
        settings = get_settings()
        if settings.is_sqlite:
            path = settings.database_url.split("sqlite:///", 1)[-1]
            stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
            backup = f"{path}.backup-{stamp}"
            shutil.copy2(path, backup)
            print(f"database backed up to {backup}")

    with SessionLocal() as db:
        try:
            report = maintenance.remove_league(db, args.league_id, apply=args.apply)
            if args.apply:
                db.commit()
            else:
                db.rollback()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            print(f"remove-league failed, no changes written: {exc}")
            return 1

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0

    mode = "applied" if report.applied else "dry-run"
    if not report.leagues:
        print(f"remove-league ({mode}): no matching leagues")
        return 0
    print(f"remove-league ({mode}):")
    for lg in report.leagues:
        print(f"  {lg['id']}: {lg['name']} ({lg['slug']})")
    print(
        f"  matches={report.matches_removed} seasons={report.seasons_removed} "
        f"clubs={report.teams_removed} stats={report.stats_removed} odds={report.odds_removed} "
        f"predictions={report.predictions_removed} model_runs={report.model_runs_removed} "
        f"backtests={report.backtests_removed} provider_records={report.provider_records_removed}"
    )
    if not report.applied:
        print("re-run with --apply to write these deletions")
    return 0


def cmd_fix_kickoffs(args: argparse.Namespace) -> int:
    """Reinterpret stored venue-local kick-off times as true UTC.

    openfootball publishes each fixture's local wall-clock time; rows ingested
    before that was converted stored the local time as if it were UTC.  This
    pass rewrites those rows so every stored kick-off is true UTC.  Dry-run by
    default; ``--apply`` writes, copying the SQLite database first.
    """
    import shutil
    from datetime import datetime

    from app.config import get_settings
    from app.ingest import maintenance

    init_db()
    if args.apply:
        settings = get_settings()
        if settings.is_sqlite:
            path = settings.database_url.split("sqlite:///", 1)[-1]
            stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
            backup = f"{path}.backup-{stamp}"
            shutil.copy2(path, backup)
            print(f"database backed up to {backup}")

    with SessionLocal() as db:
        try:
            report = maintenance.fix_kickoff_utc(db, apply=args.apply)
            if args.apply:
                db.commit()
            else:
                db.rollback()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            print(f"fix-kickoffs failed, no changes written: {exc}")
            return 1

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0

    mode = "applied" if report.applied else "dry-run"
    print(f"fix-kickoffs ({mode}): candidates={report.candidates} updated={report.updated} "
          f"unchanged={report.unchanged} skipped_unknown_venue={report.skipped_unknown_venue}")
    for provider, n in sorted(report.by_provider.items()):
        print(f"  {provider}: {n} row(s) shifted")
    if report.samples:
        print("  samples (before -> after):")
        for s in report.samples:
            print(f"    #{s['id']} {s['provider']} [{s['competition_country']} / {s['timezone']}]: "
                  f"{s['before']} -> {s['after']}")
    if not report.applied and report.updated:
        print("re-run with --apply to write these changes")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    from datetime import datetime

    from app.analysis import engine
    from app.db.models import BacktestResult, ModelRun
    from app.models import backtest as bt
    from app.models.dixon_coles import MODEL_NAME, MODEL_VERSION

    init_db()
    with SessionLocal() as db:
        records = engine.training_records(db, args.league_id, datetime.now(tz=UTC), 3650)
        if len(records) <= args.min_train:
            print(f"not enough matches ({len(records)}); need > {args.min_train}")
            return 1
        config = bt.BacktestConfig(
            min_train=args.min_train,
            retrain_every=args.retrain_every,
            halflife_days=args.halflife,
            regularization=args.regularization,
            use_rest=args.use_rest,
        )
        report = bt.walk_forward(records, config)
        run = ModelRun(
            model_name=MODEL_NAME, model_version=MODEL_VERSION, league_id=args.league_id,
            params={"backtest": config.to_dict()}, n_matches=len(records),
        )
        db.add(run)
        db.flush()
        for m in report.markets:
            db.add(BacktestResult(
                model_run_id=run.id, league_id=args.league_id, market=m.market,
                n_train=report.n_train, n_eval=m.n, brier_score=m.brier, log_loss=m.log_loss,
                accuracy=m.accuracy, baseline_brier=m.baseline_brier,
                baseline_log_loss=m.baseline_log_loss,
                details={"improvement_brier": m.improvement_brier},
            ))
        db.commit()
        print(f"backtest complete: n_eval={report.n_eval}")
        for m in report.markets:
            print(f"  {m.market:12} brier={m.brier:.4f} baseline={m.baseline_brier:.4f} "
                  f"logloss={m.log_loss:.4f} acc={m.accuracy}")
        for w in report.warnings:
            print(f"  warning: {w}")
    return 0


def cmd_tune(args: argparse.Namespace) -> int:
    """Sweep model hyper-parameters and rank them by a holding-out score.

    A walk-forward backtest is run once per combination of ``--halflives``,
    ``--kmaxs`` and ``--regularizations`` against the same chronological window, so
    the scores are directly comparable.  Ranking is by Brier (or log-loss) on the
    chosen market: lower is better.  Without odds this only tells you which
    settings fit the past best; it is a starting point, not proof of future
    accuracy.
    """
    from datetime import datetime

    from app.analysis import engine
    from app.models import backtest as bt

    init_db()
    halflives = [float(x) for x in args.halflives.split(",") if x.strip()]
    kmaxs = [int(x) for x in args.kmaxs.split(",") if x.strip()]
    regs = [float(x) for x in args.regularizations.split(",") if x.strip()]
    if not halflives:
        print("no halflives to try")
        return 1
    if not regs:
        regs = [0.0]
    with SessionLocal() as db:
        records = engine.training_records(db, args.league_id, datetime.now(tz=UTC), 3650)
    if len(records) <= args.min_train:
        print(f"not enough matches ({len(records)}); need > {args.min_train}")
        return 1

    rows: list[dict[str, object]] = []
    for regular in regs:
        for halflife in halflives:
            for kmax in kmaxs:
                report = bt.walk_forward(
                    records,
                    bt.BacktestConfig(
                        min_train=args.min_train,
                        retrain_every=args.retrain_every,
                        halflife_days=halflife,
                        kmax=kmax,
                        regularization=regular,
                        use_rest=args.use_rest,
                    ),
                )
                metric = next((m for m in report.markets if m.market == args.market), None)
                if metric is None:
                    continue
                rows.append(
                    {
                        "halflife": halflife,
                        "kmax": kmax,
                        "regularization": regular,
                        "n_eval": metric.n,
                        "brier": metric.brier,
                        "log_loss": metric.log_loss,
                        "accuracy": metric.accuracy,
                        "baseline_brier": metric.baseline_brier,
                        "improvement_brier": metric.improvement_brier,
                    }
                )

    if not rows:
        print(f"no results for market {args.market!r}")
        return 1
    key = args.metric
    rows.sort(key=lambda r: r[key])  # type: ignore[arg-type,return-value]

    print(f"tune league_id={args.league_id} market={args.market} metric={key} "
          f"(lower is better, {len(records)} matches)")
    print(f"  {'halflife':>9} {'kmax':>5} {'reg':>6} {'n_eval':>7} {'brier':>8} {'log_loss':>9} "
          f"{'acc':>6} {'vs base':>8}")
    for r in rows:
        print(
            f"  {r['halflife']:>9.1f} {r['kmax']:>5d} {r['regularization']:>6.2f} "
            f"{r['n_eval']:>7d} "
            f"{r['brier']:>8.4f} {r['log_loss']:>9.4f} {r['accuracy']:>6.3f} "
            f"{r['improvement_brier']:>+8.4f}"
        )
    best = rows[0]
    print(f"best: halflife={best['halflife']} kmax={best['kmax']} "
          f"regularization={best['regularization']} {key}={best[key]:.4f}")
    return 0


def cmd_seed_sample(args: argparse.Namespace) -> int:

    init_db()
    print("WARNING: seeding SYNTHETIC sample data for demo/testing. NOT real results.")
    with SessionLocal() as db:
        result = ingest_provider(db, "sample")
    print(json.dumps(result, indent=2))
    return 0


def cmd_predict(args: argparse.Namespace) -> int:
    import time

    from app.analysis import bot

    init_db()

    def run_once() -> int:
        with SessionLocal() as db:
            result = bot.predict_upcoming(
                db,
                league_id=args.league_id,
                days_ahead=args.days,
                limit=args.limit,
                include_live=not args.no_live,
                persist=not args.no_persist,
            )
        if args.json:
            print(json.dumps(result, indent=2))
            return 0
        print(
            f"[{result['generated_at']}] {result['predicted']} predictions from "
            f"{result['considered']} upcoming matches ({result['window_days']}d window)"
        )
        for p in result["predictions"]:
            ox2 = p["probabilities"]["one_x_two"]
            value = p["best_value"]
            ev = (
                f"  bestEV {value['market']}:{value['selection']} @ {value['decimal_odds']:.2f} "
                f"({value['expected_value']:+.1%})"
                if value
                else ""
            )
            print(
                f"  {p['kickoff_utc'][:16]}  {p['home_team']} v {p['away_team']}  "
                f"H {ox2['home']:.0%} / D {ox2['draw']:.0%} / A {ox2['away']:.0%}"
                f"  score {p['predicted_score']['score']}{ev}"
            )
        for s in result["skipped"]:
            print(f"  skipped {s['home_team']} v {s['away_team']}: {s['reason']}")
        return 0

    if not args.loop:
        return run_once()
    print(f"prediction bot running every {args.loop}s (Ctrl+C to stop)")
    try:
        while True:
            run_once()
            time.sleep(args.loop)
    except KeyboardInterrupt:
        print("stopped")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="Sky CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("initdb", help="create tables and register data sources").set_defaults(func=cmd_initdb)
    sub.add_parser("sources", help="list configured data sources").set_defaults(func=cmd_sources)

    p_ingest = sub.add_parser("ingest", help="run a provider import")
    p_ingest.add_argument("provider")
    p_ingest.add_argument("--date", help="a single UTC date YYYY-MM-DD (live provider fixtures)")
    p_ingest.add_argument("--league-id", type=int, default=None, help="provider league id (with --season)")
    p_ingest.add_argument("--season", type=int, default=None, help="season start year (with --league-id)")
    p_ingest.add_argument(
        "--leagues",
        help="comma-separated 'Name|Country' specs to keep, e.g. \"Premier League|England,La Liga|Spain\"",
    )
    p_ingest.add_argument(
        "--no-details", action="store_true",
        help="fixtures only (skip per-match stats/odds; saves API quota)",
    )
    p_ingest.set_defaults(func=cmd_ingest)

    p_refresh = sub.add_parser(
        "refresh-upcoming",
        help="import today's big-five fixtures + odds, then run the prediction bot",
    )
    p_refresh.add_argument("--date", help="UTC date YYYY-MM-DD (default: today)")
    p_refresh.add_argument("--leagues", help="override the default big-five league specs")
    p_refresh.add_argument("--no-details", action="store_true", help="fixtures only (skip odds)")
    p_refresh.add_argument("--no-predict", action="store_true", help="import only, do not run the bot")
    p_refresh.add_argument("--days", type=int, default=3, help="bot window after import")
    p_refresh.add_argument("--limit", type=int, default=50, help="max fixtures to predict")
    p_refresh.add_argument("--json", action="store_true", help="print raw JSON")
    p_refresh.set_defaults(func=cmd_refresh_upcoming)

    p_enrich = sub.add_parser(
        "enrich-stats",
        help="backfill per-match statistics (e.g. xG) for matches already stored",
    )
    p_enrich.add_argument("provider", help="provider whose stored fixtures to enrich, e.g. api_football")
    p_enrich.add_argument("--league-id", type=int, default=None, help="restrict to one local league id")
    p_enrich.add_argument("--season-label", default=None, help="restrict to one season label, e.g. 2024")
    p_enrich.add_argument("--limit", type=int, default=90, help="max matches to enrich this run (quota budget)")
    p_enrich.add_argument("--force", action="store_true", help="re-fetch even if statistics already exist")
    p_enrich.add_argument("--json", action="store_true", help="print raw JSON")
    p_enrich.set_defaults(func=cmd_enrich_stats)

    p_bt = sub.add_parser("backtest", help="run a walk-forward backtest")
    p_bt.add_argument("--league-id", type=int, required=True)
    p_bt.add_argument("--min-train", type=int, default=60)
    p_bt.add_argument("--retrain-every", type=int, default=10)
    p_bt.add_argument("--halflife", type=float, default=180.0, help="time-decay half-life in days")
    p_bt.add_argument(
        "--regularization", type=float, default=1.0,
        help="L2 penalty on attack/defence strengths (shrinks thin-sample teams)",
    )
    p_bt.add_argument(
        "--use-rest", action="store_true",
        help="fit and apply a rest-difference (time off) term",
    )
    p_bt.set_defaults(func=cmd_backtest)

    p_tune = sub.add_parser(
        "tune", help="sweep hyper-parameters and rank them by Brier/log-loss"
    )
    p_tune.add_argument("--league-id", type=int, required=True)
    p_tune.add_argument(
        "--halflives", default="90,120,180,270,365",
        help="comma-separated half-lives in days",
    )
    p_tune.add_argument("--kmaxs", default="12", help="comma-separated max-goals grid sizes")
    p_tune.add_argument(
        "--regularizations", default="0,0.1,1,3",
        help="comma-separated L2 penalties on team strengths",
    )
    p_tune.add_argument("--min-train", type=int, default=200)
    p_tune.add_argument("--retrain-every", type=int, default=25)
    p_tune.add_argument(
        "--market", default="1x2", choices=["1x2", "over_under", "btts"]
    )
    p_tune.add_argument("--metric", default="brier", choices=["brier", "log_loss"])
    p_tune.add_argument(
        "--use-rest", action="store_true",
        help="fit and apply a rest-difference (time off) term",
    )
    p_tune.set_defaults(func=cmd_tune)

    p_dedupe = sub.add_parser(
        "dedupe",
        help="merge duplicate clubs and fixtures from different providers (dry-run)",
    )
    p_dedupe.add_argument(
        "--apply", action="store_true", help="write the merges (backs up the database first)"
    )
    p_dedupe.add_argument("--json", action="store_true", help="print the raw JSON report")
    p_dedupe.set_defaults(func=cmd_dedupe)

    p_rm = sub.add_parser(
        "remove-league",
        help="delete a competition and all its data (dry-run)",
    )
    p_rm.add_argument(
        "--league-id", type=int, action="append", required=True,
        help="local league id to remove (repeat for several)",
    )
    p_rm.add_argument(
        "--apply", action="store_true", help="write the deletions (backs up the database first)"
    )
    p_rm.add_argument("--json", action="store_true", help="print the raw JSON report")
    p_rm.set_defaults(func=cmd_remove_league)

    p_fix = sub.add_parser(
        "fix-kickoffs",
        help="reinterpret venue-local kick-off times as UTC (dry-run)",
    )
    p_fix.add_argument(
        "--apply", action="store_true", help="write the changes (backs up the database first)"
    )
    p_fix.add_argument("--json", action="store_true", help="print the raw JSON report")
    p_fix.set_defaults(func=cmd_fix_kickoffs)

    p_predict = sub.add_parser("predict", help="predict upcoming fixtures (prediction bot)")
    p_predict.add_argument("--league-id", type=int, default=None)
    p_predict.add_argument("--days", type=int, default=14, help="forward window in days")
    p_predict.add_argument("--limit", type=int, default=50, help="max fixtures to predict")
    p_predict.add_argument("--no-live", action="store_true", help="only scheduled, not in-play")
    p_predict.add_argument("--no-persist", action="store_true", help="do not store model runs")
    p_predict.add_argument("--json", action="store_true", help="print the raw JSON result")
    p_predict.add_argument(
        "--loop", type=int, default=0, metavar="SECONDS",
        help="run continuously (bot mode), refreshing every SECONDS",
    )
    p_predict.set_defaults(func=cmd_predict)

    p_seed = sub.add_parser("seed-sample", help="seed SYNTHETIC demo data (not real)")
    p_seed.set_defaults(func=cmd_seed_sample)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
