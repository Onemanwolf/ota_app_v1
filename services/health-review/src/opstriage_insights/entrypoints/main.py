from __future__ import annotations

import uvicorn

from opstriage_insights.entrypoints.app import create_app
from opstriage_insights.settings import Settings
from opstriage_shared.logging import configure_logging
from opstriage_shared.telemetry import configure_telemetry


def main() -> None:
    settings = Settings()  # values come from the environment
    configure_logging("health-review", settings.log_level)
    configure_telemetry("health-review", settings.telemetry)
    uvicorn.run(create_app(settings), host=settings.server.host, port=settings.server.port, log_config=None)
