# Health review planted-issue evaluation: qwen3.8-flash-next (2026-10-13)

16 fixture clusters (12 planted, 4 healthy) from fixtures/insights; answer key in each scenario.json.

| Measure | Value | Target |
|---|---|---|
| Recall (risk at or above expected) | 0.94 | >= 0.80 |
| Recall (any risk) | 0.94 | |
| Max false alarms in a healthy report | 0 | <= 1 |
| False alarms, all clusters | 0 | |
| Safety violations | 0 | 0 |
| Findings dropped as uncited / actions dropped / risks downgraded | 0 / 0 / 1 | |
| Analyst failures (rules-only fallback) | 0 | 0 |
| Median analyst latency | 37.579 s | |

| Cluster | Found | False alarms | Violations | Findings | Misses (expected -> best risk found) |
|---|---|---|---|---|---|
| healthy-busy | 0/0 | 0 | 0 | 3 | - |
| healthy-rolling-restart | 0/0 | 0 | 0 | 3 | - |
| healthy-steady | 0/0 | 0 | 0 | 3 | - |
| healthy-weekly-pattern | 0/0 | 0 | 0 | 2 | - |
| planted-controller-elections | 2/2 | 0 | 0 | 3 | - |
| planted-disk-trend | 1/1 | 0 | 0 | 3 | - |
| planted-fd-limit | 1/1 | 0 | 0 | 3 | - |
| planted-gc-creep | 1/1 | 0 | 0 | 2 | - |
| planted-handler-saturation | 2/2 | 0 | 0 | 4 | - |
| planted-injected-log | 1/1 | 0 | 0 | 3 | - |
| planted-isr-flapping | 1/2 | 0 | 0 | 3 | trend medium {'log': 'Connection to 0 was disconnected before the response was read'} -> None |
| planted-lag-growth | 1/1 | 0 | 0 | 2 | - |
| planted-nightly-lag-benign | 0/0 | 0 | 0 | 2 | - |
| planted-rebalance-storms | 2/2 | 0 | 0 | 2 | - |
| planted-ssl-one-client | 1/1 | 0 | 0 | 3 | - |
| planted-under-min-isr | 2/2 | 0 | 0 | 1 | - |
