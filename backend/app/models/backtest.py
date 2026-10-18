"""Walk-forward backtesting with strict chronological separation.

At every evaluation step the model is trained only on matches that kicked off
before the match being predicted, so there is no look-ahead leakage.  We report
Brier score, log loss and accuracy against a naive base-rate baseline, plus a
reliability diagram for calibration.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.models.calibration import (
    ReliabilityBin,
    binary_brier,
    binary_log_loss,
    multiclass_brier,
    multiclass_log_loss,
    reliability_diagram,
)
from app.models.dixon_coles import (
    MODEL_VERSION,
    DixonColesModel,
    MatchRecord,
    with_form,
    with_rest,
)
from app.models.markets import btts as btts_market
from app.models.markets import one_x_two, totals
from app.models.poisson import outcome_from_score

OUTCOME_INDEX = {"home": 0, "draw": 1, "away": 2}


@dataclass
class BacktestConfig:
    min_train: int = 40
    retrain_every: int = 5
    halflife_days: float = 365.0
    kmax: int = 10
    totals_line: float = 2.5
    regularization: float = 1.0
    use_rest: bool = False
    use_form: bool = False
    form_window: int = 5

    def to_dict(self) -> dict:
        return {
            "min_train": self.min_train,
            "retrain_every": self.retrain_every,
            "halflife_days": self.halflife_days,
            "kmax": self.kmax,
            "totals_line": self.totals_line,
            "regularization": self.regularization,
            "use_rest": self.use_rest,
            "use_form": self.use_form,
            "form_window": self.form_window,
        }


@dataclass
class MarketMetrics:
    market: str
    n: int
    brier: float
    log_loss: float
    accuracy: float | None
    baseline_brier: float
    baseline_log_loss: float
    baseline_accuracy: float | None
    improvement_brier: float
    reliability: list[ReliabilityBin] = field(default_factory=list)


@dataclass
class BacktestReport:
    n_train: int
    n_eval: int
    model_name: str
    model_version: str
    config: dict
    markets: list[MarketMetrics]
    warnings: list[str] = field(default_factory=list)


def _fit(
    records: list[MatchRecord],
    halflife_days: float,
    regularization: float = 1.0,
    use_rest: bool = False,
    use_form: bool = False,
    form_window: int = 5,
) -> DixonColesModel:
    return DixonColesModel.fit(
        records,
        halflife_days=halflife_days,
        regularization=regularization,
        use_rest=use_rest,
        use_form=use_form,
        form_window=form_window,
    )


def walk_forward(records: list[MatchRecord], config: BacktestConfig | None = None) -> BacktestReport:
    cfg = config or BacktestConfig()
    ordered = sorted(records, key=lambda r: r.date)
    if cfg.use_rest:
        ordered = with_rest(ordered)
    if cfg.use_form:
        ordered = with_form(ordered, cfg.form_window)
    warnings: list[str] = []

    if len(ordered) <= cfg.min_train:
        return BacktestReport(
            n_train=len(ordered),
            n_eval=0,
            model_name="dixon_coles",
            model_version=MODEL_VERSION,
            config=cfg.to_dict(),
            markets=[],
            warnings=[f"not enough matches ({len(ordered)}) to backtest; need > {cfg.min_train}"],
        )

    x1x2_probs: list[list[float]] = []
    x1x2_true: list[int] = []
    ou_probs: list[float] = []
    ou_true: list[int] = []
    btts_probs: list[float] = []
    btts_true: list[int] = []
    # Baseline accumulators refreshed whenever we retrain.
    base_1x2 = np.array([1 / 3, 1 / 3, 1 / 3])
    base_over = 0.5
    base_btts = 0.5

    model: DixonColesModel | None = None
    n_eval = 0
    for i in range(cfg.min_train, len(ordered)):
        train = ordered[:i]
        test = ordered[i]
        if model is None or (i - cfg.min_train) % cfg.retrain_every == 0:
            if not {test.home, test.away} <= {r.home for r in train} | {r.away for r in train}:
                continue
            try:
                model = _fit(
                    train,
                    cfg.halflife_days,
                    cfg.regularization,
                    cfg.use_rest,
                    cfg.use_form,
                    cfg.form_window,
                )
            except ValueError:
                continue
            counts = np.array(
                [
                    sum(1 for r in train if r.home_goals > r.away_goals),
                    sum(1 for r in train if r.home_goals == r.away_goals),
                    sum(1 for r in train if r.home_goals < r.away_goals),
                ],
                dtype=float,
            )
            base_1x2 = counts / max(counts.sum(), 1)
            over_count = sum(1 for r in train if r.home_goals + r.away_goals > cfg.totals_line)
            base_over = over_count / max(len(train), 1)
            btts_count = sum(1 for r in train if r.home_goals >= 1 and r.away_goals >= 1)
            base_btts = btts_count / max(len(train), 1)

        if model is None or not (model.has_team(test.home) and model.has_team(test.away)):
            continue

        rest_diff = None
        if (
            cfg.use_rest
            and test.rest_home is not None
            and test.rest_away is not None
        ):
            rest_diff = test.rest_home - test.rest_away
        form_diff = None
        if cfg.use_form:
            mean = model.params.form_mean or 0.0
            fh = test.form_home if test.form_home is not None else mean
            fa = test.form_away if test.form_away is not None else mean
            form_diff = fh - fa
        matrix = model.score_matrix(
            test.home, test.away, kmax=cfg.kmax, rest_diff=rest_diff, form_diff=form_diff
        )
        probs_1x2 = one_x_two(matrix)
        x1x2_probs.append([probs_1x2["home"], probs_1x2["draw"], probs_1x2["away"]])
        x1x2_true.append(OUTCOME_INDEX[outcome_from_score(test.home_goals, test.away_goals)])

        line_key = f"{cfg.totals_line}"
        over = totals(matrix, lines=(cfg.totals_line,))[line_key]["over"]
        ou_probs.append(over)
        ou_true.append(1 if test.home_goals + test.away_goals > cfg.totals_line else 0)

        btts_p = btts_market(matrix)["yes"]
        btts_probs.append(btts_p)
        btts_true.append(1 if test.home_goals >= 1 and test.away_goals >= 1 else 0)
        n_eval += 1

    markets: list[MarketMetrics] = []
    if n_eval > 0:
        markets.append(_eval_1x2(x1x2_probs, x1x2_true, base_1x2))
        markets.append(_eval_binary("over_under", ou_probs, ou_true, base_over))
        markets.append(_eval_binary("btts", btts_probs, btts_true, base_btts))
    else:
        warnings.append("no evaluable matches in the backtest window")

    if n_eval < 50:
        warnings.append(
            f"small evaluation sample ({n_eval} matches): treat scores as indicative only"
        )

    return BacktestReport(
        n_train=cfg.min_train,
        n_eval=n_eval,
        model_name="dixon_coles",
        model_version=MODEL_VERSION,
        config=cfg.to_dict(),
        markets=markets,
        warnings=warnings,
    )


def _eval_1x2(probs: list[list[float]], outcomes: list[int], baseline: np.ndarray) -> MarketMetrics:
    p = np.array(probs, dtype=float)
    y = np.array(outcomes, dtype=int)
    n = len(y)
    brier = multiclass_brier(p, y, 3)
    logloss = multiclass_log_loss(p, y)
    acc = float(np.mean(np.argmax(p, axis=1) == y))
    base = np.tile(baseline, (n, 1))
    base_brier = multiclass_brier(base, y, 3)
    base_logloss = multiclass_log_loss(base, y)
    base_acc = float(np.mean(np.argmax(base, axis=1) == y))
    # Calibration over all three outcomes pooled.
    flat_probs = p.reshape(-1)
    flat_true = np.zeros((n, 3))
    flat_true[np.arange(n), y] = 1.0
    reliability = reliability_diagram(flat_probs, flat_true.reshape(-1), n_bins=10)
    return MarketMetrics(
        market="1x2",
        n=n,
        brier=brier,
        log_loss=logloss,
        accuracy=acc,
        baseline_brier=base_brier,
        baseline_log_loss=base_logloss,
        baseline_accuracy=base_acc,
        improvement_brier=base_brier - brier,
        reliability=reliability,
    )


def _eval_binary(market: str, probs: list[float], outcomes: list[int], baseline: float) -> MarketMetrics:
    p = np.array(probs, dtype=float)
    y = np.array(outcomes, dtype=int)
    base = np.full_like(p, baseline)
    brier = binary_brier(p, y)
    logloss = binary_log_loss(p, y)
    base_brier = binary_brier(base, y)
    base_logloss = binary_log_loss(base, y)
    acc = float(np.mean((p >= 0.5).astype(int) == y))
    base_acc = float(np.mean((base >= 0.5).astype(int) == y))
    return MarketMetrics(
        market=market,
        n=len(y),
        brier=brier,
        log_loss=logloss,
        accuracy=acc,
        baseline_brier=base_brier,
        baseline_log_loss=base_logloss,
        baseline_accuracy=base_acc,
        improvement_brier=base_brier - brier,
        reliability=reliability_diagram(p, y, n_bins=10),
    )
