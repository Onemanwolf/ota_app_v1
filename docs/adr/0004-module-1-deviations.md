# ADR 0004: Module 1 implementation decisions and deviations

- Status: accepted (2026-10-12, Module 1)

| # | Spec says | We did | Why |
|---|---|---|---|
| 1 | Migrations in `services/<svc>/migrations/` | In the package: `src/<pkg>/migrations/` | Images install non-editable wheels, so a path relative to the source tree does not exist in the container. The wheel ships the SQL files. |
| 2 | Agent gateway uses `create_mcp_http_client` from `mcp.client.streamable_http` | Builds `httpx2.AsyncClient(headers, timeout, follow_redirects=True)` itself | In mcp 2.3.0 that helper is only exported from the private `mcp.shared._httpx_utils`; strict mypy rejects the re-export. Same settings. |
| 3 | Ambiguous target: "no target is returned and the run escalates" (12.5.2) | `policy_gate` adds `escalate` for any non-suppressed decision with an ambiguous target | The rules classifier picks `notify_only` when there is no unambiguous target, so the matrix alone never escalated. |
| 4 | `fail()` returns the row to `pending` | `pending` with `locked_until = now() + backoff x attempt` (`TRIAGE__INBOX__RETRY_BACKOFF_SECONDS`, default 10) | Otherwise a brief MCP outage burns all attempts within seconds. |
| 5 | Module 1 notification stub logs only | Real `WorkflowsWebhookTeamsSender` (to webhook-sink) and `SmtpEmailSender` (to Mailpit) now; `log` providers remain | The spec gives both adapters; having them now lets the Module 1 Postman run check real cards and emails. Rate limiting is in too. |
| 6 | Approval service resumes in a background task | Decisions are rows; one resumer (the sweeper loop, poked on each decision) resumes every decided-but-not-resumed approval | Crash-safe: a decision recorded just before a crash is still applied. Single replica assumed locally (open question 5). |
| 7 | `FakeProblemContextSource` returns canned logs | Logs from `fixtures/context/<display_id>.json`, else `<tags.context_fixture>.json`, else `default.json`; `get_problem` reads the newest inbox event | Lets tests inject log text (prompt injection) and lets the guard see a CLOSED webhook. |
| 8 | `RestartRequest.dry_run` defaults to `true` | The agent sends `policy.remediation.request_dry_run` (default `false`); `REMEDIATION__DRY_RUN` on the server still wins | Otherwise a real restart could never happen even with the server's dry run off (12.5.5 DoD). |
| 9 | Module 1 remediation: `DryRunOnlyExecutor` | Plus guard rails 1-4 (idempotency, kill switch, unknown target, autonomy and approval proof) | Cheap now; the rest (serialization, cooldown and cap, breaker, execution) is Module 4. |
| 10 | `mypy packages services` | Same, with `tests/` excluded | Test modules share names across services (`conftest.py`); pytest still runs them. |
| 11 | Local `targets.example.yaml` includes Azure and SSH targets | Local file has only `local_docker` targets (plus a second one for the ambiguity scenario); the spec's Azure/SSH examples live in `targets.dev.yaml` | Section 8.3: "a local stack never needs Azure credentials". |
| 12 | Compose `env_file` | Never put a comment after an empty value | Compose reads `KEY=   # comment` as the value `# comment` (it broke the inbox consumer grant). |
