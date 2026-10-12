"""`opstriage-poller --loop` (local container) or `--once` (Azure Container Apps Job). Same use case."""

from __future__ import annotations

import argparse
import asyncio
import signal

import structlog

from opstriage_poller.bootstrap import build_container
from opstriage_poller.settings import Settings
from opstriage_shared.logging import configure_logging
from opstriage_shared.telemetry import configure_telemetry

log = structlog.get_logger()


async def run(settings: Settings, *, once: bool) -> int:
    container = await build_container(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        while True:
            try:
                await container.poll.run_once()
            except Exception:
                log.exception("poll_cycle_failed")  # the cursor did not move; the next cycle retries
                if once:
                    return 1
            if once:
                return 0
            try:
                async with asyncio.timeout(settings.poller.interval_seconds):
                    await stop.wait()
                return 0
            except TimeoutError:
                continue
    finally:
        await container.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="opstriage-poller")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--loop", action="store_true", help="poll every POLLER__INTERVAL_SECONDS (default)")
    mode.add_argument("--once", action="store_true", help="run one cycle and exit (job mode)")
    args = parser.parse_args()
    settings = Settings()  # values come from the environment
    configure_logging("poller", settings.log_level)
    configure_telemetry("poller", settings.telemetry)
    raise SystemExit(asyncio.run(run(settings, once=args.once)))
