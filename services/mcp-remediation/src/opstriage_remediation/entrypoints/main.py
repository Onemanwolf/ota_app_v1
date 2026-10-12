"""`opstriage-remediation` serves MCP; `opstriage-remediation reset-breaker --operator NAME` is the admin CLI
that resets the circuit breaker (run it inside the container: docker compose exec mcp-remediation ...)."""

from __future__ import annotations

import argparse
import asyncio

import uvicorn

from opstriage_remediation.bootstrap import Container, build_container
from opstriage_remediation.entrypoints.server import build_app
from opstriage_remediation.settings import Settings
from opstriage_shared.logging import configure_logging
from opstriage_shared.telemetry import configure_telemetry


async def _reset_breaker(settings: Settings, operator: str, reason: str | None) -> None:
    container = Container(settings)
    await container.start(background=False)
    try:
        was_open = await container.service.breaker_open()
        await container.service.reset_breaker(operator, reason)
        print(f"circuit breaker reset by {operator} (was {'open' if was_open else 'closed'})")
    finally:
        await container.stop()


def main() -> None:
    parser = argparse.ArgumentParser(prog="opstriage-remediation")
    sub = parser.add_subparsers(dest="command")
    reset = sub.add_parser("reset-breaker", help="reset the circuit breaker (writes an audit log line)")
    reset.add_argument("--operator", required=True, help="who is resetting it (recorded)")
    reset.add_argument("--reason", default=None)
    args = parser.parse_args()
    settings = Settings()  # values come from the environment
    configure_logging("mcp-remediation", settings.log_level)
    if args.command == "reset-breaker":
        asyncio.run(_reset_breaker(settings, args.operator, args.reason))
        return
    configure_telemetry("mcp-remediation", settings.telemetry)
    app = build_app(settings, build_container(settings))
    uvicorn.run(app, host=settings.server.host, port=settings.server.port, log_config=None)
