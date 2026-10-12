# ADR 0008: Realistic Kafka fixtures, contract 1.1 and the Kafka eval set (v2)

- Status: accepted (2026-10-12)
- Evidence: `reports/Dynatrace Kafka triage data models.md` (+ Appendix A/B), `evals/reports/kev-4b-kafka-2026-10-12.md`

## Decisions
1. **ProblemEvent schema 1.1 (additive):** `impact_levels`, typed `evidence`, `is_duplicate`. 1.0 events still validate.
2. **Severity follows Grail:** 1 = Critical ... 5 = Informational, null when unset; never invented for Problems API v2 or
   the classic webhook (they have none). `Policy.notification_severity` was inverted (treated >= 4 as critical) and is
   fixed; without severity it falls back to the documented category order. Older fixtures were remapped (5->1, 4->2, 2->4).
3. **One normalization module** `opstriage_contracts.dynatrace` (pure functions) is shared by intake and poller:
   PERFORMANCE->SLOWDOWN, SERVICE/SERVICES/"Services"->SERVICE, entity types lowercase with `dt.entity.` stripped,
   v2 EntityStub/evidence/tags, `endTime = -1`, Grail ns or ISO timestamps. Entity types stay lowercase canonical
   (the report suggested UPPER_SNAKE; the target matcher already compares lowercase).
4. **Problems API v2 poller requests `fields=evidenceDetails,impactAnalysis` on every page** (was missing; the docs
   require it again with `nextPageKey`).
5. **Evidence for Workflow/Grail sources comes from the context source** (stand-in for joining `dt.davis.event_ids`
   to `dt.davis.events`); Kev's state text merges event and context evidence.
6. **Structural suppress rules** (no word lists): Dynatrace "Send test notification" by exact title, and
   `dt.davis.is_duplicate`. Maintenance stays tag-based.
7. **Rules-only classifier no longer needs a root cause** (absent on most real problems); the registry target decides.
   It cannot tell a hung broker from a disk-full one: that is the decision model's job, and every restart still needs
   approval and passes the server guard rails.
8. **Fixtures:** `fixtures/kafka/generate.py` defines 20 scenarios once and renders the raw Grail record, our Workflow
   template, the poller's DQL row, the classic body with `{ProblemDetailsJSONv2}`, a Problems API v2 page, and context
   (log lines + evidence + Grail log records). A parity test asserts all shapes normalize to the same problem.
   `context_fixture` in Workflow tags is a test hook only.
9. **Kafka restart targets** (broker PGIs, orders consumer) with stand-in containers in compose; a problem touching
   several brokers matches several targets and is therefore ambiguous (escalate, never restart).
10. **Eval set v2** (`evals/datasets/triage_kafka_v2.jsonl`, 150 cases) from the scenarios with varied brokers, metric
   magnitudes, titles, root cause (~13%), severity, and evidence path. Labels are INFERRED from the report's runbook
   table and need human review (open question 12).

## Kev-4B on the Kafka set (vs the synthetic v1 set)
| | v1 synthetic | v2 Kafka |
|---|---|---|
| Kev accuracy / macro F1 | 0.625 / 0.536 | 0.447 / 0.361 |
| Rules + Kev end to end | n/a | 0.520 (rules decided 15 cases) |
| escalate_human recall | 0.79 | 1.00 (all 8 adversarial disk-full brokers escalated) |
| restart_container | P 0.74 / R 0.83 | P 1.00 / R 0.07 (escalates single-broker failures) |
| open_ticket recall | 0.23 | 0.00 (answers notify_only) |
| Confident errors | 0 | 0 |
| Option-order sensitivity | 0.33-0.38 | 0.29 |

Every remaining error is in the safe direction (notify or escalate instead of ticket or restart); there were no
unsafe restarts. Kev cannot yet distinguish "restart this one broker" from "escalate" or "ticket" from "notify" on
realistic Kafka evidence. Next: yes/no question decomposition and option-order averaging (`/v1/systemone/permute`),
then fine-tuning on reviewed, real incidents.
