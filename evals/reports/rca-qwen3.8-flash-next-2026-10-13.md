# Root-cause analysis evaluation: qwen3.8-flash-next (2026-10-13)

**10 of 10** cases have the planted cause in the top 2 hypotheses (target: at least 8).

| Cluster | Problem | Cause at rank | Pass | Top hypotheses |
|---|---|---|---|---|
| rca-cert-expiry | P-26101205 | 1 | yes | Expired or invalid SSL certificates on producer clients causing handshake failures |
| rca-consumer-deploy | P-26101204 | 1 | yes | Consumer processing bottleneck causing poll timeouts and rebalance storms |
| rca-controller-quorum | P-26101207 | 1 | yes | Controller Quorum Instability via Heartbeat RPC Timeouts |
| rca-disk-full | P-26101201 | 1 | yes | Disk exhaustion on aks-kafka-pool-0002 caused LogDirFailure and subsequent under-replication |
| rca-fd-exhaustion | P-26101206 | 1 | yes | File Descriptor Exhaustion on kafka-2 |
| rca-follower-lag | P-26101208 | 1 | yes | Follower replica lag or failure causing ISR shrinkage on kafka-1 |
| rca-gc-pause | P-26101202 | 1 | yes | JVM GC Pressure on kafka-1 causing request handler saturation |
| rca-network-flap | P-26101203 | 1 | yes | Network connectivity loss between brokers causing replication and election failures |
| rca-oom | P-26101209 | 1 | yes | JVM Heap Exhaustion on kafka-0 leading to Process Failure |
| rca-traffic-surge | P-26101210 | 1 | yes | Inbound Traffic Surge Saturating Request Handlers |
