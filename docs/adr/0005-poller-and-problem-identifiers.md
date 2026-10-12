# ADR 0005: Poller decisions, and the problem-identifier check (deferred)

- Status: accepted for local scope (2026-10-12, Module 2). The identifier check stays open until a tenant is connected.

## Problem identifiers (spec 9.1, 12.3.6 DoD item 3): DEFERRED
The local build has no Dynatrace tenant (BUILD-PLAN section 2), so nobody has confirmed that the webhook and the
poller produce the same `problem_id` for the same problem. Until then, the intake's secondary dedup on
`display_id:status` (spec 10.2, integration-tested) covers a mismatch. When a tenant is connected: run one
Workflow and one `opstriage-poller --once` against the same problem, compare `intake.alert_inbox.problem_id`,
and record the result here.

## Poller decisions
| # | Spec | Decision | Why |
|---|---|---|---|
| 1 | `problems_active.dql` is read without substitution | It is a `string.Template` with `${lookback_hours}` like the changed query | `fetch` without `from:` defaults to a short window; active problems refresh at least every 6 h, so the lookback is the right window. |
| 2 | `FixtureProblemSource` replays files | Rebases `last_update` to the time the source was created | Fixture timestamps are fixed, so they would fall out of the lookback a day later. The first cycle sees every record; later cycles see nothing new, and the active sweep yields duplicates. |
| 3 | Cursor `set` | `GREATEST(old, new)` in SQL | "The high-water mark only moves forward" is enforced by the database too, not just by the use case. |
| 4 | Classic adapter `observed_at` | `endTime` when closed, else the poll time | Problems API v2 has no last-update field. |
| 5 | Grail SSO URL | Setting `POLLER__GRAIL__SSO_URL` (default `https://sso.dynatrace.com/sso/oauth2/token`) | No hard-coded configuration (spec rule 4); also lets tests point it at a mock. |
| 6 | Leader lock | Session advisory lock held on one pooled connection for the whole cycle | `pg_try_advisory_lock` is per session; releasing on the same connection is required. |
