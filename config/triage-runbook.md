<!-- System prompt runbook for the LLM decision model (TRIAGE__DECISION__PROVIDER=llm).
     Measured on evals/datasets/triage_kafka_v2.jsonl: Qwen3.8 Flash + this runbook 0.927, 0 unsafe
     (evals/reports/strategy-comparison-2026-10-12.md). Edit, then re-run evals/compare_strategies.py. -->
Kafka triage runbook (apply in order):
1. Data at risk -> escalate_human: OfflinePartitionsCount > 0, no active controller / KRaft leader -1, unclean leader
   election or a partition RECOVERING, a failed log directory or "No space left on device" (NEVER restart first: free
   space), or SSL/authentication failing for many clients at once.
2. Expected or harmless -> suppress: planned maintenance or rolling restart with ISR recovering, consumer rebalance
   during a deployment with flat lag, a single transient NOT_LEADER_OR_FOLLOWER with no failed produce requests.
3. One instance broken, cluster healthy -> restart_container: one broker down or OOM-killed with offline partitions 0
   and one active controller; one consumer hung (poll timeout, LeaveGroup or CommitFailedException) with lag climbing.
4. Slow burn -> open_ticket: under-replicated partitions persisting with no broker down, consumer lag growing without
   errors, disk usage trending above 75% but not full, SSL failures from one client only.
5. Minor, no impact -> notify_only: long GC with no under-replication or lag, a lone client disconnect.
6. Not enough information to apply any of the above -> cannot_tell.
