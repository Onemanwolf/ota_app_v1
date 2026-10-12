"""Mock Teams: `POST /teams` stores bodies, `GET /requests` returns them, `GET /` renders the cards."""

from __future__ import annotations

import html
from collections import deque
from datetime import UTC, datetime
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SINK__", extra="ignore")
    host: str = "0.0.0.0"  # noqa: S104 (container)
    port: int = 8090
    max_requests: int = 200


def build_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="dev-webhook-sink")
    received: deque[dict[str, Any]] = deque(maxlen=settings.max_requests)

    @app.post("/teams", status_code=202)
    async def teams(request: Request) -> dict[str, int]:
        received.append({"received_at": datetime.now(UTC).isoformat(), "body": await request.json()})
        return {"stored": len(received)}

    @app.get("/requests")
    async def requests() -> list[dict[str, Any]]:
        return list(received)

    @app.delete("/requests", status_code=204)
    async def clear() -> None:
        received.clear()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        cards = "".join(_render(item) for item in reversed(received)) or "<p>No cards received yet.</p>"
        return _PAGE.format(count=len(received), cards=cards)

    return app


def _render(item: dict[str, Any]) -> str:
    """Rough Adaptive Card preview: TextBlocks, FactSets and OpenUrl actions."""
    parts: list[str] = []
    for attachment in item["body"].get("attachments", []):
        card = attachment.get("content") or {}
        for block in card.get("body", []):
            if block.get("type") == "TextBlock":
                cls = "title" if block.get("weight") == "Bolder" else "text"
                color = html.escape(str(block.get("color", "")))
                parts.append(f'<div class="{cls} {color}">{html.escape(str(block.get("text", "")))}</div>')
            elif block.get("type") == "FactSet":
                rows = "".join(
                    f"<tr><th>{html.escape(str(f.get('title')))}</th><td>{html.escape(str(f.get('value')))}</td></tr>"
                    for f in block.get("facts", [])
                )
                parts.append(f"<table>{rows}</table>")
        links = " ".join(
            f'<a href="{html.escape(str(a.get("url")))}">{html.escape(str(a.get("title")))}</a>'
            for a in card.get("actions", [])
            if a.get("type") == "Action.OpenUrl"
        )
        if links:
            parts.append(f'<div class="links">{links}</div>')
    received_at = html.escape(item["received_at"])
    return f'<div class="card"><div class="meta">{received_at}</div>{"".join(parts)}</div>'


_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Mock Teams</title>
<meta http-equiv="refresh" content="5">
<style>
body{{font-family:system-ui,sans-serif;background:#f3f2f1;margin:0;padding:16px}}
.card{{background:#fff;border-radius:6px;padding:12px 16px;margin:0 0 12px;max-width:720px;
box-shadow:0 1px 3px #0002}}
.meta{{color:#888;font-size:12px}}.title{{font-weight:600;font-size:16px;margin:6px 0}}
.Attention{{color:#c4314b}}.Warning{{color:#c87d0a}}.Accent{{color:#4f52b2}}
table{{border-collapse:collapse;margin:8px 0}}th{{text-align:left;padding:2px 12px 2px 0;color:#555}}
.links a{{margin-right:12px}}
</style></head><body><h1>Mock Teams channel ({count} cards, newest first)</h1>{cards}</body></html>"""


def main() -> None:
    settings = Settings()
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port)
