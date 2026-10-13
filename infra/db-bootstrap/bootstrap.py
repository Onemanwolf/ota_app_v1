"""Create one PostgreSQL role per app managed identity (OTA-AZ-001 Section 8).

Runs as the manually triggered Container Apps Job `db-bootstrap`, inside the VNet, under `id-db-bootstrap`
(the server's Entra administrator). Idempotent: existing roles are left alone, grants are re-applied.

Environment:
  AZURE_CLIENT_ID   client ID of id-db-bootstrap (user-assigned managed identity)
  PGHOST            server FQDN
  APP_PRINCIPALS    JSON object: role name -> Entra object (principal) ID, e.g. {"id-intake-api": "<oid>"}
  PGDATABASE        application database (default: opstriage)
  BOOTSTRAP_ROLE    the administrator's role name (default: id-db-bootstrap)
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable, Iterable
from typing import Any

SCOPE = "https://ossrdbms-aad.database.windows.net/.default"
ROLE_NAME = re.compile(r"^id-[a-z0-9-]{1,60}$")
OBJECT_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

CREATE_ROLE = """
SELECT pg_catalog.pgaadauth_create_principal_with_oid(%(name)s, %(oid)s, 'service', false, false)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %(name)s)
"""


def parse_principals(raw: str) -> dict[str, str]:
    """Validate before touching the database: names and IDs end up in SQL (as parameters or identifiers)."""
    data = json.loads(raw)
    if not isinstance(data, dict) or not data:
        raise ValueError("APP_PRINCIPALS must be a non-empty JSON object")
    out: dict[str, str] = {}
    for name, oid in data.items():
        if not isinstance(name, str) or not ROLE_NAME.match(name):
            raise ValueError(f"invalid role name: {name!r}")
        if not isinstance(oid, str) or not OBJECT_ID.match(oid.lower()):
            raise ValueError(f"invalid object id for {name}")
        out[name] = oid.lower()
    return out


def bootstrap(connect: Callable[[str], Any], principals: dict[str, str], database: str) -> list[str]:
    """`connect(dbname)` returns an autocommit psycopg connection (a context manager). Returns the steps."""
    from psycopg import sql

    steps: list[str] = []
    with connect("postgres") as conn:
        for name, oid in principals.items():
            conn.execute(CREATE_ROLE, {"name": name, "oid": oid})
            steps.append(f"role {name}")
    with connect(database) as conn:
        for name in principals:
            # Each service creates and owns its schema on first start, so it needs CREATE on the database.
            conn.execute(
                sql.SQL("GRANT CONNECT, CREATE ON DATABASE {} TO {}").format(
                    sql.Identifier(database), sql.Identifier(name)
                )
            )
            steps.append(f"grant {name}")
    return steps


def main(argv: Iterable[str] = ()) -> int:
    import psycopg
    from azure.identity import ManagedIdentityCredential

    principals = parse_principals(os.environ["APP_PRINCIPALS"])
    database = os.environ.get("PGDATABASE", "opstriage")
    credential = ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"])

    def connect(dbname: str) -> Any:
        token = credential.get_token(SCOPE).token  # fresh per connection; never logged
        return psycopg.connect(
            host=os.environ["PGHOST"],
            dbname=dbname,
            user=os.environ.get("BOOTSTRAP_ROLE", "id-db-bootstrap"),
            password=token,
            sslmode="require",
            autocommit=True,
        )

    steps = bootstrap(connect, principals, database)
    print(json.dumps({"event": "db_bootstrap_done", "steps": len(steps), "roles": sorted(principals)}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
