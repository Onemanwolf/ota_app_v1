"""Auth header factories. The composition root picks one from LlmEndpoint.auth_mode."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from opstriage_llm.settings import LlmEndpoint

AuthHeader = Callable[[], Awaitable[dict[str, str]]]
TokenSource = Callable[[str], Awaitable[str]]  # scope -> bearer token (opstriage_shared TokenProvider.token)
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


def no_auth() -> AuthHeader:
    async def _h() -> dict[str, str]:
        return {}

    return _h


def static_bearer(key: str) -> AuthHeader:
    async def _h() -> dict[str, str]:
        return {"Authorization": f"Bearer {key}"}

    return _h


def static_header(name: str, value: str) -> AuthHeader:
    async def _h() -> dict[str, str]:
        return {name: value}

    return _h


def entra(scope: str = COGNITIVE_SCOPE, tokens: TokenSource | None = None) -> AuthHeader:
    """Entra ID bearer tokens (Azure OpenAI, Foundry).

    `tokens` is the process's shared credential (ADR 0013); without it, DefaultAzureCredential
    is used. azure-identity is imported lazily: install the `entra` extra; local stacks never need it."""
    if tokens is not None:
        source = tokens

        async def _shared() -> dict[str, str]:
            return {"Authorization": f"Bearer {await source(scope)}"}

        return _shared

    from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

    provider = get_bearer_token_provider(DefaultAzureCredential(), scope)  # caches and refreshes

    async def _h() -> dict[str, str]:
        return {"Authorization": f"Bearer {await provider()}"}

    return _h


def auth_for(endpoint: LlmEndpoint, tokens: TokenSource | None = None) -> AuthHeader:
    key = endpoint.api_key.get_secret_value() if endpoint.api_key else ""
    match endpoint.auth_mode:
        case "bearer":
            return static_bearer(key)
        case "api_key":
            return static_header(endpoint.api_key_header, key)
        case "entra":
            return entra(endpoint.entra_scope, tokens)
        case _:
            return no_auth()
