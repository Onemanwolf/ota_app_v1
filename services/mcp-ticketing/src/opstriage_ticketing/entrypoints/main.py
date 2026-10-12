from __future__ import annotations

import uvicorn

from opstriage_shared.logging import configure_logging
from opstriage_shared.telemetry import configure_telemetry
from opstriage_ticketing.bootstrap import Container
from opstriage_ticketing.entrypoints.server import build_app
from opstriage_ticketing.settings import Settings


def main() -> None:
    settings = Settings()  # values come from the environment
    configure_logging("mcp-ticketing", settings.log_level)
    configure_telemetry("mcp-ticketing", settings.telemetry)
    app = build_app(settings, Container(settings))
    uvicorn.run(app, host=settings.server.host, port=settings.server.port, log_config=None)
