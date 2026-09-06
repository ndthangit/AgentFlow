"""Provider-neutral LLM contracts and the first OpenRouter adapter."""

import os
import re
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
ENV_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0)


class ChatCompletionResult(BaseModel):
    id: str
    model: str
    message: ChatMessage
    finish_reason: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class ProviderModel(BaseModel):
    id: str
    name: str
    context_length: int | None = None
    pricing: dict[str, str] = Field(default_factory=dict)


class OpenRouterSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    api_key_env: str = "OPENROUTER_API_KEY"
    default_model: str | None = Field(default=None, max_length=200)
    site_url: HttpUrl | None = None
    app_title: str = Field(default="AgentFlow", min_length=1, max_length=120)

    @field_validator("api_key_env")
    @classmethod
    def valid_env_name(cls, value: str) -> str:
        if not ENV_NAME_PATTERN.fullmatch(value):
            raise ValueError("api_key_env must be an uppercase environment variable name")
        return value


class LlmProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["openrouter"]
    settings: OpenRouterSettings = Field(default_factory=OpenRouterSettings)


class LlmProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    settings: OpenRouterSettings
    enabled: bool = True


class LlmProviderView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    kind: str
    settings: dict[str, Any]
    enabled: bool
    revision: int
    created_at: datetime
    updated_at: datetime


class CredentialResolver(ABC):
    """Resolves a secret outside persisted provider configuration."""

    @abstractmethod
    def resolve(self, reference: str) -> str:
        raise NotImplementedError


class EnvironmentCredentialResolver(CredentialResolver):
    def resolve(self, reference: str) -> str:
        value = os.getenv(reference, "").strip()
        if not value:
            raise ProviderConfigurationError(f"Credential environment variable {reference} is not set")
        return value


class ProviderError(RuntimeError):
    pass


class ProviderConfigurationError(ProviderError):
    pass


class ProviderRequestError(ProviderError):
    pass


class LlmProviderAdapter(ABC):
    """Stable interface used by AgentFlow regardless of the external provider."""

    kind: str

    @abstractmethod
    async def list_models(self) -> list[ProviderModel]:
        raise NotImplementedError

    @abstractmethod
    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResult:
        raise NotImplementedError


class OpenRouterAdapter(LlmProviderAdapter):
    kind = "openrouter"

    def __init__(
        self,
        settings: OpenRouterSettings,
        credential_resolver: CredentialResolver | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._resolver = credential_resolver or EnvironmentCredentialResolver()
        self._client = client

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._resolver.resolve(self.settings.api_key_env)}"}
        if self.settings.site_url:
            headers["HTTP-Referer"] = str(self.settings.site_url)
            headers["X-OpenRouter-Title"] = self.settings.app_title
        return headers

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(base_url=OPENROUTER_BASE_URL, timeout=60)
        try:
            response = await client.request(method, path, headers=self._headers(), **kwargs)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderRequestError("OpenRouter request failed") from exc
        finally:
            if owns_client:
                await client.aclose()

    async def list_models(self) -> list[ProviderModel]:
        payload = await self._request("GET", "/models")
        return [
            ProviderModel(
                id=item["id"],
                name=item.get("name", item["id"]),
                context_length=item.get("context_length"),
                pricing=item.get("pricing") or {},
            )
            for item in payload.get("data", [])
        ]

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResult:
        model = request.model or self.settings.default_model
        if not model:
            raise ProviderConfigurationError("A model is required")
        body = request.model_dump(exclude_none=True)
        body["model"] = model
        body["messages"] = [message.model_dump() for message in request.messages]
        payload = await self._request("POST", "/chat/completions", json=body)
        try:
            choice = payload["choices"][0]
            return ChatCompletionResult(
                id=payload["id"],
                model=payload.get("model", model),
                message=ChatMessage.model_validate(choice["message"]),
                finish_reason=choice.get("finish_reason"),
                usage=payload.get("usage") or {},
                raw=payload,
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderRequestError("OpenRouter returned an invalid response") from exc


def create_provider_adapter(
    kind: str,
    settings: dict[str, Any],
    credential_resolver: CredentialResolver | None = None,
    client: httpx.AsyncClient | None = None,
) -> LlmProviderAdapter:
    if kind == "openrouter":
        return OpenRouterAdapter(OpenRouterSettings.model_validate(settings), credential_resolver, client)
    raise ProviderConfigurationError(f"Unsupported LLM provider: {kind}")
