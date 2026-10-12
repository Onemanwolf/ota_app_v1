"""Pure metrics for the decision-model eval harness (spec 12.4.5). No I/O, no frameworks."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

ACTIONS = ("notify_only", "restart_container", "open_ticket", "escalate_human", "suppress", "cannot_tell")


@dataclass(frozen=True, slots=True)
class Prediction:
    case_id: str
    label: str
    predicted: str
    probabilities: dict[str, float]
    confidence: float | None  # the provider's own confidence (Kev: chance-normalised top probability)
    auto_remediable: float | None
    noise: float | None
    severity_score: float | None
    latency_ms: int | None
    shuffled_predicted: str | None = None  # same case, options shuffled
    error: str | None = None

    @property
    def top_probability(self) -> float | None:
        return max(self.probabilities.values()) if self.probabilities else None

    @property
    def correct(self) -> bool:
        return self.error is None and self.predicted == self.label


@dataclass(frozen=True, slots=True)
class SweepPoint:
    threshold: float
    automated: int
    correct: int
    precision: float | None
    coverage: float


@dataclass(slots=True)
class Report:
    n: int
    errors: int
    accuracy: float
    macro_f1: float
    per_class: dict[str, dict[str, float]]
    confusion: dict[str, dict[str, int]]
    brier: float
    latency_p50: float | None
    latency_p95: float | None
    confident_error_rate: dict[str, float]
    sweeps: dict[str, list[SweepPoint]]
    recommendation: dict[str, Any]
    order_sensitivity: float | None
    extra: dict[str, Any] = field(default_factory=dict)


def percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def per_class_f1(preds: Sequence[Prediction]) -> tuple[dict[str, dict[str, float]], float]:
    stats: dict[str, dict[str, float]] = {}
    for a in ACTIONS:
        tp = sum(p.predicted == a and p.label == a for p in preds)
        fp = sum(p.predicted == a and p.label != a for p in preds)
        fn = sum(p.predicted != a and p.label == a for p in preds)
        support = sum(p.label == a for p in preds)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        stats[a] = {"precision": precision, "recall": recall, "f1": f1, "support": float(support)}
    present = [a for a in ACTIONS if stats[a]["support"] > 0]
    macro = sum(stats[a]["f1"] for a in present) / len(present) if present else 0.0
    return stats, macro


def brier(preds: Sequence[Prediction]) -> float:
    """Multi-class Brier score on the action question (lower is better; 0 is perfect, 2 is the worst)."""
    scored = [p for p in preds if p.probabilities]
    if not scored:
        return math.nan
    total = 0.0
    for p in scored:
        total += sum((p.probabilities.get(a, 0.0) - (1.0 if a == p.label else 0.0)) ** 2 for a in ACTIONS)
    return total / len(scored)


def restart_sweep(preds: Sequence[Prediction], measure: str) -> list[SweepPoint]:
    """For each threshold: precision of automated restarts and coverage of true restart cases.

    The spec sweeps 0.50-0.99; we sweep from 0.00 because Kev's chance-normalised confidence sits well
    below 0.50 (ADR 0006), and a sweep that starts at 0.50 shows nothing.
    """
    true_restarts = sum(p.label == "restart_container" for p in preds)
    points = []
    for step in range(0, 100):
        t = step / 100
        automated = [
            p for p in preds if p.predicted == "restart_container" and (_measure(p, measure) or 0) >= t
        ]
        correct = sum(p.label == "restart_container" for p in automated)
        points.append(
            SweepPoint(
                threshold=t,
                automated=len(automated),
                correct=correct,
                precision=correct / len(automated) if automated else None,
                coverage=correct / true_restarts if true_restarts else 0.0,
            )
        )
    return points


def recommend(points: Sequence[SweepPoint], precision_target: float) -> SweepPoint | None:
    """Lowest threshold whose automated restarts meet the precision target (and automate at least one)."""
    return next(
        (p for p in points if p.automated and p.precision is not None and p.precision >= precision_target),
        None,
    )


def _measure(p: Prediction, measure: str) -> float | None:
    return p.confidence if measure == "confidence" else p.top_probability


def evaluate(preds: Sequence[Prediction], precision_target: float) -> Report:
    ok = [p for p in preds if p.error is None]
    per_class, macro = per_class_f1(ok)
    confusion = {a: {b: sum(p.label == a and p.predicted == b for p in ok) for b in ACTIONS} for a in ACTIONS}
    latencies = [float(p.latency_ms) for p in ok if p.latency_ms is not None]
    sweeps = {m: restart_sweep(ok, m) for m in ("confidence", "top_probability")}
    recommendation: dict[str, Any] = {}
    for measure, points in sweeps.items():
        best = recommend(points, precision_target)
        recommendation[measure] = (
            None
            if best is None
            else {"threshold": best.threshold, "precision": best.precision, "coverage": best.coverage}
        )
    shuffled = [p for p in ok if p.shuffled_predicted is not None]
    return Report(
        n=len(preds),
        errors=len(preds) - len(ok),
        accuracy=sum(p.correct for p in ok) / len(ok) if ok else 0.0,
        macro_f1=macro,
        per_class=per_class,
        confusion=confusion,
        brier=brier(ok),
        latency_p50=percentile(latencies, 0.5),
        latency_p95=percentile(latencies, 0.95),
        confident_error_rate={
            m: (sum((_measure(p, m) or 0) >= 0.9 and not p.correct for p in ok) / len(ok) if ok else 0.0)
            for m in ("confidence", "top_probability")
        },
        sweeps=sweeps,
        recommendation=recommendation,
        order_sensitivity=(
            sum(p.shuffled_predicted != p.predicted for p in shuffled) / len(shuffled) if shuffled else None
        ),
    )
