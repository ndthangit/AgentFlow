"""Deterministic local test double for AgentFlow LLM provider integrations."""

import asyncio
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI(title="AgentFlow Provider Mock", docs_url=None, redoc_url=None)

EXPECTED_BEARER_KEYS = {
    "openai": "test-openai-key",
    "nvidia": "test-nvidia-key",
}
EXPECTED_ANTHROPIC_KEY = "test-anthropic-key"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _require_bearer(provider: str, authorization: str | None) -> None:
    if authorization != f"Bearer {EXPECTED_BEARER_KEYS[provider]}":
        raise HTTPException(
            status_code=401,
            detail={
                "error": {"type": "authentication_error", "message": "invalid key"}
            },
        )


def _model_fixture(model: str) -> None:
    if model == "test-rate-limit":
        raise HTTPException(status_code=429, detail={"error": {"type": "rate_limit"}})
    if model == "test-unavailable":
        raise HTTPException(
            status_code=503, detail={"error": {"type": "service_unavailable"}}
        )


async def _delay_fixture(model: str) -> None:
    if model == "test-slow":
        await asyncio.sleep(65)


def _openai_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": model,
                "object": "model",
                "owned_by": "agentflow-test",
                "context_length": 8192,
            }
            for model in (
                "test-chat-model",
                "test-rate-limit",
                "test-unavailable",
                "test-invalid-envelope",
                "test-slow",
            )
        ],
    }


@app.get("/openai/v1/models")
@app.get("/nvidia/v1/models")
async def list_openai_models(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    provider = request.url.path.split("/", 2)[1]
    _require_bearer(provider, authorization)
    return _openai_models()


@app.post("/openai/v1/chat/completions")
@app.post("/nvidia/v1/chat/completions")
async def create_openai_completion(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    provider = request.url.path.split("/", 2)[1]
    _require_bearer(provider, authorization)
    body = await request.json()
    model = str(body.get("model") or "")
    _model_fixture(model)
    await _delay_fixture(model)
    if model == "test-invalid-envelope":
        return {"id": "chatcmpl-invalid", "choices": []}
    return {
        "id": "chatcmpl-test-001",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "mock response"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


def _require_anthropic(
    api_key: str | None,
    version: str | None,
) -> None:
    if api_key != EXPECTED_ANTHROPIC_KEY or not version:
        raise HTTPException(
            status_code=401,
            detail={"type": "error", "error": {"type": "authentication_error"}},
        )


@app.get("/anthropic/v1/models")
async def list_anthropic_models(
    x_api_key: str | None = Header(default=None),
    anthropic_version: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_anthropic(x_api_key, anthropic_version)
    return {
        "data": [
            {
                "id": model,
                "type": "model",
                "display_name": model.replace("-", " ").title(),
            }
            for model in (
                "test-claude-model",
                "test-rate-limit",
                "test-unavailable",
                "test-invalid-envelope",
                "test-slow",
            )
        ],
        "has_more": False,
        "first_id": "test-claude-model",
        "last_id": "test-slow",
    }


@app.post("/anthropic/v1/messages")
async def create_anthropic_message(
    request: Request,
    x_api_key: str | None = Header(default=None),
    anthropic_version: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_anthropic(x_api_key, anthropic_version)
    body = await request.json()
    model = str(body.get("model") or "")
    _model_fixture(model)
    await _delay_fixture(model)
    if model == "test-invalid-envelope":
        return {"id": "msg_invalid", "content": []}
    return {
        "id": "msg_test_001",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": "mock response"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }
