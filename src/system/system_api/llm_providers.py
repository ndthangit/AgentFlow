"""Provider-neutral LLM contracts and the first OpenRouter adapter."""

import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


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

    default_model: str | None = Field(default=None, max_length=200)
    site_url: HttpUrl | None = None
    app_title: str = Field(default="AgentFlow", min_length=1, max_length=120)


class LlmProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["openrouter"]
    api_key: SecretStr = Field(min_length=1, max_length=1000)
    settings: OpenRouterSettings = Field(default_factory=OpenRouterSettings)


class LlmProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=1000)
    settings: OpenRouterSettings
    enabled: bool = True


class LlmProviderView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    kind: str
    settings: OpenRouterSettings
    has_api_key: bool
    enabled: bool
    revision: int
    created_at: datetime
    updated_at: datetime


class CredentialResolver(ABC):
    """Supplies a provider secret without placing it in persisted settings."""

    @abstractmethod
    def resolve(self) -> str:
        raise NotImplementedError


class StaticCredentialResolver(CredentialResolver):
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def resolve(self) -> str:
        return self._api_key


class ProviderError(RuntimeError):
    pass


class ProviderConfigurationError(ProviderError):
    pass


class ProviderRequestError(ProviderError):
    pass


class ProviderAuthenticationError(ProviderRequestError):
    pass


class LlmProviderAdapter(ABC):
    """Stable interface used by AgentFlow regardless of the external provider."""

    kind: str

    @abstractmethod
    async def verify(self) -> None:
        raise NotImplementedError

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
        credential_resolver: CredentialResolver,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._resolver = credential_resolver
        self._client = client

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._resolver.resolve()}"}
        if self.settings.site_url:
            headers["HTTP-Referer"] = str(self.settings.site_url)
            headers["X-OpenRouter-Title"] = self.settings.app_title
        return headers

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            base_url=OPENROUTER_BASE_URL, timeout=60
        )
        try:
            response = await client.request(
                method, path, headers=self._headers(), **kwargs
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise ProviderAuthenticationError(
                    "OpenRouter rejected the API key"
                ) from exc
            raise ProviderRequestError("OpenRouter request failed") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderRequestError("OpenRouter request failed") from exc
        finally:
            if owns_client:
                await client.aclose()

    async def verify(self) -> None:
        payload = await self._request("GET", "/key")
        if not isinstance(payload.get("data"), dict):
            raise ProviderRequestError(
                "OpenRouter returned an invalid verification response"
            )

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
            raise ProviderRequestError(
                "OpenRouter returned an invalid response"
            ) from exc


def create_provider_adapter(
    kind: str,
    settings: dict[str, Any],
    api_key: str,
    client: httpx.AsyncClient | None = None,
) -> LlmProviderAdapter:
    if kind == "openrouter":
        return OpenRouterAdapter(
            OpenRouterSettings.model_validate(settings),
            StaticCredentialResolver(api_key),
            client,
        )
    raise ProviderConfigurationError(f"Unsupported LLM provider: {kind}")
