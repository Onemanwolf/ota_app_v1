"""Auth header factories for the decision model; implemented once in opstriage_llm for every service."""

from opstriage_llm.auth import COGNITIVE_SCOPE, AuthHeader, entra, no_auth, static_bearer, static_header

__all__ = ["COGNITIVE_SCOPE", "AuthHeader", "entra", "no_auth", "static_bearer", "static_header"]
