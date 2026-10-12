from __future__ import annotations

import structlog

from opstriage_contracts.tools import ChannelOutcome, NotificationRequest, NotificationResult
from opstriage_notification.domain.email import render_email
from opstriage_notification.domain.teams_card import build_teams_message
from opstriage_notification.ports import (
    ChannelDeliveryError,
    DeliveryStore,
    EmailSender,
    RateLimiter,
    TeamsSender,
)

log = structlog.get_logger()


class SendNotification:
    """Idempotent per key. Channels are sent independently: one failing never blocks the other."""

    def __init__(
        self,
        teams: TeamsSender,
        email: EmailSender,
        store: DeliveryStore,
        limiter: RateLimiter,
        *,
        card_version: str,
        sender: str,
        default_recipients: tuple[str, ...],
    ) -> None:
        self._teams, self._email, self._store, self._limiter = teams, email, store, limiter
        self._card_version, self._sender, self._default_recipients = card_version, sender, default_recipients

    async def __call__(self, req: NotificationRequest) -> NotificationResult:
        existing = await self._store.get(req.idempotency_key)
        if existing is not None:
            return existing.model_copy(update={"duplicate": True})

        outcomes = []
        for channel in dict.fromkeys(req.channels):  # de-duplicate, keep order
            outcomes.append(await self._deliver(channel, req))
        result = NotificationResult(idempotency_key=req.idempotency_key, outcomes=tuple(outcomes))
        if not await self._store.save(
            result, kind=req.kind.value, severity=req.severity.value, title=req.title
        ):
            stored = await self._store.get(req.idempotency_key)
            if stored is not None:
                return stored.model_copy(update={"duplicate": True})
        log.info(
            "notification_sent",
            kind=req.kind.value,
            idempotency_key=req.idempotency_key,
            outcomes={o.channel: o.delivered for o in outcomes},
        )
        return result

    async def _deliver(self, channel: str, req: NotificationRequest) -> ChannelOutcome:
        if channel == "email" and not (req.email_recipients or self._default_recipients):
            return ChannelOutcome(channel="email", delivered=False, detail="no recipients configured")
        if not self._limiter.try_acquire(channel):
            return ChannelOutcome(channel=channel, delivered=False, detail="rate limited")
        try:
            if channel == "teams":
                await self._teams.send(build_teams_message(req, self._card_version))
                return ChannelOutcome(channel="teams", delivered=True)
            await self._email.send(render_email(req, self._sender, self._default_recipients))
            return ChannelOutcome(channel="email", delivered=True)
        except ChannelDeliveryError as exc:
            log.warning("channel_delivery_failed", channel=channel, detail=exc.detail)
            return ChannelOutcome(channel=channel, delivered=False, detail=exc.detail)
