"""PLATFORM=local|azure profile for agent-runtime (ADR 0013)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from opstriage_triage.settings import Settings

GRAIL_CREDS = {
    "TRIAGE__CONTEXT__GRAIL__OAUTH_CLIENT_ID": "id",
    "TRIAGE__CONTEXT__GRAIL__OAUTH_CLIENT_SECRET": "s",
}
AZURE = {
    "PLATFORM": "azure",
    "APP_ENV": "dev",
    "DB__DSN": "postgresql://app@db:5432/opstriage",
    "TRIAGE__APPROVAL__MODE": "manual_api",
    "TRIAGE__DECISION__BASE_URL": "https://aif-opstriage-dev-eus2.cognitiveservices.azure.com",
    **GRAIL_CREDS,
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no .env / .env.local from the repo
    for key in list(os.environ):
        if key.startswith(("TRIAGE__", "DB__", "PLATFORM", "APP_ENV", "AZURE_")):
            monkeypatch.delenv(key)


def load(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()  # type: ignore[call-arg]


def test_local_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    s = load(monkeypatch, {"DB__DSN": "postgresql://u:p@db/x"})
    assert s.platform == "local" and s.db.auth_mode == "password"
    t = s.triage
    assert (t.decision.provider, t.decision.auth_mode, t.context.provider, t.summarizer.provider) == (
        "rules",
        "none",
        "fake",
        "template",
    )


def test_azure_profile_applies_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    s = load(monkeypatch, AZURE)
    d = s.triage.decision
    assert (d.provider, d.path, d.model, d.auth_mode) == (
        "systemone",
        "/providers/microsoft/v1/systemone",
        "decision-1",
        "entra",
    )
    assert s.triage.context.provider == "grail" and s.triage.summarizer.provider == "template"
    assert s.db.auth_mode == "entra"


def test_explicit_env_wins_over_the_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    s = load(monkeypatch, AZURE | {"TRIAGE__DECISION__PROVIDER": "rules", "TRIAGE__DECISION__MODEL": "d-2"})
    assert s.triage.decision.provider == "rules" and s.triage.decision.model == "d-2"
    assert s.triage.decision.auth_mode == "entra"  # untouched keys keep the profile default


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"TRIAGE__CONTEXT__PROVIDER": "fake"}, "TRIAGE__CONTEXT__PROVIDER=fake"),
        ({"TRIAGE__DECISION__AUTH_MODE": "none"}, "TRIAGE__DECISION__AUTH_MODE must not be none"),
        (
            {
                "TRIAGE__SUMMARIZER__PROVIDER": "foundry",
                "TRIAGE__SUMMARIZER__BASE_URL": "https://r.openai.azure.com/openai/v1",
                "TRIAGE__SUMMARIZER__DEPLOYMENT": "gpt-main",
            },
            "TRIAGE__SUMMARIZER__AUTH_MODE must not be none",
        ),
        ({"TRIAGE__APPROVAL__MODE": "auto_approve"}, "auto_approve"),
        ({"APP_ENV": "local"}, "APP_ENV must be dev or prod"),
    ],
)
def test_azure_refuses_local_only_strategies(
    monkeypatch: pytest.MonkeyPatch, override: dict[str, str], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        load(monkeypatch, AZURE | override)


def test_azure_foundry_summarizer_with_entra_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    s = load(
        monkeypatch,
        AZURE
        | {
            "TRIAGE__SUMMARIZER__PROVIDER": "foundry",
            "TRIAGE__SUMMARIZER__BASE_URL": "https://r.openai.azure.com/openai/v1",
            "TRIAGE__SUMMARIZER__DEPLOYMENT": "gpt-main",
            "TRIAGE__SUMMARIZER__AUTH_MODE": "entra",
        },
    )
    assert s.triage.summarizer.model == "gpt-main"


def test_grail_needs_oauth_client(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="OAUTH_CLIENT_ID"):
        load(monkeypatch, {"DB__DSN": "postgresql://x", "TRIAGE__CONTEXT__PROVIDER": "grail"})
    s = load(monkeypatch, {"DB__DSN": "postgresql://x", "TRIAGE__CONTEXT__PROVIDER": "grail", **GRAIL_CREDS})
    assert s.triage.context.grail.configured
    assert "storage:logs:read" in s.triage.context.grail.oauth_scopes


def test_foundry_needs_endpoint_and_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="TRIAGE__SUMMARIZER__BASE_URL"):
        load(monkeypatch, {"DB__DSN": "postgresql://x", "TRIAGE__SUMMARIZER__PROVIDER": "foundry"})


@pytest.mark.parametrize(
    "url", ["http://kev:8009", "http://host.docker.internal:18003", "http://localhost:8000"]
)
def test_azure_refuses_a_local_decision_model(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    with pytest.raises(ValidationError, match="points at a local model"):
        load(monkeypatch, AZURE | {"TRIAGE__DECISION__BASE_URL": url})


def test_local_refuses_entra_at_start_up(monkeypatch: pytest.MonkeyPatch) -> None:
    # NoCredentials would fail on the first decision instead; refuse it before the service starts.
    env = {"DB__DSN": "postgresql://u:p@db/x", "TRIAGE__DECISION__PROVIDER": "systemone"}
    with pytest.raises(ValidationError, match="TRIAGE__DECISION__AUTH_MODE=entra needs PLATFORM=azure"):
        load(monkeypatch, env | {"TRIAGE__DECISION__AUTH_MODE": "entra"})
