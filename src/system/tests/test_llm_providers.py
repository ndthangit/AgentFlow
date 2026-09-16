import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from api.providers import (
    create_llm_provider,
    delete_llm_provider,
    test_llm_provider_model,
    update_llm_provider,
)
from domain.models import LlmProvider
from providers.adapters import (
    ChatCompletionRequest,
    LlmProviderCreate,
    LlmProviderUpdate,
    OpenRouterSettings,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderModelTestRequest,
    ProviderUpstreamError,
    create_provider_adapter,
)
from providers.secrets import ProviderSecretStore


class LlmProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_create_requires_non_empty_api_key(self):
        with self.assertRaises(ValidationError):
            LlmProviderCreate(name="OpenRouter", kind="openrouter", api_key="")

    def test_create_contract_only_requires_api_key(self):
        request = LlmProviderCreate(api_key="test-secret")
        self.assertEqual(request.model_fields_set, {"api_key"})

    def test_selected_models_are_trimmed_and_deduplicated(self):
        settings = OpenRouterSettings(
            selected_models=[" openai/test ", "openai/test", "anthropic/test"]
        )
        self.assertEqual(
            settings.selected_models, ["openai/test", "anthropic/test"]
        )

    async def test_new_provider_is_saved_inactive_without_network_verification(self):
        class FakeSession:
            added = None

            def add(self, provider):
                self.added = provider

            async def commit(self):
                return None

            async def refresh(self, _provider):
                return None

        session = FakeSession()
        with patch("api.providers._encrypt_api_key", return_value="encrypted"):
            provider = await create_llm_provider(
                LlmProviderCreate(api_key="test-secret"),
                {"sub": "test-user"},
                session,
            )

        self.assertIs(provider, session.added)
        self.assertEqual(provider.name, "OpenRouter")
        self.assertEqual(provider.kind, "openrouter")
        self.assertEqual(provider.settings, {})
        self.assertFalse(provider.enabled)

    async def test_activation_requires_at_least_one_selected_model(self):
        provider = LlmProvider(
            id=uuid.uuid4(),
            owner_subject="test-user",
            name="OpenRouter",
            kind="openrouter",
            settings={},
            api_key_encrypted="encrypted",
            enabled=False,
            revision=1,
        )
        session = AsyncMock()
        session.scalar.return_value = provider

        with self.assertRaises(HTTPException) as caught:
            await update_llm_provider(
                provider.id,
                LlmProviderUpdate(
                    expected_revision=1,
                    name="OpenRouter",
                    settings=OpenRouterSettings(),
                    enabled=True,
                ),
                {"sub": "test-user"},
                session,
            )

        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("at least one model", caught.exception.detail)
        session.execute.assert_not_awaited()

    async def test_delete_provider_commits_when_owned_provider_exists(self):
        provider_id = uuid.uuid4()

        class DeleteResult:
            def scalar_one_or_none(self):
                return provider_id

        session = AsyncMock()
        session.execute.return_value = DeleteResult()

        response = await delete_llm_provider(
            provider_id,
            {"sub": "test-user"},
            session,
        )

        self.assertEqual(response.status_code, 204)
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()

    async def test_delete_provider_returns_not_found_without_ownership(self):
        class DeleteResult:
            def scalar_one_or_none(self):
                return None

        session = AsyncMock()
        session.execute.return_value = DeleteResult()

        with self.assertRaises(HTTPException) as caught:
            await delete_llm_provider(
                uuid.uuid4(),
                {"sub": "another-user"},
                session,
            )

        self.assertEqual(caught.exception.status_code, 404)
        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()

    def test_factory_rejects_unknown_provider(self):
        with self.assertRaises(ProviderConfigurationError):
            create_provider_adapter("unknown", {}, "test-secret")

    def test_provider_secret_store_encrypts_api_key(self):
        with TemporaryDirectory() as directory:
            store = ProviderSecretStore.from_path(Path(directory) / "provider.key")
            encrypted = store.encrypt("sk-or-test-secret")

            self.assertNotIn("sk-or-test-secret", encrypted)
            self.assertEqual(store.decrypt(encrypted), "sk-or-test-secret")

    async def test_openrouter_verification_rejects_invalid_api_key(self):
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "invalid key"})

        client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            transport=httpx.MockTransport(handler),
        )
        self.addAsyncCleanup(client.aclose)
        adapter = create_provider_adapter("openrouter", {}, "invalid", client)

        with self.assertRaises(ProviderAuthenticationError):
            await adapter.verify()

    async def test_openrouter_verification_uses_current_key_endpoint(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/v1/key")
            self.assertEqual(request.headers["Authorization"], "Bearer test-secret")
            return httpx.Response(200, json={"data": {"label": "test"}})

        client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            transport=httpx.MockTransport(handler),
        )
        self.addAsyncCleanup(client.aclose)

        await create_provider_adapter("openrouter", {}, "test-secret", client).verify()

    async def test_openrouter_adapter_uses_contract_and_headers(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["Authorization"], "Bearer test-secret")
            self.assertEqual(
                request.headers["HTTP-Referer"], "https://agentflow.example/"
            )
            self.assertEqual(request.headers["X-OpenRouter-Title"], "AgentFlow Test")
            self.assertEqual(request.url.path, "/api/v1/chat/completions")
            return httpx.Response(
                200,
                json={
                    "id": "chat-1",
                    "model": "openai/test-model",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "hello"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 1,
                        "total_tokens": 3,
                    },
                },
            )

        client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            transport=httpx.MockTransport(handler),
        )
        self.addAsyncCleanup(client.aclose)
        adapter = create_provider_adapter(
            "openrouter",
            {
                "default_model": "openai/test-model",
                "site_url": "https://agentflow.example",
                "app_title": "AgentFlow Test",
            },
            "test-secret",
            client,
        )
        result = await adapter.complete(
            ChatCompletionRequest(messages=[{"role": "user", "content": "hi"}])
        )
        self.assertEqual(result.message.content, "hello")
        self.assertEqual(result.usage["total_tokens"], 3)

    async def test_openrouter_accepts_nested_usage_details(self):
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "chat-1",
                    "model": "openai/test-model",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "{}"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 4,
                        "prompt_tokens_details": {"cached_tokens": 0},
                    },
                },
            )

        client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            transport=httpx.MockTransport(handler),
        )
        self.addAsyncCleanup(client.aclose)
        adapter = create_provider_adapter(
            "openrouter",
            {"default_model": "openai/test-model"},
            "test-secret",
            client,
        )
        result = await adapter.complete(
            ChatCompletionRequest(messages=[{"role": "user", "content": "hi"}])
        )

        self.assertEqual(result.usage["prompt_tokens_details"], {"cached_tokens": 0})

    async def test_openrouter_retries_an_empty_choices_response(self):
        calls = 0

        async def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(200, json={"choices": []})
            return httpx.Response(
                200,
                json={
                    "id": "chat-2",
                    "model": "openai/test-model",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "{}"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

        client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            transport=httpx.MockTransport(handler),
        )
        self.addAsyncCleanup(client.aclose)
        adapter = create_provider_adapter(
            "openrouter",
            {"default_model": "openai/test-model"},
            "test-secret",
            client,
        )

        result = await adapter.complete(
            ChatCompletionRequest(messages=[{"role": "user", "content": "hi"}])
        )

        self.assertEqual(result.message.content, "{}")
        self.assertEqual(calls, 2)

    async def test_openrouter_retries_a_transient_error_envelope(self):
        calls = 0

        async def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(
                    200,
                    json={"error": {"code": 503, "message": "provider overloaded"}},
                )
            return httpx.Response(
                200,
                json={
                    "id": "chat-3",
                    "model": "openai/test-model",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "OK"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

        client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            transport=httpx.MockTransport(handler),
        )
        self.addAsyncCleanup(client.aclose)
        adapter = create_provider_adapter(
            "openrouter",
            {"default_model": "openai/test-model"},
            "test-secret",
            client,
        )

        result = await adapter.complete(
            ChatCompletionRequest(messages=[{"role": "user", "content": "hi"}])
        )

        self.assertEqual(result.message.content, "OK")
        self.assertEqual(calls, 2)

    async def test_model_test_maps_rate_limit_and_uses_a_practical_token_limit(self):
        provider = LlmProvider(
            id=uuid.uuid4(),
            owner_subject="test-user",
            name="OpenRouter",
            kind="openrouter",
            settings={"selected_models": ["openai/test-model"]},
            api_key_encrypted="encrypted",
            enabled=True,
            revision=1,
        )
        session = AsyncMock()
        session.scalar.return_value = provider
        adapter = AsyncMock()
        adapter.complete.side_effect = ProviderUpstreamError(429)

        with (
            patch("api.providers._decrypt_api_key", return_value="test-secret"),
            patch("api.providers.create_provider_adapter", return_value=adapter),
            self.assertRaises(HTTPException) as caught,
        ):
            await test_llm_provider_model(
                provider.id,
                ProviderModelTestRequest(model="openai/test-model"),
                {"sub": "test-user"},
                session,
            )

        self.assertEqual(caught.exception.status_code, 429)
        completion_request = adapter.complete.await_args.args[0]
        self.assertEqual(completion_request.max_tokens, 64)

    async def test_openrouter_model_catalog_is_normalized(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/v1/models")
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "openai/test",
                            "name": "Test",
                            "context_length": 8192,
                            "pricing": {
                                "prompt": "0.1",
                                "overrides": [
                                    {
                                        "utc_start": 0,
                                        "utc_end": 8,
                                        "prompt": "0.000000015",
                                    }
                                ],
                            },
                        }
                    ]
                },
            )

        client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            transport=httpx.MockTransport(handler),
        )
        self.addAsyncCleanup(client.aclose)
        adapter = create_provider_adapter("openrouter", {}, "test-secret", client)
        models = await adapter.list_models()
        self.assertEqual(models[0].id, "openai/test")
        self.assertEqual(models[0].context_length, 8192)
        self.assertEqual(models[0].pricing["overrides"][0]["utc_end"], 8)


if __name__ == "__main__":
    unittest.main()
