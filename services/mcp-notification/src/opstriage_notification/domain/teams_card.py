from __future__ import annotations

from typing import Any

from opstriage_contracts.tools import NotificationRequest, Severity

_COLOR = {Severity.INFO: "Accent", Severity.WARNING: "Warning", Severity.CRITICAL: "Attention"}
MAX_SUMMARY = 3_000


def build_teams_message(req: NotificationRequest, card_version: str = "1.5") -> dict[str, Any]:
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": req.title,
            "weight": "Bolder",
            "size": "Medium",
            "color": _COLOR[req.severity],
            "wrap": True,
        },
        {"type": "TextBlock", "text": req.summary[:MAX_SUMMARY], "wrap": True},
    ]
    if req.facts:
        body.append({"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in req.facts.items()]})
    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": card_version,
        "body": body,
        "actions": [
            {"type": "Action.OpenUrl", "title": label, "url": url} for label, url in req.links.items()
        ],
    }
    return {
        "type": "message",
        "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}
        ],
    }
