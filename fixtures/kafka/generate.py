"""Generate realistic Kafka problem fixtures in every Dynatrace delivery shape (stdlib only, deterministic).

Run: uv run python fixtures/kafka/generate.py   (Python 3.13, stdlib only)

Each scenario is defined ONCE and rendered into:
  fixtures/dynatrace/grail_records/kafka_<id>.json  raw dt.davis.problems record = Workflow problem-trigger event()
  fixtures/dynatrace/workflow/kafka_<id>.json       our Workflow HTTP template rendered from that record
  fixtures/dynatrace/grail/kafka_<id>.json          the poller's problems_changed.dql summarize output
  fixtures/dynatrace/classic/kafka_<id>.json        our classic custom-integration template, details = v2 object
  fixtures/dynatrace/problems_v2/kafka_<id>.json    a Problems API v2 page (fields=evidenceDetails,impactAnalysis)
  fixtures/context/kafka_<id>.json                  log lines + evidence the Grail context source would return
  fixtures/kafka/scenarios.json                     index: id, expected label, signature, provenance

Field names and enums are DOCUMENTED (reports/Dynatrace Kafka triage data models.md). Values are INFERRED but
built from documented pieces: Dynatrace default titles and event types, Apache Kafka extension metric keys and
display names (Appendix A), Kafka 3.8 normal values (Appendix B) and Kafka 3.7 source log strings. Log lines not
verified against source are marked "paraphrased" in each scenario's provenance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = datetime(2026, 10, 12, 14, 0, tzinfo=UTC)
TENANT = "https://abc12345.apps.dynatrace.com"
CLUSTER = {"k8s.cluster.name": "prod-aks-weu", "k8s.namespace.name": "streaming"}


# ---------------------------------------------------------------------------------------------- entities
def ent(eid: str, etype: str, name: str) -> dict[str, str]:
    """Grail smartscape shape {id, type, name}; type uppercase as observed on a live tenant."""
    return {"id": eid, "type": etype, "name": name}


def broker(n: int) -> dict[str, str]:
    return ent(
        f"PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E1{n}", "PROCESS_GROUP_INSTANCE", f"kafka-broker (kafka-{n})"
    )


def host(n: int) -> dict[str, str]:
    return ent(f"HOST-91C2D4E5F6A7081{n}", "HOST", f"aks-kafka-pool-000{n}")


BROKER_PG = ent("PROCESS_GROUP-3D7A1C0E9B2F4A65", "PROCESS_GROUP", "kafka-broker")
CONSUMER = ent(
    "PROCESS_GROUP_INSTANCE-C0A1B2C3D4E5F601",
    "PROCESS_GROUP_INSTANCE",
    "orders-consumer (orders-consumer-7d9f)",
)
CONSUMER_SVC = ent("SERVICE-7F3E2D1C0B9A8877", "SERVICE", "orders-consumer")
PRODUCER_SVC = ent("SERVICE-1A2B3C4D5E6F7788", "SERVICE", "checkout-producer")
PAYMENTS_SVC = ent("SERVICE-2B3C4D5E6F708899", "SERVICE", "payments-producer")


# ---------------------------------------------------------------------------------------------- log lines
def log(minute: int, level: str, msg: str, logger: str) -> str:
    """Broker log4j layout `[yyyy-MM-dd HH:mm:ss,SSS] LEVEL message (logger)` (confirmed by KAFKA-9604)."""
    t = BASE + timedelta(minutes=minute, seconds=7)
    return f"[{t:%Y-%m-%d %H:%M:%S},{(minute * 37) % 1000:03d}] {level} {msg} ({logger})"


@dataclass
class Metric:
    display: str  # Dynatrace display name (extension docs, Appendix A)
    key: str  # metric key (extension docs, Appendix A)
    unit: str
    before: float
    after: float
    entity: dict[str, str]
    root_cause_relevant: bool = False


@dataclass
class Event:
    display: str  # Dynatrace default title
    event_type: str  # Events v2 / alerting-profile name
    entity: dict[str, str]
    root_cause_relevant: bool = True


@dataclass
class Scenario:
    id: str
    label: str  # expected triage action
    signature: str  # why: the Kafka signal that decides the label
    title: str
    category: str  # Grail name
    severity: int | None  # Grail 1 = Critical ... 5 = Informational
    impact: str  # Grail title-case impact level
    affected: list[dict[str, str]]
    minute: int  # start offset from BASE
    root_cause: dict[str, str] | None = None  # usually absent on real problems
    events: list[Event] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=lambda: {"team": "streaming", "app": "kafka"})
    maintenance: bool = False
    duplicate: bool = False
    status: str = "ACTIVE"
    duration_min: int = 0  # for CLOSED
    provenance: list[str] = field(default_factory=list)
    channels: tuple[str, ...] = ("grail", "workflow", "classic", "problems_v2")
    restart_target: str | None = None  # registry target the label would act on

    # identifiers derived deterministically
    @property
    def start(self) -> datetime:
        return BASE + timedelta(minutes=self.minute)

    @property
    def start_ms(self) -> int:
        return int(self.start.timestamp() * 1000)

    @property
    def event_id(self) -> str:
        return f"-{4417092361180924518 + self.minute * 7919}_{self.start_ms}V2"

    @property
    def display_id(self) -> str:
        return f"P-2610121{self.minute:02d}"

    @property
    def end(self) -> datetime | None:
        return self.start + timedelta(minutes=self.duration_min) if self.status == "CLOSED" else None


INFRA = "Infrastructure"
SERVICES = "Services"
URP = "kafka.server.ReplicaManager.UnderReplicatedPartitions"
OFFLINE = "kafka.controller.KafkaController.OfflinePartitionsCount"
CONTROLLERS = "kafka.controller.KafkaController.ActiveControllerCount.Value"
LAG_MAX = "kafka.consumer.consumer-metrics.records-lag-max"

SCENARIOS: list[Scenario] = [
    Scenario(
        id="broker_down_single",
        label="restart_container",
        restart_target="kafka-broker-2",
        signature="one broker process down; URP > 0 but OfflinePartitions = 0 and one active controller",
        title="Process unavailable",
        category="AVAILABILITY",
        severity=2,
        impact=INFRA,
        minute=7,
        root_cause=broker(2),
        affected=[broker(2), host(2)],
        events=[Event("Process unavailable", "PGI_UNAVAILABLE", broker(2))],
        metrics=[
            Metric("Kafka Server - Under replicated partitions", URP, "Count", 0, 118, broker(0)),
            Metric("Kafka Controller - Offline partitions", OFFLINE, "Count", 0, 0, broker(0)),
            Metric("Kafka Controller - Active cluster controllers", CONTROLLERS, "Count", 1, 1, broker(0)),
        ],
        logs=[
            log(
                7,
                "INFO",
                "[Partition orders-3 broker=0] Shrinking ISR from 0,1,2 to 0,1. Leader: (highWatermark: 88123, "
                "endOffset: 88131). Out of sync replicas: (brokerId: 2, endOffset: 88123).",
                "kafka.cluster.Partition",
            ),
            log(
                7,
                "WARN",
                "[Producer clientId=checkout-producer-1] Connection to node 2 (kafka-2.kafka-headless.streaming.svc/"
                "10.42.3.17:9092) could not be established. Node may not be available.",
                "org.apache.kafka.clients.NetworkClient",
            ),
        ],
        provenance=[
            "ISR shrink: Kafka 3.7 Partition.scala (assembled)",
            "connection: Kafka 3.7 NetworkClient.java",
        ],
    ),
    Scenario(
        id="broker_oom",
        label="restart_container",
        restart_target="kafka-broker-1",
        signature="single broker container OOM-killed and restarting; cluster otherwise healthy (offline 0, 1 controller)",
        title="Out-of-memory kills",
        category="ERROR",
        severity=2,
        impact=INFRA,
        minute=12,
        affected=[broker(1), host(1)],
        events=[
            Event("Out-of-memory kills", "ERROR_EVENT", broker(1)),
            Event("Container restarts", "ERROR_EVENT", broker(1), root_cause_relevant=False),
        ],
        metrics=[
            Metric("Kafka Server - Under replicated partitions", URP, "Count", 0, 64, broker(0)),
            Metric("Kafka Controller - Offline partitions", OFFLINE, "Count", 0, 0, broker(0)),
        ],
        logs=[
            log(
                11,
                "ERROR",
                "[KafkaApi-1] Unexpected error handling request. java.lang.OutOfMemoryError: Java heap space",
                "kafka.server.KafkaApis",
            )
        ],
        provenance=[
            "K8s titles 'Out-of-memory kills'/'Container restarts': Dynatrace K8s alerting docs",
            "OutOfMemoryError line: paraphrased (not verified against source)",
        ],
    ),
    Scenario(
        id="consumer_stuck",
        label="restart_container",
        restart_target="orders-consumer",
        signature="one consumer instance hung: poll timeout + LeaveGroup + CommitFailedException, lag climbing",
        title="Kafka consumer lag high",
        category="CUSTOM_ALERT",
        severity=3,
        impact=SERVICES,
        minute=18,
        affected=[CONSUMER, CONSUMER_SVC],
        events=[Event("Kafka consumer lag high", "CUSTOM_ALERT", CONSUMER)],
        metrics=[
            Metric("Kafka Consumer - Consumer lag maximum", LAG_MAX, "Count", 140, 48_310, CONSUMER, True)
        ],
        logs=[
            log(
                16,
                "WARN",
                "[Consumer clientId=orders-consumer-7d9f, groupId=orders] consumer poll timeout has expired. This "
                "means the time between subsequent calls to poll() was longer than the configured max.poll.interval.ms, "
                "which typically implies that the poll loop is spending too much time processing messages.",
                "org.apache.kafka.clients.consumer.internals.ConsumerCoordinator",
            ),
            log(
                16,
                "INFO",
                "[Consumer clientId=orders-consumer-7d9f, groupId=orders] Member orders-consumer-7d9f-1a2b sending "
                "LeaveGroup request to coordinator kafka-1:9092 (id: 2147483646 rack: null) due to consumer poll timeout "
                "has expired.",
                "org.apache.kafka.clients.consumer.internals.ConsumerCoordinator",
            ),
            log(
                17,
                "ERROR",
                "[Consumer clientId=orders-consumer-7d9f, groupId=orders] Offset commit failed: "
                "org.apache.kafka.clients.consumer.CommitFailedException: Commit cannot be completed since the group has "
                "already rebalanced and assigned the partitions to another member.",
                "org.apache.kafka.clients.consumer.internals.ConsumerCoordinator",
            ),
        ],
        provenance=[
            "metric event title: INFERRED (user-defined)",
            "poll timeout / LeaveGroup / CommitFailed: Kafka 3.7 source",
        ],
    ),
    Scenario(
        id="rolling_restart_isr",
        label="suppress",
        signature="ISR shrink then 'ISR updated' within a minute during a maintenance window; URP back to 0",
        title="Multiple infrastructure problems",
        category="AVAILABILITY",
        severity=3,
        impact=INFRA,
        minute=24,
        affected=[broker(0), host(0)],
        maintenance=True,
        tags={"team": "streaming", "app": "kafka", "maintenance": "true"},
        events=[
            Event("Process unavailable", "PGI_UNAVAILABLE", broker(0)),
            Event("Planned maintenance", "MAINTENANCE_WINDOW", broker(0), root_cause_relevant=False),
        ],
        metrics=[Metric("Kafka Server - Under replicated partitions", URP, "Count", 36, 0, broker(1))],
        logs=[
            log(
                24,
                "INFO",
                "[Partition payments-1 broker=1] Shrinking ISR from 1,0,2 to 1,2. Leader: (highWatermark: 5512, "
                "endOffset: 5512). Out of sync replicas: (brokerId: 0, endOffset: 5498).",
                "kafka.cluster.Partition",
            ),
            log(
                25, "INFO", "[Partition payments-1 broker=1] ISR updated to 1,2,0", "kafka.cluster.Partition"
            ),
        ],
        provenance=[
            "'Multiple infrastructure problems': documented example title",
            "ISR lines: Kafka 3.7 Partition.scala",
        ],
    ),
    Scenario(
        id="rebalance_during_deploy",
        label="suppress",
        signature="consumer group rejoins during a deployment; lag flat; no errors",
        title="Unexpected low load",
        category="AVAILABILITY",
        severity=4,
        impact=SERVICES,
        minute=30,
        affected=[CONSUMER_SVC],
        events=[
            Event("Unexpected low load", "SERVICE_UNEXPECTED_LOW_LOAD", CONSUMER_SVC),
            Event(
                "Deployment orders-consumer 4.12.0", "CUSTOM_DEPLOYMENT", CONSUMER, root_cause_relevant=False
            ),
        ],
        metrics=[Metric("Kafka Consumer - Consumer lag maximum", LAG_MAX, "Count", 120, 160, CONSUMER)],
        logs=[
            log(
                30,
                "INFO",
                "[Consumer clientId=orders-consumer-5c2a, groupId=orders] (Re-)joining group",
                "org.apache.kafka.clients.consumer.internals.ConsumerCoordinator",
            ),
            log(
                30,
                "INFO",
                "[Consumer clientId=orders-consumer-5c2a, groupId=orders] Successfully joined group with "
                "generation Generation{generationId=482, memberId='orders-consumer-5c2a-77f1', protocol='range'}",
                "org.apache.kafka.clients.consumer.internals.ConsumerCoordinator",
            ),
        ],
        provenance=[
            "event type SERVICE_UNEXPECTED_LOW_LOAD: INFERRED",
            "rejoin lines: Kafka 3.7 AbstractCoordinator.java",
        ],
    ),
    Scenario(
        id="duplicate_problem",
        label="suppress",
        signature="Grail dt.davis.is_duplicate = true: merged into another problem",
        title="Process unavailable",
        category="AVAILABILITY",
        severity=2,
        impact=INFRA,
        minute=8,
        affected=[broker(2)],
        duplicate=True,
        channels=("grail", "workflow"),
        events=[Event("Process unavailable", "PGI_UNAVAILABLE", broker(2))],
        provenance=["dt.davis.is_duplicate: Davis semantic dictionary"],
    ),
    Scenario(
        id="urp_persistent",
        label="open_ticket",
        signature="under-replicated partitions persist with no broker down (capacity / ACL), no offline partitions",
        title="Kafka under-replicated partitions",
        category="CUSTOM_ALERT",
        severity=3,
        impact=INFRA,
        minute=36,
        affected=[broker(1)],
        events=[Event("Kafka under-replicated partitions", "CUSTOM_ALERT", broker(1))],
        metrics=[
            Metric("Kafka Server - Under replicated partitions", URP, "Count", 0, 14, broker(1), True),
            Metric(
                "Kafka Server - Under min ISR partitions",
                "kafka.server.ReplicaManager.UnderMinIsrPartitionCount",
                "Count",
                0,
                0,
                broker(1),
            ),
            Metric("Kafka Controller - Offline partitions", OFFLINE, "Count", 0, 0, broker(0)),
        ],
        logs=[
            log(
                35,
                "WARN",
                "[ReplicaFetcher replicaId=1, leaderId=2, fetcherId=0] Partition audit-events-9 marked as failed",
                "kafka.server.ReplicaFetcherThread",
            )
        ],
        provenance=["metric event title: INFERRED", "fetcher line: Kafka 3.7 source (prefix unverified)"],
    ),
    Scenario(
        id="lag_growth_gradual",
        label="open_ticket",
        signature="consumer lag grows slowly over hours, no errors, consumers healthy (capacity)",
        title="Kafka consumer lag high",
        category="CUSTOM_ALERT",
        severity=4,
        impact=SERVICES,
        minute=42,
        affected=[CONSUMER_SVC],
        events=[Event("Kafka consumer lag high", "CUSTOM_ALERT", CONSUMER)],
        metrics=[
            Metric("Kafka Consumer - Consumer lag maximum", LAG_MAX, "Count", 2_200, 9_480, CONSUMER),
            Metric(
                "Kafka Consumer - Records consumed rate",
                "kafka.consumer.consumer-metrics.records-consumed-rate",
                "PerSecond",
                1_950,
                1_910,
                CONSUMER,
            ),
        ],
        logs=[],
        provenance=["metric event title: INFERRED"],
    ),
    Scenario(
        id="low_disk_trending",
        label="open_ticket",
        signature="broker log dir disk trending above 75% (ticket), not full, no log-dir failure",
        title="Low disk space",
        category="RESOURCE_CONTENTION",
        severity=3,
        impact=INFRA,
        minute=48,
        affected=[host(0)],
        events=[Event("Low disk space", "OSI_LOW_DISK_SPACE", host(0))],
        metrics=[Metric("Disk used %", "builtin:host.disk.usedPct", "Percent", 71, 78.4, host(0), True)],
        logs=[],
        provenance=[
            "'Low disk space'/OSI_LOW_DISK_SPACE: documented",
            "builtin:host.disk.usedPct: INFERRED key",
        ],
    ),
    Scenario(
        id="ssl_single_client",
        label="open_ticket",
        signature="SSL handshake failures from ONE client IP (client certificate/config problem)",
        title="Connectivity problem",
        category="ERROR",
        severity=4,
        impact=INFRA,
        minute=54,
        affected=[broker(0)],
        events=[Event("Connectivity problem", "PROCESS_NA_HIGH_CONN_FAIL_RATE", broker(0))],
        logs=[
            log(
                54,
                "INFO",
                "[SocketServer listenerType=BROKER, nodeId=0] Failed authentication with /10.42.7.19 "
                "(SSL handshake failed)",
                "org.apache.kafka.common.network.Selector",
            )
        ]
        * 3,
        provenance=[
            "SSL line: vendor KBs (Yandex Cloud, IBM)",
            "PROCESS_NA_HIGH_CONN_FAIL_RATE category: INFERRED",
        ],
    ),
    Scenario(
        id="offline_partitions",
        label="escalate_human",
        signature="OfflinePartitionsCount > 0: partitions without a leader, data unavailable",
        title="Kafka offline partitions",
        category="CUSTOM_ALERT",
        severity=1,
        impact=INFRA,
        minute=60,
        affected=[broker(0), broker(1)],
        events=[Event("Kafka offline partitions", "CUSTOM_ALERT", broker(0))],
        metrics=[
            Metric("Kafka Controller - Offline partitions", OFFLINE, "Count", 0, 12, broker(0), True),
            Metric("Kafka Server - Under replicated partitions", URP, "Count", 0, 211, broker(1)),
        ],
        logs=[
            log(
                60,
                "ERROR",
                "[ReplicaFetcher replicaId=1, leaderId=0, fetcherId=0] Error for partition orders-5 at offset 993201",
                "kafka.server.ReplicaFetcherThread",
            )
        ],
        provenance=["metric event title: INFERRED", "fetcher error: Kafka 3.7 source"],
    ),
    Scenario(
        id="controller_lost_kraft",
        label="escalate_human",
        signature="KRaft quorum lost: no active controller (sum 0), current-leader -1",
        title="Multiple infrastructure problems",
        category="AVAILABILITY",
        severity=1,
        impact=INFRA,
        minute=66,
        affected=[broker(0), broker(1), broker(2)],
        events=[Event("Process unavailable", "PGI_UNAVAILABLE", broker(0))],
        metrics=[
            Metric(
                "Kafka Controller - Active cluster controllers", CONTROLLERS, "Count", 1, 0, broker(0), True
            ),
            Metric("Current leader", "kafka.raft.current-leader", "Count", 3000, -1, broker(1)),
        ],
        logs=[
            log(
                65,
                "WARN",
                "[QuorumController id=3000] Renouncing the leadership due to a metadata log event. We were the "
                "leader at epoch 41, but in the new epoch 42, the leader is (none). Reverting to last stable offset 1288331.",
                "org.apache.kafka.controller.QuorumController",
            )
        ],
        provenance=[
            "QuorumController lines: Kafka 3.7 source (two lines joined)",
            "kafka.raft.current-leader: extension docs",
        ],
    ),
    Scenario(
        id="log_dir_failed",
        label="escalate_human",
        restart_target="kafka-broker-1",
        signature="ADVERSARIAL: titled 'Process unavailable' but the log dir failed (disk full) - never restart first",
        title="Process unavailable",
        category="AVAILABILITY",
        severity=2,
        impact=INFRA,
        minute=72,
        root_cause=broker(1),
        affected=[broker(1), host(1)],
        events=[
            Event("Process unavailable", "PGI_UNAVAILABLE", broker(1)),
            Event("Low disk space", "OSI_LOW_DISK_SPACE", host(1)),
        ],
        metrics=[Metric("Disk used %", "builtin:host.disk.usedPct", "Percent", 91, 100, host(1), True)],
        logs=[
            log(
                71,
                "ERROR",
                "Error while writing to checkpoint file /var/lib/kafka/data/replication-offset-checkpoint: "
                "java.io.IOException: No space left on device",
                "kafka.server.LogDirFailureChannel",
            ),
            log(71, "INFO", "Stopping serving logs in dir /var/lib/kafka/data", "kafka.log.LogManager"),
            log(
                71,
                "ERROR",
                "Shutdown broker because all log dirs in /var/lib/kafka/data have failed",
                "kafka.log.LogManager",
            ),
        ],
        provenance=[
            "log-dir failure sequence: real log, KAFKA-9604",
            "never restart before freeing space: Conduktor, Netdata",
        ],
    ),
    Scenario(
        id="unclean_election",
        label="escalate_human",
        signature="unclean leader election: possible data loss (partition RECOVERING)",
        title="Kafka unclean leader election",
        category="CUSTOM_ALERT",
        severity=1,
        impact=INFRA,
        minute=78,
        affected=[broker(2)],
        events=[Event("Kafka unclean leader election", "CUSTOM_ALERT", broker(2))],
        metrics=[
            Metric(
                "Kafka Controller - Unclean leader elections",
                "kafka.controller.ControllerStats.UncleanLeaderElectionsPerSec.OneMinuteRate",
                "PerSecond",
                0,
                0.2,
                broker(2),
                True,
            )
        ],
        logs=[
            log(
                78,
                "INFO",
                "The topic partition payments-4 was marked as RECOVERING.",
                "kafka.cluster.Partition",
            )
        ],
        provenance=["normal value 0: Kafka 3.8 docs", "RECOVERING line: Kafka 3.7 source"],
    ),
    Scenario(
        id="ssl_all_clients",
        label="escalate_human",
        signature="SSL handshake failures from MANY clients right after a certificate rotation; produce failures rising",
        title="Failure rate increase",
        category="ERROR",
        severity=2,
        impact=SERVICES,
        minute=84,
        affected=[PRODUCER_SVC, PAYMENTS_SVC, broker(0)],
        events=[Event("Failure rate increase", "SERVICE_ERROR_RATE_INCREASED", PRODUCER_SVC)],
        metrics=[
            Metric(
                "Kafka Server - Failed produce requests",
                "kafka.server.BrokerTopicMetrics.FailedProduceRequestsPerSec.OneMinuteRate",
                "PerSecond",
                0,
                412,
                broker(0),
                True,
            )
        ],
        logs=[
            log(
                84,
                "INFO",
                f"[SocketServer listenerType=BROKER, nodeId=0] Failed authentication with /10.42.{a}.{b} "
                "(SSL handshake failed)",
                "org.apache.kafka.common.network.Selector",
            )
            for a, b in ((7, 19), (9, 4), (2, 88), (5, 140))
        ],
        provenance=["SSL line: vendor KBs", "SERVICE_ERROR_RATE_INCREASED: alerting-profile enum"],
    ),
    Scenario(
        id="gc_long_no_impact",
        label="notify_only",
        signature="long GC on one broker, no lag, no URP, no errors",
        title="Long garbage-collection time",
        category="RESOURCE_CONTENTION",
        severity=4,
        impact=INFRA,
        minute=90,
        affected=[broker(0)],
        events=[Event("Long garbage-collection time", "PROCESS_HIGH_GC_ACTIVITY", broker(0))],
        metrics=[
            Metric("Kafka Server - Under replicated partitions", URP, "Count", 0, 0, broker(0)),
            Metric("Kafka Consumer - Consumer lag maximum", LAG_MAX, "Count", 90, 110, CONSUMER),
        ],
        provenance=["PROCESS_HIGH_GC_ACTIVITY 'Long garbage-collection time': alerting-profile enum"],
    ),
    Scenario(
        id="node_disconnect_lone",
        label="notify_only",
        signature="a lone client disconnect with no metric impact",
        title="Connectivity problem",
        category="ERROR",
        severity=5,
        impact=INFRA,
        minute=96,
        affected=[PRODUCER_SVC],
        events=[Event("Connectivity problem", "PROCESS_NA_HIGH_CONN_FAIL_RATE", PRODUCER_SVC)],
        logs=[
            log(
                96,
                "WARN",
                "[Producer clientId=checkout-producer-1] Connection to node 1 (kafka-1.kafka-headless.streaming.svc/"
                "10.42.1.12:9092) could not be established. Node may not be available.",
                "org.apache.kafka.clients.NetworkClient",
            )
        ],
        provenance=["NetworkClient line: Kafka 3.7 source"],
    ),
    Scenario(
        id="log_alert_no_context",
        label="cannot_tell",
        signature="a single log-event alert, no metrics, no entity health context",
        title="Kafka error log",
        category="CUSTOM_ALERT",
        severity=None,
        impact=INFRA,
        minute=102,
        affected=[broker(2)],
        events=[Event("Kafka error log", "CUSTOM_ALERT", broker(2))],
        logs=[
            log(
                102,
                "ERROR",
                "[ReplicaFetcher replicaId=2, leaderId=1, fetcherId=0] Error for partition inventory-2 at "
                "offset 412",
                "kafka.server.ReplicaFetcherThread",
            )
        ],
        provenance=[
            "log event with {content} title: INFERRED",
            "event.severity null: documented as nullable",
        ],
    ),
    Scenario(
        id="broker_down_closed",
        label="resolve",
        status="CLOSED",
        duration_min=23,
        signature="the broker_down_single problem closing (resolve path, not a classifier label)",
        title="Process unavailable",
        category="AVAILABILITY",
        severity=2,
        impact=INFRA,
        minute=7,
        root_cause=broker(2),
        affected=[broker(2), host(2)],
        events=[Event("Process unavailable", "PGI_UNAVAILABLE", broker(2))],
        logs=[
            log(30, "INFO", "[Partition orders-3 broker=0] ISR updated to 0,1,2", "kafka.cluster.Partition")
        ],
        provenance=["CLOSED adds event.end and resolved_problem_duration: Davis semantic dictionary"],
    ),
]


# ---------------------------------------------------------------------------------------------- renderers
def iso_ns(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond:06d}000Z"


def ms(t: datetime | None) -> int:
    return int(t.timestamp() * 1000) if t else -1


def evidence_v2(s: Scenario) -> list[dict[str, Any]]:
    def stub(e: dict[str, str]) -> dict[str, Any]:
        return {"entityId": {"id": e["id"], "type": e["type"]}, "name": e["name"]}

    out: list[dict[str, Any]] = []
    for i, e in enumerate(s.events):
        out.append(
            {
                "evidenceType": "MAINTENANCE_WINDOW" if e.event_type == "MAINTENANCE_WINDOW" else "EVENT",
                "displayName": e.display,
                "entity": stub(e.entity),
                "groupingEntity": stub(BROKER_PG)
                if e.entity["type"] == "PROCESS_GROUP_INSTANCE" and "kafka" in e.entity["name"]
                else None,
                "rootCauseRelevant": e.root_cause_relevant,
                "startTime": s.start_ms,
                "endTime": ms(s.end),
                "eventId": f"{8302664511948770210 + i}_{s.start_ms}",
                "eventType": e.event_type,
            }
        )
    for m in s.metrics:
        out.append(
            {
                "evidenceType": "METRIC",
                "displayName": m.display,
                "entity": stub(m.entity),
                "groupingEntity": None,
                "rootCauseRelevant": m.root_cause_relevant,
                "startTime": s.start_ms + 60_000,
                "metricId": m.key,
                "unit": m.unit,
                "valueBeforeChangePoint": m.before,
                "valueAfterChangePoint": m.after,
                "endTime": ms(s.end) if s.end else None,
            }
        )
    for item in out:
        if item.get("groupingEntity") is None:
            item.pop("groupingEntity")
    return out


def canonical_evidence(s: Scenario) -> list[dict[str, Any]]:
    """What the Grail context source returns: the same evidence in canonical (snake_case) form."""
    out: list[dict[str, Any]] = []
    for item in evidence_v2(s):
        e = item["entity"]
        out.append(
            {
                k: v
                for k, v in {
                    "evidence_type": item["evidenceType"],
                    "display_name": item["displayName"],
                    "entity": {
                        "id": e["entityId"]["id"],
                        "type": e["entityId"]["type"].lower(),
                        "name": e.get("name"),
                    },
                    "root_cause_relevant": item["rootCauseRelevant"],
                    "event_type": item.get("eventType"),
                    "event_id": item.get("eventId"),
                    "metric_id": item.get("metricId"),
                    "unit": item.get("unit"),
                    "value_before": item.get("valueBeforeChangePoint"),
                    "value_after": item.get("valueAfterChangePoint"),
                }.items()
                if v is not None
            }
        )
    return out


def grail_record(s: Scenario) -> dict[str, Any]:
    """Raw dt.davis.problems record, i.e. the Workflow problem trigger's event()."""
    affected = s.affected
    rec: dict[str, Any] = {
        "timestamp": iso_ns(s.start + timedelta(seconds=31)),
        "event.kind": "DAVIS_PROBLEM",
        "event.id": s.event_id,
        "display_id": s.display_id,
        "event.name": s.title,
        "event.description": f"{s.title} on {affected[0]['name']}",
        "event.category": s.category,
        "event.status": s.status,
        "event.status_transition": "RESOLVED" if s.status == "CLOSED" else "CREATED",
        "event.start": iso_ns(s.start),
        "dt.analysis.ready": True,
        "dt.davis.event_ids": [f"{8302664511948770210 + i}_{s.start_ms}" for i in range(len(s.events) or 1)],
        "dt.davis.impact_level": s.impact,
        "dt.davis.is_duplicate": s.duplicate,
        "dt.duration_marker": "5 min",
        "maintenance.is_under_maintenance": s.maintenance,
        "smartscape.affected_entities": affected,
        "affected_entity_ids": [e["id"] for e in affected],
        "affected_entity_types": [f"dt.entity.{e['type'].lower()}" for e in affected],
        "entity_tags": [f"{k}:{v}" for k, v in s.tags.items() if k != "maintenance"],
    }
    if s.severity is not None:
        rec["event.severity"] = s.severity
    if s.root_cause:
        rec["root_cause.smartscape_entity"] = s.root_cause
        rec["root_cause_entity_id"] = s.root_cause["id"]
    if s.end:
        rec["event.end"] = iso_ns(s.end)
        rec["resolved_problem_duration"] = s.duration_min * 60 * 1_000_000_000
    if s.duplicate:
        rec["duplicate_problem_ids"] = ["-4417092361180924518_1791814020000V2"]
    return rec


