"""PLATFORM strategy switch (ADR 0013): profile defaults, explicit overrides, Azure refusals, credentials."""

import sys
import types
from typing import Any

import pytest
from pydantic import ValidationError

from opstriage_shared.credentials import (
    SCOPE_COGNITIVE,
    AzureCredentials,
    CredentialsUnavailable,
    NoCredentials,
    StaticCredentials,
    credentials_for,
)
from opstriage_shared.settings import BaseServiceSettings, Platform, deep_merge

DSN = "postgresql://id-app@psql.example:5432/opstriage?sslmode=require"


class Svc(BaseServiceSettings):
    """A service with one strategy, to exercise the hooks."""

    source: str = "fake"

    @classmethod
    def platform_defaults(cls, platform: Platform) -> dict[str, Any]:
        return {"source": "grail"} if platform == "azure" else {}

    def platform_violations(self) -> list[str]:
        return ["SOURCE must not be fake"] if self.source == "fake" else []


def test_local_is_the_default_and_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB__DSN", "postgresql://u:p@h/d")
    s = Svc(_env_file=None)
    assert (s.platform, s.source, s.db.auth_mode, s.telemetry.enabled) == ("local", "fake", "password", False)


def test_azure_profile_applies_shared_and_service_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in {"PLATFORM": "azure", "APP_ENV": "dev", "DB__DSN": DSN, "AZURE_CLIENT_ID": "cid"}.items():
        monkeypatch.setenv(k, v)
    s = Svc(_env_file=None)
    assert (s.source, s.db.auth_mode, s.telemetry.enabled) == ("grail", "entra", True)
    assert s.db.entra_client_id == "cid"  # taken from AZURE_CLIENT_ID unless DB__ENTRA_CLIENT_ID is set


def test_explicit_settings_beat_the_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in {
        "PLATFORM": "azure",
        "APP_ENV": "prod",
        "DB__DSN": DSN,
        "AZURE_CLIENT_ID": "cid",
        "DB__ENTRA_CLIENT_ID": "db-cid",
        "TELEMETRY__ENABLED": "false",
        "SOURCE": "classic",
    }.items():
        monkeypatch.setenv(k, v)
    s = Svc(_env_file=None)
    assert (s.source, s.telemetry.enabled, s.db.entra_client_id) == ("classic", False, "db-cid")


def test_azure_refuses_local_only_configuration() -> None:
    with pytest.raises(ValidationError) as err:
        Svc.model_validate(
            {
                "platform": "azure",
                "app_env": "local",
                "db": {"dsn": DSN, "auth_mode": "password"},
                "source": "fake",
            }
        )
    text = str(err.value)
    assert "APP_ENV must be dev or prod" in text and "DB__AUTH_MODE must be entra" in text
    assert "SOURCE must not be fake" in text  # every problem is listed at once


def test_unknown_platform_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Svc.model_validate({"platform": "aws", "db": {"dsn": DSN}})


def test_deep_merge_override_wins_and_nests() -> None:
    assert deep_merge({"a": {"b": 1, "c": 2}, "d": 1}, {"a": {"b": 9}, "e": 3}) == {
        "a": {"b": 9, "c": 2},
        "d": 1,
        "e": 3,
    }


async def test_local_credentials_refuse_cloud_tokens() -> None:
    creds = credentials_for(Svc.model_validate({"db": {"dsn": DSN}}))
    assert isinstance(creds, NoCredentials)
    with pytest.raises(CredentialsUnavailable):
        await creds.token(SCOPE_COGNITIVE)


async def test_static_credentials_record_scopes() -> None:
    creds = StaticCredentials("t")
    assert await creds.token(SCOPE_COGNITIVE) == "t" and creds.scopes == [SCOPE_COGNITIVE]


async def test_azure_credentials_use_the_user_assigned_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[tuple[str, Any]] = []

    class _Token:
        token = "mi-token"

    class _Cred:
        def __init__(self, client_id: str | None = None) -> None:
            built.append((type(self).__name__, client_id))

        async def get_token(self, scope: str) -> _Token:
            return _Token()

        async def close(self) -> None:
            return None

    class ManagedIdentityCredential(_Cred):
        pass

    class DefaultAzureCredential(_Cred):
        pass

    aio = types.ModuleType("azure.identity.aio")
    aio.ManagedIdentityCredential = ManagedIdentityCredential  # type: ignore[attr-defined]
    aio.DefaultAzureCredential = DefaultAzureCredential  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "azure.identity.aio", aio)

    s = Svc.model_validate(
        {
            "platform": "azure",
            "app_env": "dev",
            "azure_client_id": "cid",
            "db": {"dsn": DSN, "auth_mode": "entra"},  # explicit: the repo .env sets password
            "source": "grail",
        }
    )
    creds = credentials_for(s)
    assert isinstance(creds, AzureCredentials)
    assert await creds.token(SCOPE_COGNITIVE) == "mi-token"
    await creds.close()
    AzureCredentials(None)  # no client id: DefaultAzureCredential (developer laptop with az login)
    assert built == [("ManagedIdentityCredential", "cid"), ("DefaultAzureCredential", None)]


def test_local_refuses_entra_database_auth() -> None:
    with pytest.raises(ValidationError, match="DB__AUTH_MODE=entra needs PLATFORM=azure"):
        Svc(_env_file=None, db={"dsn": DSN, "auth_mode": "entra"})


def test_validation_errors_never_echo_the_input() -> None:
    with pytest.raises(ValidationError) as err:
        Svc(_env_file=None, platform="azure", app_env="local", db={"dsn": "postgresql://u:s3cret@h/d"})
    assert "s3cret" not in str(err.value)
