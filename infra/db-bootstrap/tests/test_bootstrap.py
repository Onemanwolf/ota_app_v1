"""db-bootstrap: input validation and the SQL it issues (no database needed)."""

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_spec = importlib.util.spec_from_file_location("db_bootstrap", Path(__file__).parents[1] / "bootstrap.py")
assert _spec and _spec.loader
db_bootstrap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db_bootstrap)

OID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"


class FakeConn:
    def __init__(self, log: list[tuple[str, Any]], db: str) -> None:
        self.log, self.db = log, db

    def __enter__(self) -> "FakeConn":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: Any, params: Any = None) -> None:
        self.log.append((self.db, query, params))


def test_roles_then_grants_with_parameters_and_identifiers() -> None:
    log: list[Any] = []
    steps = db_bootstrap.bootstrap(lambda db: FakeConn(log, db), {"id-health-review": OID}, "opstriage")
    assert steps == ["role id-health-review", "grant id-health-review"]
    (db1, q1, p1), (db2, q2, _) = log
    assert db1 == "postgres" and "pgaadauth_create_principal_with_oid" in q1
    assert p1 == {"name": "id-health-review", "oid": OID}
    assert db2 == "opstriage" and "GRANT CONNECT, CREATE ON DATABASE" in repr(q2)


@pytest.mark.parametrize(
    "raw",
    [
        "{}",
        "[]",
        json.dumps({"postgres": OID}),  # only id-* roles
        json.dumps({"id-x; DROP ROLE y": OID}),
        '{"id-x": "not-a-guid"}',
    ],
)
def test_bad_principals_are_refused_before_any_sql(raw: str) -> None:
    with pytest.raises(ValueError):
        db_bootstrap.parse_principals(raw)


def test_principals_are_normalised() -> None:
    assert db_bootstrap.parse_principals(json.dumps({"id-poller": OID.upper()})) == {"id-poller": OID}