def workflow_body(rec: dict[str, Any], s: Scenario) -> dict[str, Any]:
    """Our Workflow HTTP Request template (spec 12.6.2 + 1.1 additions), rendered from event() like Jinja would.

    `context_fixture` is a TEST HOOK so the local fake context source finds this scenario's logs/evidence even when
    Postman randomizes display_id; a real Dynatrace payload never carries it.
    """
    return {
        "source": "dynatrace-workflow",
        "event_id": rec["event.id"],
        "display_id": rec["display_id"],
        "status": rec["event.status"],
        "name": rec["event.name"],
        "category": rec["event.category"],
        "severity": rec.get("event.severity"),
        "start": rec["event.start"],
        "end": rec.get("event.end"),
        "affected": rec["smartscape.affected_entities"],
        "root_cause": rec.get("root_cause.smartscape_entity"),
        "maintenance": rec["maintenance.is_under_maintenance"],
        "impact_level": rec["dt.davis.impact_level"],
        "is_duplicate": rec["dt.davis.is_duplicate"],
        "tags": {
            **{t.split(":", 1)[0]: t.split(":", 1)[1] for t in rec["entity_tags"]},
            "context_fixture": f"kafka_{s.id}",
        },
        "problem_url": f"{TENANT}/ui/apps/dynatrace.davis.problems/problem/{rec['display_id']}",
    }


