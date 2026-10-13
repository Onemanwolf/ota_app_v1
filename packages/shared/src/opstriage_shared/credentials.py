"""Cloud credential strategy (ADR 0013). The composition root builds ONE provider per process and passes it to
every adapter that needs a cloud token (PostgreSQL, Foundry, ACS, ARM).

- `PLATFORM=local`: `NoCredentials`. Local adapters use static tokens or none; asking for a cloud token fails
  loudly instead of silently reaching for whatever `az login` session the laptop has.
- `PLATFORM=azure`: `AzureCredentials`, the user-assigned managed identity named by `AZURE_CLIENT_ID`
  (Container Apps sets it per app). Without a client ID it falls back to `DefaultAzureCredential`, which lets
  a developer run the Azure profile from a laptop with `az login`.

azure-identity is imported lazily: it is installed only in images built with `PLATFORM=azure`.
"""

from __future__ import annotations

from typing import Any, Protocol

from opstriage_shared.settings import BaseServiceSettings

# Entra token scopes used by the adapters.
SCOPE_POSTGRES = "https://ossrdbms-aad.database.windows.net/.default"
SCOPE_COGNITIVE = "https://cognitiveservices.azure.com/.default"  # Foundry, Azure OpenAI
SCOPE_COMMUNICATION = "https://communication.azure.com/.default"  # ACS Email
SCOPE_ARM = "https://management.azure.com/.default"  # VM Run Command


class CredentialsUnavailable(RuntimeError):
    """A cloud token was requested on a platform that has no cloud credentials."""


class TokenProvider(Protocol):
    async def token(self, scope: str) -> str: ...

    async def close(self) -> None: ...


class NoCredentials:
    async def token(self, scope: str) -> str:
        raise CredentialsUnavailable(f"no cloud credentials on PLATFORM=local (scope {scope})")

    async def close(self) -> None:
        return None


class StaticCredentials:
    """Fake for tests: returns a fixed token and records the scopes asked for."""

    def __init__(self, value: str = "test-token") -> None:
        self.value, self.scopes = value, list[str]()

    async def token(self, scope: str) -> str:
        self.scopes.append(scope)
        return self.value

    async def close(self) -> None:
        return None


class AzureCredentials:
    def __init__(self, client_id: str | None) -> None:
        from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential

        self.client_id = client_id
        self.credential: Any = (
            ManagedIdentityCredential(client_id=client_id) if client_id else DefaultAzureCredential()
        )

    async def token(self, scope: str) -> str:
        # azure-identity caches tokens per scope and refreshes them before expiry.
        return str((await self.credential.get_token(scope)).token)

    async def close(self) -> None:
        await self.credential.close()


def credentials_for(settings: BaseServiceSettings) -> TokenProvider:
    if settings.platform == "azure":
        return AzureCredentials(settings.azure_client_id)
    return NoCredentials()
