# Open questions

Spec section 17 lists the architectural ones. This file tracks questions raised during the build.

| # | Raised | Question | Current default |
|---|---|---|---|
| 1 | Module 0 | Which Spark address is stable: 192.168.5.147 or .144? | .147 in `.env.local` |
| 2 | Plan | Kev's `confidence` is not the chosen option's probability (sample: p=0.295, confidence=0.060). Which value gates restarts? | Spec mapping (`confidence`) until the Module 3 evals; ADR 0006 then |
| 3 | Plan | Kev returns `latency_ms` as a float; `TriageDecision.latency_ms` is `int` | Adapter rounds; contract unchanged |
| 4 | Module 2 | Real Grail and Problems API v2 runs and the identifier ADR (spec 12.3.6 items 2 and 3) | Deferred: no tenant credentials in local scope |
