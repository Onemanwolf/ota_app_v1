# ADR 0001: Docker images copy the whole `services/` tree

- Status: accepted (2026-10-12, Module 0)
- Spec: OTA-APP-001 section 7 (Dockerfile pattern and the note under it)

## Context
`uv sync --frozen --package <svc>` validates the workspace lockfile, which needs every member's
`pyproject.toml`. The spec's layer-2 step copies only `services/<svc>`, which breaks that check once
there is more than one service.

## Decision
Layer 1 (third-party dependencies) keeps the spec's bind-mount approach and stays cached unless `uv.lock`
changes. Layer 2 copies `packages/` and the whole `services/` tree, as the spec's own fallback allows.

## Consequences
A code change in any service invalidates layer 2 of every image. That is cheap (workspace code only). The
dependency layer, which is the slow one, is unaffected.
