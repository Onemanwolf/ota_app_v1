"""Deterministic Kafka incident signatures (documented metrics, Kafka log identifiers, Davis event types).

Each signature is a structural check on facts Dynatrace and Kafka publish: Apache Kafka extension metric
keys with Kafka 3.8 documented normal values, Kafka 3.7 log strings (matched together with their logger
class), and Davis event types. A signature returns an action with its id and the facts that fired it, so
every decision is auditable. No match -> None, and the decision model handles the long tail.

Source: reports/Dynatrace Kafka triage data models.md (label table, Appendix A and B). Order matters:
data-safety checks run first, so a disk-full broker titled "Process unavailable" is escalated before any
restart check.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from opstriage_contracts.problems import Evidence
from opstriage_contracts.triage import Action
from opstriage_triage.domain.context import ProblemContext

URP = "kafka.server.ReplicaManager.UnderReplicatedPartitions"
OFFLINE = "kafka.controller.KafkaController.OfflinePartitionsCount"
CONTROLLERS = "kafka.controller.KafkaController.ActiveControllerCount"
RAFT_LEADER = "kafka.raft.current-leader"
UNCLEAN = "kafka.controller.ControllerStats.UncleanLeaderElectionsPerSec"
LAG = "kafka.consumer.consumer-metrics.records-lag"
FAILED_PRODUCE = "kafka.server.BrokerTopicMetrics.FailedProduceRequestsPerSec"
DISK_USED = "builtin:host.disk.usedPct"

BROKER_DOWN_EVENTS = {"PGI_UNAVAILABLE", "PROCESS_UNAVAILABLE", "PROCESS_CRASHED"}
OOM_TITLES = {"Out-of-memory kills", "Container restarts", "Backoff event"}
_IP = re.compile(r"Failed authentication with /([0-9a-fA-F.:]+) \(SSL handshake failed\)")


@dataclass(frozen=True, slots=True)
class SignatureMatch:
    signature: str
    action: Action
    facts: tuple[str, ...]


class Facts:
    """Read-only view over the problem's evidence and log lines."""

    def __init__(self, ctx: ProblemContext) -> None:
        self.ctx = ctx
        self.evidence: Sequence[Evidence] = ctx.all_evidence()
        self.logs: Sequence[str] = ctx.logs
        self.event = ctx.event

    def metric(self, key: str) -> Evidence | None:
        """First METRIC evidence whose id starts with the key (extension keys add .Value / .OneMinuteRate)."""
        return next((e for e in self.evidence if e.metric_id and e.metric_id.startswith(key)), None)

    def after(self, key: str) -> float | None:
        m = self.metric(key)
        return m.value_after if m else None

    def event_types(self) -> set[str]:
        return {e.event_type for e in self.evidence if e.event_type}

    def log(self, logger: str, *fragments: str) -> str | None:
        """A log line from `logger` (the class in parentheses at the end) containing all fragments."""
        return next(
            (
                line
                for line in self.logs
                if line.rstrip().endswith(f"({logger})") and all(f in line for f in fragments)
            ),
            None,
        )

    def ssl_failing_ips(self) -> set[str]:
        return {m.group(1) for line in self.logs if (m := _IP.search(line))}

    def single_instance(self) -> bool:
        pgis = {e.id for e in self.event.affected_entities if e.type == "process_group_instance"}
        return len(pgis) == 1


Rule = Callable[[Facts], SignatureMatch | None]


def _escalate_data_at_risk(f: Facts) -> SignatureMatch | None:
    if (v := f.after(OFFLINE)) is not None and v > 0:
        return SignatureMatch(
            "kafka.offline-partitions", Action.ESCALATE_HUMAN, (f"OfflinePartitionsCount={v:g}",)
        )
    if (v := f.after(CONTROLLERS)) is not None and v < 1:
        return SignatureMatch(
            "kafka.no-active-controller", Action.ESCALATE_HUMAN, (f"ActiveControllerCount={v:g}",)
        )
    if (v := f.after(RAFT_LEADER)) is not None and v < 0:
        return SignatureMatch(
            "kafka.kraft-quorum-lost", Action.ESCALATE_HUMAN, ("kafka.raft.current-leader=-1",)
        )
    if ((v := f.after(UNCLEAN)) is not None and v > 0) or f.log(
        "kafka.cluster.Partition", "was marked as RECOVERING"
    ):
        return SignatureMatch("kafka.unclean-election", Action.ESCALATE_HUMAN, ("unclean leader election",))
    line = (
        f.log("kafka.log.LogManager", "log dirs in", "have failed")
        or f.log("kafka.log.LogManager", "Stopping serving logs in dir")
        or f.log("kafka.server.LogDirFailureChannel", "No space left on device")
    )
    if line or ((v := f.after(DISK_USED)) is not None and v >= 95):
        return SignatureMatch(
            "kafka.log-dir-failed",
            Action.ESCALATE_HUMAN,
            ("log dir failed / disk full: free space before any restart",),
        )
    ips = f.ssl_failing_ips()
    if len(ips) >= 3:
        return SignatureMatch(
            "kafka.ssl-many-clients", Action.ESCALATE_HUMAN, (f"SSL handshake failures from {len(ips)} IPs",)
        )
    return None


