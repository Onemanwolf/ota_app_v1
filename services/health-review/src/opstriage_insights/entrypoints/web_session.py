"""Browser sessions for the web view (OTA-APP-002 7.1): a signed, expiring cookie with a CSRF token.

Cookie value: base64url(JSON {sub, exp, csrf}) "." base64url(HMAC-SHA256(secret, payload)). The secret is
INSIGHTS__WEB__SESSION_SECRET. Behind an authenticating proxy, a verified identity header is accepted instead,
but only from the configured proxy addresses; its CSRF token is derived from the subject and the day.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

COOKIE = "ota_session"


@dataclass(frozen=True, slots=True)
class Session:
    subject: str
    csrf: str
    expires: datetime | None  # None for proxy identities (the proxy owns the session)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SessionManager:
    def __init__(
        self,
        secret: str,
        ttl: timedelta,
        *,
        proxy_header: str = "",
        proxy_addresses: Sequence[str] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._key = hashlib.sha256(b"ota-web-session:" + secret.encode()).digest()
        self._ttl, self._clock = ttl, clock
        self._proxy_header, self._proxy_addresses = proxy_header.lower(), frozenset(proxy_addresses)

    @property
    def ttl(self) -> timedelta:
        return self._ttl

    def _sign(self, payload: bytes) -> str:
        return _b64(hmac.new(self._key, payload, hashlib.sha256).digest())

    def issue(self, subject: str) -> tuple[str, Session]:
        expires = self._clock() + self._ttl
        session = Session(subject, secrets.token_urlsafe(24), expires)
        payload = json.dumps(
            {"sub": subject, "exp": int(expires.timestamp()), "csrf": session.csrf}, separators=(",", ":")
        ).encode()
        return f"{_b64(payload)}.{self._sign(payload)}", session

    def read(self, cookie: str | None) -> Session | None:
        if not cookie or cookie.count(".") != 1:
            return None
        body, signature = cookie.split(".")
        try:
            payload = _unb64(body)
        except ValueError:
            return None
        if not hmac.compare_digest(signature, self._sign(payload)):
            return None
        try:
            data = json.loads(payload)
            expires = datetime.fromtimestamp(int(data["exp"]), UTC)
            subject, csrf = str(data["sub"]), str(data["csrf"])
        except (ValueError, KeyError, TypeError):
            return None
        if expires <= self._clock():
            return None
        return Session(subject, csrf, expires)

    def from_proxy(self, headers: dict[str, str], client_host: str | None) -> Session | None:
        """The proxy's identity header, only when the request comes from a configured proxy address."""
        if not self._proxy_header or client_host not in self._proxy_addresses:
            return None
        subject = headers.get(self._proxy_header, "").strip()
        if not subject:
            return None
        day = self._clock().strftime("%Y-%m-%d")
        return Session(subject, self._sign(f"csrf:{subject}:{day}".encode())[:32], None)

    @staticmethod
    def csrf_ok(session: Session, presented: str | None) -> bool:
        return bool(presented) and hmac.compare_digest(session.csrf, presented or "")
