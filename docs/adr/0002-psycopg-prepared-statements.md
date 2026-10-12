# ADR 0002: Disable psycopg prepared statements (`prepare_threshold=None`)

- Status: accepted (2026-10-12, Module 0)
- Spec: OTA-APP-001 section 12.1, `open_pool` sets `prepare_threshold=0`

## Context
In psycopg 3, `prepare_threshold=0` means "prepare every statement on first execution". It does not mean
"never prepare". A migration file with several statements then fails with `cannot insert multiple commands
into a prepared statement`. The integration test `test_failed_migration_is_not_recorded` caught this.
The spec's intent (no server-side prepared statements, safe behind connection poolers) is `None`.

## Decision
`open_pool` uses `prepare_threshold=None`. The migration runner also passes `prepare=False` explicitly.

## Consequences
No prepared-statement reuse. That is irrelevant at this system's query volume, and it is pooler-safe in Azure.
