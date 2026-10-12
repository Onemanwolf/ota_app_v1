"""Generate the SYNTHETIC labelled eval set evals/datasets/triage_v1.jsonl (spec 12.4.5).

There is no problem history yet, so cases come from hand-written scenario families with deterministic variation.
The labels are the author's judgement and MUST be reviewed by a person before thresholds are set from them.
Run: python3 evals/generate_dataset.py   (stdlib only, seed fixed, output is reproducible)
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent / "datasets" / "triage_v1.jsonl"
RNG = random.Random(20261012)  # noqa: S311 (reproducible test data, not security)

APPS = ["orders", "payments", "checkout", "inventory", "billing", "search", "catalog", "shipping"]
ROLES = ["api", "worker", "web", "consumer", "scheduler"]


def ident(prefix: str) -> str:
    return f"{prefix}-{RNG.getrandbits(64):016X}"


@dataclass
class Case:
    title: str
    category: str
    severity: int
    action: str
    sev_label: str
    logs: list[str]
    has_target: bool
    root: str = "container"  # container | service | host | process | none
    extra_affected: int = 0
    tags: dict[str, str] = field(default_factory=dict)
    name: str = "unknown"  # root-cause entity name


def ts(i: int) -> str:
    return f"2026-10-12T10:{i % 60:02d}:{(i * 7) % 60:02d}Z"


def restart_cases() -> list[Case]:
    lines = [
        ["ERROR container exited with code 137 (OOMKilled)", "WARN health check failed: connection refused"],
        [
            "ERROR liveness probe failed: HTTP 503 on /healthz (3/3)",
            "ERROR process exited with signal SIGKILL",
        ],
        [
            "ERROR worker thread deadlock detected, no progress for 300s",
            "WARN request queue full, rejecting requests",
        ],
        ["ERROR java.lang.OutOfMemoryError: Java heap space", "ERROR container restarting (back-off 5m0s)"],
        ["ERROR panic: runtime error: invalid memory address", "WARN container exited unexpectedly (exit 2)"],
        ["ERROR event loop blocked for 45000 ms", "WARN /healthz timed out after 10s"],
    ]
    out = []
    for i in range(24):
        app, role = RNG.choice(APPS), RNG.choice(ROLES)
        out.append(
            Case(
                f"Container {app}-{role} unavailable",
                RNG.choice(["AVAILABILITY", "ERROR"]),
                RNG.choice([3, 4]),
                "restart_container",
                RNG.choice(["degraded", "warning"]),
                lines[i % len(lines)],
                has_target=True,
                name=f"{app}-{role}",
            )
        )
    return out


def escalate_cases() -> list[Case]:
    scenarios = [
        (
            "Database {app}-db unavailable, multiple services failing",
            "AVAILABILITY",
            5,
            "service",
            [
                "ERROR could not connect to server: Connection refused (5432)",
                "ERROR {app}-api: 500 on 64% of requests",
            ],
        ),
        (
            "Disk full on host vm-{app}-01",
            "RESOURCE",
            5,
            "host",
            ["ERROR No space left on device", "ERROR write failed for /var/lib/docker"],
        ),
        (
            "Failure rate increase across {app}, payments and checkout",
            "ERROR",
            5,
            "service",
            ["ERROR upstream TLS handshake failed", "ERROR circuit breaker open for payments"],
        ),
        (
            "Data inconsistency detected in {app} ledger",
            "CUSTOM_ALERT",
            4,
            "service",
            ["ERROR checksum mismatch for batch 2231", "WARN duplicate primary keys rejected"],
        ),
        (
            "Network partition between availability zones",
            "AVAILABILITY",
            5,
            "host",
            ["ERROR raft: leader lost quorum", "ERROR connection timed out to 10.2.0.0/16"],
        ),
        (
            "Container {app}-api restarted 14 times in 1 hour",
            "AVAILABILITY",
            4,
            "container",
            ["WARN restart count 14", "ERROR crash on start: missing secret DB_PASSWORD"],
        ),
    ]
    out = []
    for i in range(22):
        title, cat, sev, root, logs = scenarios[i % len(scenarios)]
        app = RNG.choice(APPS)
        out.append(
            Case(
                title.format(app=app),
                cat,
                sev,
                "escalate_human",
                "outage",
                [line.format(app=app) for line in logs],
                has_target=root == "container" and i % 2 == 0,
                root=root,
                extra_affected=RNG.randint(2, 6),
                name={"service": f"{app}-db", "host": f"vm-{app}-01", "container": f"{app}-api"}[root],
            )
        )
    return out


def suppress_cases() -> list[Case]:
    scenarios = [
        (
            "Container {app}-{role} unavailable during patching",
            {"maintenance": "true"},
            ["INFO maintenance window 10:00-11:00 started", "WARN container stopped by operator"],
        ),
        (
            "Synthetic monitor {app}-login failed in load-test environment",
            {"env": "loadtest"},
            ["INFO load test run 42 in progress", "WARN synthetic browser timed out"],
        ),
        (
            "Response time degradation on {app}-{role} (test environment)",
            {"env": "test"},
            ["INFO chaos experiment latency-injection active"],
        ),
        (
            "Known flapping monitor {app}-cron-check",
            {"known_flapping": "true"},
            ["WARN check failed, will retry", "INFO check passed"],
        ),
    ]
    out = []
    for i in range(16):
        title, tags, logs = scenarios[i % len(scenarios)]
        app, role = RNG.choice(APPS), RNG.choice(ROLES)
        out.append(
            Case(
                title.format(app=app, role=role),
                RNG.choice(["AVAILABILITY", "SLOWDOWN"]),
                2,
                "suppress",
                "noise",
                logs,
                has_target=i % 3 == 0,
                tags=dict(tags),
                name=f"{app}-{role}",
            )
        )
    return out


def notify_cases() -> list[Case]:
    scenarios = [
        (
            "CPU saturation on host vm-{app}-02 (5 minutes)",
            "RESOURCE",
            2,
            "host",
            ["WARN cpu usage 96% for 300s", "INFO cpu usage back to 41%"],
        ),
        (
            "Response time degradation on {app}-{role}",
            "SLOWDOWN",
            2,
            "service",
            ["WARN p95 latency 840 ms (baseline 300 ms)"],
        ),
        (
            "Failure rate increase on {app}-{role} (2%)",
            "ERROR",
            2,
            "service",
            ["WARN 2.1% of requests returned 5xx", "INFO retries succeeded"],
        ),
        (
            "Single synthetic check failed for {app}",
            "AVAILABILITY",
            2,
            "none",
            ["WARN synthetic check failed in 1 of 5 locations"],
        ),
    ]
    out = []
    for i in range(24):
        title, cat, sev, root, logs = scenarios[i % len(scenarios)]
        app, role = RNG.choice(APPS), RNG.choice(ROLES)
        out.append(
            Case(
                title.format(app=app, role=role),
                cat,
                sev,
                "notify_only",
                "warning",
                logs,
                has_target=False,
                root=root,
                name=f"vm-{app}-02" if root == "host" else f"{app}-{role}",
            )
        )
    return out


def ticket_cases() -> list[Case]:
    scenarios = [
        (
            "Memory usage of {app}-{role} growing steadily",
            "RESOURCE",
            ["WARN heap usage 71% (+4% per day for 9 days)", "INFO GC pause 120 ms"],
        ),
        (
            "TLS certificate for {app}.example.com expires in 14 days",
            "CUSTOM_ALERT",
            ["WARN certificate notAfter=2026-10-26"],
        ),
        (
            "Disk usage on vm-{app}-03 at 81% and rising",
            "RESOURCE",
            ["WARN /data 81% used, growth 2% per day"],
        ),
        (
            "Slow degradation of {app}-{role} batch duration",
            "SLOWDOWN",
            ["WARN nightly batch took 3h12m (baseline 2h)", "INFO completed successfully"],
        ),
    ]
    out = []
    for i in range(22):
        title, cat, logs = scenarios[i % len(scenarios)]
        app, role = RNG.choice(APPS), RNG.choice(ROLES)
        out.append(
            Case(
                title.format(app=app, role=role),
                cat,
                2,
                "open_ticket",
                "warning",
                logs,
                has_target=False,
                root="service" if i % 2 else "host",
                name=f"{app}-{role}" if i % 2 else f"vm-{app}-03",
            )
        )
    return out


def ambiguous_cases() -> list[Case]:
    out = []
    for i in range(12):
        app = RNG.choice(APPS)
        if i % 2 == 0:
            out.append(
                Case(
                    f"Custom alert from {app} integration",
                    "CUSTOM_ALERT",
                    3,
                    "cannot_tell",
                    "warning",
                    [],
                    has_target=False,
                    root="none",
                )
            )
        else:
            out.append(
                Case(
                    f"Container {app}-api unavailable and error rate rising on its database",
                    "AVAILABILITY",
                    4,
                    "escalate_human",
                    "degraded",
                    ["ERROR container exited with code 1", "WARN db connections at 98% of pool"],
                    has_target=True,
                    root="container",
                    extra_affected=1,
                    name=f"{app}-api",
                )
            )
    return out


def to_record(i: int, c: Case) -> dict[str, Any]:
    entity_type = {"container": "container", "service": "service", "host": "host", "process": "process_group"}
    root = None
    affected = []
    if c.root != "none":
        prefix = {"container": "CONTAINER", "service": "SERVICE", "host": "HOST", "process": "PROCESS_GROUP"}[
            c.root
        ]
        root = {"id": ident(prefix), "type": entity_type[c.root], "name": c.name}
        affected.append(root)
    for _ in range(c.extra_affected):
        affected.append({"id": ident("SERVICE"), "type": "service", "name": f"{RNG.choice(APPS)}-svc"})
    if c.root == "container":
        affected.append({"id": ident("HOST"), "type": "host", "name": f"vm-{RNG.choice(APPS)}-01"})
    event = {
        "schema_version": "1.0",
        "source": "simulator",
        "problem_id": f"-{RNG.getrandbits(62)}_1760263200000V2",
        "display_id": f"P-EV{i:04d}",
        "status": "ACTIVE",
        "title": c.title,
        "category": c.category,
        "severity": c.severity,
        "started_at": ts(i),
        "ended_at": None,
        "affected_entities": affected,
        "root_cause_entity": root,
        "tags": c.tags,
        "observed_at": ts(i + 1),
        "problem_url": None,
    }
    return {
        "id": f"c-{i:03d}",
        "event": event,
        "logs": [f"{ts(i)} {line}" for line in c.logs],
        "has_target": c.has_target,
        "label": {"action": c.action, "severity": c.sev_label},
    }


def main() -> None:
    cases = (
        restart_cases()
        + escalate_cases()
        + suppress_cases()
        + notify_cases()
        + ticket_cases()
        + ambiguous_cases()
    )
    RNG.shuffle(cases)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        for i, case in enumerate(cases, start=1):
            fh.write(json.dumps(to_record(i, case)) + "\n")
    counts: dict[str, int] = {}
    for c in cases:
        counts[c.action] = counts.get(c.action, 0) + 1
    print(f"{OUT}: {len(cases)} cases {dict(sorted(counts.items()))}")


if __name__ == "__main__":
    main()
