# Dynatrace Grail record schemas: Davis problems, Davis events, logs

Research date: 2026-10-12. Target platform: Dynatrace SaaS 3rd gen (Grail / Smartscape), docs version current as of Oct 2026.

Labels used throughout:
- **DOCUMENTED** = field name/type/value appears verbatim on the cited page.
- **OBSERVED (3rd-party)** = seen in a live-tenant sample published by a Dynatrace practitioner notebook on GitHub (not official docs).
- **INFERRED** = my reconstruction; plausible but not verified against a real record.

Primary sources:
- Davis semantic dictionary: https://docs.dynatrace.com/docs/semantic-dictionary/model/davis (abbrev. **[DAVIS-SD]**)
- Logs semantic dictionary: https://docs.dynatrace.com/docs/semantic-dictionary/model/log (**[LOG-SD]**)
- Global fields dictionary: https://docs.dynatrace.com/docs/semantic-dictionary/fields (**[FIELDS-SD]**)
- Log enrichment via OneAgent: https://docs.dynatrace.com/docs/analyze-explore-automate/logs/lma-log-ingestion/lma-log-ingestion-via-oa/lma-log-data-transformation-oa (**[LOG-OA]**)
- Davis DQL examples: https://www.dynatrace.com/docs/dynatrace-intelligence/use-cases/davis-dql-examples (read via GitHub mirror https://github.com/Yerkebulan-Ardabayev/dynatrace-docs-website/blob/main/docs/en/dynatrace-intelligence/use-cases/davis-dql-examples.md, "Updated on Jan 28, 2026") (**[DQL-EX]**)
- Workflow email tutorial: https://docs.dynatrace.com/docs/analyze-explore-automate/workflows/use-cases/workflows-tutorial-problems-email (**[WF-EMAIL]**)
- Practitioner notebook with live-tenant problem record: https://github.com/timstewart-dynatrace/Best-Practice-Notebooks (file `WFLOW - Workflows and Alert Notifications/markdown/-[WFLOW]-02-triggers.md`) (**[WFLOW-02]**)

---

## Q1. `dt.davis.problems` record fields and a full example

### Takeaway
`dt.davis.problems` is a view that holds the **latest state of each problem**. Each record has `event.kind = "DAVIS_PROBLEM"`, a stable `event.id`, a human `display_id` ("P-…"), `event.status` ACTIVE/CLOSED, `event.category` (6 values), `event.severity` (long 1–5, 1 = most severe), and topology fields. The Smartscape fields (`smartscape.affected_entities`, `root_cause.smartscape_entity`) are records with `{id, type, name}`. The classic `affected_entity_ids` / `root_cause_entity_id` / `entity_tags` still appear on records but are **deprecated**. The docs contain **no** full example record. The only published full-ish record is an abridged live-tenant sample from a practitioner notebook.

### Cited Findings

**General event fields (all DOCUMENTED, [DAVIS-SD])**
| Field | Type | Values / example (verbatim) | Notes |
|---|---|---|---|
| `event.id` | string | `5547782627070661074_1647601320000` | Stable across refreshes. |
| `display_id` | string | `P-2307288` | Human-friendly ID (system field). |
| `event.kind` | string | `DAVIS_PROBLEM` | Permission-tagged. |
| `event.status` | string | `ACTIVE` ; `CLOSED` | |
| `event.status_transition` | string (experimental) | `CREATED` ; `UPDATED` ; `REFRESHED` ; `RESOLVED` ; `REOPENED` ; `CLOSED` | |
| `event.category` | string | `AVAILABILITY` ; `ERROR` ; `SLOWDOWN` ; `RESOURCE_CONTENTION` ; `CUSTOM_ALERT` ; `MONITORING_UNAVAILABLE` | `INFO` exists only for **events**, not problems. |
| `event.name` | string | `CPU saturation` ; `Multiple infrastructure problems` | |
| `event.description` | string | `The current response time (11 s) exceeds the auto-detected baseline (767 ms) by 1,336 %` | Combines descriptions of the problem's events. |
| `event.start` | timestamp | `16481073970000` (docs example) | Set to the earliest event's start plus analysis offset. |
| `event.end` | timestamp | `16481073970000` | UTC, Grail nanosecond format. |
| `timestamp` | timestamp | `1649822520123123123` | UNIX epoch ns of the update. |
| `event.severity` | long | 1 (most severe) – 5 (least) | Problem severity = min across its events; **null** if no event has a severity. |
| `dt.analysis.offset` | duration | `5m` ; `-5m` | |

**System fields (DOCUMENTED, [DAVIS-SD])**
| Field | Type | Example / enum |
|---|---|---|
| `dt.analysis.ready` | boolean | true once initial analysis is available |
| `dt.davis.affected_users_count` | long | — |
| `dt.davis.event_ids` | string[] | `['-2127669892157121805_1688396340000']` |
| `dt.davis.impact_level` | string (see conflict below) | `Application` ; `Environment` ; `Infrastructure` ; `Services` ; `Synthetic` |
| `dt.davis.is_duplicate` | boolean | — |
| `dt.davis.last_reopen_timestamp` | timestamp | not set if never reopened |
| `dt.davis.mute.status` | string | `MUTED` (also set on manual close) |
| `dt.davis.mute.user` | string | `donald_duck@gmail.com` |
| `dt.duration_marker` | duration | `5 min` ; `10 min` ; `15 min` ; `30 min` ; `60 min` ; `2 h` ; `4 h` ; `24 h` ; `7 days` |
| `duplicate_problem_ids` | string[] | `['P-260401367', 'P-26040212']` |
| `labels.alerting_profile` | string[] | **deprecated** |
| `maintenance.is_under_maintenance` | boolean | — |
| `resolved_problem_duration` | duration | final duration in ns after resolution |

