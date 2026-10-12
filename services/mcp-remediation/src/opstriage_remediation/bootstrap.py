"""Composition root for mcp-remediation."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import yaml

from opstriage_remediation.adapters.dry_run import DryRunOnlyExecutor
from opstriage_remediation.adapters.executions_pg import PostgresExecutionStore
from opstriage_remediation.application.service import GuardSettings, RemediationService
from opstriage_remediation.domain.targets import TargetRegistry
from opstriage_remediation.ports.executor import RestartExecutor
from opstriage_remediation.settings import Settings
from opstriage_shared.clock import SystemClock
from opstriage_shared.db import Pool, migrate, open_pool

MIGRATIONS = Path(__file__).resolve().parent / "migrations"  # shipped inside the package


def load_registry(path: Path) -> TargetRegistry:
    return TargetRegistry.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class Container:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: Pool | None = None
        self._service: RemediationService | None = None

    @property
    def service(self) -> RemediationService:
        if self._service is None:
            raise RuntimeError("container not started")
        return self._service

    async def start(self) -> None:
        s = self._settings
        registry = await asyncio.to_thread(load_registry, s.remediation.targets_file)
        self._pool = await open_pool(s.db)
        await migrate(self._pool, "remediation", MIGRATIONS)
        clock = SystemClock()
        # Module 1: one dry-run-only executor for every kind referenced by the registry.
        dry_run: RestartExecutor = DryRunOnlyExecutor(started_at=clock.now())
        executors = dict.fromkeys(registry.executor_kinds(), dry_run)
        guard = GuardSettings(
            enabled=s.remediation.enabled,
            dry_run=s.remediation.dry_run,
            max_autonomy=s.remediation.max_autonomy,
            approval_ttl=timedelta(minutes=s.remediation.approval_ttl_minutes),
            allowed_approval_modes=frozenset(s.remediation.allowed_approval_modes),
        )
        self._service = RemediationService(
            registry, PostgresExecutionStore(self._pool), executors, clock, guard
        )

    async def stop(self) -> None:
        if self._pool is not None:
            await self._pool.close()


def build_container(settings: Settings) -> Container:
    return Container(settings)
