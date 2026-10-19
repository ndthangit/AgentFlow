"""Provider-neutral LLM contracts and HTTP adapters."""

import os
import uuid
from abc import ABC, abstractmethod
from asyncio import sleep
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, field_validator

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
ANTHROPIC_VERSION = "2023-06-01"

ProviderKind = Literal["openrouter", "openai", "anthropic", "nvidia"]


@dataclass(frozen=True)
class ProviderSpec:
    kind: str
    api_style: Literal["openai_chat", "anthropic_messages"]
    default_base_url: str
    override_environment: str
    runtime_provider: str


PROVIDER_REGISTRY: dict[str, ProviderSpec] = {
    "openrouter": ProviderSpec(
        kind="openrouter",
        api_style="openai_chat",
        default_base_url=OPENROUTER_BASE_URL,
        override_environment="OPENROUTER_BASE_URL_OVERRIDE",
        runtime_provider="openrouter",
    ),
    "openai": ProviderSpec(
        kind="openai",
        api_style="openai_chat",
        default_base_url=OPENAI_BASE_URL,
        override_environment="OPENAI_BASE_URL_OVERRIDE",
        runtime_provider="openai",
    ),
    "anthropic": ProviderSpec(
        kind="anthropic",
        api_style="anthropic_messages",
        default_base_url=ANTHROPIC_BASE_URL,
        override_environment="ANTHROPIC_BASE_URL_OVERRIDE",
        runtime_provider="anthropic",
    ),
    "nvidia": ProviderSpec(
        kind="nvidia",
        api_style="openai_chat",
        default_base_url=NVIDIA_BASE_URL,
        override_environment="NVIDIA_BASE_URL_OVERRIDE",
        runtime_provider="nvidia",
    ),
}


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


class ProviderSettings(BaseModel):
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


# Backwards-compatible import while callers migrate to the provider-neutral name.
OpenRouterSettings = ProviderSettings


class LlmProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=160)
    kind: ProviderKind = "openrouter"
    api_key: SecretStr = Field(min_length=1, max_length=1000)


class LlmProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=1000)
    settings: ProviderSettings
    enabled: bool = True


class LlmProviderView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    kind: str
    settings: ProviderSettings
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
        super().__init__("LLM provider upstream request failed")


def _environment_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _validated_base_url(value: str, environment_name: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderConfigurationError(
            f"{environment_name} must be an absolute HTTP(S) URL without credentials, query or fragment"
        )
    return value.rstrip("/")


def provider_spec(kind: str) -> ProviderSpec:
    try:
        return PROVIDER_REGISTRY[kind]
    except KeyError:
        raise ProviderConfigurationError(f"Unsupported LLM provider: {kind}") from None


def resolve_provider_base_url(kind: str) -> str:
    """Resolve a trusted endpoint; user-controlled settings never supply this URL."""

    spec = provider_spec(kind)
    if not _environment_flag("ALLOW_PROVIDER_ENDPOINT_OVERRIDES"):
        return spec.default_base_url
    deployment = os.getenv("DEPLOYMENT_ENVIRONMENT", "local").strip().lower()
    if deployment in {"prod", "production"}:
        raise ProviderConfigurationError(
            "Provider endpoint overrides are disabled in production"
        )
    override = os.getenv(spec.override_environment, "").strip()
    if not override:
        return spec.default_base_url
    return _validated_base_url(override, spec.override_environment)


def validate_provider_endpoint_configuration() -> None:
    """Fail process startup when trusted endpoint override configuration is unsafe."""

    if not _environment_flag("ALLOW_PROVIDER_ENDPOINT_OVERRIDES"):
        return
    for kind in PROVIDER_REGISTRY:
        resolve_provider_base_url(kind)


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


class HttpLlmProviderAdapter(LlmProviderAdapter):
    def __init__(
        self,
        kind: str,
        settings: ProviderSettings,
        credential_resolver: CredentialResolver,
        client: httpx.AsyncClient | None = None,
        *,
        base_url: str | None = None,
    ) -> None:
        self.kind = kind
        self.settings = settings
        self._resolver = credential_resolver
        self._client = client
        self.base_url = (base_url or resolve_provider_base_url(kind)).rstrip("/")

    @abstractmethod
    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(base_url=self.base_url, timeout=60)
        try:
            response = await client.request(
                method, path, headers=self._headers(), **kwargs
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ProviderRequestError("LLM provider returned invalid JSON")
            return payload
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise ProviderAuthenticationError(
                    "LLM provider rejected the API key"
                ) from exc
            raise ProviderUpstreamError(exc.response.status_code) from exc
        except ProviderRequestError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderRequestError("LLM provider request failed") from exc
        finally:
            if owns_client:
                await client.aclose()


class OpenAICompatibleAdapter(HttpLlmProviderAdapter):
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._resolver.resolve()}"}

    async def verify(self) -> None:
        await self.list_models()

    async def list_models(self) -> list[ProviderModel]:
        payload = await self._request("GET", "/models")
        data = payload.get("data")
        if not isinstance(data, list):
            raise ProviderRequestError("LLM provider returned an invalid model catalog")
        models: list[ProviderModel] = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            context_length = item.get("context_length", item.get("max_model_len"))
            models.append(
                ProviderModel(
                    id=item["id"],
                    name=str(item.get("name") or item["id"]),
                    context_length=(
                        context_length if isinstance(context_length, int) else None
                    ),
                    pricing=(
                        item.get("pricing")
                        if isinstance(item.get("pricing"), dict)
                        else {}
                    ),
                )
            )
        return models

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
                usage=(
                    payload.get("usage")
                    if isinstance(payload.get("usage"), dict)
                    else {}
                ),
                raw=payload,
            )
        raise ProviderRequestError(
            f"LLM provider {invalid_reason} after {max_attempts} attempts"
        )


