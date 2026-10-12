# Dynatrace Davis event types, problem titles, categories and severities (Kafka-heavy environment focus)

Research date: 2026-10-12. Target: current Dynatrace SaaS (Grail / `dt.davis.events` + `dt.davis.problems`), classic-only items marked.
Labels: **DOCUMENTED** = a cited source states the value verbatim; **INFERRED** = my reasoning or analogy, not stated verbatim by a source.

Key source URLs used repeatedly:
- S-SEM: Semantic dictionary, Davis model — https://docs.dynatrace.com/docs/discover-dynatrace/references/semantic-dictionary/model/davis
- S-CAT: Event categories overview — https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/event-analysis-and-correlation/event-categories
- S-AVAIL / S-RES / S-ERR / S-SLOW / S-INFO: category subpages — `.../event-categories/availability-events`, `/resource-events`, `/error-events`, `/slowdown-events`, `/info-events` (same base path as S-CAT)
- S-TF-PROFILE: Terraform provider alerting-profile settings schema (mirror of Dynatrace `builtin:alerting.profile` schema, with display names) — https://github.com/dynatrace-oss/terraform-provider-dynatrace/blob/main/dynatrace/api/builtin/alerting/profile/schema.json
- S-TF-PREDEF: Terraform provider `predefined_event_filter.go` — https://github.com/dynatrace-oss/terraform-provider-dynatrace/blob/main/dynatrace/api/v1/config/alerting/settings/predefined_event_filter.go
- S-ENV1: Generated TypeScript client of classic Environment API v1 (Problems/Events v1 `eventType` enum) — https://github.com/Dynatrace-ESA/dynatrace-api-client/blob/main/src/api/generated/env-v1.ts
- S-DQL: Dynatrace Intelligence DQL examples — https://docs.dynatrace.com/docs/dynatrace-intelligence/use-cases/dynatrace-intelligence-dql-examples
- S-AI-EVENTS: Dynatrace's official "dynatrace-for-ai" skill reference, Davis events — https://github.com/Dynatrace/dynatrace-for-ai/blob/main/skills/dt-alerting/references/davis-events.md
- S-AI-PROB: same repo, problems skill — https://github.com/Dynatrace/dynatrace-for-ai/blob/main/skills/dt-obs-problems/SKILL.md and `/references/problem-merging.md`
- S-K8S: Kubernetes alerting — https://docs.dynatrace.com/docs/observe/infrastructure-monitoring/container-platform-monitoring/kubernetes-monitoring/alert-on-kubernetes-issues
- S-K8S-APP: Kubernetes app anomaly detectors — https://docs.dynatrace.com/docs/observe/infrastructure-monitoring/container-platform-monitoring/kubernetes-app/reference/anomaly-detectors
- S-MKE: Metric key events — https://docs.dynatrace.com/docs/dynatrace-intelligence/anomaly-detection/metric-events/metric-key-events
- S-ME: Metric events overview — https://docs.dynatrace.com/docs/shortlink/metric-events
- S-TF-ME / S-TF-LE: Terraform `dynatrace_metric_events` / `dynatrace_log_events` docs + enums — https://github.com/dynatrace-oss/terraform-provider-dynatrace/blob/main/docs/resources/metric_events.md , https://github.com/dynatrace-oss/terraform-provider-dynatrace/blob/main/docs/resources/log_events.md , https://github.com/dynatrace-oss/terraform-provider-dynatrace/blob/main/dynatrace/api/builtin/anomalydetection/metricevents/settings/enums.go
- S-API2-ET: Events API v2 GET event type — https://docs.dynatrace.com/docs/dynatrace-api/environment-api/events-v2/get-event-type ; ingest: https://docs.dynatrace.com/docs/shortlink/api-events-v2-post-event
- S-KAFKA: Apache Kafka extension — https://docs.dynatrace.com/docs/ingest-from/extensions/supported-extensions/apache-kafka

---

## 1. Full list of Davis event types (event.type / eventType), default titles and categories

### Takeaway
There are **three naming generations** of event-type identifiers and they do not match: (a) the docs "event categories" pages and the classic Problems/Events API v1 use codes like `CPU_SATURATED`, `MEMORY_SATURATED`, `LOW_DISK_SPACE`, `PROCESS_CRASHED`; (b) the alerting-profile / Events API v2 / Grail world uses `OSI_HIGH_CPU`, `OSI_HIGH_MEMORY`, `OSI_LOW_DISK_SPACE`, `PGI_OF_SERVICE_UNAVAILABLE` etc.; (c) user-defined events (metric events, log events, Events API ingest) use generic types (`CUSTOM_ALERT`, `AVAILABILITY_EVENT`, `ERROR_EVENT`, `RESOURCE_CONTENTION_EVENT`, ...). For generating realistic **Grail** data, use the (b) `OSI_*`/`PGI_*`/`SERVICE_*` names for built-in detections and the (c) names for custom ones. Grail `event.category` enum is `INFO, AVAILABILITY, ERROR, SLOWDOWN, RESOURCE_CONTENTION, CUSTOM_ALERT, MONITORING_UNAVAILABLE`.

### Cited Findings

