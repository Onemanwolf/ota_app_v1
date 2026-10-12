from __future__ import annotations

import hmac
from collections.abc import Callable, Sequence
from typing import Annotated

from fastapi import Header, HTTPException, status
from pydantic import SecretStr


def token_matches(authorization: str | None, accepted: Sequence[bytes]) -> bool:
    """Constant-time check of an `Authorization: Bearer <token>` header against a rotatable token list."""
    scheme, _, presented = (authorization or "").partition(" ")
    return scheme.lower() == "bearer" and any(hmac.compare_digest(presented.encode(), a) for a in accepted)


def bearer_guard(tokens: Sequence[SecretStr]) -> Callable[[str | None], None]:
    """FastAPI dependency. 503 when no tokens are configured (fail closed), 401 on a bad token."""
    accepted = [t.get_secret_value().encode() for t in tokens]

    def _check(authorization: Annotated[str | None, Header()] = None) -> None:
        if not accepted:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "no tokens configured")
        if not token_matches(authorization, accepted):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")

    return _check
