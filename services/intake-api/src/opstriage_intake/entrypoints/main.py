from __future__ import annotations

import uvicorn

from opstriage_intake.entrypoints.app import create_app
from opstriage_intake.settings import Settings
from opstriage_shared.logging import configure_logging
from opstriage_shared.telemetry import configure_telemetry


def main() -> None:
    settings = Settings()  # values come from the environment
    configure_logging("intake-api", settings.log_level)
    configure_telemetry("intake-api", settings.telemetry)
    uvicorn.run(create_app(settings), host=settings.server.host, port=settings.server.port, log_config=None)
