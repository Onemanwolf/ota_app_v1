from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, model_validator

from opstriage_dynatrace.grail import GrailSettings
from opstriage_llm import LlmEndpoint
from opstriage_shared.settings import BaseServiceSettings, Platform


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


class ContextGrailSettings(GrailSettings):
    """Read-only scopes for problems, Davis events and logs (spec 12.3.3 plus storage:logs:read)."""

    oauth_scopes: str = (
        "storage:buckets:read storage:events:read storage:logs:read storage:entities:read "
        "storage:smartscape:read"
    )


class ContextSettings(BaseModel):
    provider: Literal["fake", "grail"] = "fake"
    fixtures_dir: Path = Path("/app/fixtures/context")  # fake provider
    grail: ContextGrailSettings = ContextGrailSettings()
    log_window_minutes: int = Field(default=30, ge=1, le=1_440)  # grail provider: how far back logs go
    problem_lookback_days: int = Field(default=30, ge=1, le=90)  # grail provider: get_problem re-read window
    timeout_seconds: float = Field(default=20.0, gt=0)  # grail provider: budget per lookup, then degrade
    max_log_lines: int = Field(default=50, ge=0)
    max_state_chars: int = Field(default=12_000, ge=1_000)


class SummarizerSettings(LlmEndpoint):
    """template: deterministic text. foundry: any OpenAI-compatible chat model (Foundry / Azure OpenAI v1 API)
    writes the human-readable summary; its output is untrusted and the template is the fallback.

    `deployment` is kept as an alias of `model` (Foundry calls the request's model field the deployment).
    """

    provider: Literal["template", "foundry"] = "template"
    deployment: str = ""
    base_url: str = ""  # e.g. https://<foundry-resource>.openai.azure.com/openai/v1
    path: str = "/chat/completions"  # base_url ends in /openai/v1 for Foundry and Azure OpenAI
    max_tokens: int = Field(default=400, ge=64, le=4_000)
    timeout_seconds: float = Field(default=15, gt=0)
    repair_attempts: int = Field(default=0, ge=0, le=3)  # notifications are latency-sensitive
    max_chars: int = Field(default=1_500, ge=200, le=4_000)  # longer summaries are cut

    @model_validator(mode="after")
    def _deployment_is_model(self) -> SummarizerSettings:
        if self.deployment and not self.model:
            self.model = self.deployment
        return self


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
    insights_base_url: str = ""  # health-review web view; set to add an "Analyse root cause" link to cards
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


def _is_local_endpoint(url: str) -> bool:
    host = url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    return host in {"kev", "jev", "localhost", "127.0.0.1", "host.docker.internal"}


class Settings(BaseServiceSettings):
    triage: TriageOptions = TriageOptions()
    server: TriageServerSettings = TriageServerSettings()

    @classmethod
    def platform_defaults(cls, platform: Platform) -> dict[str, Any]:
        if platform != "azure":
            return {}
        # Microsoft-Decision-1 in Foundry (spec 12.4.3) and Grail context. BASE_URL is per environment.
        # The summarizer stays `template`; foundry is opt-in.
        return {
            "triage": {
                "decision": {
                    "provider": "systemone",
                    "path": "/providers/microsoft/v1/systemone",
                    "model": "decision-1",
                    "auth_mode": "entra",
                },
                "context": {"provider": "grail"},
            }
        }

    def platform_violations(self) -> list[str]:
        t, problems = self.triage, list[str]()
        if t.context.provider == "fake":
            problems.append("TRIAGE__CONTEXT__PROVIDER=fake reads local fixtures")
        if t.decision.provider != "rules" and t.decision.auth_mode == "none":
            problems.append("TRIAGE__DECISION__AUTH_MODE must not be none")
        if t.summarizer.provider == "foundry" and t.summarizer.auth_mode == "none":
            problems.append("TRIAGE__SUMMARIZER__AUTH_MODE must not be none")
        if t.approval.mode == "auto_approve":
            problems.append("TRIAGE__APPROVAL__MODE=auto_approve is local only")
        if t.decision.provider != "rules" and _is_local_endpoint(t.decision.base_url):
            problems.append("TRIAGE__DECISION__BASE_URL points at a local model (set the Foundry endpoint)")
        return problems

    def entra_settings(self) -> list[str]:
        t, out = self.triage, list[str]()
        if t.decision.provider != "rules" and t.decision.auth_mode == "entra":
            out.append("TRIAGE__DECISION__AUTH_MODE")
        if t.summarizer.provider == "foundry" and t.summarizer.auth_mode == "entra":
            out.append("TRIAGE__SUMMARIZER__AUTH_MODE")
        return out

    @model_validator(mode="after")
    def _check(self) -> Settings:
        t = self.triage
        if t.approval.mode == "auto_approve" and self.app_env != "local":
            raise ValueError("TRIAGE__APPROVAL__MODE=auto_approve is only permitted when APP_ENV=local")
        if t.inbox.lease_seconds <= t.run_timeout_seconds:
            raise ValueError("TRIAGE__INBOX__LEASE_SECONDS must exceed TRIAGE__RUN_TIMEOUT_SECONDS")
        if t.decision.provider == "systemone" and t.decision.auth_mode == "bearer" and not t.decision.api_key:
            raise ValueError("TRIAGE__DECISION__API_KEY is required for auth_mode=bearer")
        if t.context.provider == "grail" and not t.context.grail.configured:
            raise ValueError(
                "TRIAGE__CONTEXT__GRAIL__OAUTH_CLIENT_ID and TRIAGE__CONTEXT__GRAIL__OAUTH_CLIENT_SECRET "
                "are required for the grail context provider"
            )
        if t.summarizer.provider == "foundry" and not (t.summarizer.model and t.summarizer.base_url):
            raise ValueError(
                "TRIAGE__SUMMARIZER__BASE_URL and TRIAGE__SUMMARIZER__MODEL (or DEPLOYMENT) are required "
                "for the foundry summarizer"
            )
        return self