def grail_summarize(rec: dict[str, Any]) -> dict[str, Any]:
    """The poller's problems_changed.dql output row (aliases from the summarize)."""
    return {
        "event.id": rec["event.id"],
        "status": rec["event.status"],
        "dup": rec["dt.davis.is_duplicate"],
        "name": rec["event.name"],
        "display_id": rec["display_id"],
        "category": rec["event.category"],
        "severity": rec.get("event.severity"),
        "start": rec["event.start"],
        "end": rec.get("event.end"),
        "affected": rec["smartscape.affected_entities"],
        "root_cause": rec.get("root_cause.smartscape_entity"),
        "maintenance": rec["maintenance.is_under_maintenance"],
        "impact_level": rec["dt.davis.impact_level"],
        "last_update": rec["timestamp"],
    }


def v2_problem(s: Scenario) -> dict[str, Any]:
    def stub(e: dict[str, str]) -> dict[str, Any]:
        return {"entityId": {"id": e["id"], "type": e["type"]}, "name": e["name"]}

    impact = {"Infrastructure": "INFRASTRUCTURE", "Services": "SERVICES", "Application": "APPLICATION"}[
        s.impact
    ]
    category = "PERFORMANCE" if s.category == "SLOWDOWN" else s.category
    return {
        "problemId": s.event_id,
        "displayId": s.display_id,
        "title": s.title,
        "status": "CLOSED" if s.status == "CLOSED" else "OPEN",
        "severityLevel": category,
        "impactLevel": impact,
        "startTime": s.start_ms,
        "endTime": ms(s.end),
        "affectedEntities": [stub(e) for e in s.affected],
        "impactedEntities": [stub(e) for e in s.affected],
        "rootCauseEntity": stub(s.root_cause) if s.root_cause else None,
        "managementZones": [{"id": "4567890123456789012", "name": "Kafka Prod"}],
        "entityTags": [
            {"context": "CONTEXTLESS", "key": k, "value": v, "stringRepresentation": f"{k}:{v}"}
            for k, v in s.tags.items()
        ],
        "problemFilters": [{"id": "c1b2a3d4-0000-4000-8000-000000000001", "name": "Kafka on-call"}],
        "evidenceDetails": {"totalCount": len(s.events) + len(s.metrics), "details": evidence_v2(s)},
        "impactAnalysis": {"impacts": []},
        "linkedProblemInfo": {"displayId": "P-261012107", "problemId": "-4417092361180924518_1791814020000V2"}
        if s.duplicate
        else None,
        "k8s.cluster.name": [CLUSTER["k8s.cluster.name"]],
        "k8s.cluster.uid": [],
        "k8s.namespace.name": [CLUSTER["k8s.namespace.name"]],
    }


