"""Scoring rules and calibration analysis for probabilistic forecasts."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EPS = 1e-15


def _clip(probs: np.ndarray) -> np.ndarray:
    return np.clip(probs, EPS, 1.0 - EPS)


def multiclass_brier(probs: np.ndarray, outcomes: np.ndarray, n_classes: int) -> float:
    """Mean Brier score for a categorical forecast (lower is better)."""
    probs = np.asarray(probs, dtype=float)
    outcomes = np.asarray(outcomes, dtype=int)
    one_hot = np.zeros((len(outcomes), n_classes))
    one_hot[np.arange(len(outcomes)), outcomes] = 1.0
    return float(np.mean(np.sum((probs - one_hot) ** 2, axis=1)))


def multiclass_log_loss(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Mean negative log-likelihood of the realised outcome."""
    probs = _clip(np.asarray(probs, dtype=float))
    outcomes = np.asarray(outcomes, dtype=int)
    true_probs = probs[np.arange(len(outcomes)), outcomes]
    return float(-np.mean(np.log(true_probs)))


def binary_brier(probs: np.ndarray, outcomes: np.ndarray) -> float:
    probs = np.asarray(probs, dtype=float)
    outcomes = np.asarray(outcomes, dtype=float)
    return float(np.mean((probs - outcomes) ** 2))


def binary_log_loss(probs: np.ndarray, outcomes: np.ndarray) -> float:
    probs = _clip(np.asarray(probs, dtype=float))
    outcomes = np.asarray(outcomes, dtype=float)
    return float(-np.mean(outcomes * np.log(probs) + (1 - outcomes) * np.log(1 - probs)))


def accuracy(probs: np.ndarray, outcomes: np.ndarray) -> float:
    return float(np.mean(np.argmax(probs, axis=1) == outcomes))


@dataclass
class ReliabilityBin:
    lower: float
    upper: float
    n: int
    mean_predicted: float | None
    observed_frequency: float | None


def reliability_diagram(
    probs: np.ndarray, outcomes: np.ndarray, n_bins: int = 10
) -> list[ReliabilityBin]:
    """Group binary forecasts into equal-width bins and compare predicted vs observed."""
    probs = np.asarray(probs, dtype=float)
    outcomes = np.asarray(outcomes, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bins: list[ReliabilityBin] = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (probs >= lo) & (probs < hi if i < n_bins - 1 else probs <= hi)
        n = int(mask.sum())
        if n == 0:
            bins.append(ReliabilityBin(lo, hi, 0, None, None))
        else:
            bins.append(
                ReliabilityBin(
                    lower=float(lo),
                    upper=float(hi),
                    n=n,
                    mean_predicted=float(probs[mask].mean()),
                    observed_frequency=float(outcomes[mask].mean()),
                )
            )
    return bins


def expected_calibration_error(
    probs: np.ndarray, outcomes: np.ndarray, n_bins: int = 10
) -> float:
    bins = reliability_diagram(probs, outcomes, n_bins=n_bins)
    total = sum(b.n for b in bins)
    if total == 0:
        return 0.0
    ece = 0.0
    for b in bins:
        if b.n and b.mean_predicted is not None and b.observed_frequency is not None:
            ece += (b.n / total) * abs(b.observed_frequency - b.mean_predicted)
    return float(ece)