**Grail field model**
- DOCUMENTED: `dt.davis.events.event.category` enum = `INFO`, `AVAILABILITY`, `ERROR`, `SLOWDOWN`, `RESOURCE_CONTENTION`, `CUSTOM_ALERT`, `MONITORING_UNAVAILABLE`; `event.kind` = `DAVIS_EVENT`; `event.name` examples `CPU saturation`, `User action duration degradation`; `event.type` examples `APPLICATION_SLOWDOWN`, `AVAILABILITY_EVENT`; `event.description` example "The current response time (11 s) exceeds the auto-detected baseline (767 ms) by 1,336 %"; `event.provider` examples `AVAILABILITY`, `LOG_EVENTS`, `METRIC_EVENTS`, `SYNTHETIC`, `EVENTS_REST_API_INGEST`, `KUBERNETES_ANOMALY_DETECTION`, `AGENT_LOCAL_REST_API_INGEST`; `event.status` Active/Closed; `event.status_transition` `CREATED, UPDATED, REFRESHED, TIMED_OUT, RECOVERED`; `dt.davis.impact_level` `Application, Environment, Infrastructure, Services, Synthetic`; `event.id` example `5547782627070661074_1647601320000`; `dt.davis.timeout` default 15 min, max 360 — [S-SEM](https://docs.dynatrace.com/docs/discover-dynatrace/references/semantic-dictionary/model/davis)
- DOCUMENTED: `event.type == "OSI_HIGH_CPU"` and `event.type == "OSI_HIGH_MEMORY"` are used in official DQL examples against `dt.davis.events` (i.e., these are real Grail event.type values for CPU/memory saturation) — [S-DQL](https://docs.dynatrace.com/docs/dynatrace-intelligence/use-cases/dynatrace-intelligence-dql-examples)
- CONFLICT (DOCUMENTED both ways): Dynatrace's own AI skill reference lists categories as `AVAILABILITY, ERROR, SLOWDOWN, RESOURCE, INFO, CUSTOM` — [S-AI-EVENTS](https://github.com/Dynatrace/dynatrace-for-ai/blob/main/skills/dt-alerting/references/davis-events.md) — whereas the semantic dictionary says `RESOURCE_CONTENTION` and `CUSTOM_ALERT` — [S-SEM](https://docs.dynatrace.com/docs/discover-dynatrace/references/semantic-dictionary/model/davis). The semantic dictionary is the formal schema; prefer it. (`RESOURCE`/`CUSTOM`/`SLOWDOWN`/`ERROR`/`AVAILABILITY`/`INFO` are, however, the *event type enum* values in the metric-event/log-event settings templates — see §4.)
- DOCUMENTED: S-AI-EVENTS lists `event.provider` values `metric_events`, `Baseline`, `KUBERNETES_ANOMALY_DETECTION`, `Kubernetes_events`, `synthetic`, `opentelemetry` (note casing differs from S-SEM) — [S-AI-EVENTS](https://github.com/Dynatrace/dynatrace-for-ai/blob/main/skills/dt-alerting/references/davis-events.md)
- DOCUMENTED: Category order most→least severe: Monitoring unavailable, Availability, Error, Slowdown, Resource, Custom, Info, Warning. Info and Warning events don't open problems — [S-CAT](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/event-analysis-and-correlation/event-categories)
- DOCUMENTED: Events API v2 ingest `eventType` values: `AVAILABILITY_EVENT, CUSTOM_ALERT, CUSTOM_ANNOTATION, CUSTOM_CONFIGURATION, CUSTOM_DEPLOYMENT, CUSTOM_INFO, ERROR_EVENT, MARKED_FOR_TERMINATION, PERFORMANCE_EVENT, RESOURCE_CONTENTION_EVENT, WARNING` — [S-API2 ingest](https://docs.dynatrace.com/docs/shortlink/api-events-v2-post-event); same list given in [S-SEM](https://docs.dynatrace.com/docs/discover-dynatrace/references/semantic-dictionary/model/davis)

**Catalog A — built-in event types as used by alerting profiles / Events API v2 (Grail-style names), with display title (DOCUMENTED verbatim from [S-TF-PROFILE](https://github.com/dynatrace-oss/terraform-provider-dynatrace/blob/main/dynatrace/api/builtin/alerting/profile/schema.json), same list in [S-TF-PREDEF](https://github.com/dynatrace-oss/terraform-provider-dynatrace/blob/main/dynatrace/api/v1/config/alerting/settings/predefined_event_filter.go)). The *category* column is INFERRED by matching title to the docs category pages (S-AVAIL/S-ERR/S-SLOW/S-RES) unless marked D.**

| event.type (Grail/v2) | Default title (event.name) | Category | Kafka relevance |
|---|---|---|---|
| `OSI_HIGH_CPU` | CPU saturation | RESOURCE_CONTENTION (D: listed under Resource events as "CPU saturation", [S-RES]) | broker hosts/VMs |
| `OSI_HIGH_MEMORY` | Memory saturation | RESOURCE_CONTENTION (D, [S-RES]) | broker hosts |
| `OSI_LOW_DISK_SPACE` | Low disk space | RESOURCE_CONTENTION (D, [S-RES]) | broker log.dirs filling up — very realistic |
| `OSI_DISK_LOW_INODES` | Low number of inodes available | RESOURCE_CONTENTION (D, [S-RES]) | many segment files |
| `OSI_SLOW_DISK` | Slow disk | RESOURCE_CONTENTION (D, [S-RES]) | broker I/O |
| `OSI_NIC_UTILIZATION_HIGH` | High network utilization | RESOURCE_CONTENTION (D, [S-RES]) | replication traffic |
| `OSI_NIC_ERRORS_HIGH` | High number of network errors | ERROR (D, [S-ERR]) | |
| `OSI_NIC_DROPPED_PACKETS_HIGH` | High rate of dropped packets | ERROR (D, [S-ERR]) | |
| `OSI_UNEXPECTEDLY_UNAVAILABLE` | Host or monitoring unavailable | AVAILABILITY (D, [S-AVAIL]) | broker VM down |
| `OSI_GRACEFULLY_SHUTDOWN` | Host gracefully shutdown | INFO (INFERRED; S-AVAIL says graceful shutdown does not raise a problem) | |
| `HOST_OF_SERVICE_UNAVAILABLE` | Host unavailable | AVAILABILITY (INFERRED) | |
| `PGI_UNAVAILABLE` | Process unavailable | AVAILABILITY (D, [S-AVAIL] lists "Process unavailable") | Kafka JVM process |
| `PG_LOW_INSTANCE_COUNT` | Process group low instance count | AVAILABILITY (D, [S-AVAIL]) | fewer brokers than expected |
| `PGI_OF_SERVICE_UNAVAILABLE` | Service process unavailable | AVAILABILITY (INFERRED) | |
| `PROCESS_CRASHED` | Process crashed | AVAILABILITY (INFERRED; not on S-AVAIL page) | broker JVM crash |
| `PROCESS_HIGH_GC_ACTIVITY` | Long garbage-collection time | RESOURCE_CONTENTION (D, [S-RES] "Long garbage-collection time", GC time >40% or suspension >25%) | broker/consumer JVMs |
| `PROCESS_MEMORY_RESOURCE_EXHAUSTED` | Memory resources exhausted | RESOURCE_CONTENTION (INFERRED) | JVM heap |
| `PROCESS_THREADS_RESOURCE_EXHAUSTED` | Threads resources exhausted | RESOURCE_CONTENTION (INFERRED) | |
| `PROCESS_NA_HIGH_CONN_FAIL_RATE` | Connectivity problem | ERROR (INFERRED) | clients failing to connect to brokers |
| `PROCESS_NA_HIGH_LOSS_RATE` | Network problem | ERROR (INFERRED) | |
| `SERVICE_ERROR_RATE_INCREASED` | Failure rate increase | ERROR (INFERRED) | producer/consumer services |
| `SERVICE_SLOWDOWN` | Response time degradation | SLOWDOWN (D, [S-SLOW] "Response time degradation") | |
| `SERVICE_UNEXPECTED_HIGH_LOAD` | Unexpected high load | RESOURCE_CONTENTION (D for "Unexpected high traffic" under Resource, [S-RES]; service variant INFERRED) | |
| `SERVICE_UNEXPECTED_LOW_LOAD` | Unexpected low load | AVAILABILITY (D for "Unexpected low traffic" under Availability, [S-AVAIL]; service variant INFERRED) | consumer stops processing |
| `DATABASE_CONNECTION_FAILURE` | Failed database connects | ERROR (INFERRED) | |
| `MONITORING_UNAVAILABLE` | Monitoring unavailable | MONITORING_UNAVAILABLE (INFERRED) | |
| `APPLICATION_SLOWDOWN` | User action duration degradation | SLOWDOWN (D, [S-SLOW]; also S-SEM example) | n/a |
| `APPLICATION_ERROR_RATE_INCREASED` | JavaScript error rate increase | ERROR (D, [S-ERR]) | n/a |
| `APPLICATION_UNEXPECTED_HIGH_LOAD` / `_LOW_LOAD` | Unexpected high traffic / Unexpected low traffic | RESOURCE_CONTENTION / AVAILABILITY (D) | n/a |
| `SYNTHETIC_GLOBAL_OUTAGE` / `SYNTHETIC_LOCAL_OUTAGE` | Browser monitor global outage / local outage | AVAILABILITY (INFERRED from S-AVAIL synthetic outage rows) | |
| `HTTP_CHECK_GLOBAL_OUTAGE` / `HTTP_CHECK_LOCAL_OUTAGE` | HTTP monitor global outage / HTTP monitor local outage | AVAILABILITY (INFERRED) | REST proxy / Connect health checks |
| `HTTP_CHECK_TEST_LOCATION_SLOWDOWN` | HTTP monitor performance threshold violation | SLOWDOWN (INFERRED) | |
| `SYNTHETIC_TEST_LOCATION_SLOWDOWN` | Browser monitor performance threshold violation | SLOWDOWN (INFERRED) | |
| `MULTI_PROTOCOL_GLOBAL_OUTAGE` / `_LOCAL_OUTAGE` / `MULTI_PROTOCOL_LOCATION_SLOWDOWN` | Network availability monitor global outage / local outage / performance threshold violation | AVAILABILITY / SLOWDOWN (INFERRED) | TCP probe of broker port 9092 |
| `SYNTHETIC_NODE_OUTAGE` | Synthetic ActiveGate outage | AVAILABILITY (INFERRED) | |
| `SYNTHETIC_PRIVATE_LOCATION_OUTAGE` | Synthetic private location outage | AVAILABILITY (INFERRED) | |
| `EXTERNAL_SYNTHETIC_TEST_OUTAGE` / `_SLOWDOWN` | Third party monitor outage / slowdown | AVAILABILITY / SLOWDOWN (INFERRED) | |
| `EC2_HIGH_CPU` | AWS CPU saturation | RESOURCE_CONTENTION (INFERRED) | brokers on EC2 |
| `EBS_VOLUME_HIGH_LATENCY` | Elastic Block Storage has high latency. | RESOURCE_CONTENTION or SLOWDOWN (INFERRED, unclear) | broker EBS volumes |
| `AWS_LAMBDA_HIGH_ERROR_RATE` | Lambda high error rate | ERROR (D, [S-ERR]) | |
| `ELB_HIGH_BACKEND_ERROR_RATE` | Classic Load Balancer has a high backend connection error rate | ERROR (INFERRED) | |
| `ESXI_HOST_CPU_SATURATION`, `ESXI_HOST_MEMORY_SATURATION`, `ESXI_GUEST_CPU_LIMIT_REACHED` ("ESXi Guest CPU saturation"), `ESXI_GUEST_ACTIVE_SWAP_WAIT` ("ESXi Guest memory saturation"), `ESXI_HOST_DISK_SLOW` ("ESXi slow disk"), `ESXI_HOST_DISK_QUEUE_SLOW` ("I/O commands queued"), `ESXI_HOST_NETWORK_PROBLEMS` ("ESXi network problems"), `ESXI_HOST_OVERLOADED_STORAGE` ("Overloaded storage"), `ESXI_HOST_DATASTORE_LOW_DISK_SPACE` ("Low datastore space"), `ESXI_VM_IMPACT_HOST_CPU_SATURATION`, `ESXI_VM_IMPACT_HOST_MEMORY_SATURATION` | (titles in parentheses / as named) | RESOURCE_CONTENTION mostly (INFERRED) | Kafka on VMware VMs |
| `RDS_HIGH_CPU` ("RDS CPU saturation"), `RDS_LOW_MEMORY` ("RDS memory saturation"), `RDS_HIGH_LATENCY` ("High latency"), `RDS_LOW_STORAGE_SPACE` ("Low storage space"), `RDS_OF_SERVICE_UNAVAILABLE` ("Relational database service unavailable"), `RDS_RESTART_SEQUENCE` ("Restart sequence") | as named | mixed (D: Restart sequence = ERROR [S-ERR]; High latency = RESOURCE [S-RES]) | n/a |
| `MOBILE_APP_CRASH_RATE_INCREASED`, `MOBILE_APPLICATION_*`, `CUSTOM_APPLICATION_*`, `CUSTOM_APP_CRASH_RATE_INCREASED` | Mobile/custom app titles | n/a for Kafka | n/a |

**Catalog B — docs "event categories" codes (same detections, classic v1 naming). DOCUMENTED from the category pages:**
- Availability [S-AVAIL](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/event-analysis-and-correlation/event-categories/availability-events): "Unexpected low traffic" `UNEXPECTED_LOW_LOAD`; "Host or monitoring unavailable" `OSI_UNEXPECTEDLY_UNAVAILABLE` (host shut down abruptly / lost OneAgent connection >5 min; closes when available again or after 12 h; graceful OS shutdown → no problem); "Process unavailable" `PROCESS_UNAVAILABLE` (PGI) / `PROCESS_GROUP_LOW_INSTANCE_COUNT` (PG) — process-group availability monitoring is **off by default**, enabled per process group; closes when available again or after 5 days; "Synthetic monitor global outage" `WEB_CHECK_GLOBAL_OUTAGE`; "Synthetic monitor local outage" `WEB_CHECK_LOCAL_OUTAGE`; "Availability log pattern found" `HOST_LOG_AVAILABILITY` / `PROCESS_LOG_AVAILABILITY`; "Custom availability event" `AVAILABILITY_EVENT`.
- Error [S-ERR](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/event-analysis-and-correlation/event-categories/error-events): "JavaScript error rate increase" `JAVASCRIPT_ERROR_RATE_INCREASED`; "Mobile app crash rate increased" `MOBILE_APP_CRASH_RATE_INCREASED`; "High rate of dropped packets" `HIGH_DROPPED_PACKETS_RATE`; "High number of network errors" `HIGH_NETWORK_ERROR_RATE`; "Lambda high error rate" `LAMBDA_FUNCTION_HIGH_ERROR_RATE`; "Elastic load balancer has a high backend failure rate" `ELASTIC_LOAD_BALANCER_HIGH_BACKEND_FAILURE_RATE`; "Error log pattern found" `HOST_LOG_ERROR` / `PROCESS_LOG_ERROR`; "Custom error event" `ERROR_EVENT`; "Restart sequence" `RDS_RESTART_SEQUENCE`.
- Slowdown [S-SLOW](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/event-analysis-and-correlation/event-categories/slowdown-events): "User action duration degradation" `USER_ACTION_DURATION_DEGRADATION`; "Response time degradation" `SERVICE_RESPONSE_TIME_DEGRADED` (median or 90th percentile vs auto baseline or static threshold); "Performance log pattern found" `HOST_LOG_PERFORMANCE` / `PROCESS_LOG_PERFORMANCE`; "Synthetic monitor performance threshold violation" `SYNTHETIC_SLOWDOWN`; "Custom performance event" `PERFORMANCE_EVENT`.
- Resource [S-RES](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/event-analysis-and-correlation/event-categories/resource-events): "Unexpected high traffic" `UNEXPECTED_HIGH_LOAD`; "CPU saturation" `CPU_SATURATED` (avg CPU >95% in 18 of 30 ten-second intervals); "Memory saturation" `MEMORY_SATURATED` (Linux: >80% and >20 page faults/s; Windows: >90% and >100 page faults/s, 18 of 30); "Low disk space" `LOW_DISK_SPACE` (<3% free, 18 of 30); "Slow disk" `SLOW_DISK` (>200 ms read/write); "Low number of inodes available" `HOST_DISK_LOW_INODES` (<5%); "High network utilization" `HIGH_NETWORK_UTILIZATION` (>90%); "Long garbage-collection time" `HIGH_GC_ACTIVITY` (GC time >40% or suspension >25%); "High latency" `HIGH_LATENCY`; "I/O commands queued" `INSUFFICIENT_DISK_QUEUE_DEPTH`; "Custom resource contention event" `RESOURCE_EVENT`.
- Info [S-INFO](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/event-analysis-and-correlation/event-categories/info-events): "Annotation" `CUSTOM_ANNOTATION`; "Deployment" `CUSTOM_DEPLOYMENT`; "Info" `CUSTOM_INFO`; "Log pattern matched on host or process" `LOG_MATCHED`; "Marked for termination" `MARKED_FOR_TERMINATION` (suppresses host-unavailability problems for 60 min); "JavaScript framework changes" `APPLICATION_JS_FRAMEWORK_DETECTED`; "Out of memory kill" — container, OOM killer stopped the container; **raises no alert by itself; no type code listed**.
- DOCUMENTED: classic v1 `eventType` enum (S-ENV1) additionally contains: `CONNECTION_LOST`, `FAILURE_RATE_INCREASED`, `HIGH_CONNECTIVITY_FAILURES`, `HIGH_NETWORK_LOSS_RATE`, `HOST_CONNECTION_FAILED`, `HOST_CONNECTION_LOST`, `HOST_GRACEFULLY_SHUTDOWN`, `HOST_LOG_MATCHED`, `HOST_MAINTENANCE`, `HOST_NO_CONNECTION`, `HOST_OF_SERVICE_UNAVAILABLE`, `HOST_SHUTDOWN`, `HOST_TIMEOUT`, `LOG_AVAILABILITY`, `LOG_ERROR`, `LOG_PERFORMANCE`, `LOW_STORAGE_SPACE`, `MEMORY_RESOURCES_EXHAUSTED`, `MONITORING_UNAVAILABLE`, `OSI_DOCKER_DEVICEMAPPER_LOW_DATA_SPACE`, `OSI_DOCKER_DEVICEMAPPER_LOW_METADATA_SPACE`, `OVERLOADED_STORAGE`, `PGI_CRASHED_INFO`, `PGI_LOG_MATCHED`, `PGI_MEMDUMP`, `PGI_OF_SERVICE_UNAVAILABLE`, `PGI_RMQ_HIGH_MEM_USAGE` (and other `PGI_RMQ_*`, `PGI_HAPROXY_*`, `PGI_MYSQL_SLOW_QUERIES_RATE_HIGH`), `PROCESS_CRASHED`, `PROCESS_CUSTOM_AVAILABILITY`, `PROCESS_CUSTOM_ERROR`, `PROCESS_CUSTOM_PERFORMANCE`, `PROCESS_RESTART`, `PROCESS_UNAVAILABLE`, `RESOURCE_CONTENTION`, `SERVICE_RESPONSE_TIME_DEGRADED`, `THREADS_RESOURCES_EXHAUSTED`, `UNEXPECTED_HIGH_LOAD`, `UNEXPECTED_LOW_LOAD`, `DEPLOYMENT_CHANGED_CHANGE/NEW/REMOVED`, `ESXI_VM_*`, `OPENSTACK_*`, `RDS_*`, `VIRTUAL_MACHINE_SHUTDOWN`, `CPU_SATURATED`, `MEMORY_SATURATED`, `SLOW_DISK`, `LOW_DISK_SPACE`, `HIGH_GC_ACTIVITY` — **classic-only (API v1, deprecated)** — [S-ENV1](https://github.com/Dynatrace-ESA/dynatrace-api-client/blob/main/src/api/generated/env-v1.ts)
- DOCUMENTED: Events API v2 `GET /eventTypes/{eventType}` returns `type` and a `severityLevel` with values `AVAILABILITY, CUSTOM_ALERT, ERROR, INFO, MONITORING_UNAVAILABLE, PERFORMANCE, RESOURCE_CONTENTION`; the documented sample uses OSI-style naming with severity `PERFORMANCE`; `GET /eventTypes` lists all types that can be raised in the environment — [S-API2-ET](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/events-v2/get-event-type)

**Items the brief asked about that I could NOT find as verbatim type names**
- `SERVICE_UNAVAILABLE`, `FAILURE_RATE_INCREASED` (exists only in classic v1 enum; v2 name is `SERVICE_ERROR_RATE_INCREASED`), `RESPONSE_TIME_DEGRADED` (v2 is `SERVICE_SLOWDOWN`, docs code `SERVICE_RESPONSE_TIME_DEGRADED`), `SERVICE_LOAD_DROP`/`SPIKE` (actual: `SERVICE_UNEXPECTED_LOW_LOAD`/`SERVICE_UNEXPECTED_HIGH_LOAD`), `CUSTOM_AVAILABILITY`/`CUSTOM_ERROR` (actual: `AVAILABILITY_EVENT`/`ERROR_EVENT`; classic `PROCESS_CUSTOM_AVAILABILITY`/`PROCESS_CUSTOM_ERROR`), `ESTIMATED_TIME_TO_DISK_FULL`, `JVM_GARBAGE_COLLECTION` (actual: `PROCESS_HIGH_GC_ACTIVITY` / `HIGH_GC_ACTIVITY`), `MEMORY_RESOURCES_EXHAUSTED` (classic; v2 `PROCESS_MEMORY_RESOURCE_EXHAUSTED`), `K8S_POD_*` / `K8S_CONTAINER_OOM_KILLED` event types — none found verbatim. Do not use them.

### Inferences
- INFERRED: In Grail, built-in host/process detections use the v2/alerting-profile names (`OSI_*`, `PGI_*`, `PG_LOW_INSTANCE_COUNT`, `PROCESS_*`, `SERVICE_*`). Only `OSI_HIGH_CPU`/`OSI_HIGH_MEMORY` are directly confirmed in a Grail query example; the rest are inferred by analogy (same naming family in the Events v2/alerting-profile enum).
- INFERRED: "Raises a problem by default" — all AVAILABILITY/ERROR/SLOWDOWN/RESOURCE_CONTENTION/CUSTOM_ALERT/MONITORING_UNAVAILABLE events do (subject to merging/suppression); INFO and WARNING never do ([S-CAT] states only the latter explicitly). Notable default-off detectors: process availability monitoring (S-AVAIL), unexpected high traffic (opt-in, S-RES).
- INFERRED for Kafka eval data: the most realistic built-in problems in a Kafka estate are `OSI_LOW_DISK_SPACE`, `OSI_HIGH_CPU`, `OSI_HIGH_MEMORY`, `PROCESS_HIGH_GC_ACTIVITY`, `PGI_UNAVAILABLE`/`PG_LOW_INSTANCE_COUNT` (if availability monitoring enabled on the broker PG), `PROCESS_CRASHED`, `OSI_UNEXPECTEDLY_UNAVAILABLE`, `PROCESS_NA_HIGH_CONN_FAIL_RATE`, `SERVICE_ERROR_RATE_INCREASED`/`SERVICE_SLOWDOWN` on producer/consumer services, plus K8s detector events (§5) and CUSTOM_ALERT metric events on Kafka metrics (§4).

### Gaps
- No public, authoritative list mapping every Grail `event.type` to `event.category`; `GET /api/v2/eventTypes` on a live tenant would settle it.
- Whether Grail stores the classic `CPU_SATURATED`-style codes anywhere (docs category pages show them) vs. only `OSI_*` — sources conflict; DQL example supports `OSI_*`.
- The `event.type` value emitted by the Kubernetes anomaly detectors (provider `KUBERNETES_ANOMALY_DETECTION`) is not documented; likely one of the generic types (`ERROR_EVENT`, `RESOURCE_CONTENTION_EVENT`, `AVAILABILITY_EVENT`) — INFERRED.

---

## 2. Problem titles when several events are merged

### Takeaway
A problem's `event.name` is the event name when it consists of one event (e.g., "CPU saturation"); when Davis merges several events, the title becomes a generic "Multiple … problems" label — `Multiple infrastructure problems` is documented as an example. Titles can change over the problem's life as events are added.

### Cited Findings
- DOCUMENTED: `dt.davis.problems.event.name` examples: `CPU saturation`, `Multiple infrastructure problems`; `event.kind` = `DAVIS_PROBLEM`; `display_id` like `P-2307288`; `event.description` combines descriptions of the problem's events; `dt.davis.event_ids` array lists member events — [S-SEM](https://docs.dynatrace.com/docs/discover-dynatrace/references/semantic-dictionary/model/davis)
- DOCUMENTED: Problem takes the highest severity (category) of its constituent events and can escalate, e.g., start as slowdown and escalate to availability — [S-CAT](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/event-analysis-and-correlation/event-categories)
- DOCUMENTED: "Multiple infrastructure problems" appears in practice; alerting-profile filters work on events, not on the problem title — [Community](https://community.dynatrace.com/t5/Alerting/Filtering-out-problems-with-title-quot-Multiple-infrastructure/td-p/241149); ServiceNow users see one problem covering multiple hosts — [Community](https://community.dynatrace.com/t5/Open-Q-A/ServiceNow-Integration-how-to-handle-the-Multiple-Infrastructure/m-p/194362)
- DOCUMENTED: The problem title can change over time — [Community thread](https://community.dynatrace.com/t5/Dynatrace-API/The-title-of-the-Problem-changes-over-time/m-p/95661)
- DOCUMENTED: Merge rules: overlapping active windows (starts roughly within ~3 min), same source entity, same vertical stack (process on host) or horizontal (service calls service), and `dt.davis.is_merging_allowed` true — [S-AI-PROB merging](https://github.com/Dynatrace/dynatrace-for-ai/blob/main/skills/dt-obs-problems/references/problem-merging.md)
- DOCUMENTED: `dt.davis.impact_level` values `Application, Environment, Infrastructure, Services, Synthetic` — [S-SEM](https://docs.dynatrace.com/docs/discover-dynatrace/references/semantic-dictionary/model/davis)

### Inferences
- INFERRED: The "Multiple X problems" word follows the impact level: "Multiple infrastructure problems", "Multiple service problems", "Multiple application problems", "Multiple environment problems". Only the infrastructure variant is verified verbatim. For realistic eval data, a Kafka broker host with CPU + GC + disk events merged would be titled "Multiple infrastructure problems"; a single-event problem keeps the event title ("Low disk space").
- INFERRED: When events of the same type merge on several entities (e.g., CPU saturation on 3 brokers), the title often stays the single event name — not confirmed.

### Gaps
- No official doc describing the title-selection algorithm or the full list of "Multiple …" strings.

---

## 3. Problem severity: classic severityLevel vs impactLevel; Grail event.severity

### Takeaway
Classic APIs have **no numeric severity**: `severityLevel` is the category (AVAILABILITY, ERROR, PERFORMANCE, RESOURCE_CONTENTION, CUSTOM_ALERT, MONITORING_UNAVAILABLE, INFO) and `impactLevel` is the affected layer (APPLICATION/SERVICES/INFRASTRUCTURE/ENVIRONMENT). **Grail does have a numeric `event.severity` 1–5** (1 = most severe, ITIL-aligned) on both events and problems, but it is optional/nullable — null when no constituent event sets it.

### Cited Findings
- DOCUMENTED: `event.severity` (long) 1–5, 1 most severe, ITIL-aligned, on `dt.davis.events` and `dt.davis.problems`; problem severity = minimum across its events; null if no event has a severity — [S-SEM](https://docs.dynatrace.com/docs/discover-dynatrace/references/semantic-dictionary/model/davis)
- DOCUMENTED: Mapping 1 Critical, 2 High, 3 Medium, 4 Low, 5 Informational; detector config should set it explicitly, "do not rely on a platform default" — [S-AI-EVENTS](https://github.com/Dynatrace/dynatrace-for-ai/blob/main/skills/dt-alerting/references/davis-events.md)
- DOCUMENTED: Dynatrace's own problems skill warns that the field to use for problem type is `event.category`, listing `severity` as a "wrong field name" — [S-AI-PROB](https://github.com/Dynatrace/dynatrace-for-ai/blob/main/skills/dt-obs-problems/SKILL.md)
- DOCUMENTED: Event-type `severityLevel` enum `AVAILABILITY, CUSTOM_ALERT, ERROR, INFO, MONITORING_UNAVAILABLE, PERFORMANCE, RESOURCE_CONTENTION` — [S-API2-ET](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/events-v2/get-event-type)
- DOCUMENTED: classic v1 client includes impact enum `APPLICATION, ENVIRONMENT, INFRASTRUCTURE, SERVICE` — [S-ENV1](https://github.com/Dynatrace-ESA/dynatrace-api-client/blob/main/src/api/generated/env-v1.ts)
- DOCUMENTED: Kubernetes detectors carry a classification "Health alert" vs "Warning signal" — [S-K8S-APP](https://docs.dynatrace.com/docs/observe/infrastructure-monitoring/container-platform-monitoring/kubernetes-app/reference/anomaly-detectors)

### Inferences
- INFERRED (from memory of Problems API v2, not re-fetched): Problems API v2 objects have `severityLevel` (same category enum) and `impactLevel` (`APPLICATION, ENVIRONMENT, INFRASTRUCTURE, SERVICES`), status `OPEN/CLOSED`. Note the API's `PERFORMANCE` corresponds to Grail `SLOWDOWN`.
- INFERRED: Built-in detections (OSI_HIGH_CPU etc.) probably do not populate `event.severity` today, so many real problems have null severity. For eval generation, either leave it null for built-ins or derive it from category; don't present it as always-present.

### Gaps
- Which built-in detectors (if any) set `event.severity` by default is not documented.

---

## 4. Metric events (custom alerts / anomaly detectors) — e.g., Kafka consumer lag, under-replicated partitions

### Takeaway
A user-defined metric event's problem title is the **user-configured title verbatim** (`eventTemplate.title`); placeholders are documented for the description. Event type is chosen from `AVAILABILITY, CUSTOM_ALERT, CUSTOM_ANNOTATION, CUSTOM_CONFIGURATION, CUSTOM_DEPLOYMENT, ERROR, INFO, MARKED_FOR_TERMINATION, RESOURCE, SLOWDOWN, WARNING`, which determines the category. Models: static threshold, auto-adaptive threshold, seasonal baseline. Provider = `METRIC_EVENTS`.

### Cited Findings
- DOCUMENTED: Title = "a short, easy-to-read string describing the situation", examples "High network activity", "CPU saturation"; placeholders: `{alert_condition}` (above/below), `{baseline}`, `{dims}` and `{dims:dt.entity.<entity>}`, `{entityname}`, `{metricname}`, `{missing_data_samples}`, `{severity}`, `{threshold}`; merge strategy: if allowed, Davis tries to merge into existing problems, else new problem each time; Violating samples / Sliding window (max 60 one-minute samples) / Dealerting samples — [S-MKE](https://docs.dynatrace.com/docs/dynatrace-intelligence/anomaly-detection/metric-events/metric-key-events)
- DOCUMENTED: `{alert_condition}` renders as `ABOVE`/`BELOW` — [Community](https://community.dynatrace.com/t5/Alerting/What-are-the-available-placeholders-for-event-description-while/td-p/126394); example description "The {metricname} value of {severity} was {alert_condition} the baseline of {baseline}" — [Terraform custom_anomalies](https://registry.terraform.io/providers/dynatrace-oss/dynatrace/latest/docs/resources/custom_anomalies)
- DOCUMENTED: community experts say dimension values can't be put in the *title* (older thread) — [Community](https://community.dynatrace.com/t5/Alerting/Create-custom-event-for-alerting-title-with-parameters/m-p/178261)
- DOCUMENTED: Monitoring strategies Static threshold / Auto-adaptive threshold / Seasonal baseline; auto-adaptive & seasonal only for metric-selector events; ≤10,000 configs; event raised on environment if no entity, on the entity if one, most relevant if several (process preferred over host) — [S-ME](https://docs.dynatrace.com/docs/shortlink/metric-events)
- DOCUMENTED: Settings enums — `EventTypeEnum`: `AVAILABILITY, CUSTOM_ALERT, CUSTOM_ANNOTATION, CUSTOM_CONFIGURATION, CUSTOM_DEPLOYMENT, ERROR, INFO, MARKED_FOR_TERMINATION, RESOURCE, SLOWDOWN, WARNING`; `AlertCondition`: `ABOVE, BELOW, OUTSIDE`; `ModelType` includes `AUTO_ADAPTIVE_THRESHOLD, SEASONAL_BASELINE` (+ static); Terraform example `event_type = "CUSTOM_ALERT"`, `title = "sample_name"` — [S-TF-ME](https://github.com/dynatrace-oss/terraform-provider-dynatrace/blob/main/dynatrace/api/builtin/anomalydetection/metricevents/settings/enums.go)
- DOCUMENTED: `CUSTOM_ALERT` = "a simple way of defining a threshold on a given metric"; alert references the component showing the violation — [Custom alerts page](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/event-analysis-and-correlation/event-categories/custom-alerts)
- DOCUMENTED: Recommended naming `{Component} {Condition} {Direction/Threshold}`, e.g. "Payment Service Error Rate High", "Kubernetes Node CPU Saturated"; keep dynamic values out of the title — [S-AI-EVENTS](https://github.com/Dynatrace/dynatrace-for-ai/blob/main/skills/dt-alerting/references/davis-events.md)
- DOCUMENTED (Kafka): Kafka JMX extension metrics: `kafka.server.ReplicaManager.UnderReplicatedPartitions`, `kafka.server.ReplicaManager.UnderMinIsrPartitionCount`, `kafka.controller.KafkaController.OfflinePartitionsCount`, `kafka.controller.KafkaController.ActiveControllerCount.Value`, `kafka.consumer.consumer-metrics.records-lag` (+ `records-lag-avg`, `records-lag-max`), `kafka.producer.producer-metrics.request-latency-avg`, `kafka.controller.ControllerStats.UncleanLeaderElectionsPerSec.OneMinuteRate`; extension ships alerts covering "broker availability, cluster controllers, offline partitions, leader elections, and replicated partitions" (exact titles not published) — [S-KAFKA](https://docs.dynatrace.com/docs/ingest-from/extensions/supported-extensions/apache-kafka)

### Inferences
- INFERRED example (realistic, not from a source): title `Kafka under-replicated partitions`, event type `ERROR` (→ category ERROR) or `CUSTOM_ALERT`, description `{metricname} was {alert_condition} {threshold} on {dims:dt.entity.process_group_instance}`, static threshold > 0 for 5 of 5 samples; title `Kafka consumer lag high`, `RESOURCE` or `CUSTOM_ALERT`, auto-adaptive threshold on `records-lag-max` split by `client-id`/`topic`. In Grail: `event.kind=DAVIS_EVENT`, `event.provider=METRIC_EVENTS`, `event.type=CUSTOM_ALERT` (for CUSTOM_ALERT template), `event.category=CUSTOM_ALERT`.
- INFERRED: template type `RESOURCE` → Grail `event.type=RESOURCE_CONTENTION_EVENT`, `event.category=RESOURCE_CONTENTION`; `ERROR`→`ERROR_EVENT`/`ERROR`; `AVAILABILITY`→`AVAILABILITY_EVENT`/`AVAILABILITY`; `SLOWDOWN`→`PERFORMANCE_EVENT`/`SLOWDOWN`. Mapping not stated verbatim.
- INFERRED: newer DQL-based "Anomaly Detection" (Davis anomaly detectors app) also uses `eventTemplate` with `event.name` from title (S-AI-EVENTS says event.name is set by `eventTemplate.title`).

### Gaps
- No verbatim Kafka extension alert titles or thresholds found (checked docs and GitHub).
- Default auto-adaptive/seasonal parameters (number of signal fluctuations, tolerance) not captured.

---

## 5. Log events

### Takeaway
Log events are user-configured (Settings > Log events / `builtin:logmonitoring.log-events`) with the same event-type enum as metric events; the title can embed log attributes (e.g., `{content}`); provider is `LOG_EVENTS`. Classic built-in log-pattern events existed as `HOST_LOG_ERROR`, `PROCESS_LOG_ERROR`, etc. ("Error log pattern found").

### Cited Findings
- DOCUMENTED: `dynatrace_log_events` event_template with `event_type` options `AVAILABILITY, CUSTOM_ALERT, CUSTOM_ANNOTATION, CUSTOM_CONFIGURATION, CUSTOM_DEPLOYMENT, ERROR, INFO, MARKED_FOR_TERMINATION, RESOURCE, SLOWDOWN, WARNING`; example `event_type = "INFO"`, `title = "{content}"` — [S-TF-LE](https://github.com/dynatrace-oss/terraform-provider-dynatrace/blob/main/docs/resources/log_events.md)
- DOCUMENTED: `event.provider` value `LOG_EVENTS` — [S-SEM](https://docs.dynatrace.com/docs/discover-dynatrace/references/semantic-dictionary/model/davis)
- DOCUMENTED (classic): "Error log pattern found" `HOST_LOG_ERROR`/`PROCESS_LOG_ERROR` (ERROR); "Availability log pattern found" `HOST_LOG_AVAILABILITY`/`PROCESS_LOG_AVAILABILITY`; "Performance log pattern found" `HOST_LOG_PERFORMANCE`/`PROCESS_LOG_PERFORMANCE`; "Log pattern matched on host or process" `LOG_MATCHED` (INFO) — [S-ERR], [S-AVAIL], [S-SLOW], [S-INFO] (URLs above)

### Inferences
- INFERRED Kafka examples: log event title `Kafka broker ISR shrink` on matcher `content contains "Shrinking ISR"`, type ERROR; `Kafka consumer rebalance storm` type WARNING/INFO. A log-metric + metric event is the alternative path (title from metric event).

### Gaps
- Exact Grail `event.type` for log events (likely `ERROR_EVENT` etc.) not documented in sources found.

---

## 6. Kubernetes problems relevant to Kafka on k8s

### Takeaway
Kubernetes anomaly detection (provider `KUBERNETES_ANOMALY_DETECTION`) produces events with fixed, documented titles: "Container restarts", "Out-of-memory kills", "Pods stuck in pending", "No pod ready", "Not all pods ready", "Backoff event", "Pod eviction event", "Kubernetes PVC: Low disk space %", etc. There is no separate CrashLoopBackOff event type — it surfaces as "Container restarts" / "Backoff event".

### Cited Findings (all DOCUMENTED — [S-K8S](https://docs.dynatrace.com/docs/observe/infrastructure-monitoring/container-platform-monitoring/kubernetes-monitoring/alert-on-kubernetes-issues); defaults/classification from [S-K8S-APP](https://docs.dynatrace.com/docs/observe/infrastructure-monitoring/container-platform-monitoring/kubernetes-app/reference/anomaly-detectors))

| Scope | Problem title (event.name) | Problem type | Description | Default | Class |
|---|---|---|---|---|---|
| Workload | Container restarts | Error | "Observed container restarts exceed the specified threshold." | ≥1 restart/3 min within 5 min, enabled | Warning signal |
| Workload | Out-of-memory kills | Error | "Out-of-memory kills have been observed for pods of this workload." | event-based, enabled | Warning signal |
| Workload | Pods stuck in pending | Resource | "Workload has pending pods." | ≥1 pod ≥10 min, enabled | Warning signal |
| Workload | Pods stuck in terminating | Resource | "Workload has pods stuck in terminating." | enabled | Warning signal |
| Workload | No pod ready | Error | "Workload does not have any ready pods." | ≥10 min, disabled by default | Health alert |
| Workload | Not all pods ready | Error | "Workload has pods that are not ready." | >30% non-ready, disabled | Warning signal |
| Workload | Deployment stuck | Error | "Deployment is stuck and therefore is no longer progressing." | disabled | Health alert |
| Workload | Backoff event | Error | "Events with reason 'BackOff' have been detected for pods of this workload." | disabled | Health alert |
| Workload | Pod eviction event | Error | "Events with reason 'Evicted' have been detected..." | disabled | Health alert |
| Workload | Preemption event | Error | "Events with reasons 'Preempted' or 'Preempting'..." | enabled | Warning signal |
| Workload | Job failure event | Error | reasons BackoffLimitExceeded/DeadlineExceeded/PodFailurePolicy | enabled | Warning signal |
| Workload | Memory usage close to limits | Resource | working set > threshold of memory limit | >90% ≥10 min, disabled | Warning signal |
| Workload | CPU usage close to limits | Resource | CPU usage vs limit | disabled | Warning signal |
| Workload | High CPU throttling | Resource | throttling-to-limits ratio | disabled | Warning signal |
| PVC | Kubernetes PVC: Low disk space % / Kubernetes PVC: Low disk space | Resource | "Available disk space for a persistent volume claim is below the threshold." | <3% enabled / ≤100 MiB disabled | Warning signal |
| Node | Node not ready / Problematic node condition / CPU-request saturation on node / Memory-request saturation on node / Pod saturation on node | Availability / Error / Resource | | | Warning signal |
| Cluster | Cluster not ready / Monitoring not available / CPU-request saturation on cluster / Memory-request saturation on cluster / Pod saturation on cluster | Availability / Resource | "Readyz endpoint indicates that this cluster is not ready." | | Cluster not ready = Health alert |
| Namespace | CPU-request quota saturation / CPU-limit quota saturation / Memory-request quota saturation / Memory-limit quota saturation / Pod quota saturation | Resource | | disabled | Warning signal |

- DOCUMENTED: "Out of memory kill" (container) info event exists but raises no alert by itself — [S-INFO](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/event-analysis-and-correlation/event-categories/info-events)
- DOCUMENTED: Manually closed K8s problems reappear after 60 days if root cause remains — [S-K8S](https://docs.dynatrace.com/docs/observe/infrastructure-monitoring/container-platform-monitoring/kubernetes-monitoring/alert-on-kubernetes-issues)
- DOCUMENTED: Smartscape source types like `K8S_POD`, `K8S_DEPLOYMENT` used in `dt.smartscape_source.type` — [S-AI-EVENTS](https://github.com/Dynatrace/dynatrace-for-ai/blob/main/skills/dt-alerting/references/davis-events.md)

### Inferences
- INFERRED: "Warning signal" detectors in the updated classification map to WARNING (no problem) while "Health alert" ones open problems — S-K8S-APP implies this but does not state it verbatim. For realistic Kafka-on-k8s (Strimzi StatefulSet) eval problems: "Container restarts" on `kafka-broker` StatefulSet, "Out-of-memory kills", "Pods stuck in pending" (PVC/node capacity), "Kubernetes PVC: Low disk space %" on broker data volumes, "Not all pods ready"/"No pod ready", "Memory usage close to limits".
- INFERRED: `event.category` for these = ERROR / RESOURCE_CONTENTION / AVAILABILITY according to the "Problem type" column.

### Gaps
- Grail `event.type` value for K8s detector events not documented.

---

## 7. Documented Kafka-specific problem titles

### Takeaway
I found **no** documented Dynatrace problem title strings such as "Kafka broker unavailable" or "Consumer lag high". The Kafka extension's alerts are described only by subject area. Kafka problems in practice come from generic built-in titles or user-named metric/log events.

### Cited Findings
- DOCUMENTED: Kafka extension alerts cover "broker availability, cluster controllers, offline partitions, leader elections, and replicated partitions" (no titles/thresholds published); extension is JMX-based (requires OneAgent "Java Metric Extensions 2.0 (JMX)") — [S-KAFKA](https://docs.dynatrace.com/docs/ingest-from/extensions/supported-extensions/apache-kafka)
- DOCUMENTED: Confluent Cloud extension is Prometheus-based (Metrics Export API), lists no built-in alerts — [Kafka/Confluent page](https://docs.dynatrace.com/docs/ingest-from/technology-support/dynatrace-extensions/supported-out-of-the-box/kafka)
- DOCUMENTED (search snippet only, page not fully verified): classic Kafka plugin docs mention an error event when multiple controllers are detected and adjusting thresholds via Settings > Anomaly detection > Extension events — [Kafka monitoring shortlink](https://docs.dynatrace.com/docs/shortlink/kafka)

### Inferences
- INFERRED realistic titles for eval generation (label as synthetic): built-in — "Low disk space", "CPU saturation", "Memory saturation", "Long garbage-collection time", "Process unavailable", "Process crashed", "Host or monitoring unavailable", "Connectivity problem", "Failure rate increase", "Response time degradation", "Unexpected low load", "Multiple infrastructure problems"; K8s — "Container restarts", "Out-of-memory kills", "Pods stuck in pending", "Kubernetes PVC: Low disk space %"; user-defined — "Kafka under-replicated partitions", "Kafka offline partitions", "Kafka active controller count not 1", "Kafka consumer lag high".

### Gaps
- Exact Kafka extension alert titles and thresholds (would need extension YAML from Dynatrace Hub or a tenant).
