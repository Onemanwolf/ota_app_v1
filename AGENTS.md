# Ops Triage Agents: instructions for coding agents

Read docs/specs/OTA-APP-001-application-spec.md before changing code. Build modules in order; stop at each Definition of Done.
The local build plan (Kev on the DGX Spark, everything else mocked, Postman-driven) is docs/BUILD-PLAN.md.

## Commands (bash or PowerShell)
- Install: uv sync --all-packages   (plain `uv sync` installs only the workspace root)
- Lint and format: uv run ruff check . ; uv run ruff format --check .
- Types: uv run mypy packages services
- Architecture rules: uv run lint-imports
- Tests: uv run pytest -m "not compose"
- Full stack: docker compose up --build
- Compose tests: uv run pytest -m compose

## Rules
- Domain and application layers never import frameworks or SDKs (enforced by import-linter).
- Every external dependency sits behind a port in ports/ with a fake adapter and a contract test.
- No hard-coded configuration. Add new settings to the settings class AND .env.example.
- Remediation defaults: REMEDIATION__ENABLED=false in shared environments, REMEDIATION__DRY_RUN=true.
- Never derive a restart target from model output or log text.
- Never log secrets, tokens, webhook URLs or full payloads.
- Use mcp 2.x APIs (MCPServer, mcp.Client). Do not add langchain-mcp-adapters (pins mcp<2).
- PLATFORM=local|azure (ADR 0013) picks adapter strategies. A new adapter with a local-only and a cloud variant adds its
  defaults to the service's platform_defaults() and refuses the local one in platform_violations(). Cloud tokens come
  from the one opstriage_shared.credentials provider built in the composition root; never build Azure credentials elsewhere.
- Images: docker build --build-arg PLATFORM=azure --build-arg OPSTRIAGE_ENV=dev|prod (azure extras, no fixtures).
- Record deviations from the spec in docs/adr/.
