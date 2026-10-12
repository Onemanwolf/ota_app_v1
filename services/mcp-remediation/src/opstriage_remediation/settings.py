from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator

from opstriage_shared.settings import BaseServiceSettings, ServiceAuthSettings

ApprovalModeName = Literal["auto_approve", "manual_api", "teams_link", "teams_bot"]


def _default_approval_modes() -> list[ApprovalModeName]:
    return ["manual_api"]


class AzureSettings(BaseModel):
    subscription_id: str | None = None
    managed_identity_client_id: str | None = None


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

    @model_validator(mode="after")
    def _local_docker_only_locally(self) -> Settings:
        if self.remediation.allow_local_docker and self.app_env != "local":
            raise ValueError("REMEDIATION__ALLOW_LOCAL_DOCKER is only permitted when APP_ENV=local")
        if "auto_approve" in self.remediation.allowed_approval_modes and self.app_env != "local":
            raise ValueError("auto_approve proofs are only accepted when APP_ENV=local")
        return self
