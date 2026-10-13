# ADR 0012: Azure deployment: Decision-1 for triage, a Foundry GPT model for health review, secrets in Key Vault

- Status: accepted (2026-10-13). Decided by the product owner. The Decision-1 eval addendum is pending (phase 5).
- Related:
  - OTA-AZ-001 v1.1 (Sections 0.2, 7.6, 7.6.1, 9.1, 9.2);
  - ADR 0009 (signatures plus an LLM), ADR 0010 (model-agnostic access), ADR 0011 (health review).

## Context

Locally, the models run on the DGX Spark:
- **Triage:** signatures plus Qwen with the runbook (ADR 0009);
- **Health review:** Qwen as the analyst, with the DeBERTa guard (ADR 0011).

None of these are available in Azure, and the Spark takes no new GPU work. OTA-AZ-001 1.0 assumed Decision-1 for triage. It predates health review and the model-agnostic `llm` client.

## Decision

1. **Triage in Azure.** `pre_route` is unchanged: policy rules, then Kafka signatures, then the decision model. The decision model is `Microsoft-Decision-1` (`TRIAGE__DECISION__PROVIDER=systemone`, Entra auth). This is a deployment deviation from ADR 0009, which chose Qwen. The code is the same; only configuration differs.
2. **Health-review analyst in Azure.** The Foundry GPT deployment `gpt-main`, reached through `packages/llm` (`provider=llm`, `auth_mode=entra`, `response_format=json_schema`, `extra_body={}`). The triage summarizer uses `gpt-main` once the Foundry summarizer exists; until then it stays `template`.
3. **Safety until Decision-1 is measured:**
   - thresholds start from `policy.example.yaml`, whose placeholders are not calibrated for Decision-1;
   - `MAX_AUTONOMY=approval_required` and `DRY_RUN=true` in both environments;
   - Phase 5 scores signatures plus Decision-1 on `evals/datasets/triage_kafka_v2.jsonl` and appends the result here before any threshold or autonomy change.
4. **Secrets and identity:**
   - **Managed identity:** every app has its own user-assigned identity, used for PostgreSQL (Entra only, no passwords), Foundry (Foundry User role) and Key Vault.
   - **Dynatrace credentials:** the OAuth client secrets (one read-only client per service), the OTLP token and the webhook tokens live in Key Vault.
   - **Per-secret access:** each app can read only its own secrets.
   - **Operator-created secrets:** Terraform never holds their values.
5. **Database.** One PostgreSQL Flexible Server and one database. Each service owns its schema under its own identity; health review owns `insights`.

## Consequences

- **New application work is needed before deploying** (OTA-AZ-001 Section 0.2):
  - the `entra` extras;
  - the Grail context adapter;
  - per-environment config files;
  - health-review config baked into the image;
  - `db-bootstrap`.
- **Local and Azure differ** in the decision model. Evals must be run against both, and reports must name the model.
- **The guard has no Azure equivalent yet** (OTA-AZ-001 risk 11). Until it is decided, health review fails closed and withholds log templates.
- **Backups and retention:** 14-day backups retain deleted health-review rows; this goes in the privacy notes.
