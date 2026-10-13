from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["local", "test", "dev", "prod"]
Platform = Literal["local", "azure"]  # selects the adapter strategy set (ADR 0013)

# Defaults every service takes on a platform. Explicit settings (env vars, .env, constructor) always win.
_SHARED_PROFILE: dict[str, dict[str, Any]] = {
    "local": {},
    "azure": {"db": {"auth_mode": "entra"}, "telemetry": {"enabled": True}},
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """`override` wins; nested dicts merge key by key."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class DatabaseSettings(BaseModel):
    dsn: SecretStr = Field(description="postgresql://user:pass@host:5432/db (password mode)")
    auth_mode: Literal["password", "entra"] = "password"
    entra_client_id: str | None = None  # user-assigned managed identity client ID (entra mode)
    pool_min_size: int = 1
    pool_max_size: int = 10
    pool_max_lifetime_seconds: int = 3000  # keep below Entra token lifetime


class TelemetrySettings(BaseModel):
    enabled: bool = False
    service_namespace: str = "opstriage"
    # Standard OTEL_EXPORTER_OTLP_* variables configure the exporter itself.


class ServiceAuthSettings(BaseModel):
    """Bearer tokens for service-to-service calls (v1). Upgrade path: Entra ID tokens."""

    inbound_tokens: list[SecretStr] = Field(default_factory=list)  # accepted; supports rotation


class BaseServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),  # later file wins; real env vars beat both
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,  # validation errors must never echo the DSN or other secrets
    )

    platform: Platform = "local"
    azure_client_id: str | None = None  # AZURE_CLIENT_ID: the app's user-assigned managed identity
    app_env: AppEnv = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    db: DatabaseSettings
    telemetry: TelemetrySettings = TelemetrySettings()

    @classmethod
    def platform_defaults(cls, platform: Platform) -> dict[str, Any]:
        """Service-specific strategy defaults for `platform`, nested like the settings (override)."""
        return {}

    def platform_violations(self) -> list[str]:
        """Local-only strategies this service must refuse on PLATFORM=azure. Override per service."""
        return []

    @model_validator(mode="before")
    @classmethod
    def _apply_platform_profile(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        platform = str(data.get("platform", "local")).lower()
        if platform not in _SHARED_PROFILE:
            return data  # the field validator reports the bad value
        defaults = deep_merge(_SHARED_PROFILE[platform], cls.platform_defaults(cast(Platform, platform)))
        if platform == "azure" and data.get("azure_client_id"):
            # Services whose only cloud dependency is PostgreSQL need no explicit DB__ENTRA_CLIENT_ID.
            defaults = deep_merge(defaults, {"db": {"entra_client_id": data["azure_client_id"]}})
        return deep_merge(defaults, data)

    def entra_settings(self) -> list[str]:
        """Names of settings that use Entra (managed identity) auth. Override per service."""
        return []

    @model_validator(mode="after")
    def _check_platform(self) -> BaseServiceSettings:
        if self.platform != "azure":
            # PLATFORM=local has no cloud credentials (NoCredentials): fail at start-up, not on first use.
            entra = self.entra_settings() + (["DB__AUTH_MODE"] if self.db.auth_mode == "entra" else [])
            if entra:
                raise ValueError(
                    f"{', '.join(entra)}=entra needs PLATFORM=azure (managed identity, or az login locally)"
                )
            return self
        problems = []
        if self.app_env in ("local", "test"):
            problems.append("APP_ENV must be dev or prod")
        if self.db.auth_mode != "entra":
            problems.append("DB__AUTH_MODE must be entra (managed identity, no passwords)")
        problems += self.platform_violations()
        if problems:
            raise ValueError("PLATFORM=azure refuses local-only configuration: " + "; ".join(problems))
        return self
