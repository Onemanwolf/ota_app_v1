from __future__ import annotations

import html
from dataclasses import dataclass

from opstriage_contracts.tools import NotificationRequest


@dataclass(frozen=True, slots=True)
class OutboundEmail:
    sender: str
    to: tuple[str, ...]
    subject: str
    text: str
    html: str


def render_email(req: NotificationRequest, sender: str, default_recipients: tuple[str, ...]) -> OutboundEmail:
    facts_text = "\n".join(f"{k}: {v}" for k, v in req.facts.items())
    links_text = "\n".join(f"{k}: {v}" for k, v in req.links.items())
    text = "\n\n".join(part for part in (req.summary, facts_text, links_text) if part)
    rows = "".join(
        f"<tr><th align='left'>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>"
        for k, v in req.facts.items()
    )
    links = " | ".join(f"<a href='{html.escape(v)}'>{html.escape(k)}</a>" for k, v in req.links.items())
    body = (
        f"<h2>{html.escape(req.title)}</h2><p>{html.escape(req.summary)}</p>"
        f"{f'<table>{rows}</table>' if rows else ''}{f'<p>{links}</p>' if links else ''}"
    )
    return OutboundEmail(
        sender=sender,
        to=req.email_recipients or default_recipients,
        subject=f"[{req.severity.value.upper()}] {req.title}",
        text=text,
        html=body,
    )
