"""Composition root for mcp-remediation."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from pathlib import Path

import structlog
import yaml

from opstriage_remediation.adapters.executions_pg import PostgresExecutionStore
from opstriage_remediation.adapters.registry import registry as executor_registry
from opstriage_remediation.application.service import GuardSettings, RemediationService
from opstriage_remediation.domain.targets import ExecutorKind, TargetRegistry
from opstriage_remediation.ports.executor import RestartExecutor
from opstriage_remediation.settings import Settings
from opstriage_shared.clock import SystemClock
from opstriage_shared.credentials import TokenProvider, credentials_for
from opstriage_shared.db import Pool, migrate, open_pool

MIGRATIONS = Path(__file__).resolve().parent / "migrations"  # shipped inside the package
RECONCILE_EVERY_SECONDS = 60

log = structlog.get_logger()


def load_registry(path: Path) -> TargetRegistry:
    return TargetRegistry.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def check_registry_for_platform(registry: TargetRegistry, settings: Settings) -> None:
    """PLATFORM=azure refuses local_docker targets at startup: the docker socket is root (ADR 0013)."""
    if settings.platform != "azure":
        return
    local = [t.id for t in registry.targets if t.executor.kind is ExecutorKind.LOCAL_DOCKER]
    if local:
        raise ValueError(
            f"PLATFORM=azure refuses local_docker targets in {settings.remediation.targets_file}: "
            + ", ".join(local)
        )


def guard_settings(settings: Settings) -> GuardSettings:
    r = settings.remediation
    return GuardSettings(
        enabled=r.enabled,
        dry_run=r.dry_run,
        max_autonomy=r.max_autonomy,
        approval_ttl=timedelta(minutes=r.approval_ttl_minutes),
        allowed_approval_modes=frozenset(r.allowed_approval_modes),
        sync_wait_seconds=r.sync_wait_seconds,
        breaker_failures=r.circuit_breaker_failures,
        breaker_window=timedelta(minutes=r.circuit_breaker_window_minutes),
    )


class Container:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: Pool | None = None
        self._service: RemediationService | None = None
        self._reconciler: asyncio.Task[None] | None = None
        self._creds: TokenProvider | None = None
        self._executors: dict[ExecutorKind, RestartExecutor] = {}

    @property
    def service(self) -> RemediationService:
        if self._service is None:
            raise RuntimeError("container not started")
        return self._service

    async def start(self, *, background: bool = True) -> None:
        s = self._settings
        registry = await asyncio.to_thread(load_registry, s.remediation.targets_file)
        check_registry_for_platform(registry, s)
        self._creds = credentials_for(s)  # ONE credential per process (ADR 0013)
        # fails fast on unknown kinds
        executors = self._executors = executor_registry.build_all(s, registry.executor_kinds(), self._creds)
        self._pool = await open_pool(s.db, credential=getattr(self._creds, "credential", None))
        await migrate(self._pool, "remediation", MIGRATIONS)
        self._service = RemediationService(
            registry, PostgresExecutionStore(self._pool), executors, SystemClock(), guard_settings(s)
        )
        log.info(
            "remediation_started",
            enabled=s.remediation.enabled,
            dry_run=s.remediation.dry_run,
            max_autonomy=s.remediation.max_autonomy,
            targets=[t.id for t in registry.targets],
            executors=sorted(k.value for k in executors),
        )
        if background:
            self._reconciler = asyncio.create_task(self._reconcile_loop(), name="reconciler")

    async def _reconcile_loop(self) -> None:
        while True:  # at startup and every minute (spec 10.3)
            try:
                await self.service.reconcile()
            except Exception:
                log.exception("reconcile_failed")
            await asyncio.sleep(RECONCILE_EVERY_SECONDS)

    async def stop(self) -> None:
        if self._reconciler is not None:
            self._reconciler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconciler
        if self._service is not None:
            await self._service.drain()
        if self._pool is not None:
            await self._pool.close()
        for executor in self._executors.values():
            close = getattr(executor, "close", None)
            if close is not None:
                await close()
        if self._creds is not None:
            await self._creds.close()


def build_container(settings: Settings) -> Container:
    return Container(settings)
