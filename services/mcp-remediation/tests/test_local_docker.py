from datetime import UTC, datetime

from opstriage_remediation.adapters.local_docker import parse_docker_time


def test_parse_docker_time_truncates_nanoseconds() -> None:
    assert parse_docker_time("2026-10-12T17:55:51.225825041Z") == datetime(
        2026, 10, 12, 17, 55, 51, 225825, tzinfo=UTC
    )
    assert parse_docker_time("2026-10-12T17:55:51Z") == datetime(2026, 10, 12, 17, 55, 51, tzinfo=UTC)
    assert parse_docker_time("0001-01-01T00:00:00Z") is None and parse_docker_time(None) is None
