"""Notification settings under PLATFORM=local|azure (ADR 0013)."""

import pytest
from pydantic import ValidationError

from opstriage_notification.settings import Settings

AZURE_BASE = {
    "PLATFORM": "azure",
    "APP_ENV": "dev",
    "DB__DSN": "postgresql://id-mcp-notification@pg.example:5432/opstriage?sslmode=require",
    "NOTIFICATION__TEAMS__WEBHOOK_URL": "https://prod.example/workflows/abc?sig=SECRET",
    "NOTIFICATION__EMAIL__ACS_ENDPOINT": "https://acs-test.communication.azure.com",
    "NOTIFICATION__EMAIL__FROM_ADDRESS": "DoNotReply@example.test",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for key in list(os.environ):
        if key.upper().startswith(("NOTIFICATION__", "DB__", "PLATFORM", "APP_ENV", "AZURE_CLIENT_ID")):
            monkeypatch.delenv(key)


def load(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_azure_profile_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    s = load(monkeypatch, AZURE_BASE)
    assert s.platform == "azure" and s.db.auth_mode == "entra"
    assert s.notification.email.provider == "acs"
    assert s.notification.teams.provider == "workflows_webhook"


def test_explicit_env_wins_over_azure_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    s = load(
        monkeypatch,
        AZURE_BASE | {"NOTIFICATION__EMAIL__PROVIDER": "log", "NOTIFICATION__TEAMS__PROVIDER": "log"},
    )
    assert s.notification.email.provider == "log" and s.notification.teams.provider == "log"


def test_smtp_refused_on_azure(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="smtp"):
        load(monkeypatch, AZURE_BASE | {"NOTIFICATION__EMAIL__PROVIDER": "smtp"})


def test_azure_profile_needs_teams_webhook_url(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {k: v for k, v in AZURE_BASE.items() if k != "NOTIFICATION__TEAMS__WEBHOOK_URL"}
    with pytest.raises(ValidationError, match="WEBHOOK_URL"):
        load(monkeypatch, env)


@pytest.mark.parametrize("endpoint", [None, "http://acs-test.communication.azure.com"])
def test_acs_requires_https_endpoint(monkeypatch: pytest.MonkeyPatch, endpoint: str | None) -> None:
    env = {k: v for k, v in AZURE_BASE.items() if k != "NOTIFICATION__EMAIL__ACS_ENDPOINT"}
    if endpoint:
        env["NOTIFICATION__EMAIL__ACS_ENDPOINT"] = endpoint
    with pytest.raises(ValidationError, match="ACS_ENDPOINT"):
        load(monkeypatch, env)


def test_acs_requires_from_address(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="FROM_ADDRESS"):
        load(monkeypatch, AZURE_BASE | {"NOTIFICATION__EMAIL__FROM_ADDRESS": " "})


def test_acs_refused_on_local_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="PLATFORM=azure"):
        load(
            monkeypatch,
            {
                "DB__DSN": "postgresql://u:p@localhost:5432/db",
                "NOTIFICATION__EMAIL__PROVIDER": "acs",
                "NOTIFICATION__EMAIL__ACS_ENDPOINT": "https://acs-test.communication.azure.com",
            },
        )


def test_local_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    s = load(monkeypatch, {"DB__DSN": "postgresql://u:p@localhost:5432/db"})
    assert s.platform == "local" and s.db.auth_mode == "password"
    assert s.notification.email.provider == "log" and s.notification.teams.provider == "log"
    smtp = load(monkeypatch, {"NOTIFICATION__EMAIL__PROVIDER": "smtp"})
    assert smtp.notification.email.provider == "smtp"
