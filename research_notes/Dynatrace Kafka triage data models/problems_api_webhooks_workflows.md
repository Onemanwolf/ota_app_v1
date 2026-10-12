# Dynatrace problem delivery formats: Problems API v2, classic custom-integration webhook, Workflows problem trigger

Research date: 2026-10-12. Primary sources: docs.dynatrace.com pages, fetched raw. The JSON code blocks were pulled from the page HTML, so the field names below are verbatim.

Labels used throughout:
- **DOCUMENTED** means the field name, enum or value appears in the cited page.
- **INFERRED** means the value is a realistic placeholder I made up for fixtures, or a conclusion I drew. It is not stated in a source.

Community.dynatrace.com threads returned HTTP 403 to every fetcher, and the GitHub code search hit its rate limit. Claims that come from those threads were seen only as search-engine snippets and are marked as such.

---

## Q1. Problems API v2: `GET /api/v2/problems` and `GET /api/v2/problems/{problemId}`

### Takeaway
The v2 schema is fully documented. The docs give only a schema-shaped example (`"string"`/`1` values) and no real-valued one. Watch for these gotchas:
- `impactLevel` uses **`SERVICES`** (plural).
- `evidenceType`-specific fields (`eventType`, `data`, `unit`, `valueBeforeChangePoint`, `metricId`…) exist only on subtypes.
- `endTime` is `-1` while the problem is open.
- `problemFilters` are alerting profiles.
- `fields` takes a plain comma list (`evidenceDetails,impactAnalysis`). The docs show no `+` prefix.

### Cited Findings

**Endpoint, auth and query params** (DOCUMENTED, [get-problems-list](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems-v2/problems/get-problems-list))
- SaaS URL: `https://{your-environment-id}.live.dynatrace.com/api/v2/problems`. The token needs the `problems.read` scope.
- `fields`: the additional properties `evidenceDetails`, `impactAnalysis` and `recentComments`. The docs say "To add properties, specify them as a comma-separated list (for example, evidenceDetails,impactAnalysis)". The field "is valid only for the current page of results. You must set it for each page you're requesting."
- `nextPageKey`: when you set it, "you must omit all other query parameters except the optional fields parameter."
- `pageSize`: maximum 500, default 50.
- `from`: accepts UTC ms, ISO-like strings (`2021-01-25T05:57:01.123+01:00`) or relative `now-NU/A` (e.g. `now-1y/w`). The default is `now-2h` (the default comes from the WebFetch summary of the same page).
- `to`: defaults to the current timestamp.
- `problemSelector` criteria are quoted and comma-separated. Multiple values OR together, multiple criteria AND together:
  - `status("open")` or `status("closed")`, one value only
  - `severityLevel("level-1","level-2")`, `impactLevel(...)`
  - `rootCauseEntity(...)`, `managementZoneIds(...)`, `managementZones(...)` (contains, case-insensitive)
  - `impactedEntities(...)`, `affectedEntities(...)`, `affectedEntityTypes(...)`
  - `problemId(...)`, `displayId(...)`
  - `problemFilterIds(...)`, `problemFilterNames(...)`, `problemFilterNames.equals(...)`
  - `entityTags("[context]key:value","key:value")`
  - `underMaintenance(true|false)`
  - `text("value")`: max 30 chars, `~` escapes; use `sort=relevance`
- `entitySelector` requires `type("TYPE")` or `entityId(...)`.
- `sort` accepts `status`, `startTime` and `relevance`, with a `+`/`-` prefix.
- Details endpoint `GET /api/v2/problems/{problemId}` takes only `fields` and returns a single `Problem` object ([get-problem-details](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems-v2/problems/get-problem-details)).

**Response model** (DOCUMENTED, [get-problems-list](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems-v2/problems/get-problems-list); [get-problem-details](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems-v2/problems/get-problem-details))
- `Problems`: `nextPageKey`, `pageSize`, `problems` (Problem[]), `totalCount`, `warnings` (string[]).
- `Problem` fields:
  - `affectedEntities` (EntityStub[], "all entities that are affected by the problem")
  - `displayId` ("The display ID of the problem")
  - `endTime` (UTC ms, "Has -1 value, if the problem is still open")
  - `entityTags` (METag[])
  - `evidenceDetails`
  - `impactAnalysis`
  - `impactLevel`
  - `impactedEntities` (EntityStub[], "all entities that are impacted by the problem")
  - `k8s.cluster.name`, `k8s.cluster.uid`, `k8s.namespace.name` (each string[])
  - `linkedProblemInfo` (`displayId`, `problemId`)
  - `managementZones` (`id`, `name`)
  - `problemFilters` (AlertingProfileStub `id`, `name`, "A list of alerting profiles that match the problem")
  - `problemId`
  - `recentComments` (CommentsList)
  - `rootCauseEntity` (EntityStub)
  - `severityLevel`, `startTime`, `status`
  - `title` ("The name of the problem, displayed in the UI")
