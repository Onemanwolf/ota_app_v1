"""PLATFORM profile for the poller (ADR 0013)."""

from typing import Any

import pytest
from pydantic import ValidationError

from opstriage_poller.settings import Settings

AZURE: dict[str, Any] = {
    "platform": "azure",
    "app_env": "dev",
    "db": {"dsn": "postgresql://id-poller@psql/opstriage", "auth_mode": "entra"},
}
POLLER: dict[str, Any] = {"intake_token": "t", "grail": {"oauth_client_id": "id", "oauth_client_secret": "s"}}


def _load(data: dict[str, Any]) -> Settings:
    return Settings(_env_file=None, **data)  # the developer .env must not leak into these tests


def test_azure_defaults_to_grail() -> None:
    assert _load({**AZURE, "poller": POLLER}).poller.source == "grail"


def test_azure_refuses_fixture_source() -> None:
    with pytest.raises(ValidationError, match="POLLER__SOURCE must not be fake"):
        _load({**AZURE, "poller": {**POLLER, "source": "fake"}})


def test_local_keeps_fixtures() -> None:
    s = _load({"db": {"dsn": "postgresql://u:p@h/d"}, "poller": {"intake_token": "t"}})
    assert (s.platform, s.poller.source) == ("local", "fake")
