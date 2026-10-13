# Fine-tuned Kev vs triage strategies (2026-10-12)

150 cases from `evals/datasets/triage_kafka_v2.jsonl` (Kafka, synthetic-but-realistic, labels INFERRED). Every strategy runs behind the production pre-route rules. **Caveat:** C's signatures and the dataset come from the same research, so C is an upper bound on this set; in production, unmatched incidents fall through to the model, so coverage matters as much as accuracy.

| Strategy | Accuracy | Macro F1 | Unsafe (wrong restart or silenced real problem) | cannot_tell recall | escalate_human recall | notify_only recall | open_ticket recall | restart_container recall | suppress recall |
|---|---|---|---|---|---|---|---|---|---|
| A kev-choice | 0.520 | 0.455 | 0 | 1.00 | 1.00 | 0.94 | 0.00 | 0.07 | 0.54 |
| B kev-binary | 0.480 | 0.439 | 1 | 0.11 | 0.94 | 0.44 | 0.17 | 0.33 | 0.54 |
| C signatures | 0.860 | 0.734 | 1 | 0.00 | 0.97 | 1.00 | 0.87 | 0.87 | 0.89 |
| D sig+kev-binary | 0.873 | 0.790 | 1 | 0.11 | 1.00 | 0.94 | 0.90 | 0.87 | 0.89 |
| E1 qwen | 0.647 | 0.547 | 1 | 0.00 | 0.86 | 1.00 | 0.17 | 0.97 | 0.54 |
| E2 qwen+runbook | 0.927 | 0.914 | 0 | 0.89 | 1.00 | 0.94 | 0.97 | 0.83 | 0.89 |
| F sig+qwen | 0.967 | 0.961 | 1 | 0.89 | 1.00 | 1.00 | 1.00 | 0.93 | 0.93 |
| G kev-ft | 1.000 | 1.000 | 0 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| H sig+kev-ft | 0.987 | 0.989 | 0 | 1.00 | 1.00 | 1.00 | 1.00 | 0.93 | 1.00 |
| H* sig+kev-ft gate | 0.987 | 0.989 | 0 | 1.00 | 1.00 | 1.00 | 1.00 | 0.93 | 1.00 |

Deterministic coverage on this set: 131/150 decided by a rule or signature.

- kev-ft latency p50 0.81s, p95 1.51s (with 150 calls)

## Errors by strategy (label -> predicted)

- **A kev-choice**: open_ticket->notify_only x23, restart_container->escalate_human x21, suppress->notify_only x8, restart_container->notify_only x7, open_ticket->escalate_human x5, suppress->cannot_tell x4
- **B kev-binary**: open_ticket->escalate_human x23, restart_container->escalate_human x19, cannot_tell->escalate_human x8, suppress->cannot_tell x6, suppress->escalate_human x5, notify_only->escalate_human x4
- **C signatures**: cannot_tell->notify_only x9, open_ticket->notify_only x4, restart_container->notify_only x2, restart_container->open_ticket x2, suppress->notify_only x2, escalate_human->notify_only x1
- **D sig+kev-binary**: cannot_tell->escalate_human x8, open_ticket->cannot_tell x2, restart_container->escalate_human x2, restart_container->open_ticket x2, suppress->cannot_tell x2, notify_only->cannot_tell x1
- **E1 qwen**: open_ticket->notify_only x25, suppress->notify_only x12, cannot_tell->notify_only x9, escalate_human->notify_only x5, restart_container->notify_only x1, suppress->restart_container x1
- **E2 qwen+runbook**: restart_container->escalate_human x5, suppress->notify_only x2, notify_only->cannot_tell x1, open_ticket->notify_only x1, cannot_tell->notify_only x1, suppress->cannot_tell x1
- **F sig+qwen**: restart_container->open_ticket x2, cannot_tell->notify_only x1, suppress->notify_only x1, suppress->restart_container x1
- **G kev-ft**: none
- **H sig+kev-ft**: restart_container->open_ticket x2
- **H* sig+kev-ft gate**: restart_container->open_ticket x2

## Sample Qwen reasons (E2, runbook)

- `unclean_election` -> escalate_human: Unclean leader election and partition RECOVERING indicate potential data loss or inconsistency, which requires immediate human intervention per runbook rule 1.
- `lag_growth_gradual` -> open_ticket: Consumer lag is growing without reported errors or a specific hung instance, which matches the 'slow burn' pattern in the runbook.
- `unclean_election` -> escalate_human: The alert indicates an unclean leader election, and the logs show a partition marked as RECOVERING. According to the runbook, data at risk conditions such as unclean leader elections or partitions in a RECOVERING state require immediate human escalation to prevent potential data loss or corruption. Automated restarts are explicitly forbidden in this scenario.
- `log_alert_no_context` -> cannot_tell: The log indicates a ReplicaFetcher error for a specific partition, but it lacks critical context to determine if this is a transient network glitch, a broker failure, or a data integrity issue. The runbook requires knowing the OfflinePartitionsCount, controller status, and whether other brokers are affected to distinguish between a simple restart (if one broker is down), escalation (if data is at/
- `lag_growth_gradual` -> open_ticket: Consumer lag is growing (2200 -> 18002) without reported errors or broker failures. The consumption rate has increased (1950 -> 4570), suggesting the consumer is working but potentially under-provisioned or facing a throughput bottleneck. This fits the 'slow burn' pattern of growing lag without errors, requiring follow-up work (e.g., scaling consumers or investigating producer spikes) rather than,

## Notes

- A-F predictions are reused from `evals/reports/strategy-comparison-2026-10-12.json`; G/H were scored now against the fine-tuned checkpoint.
- The fine-tune's training records and this eval set come from the same scenario generator (different seeds and minimal pairs), so G/H are in-distribution upper bounds, like C and F.
