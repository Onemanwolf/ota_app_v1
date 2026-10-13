"""PLATFORM=local|azure profile for mcp-remediation (ADR 0013), registry checks at startup and the shipped
target files."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from opstriage_remediation.adapters.registry import registry as executor_registry
from opstriage_remediation.bootstrap import check_registry_for_platform, load_registry
from opstriage_remediation.domain.targets import ExecutorKind
from opstriage_remediation.settings import Settings
from opstriage_shared.credentials import StaticCredentials

ROOT = Path(__file__).resolve().parents[3]
AZURE: dict[str, Any] = {"platform": "azure", "app_env": "prod", "db": {"dsn": "postgresql://id@db/x"}}
LOCAL: dict[str, Any] = {"db": {"dsn": "postgresql://u:p@db/x"}}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for name in list(os.environ):
        if name.upper().startswith(("REMEDIATION__", "PLATFORM", "APP_ENV", "DB__")):
            monkeypatch.delenv(name)


def settings(**data: Any) -> Settings:
    return Settings(_env_file=None, **data)  # type: ignore[call-arg]


def test_azure_defaults_are_safe() -> None:
    s = settings(**AZURE)
    r = s.remediation
    assert (r.enabled, r.dry_run, r.max_autonomy, r.allow_local_docker) == (
        False,
        True,
        "approval_required",
        False,
    )
    assert "auto_approve" not in r.allowed_approval_modes
    assert s.db.auth_mode == "entra"


def test_explicit_env_wins_on_azure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM", "azure")
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DB__DSN", "postgresql://id@db/x")
    monkeypatch.setenv("REMEDIATION__ENABLED", "true")
    monkeypatch.setenv("REMEDIATION__DRY_RUN", "false")
    s = settings()
    assert s.platform == "azure" and s.remediation.enabled is True and s.remediation.dry_run is False
    assert s.remediation.max_autonomy == "approval_required"  # untouched values keep the profile


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"remediation": {"allow_local_docker": True}}, "PLATFORM=azure refuses.*ALLOW_LOCAL_DOCKER"),
        (
            {"remediation": {"allowed_approval_modes": ["auto_approve", "manual_api"]}},
            "PLATFORM=azure refuses.*auto_approve",
        ),
        ({"app_env": "local"}, "APP_ENV must be dev or prod"),
        ({"db": {"dsn": "postgresql://id@db/x", "auth_mode": "password"}}, "DB__AUTH_MODE"),
    ],
)
def test_azure_violations_raise(override: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        settings(**(AZURE | override))


def test_azure_requires_db_dsn() -> None:
    with pytest.raises(ValidationError, match="dsn"):
        settings(platform="azure", app_env="prod")


def test_local_profile_unchanged() -> None:
    s = settings(
        **LOCAL, remediation={"allow_local_docker": True, "allowed_approval_modes": ["auto_approve"]}
    )
    assert s.platform == "local" and s.db.auth_mode == "password"
    assert s.remediation.allow_local_docker and s.remediation.allowed_approval_modes == ["auto_approve"]
    assert settings(**LOCAL).remediation.enabled is False  # class default, no profile applied


def test_azure_refuses_local_docker_targets_at_startup() -> None:
    registry = load_registry(ROOT / "config" / "targets.example.yaml")  # has a local_docker target
    with pytest.raises(ValueError, match=r"refuses local_docker targets.*demo-orders-api"):
        check_registry_for_platform(registry, settings(**AZURE))
    check_registry_for_platform(registry, settings(**LOCAL))  # local: fine


@pytest.mark.parametrize("env", ["dev", "prod"])
def test_azure_environment_targets_parse_and_are_azure_only(env: str) -> None:
    registry = load_registry(ROOT / "config" / f"targets.{env}.yaml")
    assert registry.targets
    assert registry.executor_kinds() == {ExecutorKind.AZURE_RUN_COMMAND}
    assert all(t.policy.autonomy == "approval_required" for t in registry.targets)
    check_registry_for_platform(registry, settings(**AZURE))


async def test_azure_executor_built_only_when_referenced() -> None:
    creds = StaticCredentials()
    s = settings(**LOCAL)
    assert executor_registry.build_all(s, set(), creds) == {}  # local stack: no Azure executor, no token
    built = executor_registry.build_all(s, {ExecutorKind.AZURE_RUN_COMMAND}, creds)
    executor = built[ExecutorKind.AZURE_RUN_COMMAND]
    assert executor.kind == "azure_run_command" and creds.scopes == []  # no token until a restart
    await executor.close()  # type: ignore[attr-defined]
