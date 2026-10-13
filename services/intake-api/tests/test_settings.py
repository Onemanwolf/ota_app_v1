"""PLATFORM profile for intake-api (ADR 0013)."""

from typing import Any

import pytest
from pydantic import ValidationError

from opstriage_intake.settings import Settings

AZURE: dict[str, Any] = {
    "platform": "azure",
    "app_env": "prod",
    "db": {"dsn": "postgresql://id-intake-api@psql/opstriage", "auth_mode": "entra"},
}


def _load(data: dict[str, Any]) -> Settings:
    return Settings(_env_file=None, **data)  # the developer .env must not leak into these tests


def test_azure_turns_dev_endpoints_off() -> None:
    s = _load(AZURE)
    assert s.intake.enable_dev_endpoints is False and s.dev_endpoints_enabled is False


def test_azure_refuses_dev_endpoints() -> None:
    with pytest.raises(ValidationError, match="ENABLE_DEV_ENDPOINTS must be false"):
        _load({**AZURE, "intake": {"enable_dev_endpoints": True}})
