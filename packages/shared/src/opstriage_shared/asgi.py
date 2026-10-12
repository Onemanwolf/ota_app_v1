"""Pure ASGI middleware for services that are not FastAPI apps (the MCP servers)."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

from pydantic import SecretStr
from starlette.types import ASGIApp, Receive, Scope, Send

from opstriage_shared.auth import token_matches


class BearerTokenMiddleware:
    def __init__(self, app: ASGIApp, tokens: Sequence[SecretStr], exempt_paths: Iterable[str] = ()) -> None:
        self._app = app
        self._accepted = [t.get_secret_value().encode() for t in tokens]
        self._exempt = frozenset(exempt_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self._exempt:
            await self._app(scope, receive, send)
            return
        if not self._accepted:
            await _reject(send, 503, "no tokens configured")
            return
        headers = dict(scope.get("headers") or [])
        authorization = headers.get(b"authorization", b"").decode("latin-1") or None
        if not token_matches(authorization, self._accepted):
            await _reject(send, 401, "invalid token")
            return
        await self._app(scope, receive, send)


async def _reject(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
