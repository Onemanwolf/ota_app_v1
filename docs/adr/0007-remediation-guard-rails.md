# ADR 0007: Remediation guard rails and the local Docker executor (Module 4)

- Status: accepted (2026-10-12, Module 4)

## Implemented (spec 12.5.2), in order
1 idempotency, 2 kill switch, 3 target lookup, 4 autonomy (lower of target and global ceiling) and approval proof
(mode allowed, younger than the TTL), 6 cooldown and daily cap, 7 circuit breaker, 8 dry run (server setting or
request; the server can force a dry run, never a real run), 5 per-target serialization (the IN_PROGRESS insert is
the lock), 9 execution in a background task with a synchronous wait of `REMEDIATION__SYNC_WAIT_SECONDS`, then
IN_PROGRESS and the caller polls `get_remediation`. A reconciler (startup and every minute) fails IN_PROGRESS rows
older than the target timeout plus 5 minutes. `opstriage-remediation reset-breaker --operator NAME` resets the
breaker and writes an audit log line plus a `remediation.breaker_resets` row.

## Decisions
| # | Decision | Why |
|---|---|---|
| 1 | Steps 6-7 run before the dry-run decision, and step 5 (the lock) only for real runs | Spec order (6 before 8). Consequence seen live: for 15 min after a REAL restart even dry runs report `rejected: cooldown`. Dry runs never count towards cooldown or the cap (spec 10.3). |
| 2 | Cooldown and cap are checked, then the IN_PROGRESS row is inserted (two statements, not one transaction) | The partial unique index makes concurrent restarts of one target impossible (tested: 8 concurrent starts, exactly 1 admitted). The remaining race could exceed the daily cap by one under concurrent requests for the same target; acceptable locally, revisit with `SELECT ... FOR UPDATE` on a per-target row if it matters. |
| 3 | `busy` vs `duplicate` told apart by `UniqueViolation.diag.constraint_name` | Spec 12.5.2 step 5. |
| 4 | Breaker counts FAILED rows of any target since `max(now - window, last reset)` | Spec 12.5.2 step 7 ("any target", operator reset). Resets are rows, so they are auditable and survive restarts. |
| 5 | `finish` only updates rows still IN_PROGRESS | A late writer (for example after the reconciler failed a row) never overwrites a terminal state. |
| 6 | `LocalDockerExecutor` succeeds when the container is Running after `restart`; output tail ends with `exit_code=N` | Same success convention as `restart-allowed.sh` on VMs. Docker's nanosecond `StartedAt` is truncated to microseconds. |
| 7 | Only `local_docker` is registered; Azure Run Command, Arc and SSH executors are not built | Out of local scope (BUILD-PLAN section 2). A registry that references them fails at startup ("no executor registered"), never at restart time. |
| 8 | Compose runs mcp-remediation as root with the Docker socket mounted | Spec 12.2.7, local only; the registry refuses `local_docker` unless `APP_ENV=local` and `REMEDIATION__ALLOW_LOCAL_DOCKER=true`. |
| 9 | Scenario overrides `compose.live-restart.yaml` (dry run off) and `compose.kill-switch.yaml` (enabled false) | Config changes by file, never by hand-editing `.env`; the default stack stays dry run. |

## Not done here
- Spec 12.5.5 DoD item 4 (one Azure test VM restarted through `azure_run_command`): out of local scope.
