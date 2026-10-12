# ADR 0003: Local development on macOS with Kev on a DGX Spark

- Status: accepted (2026-10-12, Module 0)
- Plan: docs/BUILD-PLAN.md section 3

## Decisions
1. **Kev endpoint.** Kev runs on the Spark in the `kev` container, published on `0.0.0.0:8003` (not the spec's 8009).
   Containers use the Spark's IP (`192.168.5.147`) because Docker Desktop containers do not resolve mDNS
   `.local` names. It is set in `.env.local`, not in `.env.example`.
2. **Host ports are configurable.** `SINK_HOST_PORT` (default 8090) is only used for compose interpolation.
   On this Mac, 8090 belongs to another project, so `.env` sets 8091. Containers still talk on 8090 internally.
3. **import-linter** had no pin in the spec. It is pinned to 2.15 (latest on 2026-10-12).
4. **testcontainers 4.15** moved Postgres to `testcontainers.community.postgres`. Tests import it from there.
5. **httpx2** is a dev dependency because Starlette's TestClient needs it.
6. Commands are bash/zsh. `uv` and `docker compose` are identical to the PowerShell versions in the spec.
