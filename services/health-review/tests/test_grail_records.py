from datetime import UTC, datetime, timedelta

import pytest

from opstriage_insights.adapters.grail_records import grail_time, log_record, metric_series, problem_record


def test_nanosecond_timestamps_parse_to_utc_microseconds() -> None:
    assert grail_time("2026-10-12T05:59:59.123456789Z") == datetime(
        2026, 10, 12, 5, 59, 59, 123456, tzinfo=UTC
    )
    assert grail_time("2026-10-12T06:00:00Z") == datetime(2026, 10, 12, 6, tzinfo=UTC)
    assert grail_time(None) is None


def test_timeseries_record_with_string_or_int_nanosecond_interval() -> None:
    raw = {
        "timeframe": {"start": "2026-10-12T00:00:00.000000000Z", "end": "2026-10-12T00:15:00.000000000Z"},
        "interval": "300000000000",
        "value": [1, None, 3.5],
        "entity.name": "kafka-1",
    }
    s = metric_series(raw, "m")
    assert s.interval == timedelta(minutes=5) and s.values == (1.0, None, 3.5)
    assert s.dimensions == {"entity.name": "kafka-1"} and s.label == "kafka-1"
    assert [v for _, v in s.points()] == [1.0, 3.5]
    assert metric_series({**raw, "interval": 300_000_000_000}, "m").interval == timedelta(minutes=5)


def test_log_record_tolerates_list_entities_and_unknown_status() -> None:
    r = log_record(
        {
            "timestamp": "2026-10-12T05:00:00.000000000Z",
            "status": "weird",
            "content": "x",
            "dt.source_entity": ["PROCESS_GROUP_INSTANCE-1"],
        }
    )
    assert r.status == "NONE" and r.entity_id == "PROCESS_GROUP_INSTANCE-1"
    with pytest.raises(ValueError, match="timestamp"):
        log_record({"content": "no time"})


def test_problem_record_open_and_closed() -> None:
    p = problem_record(
        {
            "event.id": "1_2V2",
            "display_id": "P-1",
            "event.name": "Process unavailable",
            "event.status": "closed",
            "event.start": "2026-10-12T01:00:00.000000000Z",
            "event.end": "2026-10-12T01:04:00.000000000Z",
            "affected_entity_ids": ["A"],
        }
    )
    assert p.status == "CLOSED" and p.duration == timedelta(minutes=4) and p.entity_ids == ("A",)
    open_p = problem_record({"event.id": "2", "event.start": "2026-10-12T01:00:00Z"})
    assert open_p.end is None and open_p.display_id == "2" and open_p.duration is None