- Enums:
  - `impactLevel`: `APPLICATION`, `ENVIRONMENT`, `INFRASTRUCTURE`, `SERVICES`
  - `severityLevel`: `AVAILABILITY`, `CUSTOM_ALERT`, `ERROR`, `INFO`, `MONITORING_UNAVAILABLE`, `PERFORMANCE`, `RESOURCE_CONTENTION`
  - `status`: `CLOSED`, `OPEN`
- `EntityStub`: `entityId` {`id`, `type`}, plus `name`, which is "Not included in the response in case no entity with the relevant ID was found."
- `METag`: `context` (custom tags use `CONTEXTLESS`), `key`, `stringRepresentation`, `value`.
- `CommentsList`: `comments`, `nextPageKey`, `pageSize`, `totalCount`. `Comment`: `authorName`, `content`, `context`, `createdAtTimestamp`, `id`.
- `ErrorEnvelope`: `error` {`code`, `constraintViolations`[{`location`, `message`, `parameterLocation` (HEADER/PATH/PAYLOAD_BODY/QUERY), `path`}], `message`}.

**Evidence and Impact subtypes** (DOCUMENTED, [Problems API v2 JSON models](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems-v2/models))
- Base `Evidence`: `displayName`, `entity`, `evidenceType`, `groupingEntity`, `rootCauseRelevant`, `startTime`.
- `evidenceType` values: `AVAILABILITY_EVIDENCE`, `EVENT`, `MAINTENANCE_WINDOW`, `METRIC`, `TRANSACTIONAL`.
- Subtype extra fields:
  - `AVAILABILITY_EVIDENCE` adds `endTime`.
  - `EVENT` (EventEvidence) adds `data` (Event object), `endTime` ("-1 if the event is still active"), `eventId` and `eventType`.
  - The `Event` object holds `correlationId`, `endTime` (null if active), `entityId` (EntityStub), `entityTags`, `eventId`, `eventType`, `frequentEvent`, `managementZones`, `properties` [{`key`, `value`}], `startTime`, `status` (CLOSED/OPEN), `suppressAlert`, `suppressProblem`, `title` and `underMaintenance`.
  - `MAINTENANCE_WINDOW` adds `endTime` and `maintenanceWindowConfigId`.
  - `METRIC` adds `endTime` (null = still open), `metricId`, `unit` (enum such as `Count`, `MilliSecond`, `Percent`, `Byte`, `MicroSecond`…), `valueAfterChangePoint` and `valueBeforeChangePoint`.
  - `TRANSACTIONAL` adds `endTime`, `unit`, `valueAfterChangePoint` and `valueBeforeChangePoint`. It has **no** `metricId`.
- The doc's EVENT JSON example shows only `eventId` and `eventType` beyond the base fields; it omits `data` and `endTime`, which the table lists. Fixtures should allow `data` and `endTime` to be absent.
- `Impact`: `estimatedAffectedUsers`, `impactType` (`APPLICATION`, `CUSTOM_APPLICATION`, `MOBILE`, `SERVICE`), `impactedEntity`. The `SERVICE` impact adds `numberOfPotentiallyAffectedServiceCalls`.
- Documented subtype examples, verbatim:
  - Metric: `{"evidenceType":"METRIC", ..., "startTime":1602500000000, "metricId":"string", "valueBeforeChangePoint":2, "valueAfterChangePoint":3, "unit":"Count", "endTime":1602600000000}`
  - Service impact: `{"impactType":"SERVICE", "impactedEntity":{...}, "estimatedAffectedUsers":5, "numberOfPotentiallyAffectedServiceCalls":50}`

**Documented schema example** (verbatim structure, values are doc placeholders) from [get-problems-list](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems-v2/problems/get-problems-list):
```json
{ "nextPageKey": "AQAAABQBAAAABQ==", "pageSize": 1,
  "problems": [ { "affectedEntities": [ { "entityId": { "id": "string", "type": "string" }, "name": "string" } ],
      "displayId": "string", "endTime": 1,
      "entityTags": [ { "context": "string", "key": "string", "stringRepresentation": "string", "value": "string" } ],
      "evidenceDetails": { "details": [ { "displayName": "string", "entity": {}, "evidenceType": "AVAILABILITY_EVIDENCE", "groupingEntity": {}, "rootCauseRelevant": true, "startTime": 1 } ], "totalCount": 1 },
      "impactAnalysis": { "impacts": [ { "estimatedAffectedUsers": 1, "impactType": "APPLICATION", "impactedEntity": {} } ] },
      "impactLevel": "APPLICATION", "impactedEntities": [ {} ],
      "k8s.cluster.name": [ "string" ], "k8s.cluster.uid": [ "string" ], "k8s.namespace.name": [ "string" ],
      "linkedProblemInfo": { "displayId": "string", "problemId": "string" },
      "managementZones": [ { "id": "string", "name": "string" } ],
      "problemFilters": [ { "id": "string", "name": "string" } ],
      "problemId": "string",
      "recentComments": { "comments": [ { "authorName": "string", "content": "string", "context": "string", "createdAtTimestamp": 1, "id": "string" } ], "nextPageKey": "AQAAABQBAAAABQ==", "pageSize": 1, "totalCount": 1 },
      "rootCauseEntity": {}, "severityLevel": "AVAILABILITY", "startTime": 1, "status": "CLOSED", "title": "string" } ],
  "totalCount": 1, "warnings": [ "string" ] }
```

