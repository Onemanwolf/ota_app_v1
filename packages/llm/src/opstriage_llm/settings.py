"""One LLM endpoint, fully described by settings, so switching models is configuration, not code."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, model_validator

ResponseFormat = Literal["json_schema", "json_object", "none"]
AuthMode = Literal["none", "bearer", "api_key", "entra"]


class LlmEndpoint(BaseModel):
    """An OpenAI-compatible chat completions endpoint.

    Works with vLLM, Ollama, LM Studio, llama.cpp server, OpenAI, Azure OpenAI (v1 API, `api_key` auth with
    header `api-key`, or `entra`) and OpenAI-compatible gateways (LiteLLM and similar).

    response_format: `json_schema` (strict structured output: vLLM, OpenAI, Azure OpenAI), `json_object` (JSON
    mode only) or `none` (plain text; the schema goes into the system prompt). Output is validated in code in
    every mode, so a weaker mode costs reliability, never safety.
    extra_body: provider-specific request fields merged into the body, e.g. Qwen on vLLM:
    {"chat_template_kwargs": {"enable_thinking": false}}.
    """

    base_url: str = "http://localhost:8000"
    path: str = "/v1/chat/completions"
    model: str = ""
    auth_mode: AuthMode = "none"
    api_key: SecretStr | None = None
    api_key_header: str = "api-key"  # auth_mode=api_key (Azure OpenAI's header name)
    entra_scope: str = "https://cognitiveservices.azure.com/.default"
    response_format: ResponseFormat = "json_schema"
    extra_body: dict[str, Any] = Field(default_factory=dict)
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int = Field(default=1_000, ge=16, le=32_000)
    timeout_seconds: float = Field(default=60, gt=0)
    attempts: int = Field(default=2, ge=1, le=5)  # transport errors, 429 and 5xx
    repair_attempts: int = Field(default=1, ge=0, le=3)  # re-ask once when the output fails validation

    @property
    def url(self) -> str:
        if not self.path:
            return self.base_url
        return f"{self.base_url.rstrip('/')}/{self.path.lstrip('/')}"

    @model_validator(mode="after")
    def _check(self) -> LlmEndpoint:
        if self.auth_mode in ("bearer", "api_key") and not (self.api_key and self.api_key.get_secret_value()):
            raise ValueError(f"auth_mode={self.auth_mode} needs api_key")
        reserved = {"model", "messages", "response_format", "temperature", "max_tokens"} & set(
            self.extra_body
        )
        if reserved:
            raise ValueError(f"extra_body must not override {sorted(reserved)}")
        return self
