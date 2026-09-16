"""LLM provider configuration endpoints."""

import uuid
from functools import lru_cache
from time import monotonic

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from api.dependencies import Claims, DatabaseSession
from domain.models import LlmProvider
from providers.adapters import (
    ChatCompletionRequest,
    LlmProviderCreate,
    LlmProviderUpdate,
    LlmProviderView,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderModel,
    ProviderModelTestRequest,
    ProviderModelTestResult,
    ProviderRequestError,
    ProviderUpstreamError,
    create_provider_adapter,
)
from providers.secrets import ProviderSecretError, ProviderSecretStore

router = APIRouter(prefix="/v1/llm-providers", tags=["llm-providers"])


@lru_cache(maxsize=1)
def _secret_store() -> ProviderSecretStore:
    try:
        return ProviderSecretStore.from_config()
    except ProviderSecretError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


def _decrypt_api_key(provider: LlmProvider) -> str:
    try:
        return _secret_store().decrypt(provider.api_key_encrypted)
    except ProviderSecretError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


def _encrypt_api_key(api_key: str) -> str:
    try:
        return _secret_store().encrypt(api_key)
    except ProviderSecretError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


async def _list_models(kind: str, settings: dict, api_key: str) -> list[ProviderModel]:
    try:
        return await create_provider_adapter(kind, settings, api_key).list_models()
    except ProviderAuthenticationError:
        raise HTTPException(
            status_code=422, detail="Provider rejected the API key"
        ) from None
    except ProviderConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ProviderRequestError:
        raise HTTPException(
            status_code=502, detail="LLM provider request failed"
        ) from None


@router.get("", response_model=list[LlmProviderView])
async def list_llm_providers(claims: Claims, session: DatabaseSession):
    result = await session.scalars(
        select(LlmProvider)
        .where(LlmProvider.owner_subject == claims["sub"])
        .order_by(LlmProvider.name)
    )
    return list(result)


@router.post("", response_model=LlmProviderView, status_code=201)
async def create_llm_provider(
    request: LlmProviderCreate,
    claims: Claims,
    session: DatabaseSession,
):
    api_key = request.api_key.get_secret_value()
    provider = LlmProvider(
        owner_subject=claims["sub"],
        name="OpenRouter",
        kind="openrouter",
        settings={},
        api_key_encrypted=_encrypt_api_key(api_key),
        enabled=False,
    )
    session.add(provider)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="Provider name already exists"
        ) from None
    await session.refresh(provider)
    return provider


@router.get("/{provider_id}", response_model=LlmProviderView)
async def get_llm_provider(
    provider_id: uuid.UUID,
    claims: Claims,
    session: DatabaseSession,
):
    provider = await session.scalar(
        select(LlmProvider).where(
            LlmProvider.id == provider_id,
            LlmProvider.owner_subject == claims["sub"],
        )
    )
    if provider is None:
        raise HTTPException(status_code=404, detail="LLM provider not found")
    return provider


