"""structlog JSON logging. Correlation IDs (problem_id, alert_id, run_id) travel in contextvars."""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.contextvars import bind_contextvars, bound_contextvars, clear_contextvars

__all__ = ["bind_contextvars", "bound_contextvars", "clear_contextvars", "configure_logging"]


def configure_logging(service: str, level: str = "INFO") -> None:
    numeric = logging.getLevelNamesMapping()[level.upper()]
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    structlog.configure(
        processors=[
            *shared,
            structlog.processors.dict_tracebacks,
            structlog.processors.EventRenamer("message"),
            _add_service(service),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    # Route stdlib loggers (uvicorn, psycopg, httpx) through the same JSON format.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.EventRenamer("message"),
                _add_service(service),
                structlog.processors.JSONRenderer(),
            ],
            foreign_pre_chain=shared,
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(numeric)


def _add_service(service: str) -> structlog.types.Processor:
    def processor(
        _logger: structlog.types.WrappedLogger, _name: str, event: structlog.types.EventDict
    ) -> structlog.types.EventDict:
        event.setdefault("service", service)
        return event

    return processor
