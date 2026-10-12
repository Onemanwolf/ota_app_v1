from __future__ import annotations

import uvicorn

from opstriage_remediation.bootstrap import build_container
from opstriage_remediation.entrypoints.server import build_app
from opstriage_remediation.settings import Settings
from opstriage_shared.logging import configure_logging
from opstriage_shared.telemetry import configure_telemetry


def main() -> None:
    settings = Settings()  # values come from the environment
    configure_logging("mcp-remediation", settings.log_level)
    configure_telemetry("mcp-remediation", settings.telemetry)
    app = build_app(settings, build_container(settings))
    uvicorn.run(app, host=settings.server.host, port=settings.server.port, log_config=None)