**Real-valued ID formats** (DOCUMENTED, from the v1 details example on [Problems API v1 GET details](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems/problems/get-details), which is deprecated)
- Problem ID: `"2307087411653364173_1538400720000V2"`.
- Display ID: `"173"` in v1. The Grail `display_id` example is `P-2307288` ([Davis semantic dictionary](https://docs.dynatrace.com/docs/semantic-dictionary/model/davis)).
- Entity IDs: `PROCESS_GROUP-09875E82E2FB98FD`, `HOST-ED6BC51DEBA8093A`.
- Tags: `{"context":"CONTEXTLESS","key":"loadtest"}` (value absent for key-only tags).

**Realistic v2 fixture** (shape DOCUMENTED, all values INFERRED except the ID formats above):
```json
{
  "totalCount": 1, "pageSize": 50,
  "problems": [{
    "problemId": "-4721069355830129151_1760280420000V2",
    "displayId": "P-25101234",
    "title": "Failure rate increase",
    "impactLevel": "SERVICES",
    "severityLevel": "ERROR",
    "status": "OPEN",
    "startTime": 1760280420000,
    "endTime": -1,
    "affectedEntities": [{"entityId": {"id": "SERVICE-8A2D3C4B5E6F7081", "type": "SERVICE"}, "name": "orders-consumer"}],
    "impactedEntities": [{"entityId": {"id": "SERVICE-8A2D3C4B5E6F7081", "type": "SERVICE"}, "name": "orders-consumer"}],
    "rootCauseEntity": {"entityId": {"id": "PROCESS_GROUP_INSTANCE-1A2B3C4D5E6F7A8B", "type": "PROCESS_GROUP_INSTANCE"}, "name": "kafka-broker-2"},
    "managementZones": [{"id": "4567890123456789012", "name": "Kafka Prod"}],
    "entityTags": [{"context": "CONTEXTLESS", "key": "team", "value": "streaming", "stringRepresentation": "team:streaming"}],
    "problemFilters": [{"id": "c1b2a3d4-0000-4000-8000-000000000001", "name": "Kafka on-call"}],
    "evidenceDetails": {"totalCount": 2, "details": [
      {"evidenceType": "EVENT", "displayName": "Failure rate increase", "entity": {"entityId": {"id": "SERVICE-8A2D3C4B5E6F7081", "type": "SERVICE"}, "name": "orders-consumer"}, "groupingEntity": {"entityId": {"id": "SERVICE-8A2D3C4B5E6F7081", "type": "SERVICE"}, "name": "orders-consumer"}, "rootCauseRelevant": false, "startTime": 1760280420000, "endTime": -1, "eventId": "-1234567890123456789_1760280420000", "eventType": "SERVICE_ERROR_RATE_INCREASED"},
      {"evidenceType": "METRIC", "displayName": "CPU usage", "entity": {"entityId": {"id": "HOST-ED6BC51DEBA8093A", "type": "HOST"}, "name": "kafka-node-2"}, "groupingEntity": {"entityId": {"id": "PROCESS_GROUP_INSTANCE-1A2B3C4D5E6F7A8B", "type": "PROCESS_GROUP_INSTANCE"}, "name": "kafka-broker-2"}, "rootCauseRelevant": true, "startTime": 1760280300000, "metricId": "builtin:host.cpu.usage", "unit": "Percent", "valueBeforeChangePoint": 41.2, "valueAfterChangePoint": 97.8}
    ]},
    "impactAnalysis": {"impacts": [{"impactType": "SERVICE", "impactedEntity": {"entityId": {"id": "SERVICE-8A2D3C4B5E6F7081", "type": "SERVICE"}, "name": "orders-consumer"}, "estimatedAffectedUsers": 0, "numberOfPotentiallyAffectedServiceCalls": 1820}]},
    "linkedProblemInfo": null,
    "k8s.cluster.name": [], "k8s.cluster.uid": [], "k8s.namespace.name": []
  }]
}
```

### Inferences
- The docs never state whether `linkedProblemInfo`, `k8s.*`, `evidenceDetails`, `impactAnalysis` and `recentComments` are omitted or null when absent or not requested. Parsers should accept both. (INFERRED)
- Live `displayId` values look like `P-<digits>` in current SaaS, but the v1 example shows a bare number. Don't validate the format strictly. (INFERRED)
- v2 and Grail spell categories differently, and the classic placeholder docs use a third spelling:
  - v2 `severityLevel` says `PERFORMANCE`; Grail `event.category` says `SLOWDOWN`.
  - v2 `impactLevel` says `SERVICES`; the classic `{ProblemImpact}` docs say `SERVICE`.
  - Normalize all of these. (INFERRED)

### Gaps
- No documented real-valued v2 JSON example exists; the docs only use `"string"`/`1` values.
- The v2 docs do not document an `eventType` enum for evidence. The terraform `davis_event.types` list ([terraform automation_workflow doc](https://raw.githubusercontent.com/dynatrace-oss/terraform-provider-dynatrace/main/docs/resources/automation_workflow.md)) gives realistic names: `SERVICE_ERROR_RATE_INCREASED`, `PROCESS_CRASHED`, `OSI_HIGH_CPU`, `SERVICE_SLOWDOWN`, `CONNECTION_LOST` (the last from the v1 example).

---

## Q2. Classic custom-integration (webhook) placeholders and payloads

### Takeaway
The authoritative placeholder list lives in the `builtin:problem.notifications` settings schema table. The doc pages only say "see Available placeholders".

`{State}` is documented as **OPEN or RESOLVED**. `MERGED` is not in the current docs. Community snippets describe it as a pre-AI-2.0 behaviour, sent only when the "notify on event merges" option fires.

The "Send test notification" payload uses fixed test data:
- `ProblemID "999"`, `PID "99999"`, `ProblemImpact "INFRASTRUCTURE"`, `State "OPEN"`
- title "Dynatrace problem notification test run"
- `ProblemDetailsJSON {"ID":"99999"}`
- Tags `"testtag1, testtag2"`

### Cited Findings

**Full webhook placeholder list** (DOCUMENTED, verbatim descriptions, from the webhook `payload` field in [Settings API: Problem notifications schema](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/schemas/builtin-problem-notifications))
- `{ImpactedEntities}`: "Details about the entities impacted by the problem in form of a json array."
- `{ImpactedEntity}`: "A short description of the problem and impacted entity (or multiple impacted entities)."
- `{ImpactedEntityNames}`: "The entity impacted by the problem."
- `{NamesOfImpactedEntities}`: "The names of all entities that are impacted by the problem."
- `{PID}`: "Unique system identifier of the reported problem."
- `{ProblemDetailsHTML}`: "All problem event details including root cause as an HTML-formatted string."
- `{ProblemDetailsJSONv2}`: "Problem as json object following the structure from the Dynatrace Problems V2 API. The optional fields evidenceDetails and impactAnalysis are included, but recentComments is not."
- `{ProblemDetailsJSON}`: "Problem as json object following the structure from the Dynatrace Problems V1 API."
- `{ProblemDetailsMarkdown}`: "All problem event details including root cause as a Markdown-formatted string."
- `{ProblemDetailsText}`: "All problem event details including root cause as a text-formatted string."
- `{ProblemID}`: "Display number of the reported problem."
- `{ProblemImpact}`: "Impact level of the problem. Possible values are APPLICATION, SERVICE, or INFRASTRUCTURE."
- `{ProblemSeverity}`: "Severity level of the problem. Possible values are AVAILABILITY, ERROR, PERFORMANCE, RESOURCE_CONTENTION, or CUSTOM_ALERT."
- `{ProblemTitle}`: "Short description of the problem."
- `{ProblemURL}`: "URL of the problem within Dynatrace."
- `{State}`: "Problem state. Possible values are OPEN or RESOLVED."
- `{Tags}`: "Comma separated list of tags that are defined for all impacted entities. To refer to the value of a specific tag, specify the tag's key in square brackets: {Tags[key]}. If the tag does not have any assigned value, the placeholder will be replaced by an empty string. The placeholder will not be replaced if the tag key does not exist."

**Placeholder sets differ per channel** (same source):
- Email subject and Slack get the short set: no `{ImpactedEntities}` and no `ProblemDetails*`.
- Some channels get `{ProblemDetailsText}` only.
- The webhook and email body get the full list above.

**Webhook settings object** (DOCUMENTED, same source)
- Fields: `url` / `secretUrl`, `acceptAnyCertificate`, `notifyEventMergesEnabled` ("Call webhook if new events merge into existing problems"), `notifyClosedProblems` ("Call webhook if problem is closed"), `useOAuth2`, `oAuth2Credentials`, `timeoutInSeconds`, `headers` (Set<WebHookNotificationHeader>) and `payload`.
- When OAuth 2.0 is used, the token is sent "in the Authorization header of the request carrying the notification payload".

**Quoting rule** (DOCUMENTED, [webhook-integration](https://docs.dynatrace.com/docs/analyze-explore-automate/notifications-and-alerting/problem-notifications/webhook-integration))
- "{ImpactedEntities} and {ProblemDetailsJSON} are JSON data types and must not have quotes around them." It is INFERRED that `{ProblemDetailsJSONv2}` also renders as a raw object, since the schema calls it a "json object".
- The page tags this as "Notifications Classic".

**Documented template and rendered test payload** (DOCUMENTED, verbatim, [webhook-integration](https://docs.dynatrace.com/docs/analyze-explore-automate/notifications-and-alerting/problem-notifications/webhook-integration))
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
Rendered:
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
- This rendered sample is the test-notification data. The title is "Dynatrace problem notification test run".
- The `{ImpactedEntities}` element shape is `{type, name, entity}`, where `entity` is the entity ID.
- "Problem URL" is a literal in the template, not `{ProblemURL}`.

**Dynatrace blog test body** (DOCUMENTED but older, [Dynatrace blog: How to write a Custom Dynatrace Problem Notification Integration](https://www.dynatrace.com/news/blog/how-to-write-a-custom-dynatrace-problem-notification-integration/), published 2018-04-05, updated 2021-06-29)
- A logged test request showed `"State":"OPEN"`, `"ProblemID":"TESTID"` and `"ProblemTitle":"Dynatrace problem notification test run"`, with the same two `ImpactedEntities` objects.
- The test ID therefore varied over time: "TESTID" in 2018–2021, "999" in the current docs.

**What `{ProblemDetailsJSON}` contains for a real problem** (DOCUMENTED, [Problems API v1 GET details](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems/problems/get-details), v1 is deprecated)
- Per the schema, it follows the Problems V1 API structure. The v1 details `result` object has:
  - `id`, `startTime`, `endTime`, `displayName`, `impactLevel`, `status`, `severityLevel`, `commentCount`, `hasRootCause`
  - `tagsOfAffectedEntities` [{`context`, `key`, `value`}]
  - `rankedEvents` [many fields, incl. `entityId`, `entityName`, `eventType`, `isRootCause`, `severities` [{`context`, `value`, `unit`}], `status`]
  - `rankedImpacts` [{`entityId`, `entityName`, `severityLevel`, `impactLevel`, `eventType`, `resourceId`, `resourceName`}]
  - `affectedCounts` / `recoveredCounts` {`INFRASTRUCTURE`, `SERVICE`, `APPLICATION`, `ENVIRONMENT`}
- The docs' real-valued v1 example uses the key **`"details": "OPEN"`** where the schema says `status`. This appears to be a doc quirk; treat `status` as canonical. (INFERRED)

**MERGED state**
- The current docs list only OPEN and RESOLVED ([schema](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/schemas/builtin-problem-notifications)).
- A Dynatrace community answer, seen only as a search snippet because the page returns 403, says merges mostly happen before a problem is raised. When a merge happens after a problem is open, "problem notification sends out a MERGED state instead of a OPEN or RESOLVED state" ([community thread](https://community.dynatrace.com/t5/Alerting/Question-about-Problem-quot-MERGED-quot/m-p/213078)).
- Another snippet says that after AI 2.0 "there is no MERGED state anymore, just open and close" ([community thread](https://community.dynatrace.com/t5/Alerting/Notifications-of-problem-changes-in-AI-2-0/m-p/116238)).
- The Grail model now represents merges as `dt.davis.is_duplicate` and `duplicate_problem_ids` ([Davis semantic dictionary](https://docs.dynatrace.com/docs/semantic-dictionary/model/davis)).

**Realistic rendered fixture** (shape DOCUMENTED, values INFERRED):
```json
{
  "ImpactedEntities": [{"type": "SERVICE", "name": "orders-consumer", "entity": "SERVICE-8A2D3C4B5E6F7081"}],
  "ImpactedEntity": "Failure rate increase on Web service orders-consumer",
  "PID": "-4721069355830129151_1760280420000V2",
  "ProblemID": "P-25101234",
  "ProblemImpact": "SERVICE",
  "ProblemSeverity": "ERROR",
  "ProblemTitle": "Failure rate increase",
  "ProblemURL": "https://abc12345.live.dynatrace.com/#problems/problemdetails;pid=-4721069355830129151_1760280420000V2",
  "State": "OPEN",
  "Tags": "team:streaming, env:prod",
  "ProblemDetailsJSONv2": { "...": "Problem v2 object incl. evidenceDetails + impactAnalysis, no recentComments" }
}
```

### Inferences
- Classic `{PID}` corresponds to v2 `problemId`, and `{ProblemID}` to v2 `displayId`. This follows from the "Unique system identifier" vs "Display number" descriptions and from the upgrade-guide mapping in Q3. (INFERRED)
- A closed problem sends `State: "RESOLVED"` only if `notifyClosedProblems` is true. A merge re-notification occurs only if `notifyEventMergesEnabled` is true. Whether that re-notification carries `State: "OPEN"` or `MERGED` today is unconfirmed; treat `MERGED` as a legacy value the parser should tolerate. (INFERRED)
- The `{ProblemURL}` format above is invented; the docs give no real example.

### Gaps
- The exact formats of `{ImpactedEntity}`, `{ImpactedEntityNames}` and `{NamesOfImpactedEntities}` for real problems are not found. Only the test value "MyHost1, MyService1" is documented.
- No real (non-test) `{ProblemDetailsMarkdown}`, `{ProblemDetailsText}` or `{ProblemDetailsHTML}` rendering was found.
- The real `{ProblemURL}` format is not found.
- Default Content-Type and headers for the webhook POST are not found in the fetched pages.
- Teams, Slack and ServiceNow classic payloads with real field values are not found. The integration pages only point to "Available placeholders".

---

## Q3. Workflows problem trigger: `event()` contents, trigger options, placeholder mapping

### Takeaway
- In Workflows, `event()` returns the **Grail Davis problem record** (`dt.davis.problems`), which has flat dotted keys. Read them with `event()['event.status']`.
- Its status is `ACTIVE`/`CLOSED`. Do not expect `OPEN`/`RESOLVED`.
- Its category uses `SLOWDOWN` for what classic and v2 call `PERFORMANCE`.
- Entities are `smartscape.affected_entities` records of the form `{id, type, name}`.
- Timestamps are nanosecond precision.
- The classic `ProblemDetailsJSON` cannot be reproduced.

### Cited Findings

**Trigger options** (DOCUMENTED, [Workflows event trigger](https://docs.dynatrace.com/docs/analyze-explore-automate/workflows/build/trigger/event-trigger))
- The event source is Davis problems, explorable with `fetch dt.davis.problems`. The raw stream is `fetch events | filter event.kind == "DAVIS_PROBLEM"`.
- **Problem state**: `active` (default), `active or closed`, `closed`.
- **Event category**: the category filter.
- **Severity**: "at or above which" problems trigger.
- **Affected entities**: "Include all entities" / "Include entities with all defined tags below" / "Include entities with any defined tag below".
- **Advanced options**:
  - **Minimum duration**: 5, 10, 15, 30, 60, 120, 240, 1440 or 10080 min, based on `dt.duration_marker`.
  - **Updates**: re-trigger when selected fields change; "Each distinct combination of selected field values triggers the workflow at most once".
  - **Wait for root cause analysis**.
  - **Additional custom filter query**: a DQL matcher.
- "Without Updates enabled, the trigger starts once per state transition."
- Limits:
  - The filter expression is limited to 1,000 characters across all fields.
  - Each workflow allows 1,000 event-triggered executions/hour, after which HTTP 429 throttling applies.
  - The trigger is auto-deactivated if the limit is exceeded 3× in 7 days.
  - The permission needed is `storage:events:read`.
- The upgrade guide says repeated limit hits do *not* auto-deactivate the trigger ([upgrade guide](https://docs.dynatrace.com/docs/manage/upgrade-guide-landing-page/upgrade-guide-alert-notification)), which contradicts the event-trigger page.

**Terraform `davis_problem` trigger config** (DOCUMENTED, [terraform-provider-dynatrace automation_workflow docs](https://raw.githubusercontent.com/dynatrace-oss/terraform-provider-dynatrace/main/docs/resources/automation_workflow.md)). These map to the stored trigger JSON:
- `categories` {`availability`, `custom`, `error`, `info`, `monitoring_unavailable`, `resource`, `slowdown`}
- `analysis_ready` (wait for RCA)
- `custom_filter`
- `entity_tags` (map), `entity_tags_match` (`all`/`any`)
- `problem_open_duration`
- `severity_threshold` (1 = critical … 5 = informational)
- `trigger_on` (`open`, `open-and-close`, `close`)
- `trigger_on_update_fields` ∈ {`dt.davis.affected_users_count`, `dt.davis.impact_level`, `event.category`, `event.severity`, `root_cause_entity_id`, `smartscape.affected_entities`}
- `on_problem_close` (deprecated)

**`event()` and Jinja** (DOCUMENTED, [Jinja expressions for Workflows](https://docs.dynatrace.com/docs/analyze-explore-automate/workflows/reference))
- `event()` "Returns: event object which was passed via execution params or None".
- Events are flattened, so dotted keys need `event()['a.b']` or `event().get('a.b', 'default')`. `event().a.b` errors.
- `problem_link()` "Outputs URL to a Davis problem… evaluates correctly in workflows with Davis problem event triggers only". Example: `https://xyz12345.apps.dynatrace.com/ui/apps/dynatrace.davis.problems/problem/20d01063-14d7-4b99-aef1-77e92962fb7f`.
- `to_json` and `from_json` filters exist.

**Grail problem record fields**, i.e. the keys of `event()` for the problem trigger (DOCUMENTED, [Davis semantic dictionary](https://docs.dynatrace.com/docs/semantic-dictionary/model/davis), updated 2026-09-21)
- Event fields:
  - `event.category`: `AVAILABILITY`, `ERROR`, `SLOWDOWN`, `RESOURCE_CONTENTION`, `CUSTOM_ALERT`, `MONITORING_UNAVAILABLE`
  - `event.description`: Markdown, e.g. "The current response time (11 s) exceeds the auto-detected baseline (767 ms) by 1,336 %"
  - `event.end`, `event.start`: nanosecond-precision timestamps
  - `event.id`: e.g. `5547782627070661074_1647601320000`
  - `event.kind`: `DAVIS_PROBLEM`
  - `event.name`: e.g. "CPU saturation", "Multiple infrastructure problems"
  - `event.severity`: 1–5, with 1 the most severe
  - `event.status`: `ACTIVE`, `CLOSED`
  - `event.status_transition`: `CREATED`, `UPDATED`, `REFRESHED`, `RESOLVED`, `REOPENED`, `CLOSED`. This is experimental. The upgrade guide lists it without `REOPENED`.
  - `timestamp`: ns
  - `dt.analysis.offset`
- System fields:
  - `display_id`: e.g. `P-2307288`
  - `dt.analysis.ready`: boolean, "the exact moment when classic problem notifications start being sent"
  - `dt.davis.affected_users_count`
  - `dt.davis.event_ids`: e.g. `['-2127669892157121805_1688396340000']`
  - `dt.davis.impact_level`: `Application`, `Environment`, `Infrastructure`, `Services`, `Synthetic`. These are title-case, and it is a list.
  - `dt.davis.is_duplicate`, `duplicate_problem_ids` (e.g. `['P-260401367', 'P-26040212']`)
  - `dt.davis.last_reopen_timestamp`
  - `dt.davis.mute.status` (`MUTED`), `dt.davis.mute.user`
  - `dt.duration_marker`
  - `labels.alerting_profile`: deprecated
  - `maintenance.is_under_maintenance`
  - `resolved_problem_duration`
- Smartscape fields:
  - `root_cause.smartscape_entity`: e.g. `{'id': 'HOST-1234567890ABCDEF', 'type': 'host', 'name': 'host-worker-097801ab8728e690a'}`. The type is lowercase.
  - `smartscape.affected_entities` (record[])
  - `smartscape.related_entities` (record[])
- Deprecated classic-topology fields:
  - `affected_entity_ids`, `affected_entity_names`, `affected_entity_types` (e.g. `['dt.entity.host','dt.entity.service']`)
  - `entity_tags`
  - `related_entity_ids`, `related_entity_names`
  - `root_cause_entity_id`, `root_cause_entity_name`
  - `smartscape.affected_entity.ids` / `.types`
- The upgrade guide also documents `primary_tags.*`, `dt.security_context`, `dt.cost.costcenter` and `dt.cost.product` as propagated onto the problem ([upgrade guide](https://docs.dynatrace.com/docs/manage/upgrade-guide-landing-page/upgrade-guide-alert-notification)).
- Every problem update is exported to Grail, "at least 1 update every 6 hours" (refreshes). `dt.davis.problems` is the deduplicated latest-state view; `dt.davis.problems.snapshots` holds immutable snapshots.

**Classic placeholder → Grail mapping** (DOCUMENTED verbatim, [Upgrade from Classic problem notification to simple workflows](https://docs.dynatrace.com/docs/manage/upgrade-guide-landing-page/upgrade-guide-alert-notification), updated 2026-09-30)

| Classic | Grail / Jinja | Note |
|---|---|---|
| `{State}` | `event.status` | ACTIVE or CLOSED |
| `{ProblemID}` | `display_id` | e.g. P-2307288 |
| `{PID}` | `event.id` | |
| `{ProblemTitle}` | `event.name` | |
| `{ProblemSeverity}` | `event.category` | also consider `event.severity` (1–5 ITIL) |
| `{ProblemImpact}` | `dt.davis.impact_level` | lists all levels: Application, Environment, Infrastructure, Services, Synthetic |
| `{ImpactedEntity}`, `{ImpactedEntities}` | `smartscape.affected_entities` | record array, each `id`, `type`, `name` |
| `{ImpactedEntityNames}`, `{NamesOfImpactedEntities}` | `smartscape.affected_entities` | read `name` |
| `{Tags}` | `primary_tags.*` | `entity_tags` deprecated |
| `{ProblemDetailsText}`, `{ProblemDetailsMarkdown}` | `event.description` | Markdown; content differs |
| `{ProblemDetailsHTML}` | not supported | use `event.description` |
| `{ProblemDetailsJSON}`, `{ProblemDetailsJSONv2}` | `{{ event() \| to_json }}` | different structure |
| `{ProblemURL}` | `{{ problem_link() }}` | Davis problem trigger only |
| `{startTime}`, `{endTime}` | `event.start`, `event.end` | format differs |

Other points from the same guide:
- Example syntax: `{{ event()["event.name"] }}`.
- Classic state behaviour maps to trigger states: "Notify on open only → active; open and resolve → active or closed; resolve only → closed".
- Enabling Wait for root cause analysis is "the closest equivalent to Dynatrace Classic timing".
- "ProblemDetailsJSON and ProblemDetailsJSONv2 can't be reproduced from a workflow". The guide adds that the classic JSON "produced payloads of 10 MB or more" for large problems.
- The problem trigger exposes only problem-record fields. Use `dt.davis.event_ids` to query `dt.davis.events`.
- Custom filter examples:
  - `maintenance.is_under_maintenance == false`
  - `matchesValue(event.category, "RESOURCE_CONTENTION")`
  - `event.status == "CLOSED"`
- Classic custom integration maps to the **HTTP Request** action. Slack, Teams, ServiceNow, Jira, PagerDuty and Email have connectors.
- Alerting profiles and problem notifications "continue to work and are not being removed on a published schedule, but they'll not receive new capabilities."

**HTTP Request action** (DOCUMENTED, [HTTP request action](https://docs.dynatrace.com/docs/analyze-explore-automate/workflows/default-workflow-actions/http-request-workflow-action))
- Inputs: Method, URL, Authentication (credential vault), Payload, Headers, Error handling.
- Result: `status_code`, `body`, `headers`, `json`.
- The terraform action id is `dynatrace.automations:http-function` ([terraform doc](https://raw.githubusercontent.com/dynatrace-oss/terraform-provider-dynatrace/main/docs/resources/automation_workflow.md)).

**Community payload template** (search snippet only, [community thread](https://community.dynatrace.com/t5/Automations/how-to-pass-problem-values-to-payload-for-API-integration/m-p/261807), page 403)
- A workflow HTTP payload built from `event()['event.id']`, `event.name`, `event.description`, `event.category`, the entity list, and `event()['event.status']` mapped to a state.
- Another community answer reads `event['display_id']` from a JS action ([community](https://community.dynatrace.com/t5/Developer-Q-A-Forum/Get-problemID-from-Davis-Problem-Trigger-in-workflow/m-p/221729)).

**Example HTTP Request payload** (INFERRED composition of DOCUMENTED expressions):
```jinja
{
  "pid": "{{ event()['event.id'] }}",
  "displayId": "{{ event()['display_id'] }}",
  "title": "{{ event()['event.name'] }}",
  "status": "{{ event()['event.status'] }}",
  "transition": "{{ event().get('event.status_transition', '') }}",
  "category": "{{ event()['event.category'] }}",
  "severity": {{ event().get('event.severity', 'null') }},
  "impactLevels": {{ event()['dt.davis.impact_level'] | to_json }},
  "affected": {{ event()['smartscape.affected_entities'] | to_json }},
  "rootCause": {{ event().get('root_cause.smartscape_entity') | to_json }},
  "start": "{{ event()['event.start'] }}",
  "url": "{{ problem_link() }}",
  "raw": {{ event() | to_json }}
}
```

**Realistic `event()` fixture** (keys DOCUMENTED, values INFERRED):
```json
{
  "timestamp": "2025-10-12T14:47:30.123000000Z",
  "event.kind": "DAVIS_PROBLEM",
  "event.id": "-4721069355830129151_1760280420000V2",
  "display_id": "P-25101234",
  "event.name": "Failure rate increase",
  "event.status": "ACTIVE",
  "event.status_transition": "CREATED",
  "event.category": "ERROR",
  "event.severity": 2,
  "event.description": "## Failure rate increase\nThe current failure rate (12.4 %) exceeds the auto-detected baseline (0.3 %) ...",
  "event.start": "2025-10-12T14:47:00.000000000Z",
  "dt.analysis.ready": true,
  "dt.davis.impact_level": ["Services"],
  "dt.davis.affected_users_count": 0,
  "dt.davis.event_ids": ["-1234567890123456789_1760280420000"],
  "dt.davis.is_duplicate": false,
  "dt.duration_marker": "5 min",
  "maintenance.is_under_maintenance": false,
  "smartscape.affected_entities": [{"id": "SERVICE-8A2D3C4B5E6F7081", "type": "service", "name": "orders-consumer"}],
  "root_cause.smartscape_entity": {"id": "PROCESS_GROUP_INSTANCE-1A2B3C4D5E6F7A8B", "type": "process", "name": "kafka-broker-2"},
  "affected_entity_ids": ["SERVICE-8A2D3C4B5E6F7081"],
  "primary_tags.team": ["streaming"]
}
```

### Inferences
- The `event()` payload is serialized JSON from the Grail record. How timestamp and duration values appear in it is not documented:
  - Timestamps may be ISO strings or ns integers.
  - `dt.duration_marker` may be a string or ns.
  - Fixtures should cover both forms. (INFERRED)
- Smartscape `type` values are lowercase (`host`, `service`), while classic `{ImpactedEntities}` and v2 use uppercase types (`HOST`, `SERVICE`). Normalize them. (INFERRED from the documented examples)
- The process type name in my fixture (`process`) is INFERRED; the docs only show `host` and `service`.

### Gaps
- No documented full real-valued `event()` JSON from a problem-trigger execution was found. The only `event()` sample in the docs is a security-problem event.
- The exact type and format of `primary_tags.*` values on a problem (array vs string) are not confirmed. The guide says values accumulate as a "deduplicated union", which suggests arrays.
- The Teams, Slack and ServiceNow connector request bodies with real values were not found.

---

## Q4. Teams, Slack and ServiceNow payloads with real values

### Takeaway
I found no source showing real rendered payloads that Dynatrace sends to Teams, Slack or ServiceNow.

### Cited Findings
- Classic Slack takes a `url`, `channel` and a `message` with the short placeholder set ([settings schema](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/schemas/builtin-problem-notifications)).
- The classic ServiceNow integration lets you customize the Description with "any of the placeholders from Available placeholders" ([ServiceNow integration](https://docs.dynatrace.com/docs/analyze-explore-automate/notifications-and-alerting/problem-notifications/servicenow-integration)).
- In Workflows, these are replaced by the Slack, Microsoft Teams and ServiceNow connectors ([upgrade guide](https://docs.dynatrace.com/docs/manage/upgrade-guide-landing-page/upgrade-guide-alert-notification)).

### Inferences
- For the triage app, the generic webhook and HTTP Request paths are what matter. The vendor connectors post vendor-native bodies (Slack blocks, Teams cards, ServiceNow incident fields) rather than Dynatrace-shaped JSON. (INFERRED)

### Gaps
- No real rendered Teams, Slack or ServiceNow payload was found in the fetched docs. Community pages were blocked (403) and GitHub code search was rate-limited.
