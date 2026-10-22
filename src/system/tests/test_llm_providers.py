import json
import os
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
    resolve_provider_base_url,
)
from providers.secrets import ProviderSecretStore


class LlmProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_create_requires_non_empty_api_key(self):
        with self.assertRaises(ValidationError):
            LlmProviderCreate(name="Personal OpenRouter", kind="openrouter", api_key="")

    def test_create_requires_a_provider_instance_name(self):
        with self.assertRaises(ValidationError):
            LlmProviderCreate(name="", kind="openrouter", api_key="test-secret")

    def test_create_contract_accepts_named_instances_of_the_same_kind(self):
        first = LlmProviderCreate(
            name="Personal OpenRouter",
            kind="openrouter",
            api_key="test-secret-1",
        )
        second = LlmProviderCreate(
            name="Company OpenRouter",
            kind="openrouter",
            api_key="test-secret-2",
        )
        self.assertEqual(first.kind, second.kind)
        self.assertNotEqual(first.name, second.name)

    def test_selected_models_are_trimmed_and_deduplicated(self):
        settings = OpenRouterSettings(
            selected_models=[" openai/test ", "openai/test", "anthropic/test"]
        )
        self.assertEqual(settings.selected_models, ["openai/test", "anthropic/test"])

    def test_protocol_provider_requires_a_safe_base_url(self):
        with self.assertRaises(ValidationError):
            LlmProviderCreate(
                name="Local service",
                kind="openai-compatible",
                api_key="test-secret",
            )
        with self.assertRaises(ValidationError):
            LlmProviderCreate(
                name="Metadata service",
                kind="openai-compatible",
                api_key="test-secret",
                base_url="http://169.254.169.254/v1",
            )

        request = LlmProviderCreate(
            name="Small vendor",
            kind="openai-compatible",
            api_key="test-secret",
            base_url="https://api.small-vendor.example/v1/",
            custom_models=[" model-a ", "model-a", "model-b"],
        )
        self.assertEqual(request.base_url, "https://api.small-vendor.example/v1")
        self.assertEqual(request.custom_models, ["model-a", "model-b"])

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
                LlmProviderCreate(
                    name="Team A OpenRouter",
                    kind="openrouter",
                    api_key="test-secret",
                ),
                {"sub": "test-user"},
                session,
            )

        self.assertIs(provider, session.added)
        self.assertEqual(provider.name, "Team A OpenRouter")
        self.assertEqual(provider.kind, "openrouter")
        self.assertEqual(provider.settings, {})
        self.assertFalse(provider.enabled)

    async def test_protocol_provider_persists_endpoint_and_manual_catalog(self):
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
                LlmProviderCreate(
                    name="Small vendor",
                    kind="openai-compatible",
                    api_key="test-secret",
                    base_url="https://api.small-vendor.example/v1",
                    custom_models=["model-a"],
                ),
                {"sub": "test-user"},
                session,
            )

        self.assertEqual(
            provider.settings,
            {
                "base_url": "https://api.small-vendor.example/v1",
                "custom_models": ["model-a"],
            },
        )

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

    async def test_update_keeps_custom_endpoint_settings_on_protocol_providers_only(
        self,
    ):
        provider = LlmProvider(
            id=uuid.uuid4(),
            owner_subject="test-user",
            name="Small vendor",
            kind="openai-compatible",
            settings={"base_url": "https://api.small-vendor.example/v1"},
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
                    name="Small vendor",
                    settings=OpenRouterSettings(),
                    enabled=False,
                ),
                {"sub": "test-user"},
                session,
            )

        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("base URL", caught.exception.detail)
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

    def test_create_contract_accepts_all_supported_provider_kinds(self):
        for kind in ("openrouter", "openai", "anthropic", "nvidia"):
            with self.subTest(kind=kind):
                request = LlmProviderCreate(
                    name=f"Provider {kind}", kind=kind, api_key="test-key"
                )
                self.assertEqual(request.kind, kind)

        for kind in ("openai-compatible", "anthropic-compatible"):
            with self.subTest(kind=kind):
                request = LlmProviderCreate(
                    name=f"Provider {kind}",
                    kind=kind,
                    api_key="test-key",
                    base_url="https://api.small-vendor.example/v1",
                )
                self.assertEqual(request.kind, kind)

    async def test_openai_compatible_provider_uses_manual_model_catalog(self):
        client = AsyncMock()
        adapter = create_provider_adapter(
            "openai-compatible",
            {
                "base_url": "https://api.small-vendor.example/v1",
                "custom_models": ["vendor/model-a", "vendor/model-b"],
            },
            "test-key",
            client,
        )

        models = await adapter.list_models()

        self.assertEqual(
            [model.id for model in models], ["vendor/model-a", "vendor/model-b"]
        )
        client.request.assert_not_awaited()

    async def test_anthropic_compatible_provider_uses_custom_endpoint(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/messages")
            self.assertEqual(request.headers["x-api-key"], "test-key")
            return httpx.Response(
                200,
                json={
                    "id": "message-1",
                    "model": "vendor-claude",
                    "content": [{"type": "text", "text": "OK"}],
                    "stop_reason": "end_turn",
                },
            )

        client = httpx.AsyncClient(
            base_url="https://api.small-vendor.example",
            transport=httpx.MockTransport(handler),
        )
        self.addAsyncCleanup(client.aclose)
        adapter = create_provider_adapter(
            "anthropic-compatible",
            {
                "base_url": "https://api.small-vendor.example",
                "default_model": "vendor-claude",
            },
            "test-key",
            client,
        )

        result = await adapter.complete(
            ChatCompletionRequest(messages=[{"role": "user", "content": "hi"}])
        )

        self.assertEqual(result.message.content, "OK")

    async def test_openai_adapter_uses_bearer_auth_and_normalizes_catalog(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/models")
            self.assertEqual(request.headers["Authorization"], "Bearer test-openai-key")
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [{"id": "test-chat-model", "owned_by": "test"}],
                },
            )

        client = httpx.AsyncClient(
            base_url="https://unit.test/v1",
            transport=httpx.MockTransport(handler),
        )
        self.addAsyncCleanup(client.aclose)
        models = await create_provider_adapter(
            "openai", {}, "test-openai-key", client
        ).list_models()
        self.assertEqual(models[0].id, "test-chat-model")

    async def test_nvidia_adapter_uses_openai_compatible_completion(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/chat/completions")
            self.assertEqual(request.headers["Authorization"], "Bearer test-nvidia-key")
            self.assertEqual(json.loads(request.content)["model"], "test-chat-model")
            return httpx.Response(
                200,
                json={
                    "id": "chat-test",
                    "model": "test-chat-model",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "OK"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

        client = httpx.AsyncClient(
            base_url="https://unit.test/v1",
            transport=httpx.MockTransport(handler),
        )
        self.addAsyncCleanup(client.aclose)
        result = await create_provider_adapter(
            "nvidia",
            {"default_model": "test-chat-model"},
            "test-nvidia-key",
            client,
        ).complete(ChatCompletionRequest(messages=[{"role": "user", "content": "hi"}]))
        self.assertEqual(result.message.content, "OK")

    async def test_anthropic_adapter_translates_system_message_and_response(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/messages")
            self.assertEqual(request.headers["x-api-key"], "test-anthropic-key")
            self.assertEqual(request.headers["anthropic-version"], "2023-06-01")
            body = json.loads(request.content)
            self.assertEqual(body["system"], "Be concise")
            self.assertEqual(body["messages"], [{"role": "user", "content": "hi"}])
            return httpx.Response(
                200,
                json={
                    "id": "msg-test",
                    "model": "test-claude-model",
                    "content": [{"type": "text", "text": "OK"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 2, "output_tokens": 1},
                },
            )

        client = httpx.AsyncClient(
            base_url="https://unit.test",
            transport=httpx.MockTransport(handler),
        )
        self.addAsyncCleanup(client.aclose)
        result = await create_provider_adapter(
            "anthropic",
            {"default_model": "test-claude-model"},
            "test-anthropic-key",
            client,
        ).complete(
            ChatCompletionRequest(
                messages=[
                    {"role": "system", "content": "Be concise"},
                    {"role": "user", "content": "hi"},
                ],
                max_tokens=64,
            )
        )
        self.assertEqual(result.message.content, "OK")
        self.assertEqual(result.usage["output_tokens"], 1)

    def test_endpoint_override_is_local_only_and_rejected_in_production(self):
        environment = {
            "ALLOW_PROVIDER_ENDPOINT_OVERRIDES": "true",
            "OPENAI_BASE_URL_OVERRIDE": "http://provider-mock:8089/openai/v1/",
            "DEPLOYMENT_ENVIRONMENT": "local",
        }
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(
                resolve_provider_base_url("openai"),
                "http://provider-mock:8089/openai/v1",
            )
        environment["DEPLOYMENT_ENVIRONMENT"] = "production"
        with (
            patch.dict(os.environ, environment, clear=True),
            self.assertRaises(ProviderConfigurationError),
        ):
            resolve_provider_base_url("openai")

    def test_private_custom_endpoint_can_only_be_enabled_outside_production(self):
        local_environment = {
            "ALLOW_CUSTOM_PROVIDER_PRIVATE_NETWORKS": "true",
            "DEPLOYMENT_ENVIRONMENT": "local",
        }
        settings = {"base_url": "http://provider-mock:8089/openai/v1"}
        with patch.dict(os.environ, local_environment, clear=True):
            self.assertEqual(
                resolve_provider_base_url("openai-compatible", settings),
                "http://provider-mock:8089/openai/v1",
            )

        production_environment = {
            **local_environment,
            "DEPLOYMENT_ENVIRONMENT": "production",
        }
        with (
            patch.dict(os.environ, production_environment, clear=True),
            self.assertRaises(ProviderConfigurationError),
        ):
            resolve_provider_base_url("openai-compatible", settings)


if __name__ == "__main__":
    unittest.main()
