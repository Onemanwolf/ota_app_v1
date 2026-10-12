from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from opstriage_shared.settings import BaseServiceSettings, ServiceAuthSettings


class TicketingOptions(BaseModel):
    provider: Literal["fake"] = "fake"  # a real system (ServiceNow, Jira, Azure DevOps) adds an adapter
    public_base_url: str = "http://localhost:8102"


class TicketingServerSettings(BaseModel):
    host: str = "0.0.0.0"  # noqa: S104 (container)
    port: int = 8102
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost:*", "mcp-ticketing:*"])


class Settings(BaseServiceSettings):
    ticketing: TicketingOptions = TicketingOptions()
    server: TicketingServerSettings = TicketingServerSettings()
    auth: ServiceAuthSettings = ServiceAuthSettings()
