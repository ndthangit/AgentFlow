"""Provider-neutral LLM contracts and the first OpenRouter adapter."""

import uuid
from abc import ABC, abstractmethod
from asyncio import sleep
from datetime import datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, field_validator

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
    usage: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class ProviderModel(BaseModel):
    id: str
    name: str
    context_length: int | None = None
    pricing: dict[str, Any] = Field(default_factory=dict)


class OpenRouterSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    default_model: str | None = Field(default=None, max_length=200)
    selected_models: list[str] = Field(default_factory=list, max_length=100)
    site_url: HttpUrl | None = None
    app_title: str = Field(default="AgentFlow", min_length=1, max_length=120)

    @field_validator("selected_models")
    @classmethod
    def normalize_selected_models(cls, models: list[str]) -> list[str]:
        normalized: list[str] = []
        for model in models:
            model_id = model.strip()
            if not model_id or len(model_id) > 200:
                raise ValueError("Selected model IDs must contain 1-200 characters")
            if model_id not in normalized:
                normalized.append(model_id)
        return normalized


class LlmProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    api_key: SecretStr = Field(min_length=1, max_length=1000)


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


class ProviderModelTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    model: str = Field(min_length=1, max_length=200)


class ProviderModelTestResult(BaseModel):
    model: str
    ok: bool
    latency_ms: int
    response: str


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


class ProviderUpstreamError(ProviderRequestError):
    """A provider-side failure with a safe status code for API error mapping."""

    def __init__(self, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__("OpenRouter upstream request failed")


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
            raise ProviderUpstreamError(exc.response.status_code) from exc
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
                pricing=(
                    item.get("pricing")
                    if isinstance(item.get("pricing"), dict)
                    else {}
                ),
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
        invalid_reason = "invalid response envelope"
        transient_statuses = {408, 429, 500, 502, 503, 529}
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                payload = await self._request("POST", "/chat/completions", json=body)
            except ProviderUpstreamError as exc:
                if attempt < max_attempts - 1 and exc.status_code in transient_statuses:
                    await sleep(0.5 * (attempt + 1))
                    continue
                raise
            error = payload.get("error")
            if isinstance(error, dict):
                raw_code = error.get("code")
                status_code = (
                    raw_code
                    if isinstance(raw_code, int)
                    else int(raw_code)
                    if isinstance(raw_code, str) and raw_code.isdigit()
                    else None
                )
                if attempt < max_attempts - 1 and status_code in transient_statuses:
                    await sleep(0.5 * (attempt + 1))
                    continue
                raise ProviderUpstreamError(status_code)
            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices:
                invalid_reason = "response contained no completion choices"
                continue
            choice = choices[0]
            message = choice.get("message") if isinstance(choice, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str):
                invalid_reason = "completion message contained no text content"
                continue
            return ChatCompletionResult(
                id=str(payload.get("id") or ""),
                model=str(payload.get("model") or model),
                message=ChatMessage(role="assistant", content=content),
                finish_reason=choice.get("finish_reason"),
                usage=payload.get("usage")
                if isinstance(payload.get("usage"), dict)
                else {},
                raw=payload,
            )
        raise ProviderRequestError(
            f"OpenRouter {invalid_reason} after {max_attempts} attempts"
        )


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
