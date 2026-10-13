"""Composition root for mcp-notification."""

from __future__ import annotations

import time
from pathlib import Path

import httpx2

from opstriage_notification.adapters.acs_email import AcsEmailSender
from opstriage_notification.adapters.deliveries import PostgresDeliveryStore
from opstriage_notification.adapters.log_senders import LogEmailSender, LogTeamsSender
from opstriage_notification.adapters.smtp_email import SmtpEmailSender
from opstriage_notification.adapters.teams_workflows import WorkflowsWebhookTeamsSender
from opstriage_notification.application.send_notification import SendNotification
from opstriage_notification.domain.rate_limit import TokenBucketLimiter
from opstriage_notification.ports import EmailSender, TeamsSender
from opstriage_notification.settings import Settings
from opstriage_shared.credentials import TokenProvider, credentials_for
from opstriage_shared.db import Pool, migrate, open_pool

MIGRATIONS = Path(__file__).resolve().parent / "migrations"  # shipped inside the package


class Container:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: Pool | None = None
        self._http: httpx2.AsyncClient | None = None
        self._creds: TokenProvider | None = None
        self._send: SendNotification | None = None

    @property
    def send_notification(self) -> SendNotification:
        if self._send is None:
            raise RuntimeError("container not started")
        return self._send

    async def start(self) -> None:
        n = self._settings.notification
        # One credential provider per process (ADR 0013), shared by the DB pool and the ACS sender.
        self._creds = creds = credentials_for(self._settings)
        self._pool = await open_pool(self._settings.db, credential=getattr(creds, "credential", None))
        await migrate(self._pool, "notification", MIGRATIONS)
        self._http = httpx2.AsyncClient(timeout=httpx2.Timeout(15))

        teams: TeamsSender = LogTeamsSender()
        if n.teams.provider == "workflows_webhook" and n.teams.webhook_url is not None:
            teams = WorkflowsWebhookTeamsSender(self._http, n.teams.webhook_url)
        email: EmailSender = LogEmailSender()
        if n.email.provider == "smtp":
            email = SmtpEmailSender(n.email.smtp_host, n.email.smtp_port)
        elif n.email.provider == "acs" and n.email.acs_endpoint is not None:
            email = AcsEmailSender(
                self._http, n.email.acs_endpoint, creds, api_version=n.email.acs_api_version
            )

        self._send = SendNotification(
            teams,
            email,
            PostgresDeliveryStore(self._pool),
            TokenBucketLimiter(n.rate_limit_per_minute, time.monotonic),
            card_version=n.teams.card_version,
            sender=n.email.from_address,
            default_recipients=tuple(n.email.default_recipients),
        )

    async def stop(self) -> None:
        if self._http is not None:
            await self._http.aclose()
        if self._pool is not None:
            await self._pool.close()
        if self._creds is not None:
            await self._creds.close()
