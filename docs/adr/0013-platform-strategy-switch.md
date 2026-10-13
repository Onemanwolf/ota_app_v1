# ADR 0013: One `PLATFORM` switch selects the local or Azure adapter strategies

- Status: accepted (2026-10-13)
- Related: ADR 0010 (model-agnostic access), ADR 0012 (Azure models and secrets), OTA-AZ-001 v1.1 (Sections 0.2 and 9).

## Context

Every external dependency already sat behind a port, with the adapter chosen by a setting. Moving a service to Azure, however, meant setting 10–20 variables correctly per app. A missed variable silently kept the local strategy, for example fixture data in production. Each adapter also built its own Azure credential: PostgreSQL used `ManagedIdentityCredential` and Foundry used `DefaultAzureCredential`. Several Azure strategies were also missing:
- Grail triage context;
- Foundry summarizer;
- ACS email;
- VM Run Command.

## Decision

1. **The `PLATFORM` setting.** `PLATFORM=local|azure` lives on `BaseServiceSettings`, and the default is `local`, so local development is unchanged.
   - Each service declares `platform_defaults(platform)`, nested defaults that apply **under** explicit settings. Environment variables, `.env` and constructor values always win.
   - Each service also declares `platform_violations()`, the local-only strategies it refuses on Azure.
   - The shared Azure profile sets:
     - `DB__AUTH_MODE=entra`;
     - `TELEMETRY__ENABLED=true`;
     - `DB__ENTRA_CLIENT_ID` taken from `AZURE_CLIENT_ID`.
   - On Azure, the base class refuses:
     - `APP_ENV=local` or `test`;
     - password database authentication;
     - every service's violations, all listed at once.
2. **One credential provider per process** (`opstriage_shared.credentials`):
   - `NoCredentials` locally. Asking for a cloud token fails loudly rather than picking up a laptop `az login`.
   - `AzureCredentials` on Azure: the user-assigned managed identity named by `AZURE_CLIENT_ID`, or `DefaultAzureCredential` when no client ID is set.
   - Composition roots pass it to PostgreSQL (pool **and** the inbox LISTEN connection, through the new `opstriage_shared.db.connect`), Foundry, ACS and ARM. Domain and application layers never see it.
   - `packages/llm` accepts a `TokenSource` callable, so it does not depend on `opstriage_shared`.
3. **Azure adapters use REST, not SDKs** (httpx2 with the shared token). Only `azure-identity` and `azure-postgresql-auth` are needed. They install through each service's `azure` extra, and only when the image is built with `--build-arg PLATFORM=azure`; local images never contain them. The adapters:
   - ACS Email, `emails:send`;
   - ARM VM Run Command;
   - Foundry and Azure OpenAI, through `packages/llm`;
   - Grail, through `packages/dynatrace`.
4. **Images.** `PLATFORM` is baked into the image as an environment default. Azure images contain no fixtures: a build stage copies `fixtures/` only for `local`. Per-environment config is baked from `config/<file>.<OPSTRIAGE_ENV>.yaml`, as before; health-review now bakes its cluster list and runbook too.
5. **Secrets stay platform-neutral.** Container Apps resolves Key Vault references through each app's managed identity and injects them as environment variables. The app has no Key Vault client.
6. **ACS sends are not polled.** A `202` counts as delivered, and the operation id is logged. Polling can take minutes and the caller cannot act on the result. Retries reuse one idempotency key (`repeatability-request-id`), so a retried request is not sent twice.

## Consequences

- **Local:** `docker compose up`, the Spark models and Postman work unchanged, and the developer `.env` keeps winning locally.
- **Azure:** Section 9 of OTA-AZ-001 shrinks to `PLATFORM=azure` plus values only Azure knows: endpoints, client IDs and Key Vault references.
- **Startup checks:** a misconfigured Azure app fails at startup with every problem listed, instead of half-working.
- **Tests:** tests that build settings must pass `_env_file=None`, because `model_validate` also reads the developer `.env`.
- **Target registries:**
  - `config/targets.dev.yaml` and `targets.prod.yaml` now list only `azure_run_command` placeholder targets. The local and SSH examples from ADR 0004 are dropped. On Azure, a registry containing `local_docker` targets is refused at startup.
  - The Run Command executor cannot read container state without a second root-level command, so `status()` reports unavailable.
- **Settings errors hide their input** (`hide_input_in_errors`), so a failed startup never prints the DSN.
- **Ticketing still has no real adapter.** Only `fake` exists, so Azure is allowed to use it until a real system (ServiceNow, Jira or Azure DevOps) is chosen.
- **Adding another cloud** means a new `Platform` value, profiles and adapters. The domain and application code does not change.
