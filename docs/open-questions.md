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