def _restart_single_instance(f: Facts) -> SignatureMatch | None:
    healthy_cluster = (f.after(OFFLINE) or 0) == 0 and (f.after(CONTROLLERS) in (None, 1))
    down = f.event_types() & BROKER_DOWN_EVENTS or f.event.title in OOM_TITLES
    if down and f.single_instance() and healthy_cluster:
        return SignatureMatch(
            "kafka.single-broker-down",
            Action.RESTART_CONTAINER,
            ("one broker down", "OfflinePartitionsCount=0", "one active controller"),
        )
    consumer = "org.apache.kafka.clients.consumer.internals.ConsumerCoordinator"
    stuck = f.log(consumer, "consumer poll timeout has expired") and (
        f.log(consumer, "CommitFailedException") or f.log(consumer, "sending LeaveGroup request")
    )
    lag = f.metric(LAG)
    if stuck and f.single_instance() and lag and (lag.value_after or 0) > 10 * max(lag.value_before or 1, 1):
        return SignatureMatch(
            "kafka.consumer-hung",
            Action.RESTART_CONTAINER,
            ("poll timeout + leave group/commit failed", "lag x10"),
        )
    return None


def _ticket_slow_burn(f: Facts) -> SignatureMatch | None:
    if (v := f.after(URP)) is not None and v > 0 and not (f.event_types() & BROKER_DOWN_EVENTS):
        return SignatureMatch(
            "kafka.urp-persistent", Action.OPEN_TICKET, (f"UnderReplicatedPartitions={v:g}, no broker down",)
        )
    if (v := f.after(DISK_USED)) is not None and v >= 75:
        return SignatureMatch("kafka.disk-trending", Action.OPEN_TICKET, (f"disk used {v:g}%",))
    if len(f.ssl_failing_ips()) == 1:
        return SignatureMatch(
            "kafka.ssl-one-client", Action.OPEN_TICKET, ("SSL handshake failures from one client",)
        )
    lag = f.metric(LAG)
    errors = any(" ERROR " in line for line in f.logs)
    if lag and not errors and (lag.value_after or 0) > 3 * max(lag.value_before or 1, 1):
        return SignatureMatch("kafka.lag-growing", Action.OPEN_TICKET, ("consumer lag growing, no errors",))
    return None


def _suppress_expected(f: Facts) -> SignatureMatch | None:
    consumer = "org.apache.kafka.clients.consumer.internals.ConsumerCoordinator"
    lag = f.metric(LAG)
    rejoin = f.log(consumer, "(Re-)joining group") or f.log(consumer, "Successfully joined group")
    if (
        rejoin
        and "CUSTOM_DEPLOYMENT" in f.event_types()
        and (not lag or (lag.value_after or 0) < 3 * max(lag.value_before or 1, 1))
    ):
        return SignatureMatch(
            "kafka.rebalance-during-deploy", Action.SUPPRESS, ("rejoin during deployment, lag flat",)
        )
    producer = "org.apache.kafka.clients.producer.internals.Sender"
    if f.log(producer, "NotLeaderOrFollowerException") and (f.after(FAILED_PRODUCE) or 0) == 0:
        return SignatureMatch(
            "kafka.transient-not-leader", Action.SUPPRESS, ("NOT_LEADER_OR_FOLLOWER, no failed produces",)
        )
    return None


def _notify_minor(f: Facts) -> SignatureMatch | None:
    if "PROCESS_HIGH_GC_ACTIVITY" in f.event_types() and (f.after(URP) or 0) == 0:
        return SignatureMatch("kafka.gc-no-impact", Action.NOTIFY_ONLY, ("long GC, no under-replication",))
    net = "org.apache.kafka.clients.NetworkClient"
    if f.log(net, "could not be established") and len(f.logs) == 1 and not f.metric(""):
        return SignatureMatch(
            "kafka.lone-disconnect", Action.NOTIFY_ONLY, ("single client disconnect, no metric impact",)
        )
    return None


RULES: tuple[Rule, ...] = (
    _escalate_data_at_risk,
    _suppress_expected,
    _restart_single_instance,
    _ticket_slow_burn,
    _notify_minor,
)


def match(ctx: ProblemContext) -> SignatureMatch | None:
    facts = Facts(ctx)
    for rule in RULES:
        result = rule(facts)
        if result is not None:
            return result
    return None