@router.delete("/{provider_id}", status_code=204)
async def delete_llm_provider(
    provider_id: uuid.UUID,
    claims: Claims,
    session: DatabaseSession,
) -> Response:
    result = await session.execute(
        delete(LlmProvider)
        .where(
            LlmProvider.id == provider_id,
            LlmProvider.owner_subject == claims["sub"],
        )
        .returning(LlmProvider.id)
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        raise HTTPException(status_code=404, detail="LLM provider not found")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{provider_id}", response_model=LlmProviderView)
async def update_llm_provider(
    provider_id: uuid.UUID,
    request: LlmProviderUpdate,
    claims: Claims,
    session: DatabaseSession,
):
    current = await session.scalar(
        select(LlmProvider).where(
            LlmProvider.id == provider_id,
            LlmProvider.owner_subject == claims["sub"],
        )
    )
    if current is None:
        raise HTTPException(status_code=404, detail="LLM provider not found")
    if current.revision != request.expected_revision:
        raise HTTPException(status_code=409, detail="Provider revision conflict")

    settings = request.settings.model_dump(mode="json")
    replacement_key = (
        request.api_key.get_secret_value() if request.api_key is not None else None
    )
    if request.enabled:
        selected_models = request.settings.selected_models
        if not selected_models:
            raise HTTPException(
                status_code=422,
                detail="Select at least one model before activating the provider",
            )
        if request.settings.default_model not in selected_models:
            settings["default_model"] = selected_models[0]
        available = await _list_models(
            current.kind,
            settings,
            replacement_key or _decrypt_api_key(current),
        )
        available_ids = {model.id for model in available}
        unavailable = [model for model in selected_models if model not in available_ids]
        if unavailable:
            raise HTTPException(
                status_code=422,
                detail=f"Selected models are unavailable: {', '.join(unavailable)}",
            )
    encrypted_key = (
        _encrypt_api_key(replacement_key)
        if replacement_key is not None
        else current.api_key_encrypted
    )
    result = await session.execute(
        update(LlmProvider)
        .where(
            LlmProvider.id == provider_id,
            LlmProvider.owner_subject == claims["sub"],
            LlmProvider.revision == request.expected_revision,
        )
        .values(
            name=request.name,
            settings=settings,
            api_key_encrypted=encrypted_key,
            enabled=request.enabled,
            revision=LlmProvider.revision + 1,
        )
        .returning(LlmProvider)
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=409, detail="Provider revision conflict")
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="Provider name already exists"
        ) from None
    return provider


@router.get("/{provider_id}/models", response_model=list[ProviderModel])
async def list_llm_provider_models(
    provider_id: uuid.UUID,
    claims: Claims,
    session: DatabaseSession,
):
    provider = await session.scalar(
        select(LlmProvider).where(
            LlmProvider.id == provider_id,
            LlmProvider.owner_subject == claims["sub"],
        )
    )
    if provider is None:
        raise HTTPException(status_code=404, detail="LLM provider not found")
    return await _list_models(
        provider.kind,
        provider.settings,
        _decrypt_api_key(provider),
    )


@router.post("/{provider_id}/models/test", response_model=ProviderModelTestResult)
async def test_llm_provider_model(
    provider_id: uuid.UUID,
    request: ProviderModelTestRequest,
    claims: Claims,
    session: DatabaseSession,
):
    provider = await session.scalar(
        select(LlmProvider).where(
            LlmProvider.id == provider_id,
            LlmProvider.owner_subject == claims["sub"],
            LlmProvider.enabled.is_(True),
        )
    )
    if provider is None:
        raise HTTPException(status_code=404, detail="Active LLM provider not found")
    selected_models = provider.settings.get("selected_models") or []
    if request.model not in selected_models:
        raise HTTPException(
            status_code=422, detail="Model is not selected for this provider"
        )

    started_at = monotonic()
    try:
        adapter = create_provider_adapter(
            provider.kind,
            provider.settings,
            _decrypt_api_key(provider),
        )
        result = await adapter.complete(
            ChatCompletionRequest(
                model=request.model,
                messages=[
                    {
                        "role": "user",
                        "content": "Reply with exactly OK to confirm availability.",
                    }
                ],
                temperature=0,
                max_tokens=64,
            )
        )
    except ProviderAuthenticationError:
        raise HTTPException(
            status_code=422, detail="Provider rejected the API key"
        ) from None
    except ProviderConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ProviderUpstreamError as exc:
        if exc.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail="Model is rate-limited; wait a moment and try again",
            ) from None
        if exc.status_code == 402:
            raise HTTPException(
                status_code=402,
                detail="OpenRouter credits are insufficient for this model",
            ) from None
        if exc.status_code in {408, 500, 502, 503, 529}:
            raise HTTPException(
                status_code=503,
                detail="Model is temporarily unavailable; try again shortly",
            ) from None
        raise HTTPException(
            status_code=502,
            detail="OpenRouter rejected the model test request",
        ) from None
    except ProviderRequestError:
        raise HTTPException(status_code=502, detail="Model test failed") from None

    return ProviderModelTestResult(
        model=request.model,
        ok=True,
        latency_ms=round((monotonic() - started_at) * 1000),
        response=result.message.content,
    )
