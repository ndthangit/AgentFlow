import importlib.util
import unittest
from pathlib import Path

import httpx

from providers.adapters import (
    ChatCompletionRequest,
    ProviderAuthenticationError,
    ProviderRequestError,
    create_provider_adapter,
)


def load_provider_mock_app():
    app_path = Path(__file__).parents[2] / "provider-mock" / "app.py"
    spec = importlib.util.spec_from_file_location("agentflow_provider_mock", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load provider mock")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app


class ProviderMockIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = load_provider_mock_app()

    async def _client(self, base_url: str) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url=base_url,
        )
        self.addAsyncCleanup(client.aclose)
        return client

    async def test_openai_roundtrip_uses_only_fake_key(self):
        adapter = create_provider_adapter(
            "openai",
            {"default_model": "test-chat-model"},
            "test-openai-key",
            await self._client("http://provider-mock/openai/v1"),
        )

        models = await adapter.list_models()
        result = await adapter.complete(
            ChatCompletionRequest(messages=[{"role": "user", "content": "test"}])
        )

        self.assertIn("test-chat-model", {model.id for model in models})
        self.assertEqual(result.message.content, "mock response")

    async def test_generic_openai_protocol_roundtrip_uses_only_fake_key(self):
        adapter = create_provider_adapter(
            "openai-compatible",
            {
                "base_url": "https://api.small-vendor.example/v1",
                "default_model": "test-chat-model",
            },
            "test-openai-key",
            await self._client("http://provider-mock/openai/v1"),
        )

        models = await adapter.list_models()
        result = await adapter.complete(
            ChatCompletionRequest(messages=[{"role": "user", "content": "test"}])
        )

        self.assertIn("test-chat-model", {model.id for model in models})
        self.assertEqual(result.message.content, "mock response")

    async def test_nvidia_roundtrip_uses_only_fake_key(self):
        adapter = create_provider_adapter(
            "nvidia",
            {"default_model": "test-chat-model"},
            "test-nvidia-key",
            await self._client("http://provider-mock/nvidia/v1"),
        )

        models = await adapter.list_models()
        result = await adapter.complete(
            ChatCompletionRequest(messages=[{"role": "user", "content": "test"}])
        )

        self.assertEqual(models[0].id, "test-chat-model")
        self.assertEqual(result.model, "test-chat-model")

    async def test_anthropic_roundtrip_translates_messages(self):
        adapter = create_provider_adapter(
            "anthropic",
            {"default_model": "test-claude-model"},
            "test-anthropic-key",
            await self._client("http://provider-mock/anthropic"),
        )

        models = await adapter.list_models()
        result = await adapter.complete(
            ChatCompletionRequest(
                messages=[
                    {"role": "system", "content": "Be concise"},
                    {"role": "user", "content": "test"},
                ]
            )
        )

        self.assertEqual(models[0].id, "test-claude-model")
        self.assertEqual(result.message.content, "mock response")

    async def test_fake_server_rejects_wrong_key(self):
        adapter = create_provider_adapter(
            "openai",
            {},
            "not-the-test-key",
            await self._client("http://provider-mock/openai/v1"),
        )

        with self.assertRaises(ProviderAuthenticationError):
            await adapter.list_models()

    async def test_fake_server_exercises_invalid_response_mapping(self):
        adapter = create_provider_adapter(
            "nvidia",
            {"default_model": "test-invalid-envelope"},
            "test-nvidia-key",
            await self._client("http://provider-mock/nvidia/v1"),
        )

        with self.assertRaises(ProviderRequestError):
            await adapter.complete(
                ChatCompletionRequest(messages=[{"role": "user", "content": "test"}])
            )


if __name__ == "__main__":
    unittest.main()
