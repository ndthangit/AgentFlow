import asyncio
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

from domain.errors import WorkflowExecutionError
from runtime.agent_container import AgentContainerSettings
from runtime.agent_runtime_manager import (
    AgentRuntimeDefinition,
    AgentRuntimeManager,
    runtime_definitions_from_env,
)


def container_settings() -> AgentContainerSettings:
    return AgentContainerSettings(
        image="unused:test",
        docker_binary="docker",
        timeout_seconds=30,
        cpus="1",
        memory="512m",
        pids_limit=64,
        network="bridge",
        max_output_bytes=4096,
        pull_policy="never",
    )


class AgentRuntimeCatalogTests(unittest.TestCase):
    def test_loads_multiple_runtime_images_from_environment(self):
        with patch.dict(
            os.environ,
            {
                "AGENT_RUNTIME_IMAGES": (
                    '{"default":"agentflow-agent-default:1.0",'
                    '"reviewer":"registry.local/reviewer@sha256:abc"}'
                ),
                "AGENT_RUNTIME_DEFAULT": "default",
            },
            clear=True,
        ):
            definitions = runtime_definitions_from_env()

        self.assertEqual(
            definitions,
            (
                AgentRuntimeDefinition("default", "agentflow-agent-default:1.0"),
                AgentRuntimeDefinition(
                    "reviewer", "registry.local/reviewer@sha256:abc"
                ),
            ),
        )

    def test_falls_back_to_the_legacy_single_image_setting(self):
        with patch.dict(
            os.environ,
            {
                "AGENT_RUNTIME_DEFAULT": "custom",
                "AGENT_RUNTIME_IMAGE": "agentflow-agent-custom:1.0",
            },
            clear=True,
        ):
            definitions = runtime_definitions_from_env()

        self.assertEqual(
            definitions,
            (AgentRuntimeDefinition("custom", "agentflow-agent-custom:1.0"),),
        )

    def test_rejects_an_invalid_catalog(self):
        with patch.dict(
            os.environ, {"AGENT_RUNTIME_IMAGES": "[]"}, clear=True
        ):
            with self.assertRaisesRegex(
                WorkflowExecutionError, "non-empty JSON object"
            ):
                runtime_definitions_from_env()


class AgentRuntimeManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.default_executor = AsyncMock()
        self.default_executor.execute.return_value = {"runtime": "default"}
        self.reviewer_executor = AsyncMock()
        self.reviewer_executor.execute.return_value = {"runtime": "reviewer"}
        self.factory = Mock(
            side_effect=[self.default_executor, self.reviewer_executor]
        )
        self.manager = AgentRuntimeManager(
            [
                AgentRuntimeDefinition("default", "agentflow-agent-default:1.0"),
                AgentRuntimeDefinition("reviewer", "agentflow-agent-reviewer:2.0"),
            ],
            container_settings=container_settings(),
            executor_factory=self.factory,
        )

    async def test_resolves_legacy_alias_and_passes_the_request(self):
        request = {"task": "plan", "context": "{}"}
        result = await self.manager.execute(
            "agent",
            request,
            environment_overrides={"AGENT_MODEL": "test"},
        )

        self.assertEqual(result, {"runtime": "default"})
        built_settings = self.factory.call_args.args[0]
        self.assertEqual(built_settings.image, "agentflow-agent-default:1.0")
        self.default_executor.execute.assert_awaited_once_with(
            request, environment_overrides={"AGENT_MODEL": "test"}
        )

    async def test_selects_and_caches_each_runtime_executor(self):
        await self.manager.execute("default", {"task": "one"})
        await self.manager.execute("default", {"task": "two"})
        result = await self.manager.execute("reviewer", {"task": "review"})

        self.assertEqual(result, {"runtime": "reviewer"})
        self.assertEqual(self.factory.call_count, 2)
        images = [call.args[0].image for call in self.factory.call_args_list]
        self.assertEqual(
            images,
            ["agentflow-agent-default:1.0", "agentflow-agent-reviewer:2.0"],
        )

    async def test_rejects_a_runtime_outside_the_allow_list(self):
        with self.assertRaisesRegex(
            WorkflowExecutionError, "Agent runtime is not registered: unknown"
        ) as raised:
            await self.manager.execute("unknown", {"task": "no"})

        self.assertEqual(raised.exception.code, "CONFIGURATION_ERROR")
        self.factory.assert_not_called()

    def test_lists_available_runtime_ids(self):
        self.assertEqual(
            self.manager.available_runtime_ids(), ("default", "reviewer")
        )


if __name__ == "__main__":
    unittest.main()
