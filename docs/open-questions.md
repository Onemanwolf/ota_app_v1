# Open questions

Spec section 17 lists the architectural ones. This file tracks questions raised during the build.

| # | Raised | Question | Current default |
|---|---|---|---|
| 1 | Module 0 | Which Spark address is stable: 192.168.5.147 or .144? | .147 in `.env.local` |
| 2 | Plan | Kev's `confidence` is not the chosen option's probability (sample: p=0.295, confidence=0.060). Which value gates restarts? | Spec mapping (`confidence`) until the Module 3 evals; ADR 0006 then |
| 3 | Plan | Kev returns `latency_ms` as a float; `TriageDecision.latency_ms` is `int` | Adapter rounds; contract unchanged |
| 4 | Module 2 | Real Grail and Problems API v2 runs and the identifier check (spec 12.3.6 items 2 and 3) | Deferred: no tenant in local scope; ADR 0005 has the procedure |
| 5 | Module 1 | The approval resumer assumes one agent-runtime replica. Several replicas need `pg_try_advisory_lock` around each sweep (spec 13.3 step 4) | Single replica locally |
| 6 | Module 1 | A real Grail context source (logs, guard re-read) is out of local scope; `get_problem` reads the inbox instead of Grail | Fake context source |
| 7 | Module 3 | The eval set `evals/datasets/triage_v1.jsonl` is synthetic and its labels are the builder's judgement. Review them (and replace with anonymised history when it exists) before any threshold is taken from the report | Thresholds provisional; production keeps the 0.90 placeholder |
| 8 | Module 3 | Docker Desktop has no macOS Local Network permission, so containers cannot reach the Spark directly | `scripts/kev-tunnel.sh install` (LaunchAgent SSH tunnel to `host.docker.internal:18003`, ADR 0006 section 5) |
| 9 | Module 3 | No Microsoft-Decision-1 deployment: its contract test uses the documented shape, not a recorded response (spec 12.4.5 DoD item 1, Foundry half) | Record one when Foundry is provisioned (OTA-AZ-001) |
| 10 | Module 4 | Cooldown/cap check and the IN_PROGRESS insert are separate statements (ADR 0007 #2): concurrent requests could exceed the daily cap by one | Per-target lock prevents concurrent restarts; accepted locally |
| 11 | Module 4 | Azure Run Command, Arc Run Command and SSH executors (spec 12.5.2) are designed but not built | Out of local scope; registry fails fast if referenced |
