# Normalize four Dynatrace shapes into one problem

A Kafka incident reaches the triage app in four different shapes, and they disagree on almost every enum. Two of the paths deliver the same object. The Grail `dt.davis.problems` record is also exactly what the Workflow problem trigger hands to `event()`. That record has flat dotted keys, `event.status` ACTIVE/CLOSED, `event.category` with `SLOWDOWN`, a nullable numeric `event.severity` where **1 is most severe**, and Smartscape entity records `{id, type, name}`. Problems API v2 and the classic custom-integration webhook use an older vocabulary: OPEN/CLOSED or OPEN/RESOLVED, `PERFORMANCE` instead of `SLOWDOWN`, `SERVICES` in v2 versus `SERVICE` in the webhook, uppercase entity types, millisecond epochs, and no numeric severity at all. Only v2 (and the webhook's `{ProblemDetailsJSONv2}` placeholder) carries structured `evidenceDetails` and `impactAnalysis`. Root cause is absent on most real problems. MERGED is no longer a documented webhook state. Dynatrace publishes no Kafka-specific problem titles. Kafka problems surface either under generic built-in titles ("Low disk space", "Process unavailable", "Long garbage-collection time", "Multiple infrastructure problems"), as Kubernetes detector titles ("Container restarts", "Out-of-memory kills"), or as user-named metric events on JMX keys such as `kafka.server.ReplicaManager.UnderReplicatedPartitions`. So the fixtures need four adapter-specific shapes of the *same* incident, a canonical contract that keeps evidence, nullable severity and optional root cause, and eval labels driven by the Kafka signature (metrics plus log lines). The Dynatrace title alone is not enough to pick a label. Throughout, **DOCUMENTED** means a cited source states the value. **OBSERVED** means a third-party live-tenant sample. **INFERRED** means a realistic reconstruction you should verify against a tenant.

## Grail and Workflows share one record with flat dotted keys

`fetch dt.davis.problems` is a **deduplicated latest-state view, one row per problem**. Per-update history lives in `dt.davis.problems.snapshots`, which writes a new immutable record whenever affected entities, category or severity change ([Davis semantic dictionary](https://docs.dynatrace.com/docs/semantic-dictionary/model/davis)). Each record carries the following DOCUMENTED fields:

- `event.kind = "DAVIS_PROBLEM"` and a stable `event.id` such as `5547782627070661074_1647601320000`. Newer examples add a `V2` suffix and can be negative, e.g. `-8924735682950986450_1773318186796V2`.
- A human `display_id` such as `P-2307288`.
- `event.status`: `ACTIVE` | `CLOSED`.
- An experimental `event.status_transition`: `CREATED`, `UPDATED`, `REFRESHED`, `RESOLVED`, `REOPENED`, `CLOSED`.
- Six problem categories: `AVAILABILITY`, `ERROR`, `SLOWDOWN`, `RESOURCE_CONTENTION`, `CUSTOM_ALERT`, `MONITORING_UNAVAILABLE`. `INFO` exists only on events.
- `event.severity`, a long from 1 to 5. A problem takes the **minimum** across its events and is **null if no event sets one**.
- System fields: `dt.analysis.ready`, `dt.davis.event_ids`, `dt.davis.impact_level` (`Application`, `Environment`, `Infrastructure`, `Services`, `Synthetic`), `dt.davis.is_duplicate`, `duplicate_problem_ids`, `dt.davis.mute.status`, `dt.duration_marker` (`5 min` … `7 days`), `maintenance.is_under_maintenance` and `resolved_problem_duration`.
- Smartscape topology: `root_cause.smartscape_entity` and `smartscape.affected_entities` / `smartscape.related_entities`, all shaped like `{'id': 'HOST-1234567890ABCDEF', 'type': 'host', 'name': 'host-worker-097801ab8728e690a'}`. The classic `affected_entity_ids`, `affected_entity_types` (`dt.entity.host` style), `root_cause_entity_id` and `entity_tags` fields still appear but are **deprecated**, and `primary_tags.*` replaces tags.

All of this is from the [Davis semantic dictionary](https://docs.dynatrace.com/docs/semantic-dictionary/model/davis).

The model lists no `management_zones` field for problems. A practitioner validation found **0 of 15,878** problems carrying one over 30 days ([Best-Practice-Notebooks](https://github.com/timstewart-dynatrace/Best-Practice-Notebooks)).

Two sources conflict and fixtures must tolerate both readings:

- **Type of `dt.davis.impact_level`.** The dictionary types it as `string`. The practitioner notebook and the upgrade guide describe a list of every level seen ([upgrade guide](https://docs.dynatrace.com/docs/manage/upgrade-guide-landing-page/upgrade-guide-alert-notification)).
- **Casing of Smartscape `type`.** The dictionary shows lowercase `host`. The live sample shows `"type": "SERVICE"`.

The docs never show the Smartscape type string for a Kafka broker process (`process` vs `PROCESS` vs `process_group_instance`). It is undocumented.

The single most important field-presence observation comes from that validation tenant. **`root_cause.smartscape_entity` was present on 127 of 540 problems in 24 h**, and the deprecated `root_cause_entity_id` on 102 of 540. In the same sample, `event.end` and `resolved_problem_duration` appeared only on CLOSED records, and `event.severity` is "no longer defaulted from SaaS 1.348". A close can arrive with `event.status_transition` `RESOLVED` or `UPDATED` rather than `CLOSED`, so branch on `event.status` ([Best-Practice-Notebooks](https://github.com/timstewart-dynatrace/Best-Practice-Notebooks)). The only published near-real record is that notebook's abridged sample (OBSERVED, verbatim):

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

The Workflow Davis problem trigger "returns the problem record", and templates read "any field from the problem record, stored in `dt.davis.problems`" with bracket syntax such as `{{ event()["display_id"] }}` ([Workflow email tutorial](https://docs.dynatrace.com/docs/analyze-explore-automate/workflows/use-cases/workflows-tutorial-problems-email)). Because events are flattened, `event().a.b` errors. Use `event()['a.b']` or `event().get('a.b', default)`. `{{ problem_link() }}` produces the problem URL, and it works only under a Davis problem trigger ([Jinja reference](https://docs.dynatrace.com/docs/analyze-explore-automate/workflows/reference)). The record has **no `title`, `status`, `problem_url` or `start_time` aliases** ([Best-Practice-Notebooks](https://github.com/timstewart-dynatrace/Best-Practice-Notebooks)).

The trigger's filters map to stored config ([Terraform automation_workflow docs](https://raw.githubusercontent.com/dynatrace-oss/terraform-provider-dynatrace/main/docs/resources/automation_workflow.md)):

- `trigger_on`: `open`, `open-and-close` or `close`.
- `severity_threshold`: 1 = critical … 5 = informational. The UI applies it as "at or above".
- `categories`, `entity_tags`, `analysis_ready` ("wait for root cause analysis"), `custom_filter` and `problem_open_duration`.
- `trigger_on_update_fields`, drawn from `dt.davis.affected_users_count`, `dt.davis.impact_level`, `event.category`, `event.severity`, `root_cause_entity_id` and `smartscape.affected_entities`.

Without update fields, "the trigger starts once per state transition" ([event trigger](https://docs.dynatrace.com/docs/analyze-explore-automate/workflows/build/trigger/event-trigger)).

The upgrade guide states that **`ProblemDetailsJSON` and `ProblemDetailsJSONv2` "can't be reproduced from a workflow."** The trigger exposes only problem-record fields. To get evidence, the app must follow `dt.davis.event_ids` into `dt.davis.events` ([upgrade guide](https://docs.dynatrace.com/docs/manage/upgrade-guide-landing-page/upgrade-guide-alert-notification)). The docs do not say whether timestamps in the serialized `event()` payload arrive as ISO strings or ns integers. Also note that the dictionary's 14-digit `event.start` example `16481073970000` is not a valid ns value.

The reference Kafka fixture below is used across all four paths. It shows the Grail/`event()` shape for a broker process outage (field names DOCUMENTED, values INFERRED):

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
  "root_cause_entity_id": "PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10",
  "entity_tags": ["team:streaming", "app:kafka"]
}
```

A CLOSED variant adds `event.end`, `resolved_problem_duration` (ns, e.g. `1380000000000` = 23 min), `event.status: "CLOSED"`, and a transition of `RESOLVED`, `UPDATED` or `CLOSED`. `dt.davis.is_frequent_event` is documented only on events. The official MTTR query filters problems on it, though, so it likely appears on problems too ([Davis DQL examples](https://www.dynatrace.com/docs/dynatrace-intelligence/use-cases/davis-dql-examples)).

`dt.davis.events` records use `event.kind = "DAVIS_EVENT"` ([Davis semantic dictionary](https://docs.dynatrace.com/docs/semantic-dictionary/model/davis)). Fields that differ from problem records:

| Field | What differs |
|---|---|
| `event.type` | e.g. `OSI_HIGH_CPU`, `AVAILABILITY_EVENT` |
| `event.category` | Seven values, adding `INFO` |
| `event.provider` | `AVAILABILITY`, `LOG_EVENTS`, `METRIC_EVENTS`, `SYNTHETIC`, `EVENTS_REST_API_INGEST`, `KUBERNETES_ANOMALY_DETECTION`, `AGENT_LOCAL_REST_API_INGEST` |
| `event.status_transition` | A different enum: `CREATED`, `UPDATED`, `REFRESHED`, `TIMED_OUT`, `RECOVERED` |
| Source attribution | `dt.smartscape_source.id` / `.type` (e.g. `K8S_CLUSTER`), plus the deprecated `dt.source_entity` / `dt.source_entity.type` (`process_group_instance`, lowercase) |
| Config flags | e.g. `dt.davis.timeout` (default 15 min, cap 360), `dt.davis.is_merging_allowed` |
| `dt.davis.suppress_problem_reason` | Extra field on events |

The dictionary shows an event status example of `Active`, but every official query filters `== "ACTIVE"`. Use the uppercase form. Problem comments are themselves `CUSTOM_ANNOTATION` events.

## Problems API v2 is the only path that carries structured evidence

`GET /api/v2/problems` (scope `problems.read`) returns `{totalCount, pageSize, nextPageKey, problems[], warnings[]}`. Each `Problem` has these fields ([get-problems-list](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems-v2/problems/get-problems-list)):

- `problemId`, `displayId` and `title`.
- `status`: **`OPEN` | `CLOSED`**.
- `severityLevel`: a *category*, not a number. Values are `AVAILABILITY`, `CUSTOM_ALERT`, `ERROR`, `INFO`, `MONITORING_UNAVAILABLE`, **`PERFORMANCE`**, `RESOURCE_CONTENTION`.
- `impactLevel`: `APPLICATION`, `ENVIRONMENT`, `INFRASTRUCTURE`, **`SERVICES`**.
- `startTime` and `endTime` in ms. `endTime` is **-1 while open**.
- `affectedEntities`, `impactedEntities` and `rootCauseEntity`, each an `EntityStub` of `{entityId: {id, type}, name}`. `name` is omitted when the entity no longer exists.
- `entityTags` (`{context, key, value, stringRepresentation}`) and `managementZones`.
- `problemFilters`: the alerting profiles that match.
- `linkedProblemInfo`, `recentComments`, and the arrays `k8s.cluster.name`, `k8s.cluster.uid` and `k8s.namespace.name`.

`evidenceDetails`, `impactAnalysis` and `recentComments` appear only when requested via `fields=evidenceDetails,impactAnalysis`, and that request must be **repeated for every page**. A request with `nextPageKey` must omit all other params except `fields`. `pageSize` caps at 500, and `from` defaults to `now-2h`.

`problemSelector` supports these filters:

- `status("open")`
- `severityLevel(...)`, `impactLevel(...)`
- `affectedEntityTypes(...)`, `entityTags(...)`
- `managementZones(...)`, `underMaintenance(...)`
- `text("...")`, up to 30 chars

The schema example contains only `"string"`/`1` placeholder values. No real-valued v2 example is published.

Evidence is polymorphic on `evidenceType`: `AVAILABILITY_EVIDENCE`, `EVENT`, `MAINTENANCE_WINDOW`, `METRIC` or `TRANSACTIONAL` ([Problems API v2 models](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems-v2/models)). Every evidence item has the base fields `displayName`, `entity`, `groupingEntity`, `rootCauseRelevant` and `startTime`. The subtypes add:

| `evidenceType` | Extra fields |
|---|---|
| `EVENT` | `eventId`, `eventType`, `endTime` (-1 if active) and a `data` Event object |
| `METRIC` | `metricId`, `unit` (`Count`, `Percent`, `MilliSecond` …), `valueBeforeChangePoint`, `valueAfterChangePoint`, `endTime` (null = open) |
| `TRANSACTIONAL` | Same as `METRIC` but **no `metricId`** |
| `MAINTENANCE_WINDOW` | `maintenanceWindowConfigId` |

The doc's own EVENT example omits `data` and `endTime`, so fixtures should allow both to be absent.

Impacts carry `impactType` (`APPLICATION`, `CUSTOM_APPLICATION`, `MOBILE`, `SERVICE`), `impactedEntity` and `estimatedAffectedUsers`. SERVICE impacts add `numberOfPotentiallyAffectedServiceCalls`. The documented fragments are `{"evidenceType":"METRIC", ..., "metricId":"string", "valueBeforeChangePoint":2, "valueAfterChangePoint":3, "unit":"Count", "endTime":1602600000000}` and `{"impactType":"SERVICE", ..., "estimatedAffectedUsers":5, "numberOfPotentiallyAffectedServiceCalls":50}`. Real ID formats come from the deprecated v1 example: `2307087411653364173_1538400720000V2`, `PROCESS_GROUP-09875E82E2FB98FD`, `HOST-ED6BC51DEBA8093A`, and tags such as `{"context":"CONTEXTLESS","key":"loadtest"}` ([Problems API v1 details](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems/problems/get-details)).

Here is the same Kafka broker outage as a v2 response (shape DOCUMENTED, values INFERRED). The `eventType` string is deliberately one of two unresolved candidates; see the event-type section.

```json
{
  "totalCount": 1, "pageSize": 50,
  "problems": [{
    "problemId": "-4417092361180924518_1791814020000V2",
    "displayId": "P-261012042",
    "title": "Process unavailable",
    "status": "OPEN",
    "severityLevel": "AVAILABILITY",
    "impactLevel": "INFRASTRUCTURE",
    "startTime": 1791814020000,
    "endTime": -1,
    "affectedEntities": [{"entityId": {"id": "PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10", "type": "PROCESS_GROUP_INSTANCE"}, "name": "kafka-broker (kafka-2)"}],
    "impactedEntities": [{"entityId": {"id": "PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10", "type": "PROCESS_GROUP_INSTANCE"}, "name": "kafka-broker (kafka-2)"}],
    "rootCauseEntity": {"entityId": {"id": "PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10", "type": "PROCESS_GROUP_INSTANCE"}, "name": "kafka-broker (kafka-2)"},
    "managementZones": [{"id": "4567890123456789012", "name": "Kafka Prod"}],
    "entityTags": [{"context": "CONTEXTLESS", "key": "team", "value": "streaming", "stringRepresentation": "team:streaming"}],
    "problemFilters": [{"id": "c1b2a3d4-0000-4000-8000-000000000001", "name": "Kafka on-call"}],
    "evidenceDetails": {"totalCount": 2, "details": [
      {"evidenceType": "EVENT", "displayName": "Process unavailable",
       "entity": {"entityId": {"id": "PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10", "type": "PROCESS_GROUP_INSTANCE"}, "name": "kafka-broker (kafka-2)"},
       "groupingEntity": {"entityId": {"id": "PROCESS_GROUP-3D7A1C0E9B2F4A65", "type": "PROCESS_GROUP"}, "name": "kafka-broker"},
       "rootCauseRelevant": true, "startTime": 1791814020000, "endTime": -1,
       "eventId": "8302664511948770210_1791814020000", "eventType": "PGI_UNAVAILABLE"},
      {"evidenceType": "METRIC", "displayName": "Kafka broker - Under replicated partitions",
       "entity": {"entityId": {"id": "PROCESS_GROUP_INSTANCE-0B9C8D7E6F5A4B3C", "type": "PROCESS_GROUP_INSTANCE"}, "name": "kafka-broker (kafka-0)"},
       "groupingEntity": {"entityId": {"id": "PROCESS_GROUP-3D7A1C0E9B2F4A65", "type": "PROCESS_GROUP"}, "name": "kafka-broker"},
       "rootCauseRelevant": false, "startTime": 1791814080000,
       "metricId": "builtin:tech.kafka.pg.kafka.server.ReplicaManager.UnderReplicatedPartitions.Value",
       "unit": "Count", "valueBeforeChangePoint": 0, "valueAfterChangePoint": 118}
    ]},
    "impactAnalysis": {"impacts": []},
    "linkedProblemInfo": null,
    "k8s.cluster.name": ["prod-aks-weu"], "k8s.cluster.uid": [], "k8s.namespace.name": ["streaming"]
  }]
}
```

The docs never say whether unrequested or empty members (`linkedProblemInfo`, `k8s.*`, `impactAnalysis`) are omitted or null. Parse both.

### The classic webhook delivers strings, OPEN/RESOLVED and optionally the v2 object

The authoritative placeholder list is in the `builtin:problem.notifications` settings schema ([settings schema](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/schemas/builtin-problem-notifications)). The webhook and email body get the full set:

- `{PID}`: the "unique system identifier", which equals v2 `problemId` / Grail `event.id`.
- `{ProblemID}`: the "display number", which equals `displayId` / `display_id`.
- `{ProblemTitle}` and `{ProblemURL}`.
- **`{State}`: "OPEN or RESOLVED"**.
- **`{ProblemImpact}`: "APPLICATION, SERVICE, or INFRASTRUCTURE"**. That is singular `SERVICE`, and there is no ENVIRONMENT.
- **`{ProblemSeverity}`: "AVAILABILITY, ERROR, PERFORMANCE, RESOURCE_CONTENTION, or CUSTOM_ALERT"**.
- `{ImpactedEntities}`: a JSON array of `{type, name, entity}`.
- `{ImpactedEntity}`, `{ImpactedEntityNames}`, `{NamesOfImpactedEntities}` and `{Tags}`, a comma-separated string that also supports `{Tags[key]}`.
- `{ProblemDetailsText}`, `{ProblemDetailsMarkdown}` and `{ProblemDetailsHTML}`.
- `{ProblemDetailsJSON}`, which follows the deprecated v1 structure.
- **`{ProblemDetailsJSONv2}`, the v2 object "including evidenceDetails and impactAnalysis, but not recentComments"**.

Slack and the email subject get a short set without entities or details. The webhook settings object adds `notifyClosedProblems` (send RESOLVED), `notifyEventMergesEnabled` ("call webhook if new events merge into existing problems"), `headers`, OAuth2 and `payload`. JSON placeholders "must not have quotes around them" ([webhook integration](https://docs.dynatrace.com/docs/analyze-explore-automate/notifications-and-alerting/problem-notifications/webhook-integration)).

The documented template and the "Send test notification" rendering are verbatim (DOCUMENTED):

```json
{
  "ImpactedEntities": {ImpactedEntities},
  "ImpactedEntity": "{ImpactedEntity}",
  "PID": "{PID}",
  "ProblemDetailsHTML": "{ProblemDetailsHTML}",
  "ProblemDetailsJSON": {ProblemDetailsJSON},
  "ProblemID": "{ProblemID}",
  "ProblemImpact": "{ProblemImpact}",
  "ProblemTitle": "{ProblemTitle}",
  "Problem URL": "https://example.com",
  "State": "{State}",
  "Tags": "{Tags}"
}
```

```json
{
  "ImpactedEntities": [
    {"type": "HOST", "name": "MyHost1", "entity": "HOST-XXXXXXXXXXXXX" },
    {"type": "SERVICE", "name": "MyService1", "entity": "SERVICE-XXXXXXXXXXXXX"}
  ],
  "ImpactedEntity": "MyHost1, MyService1",
  "PID": "99999",
  "ProblemDetailsHTML": "<h1>Dynatrace problem notification test run details</h1>",
  "ProblemDetailsJSON": {"ID" : "99999" },
  "ProblemID": "999",
  "ProblemImpact": "INFRASTRUCTURE",
  "ProblemTitle": "Dynatrace problem notification test run",
  "Problem URL": "https://example.com",
  "State": "OPEN",
  "Tags": "testtag1, testtag2"
}
```

The test data has changed over time: a 2018–2021 Dynatrace blog logged `"ProblemID":"TESTID"` ([Dynatrace blog](https://www.dynatrace.com/news/blog/how-to-write-a-custom-dynatrace-problem-notification-integration/)). The app should recognise both test signatures and treat them as `suppress` (or as a connectivity check), never as an incident.

For a real problem the same Kafka outage would render roughly as follows (shape DOCUMENTED, values INFERRED; the `ProblemURL` format is invented because no real one is documented):

```json
{
  "ImpactedEntities": [{"type": "PROCESS_GROUP_INSTANCE", "name": "kafka-broker (kafka-2)", "entity": "PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10"}],
  "ImpactedEntity": "Process unavailable on kafka-broker (kafka-2)",
  "PID": "-4417092361180924518_1791814020000V2",
  "ProblemID": "P-261012042",
  "ProblemImpact": "INFRASTRUCTURE",
  "ProblemSeverity": "AVAILABILITY",
  "ProblemTitle": "Process unavailable",
  "ProblemURL": "https://abc12345.live.dynatrace.com/#problems/problemdetails;pid=-4417092361180924518_1791814020000V2",
  "State": "OPEN",
  "Tags": "team:streaming, app:kafka",
  "ProblemDetailsJSONv2": { "...": "the v2 Problem object above, without recentComments" }
}
```

**MERGED is not a documented state.** The current schema lists only OPEN and RESOLVED ([settings schema](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/schemas/builtin-problem-notifications)). Two community answers disagree, and both were seen only as search snippets:

- One says a post-open merge "sends out a MERGED state" ([community](https://community.dynatrace.com/t5/Alerting/Question-about-Problem-quot-MERGED-quot/m-p/213078)).
- The other says that after AI 2.0 "there is no MERGED state anymore, just open and close" ([community](https://community.dynatrace.com/t5/Alerting/Notifications-of-problem-changes-in-AI-2-0/m-p/116238)).

Grail now models merges as `dt.davis.is_duplicate` plus `duplicate_problem_ids` ([Davis semantic dictionary](https://docs.dynatrace.com/docs/semantic-dictionary/model/davis)). Accept MERGED as a legacy value, but do not build eval cases that depend on it.

## One normalization table reconciles the four paths

The upgrade guide gives the official classic-to-Grail placeholder mapping ([upgrade guide](https://docs.dynatrace.com/docs/manage/upgrade-guide-landing-page/upgrade-guide-alert-notification)):

| Classic placeholder | Grail field |
|---|---|
| `{State}` | `event.status` |
| `{ProblemID}` | `display_id` |
| `{PID}` | `event.id` |
| `{ProblemSeverity}` | `event.category` (the guide says also consider `event.severity`) |
| `{ProblemImpact}` | `dt.davis.impact_level` |
| `{ImpactedEntities}` | `smartscape.affected_entities` |
| `{Tags}` | `primary_tags.*` |
| `{ProblemDetailsText}` / `{ProblemDetailsMarkdown}` | `event.description` |
| `{ProblemURL}` | `problem_link()` |

The rows below extend that mapping across all four paths and propose a canonical value for each. Enums are DOCUMENTED. Canonical choices are recommendations.

| Concept | Grail `dt.davis.problems` / Workflow `event()` | Problems API v2 | Classic webhook | Canonical `ProblemEvent` |
|---|---|---|---|---|
| Internal ID | `event.id` (`…_…V2`, may be negative) | `problemId` | `{PID}` | `problemId` (opaque string) |
| Display ID | `display_id` (`P-2307288`) | `displayId` | `{ProblemID}` (`999` in tests) | `displayId` (no format validation) |
| Status | `ACTIVE` / `CLOSED` | `OPEN` / `CLOSED` | `OPEN` / `RESOLVED` (legacy `MERGED`) | `OPEN` / `CLOSED` (+ `MERGED` tolerated → treat as update) |
| Transition | `event.status_transition` (`CREATED`, `UPDATED`, `REFRESHED`, `RESOLVED`, `REOPENED`, `CLOSED`; experimental, optional) | none (diff successive polls) | none (each call is a transition) | `transition` nullable; derive from status change when absent |
| Category | `AVAILABILITY`, `ERROR`, **`SLOWDOWN`**, `RESOURCE_CONTENTION`, `CUSTOM_ALERT`, `MONITORING_UNAVAILABLE` | `severityLevel` with **`PERFORMANCE`** and also `INFO` | `{ProblemSeverity}` with `PERFORMANCE`; no `MONITORING_UNAVAILABLE` listed | `category` using Grail names; map `PERFORMANCE`→`SLOWDOWN` |
| Numeric severity | `event.severity` 1–5, **1 = most severe**, nullable | absent | absent | `severity: int?` (1 = Critical … 5 = Informational); never invent for v2/webhook |
| Impact level | `dt.davis.impact_level` title-case `Application`/`Environment`/`Infrastructure`/`Services`/`Synthetic`; string or list | `APPLICATION`/`ENVIRONMENT`/`INFRASTRUCTURE`/**`SERVICES`** | `APPLICATION`/**`SERVICE`**/`INFRASTRUCTURE` | `impactLevels: set<enum>` uppercase, `SERVICE`≡`SERVICES` |
| Entity record | `{id, type, name}`; type `host` (docs) or `SERVICE` (observed) | `{entityId:{id,type}, name?}`, type uppercase | `{type, name, entity}` | `{id, type (UPPER_SNAKE), name?}` |
| Deprecated entity type | `dt.entity.host`, `dt.entity.process_group_instance` | n/a | n/a | strip `dt.entity.` and uppercase |
| Root cause | `root_cause.smartscape_entity` / `root_cause_entity_id`, **usually absent** | `rootCauseEntity` (nullable) | only inside details JSON/text | `rootCause?` |
| Times | ns or ISO-8601 ns strings; `event.end` only when closed | ms; `endTime = -1` while open | none as fields | `startedAt`/`endedAt?` as UTC instants |
| Tags | `entity_tags` (deprecated), `primary_tags.*` | `entityTags[]` objects | `{Tags}` comma string | `tags: map<string, list<string>>` |
| Evidence | not on record; join `dt.davis.event_ids` → `dt.davis.events` | `evidenceDetails.details[]` (with `fields=`) | inside `{ProblemDetailsJSONv2}` only | `evidence[]` typed union (EVENT/METRIC/…) |
| Merge/duplicate | `dt.davis.is_duplicate`, `duplicate_problem_ids` | `linkedProblemInfo` | `MERGED` (undocumented) | `duplicateOf?`, `isDuplicate` |
| Maintenance | `maintenance.is_under_maintenance` | `underMaintenance()` selector only | none | `underMaintenance?` |
| Management zone | not present | `managementZones[]` | none | `managementZones?` (v2 only) |

Severity direction deserves care. Grail `event.severity` is ITIL-aligned, **1 = Critical, 2 = High, 3 = Medium, 4 = Low, 5 = Informational**, and Dynatrace's own guidance says detectors should set it explicitly rather than rely on a platform default ([dynatrace-for-ai davis-events reference](https://github.com/Dynatrace/dynatrace-for-ai/blob/main/skills/dt-alerting/references/davis-events.md)). Category has its own documented order, most to least severe: **Monitoring unavailable, Availability, Error, Slowdown, Resource, Custom, Info, Warning**. A problem escalates to its most severe constituent category, for example from slowdown to availability ([event categories](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/event-analysis-and-correlation/event-categories)). The adapter should therefore rank by numeric severity when present and fall back to category rank otherwise. Any sort must run ascending on `event.severity`. An adapter that treats 5 as "worst" will invert every routing decision.

## Kafka problems wear generic Dynatrace titles

Dynatrace publishes **no Kafka-specific problem titles**. The Apache Kafka extension documents that it ships alerts for "broker availability, cluster controllers, offline partitions, leader elections, and replicated partitions", but it lists neither names nor thresholds ([Apache Kafka extension](https://docs.dynatrace.com/docs/ingest-from/extensions/supported-extensions/apache-kafka)). Release 1.0.8 renamed those alerts to include "Kafka" ([Dynatrace Hub](https://www.dynatrace.com/hub/detail/apache-kafka/)).

Real Kafka incidents arrive through three channels: built-in OneAgent detections, Kubernetes anomaly detectors, and user-defined metric or log events. Event-type identifiers come in **three naming generations that do not match**:

- **Category-page and API v1 codes**, such as `CPU_SATURATED`, `LOW_DISK_SPACE` and `HIGH_GC_ACTIVITY`.
- **Alerting-profile and Events-v2 names**, such as `OSI_HIGH_CPU`, `OSI_LOW_DISK_SPACE` and `PROCESS_HIGH_GC_ACTIVITY` ([alerting profile schema](https://github.com/dynatrace-oss/terraform-provider-dynatrace/blob/main/dynatrace/api/builtin/alerting/profile/schema.json)).
- **Generic ingest types**: `AVAILABILITY_EVENT`, `ERROR_EVENT`, `PERFORMANCE_EVENT`, `RESOURCE_CONTENTION_EVENT`, `CUSTOM_ALERT`, `CUSTOM_INFO`, `WARNING` and others ([Davis semantic dictionary](https://docs.dynatrace.com/docs/semantic-dictionary/model/davis)).

Only `OSI_HIGH_CPU` and `OSI_HIGH_MEMORY` are confirmed as Grail `event.type` values, through official DQL ([Dynatrace Intelligence DQL examples](https://docs.dynatrace.com/docs/dynatrace-intelligence/use-cases/dynatrace-intelligence-dql-examples)). For process outage, the availability docs give `PROCESS_UNAVAILABLE` ([availability events](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/event-analysis-and-correlation/event-categories/availability-events)) while the alerting-profile enum gives `PGI_UNAVAILABLE`. Treat both as aliases. Running `summarize count(), by:{event.type, event.name, event.category}` on a live tenant would settle the question.

| Kafka-relevant `event.type` (v2/Grail style) | Default title | Category | Status | Kafka meaning |
|---|---|---|---|---|
| `OSI_LOW_DISK_SPACE` | Low disk space | RESOURCE_CONTENTION | DOCUMENTED (<3% free, 18 of 30 ten-second intervals) | broker `log.dirs` filling |
| `OSI_HIGH_CPU` | CPU saturation | RESOURCE_CONTENTION | DOCUMENTED (>95% avg, 18 of 30) | broker host |
| `OSI_HIGH_MEMORY` | Memory saturation | RESOURCE_CONTENTION | DOCUMENTED (Linux >80% + >20 page faults/s) | broker host |
| `OSI_SLOW_DISK` / `OSI_DISK_LOW_INODES` | Slow disk / Low number of inodes available | RESOURCE_CONTENTION | DOCUMENTED (>200 ms; <5% inodes) | segment I/O, many segments |
| `PROCESS_HIGH_GC_ACTIVITY` | Long garbage-collection time | RESOURCE_CONTENTION | DOCUMENTED (GC time >40% or suspension >25%) | broker/consumer JVM |
| `PGI_UNAVAILABLE` / `PROCESS_UNAVAILABLE` | Process unavailable | AVAILABILITY | DOCUMENTED; **process availability monitoring is off by default** | broker JVM down |
| `PG_LOW_INSTANCE_COUNT` | Process group low instance count | AVAILABILITY | DOCUMENTED | fewer brokers than expected |
| `OSI_UNEXPECTEDLY_UNAVAILABLE` | Host or monitoring unavailable | AVAILABILITY | DOCUMENTED (>5 min; graceful shutdown raises nothing) | broker VM lost |
| `PROCESS_CRASHED` | Process crashed | AVAILABILITY | category INFERRED; as Grail literal **not found**, use `event.group_label == "Process crashed"` | JVM crash |
| `PROCESS_NA_HIGH_CONN_FAIL_RATE` | Connectivity problem | ERROR | category INFERRED | clients cannot reach brokers |
| `SERVICE_ERROR_RATE_INCREASED` | Failure rate increase | ERROR | category INFERRED | producer/consumer service |
| `SERVICE_SLOWDOWN` | Response time degradation | SLOWDOWN | DOCUMENTED | producer/consumer latency |
| `SERVICE_UNEXPECTED_LOW_LOAD` | Unexpected low load | AVAILABILITY | INFERRED from "Unexpected low traffic" | consumer stopped |

Thresholds come from the [resource events](https://docs.dynatrace.com/docs/shortlink/event-types-resource) and [availability events](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/event-analysis-and-correlation/event-categories/availability-events) pages. Several names that might seem natural were searched for and **not found verbatim**: `SERVICE_UNAVAILABLE`, `JVM_GARBAGE_COLLECTION`, `ESTIMATED_TIME_TO_DISK_FULL`, and any `K8S_POD_*` or `K8S_CONTAINER_OOM_KILLED` type. Do not put them in fixtures.

When Davis merges events, the problem title becomes generic. **"Multiple infrastructure problems" is the documented example** ([Davis semantic dictionary](https://docs.dynatrace.com/docs/semantic-dictionary/model/davis)). It is common in practice, and titles change over a problem's life ([community](https://community.dynatrace.com/t5/Alerting/Filtering-out-problems-with-title-quot-Multiple-infrastructure/td-p/241149)). Events merge when their active windows overlap and they share a source entity, a vertical stack (process on host) or a horizontal call path, provided `dt.davis.is_merging_allowed` is true ([problem-merging reference](https://github.com/Dynatrace/dynatrace-for-ai/blob/main/skills/dt-obs-problems/references/problem-merging.md)). Other variants such as "Multiple service problems" are INFERRED.

Kubernetes anomaly detection (provider `KUBERNETES_ANOMALY_DETECTION`) emits fixed, DOCUMENTED titles ([Kubernetes alerting](https://docs.dynatrace.com/docs/observe/infrastructure-monitoring/container-platform-monitoring/kubernetes-monitoring/alert-on-kubernetes-issues); [anomaly detectors](https://docs.dynatrace.com/docs/observe/infrastructure-monitoring/container-platform-monitoring/kubernetes-app/reference/anomaly-detectors)):

| Title | Category | Default | Class |
|---|---|---|---|
| "Container restarts" | Error | ≥1 restart per 3 min within 5 min, enabled | Warning signal |
| "Out-of-memory kills" | Error | enabled | Warning signal |
| "Pods stuck in pending" | Resource | ≥10 min, enabled | Warning signal |
| "Kubernetes PVC: Low disk space %" | Resource | <3%, enabled | Warning signal |
| "No pod ready" | Error | disabled | Health alert |
| "Not all pods ready" | Error | disabled | Warning signal |
| "Backoff event" | Error | disabled | Health alert |
| "Pod eviction event" | Error | disabled | Health alert |
| "Memory usage close to limits" | Resource | >90%, disabled | Warning signal |

There is **no CrashLoopBackOff type**. It surfaces as "Container restarts" or "Backoff event". The container-level "Out of memory kill" info event raises no alert on its own ([info events](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/event-analysis-and-correlation/event-categories/info-events)). The Grail `event.type` these detectors emit is undocumented. It is probably a generic `ERROR_EVENT` or `RESOURCE_CONTENTION_EVENT` (INFERRED).

User-defined metric events take their **title verbatim from the configured `eventTemplate.title`**. Placeholders such as `{alert_condition}`, `{threshold}`, `{dims:dt.entity.<type>}` and `{entityname}` are documented for the description ([metric key events](https://docs.dynatrace.com/docs/dynatrace-intelligence/anomaly-detection/metric-events/metric-key-events)). The template event type comes from `AVAILABILITY`, `CUSTOM_ALERT`, `ERROR`, `INFO`, `RESOURCE`, `SLOWDOWN`, `WARNING` and others, and the alert condition is `ABOVE`, `BELOW` or `OUTSIDE` ([Terraform metric events enums](https://github.com/dynatrace-oss/terraform-provider-dynatrace/blob/main/dynatrace/api/builtin/anomalydetection/metricevents/settings/enums.go)). Log events use the same enum with provider `LOG_EVENTS`, and their titles can embed `{content}` ([Terraform log_events](https://github.com/dynatrace-oss/terraform-provider-dynatrace/blob/main/docs/resources/log_events.md)).

Realistic user-defined titles for fixtures, all INFERRED and labelled synthetic, include "Kafka under-replicated partitions", "Kafka offline partitions", "Kafka active controller count not 1" and "Kafka consumer lag high". A `CUSTOM_ALERT` template yields Grail `event.type = CUSTOM_ALERT`, `event.category = CUSTOM_ALERT` and `event.provider = METRIC_EVENTS`. The mapping of the other template types (`RESOURCE`→`RESOURCE_CONTENTION_EVENT`, and so on) is INFERRED.

## Six classic keys and one JMX extension define Kafka health

OneAgent's classic built-in Kafka coverage is **only six `builtin:tech.kafka.pg.*` metrics**, all split by process group ([built-in metrics](https://docs.dynatrace.com/docs/analyze-explore-automate/metrics-classic/built-in-metrics)):

| Key | Display name |
|---|---|
| `...ReplicaManager.UnderReplicatedPartitions.Value` | Kafka broker - Under replicated partitions |
| `...ReplicaManager.PartitionCount.Value` | Kafka broker - Partitions |
| `...ControllerStats.LeaderElectionRateAndTimeMs.OneMinuteRate` | Kafka broker - Leader election rate |
| `...ControllerStats.UncleanLeaderElectionsPerSec.OneMinuteRate` | Kafka broker - Unclean election rate |
| `...KafkaController.ActiveControllerCount.Value` | Kafka controller - Active cluster controllers |
| `...KafkaController.OfflinePartitionsCount.Value` | Kafka controller - Offline partitions |

Broker JVM health comes from these generic keys:

- Heap: `builtin:tech.jvm.memory.pool.used`
- GC: `builtin:tech.jvm.memory.gc.suspensionTime` (Percent)
- Process availability: `builtin:pgi.availability`
- Process resources: `builtin:tech.generic.cpu.usage` and `builtin:tech.generic.handles.fileDescriptorsPercentUsed`

In the classic entity model, the cluster is a `PROCESS_GROUP` and each broker is a `PROCESS_GROUP_INSTANCE` on a `HOST` (INFERRED from the `.pg.` split).

Everything else comes from the Extensions 2.0 **Apache Kafka (JMX) extension `com.dynatrace.extension.jmx-kafka`, latest 3.1.4**. It ships **113 metrics in 19 feature sets**, and its metric keys mirror the MBeans ([Dynatrace Hub](https://www.dynatrace.com/hub/detail/apache-kafka/); [Apache Kafka extension docs](https://docs.dynatrace.com/docs/observe/infrastructure-observability/extensions/apache-kafka)). All keys below are DOCUMENTED:

| Feature set | Keys (units where stated) | Triage use |
|---|---|---|
| broker-metrics | `kafka.server.ReplicaManager.UnderReplicatedPartitions`, `kafka.server.ReplicaManager.UnderMinIsrPartitionCount`, `kafka.server.BrokerTopicMetrics.FailedProduceRequestsPerSec.OneMinuteRate`, `…FailedFetchRequestsPerSec.OneMinuteRate`, `…BytesInPerSec.OneMinuteRate`, `kafka.server.ReplicaFetcherManager.MaxLag.Replica.Value`, `kafka.server.FetcherLagMetrics.ConsumerLag.Value` | URP and under-min-ISR; failed requests. **`FetcherLagMetrics.ConsumerLag` is follower replica lag, not consumer-group lag.** |
| controller-metrics | `kafka.controller.KafkaController.OfflinePartitionsCount`, `kafka.controller.KafkaController.ActiveControllerCount.Value` | Offline partitions, controller sum |
| leader-metrics | `kafka.controller.ControllerStats.LeaderElectionRateAndTimeMs.OneMinuteRate`, `…UncleanLeaderElectionsPerSec.OneMinuteRate`, `kafka.server.ReplicaManager.LeaderCount.Value` | Broker failure, data-loss risk |
| handler-metrics | `kafka.server.handler.average-idle-percent.rate` | Request handler saturation |
| network-metrics | `kafka.network.RequestChannel.RequestQueueSize.Value`, `…ResponseQueueSize.Value`, `kafka.network.SocketServer.NetworkProcessorAvgIdlePercent` (Percent), `kafka.network.RequestMetrics.TotalTimeMs.Produce/FetchConsumer/FetchFollower.Count` | Network saturation, request latency (split by `node`) |
| purgatory-metrics | `kafka.server.purgatory.produce-delay-size`, `…fetch-delay-size` | Slow acks |
| zookeeper-metrics | `kafka.server.SessionExpireListener.ZooKeeperDisconnectsPerSec.OneMinuteRate`, `…ZooKeeperExpiresPerSec.OneMinuteRate` | ZK-mode session loss |
| Raft Broker | `kafka.raft.last-applied-record-offset`, `…last-applied-record-timestamp`, `…last-applied-record-lag-ms`, `…metadata-load-error-count`, `…metadata-apply-error-count` | KRaft metadata health |
| Raft Quorum | `kafka.raft.append-records-rate`, `…commit-latency-avg`, `…commit-latency-max` (MilliSecond), `…fetch-records-rate`, `…log-end-epoch`, `…log-end-offset`, `…current-leader` (-1 = unknown) | KRaft quorum loss |
| consumer-records-lag | `kafka.consumer.consumer-metrics.records-lag`, `…records-lag-avg`, `…records-lag-max` (Count); own feature set since 3.1.0 | Consumer lag |
| confluent-consumer-lag | `kafka.server.tenant-metrics.consumer-lag-offsets` (Confluent Platform only; split by consumer group, client ID, member ID, partition, topic, group protocol) | Broker-side group lag |
| connector-metrics | `kafka.connector.status`, `kafka.connector.task.status` (1 = running), `…task.pause-ratio`, `…task.running-ratio`, `…task.offset-commit-success-percentage`, `…offset-commit-failure-percentage` | Connect task failure |

The remaining sets cover consumer, producer, connect, log-flush, disk, cluster-link and multi-region metrics. The extension needs the OneAgent feature **"Java Metric Extensions 2.0 (JMX)"** on every broker, producer and consumer host, running on supported Linux or Windows ([Apache Kafka extension docs](https://docs.dynatrace.com/docs/observe/infrastructure-observability/extensions/apache-kafka)). The docs do not state supported Kafka versions or the EEC version, and they do not say whether KRaft or ZooKeeper is supported. Both raft-* and zookeeper-metrics feature sets exist, so fixtures should model both controller modes. The docs give a few healthy values. The unclean leader election rate "should be 0". The leader election rate is "non-zero when there are broker failures". Under-min-ISR partitions count partitions whose ISR is below minIsr. They give no lag thresholds, no DQL examples, and nothing on log ingestion.

The package ships "generic types" for Kafka topics, producers, consumers and brokers, plus their relationships. **The exact entity type names are not stated** on either page (`kafka:broker`?), so do not hard-code them.

Alerts have the same gap. The docs say the alerts cover broker availability, cluster controllers, offline partitions, leader elections and replicated partitions. **Alert names, thresholds and windows are not published.** The release notes add some history. Release 1.0.8 "changed the name of the out-of-the-box alerts to include the word Kafka in the title". Release 2.4.0 "added ALERT_TEMPLATES for anomaly detectors in the Davis Anomaly Detection app". Release 3.1.4 "added smartscape node extraction to Anomaly Detection Templates" ([Dynatrace Hub](https://www.dynatrace.com/hub/detail/apache-kafka/)). So extension-raised problems will carry "Kafka" in the title and an anomaly-detector provider. The exact strings still need to be captured from a tenant.

Breaking changes matter for fixture realism ([Dynatrace Hub](https://www.dynatrace.com/hub/detail/apache-kafka/)):

- **3.1.0** sends metrics through a dedicated OpenPipeline pipeline and drops dynamic routes for extension metrics. Field-name capitalization may differ, and it needs Dynatrace/EEC 1.343.0 or later. `records-consumed-rate` and `bytes-consumed-rate` are now always split by topic.
- **2.0.0** gave consumers and producers new entity IDs, built from PGI plus host rather than `client-id`.
- **1.0.1** fixed a key typo, `CosumerLag` → `ConsumerLag`.

Historical fixtures can legitimately contain the old IDs and the misspelled key.

There is **no `builtin:` consumer-lag metric**. Lag comes from one of these sources:

- Consumer-side JMX via the extension.
- Confluent Platform's `kafka.server.tenant-metrics.consumer-lag-offsets`.
- The Confluent Cloud extension's `confluent_kafka_server_consumer_lag_offsets`.
- Prometheus `kafka_consumergroup_lag{consumergroup,topic,partition}` from kafka_exporter ([kafka_exporter](https://github.com/danielqsj/kafka_exporter)).

OneAgent Full-Stack also creates Queue entities for topics seen in traces, along with producer, consumer and listener services, and emits `builtin:queue.incoming_requests` and `builtin:queue.outgoing_requests` ([queue concepts](https://docs.dynatrace.com/docs/observe/infrastructure-observability/queues/queue-concepts)).

Healthy values come from Apache Kafka, not Dynatrace, which publishes no thresholds for its shipped alerts ([Kafka monitoring docs](https://kafka.apache.org/41/operations/monitoring/)):

| Signal | Healthy (DOCUMENTED) | Unhealthy for fixtures (INFERRED ops practice) |
|---|---|---|
| UnderReplicatedPartitions | 0 | >0 persistent outside maintenance |
| UnderMinIsrPartitionCount | 0 | >0 (acks=all producers fail) |
| ActiveControllerCount | exactly one broker has 1 | cluster sum ≠ 1 |
| OfflinePartitionsCount | (0) | >0 critical |
| UncleanLeaderElectionsPerSec | 0 | >0 (data-loss risk) |
| IsrShrinks/ExpandsPerSec | 0 except around broker restarts | sustained shrinks |
| Request handler / network processor idle | ideally >0.3 | <0.3 saturation |
| LeaderElectionRate | non-zero only on broker failure | repeated spikes |
| Consumer lag (`records-lag-max`) | flat, near 0 to hundreds | monotonic growth for 10–15 min |
| Log-dir disk | — | ticket >75%, page >90% ([Netdata](https://www.netdata.cloud/guides/kafka/kafka-broker-out-of-disk/)) |

The extension has no IsrShrinksPerSec key, so use UnderReplicatedPartitions as the proxy. Classic metrics are per process group, so a cluster-wide controller check needs `:splitBy():sum`.

## Log lines and metrics, not titles, decide the triage label

The broker log layout `[yyyy-MM-dd HH:mm:ss,SSS] LEVEL message (logger.class)` is confirmed by real pasted logs ([KAFKA-9604](https://issues.apache.org/jira/browse/KAFKA-9604)). The strings below are verbatim from Kafka 3.7 source unless noted ([Partition.scala](https://raw.githubusercontent.com/apache/kafka/3.7/core/src/main/scala/kafka/cluster/Partition.scala); [ReplicaManager.scala](https://raw.githubusercontent.com/apache/kafka/3.7/core/src/main/scala/kafka/server/ReplicaManager.scala); [QuorumController.java](https://raw.githubusercontent.com/apache/kafka/3.7/metadata/src/main/java/org/apache/kafka/controller/QuorumController.java); [AbstractCoordinator.java](https://raw.githubusercontent.com/apache/kafka/3.7/clients/src/main/java/org/apache/kafka/clients/consumer/internals/AbstractCoordinator.java); [Sender.java](https://raw.githubusercontent.com/apache/kafka/3.7/clients/src/main/java/org/apache/kafka/clients/producer/internals/Sender.java); [NetworkClient.java](https://raw.githubusercontent.com/apache/kafka/3.7/clients/src/main/java/org/apache/kafka/clients/NetworkClient.java); [WorkerTask.java](https://raw.githubusercontent.com/apache/kafka/3.7/connect/runtime/src/main/java/org/apache/kafka/connect/runtime/WorkerTask.java)). Placeholders are kept from the source:

| Incident | Representative line(s) | Provenance |
|---|---|---|
| ISR shrink | `INFO [Partition orders-0 broker=1] Shrinking ISR from 1,2,3 to 1,3. Leader: (highWatermark: …). Out of sync replicas: (…). (kafka.cluster.Partition)` | source pieces; rendered assembly INFERRED |
| ISR recover / under-min | `INFO [Partition orders-0 broker=1] ISR updated to 1,2,3` (suffix `(under-min-isr)` when below min.insync) | source; **no "Expanding ISR from" literal in 3.x** |
| Log dir failure | `ERROR Error while deleting segments for ocs.account-balances-12 in dir /data/… (kafka.server.LogDirFailureChannel)`, `INFO Stopping serving logs in dir … (kafka.log.LogManager)`, `ERROR Shutdown broker because all log dirs in … have failed (kafka.log.LogManager)`, `FATAL Halting broker because dir $dir is offline` | real log + source |
| Replica fetcher | `ERROR Error for partition $tp at offset $offset`; `WARN Partition $tp marked as failed` | source |
| KRaft failover | `INFO [QuorumController id=3000] Becoming the active controller at epoch {}, next write offset {}.`; `WARN We were the leader at epoch {}, but in the new epoch {}, the leader is {}. Reverting to last stable offset {}.` | source |
| Unclean recovery | `INFO The topic partition $tp was marked as RECOVERING.` | source |
| Consumer stuck | `WARN consumer poll timeout has expired. This means the time between subsequent calls to poll() was longer than the configured max.poll.interval.ms …`; `Commit cannot be completed since the group has already rebalanced …` | source |
| Normal rebalance | `INFO (Re-)joining group`; `Successfully joined group with generation {}` | source |
| Producer | `Expiring 12 record(s) for orders-0:120001 ms has passed since batch creation`; `WARN Received invalid metadata error in produce request on partition {} due to {}. Going to request metadata update now` | source (120000 ms = 3.x `delivery.timeout.ms`) |
| Client connectivity | `WARN Connection to node {} ({}) could not be established. Node may not be available.` | source ("Broker may not be available" is the older wording) |
| Connect | `ERROR {} Task threw an uncaught and unrecoverable exception. Task is being killed and will not recover until manually restarted` | source |
| Auth | `INFO Failed authentication with /10.x.x.x (SSL handshake failed) (org.apache.kafka.common.network.Selector)` | vendor KBs ([Yandex Cloud](https://docs.yandex.cloud/int/en/troubleshooting/managed-kafka/known-issues/error-failed-authentification.md)) |

The following were **not verified** and should be marked as paraphrased in fixtures:

- `java.lang.OutOfMemoryError: Java heap space`
- `Too many open files`
- Log-cleaner thread death
- Corrupted-index rebuild
- `[KafkaServer id=1] started`
- ZooKeeper "Session expired" lines
- `NotEnoughReplicasException` text
- Kafka Streams state transitions

In Grail these lines land as log records. DOCUMENTED fields ([Logs semantic dictionary](https://docs.dynatrace.com/docs/semantic-dictionary/model/log)):

- `timestamp`, `content` (a multi-line stack trace stays in one record, INFERRED) and `event.type = "LOG"`.
- `loglevel`, from a 9-level set.
- `status`, restricted to `ERROR`, `WARN`, `INFO` or `NONE`.
- `log.source`, e.g. `Docker Container Output` or `stdout`.
- `log.iostream`, `process.technology` (string array), `trace_id` and `span_id`.
- The deprecated `dt.entity.process_group_instance` and `dt.source_entity`. `dt.source_entity` is a **string array** on logs but a plain string on events.

OneAgent adds `host.name`, `dt.process.name`, `container.name`, `container.id`, `container.image.name`, `k8s.cluster.name`, `k8s.namespace.name`, `k8s.pod.name`, `k8s.pod.uid`, `k8s.container.name`, `k8s.deployment.name` and the `dt.entity.*` container and Kubernetes IDs ([OneAgent log enrichment](https://docs.dynatrace.com/docs/analyze-explore-automate/logs/lma-log-ingestion/lma-log-ingestion-via-oa/lma-log-data-transformation-oa)). It maps keywords to `loglevel`: `fatal`/`emerg` become EMERGENCY, `severe` becomes SEVERE, and so on. `status` then rolls those up, so SEVERE, CRITICAL, ALERT and EMERGENCY all become `ERROR`. Level detection scans only the first 100 characters of the first two lines.

Here is an example log record (field names DOCUMENTED, values INFERRED; `process.technology: "Apache Kafka"` and `log.source: "Container Output"` are guesses):

```json
{
  "timestamp": "2026-10-12T14:06:58.214000000Z",
  "event.type": "LOG",
  "content": "[2026-10-12 14:06:58,214] ERROR Shutdown broker because all log dirs in /var/lib/kafka/data have failed (kafka.log.LogManager)",
  "loglevel": "ERROR",
  "status": "ERROR",
  "log.source": "Container Output",
  "log.iostream": "stdout",
  "process.technology": ["Java", "Apache Kafka"],
  "dt.process.name": "kafka.Kafka",
  "host.name": "aks-kafka-pool-0001",
  "container.name": "kafka",
  "k8s.cluster.name": "prod-aks-weu",
  "k8s.namespace.name": "streaming",
  "k8s.pod.name": "kafka-2",
  "k8s.container.name": "kafka",
  "dt.entity.process_group_instance": "PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10",
  "dt.source_entity": ["PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10"]
}
```

A Kafka StatefulSet has no `k8s.deployment.name`, so use `k8s.workload.kind: "statefulset"`. The official join from a problem to its logs is `fetch logs | filter dt.source_entity in [ fetch dt.davis.problems | filter display_id == "P-…" | fields affected_entity_ids ]` ([Davis semantic dictionary](https://docs.dynatrace.com/docs/semantic-dictionary/model/davis)).

The remediation guidance converges on a clear label map:

- Under-replicated partitions during a controlled rolling restart are expected and return to 0 after catch-up. Operators should wait for each broker to catch up and restart the active controller last ([Confluent post-deployment](https://docs.confluent.io/platform/current/kafka/post-deployment.html)).
- Persistent URP at low traffic points to capacity or ACL problems, not to something a restart fixes. `NotLeaderForPartitionException` "is often a transient error" ([AWS MSK troubleshooting](https://docs.aws.amazon.com/msk/latest/developerguide/troubleshooting.html)).
- A full log dir must have space freed before any restart. Deleting segments on a running broker corrupts data ([Conduktor](https://www.conduktor.io/blog/disk-full-emergency-recovery)).
- A disk incident can cascade cluster-wide, as other brokers fail writing checkpoint files ([KAFKA-9604](https://issues.apache.org/jira/browse/KAFKA-9604)).

The resulting labels use the app's five actions. The mapping is INFERRED from the cited guidance:

| Kafka incident signature (Dynatrace view + logs + metrics) | Label |
|---|---|
| "Send test notification" payload (`ProblemTitle` "Dynatrace problem notification test run") | suppress |
| ISR shrink followed by `ISR updated to 1,2,3` within ~1 min, URP back to 0, during rolling restart / maintenance window | suppress |
| `(Re-)joining group` / `Successfully joined group` during a deployment, lag flat | suppress |
| Single burst of `Received invalid metadata error … NOT_LEADER_OR_FOLLOWER` | suppress (or notify if repeated) |
| `dt.davis.is_duplicate = true`, or `maintenance.is_under_maintenance = true` | suppress |
| "Process unavailable" / "Container restarts" on one broker; URP >0 but OfflinePartitions = 0 and controller sum = 1 | restart container (that broker), then verify |
| Single broker JVM OOM or hung, cluster otherwise healthy | restart container |
| Connect `Task threw an uncaught and unrecoverable exception`, first occurrence | restart (task); repeat → open ticket |
| Consumer poll-timeout + LeaveGroup + CommitFailedException, one instance, lag rising | restart container if hung; recurring → open ticket |
| Persistent URP >0 with no broker down; gradual lag growth with no errors | open ticket |
| "Low disk space" / PVC low disk trending >75% but no dir failed | open ticket |
| Log cleaner dead (`DeadThreadCount` >0) | open ticket (planned restart) |
| `SSL handshake failed` from one client IP | open ticket (client team) |
| OfflinePartitionsCount >0, controller sum ≠ 1, or KRaft quorum loss | escalate |
| Unclean leader election / `marked as RECOVERING` | escalate |
| `No space left on device`, `Stopping serving logs in dir`, `Shutdown broker because all log dirs … have failed` | escalate (**never** restart first) |
| `SSL handshake failed` from all clients after cert rotation | escalate |
| Lone `Node … disconnected.` with no metric context; "Long garbage-collection time" with no lag or error impact | notify |

## Prioritized implications for fixtures, contract, adapters and evals

| # | Implication | Why |
|---|---|---|
| 1 | Build each eval incident as **one scenario rendered four ways**: Grail/`event()` record, v2 response with `fields=evidenceDetails,impactAnalysis`, classic webhook body, and a `.snapshots` sequence (CREATED → UPDATED → RESOLVED). Assert that all four normalize to the same `ProblemEvent`. | The paths share IDs but no enums; parity tests catch adapter drift. |
| 2 | Make `severity` an **optional int with 1 = most severe**. Populate it only from Grail. Never synthesize it for v2/webhook, and rank by category order (MU > AVAILABILITY > ERROR > SLOWDOWN > RESOURCE > CUSTOM > INFO) when it is null. | It is nullable, not defaulted since SaaS 1.348, and absent from classic paths. Inverted direction is a silent routing bug. |
| 3 | Make `rootCause` optional, and keep **~75–80% of fixtures without it**. | Observed in only 127/540 problems. A classifier that needs root cause will fail in production. |
| 4 | Carry **typed evidence** (`EVENT` with `eventType`/`eventId`, `METRIC` with `metricId`/before/after/`unit`) in the contract. For Workflow and Grail sources, enrich by querying `dt.davis.events` via `dt.davis.event_ids`. | Only v2 and `{ProblemDetailsJSONv2}` carry evidence, and Workflows can't reproduce it. Kafka labels hinge on URP, offline-partition and lag evidence. |
| 5 | Normalize: status ACTIVE/OPEN → `OPEN`, RESOLVED → `CLOSED`, accept `MERGED` as an update. Map `PERFORMANCE` → `SLOWDOWN`. Merge `SERVICE`/`SERVICES`/`Services`. Uppercase entity types and strip `dt.entity.`. Accept impact level as string or list. | Documented enum divergence; MERGED is undocumented but appears in community reports. |
| 6 | Key idempotency on `problemId` + transition (or status + `timestamp`). Treat `REFRESHED` (≥1 per 6 h) and `UPDATED` as non-new. Branch close detection on status, not transition. | Closes arrive as RESOLVED or UPDATED, and the trigger fires once per transition. |
| 7 | Parse v2 `endTime = -1`, Grail ns or ISO timestamps, and missing `event.end`. Tolerate omitted versus null optional members (`linkedProblemInfo`, `k8s.*`, `data`). | Undocumented serialization. Docs examples include an invalid 14-digit ns value. |
| 8 | Label by **Kafka signature, not title**: include "Multiple infrastructure problems", "Process unavailable", "Container restarts" and "Low disk space" cases whose correct label differs only by evidence (URP vs offline partitions vs disk-full logs). | Titles are generic and change over time. Identical titles map to restart, ticket or escalate. |
| 9 | Seed **suppress** cases: test-notification payloads (`999`/`99999` and `TESTID`), `dt.davis.is_duplicate`, maintenance windows, rolling-restart ISR churn, and normal rebalances. | These are high-volume real-world noise with documented signatures. |
| 10 | Encode the **"don't restart" rule** for disk-full / log-dir failure (escalate) and for offline partitions / controller loss. Add adversarial cases where restart looks tempting ("Process unavailable" caused by a disk-full broker). | Restarting before freeing space fails and can cascade (KAFKA-9604). |
| 11 | Use only verified type strings (`OSI_*`, `PGI_UNAVAILABLE`/`PROCESS_UNAVAILABLE` aliases, `PROCESS_HIGH_GC_ACTIVITY`, `CUSTOM_ALERT` + `METRIC_EVENTS`). Tag unverified ones (`PROCESS_CRASHED` as a Grail literal, K8s detector types, extension entity types) as `INFERRED` in fixture metadata. | Three naming generations exist and Grail confirms only two values. |
| 12 | Model logs with `dt.source_entity` as an **array** and `status` ∈ {ERROR, WARN, INFO, NONE}. Join to problems on PGI ID. Mark paraphrased log lines (OOM, Too many open files, ZK session) as such. | Logs and events differ in cardinality, and only some log strings are source-verified. |
| 13 | Draw metric evidence from the documented extension keys. Keep `FetcherLagMetrics.ConsumerLag` (follower lag) separate from `records-lag-max` / `consumer-lag-offsets` (group lag). Make dimension-name matching case-insensitive (post-3.1.0 capitalization). Add KRaft cases (`kafka.raft.current-leader = -1`, metadata error counts) next to ZK cases (`ZooKeeperExpiresPerSec`). Leave extension alert titles and entity type names as placeholders until captured from a tenant. | Documented on the Hub and docs pages. The alert names, thresholds and entity types are explicitly unpublished. |
| 14 | Before freezing the eval set, run `fetch dt.davis.events \| summarize count(), by:{event.type, event.name, event.category, event.provider}` and `fetch dt.davis.problems \| fields smartscape.affected_entities` on the target tenant. | This settles Smartscape type casing, the process type name and the K8s event types, which no source documents. |

## Conclusion

The hard part of this integration is not parsing; it is choosing the right truth source. Grail is the forward-looking canonical shape, but it lacks the structured evidence that makes Kafka triage decidable. Problems API v2 has that evidence but speaks a legacy enum dialect and has no numeric severity. The design that follows is a canonical `ProblemEvent` built on Grail semantics (ACTIVE/CLOSED mapped to OPEN/CLOSED, SLOWDOWN, nullable 1-is-worst severity, Smartscape entities) and enriched with v2-style typed evidence, with every source-specific gap represented as an explicit null rather than a guessed value.

For the eval set, Dynatrace's problem title is weak evidence. The documented generic titles, mutable "Multiple … problems" merges and absent root causes mean a correctly labelled dataset has to encode the Kafka signature underneath: URP versus offline partitions, controller count, disk-full log lines, ISR re-expansion, and consumer poll timeouts. Those features, not the Dynatrace envelope, separate suppress from restart from escalate. A model that scores well on such a dataset is learning operations judgment rather than memorising title strings, which is the property the decision engine needs in production.

## Appendix A: Apache Kafka extension metric catalog (113 metrics, DOCUMENTED)

Verbatim from the 19 feature-set dropdowns on the [official extension docs page](https://docs.dynatrace.com/docs/observe/infrastructure-observability/extensions/apache-kafka), extracted 2026-10-12 (extension 3.1.4). Machine-readable copy: `research_notes/Dynatrace Kafka triage data models/kafka_extension_metric_catalog.json`. Names are the display names Dynatrace shows in charts; keys are what DQL and metric events use. Note `kafka.server.FetcherLagMetrics.ConsumerLag.Value` is follower-replica lag, not consumer-group lag.

### purgatory-metrics (2)

| Metric name | Metric key | Description |
|---|---|---|
| Kafka Server - Purgatory Produce Size | `kafka.server.purgatory.produce-delay-size` | Requests waiting in the produce purgatory. |
| Kafka Server - Purgatory Fetch Size | `kafka.server.purgatory.fetch-delay-size` | Requests waiting in the fetch purgatory. |

### connect-metrics (4)

| Metric name | Metric key | Description |
|---|---|---|
| Kafka Connect - Requests | `kafka.connect.connect-metrics.request-rate` | The average number of requests sent per second. |
| Kafka Connect - Outgoing byte rate | `kafka.connect.connect-metrics.outgoing-byte-rate` | The average number of outgoing bytes sent per second to all servers. |
| Kafka Connect - Request size | `kafka.connect.connect-metrics.request-size-avg` | The average size of all requests in the window. |
| Kafka Connect - Incoming byte rate | `kafka.connect.connect-metrics.incoming-byte-rate` | Bytes/second read off all sockets. |

### cluster-link (12)

| Metric name | Metric key | Description |
|---|---|---|
| Active link count | `kafka.cluster-link.link-count` | Total number of links in an active state. |
| Active mirror count | `kafka.cluster-link.mirror-topic-count` | Total number of active (healthy) mirror topics. |
| Cluster link max lag | `kafka.cluster-link.max-lag` | Maximum lag in messages between the replicas on the destination cluster and the leader replica on the source cluster. |
| Fetched bytes per second | `kafka.cluster-link.bytes-per-sec.count` | Rate at which data is fetched from the source cluster. Indicates amount of throughput in bytes per second on the cluster link. |
| Fetched throttle time | `kafka.cluster-link.fetch-throttle-time-avg` | Gives throttle times for the Cluster Linking fetchers. May indicate increases in lag on the cluster link due to throttling/quotas being enforced. |
| Active task count | `kafka.cluster-link.link-task-count` | Monitor the state of link level tasks. |
| Failed reverse connection count | `kafka.cluster-link.reverse-connection-failed-total` | Total count of reverse connections that failed for this cluster link. Only available for outbound connections. |
| Authentication problems | `kafka.cluster-link.authentication-problems` | Unavailable links due to authentication. |
| Unavailable link (by reason) | `kafka.cluster-link.unavailable-link-count` | Unavailable links due to authentication. |
| Controller reverse connection count | `kafka.cluster-link.controller-reverse-connection-count` | Number of persistent reverse connections for this link between the local broker and the remote link coordinator. |
| Controller reverse connection count | `kafka.cluster-link.remote-admin-request-time` | Admin request processing time. |
| Background thread count | `kafka.cluster-link.background-thread-usage` | Background thread active usage. |

### log-metrics (3)

| Metric name | Metric key | Description |
|---|---|---|
| Kafka Log - Log flush 95th percentile | `kafka.log.LogFlushStats.LogFlushRateAndTimeMs.Percentile95th` | Log flush rate and time in milliseconds. |
| Kafka Log - Log flush mean time | `kafka.log.LogFlushStats.LogFlushRateAndTimeMs.Mean` | Log flush rate and time in milliseconds. |
| Kafka Log - Log end offset | `kafka.log.Log.LogEndOffset` | The offset of the next message that will be appended to the log, by topic and partition. |

### confluent-consumer-lag (1)

| Metric name | Metric key | Description |
|---|---|---|
| Kafka Server - Consumer group lag offsets | `kafka.server.tenant-metrics.consumer-lag-offsets` | The difference between the last offset stored by the broker and the last committed offset for a specific consumer group name, client ID, member ID, partition ID, topic name, and group protocol. Confluent Platform only. |

### broker-metrics (17)

| Metric name | Metric key | Description |
|---|---|---|
| Kafka Broker - Incoming byte rate | `kafka.server.BrokerTopicMetrics.BytesInPerSec.OneMinuteRate` | The rate at which data sent from producers is consumed by the broker. |
| Kafka Broker - Mirror topic bytes in rate | `kafka.server.BrokerTopicMetrics.MirrorBytesInPerSec.OneMinuteRate` | The rate at which mirror bytes are received, by topic. |
| Kafka Broker - Outgoing byte rate | `kafka.server.BrokerTopicMetrics.BytesOutPerSec.OneMinuteRate` | The rate at which data sent from other brokers is consumed by the follower broker. |
| Kafka Broker - Messages in rate | `kafka.server.BrokerTopicMetrics.MessagesInPerSec.OneMinuteRate` | The rate at which individual messages are consumed by the broker. |
| Kafka Broker - Follower fetch requests rate | `kafka.server.BrokerTopicMetrics.TotalFollowerFetchRequestsPerSec.OneMinuteRate` | The follower fetch request rate for the broker. |
| Kafka Broker - Produce message conversions rate | `kafka.server.BrokerTopicMetrics.ProduceMessageConversionsPerSec.OneMinuteRate` | The rate at which produce messages are converted, by topic. |
| Kafka Broker - Partitions | `kafka.server.ReplicaManager.PartitionCount` | The number of partitions in the broker. |
| Kafka Broker - Under replicated partitions | `kafka.server.ReplicaManager.UnderReplicatedPartitions` | The number of partitions that have not been fully replicated in the follower replicas. |
| Kafka Broker - Under minimum in-sync replica partitions | `kafka.server.ReplicaManager.UnderMinIsrPartitionCount` | The number of partitions whose in-sync replicas count is less than minIsr. |
| Kafka Broker - Produce request rate | `kafka.server.BrokerTopicMetrics.TotalProduceRequestsPerSec.OneMinuteRate` | The produce request rate per second. |
| Kafka Broker - Fetch request rate | `kafka.server.BrokerTopicMetrics.TotalFetchRequestsPerSec.OneMinuteRate` | The fetch request rate per second. |
| Kafka Broker - Failed produce requests | `kafka.server.BrokerTopicMetrics.FailedProduceRequestsPerSec.OneMinuteRate` | The produce request rate for requests that failed. |
| Kafka Broker - Failed fetch requests | `kafka.server.BrokerTopicMetrics.FailedFetchRequestsPerSec.OneMinuteRate` | The fetch request rate for requests that failed. |
| Kafka Server - Max follower lag | `kafka.server.ReplicaFetcherManager.MaxLag.Replica.Value` | The maximum lag between the time that messages are received by the leader replica and by the follower replicas. |
| Kafka Server - Current follower lag | `kafka.server.FetcherLagMetrics.ConsumerLag.Value` | The lag in number of messages per follower replica. |
| Kafka Server - Fetch Conversions Rate | `kafka.server.FetchConversionsRate.OneMinuteRate` | — |
| Kafka Server - Produce Conversions Rate | `kafka.server.ProduceConversionsRate.OneMinuteRate` | — |

### connector-metrics (10)

| Metric name | Metric key | Description |
|---|---|---|
| Kafka Connector - Status | `kafka.connector.status` | Equals 1 if the status is running, 0 otherwise. |
| Kafka Connector - Task status | `kafka.connector.task.status` | Equals 1 if the status is running, 0 otherwise. |
| Kafka Connector - Task pause ratio | `kafka.connector.task.pause-ratio` | The fraction of time this task has spent in the pause state. |
| Kafka Connector - Task running ratio | `kafka.connector.task.running-ratio` | The fraction of time this task has spent in the running state. |
| Kafka Connector - Task success ratio | `kafka.connector.task.offset-commit-success-percentage` | The average percentage of this task's offset commit attempts that succeeded. |
| Kafka Connector - Task commit time (max) | `kafka.connector.task.offset-commit-max-time-ms` | The maximum time in milliseconds taken by this task to commit offsets. |
| Kafka Connector - Task failure ratio | `kafka.connector.task.offset-commit-failure-percentage` | The maximum time in milliseconds taken by this task to commit offsets. |
| Kafka Connector - Task commit time (avg) | `kafka.connector.task.offset-commit-avg-time-ms` | The average time in milliseconds taken by this task to commit offsets. |
| Kafka Connector - Task batch size (max) | `kafka.connector.task.batch-size-max` | The maximum size of the batches processed by the connector. |
| Kafka Connector - Task batch size (avg) | `kafka.connector.task.batch-size-avg` | The average size of the batches processed by the connector. |

### consumer-records-lag (3)

| Metric name | Metric key | Description |
|---|---|---|
| Kafka Consumer - Consumer lag | `kafka.consumer.consumer-metrics.records-lag` | The latest lag of the partition. |
| Kafka Consumer - Consumer lag average | `kafka.consumer.consumer-metrics.records-lag-avg` | The average lag of the partition. |
| Kafka Consumer - Consumer lag maximum | `kafka.consumer.consumer-metrics.records-lag-max` | The max lag of the partition. |

### multi-region-kafka (4)

| Metric name | Metric key | Description |
|---|---|---|
| Kafka Partition - Observer replicas count | `kafka.cluster.Partition.ObserverReplicasCount` | The number of observer replicas for a partition in a Confluent Multi-Region Cluster (MRC), by topic and partition. |
| Kafka Partition - Caught up replicas count | `kafka.cluster.Partition.CaughtUpReplicasCount` | The number of replicas considered caught up to the topic partition leader. This may be greater than the ISR size, as observers may be caught up but not part of the ISR. By topic and partition. |
| Kafka Partition - Is not caught up | `kafka.cluster.Partition.IsNotCaughtUp` | Reports 1 (true) if not all replicas are considered caught up to the partition leader, otherwise 0. By topic and partition. |
| Kafka Partition - Observers in ISR count | `kafka.cluster.Partition.ObserversInIsrCount` | The number of observers that are currently in the in-sync replicas (ISR) list for a partition in a Confluent Multi-Region Cluster (MRC), by topic and partition. |

### Raft Broker (5)

| Metric name | Metric key | Description |
|---|---|---|
| Last applied record offset | `kafka.raft.last-applied-record-offset` | The offset of the last record from the cluster metadata partition that was applied by the broker. |
| Last applied record timestamp | `kafka.raft.last-applied-record-timestamp` | The timestamp of the last record from the cluster metadata partition that was applied by the broker. |
| Last applied record lag | `kafka.raft.last-applied-record-lag-ms` | The difference between now and the timestamp of the last record from the cluster metadata partition that was applied by the broker. |
| Metadata load error count | `kafka.raft.metadata-load-error-count` | The number of errors encountered by the BrokerMetadataListener while loading the metadata log and generating a new MetadataDelta based on it. |
| Metadata apply error count | `kafka.raft.metadata-apply-error-count` | The number of errors encountered by the BrokerMetadataPublisher while applying a new MetadataImage based on the latest MetadataDelta. |

### Raft Quorum (7)

| Metric name | Metric key | Description |
|---|---|---|
| Records appended rate | `kafka.raft.append-records-rate` | The average number of records appended per sec by the leader of the raft quorum. |
| Commit latency (avg) | `kafka.raft.commit-latency-avg` | The average time in milliseconds to commit an entry in the raft log. |
| Commit latency (max) | `kafka.raft.commit-latency-max` | The maximum time in milliseconds to commit an entry in the raft log. |
| Record fetch rate | `kafka.raft.fetch-records-rate` | The average number of records fetched from the leader of the raft quorum. |
| Log end epoch | `kafka.raft.log-end-epoch` | The current raft log end epoch. |
| Log end offset | `kafka.raft.log-end-offset` | The current raft log end offset. |
| Current leader ID | `kafka.raft.current-leader` | The current quorum leader's id; -1 indicates unknown. |

### network-metrics (9)

| Metric name | Metric key | Description |
|---|---|---|
| Kafka Network - Produce requests per second | `kafka.network.RequestMetrics.RequestsPerSec.Produce.OneMinuteRate` | The total number of requests made for produce per second |
| Kafka Network - FetchConsumer requests per second | `kafka.network.RequestMetrics.RequestsPerSec.FetchConsumer.OneMinuteRate` | The total number of requests made for fetch consumer per second |
| Kafka Network - FetchFollower requests per second | `kafka.network.RequestMetrics.RequestsPerSec.FetchFollower.OneMinuteRate` | The total number of requests made for fetch follower per second |
| Kafka Network - Total time per Produce request | `kafka.network.RequestMetrics.TotalTimeMs.Produce.Count` | Total time, in milliseconds, spent processing requests, for produce. |
| Kafka Network - Total time per FetchConsumer request | `kafka.network.RequestMetrics.TotalTimeMs.FetchConsumer.Count` | Total time, in milliseconds, spent processing requests, for fetch consumer. |
| Kafka Network - Total time per FetchFollower request | `kafka.network.RequestMetrics.TotalTimeMs.FetchFollower.Count` | Total time, in milliseconds, spent processing requests, for pfetch follower. |
| Kafka Network - Request queue size | `kafka.network.RequestChannel.RequestQueueSize.Value` | Size of the request queue. |
| Kafka Network - Response queue size | `kafka.network.RequestChannel.ResponseQueueSize.Value` | Size of the response queue. |
| Kafka Network - Network processor average idle percent | `kafka.network.SocketServer.NetworkProcessorAvgIdlePercent` | The average fraction of time the network processor threads are idle. |

### zookeeper-metrics (3)

| Metric name | Metric key | Description |
|---|---|---|
| Kafka Server - ZooKeeper disconnects | `kafka.server.SessionExpireListener.ZooKeeperDisconnectsPerSec.OneMinuteRate` | A meter that provides the number of recent ZooKeeper client disconnects. |
| Kafka Server - ZooKeeper expires | `kafka.server.SessionExpireListener.ZooKeeperExpiresPerSec.OneMinuteRate` | The number of ZooKeeper sessions that have expired. |
| Kafka Server - Zookeeper Active Connections | `kafka.server.active-connections` | The number of currently open connections to the broker. |

### handler-metrics (1)

| Metric name | Metric key | Description |
|---|---|---|
| Kafka Server - Handler Pool Idle Percent Rate | `kafka.server.handler.average-idle-percent.rate` | The average fraction of time the request handler threads are idle. Values are between 0 meaning all resources are used and 1 meaning all resources are available. |

### leader-metrics (3)

| Metric name | Metric key | Description |
|---|---|---|
| Kafka Controller - Leader election rate | `kafka.controller.ControllerStats.LeaderElectionRateAndTimeMs.OneMinuteRate` | The broker leader election rate and latency in milliseconds. This is non-zero when there are broker failures. |
| Kafka Controller - Unclean election rate | `kafka.controller.ControllerStats.UncleanLeaderElectionsPerSec.OneMinuteRate` | The unclean broker leader election rate. Should be 0. |
| Kafka Server - Leader count | `kafka.server.ReplicaManager.LeaderCount.Value` | The number of replicas for which this broker is the leader. |

### consumer-metrics (12)

| Metric name | Metric key | Description |
|---|---|---|
| Kafka Consumer - Requests | `kafka.consumer.consumer-metrics.request-rate` | The average number of requests sent per second for a node. |
| Kafka Consumer - Request size | `kafka.consumer.consumer-metrics.request-size-avg` | The average size of all requests in the window. |
| Kafka Consumer - Incoming byte rate | `kafka.consumer.consumer-metrics.incoming-byte-rate` | Bytes/second read off all sockets. |
| Kafka Consumer - Outgoing byte rate | `kafka.consumer.consumer-metrics.outgoing-byte-rate` | The average number of outgoing bytes sent per second to all servers. |
| Kafka Consumer - Request latency | `kafka.consumer.consumer-metrics.request-latency-avg` | The average request latency in ms for a node. |
| Kafka Consumer - Assigned Partitions | `kafka.consumer.consumer-metrics.assigned-partitions` | The number of partitions currently assigned to the consumer. |
| Kafka Consumer - Messages consumed rate | `kafka.consumer.consumer-metrics.records-consumed-rate` | The average number of records consumed per second for a topic. |
| Kafka Consumer - Bytes consumed rate | `kafka.consumer.consumer-metrics.bytes-consumed-rate` | The average number of bytes consumed per second for a topic. |
| Kafka Consumer - Messages consumed rate total | `kafka.consumer.consumer-metrics.records-consumed-rate-total` | The average number of records consumed per second. |
| Kafka Consumer - Bytes consumed rate total | `kafka.consumer.consumer-metrics.bytes-consumed-rate-total` | The average number of bytes consumed per second. |
| Kafka Consumer - Fetch latency | `kafka.consumer.consumer-metrics.fetch-latency-avg` | The average time taken for a fetch request. |
| Kafka Consumer - Fetch throttle time avg | `kafka.consumer.consumer-fetch-manager-metrics.fetch-throttle-time-avg` | The average time in milliseconds a fetch request was throttled by the broker. |

### producer-metrics (13)

| Metric name | Metric key | Description |
|---|---|---|
| Kafka Producer - Incoming byte rate | `kafka.producer.producer-metrics.incoming-byte-rate` | The average number of responses received per second for a node. |
| Kafka Producer - Outgoing byte rate | `kafka.producer.producer-metrics.outgoing-byte-rate` | The average number of outgoing bytes sent per second to all servers. |
| Kafka Producer - I/O Wait time | `kafka.producer.producer-metrics.io-wait-time-ns-avg` | The average length of time the I/O thread spent waiting for a socket ready for reads or writes in nanoseconds. |
| Kafka Producer - Response rate | `kafka.producer.producer-metrics.response-rate` | Responses received sent per second. |
| Kafka Producer - Request latency | `kafka.producer.producer-metrics.request-latency-avg` | The average request latency in ms. |
| Kafka Producer - Compression rate | `kafka.producer.producer-metrics.compression-rate-avg` | The average compression rate of record batches, defined as the average ratio of the compressed batch size over the uncompressed size. |
| Kafka Producer - Request size | `kafka.producer.producer-metrics.request-size-avg` | The average size of all requests in the window. |
| Kafka Producer - Requests | `kafka.producer.producer-metrics.request-rate` | The average number of requests sent per second. |
| Kafka Producer - Produce throttle time avg | `kafka.producer.producer-metrics.produce-throttle-time-avg` | The average time in milliseconds a request was throttled by the broker. |
| Kafka Producer - Byte rate | `kafka.producer.producer-topic-metrics.byte-rate` | The average number of bytes sent per second for a topic. |
| Kafka Producer - Compression rate | `kafka.producer.producer-topic-metrics.compression-rate` | The average compression rate of record batches for a topic, defined as the average ratio of the compressed batch size over the uncompressed size. |
| Kafka Producer - Failed Requests Rate | `kafka.producer.producer-topic-metrics.record-error-rate` | The average per-second number of record sends that resulted in errors for a topic. |
| Kafka Producer - Requests Sent Rate | `kafka.producer.producer-topic-metrics.record-send-rate` | The average number of records sent per second for a topic. |

### controller-metrics (2)

| Metric name | Metric key | Description |
|---|---|---|
| Kafka Controller - Offline partitions | `kafka.controller.KafkaController.OfflinePartitionsCount` | The number of partitions that don't have an active leader and are therefore not writable or readable. |
| Kafka Controller - Active cluster controllers | `kafka.controller.KafkaController.ActiveControllerCount.Value` | Indicates whether the broker is the controller broker. |

### disk-metrics (2)

| Metric name | Metric key | Description |
|---|---|---|
| Kafka Server - Disk Read Rate | `kafka.server.disk.read-bytes` | The total number of bytes read by the broker process, including reads from all disks. The total doesn't include reads from page cache. Available only on Linux-based systems. |
| Kafka Server - Disk Write Rate | `kafka.server.disk.write-bytes` | The total number of bytes written by the broker process, including writes from all disks. Available only on Linux-based systems. |

## Appendix B: Apache Kafka 3.8 broker MBeans and documented normal values (DOCUMENTED)

Verbatim from [Apache Kafka 3.8 monitoring docs](https://kafka.apache.org/38/operations/monitoring/), extracted 2026-10-12. Every broker metric with a "Normal value" is listed. The Dynatrace column is filled only on an **exact MBean-path match** to an Apache Kafka extension key (Appendix A): `kafka.server:type=ReplicaManager,name=X` matches `kafka.server.ReplicaManager.X...`. "Not collected" means the extension has no such key; collect it another way (for example Prometheus JMX exporter) or derive it. All 21 tables (KRaft quorum, controller and broker, producer, consumer group, consumer fetch, Connect, Streams, tiered storage) are in `research_notes/Dynatrace Kafka triage data models/kafka_3_8_monitoring_mbeans.json`.

| Description | MBean | Normal value | Dynatrace extension key |
|---|---|---|---|
| Message in rate | `kafka.server:type=BrokerTopicMetrics,name=MessagesInPerSec,topic=([-.\w]+)` | Incoming message rate per topic. Omitting ’topic=(…)’ will yield the all-topic rate. | `kafka.server.BrokerTopicMetrics.MessagesInPerSec.OneMinuteRate` |
| Byte in rate from clients | `kafka.server:type=BrokerTopicMetrics,name=BytesInPerSec,topic=([-.\w]+)` | Byte in (from the clients) rate per topic. Omitting ’topic=(…)’ will yield the all-topic rate. | `kafka.server.BrokerTopicMetrics.BytesInPerSec.OneMinuteRate` |
| Byte in rate from other brokers | `kafka.server:type=BrokerTopicMetrics,name=ReplicationBytesInPerSec` | Byte in (from the other brokers) rate across all topics. | not collected by the extension |
| Controller Request rate from Broker | `kafka.controller:type=ControllerChannelManager,name=RequestRateAndQueueTimeMs,brokerId=([0-9]+)` | The rate (requests per second) at which the ControllerChannelManager takes requests from the queue of the given broker. And the time it takes for a request to stay in this queue before it is taken from the queue. | not collected by the extension |
| Controller Event queue size | `kafka.controller:type=ControllerEventManager,name=EventQueueSize` | Size of the ControllerEventManager’s queue. | not collected by the extension |
| Controller Event queue time | `kafka.controller:type=ControllerEventManager,name=EventQueueTimeMs` | Time that takes for any event (except the Idle event) to wait in the ControllerEventManager’s queue before being processed | not collected by the extension |
| Error rate | `kafka.network:type=RequestMetrics,name=ErrorsPerSec,request=([-.\w]+),error=([-.\w]+)` | Number of errors in responses counted per-request-type, per-error-code. If a response contains multiple errors, all are counted. error=NONE indicates successful responses. | not collected by the extension |
| Produce request rate | `kafka.server:type=BrokerTopicMetrics,name=TotalProduceRequestsPerSec,topic=([-.\w]+)` | Produce request rate per topic. Omitting ’topic=(…)’ will yield the all-topic rate. | `kafka.server.BrokerTopicMetrics.TotalProduceRequestsPerSec.OneMinuteRate` |
| Fetch request rate | `kafka.server:type=BrokerTopicMetrics,name=TotalFetchRequestsPerSec,topic=([-.\w]+)` | Fetch request (from clients or followers) rate per topic. Omitting ’topic=(…)’ will yield the all-topic rate. | `kafka.server.BrokerTopicMetrics.TotalFetchRequestsPerSec.OneMinuteRate` |
| Failed produce request rate | `kafka.server:type=BrokerTopicMetrics,name=FailedProduceRequestsPerSec,topic=([-.\w]+)` | Failed Produce request rate per topic. Omitting ’topic=(…)’ will yield the all-topic rate. | `kafka.server.BrokerTopicMetrics.FailedProduceRequestsPerSec.OneMinuteRate` |
| Failed fetch request rate | `kafka.server:type=BrokerTopicMetrics,name=FailedFetchRequestsPerSec,topic=([-.\w]+)` | Failed Fetch request (from clients or followers) rate per topic. Omitting ’topic=(…)’ will yield the all-topic rate. | `kafka.server.BrokerTopicMetrics.FailedFetchRequestsPerSec.OneMinuteRate` |
| Request size in bytes | `kafka.network:type=RequestMetrics,name=RequestBytes,request=([-.\w]+)` | Size of requests for each request type. | not collected by the extension |
| Temporary memory size in bytes | `kafka.network:type=RequestMetrics,name=TemporaryMemoryBytes,request={Produce|Fetch}` | Temporary memory used for message format conversions and decompression. | not collected by the extension |
| Message conversion time | `kafka.network:type=RequestMetrics,name=MessageConversionsTimeMs,request={Produce|Fetch}` | Time in milliseconds spent on message format conversions. | not collected by the extension |
| Message conversion rate | `kafka.server:type=BrokerTopicMetrics,name={Produce|Fetch}MessageConversionsPerSec,topic=([-.\w]+)` | Message format conversion rate, for Produce or Fetch requests, per topic. Omitting ’topic=(…)’ will yield the all-topic rate. | not collected by the extension |
| Request Queue Size | `kafka.network:type=RequestChannel,name=RequestQueueSize` | Size of the request queue. | `kafka.network.RequestChannel.RequestQueueSize.Value` |
| Byte out rate to clients | `kafka.server:type=BrokerTopicMetrics,name=BytesOutPerSec,topic=([-.\w]+)` | Byte out (to the clients) rate per topic. Omitting ’topic=(…)’ will yield the all-topic rate. | `kafka.server.BrokerTopicMetrics.BytesOutPerSec.OneMinuteRate` |
| Byte out rate to other brokers | `kafka.server:type=BrokerTopicMetrics,name=ReplicationBytesOutPerSec` | Byte out (to the other brokers) rate across all topics | not collected by the extension |
| Rejected byte rate | `kafka.server:type=BrokerTopicMetrics,name=BytesRejectedPerSec,topic=([-.\w]+)` | Rejected byte rate per topic, due to the record batch size being greater than max.message.bytes configuration. Omitting ’topic=(…)’ will yield the all-topic rate. | not collected by the extension |
| Message validation failure rate due to no key specified for compacted topic | `kafka.server:type=BrokerTopicMetrics,name=NoKeyCompactedTopicRecordsPerSec` | 0 | not collected by the extension |
| Message validation failure rate due to invalid magic number | `kafka.server:type=BrokerTopicMetrics,name=InvalidMagicNumberRecordsPerSec` | 0 | not collected by the extension |
| Message validation failure rate due to incorrect crc checksum | `kafka.server:type=BrokerTopicMetrics,name=InvalidMessageCrcRecordsPerSec` | 0 | not collected by the extension |
| Message validation failure rate due to non-continuous offset or sequence number in batch | `kafka.server:type=BrokerTopicMetrics,name=InvalidOffsetOrSequenceRecordsPerSec` | 0 | not collected by the extension |
| # of offline log directories | `kafka.log:type=LogManager,name=OfflineLogDirectoryCount` | 0 | not collected by the extension |
| Leader election rate | `kafka.controller:type=ControllerStats,name=LeaderElectionRateAndTimeMs` | non-zero when there are broker failures | `kafka.controller.ControllerStats.LeaderElectionRateAndTimeMs.OneMinuteRate` |
| Unclean leader election rate | `kafka.controller:type=ControllerStats,name=UncleanLeaderElectionsPerSec` | 0 | `kafka.controller.ControllerStats.UncleanLeaderElectionsPerSec.OneMinuteRate` |
| Is controller active on broker | `kafka.controller:type=KafkaController,name=ActiveControllerCount` | only one broker in the cluster should have 1 | `kafka.controller.KafkaController.ActiveControllerCount.Value` |
| # of under replicated partitions (/ISR/ < /all replicas/) | `kafka.server:type=ReplicaManager,name=UnderReplicatedPartitions` | 0 | `kafka.server.ReplicaManager.UnderReplicatedPartitions` |
| # of under minIsr partitions (/ISR/ < min.insync.replicas) | `kafka.server:type=ReplicaManager,name=UnderMinIsrPartitionCount` | 0 | `kafka.server.ReplicaManager.UnderMinIsrPartitionCount` |
| # of at minIsr partitions (/ISR/ = min.insync.replicas) | `kafka.server:type=ReplicaManager,name=AtMinIsrPartitionCount` | 0 | not collected by the extension |
| Producer Id counts | `kafka.server:type=ReplicaManager,name=ProducerIdCount` | Count of all producer ids created by transactional and idempotent producers in each replica on the broker | not collected by the extension |
| Partition counts | `kafka.server:type=ReplicaManager,name=PartitionCount` | mostly even across brokers | `kafka.server.ReplicaManager.PartitionCount` |
| Offline Replica counts | `kafka.server:type=ReplicaManager,name=OfflineReplicaCount` | 0 | not collected by the extension |
| Leader replica counts | `kafka.server:type=ReplicaManager,name=LeaderCount` | mostly even across brokers | `kafka.server.ReplicaManager.LeaderCount.Value` |
| ISR shrink rate | `kafka.server:type=ReplicaManager,name=IsrShrinksPerSec` | If a broker goes down, ISR for some of the partitions will shrink. When that broker is up again, ISR will be expanded once the replicas are fully caught up. Other than that, the expected value for both ISR shrink rate and expansion rate is 0. | not collected by the extension |
| ISR expansion rate | `kafka.server:type=ReplicaManager,name=IsrExpandsPerSec` | See above | not collected by the extension |
| Failed ISR update rate | `kafka.server:type=ReplicaManager,name=FailedIsrUpdatesPerSec` | 0 | not collected by the extension |
| Max lag in messages btw follower and leader replicas | `kafka.server:type=ReplicaFetcherManager,name=MaxLag,clientId=Replica` | lag should be proportional to the maximum batch size of a produce request. | `kafka.server.ReplicaFetcherManager.MaxLag.Replica.Value` |
| Lag in messages per follower replica | `kafka.server:type=FetcherLagMetrics,name=ConsumerLag,clientId=([-.\w]+),topic=([-.\w]+),partition=([0-9]+)` | lag should be proportional to the maximum batch size of a produce request. | `kafka.server.FetcherLagMetrics.ConsumerLag.Value` |
| Requests waiting in the producer purgatory | `kafka.server:type=DelayedOperationPurgatory,name=PurgatorySize,delayedOperation=Produce` | non-zero if ack=-1 is used | not collected by the extension |
| Requests waiting in the fetch purgatory | `kafka.server:type=DelayedOperationPurgatory,name=PurgatorySize,delayedOperation=Fetch` | size depends on fetch.wait.max.ms in the consumer | not collected by the extension |
| Request total time | `kafka.network:type=RequestMetrics,name=TotalTimeMs,request={Produce|FetchConsumer|FetchFollower}` | broken into queue, local, remote and response send time | `kafka.network.RequestMetrics.TotalTimeMs.Produce.Count`<br>`kafka.network.RequestMetrics.TotalTimeMs.FetchConsumer.Count`<br>`kafka.network.RequestMetrics.TotalTimeMs.FetchFollower.Count` |
| Time the request waits for the follower | `kafka.network:type=RequestMetrics,name=RemoteTimeMs,request={Produce|FetchConsumer|FetchFollower}` | non-zero for produce requests when ack=-1 | not collected by the extension |
| The average fraction of time the network processors are idle | `kafka.network:type=SocketServer,name=NetworkProcessorAvgIdlePercent` | between 0 and 1, ideally > 0.3 | `kafka.network.SocketServer.NetworkProcessorAvgIdlePercent` |
| The number of connections disconnected on a processor due to a client not re-authenticating and then using the connection beyond its expiration time for anything other than re-authentication | `kafka.server:type=socket-server-metrics,listener=[SASL_PLAINTEXT|SASL_SSL],networkProcessor=<#>,name=expired-connections-killed-count` | ideally 0 when re-authentication is enabled, implying there are no longer any older, pre-2.2.0 clients connecting to this (listener, processor) combination | not collected by the extension |
| The total number of connections disconnected, across all processors, due to a client not re-authenticating and then using the connection beyond its expiration time for anything other than re-authentication | `kafka.network:type=SocketServer,name=ExpiredConnectionsKilledCount` | ideally 0 when re-authentication is enabled, implying there are no longer any older, pre-2.2.0 clients connecting to this broker | not collected by the extension |
| The average fraction of time the request handler threads are idle | `kafka.server:type=KafkaRequestHandlerPool,name=RequestHandlerAvgIdlePercent` | between 0 and 1, ideally > 0.3 | not collected by the extension |
| Bandwidth quota metrics per (user, client-id), user or client-id | `kafka.server:type={Produce|Fetch},user=([-.\w]+),client-id=([-.\w]+)` | Two attributes. throttle-time indicates the amount of time in ms the client was throttled. Ideally = 0. byte-rate indicates the data produce/consume rate of the client in bytes/sec. For (user, client-id) quotas, both user and client-id are specified. If per-client-id quota is applied to the client, user is not specified. If per-user quota is applied, client-id is not specified. | not collected by the extension |
| Request quota metrics per (user, client-id), user or client-id | `kafka.server:type=Request,user=([-.\w]+),client-id=([-.\w]+)` | Two attributes. throttle-time indicates the amount of time in ms the client was throttled. Ideally = 0. request-time indicates the percentage of time spent in broker network and I/O threads to process requests from client group. For (user, client-id) quotas, both user and client-id are specified. If per-client-id quota is applied to the client, user is not specified. If per-user quota is applied, client-id is not specified. | not collected by the extension |
| Requests exempt from throttling | `kafka.server:type=Request` | exempt-throttle-time indicates the percentage of time spent in broker network and I/O threads to process requests that are exempt from throttling. | not collected by the extension |
| ZooKeeper client request latency | `kafka.server:type=ZooKeeperClientMetrics,name=ZooKeeperRequestLatencyMs` | Latency in milliseconds for ZooKeeper requests from broker. | not collected by the extension |
| ZooKeeper connection status | `kafka.server:type=SessionExpireListener,name=SessionState` | Connection status of broker’s ZooKeeper session which may be one of Disconnected/SyncConnected/AuthFailed/ConnectedReadOnly/SaslAuthenticated/Expired. | not collected by the extension |
| Max time to load group metadata | `kafka.server:type=group-coordinator-metrics,name=partition-load-time-max` | maximum time, in milliseconds, it took to load offsets and group metadata from the consumer offset partitions loaded in the last 30 seconds (including time spent waiting for the loading task to be scheduled) | not collected by the extension |
| Avg time to load group metadata | `kafka.server:type=group-coordinator-metrics,name=partition-load-time-avg` | average time, in milliseconds, it took to load offsets and group metadata from the consumer offset partitions loaded in the last 30 seconds (including time spent waiting for the loading task to be scheduled) | not collected by the extension |
| Max time to load transaction metadata | `kafka.server:type=transaction-coordinator-metrics,name=partition-load-time-max` | maximum time, in milliseconds, it took to load transaction metadata from the consumer offset partitions loaded in the last 30 seconds (including time spent waiting for the loading task to be scheduled) | not collected by the extension |
| Avg time to load transaction metadata | `kafka.server:type=transaction-coordinator-metrics,name=partition-load-time-avg` | average time, in milliseconds, it took to load transaction metadata from the consumer offset partitions loaded in the last 30 seconds (including time spent waiting for the loading task to be scheduled) | not collected by the extension |
| Rate of transactional verification errors | `kafka.server:type=AddPartitionsToTxnManager,name=VerificationFailureRate` | Rate of verifications that returned in failure either from the AddPartitionsToTxn API response or through errors in the AddPartitionsToTxnManager. In steady state 0, but transient errors are expected during rolls and reassignments of the transactional state partition. | not collected by the extension |
| Time to verify a transactional request | `kafka.server:type=AddPartitionsToTxnManager,name=VerificationTimeMs` | The amount of time queueing while a possible previous request is in-flight plus the round trip to the transaction coordinator to verify (or not verify) | not collected by the extension |
| Consumer Group Offset Count | `kafka.server:type=GroupMetadataManager,name=NumOffsets` | Total number of committed offsets for Consumer Groups | not collected by the extension |
| Consumer Group Count | `kafka.server:type=GroupMetadataManager,name=NumGroups` | Total number of Consumer Groups | not collected by the extension |
| Consumer Group Count, per State | `kafka.server:type=GroupMetadataManager,name=NumGroups[PreparingRebalance,CompletingRebalance,Empty,Stable,Dead]` | The number of Consumer Groups in each state: PreparingRebalance, CompletingRebalance, Empty, Stable, Dead | not collected by the extension |
| Number of reassigning partitions | `kafka.server:type=ReplicaManager,name=ReassigningPartitions` | The number of reassigning leader partitions on a broker. | not collected by the extension |
| Outgoing byte rate of reassignment traffic | `kafka.server:type=BrokerTopicMetrics,name=ReassignmentBytesOutPerSec` | 0; non-zero when a partition reassignment is in progress. | not collected by the extension |
| Incoming byte rate of reassignment traffic | `kafka.server:type=BrokerTopicMetrics,name=ReassignmentBytesInPerSec` | 0; non-zero when a partition reassignment is in progress. | not collected by the extension |
| Size of a partition on disk (in bytes) | `kafka.log:type=Log,name=Size,topic=([-.\w]+),partition=([0-9]+)` | The size of a partition on disk, measured in bytes. | not collected by the extension |
| Number of log segments in a partition | `kafka.log:type=Log,name=NumLogSegments,topic=([-.\w]+),partition=([0-9]+)` | The number of log segments in a partition. | not collected by the extension |
| First offset in a partition | `kafka.log:type=Log,name=LogStartOffset,topic=([-.\w]+),partition=([0-9]+)` | The first offset in a partition. | not collected by the extension |
| Last offset in a partition | `kafka.log:type=Log,name=LogEndOffset,topic=([-.\w]+),partition=([0-9]+)` | The last offset in a partition. | `kafka.log.Log.LogEndOffset` |
