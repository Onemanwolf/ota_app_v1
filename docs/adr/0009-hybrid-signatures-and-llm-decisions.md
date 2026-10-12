# ADR 0009: Production decisions = Kafka signatures first, Qwen (runbook) for the rest

- Status: accepted (2026-10-12), chosen by the product owner after `evals/reports/strategy-comparison-2026-10-12.md`
- Supersedes for decision-making: the Kev zero-shot classifier (ADR 0006), which stays available as `systemone`.

## Context
On 150 realistic Kafka cases (`evals/datasets/triage_kafka_v2.jsonl`), behind the same pre-route rules:
Kev choice 0.520, Kev yes/no 0.480, signatures 0.860 (131/150 coverage), signatures+Kev 0.873, Qwen 0.647,
Qwen+runbook 0.927 (0 unsafe), signatures+Qwen 0.967. The deciding Kafka signals are numbers against documented
thresholds and specific Kafka log identifiers, which a zero-shot classifier reads poorly. Caveat: dataset, signatures
and runbook share one research source, so these are upper bounds until real incidents are labelled.

## Decision
1. `pre_route` order: policy rules (maintenance, Dynatrace test notification, duplicate) -> deterministic Kafka
   signatures (`domain/kafka_signatures.py`, decision `source=rules`, `rule_id` = signature, `rationale` = facts)
   -> decision model.
2. Decision model in production: `TRIAGE__DECISION__PROVIDER=llm` (`compose.qwen.yaml`): Qwen3.8 Flash on the
   Spark via vLLM's OpenAI API, strict JSON `{action, reason}`, runbook from `config/triage-runbook.md`,
   temperature 0, thinking off. Output is untrusted: unknown actions / malformed JSON -> DecisionModelError -> rules
   fallback. `TriageDecision.rationale` (contract 1.1) carries the reason into notifications.
3. Policy: deterministic rule/signature decisions skip the model-confidence gates for restart and ticket (they are
   audited by id); target, ambiguity, autonomy ceiling, approval and server guard rails still apply. Model decisions
   without calibrated confidence (Qwen) can neither automate a restart nor open a ticket on their own: a restart
   proposal escalates, a ticket proposal notifies.
4. Default `compose.yaml` stays `rules` (no external model); Kev (`systemone`) remains selectable.
5. Qwen down (for example while fine-tuning Kev on the Spark) degrades to signatures + safe rules fallback.

## Consequences
Live: all 18 Kafka workflow scenarios got the expected action (17 by signature, 1 by Qwen). Latency is instant for
signature matches and ~8 s (p95 ~13 s) when Qwen decides. Next: fine-tune Kev on the Spark (Unsloth / Kev trainer)
as a fast, calibrated replacement for Qwen in step 2, promoted only if it matches on a held-out set.
