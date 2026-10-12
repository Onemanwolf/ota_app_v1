from __future__ import annotations

from typing import Any

import structlog

from opstriage_notification.domain.email import OutboundEmail

log = structlog.get_logger()


class LogTeamsSender:
    async def send(self, message: dict[str, Any]) -> None:
        card = message["attachments"][0]["content"]
        log.info("teams_message_logged", title=card["body"][0]["text"])


class LogEmailSender:
    async def send(self, mail: OutboundEmail) -> None:
        log.info("email_logged", subject=mail.subject, recipients=len(mail.to))
