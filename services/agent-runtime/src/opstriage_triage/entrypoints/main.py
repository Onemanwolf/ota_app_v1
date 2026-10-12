from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from opstriage_shared.logging import configure_logging
from opstriage_shared.telemetry import configure_telemetry
from opstriage_triage.entrypoints.app import create_app
from opstriage_triage.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="opstriage-triage")
    parser.add_argument("--draw-graph", type=Path, help="write the graph as Mermaid to this file and exit")
    args = parser.parse_args()
    if args.draw_graph:
        from opstriage_triage.graph.build import build_triage_graph

        args.draw_graph.write_text(
            build_triage_graph(None).get_graph(xray=1).draw_mermaid(), encoding="utf-8"
        )
        return
    settings = Settings()  # values come from the environment
    configure_logging("agent-runtime", settings.log_level)
    configure_telemetry("agent-runtime", settings.telemetry)
    uvicorn.run(create_app(settings), host=settings.server.host, port=settings.server.port, log_config=None)
