# Fine-tuned Kev stress test (2026-10-12)

134 model-routed cases from `evals/datasets/triage_kafka_v2.jsonl` (pre-route rule cases removed). Each perturbation preserves the label and is something the training generator never produces.

| Perturbation | kev-ft accuracy | kev-ft wrong restarts | kev-base accuracy | kev-base wrong restarts |
|---|---|---|---|---|
| none | 1.000 | 0 | 0.470 | 0 |
| rename | 1.000 | 0 | 0.455 | 0 |
| tags | 1.000 | 0 | 0.396 | 0 |
| reorder | 0.993 | 0 | 0.455 | 0 |
| noise | 0.925 | 0 | 0.366 | 0 |
| timestamps | 1.000 | 0 | 0.478 | 0 |
| all | 0.918 | 0 | 0.358 | 0 |

## Errors under `all` (label -> predicted)

- **kev-ft**: cannot_tell->suppress x7, open_ticket->notify_only x3, restart_container->escalate_human x1
- **kev-base**: restart_container->escalate_human x22, open_ticket->notify_only x17, suppress->notify_only x10, open_ticket->escalate_human x9, notify_only->escalate_human x8, cannot_tell->notify_only x7, restart_container->notify_only x6, open_ticket->cannot_tell x4
