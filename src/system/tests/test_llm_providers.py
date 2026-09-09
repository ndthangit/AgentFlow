import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from pydantic import ValidationError

from system_api.llm_providers import (
    ChatCompletionRequest,
    LlmProviderCreate,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    create_provider_adapter,
)
from system_api.provider_secrets import ProviderSecretStore


class LlmProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_create_requires_non_empty_api_key(self):
        with self.assertRaises(ValidationError):
            LlmProviderCreate(name="OpenRouter", kind="openrouter", api_key="")

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
                            "pricing": {"prompt": "0.1"},
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


if __name__ == "__main__":
    unittest.main()
