from __future__ import annotations

import uvicorn

from opstriage_notification.bootstrap import Container
from opstriage_notification.entrypoints.server import build_app
from opstriage_notification.settings import Settings
from opstriage_shared.logging import configure_logging
from opstriage_shared.telemetry import configure_telemetry


def main() -> None:
    settings = Settings()  # values come from the environment
    configure_logging("mcp-notification", settings.log_level)
    configure_telemetry("mcp-notification", settings.telemetry)
    app = build_app(settings, Container(settings))
    uvicorn.run(app, host=settings.server.host, port=settings.server.port, log_config=None)
