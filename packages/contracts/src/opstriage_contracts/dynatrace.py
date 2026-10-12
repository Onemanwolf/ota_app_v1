"""Pure normalizations for Dynatrace's four problem shapes (Grail/Workflow, Problems API v2, classic webhook).

Shared by the intake webhook adapters and the poller, which both receive the Problems API v2 object
(the classic webhook embeds it as {ProblemDetailsJSONv2}). Every mapping here is DOCUMENTED in
reports/Dynatrace Kafka triage data models.md ("One normalization table reconciles the four paths").
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, cast

from opstriage_contracts.problems import EntityRef, Evidence, EvidenceType, ImpactLevel

# Problems API v2 / classic say PERFORMANCE where Grail says SLOWDOWN.
_CATEGORY = {"PERFORMANCE": "SLOWDOWN", "RESOURCE": "RESOURCE_CONTENTION", "CUSTOM": "CUSTOM_ALERT"}
# v2 SERVICES, classic SERVICE and Grail "Services" are the same level.
_IMPACT = {"SERVICES": "SERVICE"}
_IMPACT_LEVELS = {"APPLICATION", "ENVIRONMENT", "INFRASTRUCTURE", "SERVICE", "SYNTHETIC"}
_EVIDENCE_TYPES = {"EVENT", "METRIC", "TRANSACTIONAL", "MAINTENANCE_WINDOW", "AVAILABILITY_EVIDENCE"}

# Documented category order, most severe first (event categories page). Used when event.severity is null.
CATEGORY_RANK = (
    "MONITORING_UNAVAILABLE",
    "AVAILABILITY",
    "ERROR",
    "SLOWDOWN",
    "RESOURCE_CONTENTION",
    "CUSTOM_ALERT",
    "INFO",
)


def category(value: str | None) -> str | None:
    if not value:
        return None
    upper = value.strip().upper()
    return _CATEGORY.get(upper, upper)


def impact_levels(value: str | Iterable[str] | None) -> tuple[ImpactLevel, ...]:
    """Grail dt.davis.impact_level is documented as a string but observed as a list; accept both."""
    if not value:
        return ()
    items = [value] if isinstance(value, str) else list(value)
    out: list[ImpactLevel] = []
    for item in items:
        level = _IMPACT.get(str(item).strip().upper(), str(item).strip().upper())
        if level in _IMPACT_LEVELS and level not in out:
            out.append(cast(ImpactLevel, level))
    return tuple(out)


def entity_type(value: str | None) -> str:
    """PROCESS_GROUP_INSTANCE or dt.entity.process_group_instance -> process_group_instance."""
    raw = (value or "unknown").strip()
    if raw.startswith("dt.entity."):
        raw = raw[len("dt.entity.") :]
    return raw.lower()


def smartscape_entity(record: dict[str, Any] | str | None) -> EntityRef | None:
    """Grail {id, type, name}; tolerate plain ID strings (type taken from the ID prefix)."""
    if not record:
        return None
    if isinstance(record, str):
        return EntityRef(id=record, type=entity_type(record.split("-", 1)[0]))
    return EntityRef(id=record["id"], type=entity_type(record.get("type")), name=record.get("name"))


def v2_entity(stub: dict[str, Any] | None) -> EntityRef | None:
    """Problems API v2 EntityStub {entityId: {id, type}, name?} (name omitted when the entity is gone)."""
    if not stub or not stub.get("entityId"):
        return None
    eid = stub["entityId"]
    return EntityRef(id=eid["id"], type=entity_type(eid.get("type")), name=stub.get("name"))


def epoch_ms(value: int | float | None) -> datetime | None:
    """v2 times are epoch ms; endTime is -1 while the problem (or evidence) is still open."""
    if value is None or value < 0:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def v2_evidence(details: dict[str, Any] | None) -> tuple[Evidence, ...]:
    """evidenceDetails.details[] -> typed Evidence. Unknown evidence types are skipped, not guessed."""
    out: list[Evidence] = []
    for item in (details or {}).get("details") or []:
        kind = str(item.get("evidenceType", "")).upper()
        if kind not in _EVIDENCE_TYPES:
            continue
        out.append(
            Evidence(
                evidence_type=cast(EvidenceType, kind),
                display_name=str(item.get("displayName") or kind.title()),
                entity=v2_entity(item.get("entity")),
                root_cause_relevant=item.get("rootCauseRelevant"),
                started_at=epoch_ms(item.get("startTime")),
                ended_at=epoch_ms(item.get("endTime")),
                event_type=item.get("eventType"),
                event_id=item.get("eventId"),
                metric_id=item.get("metricId"),
                unit=item.get("unit"),
                value_before=item.get("valueBeforeChangePoint"),
                value_after=item.get("valueAfterChangePoint"),
            )
        )
    return tuple(out)


def v2_tags(entity_tags: list[dict[str, Any]] | None) -> dict[str, str]:
    """entityTags [{key, value, stringRepresentation}] -> {key: value or 'true'}."""
    tags: dict[str, str] = {}
    for tag in entity_tags or []:
        key = tag.get("key")
        if key:
            tags[str(key)] = str(tag["value"]) if tag.get("value") is not None else "true"
    return tags


def grail_time(value: Any) -> Any:
    """Grail timestamps arrive as ISO-8601 with nanoseconds or as epoch nanoseconds (undocumented).

    ISO strings pass through (pydantic truncates nanoseconds to microseconds). Epoch integers are scaled by
    size: nanoseconds (>1e17) and microseconds (>1e14) become milliseconds, which pydantic accepts.
    """
    if isinstance(value, int | float) and not isinstance(value, bool):
        if value > 1e17:
            return int(value // 1_000_000)
        if value > 1e14:
            return int(value // 1_000)
    return value
