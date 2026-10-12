"""Auth header factories for the decision model (the composition root picks one from AUTH_MODE)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

AuthHeader = Callable[[], Awaitable[dict[str, str]]]
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


def no_auth() -> AuthHeader:
    async def _h() -> dict[str, str]:
        return {}

    return _h


def static_bearer(key: str) -> AuthHeader:
    async def _h() -> dict[str, str]:
        return {"Authorization": f"Bearer {key}"}

    return _h


def entra() -> AuthHeader:
    """Microsoft-Decision-1 in Foundry. azure-identity is imported lazily: local stacks never install it."""
    from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

    provider = get_bearer_token_provider(DefaultAzureCredential(), COGNITIVE_SCOPE)  # caches and refreshes

    async def _h() -> dict[str, str]:
        return {"Authorization": f"Bearer {await provider()}"}

    return _h