class OpenRouterAdapter(OpenAICompatibleAdapter):
    kind = "openrouter"

    def __init__(
        self,
        settings: ProviderSettings,
        credential_resolver: CredentialResolver,
        client: httpx.AsyncClient | None = None,
        *,
        base_url: str | None = None,
    ) -> None:
        super().__init__(
            "openrouter",
            settings,
            credential_resolver,
            client,
            base_url=base_url,
        )

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        if self.settings.site_url:
            headers["HTTP-Referer"] = str(self.settings.site_url)
            headers["X-OpenRouter-Title"] = self.settings.app_title
        return headers

    async def verify(self) -> None:
        payload = await self._request("GET", "/key")
        if not isinstance(payload.get("data"), dict):
            raise ProviderRequestError(
                "OpenRouter returned an invalid verification response"
            )


class AnthropicAdapter(HttpLlmProviderAdapter):
    kind = "anthropic"

    def __init__(
        self,
        settings: ProviderSettings,
        credential_resolver: CredentialResolver,
        client: httpx.AsyncClient | None = None,
        *,
        base_url: str | None = None,
    ) -> None:
        super().__init__(
            "anthropic",
            settings,
            credential_resolver,
            client,
            base_url=base_url,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._resolver.resolve(),
            "anthropic-version": ANTHROPIC_VERSION,
        }

    async def verify(self) -> None:
        await self.list_models()

    async def list_models(self) -> list[ProviderModel]:
        payload = await self._request("GET", "/v1/models")
        data = payload.get("data")
        if not isinstance(data, list):
            raise ProviderRequestError("Anthropic returned an invalid model catalog")
        models: list[ProviderModel] = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            models.append(
                ProviderModel(
                    id=item["id"],
                    name=str(item.get("display_name") or item["id"]),
                )
            )
        return models

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResult:
        model = request.model or self.settings.default_model
        if not model:
            raise ProviderConfigurationError("A model is required")
        system_parts = [
            message.content for message in request.messages if message.role == "system"
        ]
        messages = [
            {
                "role": "user" if message.role == "tool" else message.role,
                "content": message.content,
            }
            for message in request.messages
            if message.role != "system"
        ]
        if not messages:
            raise ProviderConfigurationError(
                "Anthropic requires at least one non-system message"
            )
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens or 1024,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if request.temperature is not None:
            body["temperature"] = request.temperature
        transient_statuses = {408, 429, 500, 502, 503, 529}
        payload: dict[str, Any] | None = None
        for attempt in range(3):
            try:
                payload = await self._request("POST", "/v1/messages", json=body)
                break
            except ProviderUpstreamError as exc:
                if attempt < 2 and exc.status_code in transient_statuses:
                    await sleep(0.5 * (attempt + 1))
                    continue
                raise
        if payload is None:
            raise ProviderRequestError("Anthropic message request failed")
        content = payload.get("content")
        if not isinstance(content, list):
            raise ProviderRequestError("Anthropic returned an invalid message response")
        text = "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
        if not text:
            raise ProviderRequestError("Anthropic message contained no text content")
        return ChatCompletionResult(
            id=str(payload.get("id") or ""),
            model=str(payload.get("model") or model),
            message=ChatMessage(role="assistant", content=text),
            finish_reason=(
                str(payload["stop_reason"])
                if payload.get("stop_reason") is not None
                else None
            ),
            usage=(
                payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            ),
            raw=payload,
        )


def create_provider_adapter(
    kind: str,
    settings: dict[str, Any],
    api_key: str,
    client: httpx.AsyncClient | None = None,
    *,
    base_url: str | None = None,
) -> LlmProviderAdapter:
    parsed_settings = ProviderSettings.model_validate(settings)
    resolver = StaticCredentialResolver(api_key)
    if kind == "openrouter":
        return OpenRouterAdapter(
            parsed_settings,
            resolver,
            client,
            base_url=base_url,
        )
    if kind in {"openai", "nvidia"}:
        return OpenAICompatibleAdapter(
            kind,
            parsed_settings,
            resolver,
            client,
            base_url=base_url,
        )
    if kind == "anthropic":
        return AnthropicAdapter(
            parsed_settings,
            resolver,
            client,
            base_url=base_url,
        )
    raise ProviderConfigurationError(f"Unsupported LLM provider: {kind}")
