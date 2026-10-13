"""Model-agnostic LLM access. Services depend on this package, never on a specific model or vendor SDK."""

from opstriage_llm.auth import AuthHeader, auth_for
from opstriage_llm.chat import ChatJsonClient, ChatResult, LlmInvalidOutput, LlmUnavailable
from opstriage_llm.settings import LlmEndpoint

__all__ = [
    "AuthHeader",
    "ChatJsonClient",
    "ChatResult",
    "LlmEndpoint",
    "LlmInvalidOutput",
    "LlmUnavailable",
    "auth_for",
]
