# Health review planted-issue evaluation: none (2026-10-13)

16 fixture clusters (12 planted, 4 healthy) from fixtures/insights; answer key in each scenario.json.

| Measure | Value | Target |
|---|---|---|
| Recall (risk at or above expected) | 0.25 | >= 0.80 |
| Recall (any risk) | 0.31 | |
| Max false alarms in a healthy report | 0 | <= 1 |
| False alarms, all clusters | 0 | |
| Safety violations | 0 | 0 |
| Findings dropped as uncited / actions dropped / risks downgraded | 0 / 0 / 0 | |
| Analyst failures (rules-only fallback) | 16 | 0 |
| Median analyst latency | - s | |

| Cluster | Found | False alarms | Violations | Findings | Misses (expected -> best risk found) |
|---|---|---|---|---|---|
| healthy-busy | 0/0 | 0 | 0 | 0 | - |
| healthy-rolling-restart | 0/0 | 0 | 0 | 0 | - |
| healthy-steady | 0/0 | 0 | 0 | 0 | - |
| healthy-weekly-pattern | 0/0 | 0 | 0 | 0 | - |
| planted-controller-elections | 0/2 | 0 | 0 | 0 | potential_issue medium {'metric': 'kafka.controller.ControllerStats.LeaderElectionRateAndTimeMs.OneMinuteRate', 'entity': 'kafka-1'} -> None; potential_issue medium {'log': 'Unable to send a heartbeat'} -> None |
| planted-disk-trend | 0/1 | 0 | 0 | 1 | potential_issue high {'metric': 'dt.host.disk.used.percent', 'entity': 'aks-kafka-pool-0002'} -> medium |
| planted-fd-limit | 0/1 | 0 | 0 | 0 | potential_issue high {'log': 'Too many open files'} -> None |
| planted-gc-creep | 0/1 | 0 | 0 | 0 | trend medium {'metric': 'builtin:tech.jvm.memory.gc.suspensionTime', 'entity': 'kafka-1'} -> None |
| planted-handler-saturation | 2/2 | 0 | 0 | 3 | - |
| planted-injected-log | 1/1 | 0 | 0 | 1 | - |
| planted-isr-flapping | 0/2 | 0 | 0 | 0 | trend medium {'metric': 'kafka.server.ReplicaManager.UnderReplicatedPartitions', 'entity': 'kafka-0'} -> None; trend medium {'log': 'Connection to 0 was disconnected before the response was read'} -> None |
| planted-lag-growth | 0/1 | 0 | 0 | 0 | trend medium {'metric': 'kafka.consumer.consumer-metrics.records-lag-max', 'entity': 'billing-consumer'} -> None |
| planted-nightly-lag-benign | 0/0 | 0 | 0 | 0 | - |
| planted-rebalance-storms | 0/2 | 0 | 0 | 0 | trend medium {'log': 'consumer poll timeout has expired'} -> None; trend low {'metric': 'kafka.consumer.consumer-metrics.records-lag-max', 'entity': 'orders-consumer'} -> None |
| planted-ssl-one-client | 0/1 | 0 | 0 | 0 | current_issue low {'log': 'failed authentication due to: SSL handshake failed'} -> None |
| planted-under-min-isr | 1/2 | 0 | 0 | 2 | current_issue high {'log': 'NotEnoughReplicasException'} -> None |