**Classic topology, all DEPRECATED ([DAVIS-SD])**
| Field | Type | Example | Replacement |
|---|---|---|---|
| `affected_entity_ids` | string[] | `['HOST-1234567890ABCDEF']` | `smartscape.affected_entities` |
| `affected_entity_names` | string[] | `['host-worker-097801ab8728e690a']` | same |
| `affected_entity_types` | string[] | `['dt.entity.host', 'dt.entity.service']` | same |
| `entity_tags` | string[] | `['departmentA', 'department:A']` | `primary_tags.*` |
| `related_entity_ids` | string[] | `['HOST-1234567890ABCDEF']` | `smartscape.related_entities` |
| `related_entity_names` | string[] | — | same |
| `root_cause_entity_id` | string | `HOST-1234567890ABCDEF` | `root_cause.smartscape_entity` |
| `root_cause_entity_name` | string | `Server 1.2.3.4` | same |

**Smartscape topology ([DAVIS-SD])**
| Field | Type | Example (verbatim) | Status |
|---|---|---|---|
| `root_cause.smartscape_entity` | record | `{'id': 'HOST-1234567890ABCDEF', 'type': 'host', 'name': 'host-worker-097801ab8728e690a'}` | stable |
| `smartscape.affected_entities` | record[] | `[{'id': 'HOST-1234567890ABCDEF', 'type': 'host', 'name': 'host-worker-097801ab8728e690a'}]` | stable |
| `smartscape.related_entities` | record[] | same shape | stable |
| `smartscape.affected_entity.ids` / `.types` | smartscapeId[] / string[] | `['host', 'service']` | deprecated |
| `smartscape.related_entity.ids` / `.types` | smartscapeId[] / string[] | — | deprecated |

