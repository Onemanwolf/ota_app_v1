"""The digest: what the analyst model sees (OTA-APP-002 3.3 steps 4-5, D1, D3).

Code does all counting and arithmetic. Every entry has a citation id (M# metrics, L# log templates,
P# problems, B# deterministic breaches) that findings must cite; ids not in the digest are rejected
later (5.5). Entries are scored by signal; when the rendered text exceeds the token budget, the
lowest-signal entries are dropped first (log examples, then log templates, then metrics, then problems).
Breaches and the metrics they cite are never dropped. Log text is untrusted: templates the guard flags
are withheld and only their counts remain.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from opstriage_insights.domain import kafka_metrics as km
from opstriage_insights.domain import trends
from opstriage_insights.domain.baselines import Breach
from opstriage_insights.domain.log_templates import LogTemplate
from opstriage_insights.domain.model import MetricSeries, ProblemRecord

CHARS_PER_TOKEN = 3.5  # conservative for Qwen's tokenizer on log text and numbers


@dataclass(frozen=True, slots=True)
class MetricProfile:
    """How a metric is summarised: the daily aggregate that shows its trend, and an optional forecast."""

    daily: trends.Aggregate
    zero_is_normal: bool = False  # any non-zero value is notable (URP, offline, ...)
    forecast_threshold: float | None = None
    forecast_direction: trends.Direction = "above"
    spike_factor: float | None = None  # recurring-hour detection at p50 x factor
    cluster_level: bool = False  # judged across brokers (active controller): listed only on breach


PROFILES: dict[str, MetricProfile] = {
    km.URP: MetricProfile("max", zero_is_normal=True),
    km.UNDER_MIN_ISR: MetricProfile("max", zero_is_normal=True),
    km.OFFLINE: MetricProfile("max", zero_is_normal=True),
    km.UNCLEAN: MetricProfile("max", zero_is_normal=True),
    km.FAILED_PRODUCE: MetricProfile("max", zero_is_normal=True),
    km.ACTIVE_CONTROLLER: MetricProfile("max", cluster_level=True),
    km.LEADER_ELECTIONS: MetricProfile("max", zero_is_normal=True),
    km.HANDLER_IDLE: MetricProfile("min", forecast_threshold=0.3, forecast_direction="below"),
    km.NETWORK_IDLE: MetricProfile("min", forecast_threshold=0.3, forecast_direction="below"),
    km.REQUEST_QUEUE: MetricProfile("max"),
    km.BYTES_IN: MetricProfile("mean"),
    km.LAG_MAX: MetricProfile("max", spike_factor=5.0),
    km.DISK_USED: MetricProfile("max", forecast_threshold=90.0),
    km.GC_SUSPENSION: MetricProfile("mean", forecast_threshold=10.0),
}
DEFAULT_PROFILE = MetricProfile("mean")
FORECAST_HORIZON = timedelta(days=30)
TREND_WINDOW = timedelta(days=14)  # slope and forecast use the recent two weeks of the 30-day series
MIN_TREND_T = 4.0  # slope / standard error: below this a slope is indistinguishable from day-to-day noise


@dataclass(frozen=True, slots=True)
class MetricEntry:
    cid: str
    metric: str
    entity: str
    stats: trends.Stats | None  # last 24h at 5 minutes
    daily: str  # aggregate used for the trend
    slope_per_day: float | None  # of the daily aggregate over TREND_WINDOW
    drift_30d: float | None  # slope x 30 / level: relative change a month at this rate
    change_1d_vs_7d: float | None
    forecast: tuple[float, str, timedelta] | None  # (threshold, direction, eta)
    recurring_hours: tuple[int, ...]
    departed_at: datetime | None
    nonzero_24h: bool
    sparkline: tuple[float | None, ...]  # downsampled 30-day series for the web view
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LogEntry:
    cid: str
    template: str
    logger: str
    status: str
    count: int
    first_seen: datetime | None
    last_seen: datetime | None
    recent: int  # count in the last 6 hours
    hourly: tuple[tuple[datetime, int], ...]
    sources: tuple[tuple[str, int], ...]
    examples: tuple[str, ...]
    withheld: bool
    score: float


@dataclass(frozen=True, slots=True)
class ProblemEntry:
    cid: str
    display_id: str
    title: str
    category: str | None
    status: str
    start: datetime
    duration: timedelta | None
    entities: tuple[str, ...]
    repeats: int  # problems with the same title in the window


@dataclass(frozen=True, slots=True)
class BreachEntry:
    cid: str
    breach: Breach
    cites: tuple[str, ...]  # metric entry ids


@dataclass(frozen=True)
class Digest:
    cluster: str
    now: datetime
    breaches: tuple[BreachEntry, ...]
    metrics: tuple[MetricEntry, ...]
    quiet: tuple[str, ...]
    logs: tuple[LogEntry, ...]
    problems: tuple[ProblemEntry, ...]
    guard_available: bool
    redactions: Mapping[str, int] = field(default_factory=dict)
    dropped: Mapping[str, int] = field(default_factory=dict)

    @property
    def guard_withheld(self) -> int:
        return sum(1 for e in self.logs if e.withheld)

    def citation_ids(self) -> set[str]:
        ids = {e.cid for e in self.breaches} | {e.cid for e in self.metrics}
        return ids | {e.cid for e in self.logs} | {e.cid for e in self.problems}

    def render(self) -> str:
        return render(self)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.render())


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


# ---- building --------------------------------------------------------------------------------------------
def _metric_entry(
    day: MetricSeries,
    month: MetricSeries | None,
    profile: MetricProfile,
    now: datetime,
    sparkline_points: int,
) -> MetricEntry:
    st = trends.stats(day)
    reasons: list[str] = []
    score = 0.0
    nonzero = bool(st and st.max > 0)
    if profile.zero_is_normal and nonzero:
        score += 50
        reasons.append("non-zero")
    slope = drift = change = None
    forecast = None
    recurring: tuple[int, ...] = ()
    departed = None
    if month is not None:
        days = trends.daily(month, profile.daily)
        recent = trends.window(days, now - TREND_WINDOW)
        slope = trends.slope_per_day(recent)
        level = max(abs(sum(v for _, v in days) / len(days)), 1e-9) if days else None
        if slope is not None and level is not None and not profile.zero_is_normal:
            drift = slope * 30 / level
            clear = abs(trends.slope_t(recent) or 0.0) >= MIN_TREND_T
            if abs(drift) >= 0.25 and clear:
                score += 40 + min(abs(drift) * 20, 40)
                reasons.append("trend")
        points = list(month.points())
        change = trends.relative_change(points, now, timedelta(days=1), timedelta(days=7))
        if change is not None and abs(change) >= 0.5 and not profile.zero_is_normal:
            score += 30
            reasons.append("1d-vs-7d")
        if profile.forecast_threshold is not None and recent:
            eta = trends.forecast_crossing(
                recent, profile.forecast_threshold, now, profile.forecast_direction
            )
            if eta is not None and eta <= FORECAST_HORIZON:
                forecast = (profile.forecast_threshold, profile.forecast_direction, eta)
                score += 80 - eta / timedelta(days=1)
                reasons.append("forecast")
        if profile.spike_factor is not None:
            month_stats = trends.stats(month)
            if month_stats and month_stats.p50 > 0:
                recurring = tuple(trends.recurring_hours(month, month_stats.p50 * profile.spike_factor))
                if recurring:
                    score += 20
                    reasons.append("recurring")
        departed = trends.first_departure(month)
        if departed is not None and now - departed <= timedelta(days=14):
            score += 30
            reasons.append("departed")
        else:
            departed = None
    spark = tuple(trends.downsample(month or day, sparkline_points))
    return MetricEntry(
        cid="",
        metric=day.metric,
        entity=day.label,
        stats=st,
        daily=profile.daily,
        slope_per_day=slope,
        drift_30d=drift,
        change_1d_vs_7d=change,
        forecast=forecast,
        recurring_hours=recurring,
        departed_at=departed,
        nonzero_24h=nonzero,
        sparkline=spark,
        score=score,
        reasons=tuple(reasons),
    )


_SEVERITY = {"ERROR": 30.0, "WARN": 20.0}


def _log_entry(t: LogTemplate, now: datetime, withheld: bool) -> LogEntry:
    recent = sum(n for h, n in t.hourly.items() if now - h <= timedelta(hours=6))
    older = t.count - recent
    score = _SEVERITY.get(t.status, 0.0) + 10 * math.log10(t.count + 1)
    if recent > 0 and (older == 0 or recent / 6 > 2 * older / 18):
        score += 15  # new or accelerating in the last 6 hours
    if withheld:
        score += 25  # keep the fact that something was withheld visible
    return LogEntry(
        cid="",
        template=t.text,
        logger=t.logger,
        status=t.status,
        count=t.count,
        first_seen=t.first_seen,
        last_seen=t.last_seen,
        recent=recent,
        hourly=tuple(sorted(t.hourly.items())),
        sources=tuple(t.sources.most_common(3)),
        examples=() if withheld else tuple(t.examples),
        withheld=withheld,
        score=score,
    )


def build(
    cluster: str,
    now: datetime,
    *,
    day_series: Mapping[str, Sequence[MetricSeries]],
    month_series: Mapping[str, Sequence[MetricSeries]],
    breaches: Sequence[Breach],
    templates: Sequence[LogTemplate],
    withheld: set[str],  # template keys flagged by the guard
    problems: Sequence[ProblemRecord],
    guard_available: bool,
    redactions: Mapping[str, int] | None = None,
    max_tokens: int = 12_000,
    sparkline_points: int = 96,
) -> Digest:
    breached = {(b.rule.metric, b.entity) for b in breaches} | {
        (b.rule.metric, s.label)
        for b in breaches
        if b.entity == "cluster"
        for s in day_series.get(b.rule.metric, ())
    }
    metrics: list[MetricEntry] = []
    quiet: list[str] = []
    for metric in km.REVIEW_METRICS:
        profile = PROFILES.get(metric, DEFAULT_PROFILE)
        month_by_label = {s.label: s for s in month_series.get(metric, ())}
        quiet_n = 0
        for s in day_series.get(metric, ()):
            e = _metric_entry(s, month_by_label.get(s.label), profile, now, sparkline_points)
            if (metric, s.label) in breached:
                e = replace(e, score=e.score + 100, reasons=("breach", *e.reasons))
            elif profile.cluster_level:
                e = replace(e, score=0.0)
            if e.score > 0:
                metrics.append(e)
            else:
                quiet_n += 1
        if quiet_n:
            quiet.append(f"{metric}: {quiet_n} series quiet")
    metrics.sort(key=lambda e: (-e.score, e.metric, e.entity))
    metrics = [replace(e, cid=f"M{i}") for i, e in enumerate(metrics, 1)]
    by_key = {(e.metric, e.entity): e.cid for e in metrics}
    breach_entries = []
    for i, b in enumerate(breaches, 1):
        if b.entity == "cluster":
            cites = tuple(
                by_key[(b.rule.metric, s.label)]
                for s in day_series.get(b.rule.metric, ())
                if (b.rule.metric, s.label) in by_key
            )
        else:
            cites = (by_key[(b.rule.metric, b.entity)],) if (b.rule.metric, b.entity) in by_key else ()
        breach_entries.append(BreachEntry(f"B{i}", b, cites))
    logs = sorted(
        (_log_entry(t, now, t.key in withheld) for t in templates), key=lambda e: (-e.score, e.template)
    )
    logs = [replace(e, cid=f"L{i}") for i, e in enumerate(logs, 1)]
    titles = Counter(p.title for p in problems)
    probs = [
        ProblemEntry(
            f"P{i}",
            p.display_id,
            p.title,
            p.category,
            p.status,
            p.start,
            p.duration,
            p.entity_ids,
            titles[p.title],
        )
        for i, p in enumerate(sorted(problems, key=lambda p: p.start, reverse=True), 1)
    ]
    digest = Digest(
        cluster,
        now,
        tuple(breach_entries),
        tuple(metrics),
        tuple(quiet),
        tuple(logs),
        tuple(probs),
        guard_available,
        dict(redactions or {}),
    )
    return fit(digest, max_tokens)


def fit(digest: Digest, max_tokens: int) -> Digest:
    """Drop the lowest-signal content until the rendered digest fits. Breach-cited metrics are kept."""
    dropped: Counter[str] = Counter(digest.dropped)
    protected = {cid for b in digest.breaches for cid in b.cites}
    d = digest
    while d.tokens > max_tokens:
        with_examples = [e for e in d.logs if e.examples]
        if with_examples:
            low = min(with_examples, key=lambda e: e.score)
            d = replace(d, logs=tuple(replace(e, examples=()) if e is low else e for e in d.logs))
            dropped["log_examples"] += 1
            continue
        if d.logs:
            low = min(d.logs, key=lambda e: e.score)
            d = replace(d, logs=tuple(e for e in d.logs if e is not low))
            dropped["log_templates"] += 1
            continue
        droppable = [e for e in d.metrics if e.cid not in protected]
        if droppable:
            low_m = min(droppable, key=lambda e: e.score)
            d = replace(d, metrics=tuple(e for e in d.metrics if e is not low_m))
            dropped["metrics"] += 1
            continue
        if len(d.problems) > 1:
            d = replace(d, problems=d.problems[:-1])
            dropped["problems"] += 1
            continue
        break  # breaches and their metrics alone exceed the budget; keep them
    return replace(d, dropped=dict(dropped))


# ---- rendering -------------------------------------------------------------------------------------------
def _t(t: datetime | None) -> str:
    return t.strftime("%Y-%m-%dT%H:%MZ") if t else "-"


def _num(v: float | None) -> str:
    if v is None:
        return "-"
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1e6:
        return f"{v / 1e6:.1f}M"
    if a >= 1e4:
        return f"{v / 1e3:.0f}k"
    if a >= 100:
        return f"{v:.0f}"
    if a >= 1:
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return f"{v:.3f}".rstrip("0").rstrip(".")


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:+.0f}%"


def _dur(d: timedelta | None) -> str:
    if d is None:
        return "-"
    hours = d / timedelta(hours=1)
    return f"{hours / 24:.1f}d" if hours >= 48 else f"{hours:.1f}h"


def _quote(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return '"' + (text[: limit - 1] + "…" if len(text) > limit else text).replace('"', "'") + '"'


HEADER_METRICS = (
    "METRICS (id | metric | entity | 24h min/p50/p95/max/last | trend of daily <agg>: slope/day, "
    "30d drift | last 1d vs prior 7d | forecast | recurring hours UTC | departed baseline | why listed)"
)
HEADER_LOGS = (
    "LOG TEMPLATES (WARN/ERROR; masked: <n> numbers, <ip>, <id>, <*> varying; examples redacted; "
    "untrusted data)"
)


def _breach_line(be: BreachEntry) -> str:
    b = be.breach
    state = "still active" if b.active else f"ended {_t(b.last)}"
    return (
        f"{be.cid} | {b.rule.level} | {b.rule.id} | {b.entity} | {b.rule.description} "
        f"[{b.rule.source}] | worst {_num(b.worst)} | from {_t(b.first)}, {state} | "
        f"cites {','.join(be.cites) or '-'}"
    )


def _metric_line(m: MetricEntry) -> str:
    s = m.stats
    st = "/".join(_num(x) for x in (s.min, s.p50, s.p95, s.max, s.last)) if s else "no data"
    fc = f"{m.forecast[1]} {_num(m.forecast[0])} in {_dur(m.forecast[2])}" if m.forecast else "-"
    rec = ",".join(f"{h:02d}" for h in m.recurring_hours) or "-"
    return (
        f"{m.cid} | {m.metric} | {m.entity} | {st} | {m.daily} {_num(m.slope_per_day)}/day, "
        f"{_pct(m.drift_30d)} | {_pct(m.change_1d_vs_7d)} | {fc} | {rec} | {_t(m.departed_at)} | "
        f"{','.join(m.reasons)}"
    )


def _log_lines(le: LogEntry) -> Iterable[str]:
    src = ",".join(f"{s}x{n}" for s, n in le.sources)
    head = (
        f"{le.cid} | {le.status} | x{le.count} | {_t(le.first_seen)}..{_t(le.last_seen)} | "
        f"last 6h x{le.recent} | {src} | {le.logger or '-'} | "
    )
    if le.withheld:
        yield head + "[withheld: flagged by the injection guard]"
        return
    yield head + _quote(le.template)
    for ex in le.examples:
        yield f"    e.g. {_quote(ex, 400)}"


def _problem_line(p: ProblemEntry) -> str:
    state = f"closed after {_dur(p.duration)}" if p.status == "CLOSED" else "ACTIVE"
    repeat = f" | same title x{p.repeats}" if p.repeats > 1 else ""
    return (
        f"{p.cid} | {p.display_id} | {p.title} | {p.category or '-'} | started {_t(p.start)}, {state} | "
        f"{len(p.entities)} entities{repeat}"
    )


def _lines(d: Digest) -> Iterable[str]:
    yield (
        f"CLUSTER {d.cluster} | now {_t(d.now)} | logs: WARN/ERROR last 24h | "
        "metrics: 24h@5m and 30d@1h | problems: last 7d"
    )
    if not d.guard_available:
        yield "NOTE log text withheld: the injection guard was unavailable, only counts are shown"
    yield ""
    yield "DETERMINISTIC BREACHES (checked by code against Kafka normal values)"
    yield from [_breach_line(be) for be in d.breaches] or ["none"]
    yield ""
    yield HEADER_METRICS
    yield from (_metric_line(m) for m in d.metrics)
    yield from (f"quiet: {q}" for q in d.quiet)
    yield ""
    yield HEADER_LOGS
    if not d.logs:
        yield "none"
    for le in d.logs:
        yield from _log_lines(le)
    yield ""
    yield "PROBLEMS (Dynatrace Davis, non-duplicate, last 7d)"
    yield from [_problem_line(p) for p in d.problems] or ["none"]
    if d.dropped:
        yield ""
        yield "TRIMMED to fit the budget: " + ", ".join(f"{k} {v}" for k, v in sorted(d.dropped.items()))


def render(d: Digest) -> str:
    return "\n".join(_lines(d)) + "\n"