def classic_body(s: Scenario) -> dict[str, Any]:
    """Our classic custom-integration template (spec 12.6.3) rendered: {State} OPEN/RESOLVED, {ProblemSeverity}
    is a category with PERFORMANCE, {ProblemImpact} singular SERVICE, details = {ProblemDetailsJSONv2}."""
    v2 = v2_problem(s)
    impact = {"SERVICES": "SERVICE"}.get(v2["impactLevel"], v2["impactLevel"])
    return {
        "pid": s.event_id,
        "problem_id": s.display_id,
        "state": "RESOLVED" if s.status == "CLOSED" else "OPEN",
        "title": s.title,
        "severity": v2["severityLevel"],
        "impact": impact,
        "url": f"https://abc12345.live.dynatrace.com/#problems/problemdetails;pid={s.event_id}",
        "tags": ", ".join(f"{k}:{v}" for k, v in s.tags.items()),
        "impacted_entities": [{"type": e["type"], "name": e["name"], "entity": e["id"]} for e in s.affected],
        "details": v2,
    }


TEST_NOTIFICATION = {  # our classic template rendered by "Send test notification" (documented test values)
    "pid": "99999",
    "problem_id": "999",
    "state": "OPEN",
    "title": "Dynatrace problem notification test run",
    "severity": "AVAILABILITY",
    "impact": "INFRASTRUCTURE",
    "url": "https://example.com",
    "tags": "testtag1, testtag2",
    "impacted_entities": [
        {"type": "HOST", "name": "MyHost1", "entity": "HOST-XXXXXXXXXXXXX"},
        {"type": "SERVICE", "name": "MyService1", "entity": "SERVICE-XXXXXXXXXXXXX"},
    ],
    "details": {"ID": "99999"},
}