- The deprecation is also stated in the upgrade guide: *"root_cause_entity_id and root_cause_entity_name are deprecated in favor of root_cause.smartscape_entity, and affected_entity_ids in favor of smartscape.affected_entities."* (quoted in [WFLOW-02], citing https://docs.dynatrace.com/docs/platform/upgrade/keep-problems-and-alerting-working/upgrade-guide-alert-notification)
- Fields **not** on the problem model: no `dt.entity.*` fields, no `dt.security_context`, no `management_zones` field are listed for problems in [DAVIS-SD]. A grep of the page for "security_context" and "management" returned zero matches. `dt.security_context` is defined globally as a permission field in [FIELDS-SD], but it is not listed in the problem model.
- Management zones: [WFLOW-02] OBSERVED (3rd-party): *"0 of 15,878 problems in `dt.davis.problems` over 30 days carried"* `management_zones`. The problem trigger also has no Management Zone filter. — [WFLOW-02](https://github.com/timstewart-dynatrace/Best-Practice-Notebooks)
- `primary_tags.*` is the documented replacement for `entity_tags` ([DAVIS-SD]). [WFLOW-02] describes it as present "When enriched", for example `primary_tags.team`.
- **Event ID format drift:** the dictionary example is `5547782627070661074_1647601320000`, while newer docs examples carry a `V2` suffix, e.g. `3417369641984270933_1773273960000V2` and `-8924735682950986450_1773318186796V2` (problem-comment section of [DAVIS-SD]). The [WFLOW-02] live sample shows `-1001992832270454690_1790253360000V2`. IDs can be negative (leading `-`).
- **Conflict on `dt.davis.impact_level` type:** [DAVIS-SD] types it as `string`, while [WFLOW-02] (observed) types it as `string[]` and says it holds *every* level observed across events. The [DAVIS-SD] description ("impact levels seen across the problem's events") suggests multiple values. Treat as uncertain.
- **Conflict on Smartscape `type` casing:** [DAVIS-SD] example uses lowercase `'type': 'host'`. The [WFLOW-02] live sample shows `"type": "SERVICE"` (uppercase). Fixtures should tolerate both. Deprecated `affected_entity_types` uses `dt.entity.host` style in [DAVIS-SD], yet [WFLOW-02] routing examples test `"SERVICE" in affected_entity_types`. This is unresolved.
- `dt.davis.is_frequent_event` is documented only on **events** ([DAVIS-SD]), but the official [DQL-EX] MTTR query filters problems on `dt.davis.is_frequent_event == false and dt.davis.is_duplicate == false and maintenance.is_under_maintenance == false`. So the field is likely also present on problem records (INFERRED from the official query).

**Live-tenant abridged record — OBSERVED (3rd-party) verbatim from [WFLOW-02] (dated 09/24/2026):**
```json
{
  "display_id": "P-260911127",
  "event.id": "-1001992832270454690_1790253360000V2",
  "event.name": "High response time on checkout service",
  "event.category": "SLOWDOWN",
  "event.status": "ACTIVE",
  "event.status_transition": "CREATED",
  "event.severity": 3,
  "event.start": "2026-09-24T12:37:00.000000000Z",
  "affected_entity_ids": ["SERVICE-ABC123"],
  "smartscape.affected_entities": [{"id": "SERVICE-ABC123", "name": "checkout", "type": "SERVICE"}],
  "entity_tags": ["team:checkout"],
  "maintenance.is_under_maintenance": false
}
```
[WFLOW-02] field presence notes (OBSERVED on one validation tenant, not a guarantee):
- `root_cause_entity_id` was present on 102 of 540 problems in 24 h, and `root_cause.smartscape_entity` on 127 of 540. **Root cause is usually absent.**
- `event.end` and `resolved_problem_duration` appear only on CLOSED records.
- `event.severity` is "No longer defaulted from SaaS 1.348". It may be missing.
- A close record can carry `event.status_transition` `RESOLVED` or `UPDATED` rather than `CLOSED`. Branch on `event.status`.

### Inferences
- **Full fixture record (INFERRED reconstruction, Kafka broker scenario).** Field names come from [DAVIS-SD]. Values are invented in documented formats. Timestamps are shown as ISO strings, the way DQL/JSON results render them (as in the [WFLOW-02] sample):
```json
{
  "timestamp": "2026-10-12T14:07:31.412000000Z",
  "event.kind": "DAVIS_PROBLEM",
  "event.id": "-4417092361180924518_1791814020000V2",
  "display_id": "P-261012042",
  "event.name": "Process unavailable",
  "event.description": "Process kafka-broker (kafka-2) is unavailable ...",
  "event.category": "AVAILABILITY",
  "event.status": "ACTIVE",
  "event.status_transition": "UPDATED",
  "event.severity": 2,
  "event.start": "2026-10-12T14:07:00.000000000Z",
  "dt.analysis.ready": true,
  "dt.davis.event_ids": ["8302664511948770210_1791814020000"],
  "dt.davis.impact_level": "Infrastructure",
  "dt.davis.is_duplicate": false,
  "dt.davis.is_frequent_event": false,
  "dt.duration_marker": "5 min",
  "maintenance.is_under_maintenance": false,
  "root_cause.smartscape_entity": {"id": "PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10", "type": "PROCESS", "name": "kafka-broker (kafka-2)"},
  "smartscape.affected_entities": [{"id": "PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10", "type": "PROCESS", "name": "kafka-broker (kafka-2)"}],
  "smartscape.related_entities": [{"id": "HOST-91C2D4E5F6A70812", "type": "HOST", "name": "aks-kafka-pool-0001"}],
  "affected_entity_ids": ["PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10"],
  "affected_entity_types": ["dt.entity.process_group_instance"],
  "affected_entity_names": ["kafka-broker (kafka-2)"],
  "root_cause_entity_id": "PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10",
  "root_cause_entity_name": "kafka-broker (kafka-2)",
  "entity_tags": ["team:streaming", "app:kafka"]
}
```
  INFERRED points to flag in fixtures:
  - The Smartscape `type` string for a process (`PROCESS` vs `process` vs `process_group_instance`) is not documented. Docs only show `host` and `service`. [FIELDS-SD] names the replacement field `dt.smartscape.process`, which suggests the Smartscape type name is "process".
  - Whether ID values in `smartscape.*` keep the classic `PROCESS_GROUP_INSTANCE-` prefix is not documented.
  - A CLOSED variant should add `event.end`, `resolved_problem_duration` (duration, ns; e.g. `1380000000000` = 23 min), `event.status: "CLOSED"`, and `event.status_transition: "RESOLVED"` or `"CLOSED"`.
- Raw JSON timestamp encoding: Grail stores timestamps as epoch ns. Note that the docs example `16481073970000` has only 14 digits, so it is not a valid ns value; treat that docs example as a typo. DQL JSON output (UI/API) renders ISO-8601 with ns precision, as in [WFLOW-02].

### Gaps
- No official full example `dt.davis.problems` record exists in the docs. Only per-field examples.
- The exact Smartscape `type` vocabulary inside `smartscape.affected_entities` (casing; process/container/k8s types) is not documented.
- Whether `dt.security_context` or `primary_tags.*` are populated on problem records is not documented per-model.
- `dt.davis.impact_level` cardinality (string vs string[]) has conflicting sources.
- `dt.davis.mute.*` presence on non-muted records (absent vs null) is undocumented. INFERRED: absent/null.

---

## Q2. `dt.davis.events` record fields, `event.type` values, example

### Takeaway
`dt.davis.events` records have `event.kind = "DAVIS_EVENT"`, an `event.type` identifier, `event.category` (7 values including `INFO`), `event.provider`, `event.severity`, and source attribution via `dt.smartscape_source.id` / `.type`. The deprecated `dt.source_entity` / `dt.source_entity.type` are still present. Built-in OneAgent types such as `OSI_HIGH_CPU`, `OSI_HIGH_MEMORY` and `PROCESS_UNAVAILABLE` are confirmed in official queries and docs. The event-category docs list classic identifiers such as `CPU_SATURATED`, which differ from the Grail values.

### Cited Findings
**Fields (DOCUMENTED, [DAVIS-SD] "Davis event fields")**
| Field | Type | Values / example (verbatim) | Notes |
|---|---|---|---|
| `event.kind` | string | `DAVIS_EVENT` | |
| `event.id` | string | `5547782627070661074_1647601320000` | stable across updates |
| `event.type` | string | `APPLICATION_SLOWDOWN` ; `AVAILABILITY_EVENT` | permission-tagged |
| `event.name` | string | `CPU saturation` ; `User action duration degradation` | |
| `event.group_label` | string (experimental) | `Availability` | Community example filters `event.group_label == "Process crashed"` (see below). |
| `event.category` | string | `INFO` ; `AVAILABILITY` ; `ERROR` ; `SLOWDOWN` ; `RESOURCE_CONTENTION` ; `CUSTOM_ALERT` ; `MONITORING_UNAVAILABLE` | |
| `event.provider` | string | `AVAILABILITY` ; `LOG_EVENTS` ; `METRIC_EVENTS` ; `SYNTHETIC` ; `EVENTS_REST_API_INGEST` ; `KUBERNETES_ANOMALY_DETECTION` ; `AGENT_LOCAL_REST_API_INGEST` | |
| `event.status` | string | example shown as `Active` | **Casing conflict**: all official DQL examples filter `event.status == "ACTIVE"`. Use `ACTIVE`/`CLOSED`. |
| `event.status_transition` | string (experimental) | `CREATED` ; `UPDATED` ; `REFRESHED` ; `TIMED_OUT` ; `RECOVERED` | Different enum from problems. |
| `event.severity` | long | 1–5 | |
| `event.start` / `event.end` / `timestamp` | timestamp | — | `event.start` can differ from `timestamp`. |
| `event.description` | string | — | |
| `dt.smartscape_source.id` | smartscapeId | `K8S_CLUSTER-E0D8F94D9065F24F` | stable replacement |
| `dt.smartscape_source.type` | string | `K8S_CLUSTER` ; `AWS_LAMBDA_FUNCTION` | |
| `dt.source_entity` | string | `HOST-E0D8F94D9065F24F` ; `PROCESS_GROUP_INSTANCE-E0D8F94D9065F24F` | **deprecated** |
| `dt.source_entity.type` | string | `host` ; `process_group_instance` ; `cloud:azure:resource_group` | **deprecated** |
| `dt.query` | string (experimental) | `timeseries avg(dt.host.cpu.idle)` | |
| `dt.davis.is_frequent_event` | boolean | — | system field |
| `dt.davis.impact_level` | string | same 5 values as problems | |
| `dt.davis.mute.status` / `.mute.user` | string | `MUTED` | |
| `dt.davis.suppress_problem_reason` | string | `Due to manual problem close with id P-2307288 by user donald` | |
| `dt.event.correlation_id` | uid | `b52o3b1a50dbbb31` | |
| `maintenance.is_under_maintenance` | boolean | — | |
| config: `dt.davis.timeout` (long, minutes, default 15, cap 360), `dt.davis.is_merging_allowed`, `dt.davis.is_rootcause_relevant`, `dt.davis.is_problem_suppressed`, `dt.davis.is_frequent_issue_detection_allowed`, `dt.davis.analysis_time_budget`, `dt.davis.analysis_trigger_delay`, `dt.event.correlation_tag` | various | — | |
| `affected_entity_ids`, `affected_entity_types`, `related_entity_ids`, `entity_tags` | string[] | — | **deprecated** |
| `smartscape.affected_entities` / `smartscape.related_entities` | record[] | `{id,type,name}` | |
| `dt.smartscape.__type__` | smartscapeId | placeholder for `dt.smartscape.<type>` | |

- Footnotes in [DAVIS-SD] say `dt.smartscape_source.id` equals one of the `dt.smartscape.<type>` fields, and `dt.source_entity` equals one of the `dt.entity.<type>` fields. So an event on a PGI also carries `dt.entity.process_group_instance` with the same ID. — [DAVIS-SD]
- **There is no `dt.davis.event_id` field.** The event's own ID is `event.id`. Problems reference events through `dt.davis.event_ids` (string[]). — [DAVIS-SD]
- **Built-in `event.type` values seen in official Grail queries:** `OSI_HIGH_CPU`, `OSI_HIGH_MEMORY` (`fetch dt.davis.events | filter in(event.type, array("OSI_HIGH_CPU", "OSI_HIGH_MEMORY"))`). — [DAVIS-SD]; also in [DQL-EX]: `filter event.type == "OSI_HIGH_CPU" or event.type == "OSI_HIGH_MEMORY"`.
- The Events API v2 event-types example shows `"type": "OSI_HIGH_CPU"`, `"displayName": "High CPU"`, `"severityLevel": "PERFORMANCE"`. — [Events API v2 GET event types (mirror)](https://github.com/Yerkebulan-Ardabayev/dynatrace-docs-website/blob/main/docs/managed/dynatrace-api/environment-api/events-v2/get-event-types.md)
- Availability event types (identifier, display name): `UNEXPECTED_LOW_LOAD` (Unexpected low traffic), `OSI_UNEXPECTEDLY_UNAVAILABLE` (Host or monitoring unavailable), `PROCESS_UNAVAILABLE` (Process unavailable, PGI), `PROCESS_GROUP_LOW_INSTANCE_COUNT` (Process unavailable, process group), `WEB_CHECK_GLOBAL_OUTAGE`, `WEB_CHECK_LOCAL_OUTAGE`, `HOST_LOG_AVAILABILITY`, `PROCESS_LOG_AVAILABILITY`, `AVAILABILITY_EVENT` (custom). — [Availability events](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/event-analysis-and-correlation/event-categories/availability-events)
- Resource-contention identifiers on the docs page: `CPU_SATURATED`, `MEMORY_SATURATED`, `LOW_DISK_SPACE`, `SLOW_DISK`, `HIGH_GC_ACTIVITY`, `HIGH_LATENCY`, `HIGH_NETWORK_UTILIZATION`, `HOST_DISK_LOW_INODES`, `INSUFFICIENT_DISK_QUEUE_DEPTH`, `UNEXPECTED_HIGH_LOAD`, `RESOURCE_EVENT`. CPU saturation default: "average CPU usage is higher than 95% in 18 of 30 consecutive 10-second intervals". — [Resource events](https://docs.dynatrace.com/docs/shortlink/event-types-resource). **Conflict:** these classic names (`CPU_SATURATED`) differ from the Grail `event.type` used in official DQL (`OSI_HIGH_CPU`).
- Error types: `ERROR_EVENT`, `HIGH_NETWORK_ERROR_RATE`, `HIGH_DROPPED_PACKETS_RATE`, `HOST_LOG_ERROR`, `PROCESS_LOG_ERROR`, `JAVASCRIPT_ERROR_RATE_INCREASED`, `MOBILE_APP_CRASH_RATE_INCREASED`, `LAMBDA_FUNCTION_HIGH_ERROR_RATE`, `ELASTIC_LOAD_BALANCER_HIGH_BACKEND_FAILURE_RATE`, `RDS_RESTART_SEQUENCE`. — [Error events](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/event-analysis-and-correlation/event-categories/error-events)
- Slowdown types: `SERVICE_RESPONSE_TIME_DEGRADED`, `USER_ACTION_DURATION_DEGRADATION`, `SYNTHETIC_SLOWDOWN`, `HOST_LOG_PERFORMANCE`, `PROCESS_LOG_PERFORMANCE`, `PERFORMANCE_EVENT`. — [Slowdown events](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/event-analysis-and-correlation/event-categories/slowdown-events)
- Info types: `CUSTOM_ANNOTATION`, `CUSTOM_DEPLOYMENT`, `CUSTOM_INFO`, `MARKED_FOR_TERMINATION`, `LOG_MATCHED`, `APPLICATION_JS_FRAMEWORK_DETECTED`, … — [Info events](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/event-analysis-and-correlation/event-categories/info-events)
- For **ingested event reports** (API/templates), `event.type` "Must be one of: AVAILABILITY_EVENT, PERFORMANCE_EVENT, RESOURCE_CONTENTION_EVENT, ERROR_EVENT, CUSTOM_ALERT, CUSTOM_ANNOTATION, CUSTOM_CONFIGURATION, CUSTOM_DEPLOYMENT, CUSTOM_INFO, MARKED_FOR_TERMINATION, WARNING". — [DAVIS-SD] (event report section)
- Process crashes: a community answer filters `dt.davis.events.snapshots` with `event.group_label == "Process crashed"`. — [Community: How to detect alarm process crashes](https://community.dynatrace.com/t5/Alerting/How-to-detect-alarm-process-crashes/m-p/299188) (seen via search snippet only; not fetched in full).
- Problem comments are stored as `dt.davis.events` with `event.type == "CUSTOM_ANNOTATION"`, `event.kind` `DAVIS_EVENT`, start == end, and fields `annotation.id`, `annotation.problem_ids` (string[]), `annotation.source`, `annotation.url`, `annotation.user_id`. — [DAVIS-SD]

### Inferences
- **Example Davis event (INFERRED, Kafka broker process unavailable).** Field names are from [DAVIS-SD]. The `event.type` value is from the availability docs. `event.provider` `AVAILABILITY` is a documented enum, but its pairing with this type is inferred:
```json
{
  "timestamp": "2026-10-12T14:07:20.981000000Z",
  "event.kind": "DAVIS_EVENT",
  "event.id": "8302664511948770210_1791814020000",
  "event.type": "PROCESS_UNAVAILABLE",
  "event.name": "Process unavailable",
  "event.group_label": "Availability",
  "event.category": "AVAILABILITY",
  "event.provider": "AVAILABILITY",
  "event.status": "ACTIVE",
  "event.status_transition": "CREATED",
  "event.severity": 2,
  "event.start": "2026-10-12T14:07:00.000000000Z",
  "event.description": "Process kafka-broker (kafka-2) is unavailable",
  "dt.smartscape_source.id": "PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10",
  "dt.smartscape_source.type": "PROCESS",
  "dt.source_entity": "PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10",
  "dt.source_entity.type": "process_group_instance",
  "dt.entity.process_group_instance": "PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10",
  "dt.davis.is_frequent_event": false,
  "dt.davis.impact_level": "Infrastructure",
  "dt.davis.timeout": 15,
  "dt.davis.is_merging_allowed": true,
  "dt.davis.is_rootcause_relevant": true,
  "maintenance.is_under_maintenance": false,
  "affected_entity_ids": ["PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10"],
  "affected_entity_types": ["dt.entity.process_group_instance"],
  "entity_tags": ["team:streaming"]
}
```
- For a CPU fixture, use `event.type: "OSI_HIGH_CPU"`, `event.name: "CPU saturation"`, `event.category: "RESOURCE_CONTENTION"`, and the source on a `HOST-…` ID. INFERRED: the pairing of name to category is reasonable but not verified in a single record.
- `PROCESS_CRASHED` as a literal Grail `event.type` was **not found** in any source. Prefer `event.group_label == "Process crashed"` for crash fixtures, or flag it as unverified.

### Gaps
- No official full example `dt.davis.events` record exists.
- No authoritative mapping exists between classic identifiers (`CPU_SATURATED`) and Grail `event.type` values (`OSI_HIGH_CPU`). Only the OSI_HIGH_CPU/OSI_HIGH_MEMORY pair is confirmed for Grail. Recommended verification: `fetch dt.davis.events | summarize count(), by:{event.type, event.name, event.category}` on a real tenant.
- The exact value of `dt.smartscape_source.type` for processes is not documented.
- `dt.davis.timeout` and the other config fields may be absent on built-in OneAgent events (undocumented).

---

## Q3. DQL query patterns; one row per problem or per update?

### Takeaway
In current Grail, `fetch dt.davis.problems` and `fetch dt.davis.events` are **deduplicated views, one row per problem/event (latest state)**. Per-update history lives in the separate `dt.davis.problems.snapshots` / `dt.davis.events.snapshots` tables, where you need `dedup`/`takeLast` by `event.id`. Older guidance using `takeLast` on `dt.davis.problems` reflects the earlier per-update behaviour.

### Cited Findings
- *"dt.davis.problems — This is a view over the underlying problem data. It represents the current/latest state of each problem — essentially a deduplicated, up-to-date picture of each problem. It is the recommended, less error-prone way to query Davis problems."* — [DAVIS-SD]
- *"dt.davis.problems.snapshots contains immutable point-in-time snapshots of a problem's state. Every time a problem is updated (for example, new affected entities, a change in category, or a change in severity), a new snapshot record is written."* — [DAVIS-SD]
- Events have the same split. `dt.davis.events.snapshots` "contains multiple records per event — one for each state change". When you query it you typically "Deduplicate by event.id (taking the latest timestamp)" and filter for `event.status == "ACTIVE"`. — [DAVIS-SD]
- Official DQL examples (verbatim, [DAVIS-SD]):
  - `fetch dt.davis.problems | filter not(dt.davis.is_duplicate) | filter event.status == "ACTIVE" | fields id=display_id, title=event.name, status=event.status`
  - `fetch dt.davis.problems | filter not(dt.davis.is_duplicate) | filter display_id == "P-12345678" | fields id=display_id, title=event.name, status=event.status`
  - `fetch dt.davis.problems, from: -1h | filter not(dt.davis.is_duplicate) | filter event.status == "ACTIVE" | summarize active_problem_count = count()`
  - `fetch dt.davis.problems, from: -24h | filter event.status == "ACTIVE" | makeTimeseries active_problems = count(), interval: 1h, spread: timeframe( from: event.start, to: coalesce(event.end, now()))`
  - Logs for a problem's entities: `fetch logs | filter dt.source_entity in [ fetch dt.davis.problems | filter display_id == "P-12345678" | fields affected_entity_ids ]`
  - `fetch dt.davis.problems.snapshots | filter event.id == "P-2606232" | sort timestamp desc`. Note that this docs example puts a display-ID-style value into `event.id`, which looks like a docs error.
  - `fetch dt.davis.events | filter event.status == "ACTIVE" | sort event.severity asc | fields event.id, event.name, event.severity, event.category`
  - `fetch dt.davis.events.snapshots | filter event.id == "825910910729278885_1781868757175" | sort timestamp desc`
- [DQL-EX] (Jan 2026) examples:
  - `fetch dt.davis.problems, from:now()-24h, to:now() | summarize {problemCount = countDistinct(event.id)}`
  - `fetch dt.davis.problems | expand affected_entity_ids | summarize count = countDistinct(display_id), by:{affected_entity_ids} | sort count, direction:"descending" | limit 3`
  - `fetch dt.davis.problems | expand affected_entity_ids | fieldsAdd host.name = entityName(affected_entity_ids, type: "dt.entity.host") | filter host.name == "myhost"`
  - MTTR: `... | filter event.status == "CLOSED" | filter dt.davis.is_frequent_event == false and dt.davis.is_duplicate == false and maintenance.is_under_maintenance == false | makeTimeseries ... avg(toLong(resolved_problem_duration)/3600000000000.0), time:event.end`
  - [DQL-EX] still mentions `takeLast` in its prose ("the DQL command `takeLast` of the field `event.status` receives the last state"), but the query shown no longer uses it. This is a leftover from the per-update era.
- Davis problems "and their updates are stored as Grail events". — [DQL-EX]

### Inferences
- For fixtures that emulate `fetch dt.davis.problems`, emit **one record per `event.id`**. For a history/timeline feature, emit multiple records per `event.id` in a `.snapshots`-style fixture with increasing `timestamp` and `event.status_transition` progression (CREATED → UPDATED/REFRESHED → RESOLVED/CLOSED).
- A legacy-compatible pattern that works on both views: `fetch dt.davis.problems.snapshots | summarize {status = takeLast(event.status), name = takeLast(event.name)}, by:{event.id, display_id}` (INFERRED syntax, built from standard DQL `takeLast`).

### Gaps
- The date when `dt.davis.problems` switched from per-update rows to a latest-state view was not found.
- The default query timeframe for `fetch dt.davis.problems` (how far back a long-running ACTIVE problem is visible) is not documented in the sources read.

---

## Q4. Grail log record fields (`fetch logs`) and example for containerized Java/Kafka

### Takeaway
Core log fields are `timestamp`, `content`, `loglevel`, `status`, `log.source`, `log.iostream`, `event.type` ("LOG"), `trace_id`, `span_id` and `process.technology`. OneAgent adds `dt.entity.*` IDs (all now deprecated in favour of `dt.smartscape.*`), `dt.source_entity` (string[]; deprecated), and the container/k8s fields `container.name`, `k8s.namespace.name`, `k8s.pod.name`, `k8s.container.name`, `k8s.cluster.name`, `k8s.deployment.name`, plus `host.name` and `dt.process.name`. The `status` values are only `ERROR`/`WARN`/`INFO`/`NONE`. The `loglevel` values come from a 9-level set.

### Cited Findings
**Log model fields (DOCUMENTED, [LOG-SD])**
| Field | Type | Example (verbatim) | Status |
|---|---|---|---|
| `timestamp` | timestamp | `1649822520123123123` | stable |
| `content` | string | `No keepalive from datasource statsd. Restarting` | stable |
| `loglevel` | string | `ERROR` ; `INFO` ; `TRACE` | stable |
| `status` | string | `ERROR` ; `WARN` ; `INFO` ; `NONE` ("Only INFO, WARN, ERROR and NONE values are allowed.") | experimental |
| `log.source` | string | `/var/log/messages` ; `Windows Event Log` ; `Docker Container Output` ; `stdout` | stable, permission |
| `log.iostream` | string | `stdout` ; `stderr` | stable |
| `log.record.uid` | string | `c42dbd31-17b5-4270-b1a7-824a52e0d0e9` | experimental |
| `event.type` | string | `LOG` | stable |
| `ordinal` | long | `1479670` | stable |
| `process.technology` | string[] | `['Java', 'Tomcat']` ; `['Go', 'Envoy']` | stable |
| `trace_id` | string | `357bf70f3c617cb34584b31bd4616af8` (16-byte hex) | experimental |
| `span_id` | string | `f76281848bd8288c` (8-byte hex) | experimental |
| `dt.entity.process_group` | string | `PROCESS_GROUP-E0D8F94D9065F24F` | deprecated → `dt.process_group.id` / `dt.process_group.detected_name` |
| `dt.entity.process_group_instance` | string | `PROCESS_GROUP_INSTANCE-E0D8F94D9065F24F` | deprecated → `dt.smartscape.process` |
| `dt.source_entity` | **string[]** | `['PROCESS_GROUP_INSTANCE-22714B95E4BF6AE0', 'PROCESS_GROUP_INSTANCE-D6DD5FF37FBEF0DF']` | deprecated → `dt.smartscape_source.id` |
- Process-group fields "may contain arrays when multiple processes write to the same file or run in one container"; use `matchesValue` rather than `==`. — [LOG-SD]
- **OneAgent auto-added attributes (verbatim list, [LOG-OA]):** General: `container.name`, `container.image.name`, `container.id`, `dt.host_group.id`, `dt.kubernetes.cluster.id`, `dt.kubernetes.cluster.name`, `dt.kubernetes.node.system_uuid`, `dt.process.name`, `event.type`, `host.name`, `k8s.cluster.name`, `k8s.namespace.name`, `k8s.pod.name`, `k8s.pod.uid`, `k8s.container.name`, `k8s.deployment.name`, `log.iostream`, `loglevel`, `log.source`, `process.technology`, `span_id`, `status`, `trace_id`, `web_server.iis.*`. Entity model: `dt.entity.cloud_application`, `dt.entity.cloud_application_instance`, `dt.entity.cloud_application_namespace`, `dt.entity.container_group`, `dt.entity.container_group_instance`, `dt.entity.host`, `dt.entity.kubernetes_cluster`, `dt.entity.kubernetes_node`, `dt.entity.process_group`, `dt.entity.process_group_instance`, `dt.source_entity`. Logs are also enriched with host-level resource attributes (host tags, cloud, k8s). — [LOG-OA]
- **loglevel keyword detection (OneAgent):** 19 keywords map to 9 levels. `trace`/`debug`/`fine`/`finer`/`finest` → DEBUG; `notice` → NOTICE; `info`/`information` → INFO; `warn`/`warning`/`severe_warning` → WARN; `severe` → SEVERE; `err`/`error` → ERROR; `crit`/`critical` → CRITICAL; `alert` → ALERT; `fatal`/`emerg` → EMERGENCY. Detection searches the first 100 chars of the first 2 lines. — [LOG-OA]
- **status rollup:** SEVERE, ERROR, CRITICAL, ALERT, FATAL, EMERGENCY → `ERROR`; WARN → `WARN`; INFO, TRACE, DEBUG, NOTICE → `INFO`; NONE → `NONE`. Example: input "serious" → loglevel `SEVERE` → status `ERROR`. — [LOG-OA]
- Global field definitions ([FIELDS-SD]):
  - `dt.entity.host` (deprecated → `dt.smartscape.host`, example `HOST-E0D8F94D9065F24F`)
  - `dt.entity.container_group` (deprecated, "no replacement for container groups in Smartscape", `CONTAINER_GROUP-7C2B1C24FFB288CB`)
  - `dt.entity.container_group_instance` (deprecated → `dt.smartscape.container`, `CONTAINER_GROUP_INSTANCE-F4A1347110826781`)
  - `dt.entity.cloud_application` (deprecated → `dt.smartscape.k8s_deployment`, `CLOUD_APPLICATION-3AB5BBF3E09A7942`)
  - `dt.entity.cloud_application_instance` (deprecated → `dt.smartscape.k8s_pod`)
  - `dt.entity.kubernetes_cluster` (deprecated → `dt.smartscape.k8s_cluster`, `KUBERNETES_CLUSTER-E0D8F94D9065F24F`)
  - `dt.entity.cloud_application_namespace` (`CLOUD_APPLICATION_NAMESPACE-C61324AA70F57BCB`)
  - `k8s.cluster.name` (stable, `unguard-dev` ; `acme-prod10`)
  - `k8s.workload.kind` (`deployment` ; `statefulset` ; …)
  - `k8s.workload.name` (`checkoutservice`)
  - `k8s.deployment.name` (`my-deployment`)
  - `container.id` (`a3bf90e006b2`)
  - `container.image.name` (`gcr.io/opentelemetry/operator`)
  - `container.name` ("Container name used by container runtime")
  - `host.name` (experimental, `ip-10-178-54-32.ec2.internal`; not the entity name)
  - `dt.security_context` (stable, permission tag)
  - `dt.process_group.id` (`PROCESS_GROUP-1234`)
- `dt.process.name` is listed by [LOG-OA] but was **not found** in [FIELDS-SD].

### Inferences
- **Example log record (INFERRED; Kafka broker pod on Kubernetes, OneAgent-collected).** All field names are from [LOG-SD]/[LOG-OA]. All values are invented in documented formats:
```json
{
  "timestamp": "2026-10-12T14:06:58.214000000Z",
  "event.type": "LOG",
  "content": "[2026-10-12 14:06:58,214] ERROR [ReplicaFetcher replicaId=2, leaderId=1, fetcherId=0] Error for partition orders-3 at offset 1048213 (kafka.server.ReplicaFetcherThread)\norg.apache.kafka.common.errors.NotLeaderOrFollowerException: This server is not the leader for that topic-partition.",
  "loglevel": "ERROR",
  "status": "ERROR",
  "log.source": "Container Output",
  "log.iostream": "stdout",
  "process.technology": ["Java", "Apache Kafka"],
  "dt.process.name": "kafka.Kafka",
  "host.name": "aks-kafka-pool-0001",
  "container.name": "kafka",
  "container.id": "3f2c9b1d7a4e",
  "container.image.name": "docker.io/bitnami/kafka",
  "k8s.cluster.name": "prod-aks-weu",
  "k8s.namespace.name": "streaming",
  "k8s.pod.name": "kafka-2",
  "k8s.pod.uid": "8b0c7a52-1f4e-4c3a-9d77-2e1b6f0a9c41",
  "k8s.container.name": "kafka",
  "dt.kubernetes.cluster.name": "prod-aks-weu",
  "dt.entity.host": "HOST-91C2D4E5F6A70812",
  "dt.entity.process_group": "PROCESS_GROUP-3D7A1C0E9B2F4A65",
  "dt.entity.process_group_instance": "PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10",
  "dt.entity.container_group": "CONTAINER_GROUP-7C2B1C24FFB288CB",
  "dt.entity.container_group_instance": "CONTAINER_GROUP_INSTANCE-F4A1347110826781",
  "dt.entity.kubernetes_cluster": "KUBERNETES_CLUSTER-E0D8F94D9065F24F",
  "dt.entity.cloud_application_namespace": "CLOUD_APPLICATION_NAMESPACE-C61324AA70F57BCB",
  "dt.source_entity": ["PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10"],
  "ordinal": 1479670
}
```
  Notes:
  - `log.source` for container stdout: the docs example lists `Docker Container Output` and `stdout`. "Container Output" is my INFERRED variant; use `Docker Container Output` for a Docker host fixture.
  - Kafka statefulset brokers have no `k8s.deployment.name`. Use `k8s.workload.kind: "statefulset"` / `k8s.workload.name: "kafka"` if that enrichment is modelled (INFERRED).
  - `process.technology` value "Apache Kafka" is INFERRED.
  - `trace_id`/`span_id` are usually absent for Kafka broker logs (INFERRED).
  - Multi-line Java stack traces arrive inside a single `content` (INFERRED from OneAgent "first two lines" severity rule).

### Gaps
- No official full example log record exists. Only per-field examples.
- The exact `log.source` string OneAgent emits for k8s container stdout on containerd/CRI-O was not confirmed.
- Whether `dt.smartscape.*` replacements (e.g. `dt.smartscape.process`) are already populated on logs alongside the deprecated fields was not confirmed.
- The `dt.security_context` value format on logs is not documented beyond the field definition.

---

## Q5. Workflow problem-trigger `event()` payload keys

### Takeaway
The Davis problem trigger passes **the `dt.davis.problems` record itself**, so `event()` keys are exactly the Grail field names, read with bracket syntax (`event()["display_id"]`). There are no `title`/`problem_id`/`status` aliases. The problem URL comes from `{{ problem_link() }}` or from building `/ui/apps/dynatrace.davis.problems/problem/{event.id}`.

### Cited Findings
- *"The Problem trigger returns the problem record."* and *"You can use any field from the problem record, stored in `dt.davis.problems`"*. — [WF-EMAIL]
- Verbatim template expressions: `{{ event()["event.status"] }} Problem {{ event()["display_id"] }}: {{ event()["event.category"] }}`, `{{ event()["event.name"] }}`, `{{ event()["event.description"] }}`, and `Go to problem: {{ environment().url }}/ui/apps/dynatrace.davis.problems/problem/{{ event()["event.id"] }}`. — [WF-EMAIL]
- Expressions are not allowed in "Run JavaScript" tasks. Use the automation-utils SDK `execution()` → `event()`, then `eventContext['event.id']` / `eventContext['display_id']`. — [Community: Get problemID from Davis Problem Trigger](https://community.dynatrace.com/t5/Developer-Q-A-Forum/Get-problemID-from-Davis-Problem-Trigger-in-workflow/m-p/221730) (search snippet)
- A short delay may be needed before a just-triggered problem is queryable via DQL ("it can take a couple of seconds for events … to be query-able"). — [Community: Workflows Davis problem trigger](https://community.dynatrace.com/t5/Automations/Workflows-Davis-problem-trigger/m-p/248422) (search snippet)
- Trigger config options in the Terraform provider test fixture: `categories`, `entity_tags`, `entity_tags_match`, `custom_filter`, `analysis_ready`, `trigger_on = "open-and-close"`, `severity_threshold = 3`, `trigger_on_update_fields = ["event.severity", "smartscape.affected_entities"]`, `problem_open_duration = 15`. The update-fields list uses Grail field names. — [terraform-provider-dynatrace example-davis-problem-trigger.tf](https://github.com/dynatrace-oss/terraform-provider-dynatrace/blob/main/dynatrace/api/automation/workflows/testdata/terraform/example-davis-problem-trigger.tf)
- OBSERVED (3rd-party), [WFLOW-02]:
  - There is "no `title`, `status`, `problem_url` or `start_time` field".
  - Classic placeholder mapping: `{PID}` → `event.id`, `{ProblemTitle}` → `event.name`, `{ProblemImpact}` → `dt.davis.impact_level`, `{Tags}` → `primary_tags.*`, `{ProblemURL}` → `{{ problem_link() }}` (Problem trigger only).
  - A Davis *event* trigger instead receives the `dt.davis.events` record.
  - No `management_zones` on problem records.
- The trigger can filter on `dt.duration_marker` via its minimum-duration option (dictionary: "Updated only while open"). — [DAVIS-SD]; usage per [WFLOW-02]

### Inferences
- An ops-triage app that ingests workflow webhooks can reuse the same fixture JSON as the `fetch dt.davis.problems` fixtures. Keys are identical (flat, dotted keys at top level, not nested objects). Nested values exist only for record fields (`smartscape.*`, `root_cause.smartscape_entity`).
- Timestamps in the workflow payload are ISO-8601 strings (consistent with the [WFLOW-02] sample). INFERRED: not confirmed by official docs.

### Gaps
- No official doc enumerates the full payload. Official docs only say "any field from the problem record".
- Whether the workflow payload includes fields with null values (vs omitting them) is undocumented. [WFLOW-02] advises `.get()` because root-cause fields are often absent.
