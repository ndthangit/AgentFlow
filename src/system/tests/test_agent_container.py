import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from domain.errors import WorkflowExecutionError
from runtime.agent_container import (
    AgentContainerExecutor,
    AgentContainerSettings,
    build_agent_docker_command,
)
from runtime.agent_executor import create_agent_executor
from runtime.container_process import ContainerResult


def settings() -> AgentContainerSettings:
    return AgentContainerSettings(
        image="agentflow-agent-runtime:test",
        docker_binary="docker",
        timeout_seconds=30,
        cpus="1",
        memory="512m",
        pids_limit=64,
        network="bridge",
        max_output_bytes=4096,
        pull_policy="never",
    )


def configured_openrouter(model: str = "openai/test-model"):
    provider = SimpleNamespace(
        id="provider-1",
        name="OpenRouter",
        kind="openrouter",
        settings={"default_model": model, "selected_models": [model]},
        api_key_encrypted="encrypted-key",
    )
    secret_store = Mock()
    secret_store.decrypt.return_value = "secret-key"
    return provider, secret_store


class AgentDockerCommandTests(unittest.TestCase):
    def test_command_applies_isolation_and_invokes_live_cli(self):
        command = build_agent_docker_command(
            settings(), "agentflow-agent-0123456789abcdef"
        )

        self.assertEqual(command[:3], ["docker", "run", "--rm"])
        self.assertIn("--read-only", command)
        self.assertIn("10001:10001", command)
        self.assertIn("no-new-privileges", command)
        self.assertIn("ALL", command)
        self.assertIn("agent", command)
        self.assertIn("OPENROUTER_API_KEY", command)
        self.assertNotIn("OPENAI_COMPATIBLE_API_KEY", command)
        self.assertNotIn("ANTHROPIC_API_KEY", command)
        self.assertEqual(command[command.index("--network") + 1], "bridge")
        self.assertIn("host.docker.internal:host-gateway", command)
        self.assertNotIn("top-secret", " ".join(command))
        self.assertNotIn("--demo", command)
        self.assertEqual(command[-1], "agentflow-agent-runtime:test")


class AgentContainerExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_one_container_and_returns_run_output(self):
        calls = []

        async def runner(command, name, payload, environment, timeout):
            calls.append((command, name, payload, environment, timeout))
            return ContainerResult(
                0,
                b'{"run_id":"run-1","status":"succeeded","mode":"live",'
                b'"output":{"summary":"ok","steps":[],"notes":[]}}\n',
                b"",
            )

        executor = AgentContainerExecutor(
            settings(), runner=runner, semaphore=asyncio.Semaphore(1)
        )
        request = {"task": "Plan", "context": "{}", "skills": []}

        result = await executor.execute(request)

        self.assertEqual(result, {"summary": "ok", "steps": [], "notes": []})
        self.assertEqual(len(calls), 1)
        command, name, payload, _environment, timeout = calls[0]
        self.assertTrue(name.startswith("agentflow-agent-"))
        self.assertEqual(json.loads(payload), request)
        self.assertEqual(command[-1], "agentflow-agent-runtime:test")
        self.assertEqual(timeout, 30)

    async def test_hides_container_stderr_from_workflow_error(self):
        async def runner(*_args):
            return ContainerResult(1, b"", b"provider leaked detail")

        executor = AgentContainerExecutor(settings(), runner=runner)
        with self.assertRaisesRegex(
            WorkflowExecutionError, "Agent runtime container failed"
        ) as raised:
            await executor.execute({"task": "Plan", "context": "", "skills": []})

        self.assertNotIn("provider leaked detail", str(raised.exception))

    async def test_propagates_sanitized_agent_error_contract(self):
        async def runner(*_args):
            return ContainerResult(
                1,
                json.dumps(
                    {
                        "error": {
                            "code": "PROVIDER_OVERLOADED",
                            "message": "Model provider is temporarily overloaded "
                            "(HTTP 503). Retry later or select another model.",
                        }
                    }
                ).encode(),
                b"secret provider response",
            )

        executor = AgentContainerExecutor(settings(), runner=runner)
        with self.assertRaises(WorkflowExecutionError) as raised:
            await executor.execute({"task": "Plan", "context": "", "skills": []})

        self.assertEqual(raised.exception.code, "PROVIDER_OVERLOADED")
        self.assertIn("HTTP 503", str(raised.exception))
        self.assertNotIn("secret provider response", str(raised.exception))

    async def test_rejects_unrecognized_agent_error_contract(self):
        async def runner(*_args):
            return ContainerResult(
                1,
                b'{"error":{"code":"RAW_PROVIDER_ERROR","message":"secret"}}',
                b"another secret",
            )

        executor = AgentContainerExecutor(settings(), runner=runner)
        with self.assertRaisesRegex(
            WorkflowExecutionError, "Agent runtime container failed"
        ) as raised:
            await executor.execute({"task": "Plan", "context": "", "skills": []})

        self.assertEqual(raised.exception.code, "STEP_EXECUTION_FAILED")
        self.assertNotIn("secret", str(raised.exception))

    async def test_applies_per_node_model_environment_without_putting_secret_in_argv(self):
        calls = []

        async def runner(command, name, payload, environment, timeout):
            calls.append((command, environment))
            return ContainerResult(
                0,
                b'{"output":{"summary":"ok"}}',
                b"",
            )

        executor = AgentContainerExecutor(
            settings(), runner=runner, semaphore=asyncio.Semaphore(1)
        )
        with patch.dict(
            os.environ,
            {
                "AGENT_MODEL": "stale-worker-model",
                "OPENAI_COMPATIBLE_API_KEY": "stale-worker-key",
            },
        ):
            await executor.execute(
                {"task": "Plan", "context": "{}", "skills": []},
                environment_overrides={
                    "AGENT_PROVIDER": "openrouter",
                    "AGENT_MODEL": "openai/test-model",
                    "OPENROUTER_API_KEY": "secret-key",
                },
            )

        command, environment = calls[0]
        self.assertEqual(environment["AGENT_MODEL"], "openai/test-model")
        self.assertEqual(environment["OPENROUTER_API_KEY"], "secret-key")
        self.assertNotIn("OPENAI_COMPATIBLE_API_KEY", environment)
        self.assertNotIn("secret-key", command)


class AgentRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_container_receives_selected_mcp_configuration(self):
        container = AsyncMock()
        container.execute.return_value = {"answer": "done"}
        provider, secret_store = configured_openrouter()
        server = SimpleNamespace(
            id="mcp-1",
            slug="docs",
            name="Docs MCP",
            headers_encrypted="encrypted-headers",
        )
        secret_store.decrypt.side_effect = lambda value: (
            '{"Authorization":"Bearer mcp-secret"}'
            if value == "encrypted-headers"
            else "secret-key"
        )
        graph = {
            "mcpServers": [
                {
                    "id": "mcp-1",
                    "slug": "docs",
                    "name": "Docs MCP",
                    "transport": "streamable_http",
                    "url": "https://mcp.example.com/mcp",
                    "revision": 1,
                }
            ]
        }
        execute = create_agent_executor(
            graph,
            [provider],
            secret_store,
            mcp_servers=[server],
            container_executor=container,
        )
        node = {
            "id": "agent",
            "type": "agent",
            "config": {
                "runtime": "agent",
                "instructions": "Use docs",
                "mcpServerIds": ["mcp-1"],
                "inputSchema": {},
                "outputSchema": {},
            },
        }

        self.assertEqual(await execute(node, {}), {"answer": "done"})
        environment = container.execute.await_args.kwargs["environment_overrides"]
        self.assertEqual(environment["OPENROUTER_API_KEY"], "secret-key")
        self.assertEqual(
            json.loads(environment["AGENT_MCP_SERVERS"]),
            {
                "docs": {
                    "transport": "streamable_http",
                    "url": "https://mcp.example.com/mcp",
                    "headers": {"Authorization": "Bearer mcp-secret"},
                }
            },
        )
        self.assertNotIn("mcp-secret", str(container.execute.await_args.args[0]))

    async def test_agent_container_is_the_default_runtime(self):
        container = AsyncMock()
        container.execute.return_value = {"summary": "planned"}
        skill = {
            "id": "writer",
            "slug": "clear-writing",
            "name": "Clear writing",
            "version": 2,
            "content_hash": "a" * 64,
            "instructions": "Write clearly",
        }
        graph = {"skills": [skill, {**skill, "id": "unused"}]}
        provider, secret_store = configured_openrouter()
        execute = create_agent_executor(
            graph,
            [provider],
            secret_store,
            container_executor=container,
        )
        node = {
            "id": "agent",
            "type": "agent",
            "config": {
                "instructions": "Plan {{input.topic}}",
                "skillIds": ["writer"],
                "inputSchema": {},
                "outputSchema": {},
            },
        }

        result = await execute(node, {"topic": "containers"})

        self.assertEqual(result, {"summary": "planned"})
        container.execute.assert_awaited_once_with(
            {
                "task": "Plan containers",
                "context": '{"topic": "containers"}',
                "skills": [skill],
                "output_schema": {},
            },
            environment_overrides={
                "AGENT_PROVIDER": "openrouter",
                "AGENT_MODEL": "openai/test-model",
                "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
                "OPENROUTER_API_KEY": "secret-key",
            },
        )

    async def test_agent_container_receives_the_node_output_schema(self):
        container = AsyncMock()
        container.execute.return_value = {"draft": "done"}
        output_schema = {
            "type": "object",
            "properties": {"draft": {"type": "string"}},
            "required": ["draft"],
            "additionalProperties": False,
        }
        provider, secret_store = configured_openrouter()
        execute = create_agent_executor(
            {}, [provider], secret_store, container_executor=container
        )
        node = {
            "id": "agent",
            "type": "agent",
            "config": {
                "instructions": "Write a draft",
                "inputSchema": {},
                "outputSchema": output_schema,
            },
        }

        result = await execute(node, {})

        self.assertEqual(result, {"draft": "done"})
        self.assertEqual(
            container.execute.await_args.args[0]["output_schema"], output_schema
        )

    async def test_agent_container_receives_selected_provider_model(self):
        container = AsyncMock()
        container.execute.return_value = {"draft": "done"}
        provider = SimpleNamespace(
            id="provider-1",
            name="OpenRouter",
            kind="openrouter",
            settings={"selected_models": ["openai/test-model"]},
            api_key_encrypted="encrypted-key",
        )
        secret_store = Mock()
        secret_store.decrypt.return_value = "secret-key"
        execute = create_agent_executor(
            {}, [provider], secret_store, container_executor=container
        )
        node = {
            "id": "agent",
            "type": "agent",
            "config": {
                "runtime": "agent",
                "providerId": "provider-1",
                "model": "openai/test-model",
                "instructions": "Write a draft",
                "inputSchema": {},
                "outputSchema": {},
            },
        }

        result = await execute(node, {})

        self.assertEqual(result, {"draft": "done"})
        container.execute.assert_awaited_once_with(
            {
                "task": "Write a draft",
                "context": "{}",
                "skills": [],
                "output_schema": {},
            },
            environment_overrides={
                "AGENT_PROVIDER": "openrouter",
                "AGENT_MODEL": "openai/test-model",
                "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
                "OPENROUTER_API_KEY": "secret-key",
            },
        )

    async def test_agent_container_receives_default_provider_model(self):
        container = AsyncMock()
        container.execute.return_value = {"draft": "done"}
        provider = SimpleNamespace(
            id="provider-1",
            name="OpenRouter",
            kind="openrouter",
            settings={
                "default_model": "openai/default-model",
                "selected_models": ["openai/default-model"],
            },
            api_key_encrypted="encrypted-key",
        )
        secret_store = Mock()
        secret_store.decrypt.return_value = "secret-key"
        execute = create_agent_executor(
            {}, [provider], secret_store, container_executor=container
        )
        node = {
            "id": "agent",
            "type": "agent",
            "config": {
                "runtime": "agent",
                "instructions": "Write a draft",
                "inputSchema": {},
                "outputSchema": {},
            },
        }

        result = await execute(node, {})

        self.assertEqual(result, {"draft": "done"})
        container.execute.assert_awaited_once_with(
            {
                "task": "Write a draft",
                "context": "{}",
                "skills": [],
                "output_schema": {},
            },
            environment_overrides={
                "AGENT_PROVIDER": "openrouter",
                "AGENT_MODEL": "openai/default-model",
                "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
                "OPENROUTER_API_KEY": "secret-key",
            },
        )

    async def test_agent_container_requires_a_workflow_provider(self):
        container = AsyncMock()
        execute = create_agent_executor(
            {}, [], Mock(), container_executor=container
        )
        node = {
            "id": "agent",
            "type": "agent",
            "config": {
                "runtime": "agent",
                "instructions": "Write a draft",
                "inputSchema": {},
                "outputSchema": {},
            },
        }

        with self.assertRaisesRegex(
            WorkflowExecutionError, "LLM provider is missing or disabled"
        ):
            await execute(node, {})
        container.execute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
