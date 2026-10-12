import pytest

from opstriage_triage.application.eval_metrics import (
    Prediction,
    brier,
    evaluate,
    percentile,
    recommend,
    restart_sweep,
)


def pred(
    label: str, predicted: str, conf: float, top: float | None = None, shuffled: str | None = None
) -> Prediction:
    top = conf if top is None else top
    probs = {predicted: top} | {
        a: (1 - top) / 5
        for a in (
            "notify_only",
            "open_ticket",
            "escalate_human",
            "suppress",
            "cannot_tell",
            "restart_container",
        )
        if a != predicted
    }
    return Prediction("c", label, predicted, probs, conf, 0.8, 0.1, 1.0, 100, shuffled_predicted=shuffled)


def test_sweep_precision_coverage_and_recommendation() -> None:
    preds = [
        pred("restart_container", "restart_container", 0.9),
        pred("restart_container", "restart_container", 0.6),
        pred("escalate_human", "restart_container", 0.55),
        pred("restart_container", "notify_only", 0.5),
    ]
    points = {p.threshold: p for p in restart_sweep(preds, "confidence")}
    assert points[0.5].automated == 3 and points[0.5].precision == pytest.approx(2 / 3)
    assert points[0.56].precision == 1.0 and points[0.56].coverage == pytest.approx(2 / 3)
    best = recommend(list(points.values()), 0.98)
    assert best is not None and best.threshold == 0.56


def test_no_recommendation_when_target_unreachable() -> None:
    assert (
        recommend(restart_sweep([pred("notify_only", "restart_container", 0.99)], "confidence"), 0.98) is None
    )


def test_report_metrics() -> None:
    preds = [
        pred("notify_only", "notify_only", 0.95, shuffled="notify_only"),
        pred("suppress", "notify_only", 0.95, shuffled="suppress"),
    ]
    report = evaluate(preds, 0.98)
    assert report.accuracy == 0.5
    assert report.confident_error_rate["confidence"] == 0.5
    assert report.order_sensitivity == 0.5
    assert report.confusion["suppress"]["notify_only"] == 1


def test_brier_perfect_and_percentiles() -> None:
    perfect = Prediction("c", "suppress", "suppress", {"suppress": 1.0}, 1.0, None, None, None, None)
    assert brier([perfect]) == 0.0
    assert percentile([1, 2, 3, 4], 0.5) == 2.5 and percentile([], 0.5) is None
