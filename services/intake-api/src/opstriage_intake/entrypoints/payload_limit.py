"""ASGI middleware: reject request bodies above a byte limit with 413 (Content-Length and streamed length)."""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class PayloadTooLarge(Exception):
    pass


class PayloadLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self._app, self._max = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self._max:
            await _too_large(send)
            return

        seen = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self._max:
                    raise PayloadTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self._app(scope, limited_receive, tracking_send)
        except PayloadTooLarge:
            if not started:
                await _too_large(send)


async def _too_large(send: Send) -> None:
    body = json.dumps({"detail": "payload too large"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
