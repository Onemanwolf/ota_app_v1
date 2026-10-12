"""LocalDockerExecutor: docker-py over the mounted socket. LOCAL DEVELOPMENT ONLY: the socket is root on the
host. The registry builds it only with APP_ENV=local and REMEDIATION__ALLOW_LOCAL_DOCKER=true (spec 14)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import docker
from docker.errors import DockerException, NotFound

from opstriage_remediation.domain.targets import RestartTarget
from opstriage_remediation.ports.executor import ContainerStatus, ExecutionOutcome, ExecutorUnavailable


def parse_docker_time(value: str | None) -> datetime | None:
    """Docker reports RFC 3339 with nanoseconds ('...51.225825041Z'); Python takes microseconds."""
    if not value or value.startswith("0001-01-01"):
        return None
    head, _, frac = value.rstrip("Z").partition(".")
    micro = (frac + "000000")[:6] if frac else "000000"
    return datetime.fromisoformat(f"{head}.{micro}").replace(tzinfo=UTC)


class LocalDockerExecutor:
    kind = "local_docker"

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_env(cls) -> LocalDockerExecutor:
        try:
            return cls(docker.from_env())
        except DockerException as exc:
            raise ExecutorUnavailable(f"docker socket unavailable: {type(exc).__name__}") from exc

    async def restart(self, target: RestartTarget, timeout_s: int) -> ExecutionOutcome:
        return await asyncio.to_thread(self._restart, target.executor.container, timeout_s)

    async def status(self, target: RestartTarget) -> ContainerStatus:
        return await asyncio.to_thread(self._status, target.executor.container)

    def _restart(self, name: str, timeout_s: int) -> ExecutionOutcome:
        try:
            container = self._client.containers.get(name)
            container.restart(timeout=timeout_s)
            container.reload()
        except NotFound:
            return ExecutionOutcome(succeeded=False, output_tail=f"no such container: {name} exit_code=1")
        except DockerException as exc:
            raise ExecutorUnavailable(f"docker error: {type(exc).__name__}") from exc
        state = container.attrs.get("State", {})
        running = bool(state.get("Running"))
        code = 0 if running else 70
        tail = f"status={state.get('Status')} started_at={state.get('StartedAt')} exit_code={code}"
        return ExecutionOutcome(succeeded=running, output_tail=tail)

    def _status(self, name: str) -> ContainerStatus:
        try:
            container = self._client.containers.get(name)
        except NotFound:
            return ContainerStatus(running=False, detail=f"no such container: {name}")
        except DockerException as exc:
            raise ExecutorUnavailable(f"docker error: {type(exc).__name__}") from exc
        state = container.attrs.get("State", {})
        return ContainerStatus(
            running=bool(state.get("Running")),
            started_at=parse_docker_time(state.get("StartedAt")),
            detail=state.get("Status"),
        )
