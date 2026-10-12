from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from opstriage_notification.domain.email import OutboundEmail
from opstriage_notification.ports import ChannelDeliveryError


class SmtpEmailSender:
    """Local development (Mailpit). No TLS or auth; settings allow it only with APP_ENV=local."""

    def __init__(self, host: str, port: int) -> None:
        self._host, self._port = host, port

    async def send(self, mail: OutboundEmail) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = mail.sender, ", ".join(mail.to), mail.subject
        msg.set_content(mail.text)
        msg.add_alternative(mail.html, subtype="html")

        def _send() -> None:
            with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
                smtp.send_message(msg)

        try:
            await asyncio.to_thread(_send)
        except (OSError, smtplib.SMTPException) as exc:
            raise ChannelDeliveryError("email", type(exc).__name__) from None
