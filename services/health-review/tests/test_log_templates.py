from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from opstriage_insights.adapters.fixtures import FixtureLogSource, FixtureStore
from opstriage_insights.domain.log_templates import mask, mine, split_log4j
from opstriage_insights.domain.model import ClusterScope, LogRecord
from opstriage_insights.domain.redaction import Redactor

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "insights"
ANCHOR = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
STORE = FixtureStore(FIXTURES, lambda: ANCHOR, shift_to_now=False)
REDACT = Redactor()


async def _logs(cluster: str) -> list[LogRecord]:
    return await FixtureLogSource(STORE).fetch(
        ClusterScope(cluster), ANCHOR - timedelta(hours=24), ANCHOR, 50_000
    )


def _rec(content: str, minute: int = 0, status: str = "WARN", pod: str = "kafka-0") -> LogRecord:
    return LogRecord(ANCHOR - timedelta(minutes=minute), status, status, content, pod=pod)


def test_log4j_layout_is_split_into_message_and_logger() -> None:
    line = "[2026-10-12 02:10:07,037] WARN Error in response for fetch request (type=Fetch) (kafka.server.ReplicaFetcherThread)"
    assert split_log4j(line) == (
        "Error in response for fetch request (type=Fetch)",
        "kafka.server.ReplicaFetcherThread",
    )
    assert split_log4j("plain text line") == ("plain text line", "")


def test_masking_keeps_names_and_hides_values() -> None:
    assert mask("partition payments-2 ISR Set(1) min.isr requirement of 2") == (
        "partition payments-<n> ISR Set(<n>) min.isr requirement of <n>"
    )
    assert mask("from /10.42.3.x:51234 id 7f3e2d1c0b9a") == "from /<ip> id <id>"


async def test_benign_disconnects_from_many_clients_collapse_into_one_template() -> None:
    templates = mine(await _logs("healthy-steady"), REDACT)
    disconnects = [t for t in templates if "Unexpected error from" in t.text]
    assert len(disconnects) == 1
    t = disconnects[0]
    assert t.logger == "org.apache.kafka.common.network.Selector"
    assert t.count >= 10 and len(t.sources) >= 2 and len(t.examples) == 2


async def test_planted_patterns_become_distinct_counted_templates() -> None:
    templates = mine(await _logs("planted-under-min-isr"), REDACT)
    by_logger = {t.logger: t for t in templates}
    broker = by_logger["kafka.server.ReplicaManager"]
    producer = by_logger["org.apache.kafka.clients.producer.internals.Sender"]
    assert "NotEnoughReplicasException" in broker.text and broker.status == "ERROR"
    assert broker.count == producer.count >= 14
    assert "correlation id <n>" in producer.text  # varying ids masked, not split into many templates
    assert templates[0].count >= templates[-1].count


async def test_growing_error_shows_in_hourly_counts() -> None:
    fd = next(t for t in mine(await _logs("planted-fd-limit"), REDACT) if "Too many open files" in t.text)
    hours = sorted(fd.hourly.items())
    assert fd.count == sum(fd.hourly.values()) == 55
    assert hours[-1][1] > hours[0][1]  # newest hour has more than the oldest
    assert fd.first_seen and fd.last_seen and fd.first_seen < fd.last_seen


def test_differing_tokens_become_wildcards_but_different_shapes_stay_apart() -> None:
    templates = mine(
        [
            _rec("[2026-10-12 01:00:00,000] WARN Group orders rebalancing because member alpha left (c.G)"),
            _rec(
                "[2026-10-12 01:01:00,000] WARN Group orders rebalancing because member bravo left (c.G)", 1
            ),
            _rec("[2026-10-12 01:02:00,000] WARN Something else entirely happened here today (c.G)", 2),
        ],
        REDACT,
    )
    assert len(templates) == 2
    assert templates[0].text == "Group orders rebalancing because member <*> left" and templates[0].count == 2


def test_examples_are_redacted_and_status_is_the_worst_seen() -> None:
    t = mine(
        [
            _rec("login failed password=hunter2 for x", 0, "WARN"),
            _rec("login failed password=swordfish for x", 1, "ERROR"),
        ],
        REDACT,
    )[0]
    assert t.status == "ERROR"
    assert all("hunter2" not in e and "swordfish" not in e for e in t.examples)
    assert "<redacted>" in t.text


@pytest.mark.parametrize(
    "cluster", ["planted-injected-log", "planted-rebalance-storms", "planted-ssl-one-client"]
)
async def test_templates_are_few_compared_with_lines(cluster: str) -> None:
    logs = await _logs(cluster)
    templates = mine(logs, REDACT)
    assert len(templates) <= 6 < len(logs)
