from datetime import time, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from opstriage_insights.settings import Settings


def _settings(**insights: Any) -> Settings:
    base: dict[str, Any] = {"db": {"dsn": "postgresql://unused"}, "insights": insights}
    return Settings.model_validate(base)


def test_defaults_match_the_spec() -> None:
    s = _settings()
    assert s.insights.source == "fake"
    assert s.server.port == 8120
    assert s.insights.schedule.daily_at == time(6, 0)
    r = s.insights.retention
    assert (r.report_ttl, r.evidence_max_age, r.evidence_after_report) == (
        timedelta(hours=48),
        timedelta(hours=28),
        timedelta(hours=4),
    )
    assert s.insights.analyst.concurrency == 1
    assert s.insights.tickets.enabled is False and s.insights.web.allow_download is False


def test_iso_8601_durations_parse_like_the_env_file() -> None:
    s = _settings(
        retention={"report_ttl": "PT48H", "evidence_max_age": "PT28H"}, collect={"trend_window": "P30D"}
    )
    assert s.insights.retention.report_ttl == timedelta(hours=48)
    assert s.insights.collect.trend_window == timedelta(days=30)


def test_grail_scopes_include_logs_and_metrics_even_when_other_grail_values_are_set() -> None:
    s = _settings(grail={"oauth_client_id": "id"})
    assert "storage:logs:read" in s.insights.grail.oauth_scopes
    assert "storage:metrics:read" in s.insights.grail.oauth_scopes


def test_grail_source_needs_credentials() -> None:
    with pytest.raises(ValidationError, match="OAUTH_CLIENT_ID"):
        _settings(source="grail")
    assert _settings(source="grail", grail={"oauth_client_id": "id", "oauth_client_secret": "s"})


def test_evidence_window_after_report_cannot_exceed_max_age() -> None:
    with pytest.raises(ValidationError, match="EVIDENCE_AFTER_REPORT"):
        _settings(retention={"evidence_after_report": "PT30H"})


def test_dev_session_secret_is_refused_outside_local() -> None:
    with pytest.raises(ValidationError, match="SESSION_SECRET"):
        Settings.model_validate({"db": {"dsn": "postgresql://unused"}, "app_env": "dev"})
    ok = Settings.model_validate(
        {
            "db": {"dsn": "postgresql://unused"},
            "app_env": "dev",
            "insights": {"web": {"session_secret": "x" * 32}},
        }
    )
    assert ok.app_env == "dev"


AZURE: dict[str, Any] = {
    "platform": "azure",
    "app_env": "prod",
    "db": {"dsn": "postgresql://id-health-review@psql/opstriage", "auth_mode": "entra"},
}


def _azure(**insights: Any) -> Settings:
    defaults = {
        "grail": {"oauth_client_id": "id", "oauth_client_secret": "s"},
        "web": {"session_secret": "x" * 48},
        "analyst": {"base_url": "https://aif-opstriage-prod.openai.azure.com/openai"},
    }
    return Settings(_env_file=None, **{**AZURE, "insights": {**defaults, **insights}})  # no developer .env


def test_azure_profile_selects_grail_foundry_entra_and_baked_config() -> None:
    i = _azure().insights
    assert (i.source, i.config_file.as_posix()) == ("grail", "/app/config/insights.yaml")
    assert (i.analyst.model, i.analyst.auth_mode, i.analyst.extra_body) == ("gpt-main", "entra", {})
    assert i.notify.mcp_url == "http://mcp-notification/mcp"


def test_azure_profile_keeps_explicit_choices() -> None:
    assert _azure(analyst={"provider": "none", "base_url": "https://x"}).insights.analyst.provider == "none"


@pytest.mark.parametrize(
    ("insights", "message"),
    [
        ({"source": "fake"}, "INSIGHTS__SOURCE must be grail"),
        ({"guard": {"provider": "keyword"}}, "keyword is an offline fake"),
        ({"analyst": {"auth_mode": "none", "base_url": "https://x"}}, "AUTH_MODE must not be none"),
        ({"analyst": {"base_url": "http://host.docker.internal:18001"}}, "local Spark tunnel"),
    ],
)
def test_azure_refuses_local_strategies(insights: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _azure(**insights)


def test_local_refuses_an_entra_analyst() -> None:
    with pytest.raises(ValidationError, match="INSIGHTS__ANALYST__AUTH_MODE=entra needs PLATFORM=azure"):
        Settings(
            _env_file=None, db={"dsn": "postgresql://unused"}, insights={"analyst": {"auth_mode": "entra"}}
        )
