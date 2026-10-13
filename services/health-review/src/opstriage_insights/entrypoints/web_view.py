"""View models for the web pages (OTA-APP-002 7.1). Pure functions over the stored report document.

Everything returned here is plain data; the templates escape all of it (Jinja2 autoescape, no |safe).
SVG geometry is numbers only, so no untrusted text ever reaches an attribute.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from opstriage_insights.domain.findings import RISK_ORDER
from opstriage_insights.domain.report import KIND_TITLES

SPARK_W, SPARK_H = 160, 32
BAR_W, BAR_H = 120, 20


@dataclass(frozen=True, slots=True)
class Spark:
    path: str  # SVG path data
    last_x: float
    last_y: float
    label: str  # min and max, for screen readers


def sparkline(
    values: Sequence[float | None], w: int = SPARK_W, h: int = SPARK_H, pad: float = 3.0
) -> Spark | None:
    """A polyline split at gaps (None), scaled to the series' own min and max."""
    points = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(points) < 2:
        return None
    lo, hi = min(v for _, v in points), max(v for _, v in points)
    span = (hi - lo) or 1.0
    step = (w - 2 * pad) / max(len(values) - 1, 1)

    def xy(i: int, v: float) -> tuple[float, float]:
        return round(pad + i * step, 1), round(h - pad - (v - lo) / span * (h - 2 * pad), 1)

    parts, prev = [], None
    for i, v in points:
        x, y = xy(i, v)
        parts.append(f"{'L' if prev == i - 1 else 'M'}{x} {y}")
        prev = i
    lx, ly = xy(*points[-1])
    return Spark(" ".join(parts), lx, ly, f"min {fmt_num(lo)}, max {fmt_num(hi)}")


@dataclass(frozen=True, slots=True)
class Bar:
    x: float
    y: float
    w: float
    h: float
    title: str


def hourly_bars(hourly: Sequence[dict[str, Any]], w: int = BAR_W, h: int = BAR_H) -> list[Bar]:
    if not hourly:
        return []
    top = max(int(x["count"]) for x in hourly) or 1
    bw = w / len(hourly)
    out = []
    for i, x in enumerate(hourly):
        bh = max(1.0, int(x["count"]) / top * h)
        out.append(
            Bar(
                round(i * bw, 1),
                round(h - bh, 1),
                round(max(bw - 1, 1), 1),
                round(bh, 1),
                f"{str(x['hour'])[11:16]} UTC: {x['count']}",
            )
        )
    return out


def fmt_num(v: float) -> str:
    """Compact, human-scale numbers: 18.07, 1.2M, 45k, 0.123."""
    a = abs(v)
    if a >= 1e6:
        return f"{v / 1e6:.1f}M"
    if a >= 1e4:
        return f"{v / 1e3:.0f}k"
    if a >= 100:
        return f"{v:.0f}"
    return f"{v:.2f}".rstrip("0").rstrip(".") if a >= 1 or a == 0 else f"{v:.3f}".rstrip("0").rstrip(".")


def expires_in(expires_at: datetime, now: datetime) -> str:
    left = expires_at - now
    if left <= timedelta(0):
        return "expired"
    hours = left / timedelta(hours=1)
    return f"expires in {hours:.0f} h" if hours >= 1 else f"expires in {left / timedelta(minutes=1):.0f} min"


def anchor(cid: str) -> str:
    return f"ev-{cid.lower()}"


def report_view(report: dict[str, Any], now: datetime) -> dict[str, Any]:
    evidence = []
    for e in report["evidence"]:
        item = dict(e, anchor=anchor(e["id"]))
        if e["type"] == "metric":
            item["spark"] = sparkline(e.get("sparkline") or [])
        if e["type"] == "log":
            item["bars"] = hourly_bars(e.get("hourly") or [])
        evidence.append(item)
    known = {e["id"] for e in report["evidence"]}
    groups = []
    for kind, title in KIND_TITLES.items():
        items = [
            dict(f, cites=[(c, anchor(c) if c in known else None) for c in f["citations"]])
            for f in report["findings"]
            if f["kind"] == kind
        ]
        if items:
            groups.append({"kind": kind, "title": title, "findings": items})
    recs = [
        dict(r, cites=[(c, anchor(c) if c in known else None) for c in r["citations"]])
        for r in report["recommendations"]
    ]
    by_risk = report["counts"]["by_risk"]
    return {
        "r": report,
        "groups": groups,
        "recommendations": recs,
        "evidence": evidence,
        "risk_counts": [(k, by_risk.get(k, 0)) for k in sorted(RISK_ORDER, key=lambda k: -RISK_ORDER[k])],
        "expires": expires_in(datetime.fromisoformat(report["expires_at"]), now),
    }
