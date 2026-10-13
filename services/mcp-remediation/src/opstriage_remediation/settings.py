from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, model_validator

from opstriage_shared.settings import BaseServiceSettings, Platform, ServiceAuthSettings

ApprovalModeName = Literal["auto_approve", "manual_api", "teams_link", "teams_bot"]


def _default_approval_modes() -> list[ApprovalModeName]:
    return ["manual_api"]


class AzureSettings(BaseModel):
    """azure_run_command executor (spec 12.5.2). Token:  the process credential (AZURE_CLIENT_ID)."""

    subscription_id: str | None = None  # when set, targets in any other subscription are refused
    managed_identity_client_id: str | None = None
    arm_endpoint: str = Field(default="https://management.azure.com", pattern=r"^https://[a-z0-9.-]+$")
    # INFERRED: a GA Microsoft.Compute API version that serves POST .../virtualMachines/{vm}/runCommand.
    run_command_api_version: str = Field(default="2024-07-01", pattern=r"^\d{4}-\d{2}-\d{2}(-preview)?$")
    # VM-side allow-list script (installed by the VM owner). Embedded in the constant script: strictly a path.
    allowlist_script: str = Field(
        default="/usr/local/sbin/restart-allowed.sh", pattern=r"^/[A-Za-z0-9_./-]+$"
    )
    poll_interval_seconds: float = Field(default=5.0, gt=0, le=60)  # used when ARM sends no Retry-After
    # Budget on top of the target's restart_timeout_seconds: Run Command takes >= 20 s and queues per VM.
    # Stays below the use case's own cut-off (timeout + 180 s) so the executor reports the timeout itself.
    run_command_overhead_seconds: int = Field(default=170, ge=20, le=175)
    http_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    max_attempts: int = Field(default=5, ge=1, le=10)  # per ARM call, for 429, 5xx and transport errors


class SshSettings(BaseModel):
    username: str = "svc-restart"
    private_key: SecretStr | None = None  # PEM content, from Key Vault in Azure
    known_hosts_file: Path | None = None  # host keys must be pinned
    connect_timeout_seconds: float = 10.0


class RemediationOptions(BaseModel):
    enabled: bool = False  # global kill switch: default OFF
    dry_run: bool = True  # default ON
    targets_file: Path = Path("config/targets.yaml")
    allow_local_docker: bool = False  # must be true AND app_env == "local"
    max_autonomy: Literal["notify_only", "approval_required", "autonomous"] = (
        "approval_required"  # global ceiling
    )
    approval_ttl_minutes: int = Field(default=30, ge=1, le=240)
    allowed_approval_modes: list[ApprovalModeName] = Field(default_factory=_default_approval_modes)
    default_restart_timeout_seconds: int = Field(default=30, ge=1, le=300)
    sync_wait_seconds: int = Field(default=120, ge=5, le=200)  # stay below the 240 s ACA ingress timeout
    circuit_breaker_failures: int = 3
    circuit_breaker_window_minutes: int = 30
    azure: AzureSettings = AzureSettings()
    ssh: SshSettings = SshSettings()


class RemediationServerSettings(BaseModel):
    host: str = "0.0.0.0"  # noqa: S104 (container)
    port: int = 8103
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost:*", "mcp-remediation:*"])


class Settings(BaseServiceSettings):
    remediation: RemediationOptions = RemediationOptions()
    server: RemediationServerSettings = RemediationServerSettings()
    auth: ServiceAuthSettings = ServiceAuthSettings()

    @classmethod
    def platform_defaults(cls, platform: Platform) -> dict[str, Any]:
        if platform != "azure":
            return {}
        # Safe by default in Azure: restarts off, dry run, approval required, no docker socket (ADR 0013).
        return {
            "remediation": {
                "enabled": False,
                "dry_run": True,
                "max_autonomy": "approval_required",
                "allow_local_docker": False,
            }
        }

    def platform_violations(self) -> list[str]:
        problems = []
        if self.remediation.allow_local_docker:
            problems.append("REMEDIATION__ALLOW_LOCAL_DOCKER must be false (the docker socket is root)")
        if "auto_approve" in self.remediation.allowed_approval_modes:
            problems.append("REMEDIATION__ALLOWED_APPROVAL_MODES must not contain auto_approve")
        return problems

    @model_validator(mode="after")
    def _local_docker_only_locally(self) -> Settings:
        if self.remediation.allow_local_docker and self.app_env != "local":
            raise ValueError("REMEDIATION__ALLOW_LOCAL_DOCKER is only permitted when APP_ENV=local")
        if "auto_approve" in self.remediation.allowed_approval_modes and self.app_env != "local":
            raise ValueError("auto_approve proofs are only accepted when APP_ENV=local")
        return self
