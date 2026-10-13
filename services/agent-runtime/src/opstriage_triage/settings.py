from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, model_validator

from opstriage_shared.settings import BaseServiceSettings


class InboxSettings(BaseModel):
    lease_seconds: int = Field(default=900, ge=30)
    max_attempts: int = Field(default=3, ge=1)
    idle_wait_seconds: float = Field(default=5.0, gt=0)
    retry_backoff_seconds: float = Field(default=10.0, ge=0)  # times the attempt number


class DecisionSettings(BaseModel):
    provider: Literal["rules", "systemone", "llm"] = "rules"  # llm = OpenAI-compatible chat + runbook
    base_url: str = "http://kev:8009"
    path: str = "/v1/systemone"
    model: str = "kev-latest"
    auth_mode: Literal["none", "bearer", "api_key", "entra"] = "none"
    api_key: SecretStr | None = None
    api_key_header: str = "api-key"  # auth_mode=api_key (Azure OpenAI)
    response_format: Literal["json_schema", "json_object", "none"] = "json_schema"  # llm provider
    extra_body: dict[str, Any] = Field(default_factory=dict)  # llm provider: model-specific request fields
    timeout_seconds: float = Field(default=10.0, gt=0)
    shuffle_options: bool = False
    runbook_file: Path = Path("/app/config/triage-runbook.md")  # llm provider: system-prompt runbook
    max_tokens: int = Field(default=300, ge=50, le=2000)  # llm provider


class ContextSettings(BaseModel):
    provider: Literal["fake", "grail"] = "fake"
    fixtures_dir: Path = Path("/app/fixtures/context")
    max_log_lines: int = Field(default=50, ge=0)
    max_state_chars: int = Field(default=12_000, ge=1_000)


class SummarizerSettings(BaseModel):
    provider: Literal["template", "foundry"] = "template"


class ApprovalSettings(BaseModel):
    mode: Literal["auto_approve", "auto_reject", "manual_api"] = "manual_api"
    ttl_minutes: int = Field(default=30, ge=1, le=240)
    sweep_interval_seconds: float = Field(default=60.0, gt=0)


class McpSettings(BaseModel):
    notification_url: str = "http://mcp-notification:8101/mcp"
    ticketing_url: str = "http://mcp-ticketing:8102/mcp"
    remediation_url: str = "http://mcp-remediation:8103/mcp"
    notification_token: SecretStr = SecretStr("")
    ticketing_token: SecretStr = SecretStr("")
    remediation_token: SecretStr = SecretStr("")
    timeout_seconds: float = Field(default=60.0, gt=0)


class TriageOptions(BaseModel):
    policy_file: Path = Path("/app/config/policy.yaml")
    worker_concurrency: int = Field(default=4, ge=1, le=64)
    run_timeout_seconds: float = Field(default=600.0, gt=0)
    public_base_url: str = "http://localhost:8010"  # used in notification links to run status
    inbox: InboxSettings = InboxSettings()
    decision: DecisionSettings = DecisionSettings()
    context: ContextSettings = ContextSettings()
    summarizer: SummarizerSettings = SummarizerSettings()
    approval: ApprovalSettings = ApprovalSettings()
    mcp: McpSettings = McpSettings()
    admin_tokens: list[SecretStr] = Field(default_factory=list)


class TriageServerSettings(BaseModel):
    host: str = "0.0.0.0"  # noqa: S104 (container)
    port: int = 8010


class Settings(BaseServiceSettings):
    triage: TriageOptions = TriageOptions()
    server: TriageServerSettings = TriageServerSettings()

    @model_validator(mode="after")
    def _check(self) -> Settings:
        t = self.triage
        if t.approval.mode == "auto_approve" and self.app_env != "local":
            raise ValueError("TRIAGE__APPROVAL__MODE=auto_approve is only permitted when APP_ENV=local")
        if t.inbox.lease_seconds <= t.run_timeout_seconds:
            raise ValueError("TRIAGE__INBOX__LEASE_SECONDS must exceed TRIAGE__RUN_TIMEOUT_SECONDS")
        if t.decision.provider == "systemone" and t.decision.auth_mode == "bearer" and not t.decision.api_key:
            raise ValueError("TRIAGE__DECISION__API_KEY is required for auth_mode=bearer")
        if t.context.provider == "grail":
            raise ValueError(
                "the grail context provider is not built in the local scope (BUILD-PLAN section 2)"
            )
        if t.summarizer.provider == "foundry":
            raise ValueError("the foundry summarizer arrives with the Azure deployment (OTA-AZ-001)")
        return self
