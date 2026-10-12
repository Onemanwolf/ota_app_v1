import pytest
from pydantic import ValidationError

from opstriage_shared.settings import BaseServiceSettings


def test_nested_env_vars_use_double_underscore(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB__DSN", "postgresql://u:p@h:5432/d")
    monkeypatch.setenv("DB__POOL_MAX_SIZE", "3")
    monkeypatch.setenv("SOME_OTHER_CONTEXT__VALUE", "ignored")
    s = BaseServiceSettings(_env_file=None)
    assert s.db.pool_max_size == 3
    assert s.db.dsn.get_secret_value() == "postgresql://u:p@h:5432/d"
    assert "p@h" not in repr(s)  # secrets are masked


def test_missing_dsn_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DB__DSN", raising=False)
    with pytest.raises(ValidationError):
        BaseServiceSettings(_env_file=None)
