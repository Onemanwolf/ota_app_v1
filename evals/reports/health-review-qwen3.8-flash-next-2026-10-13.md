# Health review planted-issue evaluation: qwen3.8-flash-next (2026-10-13)

16 fixture clusters (12 planted, 4 healthy) from fixtures/insights; answer key in each scenario.json.

| Measure | Value | Target |
|---|---|---|
| Recall (risk at or above expected) | 0.94 | >= 0.80 |
| Recall (any risk) | 0.94 | |
| Max false alarms in a healthy report | 0 | <= 1 |
| False alarms, all clusters | 0 | |
| Safety violations | 0 | 0 |
| Findings dropped as uncited / actions dropped / risks downgraded | 0 / 1 / 0 | |
| Analyst failures (rules-only fallback) | 0 | 0 |
| Median analyst latency | 36.1845 s | |

| Cluster | Found | False alarms | Violations | Findings | Misses (expected -> best risk found) |
|---|---|---|---|---|---|
| healthy-busy | 0/0 | 0 | 0 | 2 | - |
| healthy-rolling-restart | 0/0 | 0 | 0 | 3 | - |
| healthy-steady | 0/0 | 0 | 0 | 1 | - |
| healthy-weekly-pattern | 0/0 | 0 | 0 | 2 | - |
| planted-controller-elections | 2/2 | 0 | 0 | 3 | - |
| planted-disk-trend | 1/1 | 0 | 0 | 3 | - |
| planted-fd-limit | 1/1 | 0 | 0 | 2 | - |
| planted-gc-creep | 1/1 | 0 | 0 | 2 | - |
| planted-handler-saturation | 2/2 | 0 | 0 | 4 | - |
| planted-injected-log | 1/1 | 0 | 0 | 2 | - |
| planted-isr-flapping | 1/2 | 0 | 0 | 3 | trend medium {'log': 'Connection to 0 was disconnected before the response was read'} -> None |
| planted-lag-growth | 1/1 | 0 | 0 | 2 | - |
| planted-nightly-lag-benign | 0/0 | 0 | 0 | 1 | - |
| planted-rebalance-storms | 2/2 | 0 | 0 | 1 | - |
| planted-ssl-one-client | 1/1 | 0 | 0 | 2 | - |
| planted-under-min-isr | 2/2 | 0 | 0 | 2 | - |
| rca-cert-expiry | 0/0 | 0 | 0 | 2 | - |
| rca-consumer-deploy | 0/0 | 0 | 0 | 2 | - |
| rca-controller-quorum | 0/0 | 0 | 0 | 2 | - |
| rca-disk-full | 0/0 | 0 | 0 | 4 | - |
| rca-fd-exhaustion | 0/0 | 0 | 0 | 1 | - |
| rca-follower-lag | 0/0 | 0 | 0 | 4 | - |
| rca-gc-pause | 0/0 | 0 | 0 | 5 | - |
| rca-network-flap | 0/0 | 0 | 0 | 3 | - |
| rca-oom | 0/0 | 0 | 0 | 3 | - |
| rca-traffic-surge | 0/0 | 0 | 0 | 2 | - |
