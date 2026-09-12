"""LLM provider configuration endpoints."""

import uuid
from functools import lru_cache

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from api.dependencies import Claims, DatabaseSession
from domain.models import LlmProvider
from providers.adapters import (
    LlmProviderCreate,
    LlmProviderUpdate,
    LlmProviderView,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderModel,
    ProviderRequestError,
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


async def _verify_connection(kind: str, settings: dict, api_key: str) -> None:
    try:
        await create_provider_adapter(kind, settings, api_key).verify()
    except ProviderAuthenticationError:
        raise HTTPException(
            status_code=422, detail="Provider rejected the API key"
        ) from None
    except ProviderConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ProviderRequestError:
        raise HTTPException(
            status_code=502, detail="Could not verify provider connection"
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
    settings = request.settings.model_dump(mode="json")
    api_key = request.api_key.get_secret_value()
    await _verify_connection(request.kind, settings, api_key)
    provider = LlmProvider(
        owner_subject=claims["sub"],
        name=request.name,
        kind=request.kind,
        settings=settings,
        api_key_encrypted=_encrypt_api_key(api_key),
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
    if request.enabled or replacement_key is not None:
        await _verify_connection(
            current.kind,
            settings,
            replacement_key or _decrypt_api_key(current),
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
            LlmProvider.enabled.is_(True),
        )
    )
    if provider is None:
        raise HTTPException(status_code=404, detail="Enabled LLM provider not found")
    try:
        adapter = create_provider_adapter(
            provider.kind,
            provider.settings,
            _decrypt_api_key(provider),
        )
        return await adapter.list_models()
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
