<!-- Kafka review runbook appended to the health-review analyst's system prompt (INSIGHTS__ANALYST__RUNBOOK_FILE).
     Model-agnostic: plain guidance, no model-specific syntax. Edit, then re-run evals/health_review_eval.py. -->
Kafka health review guidance:
- Replication: under-replicated partitions (URP) persisting beyond a restart, or under-min-ISR partitions, mean
  durability is reduced; under-min-ISR with acks=all means producers are failing now (current_issue, high or above).
  Short nightly URP that recovers points at a follower falling behind on a schedule (trend: look for fetch timeouts).
- Controller: leader-election bursts without planned restarts plus missed broker heartbeats suggest an unstable
  controller quorum or network; repeated Davis problems with the same title strengthen this.
- Saturation: request-handler or network-processor idle below 0.3 at peak means the brokers are near capacity;
  a falling daily minimum with rising bytes-in is organic growth (recommend capacity planning, not restarts).
- Consumers: lag that grows day over day without errors means consumers are under-provisioned; lag spikes that
  coincide with poll-timeout logs at deploy times point to rebalance storms (recommend cooperative rebalancing,
  static membership or slower rollouts). Same-hour nightly spikes that recover are batch patterns.
- Resources: disk forecasts to 90% need a retention or capacity change before the date; GC suspension stepping up
  after a deploy suggests heap pressure from the new release; "Too many open files" growing means the file
  descriptor limit or segment/connection counts need review before brokers start refusing connections.
- Security: SSL handshake failures from one client at the same time each day point to that client (expired
  certificate or keystore); many clients failing at once would be a broker-side issue.