def log_records(s: Scenario) -> list[dict[str, Any]]:
    """The same lines as Grail log records (fields DOCUMENTED, values INFERRED)."""
    entity = s.root_cause or s.affected[0]
    pod = entity["name"].split("(")[-1].rstrip(")") if "(" in entity["name"] else entity["name"]
    out = []
    for line in s.logs:
        level = line.split("] ", 1)[1].split(" ", 1)[0]
        out.append(
            {
                "timestamp": line[1:24].replace(" ", "T").replace(",", ".") + "000000Z",
                "event.type": "LOG",
                "content": line,
                "loglevel": level,
                "status": {"ERROR": "ERROR", "WARN": "WARN", "INFO": "INFO"}.get(level, "NONE"),
                "log.source": "Container Output",
                "log.iostream": "stdout",
                "process.technology": ["Java", "Apache Kafka"],
                "dt.process.name": "kafka.Kafka",
                "container.name": "kafka",
                "k8s.pod.name": pod,
                "k8s.container.name": "kafka",
                **CLUSTER,
                "dt.entity.process_group_instance": entity["id"],
                "dt.source_entity": [entity["id"]],
            }
        )
    return out


def write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    dt = ROOT / "dynatrace"
    index = []
    for s in SCENARIOS:
        name = f"kafka_{s.id}"
        rec = grail_record(s)
        if "grail" in s.channels:
            write(dt / "grail_records" / f"{name}.json", rec)
            write(dt / "grail" / f"{name}.json", [grail_summarize(rec)])
        if "workflow" in s.channels:
            write(dt / "workflow" / f"{name}.json", workflow_body(rec, s))
        if "classic" in s.channels:
            write(dt / "classic" / f"{name}.json", classic_body(s))
        if "problems_v2" in s.channels:
            write(
                dt / "problems_v2" / f"{name}.json",
                {"totalCount": 1, "pageSize": 50, "problems": [v2_problem(s)]},
            )
        write(
            ROOT / "context" / f"{name}.json",
            {
                "logs": s.logs,
                "log_records": log_records(s),
                "evidence": canonical_evidence(s),
                "provenance": s.provenance,
            },
        )
        index.append(
            {
                "id": s.id,
                "fixture": name,
                "display_id": s.display_id,
                "event_id": s.event_id,
                "label": s.label,
                "signature": s.signature,
                "title": s.title,
                "category": s.category,
                "severity": s.severity,
                "has_root_cause": s.root_cause is not None,
                "restart_target": s.restart_target,
                "channels": list(s.channels),
                "provenance": s.provenance,
            }
        )
    write(dt / "classic" / "kafka_test_notification.json", TEST_NOTIFICATION)
    index.append(
        {
            "id": "test_notification",
            "fixture": "kafka_test_notification",
            "label": "suppress",
            "signature": "Dynatrace 'Send test notification' (PID 99999 / ProblemID 999)",
            "channels": ["classic"],
            "provenance": ["documented test notification values"],
        }
    )
    write(ROOT / "kafka" / "scenarios.json", index)
    print(f"{len(index)} Kafka scenarios written")


if __name__ == "__main__":
    main()
