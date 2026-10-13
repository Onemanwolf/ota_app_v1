"""Turn log records into templates with counts (OTA-APP-002 3.3 step 4). A simplified Drain:

1. split the Kafka log4j layout `[ts] LEVEL message (logger)` (other layouts keep the whole line as the
   message);
2. mask variable parts: timestamps, IPs, UUIDs, hex ids, `name-<n>` suffixes (partitions, pods, client
   ids), numbers;
3. group by (logger, token count, first token) and merge a line into an existing template when at least
   `similarity` of its tokens match, replacing the differing positions with `<*>`.

Templates hold counts per hour, first and last seen, sources and up to two redacted examples. Raw text
never leaves this module unredacted: examples go through the redactor first.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime

from opstriage_insights.domain.model import LogRecord

_LOG4J = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s+(?P<level>[A-Z]+)\s+(?P<msg>.*?)(?:\s+\((?P<logger>[\w.$]+)\))?\s*$", re.DOTALL
)
_MASKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:[.,]\d+)?Z?"), "<ts>"),
    (
        re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE),
        "<uuid>",
    ),
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.(?:\d{1,3}|x)(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{8,}\b", re.IGNORECASE), "<id>"),
    (re.compile(r"\b([A-Za-z_][\w.]*(?:-[A-Za-z_][\w.]*)*)-\d+\b"), r"\1-<n>"),
    (re.compile(r"(?<![\w<])-?\d+(?:\.\d+)?(?![\w>])"), "<n>"),
)
_SEVERITY = {"ERROR": 3, "WARN": 2, "INFO": 1, "NONE": 0}
WILDCARD = "<*>"


def split_log4j(content: str) -> tuple[str, str]:
    """(message, logger). Falls back to (content, "") for other layouts."""
    m = _LOG4J.match(content)
    if not m:
        return content.strip(), ""
    return m.group("msg").strip(), m.group("logger") or ""


def mask(message: str) -> str:
    for pattern, replacement in _MASKS:
        message = pattern.sub(replacement, message)
    return " ".join(message.split())


@dataclass
class LogTemplate:
    key: str
    logger: str
    tokens: list[str]
    status: str
    count: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    hourly: Counter[datetime] = field(default_factory=Counter)
    sources: Counter[str] = field(default_factory=Counter)
    examples: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(self.tokens)

    def add(self, record: LogRecord, redacted: str, max_examples: int) -> None:
        self.count += 1
        t = record.timestamp
        self.first_seen = t if self.first_seen is None else min(self.first_seen, t)
        self.last_seen = t if self.last_seen is None else max(self.last_seen, t)
        self.hourly[t.replace(minute=0, second=0, microsecond=0)] += 1
        self.sources[record.pod or record.entity_id or "unknown"] += 1
        if _SEVERITY.get(record.status, 0) > _SEVERITY.get(self.status, 0):
            self.status = record.status
        if len(self.examples) < max_examples and redacted not in self.examples:
            self.examples.append(redacted)


def _similarity(a: list[str], b: list[str]) -> float:
    same = sum(1 for x, y in zip(a, b, strict=True) if x == y or WILDCARD in (x, y))
    return same / len(a)


class TemplateMiner:
    def __init__(
        self, redact: Callable[[str], str], *, similarity: float = 0.7, max_examples: int = 2
    ) -> None:
        self._redact, self._similarity, self._max_examples = redact, similarity, max_examples
        self._buckets: dict[tuple[str, int, str], list[LogTemplate]] = {}

    def add(self, record: LogRecord) -> LogTemplate:
        redacted = self._redact(record.content)
        message, logger = split_log4j(redacted)
        tokens = mask(message).split() or ["<empty>"]
        bucket = self._buckets.setdefault((logger, len(tokens), tokens[0]), [])
        best = max(bucket, key=lambda t: _similarity(tokens, t.tokens), default=None)
        if best is not None and _similarity(tokens, best.tokens) >= self._similarity:
            best.tokens = [x if x == y else WILDCARD for x, y in zip(best.tokens, tokens, strict=True)]
            template = best
        else:
            key = hashlib.sha1(f"{logger}|{' '.join(tokens)}".encode(), usedforsecurity=False).hexdigest()[
                :10
            ]
            template = LogTemplate(key=key, logger=logger, tokens=tokens, status=record.status)
            bucket.append(template)
        template.add(record, redacted, self._max_examples)
        return template

    def templates(self) -> list[LogTemplate]:
        """Most frequent first; ties by most severe, then newest."""
        all_ = [t for b in self._buckets.values() for t in b]
        return sorted(all_, key=_rank)


def _rank(t: LogTemplate) -> tuple[int, int, float]:
    return (-t.count, -_SEVERITY.get(t.status, 0), -(t.last_seen.timestamp() if t.last_seen else 0.0))


def mine(
    records: Iterable[LogRecord],
    redact: Callable[[str], str],
    *,
    similarity: float = 0.7,
    max_examples: int = 2,
) -> list[LogTemplate]:
    miner = TemplateMiner(redact, similarity=similarity, max_examples=max_examples)
    for r in records:
        miner.add(r)
    return miner.templates()
