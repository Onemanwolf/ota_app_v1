# Postman: mock Dynatrace webhooks against the local stack

Files:
- `OpsTriage-local.postman_collection.json`: generated, do not edit by hand. Regenerate with `python3 postman/build_collection.py`.
- `local.postman_environment.json`: base URLs and dev tokens. On this Mac the mock Teams sink is on **8091** (8090 is taken).
  The collection also carries the same values as collection variables, so it works even with **No environment** selected.

## Import into Postman
File > Import, select both files, pick the `opstriage-local` environment, then run folders with the Collection Runner.
The "Wait for ..." requests poll by re-queueing themselves. That only works in the **Collection Runner** (or newman),
not when you click Send on a single request.

Request bodies are the fixture files from `fixtures/dynatrace/` verbatim, except the IDs: `event_id` / `display_id`
(classic: `pid` / `problem_id`) are randomised by pre-request scripts, so you can re-run without every request
being a duplicate. Folders 2 and 3 re-use folder 1's IDs on purpose (dedup and the CLOSED path).

## Folders

| Folder | Needs | Checks |
|---|---|---|
| 0 Health | stack up; Kev on the Spark for the last request | every `/healthz`, `/readyz`, Mailpit, `kev-latest` served |
| 1 Workflow webhook happy path | | 202, alert `done`, run completed, card in mock Teams, email in Mailpit |
| 2 Dedup | run after 1 | same body returns 200 `duplicate: true` |
| 3 Lifecycle (CLOSED) | run after 1 | CLOSED routes to `resolve`, ticket resolved |
| 4 Classic webhook | | OPEN maps to ACTIVE, RESOLVED to the resolve path |
| 5 Routing scenarios | `TRIAGE__DECISION__PROVIDER=rules` | plan per scenario (spec 13.2) |
| 6 Kev decisions | `docker compose -f compose.yaml -f compose.kev.yaml up -d agent-runtime`, Kev reachable from Docker at `KEV_BASE_URL` (`scripts/kev-tunnel.sh check`) | decisions come from `kev-latest` with all six probabilities; outage and no-target never remediate; injection only touches the registry target; maintenance is suppressed before Kev |
| 7 Security and negative | | 401, 422, 413, prompt injection does not widen the plan |
| 9 Live restart and guard rails | `docker compose -f compose.yaml -f compose.live-restart.yaml up -d mcp-remediation`; at most once per 15 min (cooldown) | demo-orders-api restarted and verified, result card, a second problem is rejected by the cooldown |
| 8 Manual approval | `docker compose -f compose.yaml -f compose.manual-approval.yaml up -d agent-runtime` | interrupt, approve, resume, second decision 409 |
| 10 Health review (OTA-APP-002) | the default stack (`health-review` on 8120) and `scripts/kev-tunnel.sh` for the analyst model (without it, reports hold code-checked findings only) | review completes with a critical under-min-ISR rule finding, Markdown report, Teams card, RCA with a rank-1 hypothesis, web view sign-in redirect with a strict CSP, expired report answers 410 |

Kill switch: add `-f compose.kill-switch.yaml` and run folder 1; the remediation outcome is `rejected` (`disabled`).

## Headless (newman)
```bash
npx -y newman@6.2.1 run postman/OpsTriage-local.postman_collection.json \
  -e postman/local.postman_environment.json \
  --folder "0 Health" --folder "1 Workflow webhook happy path" --folder "2 Dedup" \
  --folder "3 Lifecycle (CLOSED)" --folder "4 Classic webhook" --folder "5 Routing scenarios" \
  --folder "7 Security and negative"
```

## Where to look
- Mock Teams channel: http://localhost:8091/ (refreshes every 5 s)
- Mock email: http://localhost:8025/ (Mailpit)
- Run details: `GET http://localhost:8010/v1/runs/{alert_id}` with `Authorization: Bearer dev-admin-token`

## Troubleshooting "network error" in the Postman app
1. Re-import `OpsTriage-local.postman_collection.json` (replace the old copy). Earlier versions wrote URLs as plain strings,
   which the app's request editor does not handle (`e.trim is not a function` in its logs).
2. Use the **Desktop Agent**: bottom-right of the app, the agent selector must say *Desktop Agent*, not *Cloud Agent* or
   *Browser Agent*. The cloud agent runs in Postman's cloud and cannot reach `localhost`.
3. Check the stack is up: `docker compose ps` (every service healthy).
4. Kev is reached at `http://127.0.0.1:18003` (the `scripts/kev-tunnel.sh` tunnel), so the app needs no macOS
   Local Network permission. `scripts/kev-tunnel.sh status` shows whether the tunnel is up.
5. The Postman CLI runs the same collection with the same runtime as the app:
   `postman collection run postman/OpsTriage-local.postman_collection.json -i "1 Workflow webhook happy path"`.
   If that passes and the app does not, the difference is in the app (agent, proxy settings, environment).
