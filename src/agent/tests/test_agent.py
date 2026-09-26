import asyncio
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from langgraph.errors import GraphRecursionError

from agent.api import create_app
from agent.contracts import AgentRunError, AgentSkill, RunRequest
from agent.graph import build_configured_model
from agent.mcp import _connections_from_env, load_mcp_tools
from agent.runtime import AgentRuntime, run_agent


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_uses_requested_node_output_schema(self):
        with patch("agent.runtime.build_agent") as factory:
            factory.return_value.ainvoke = AsyncMock(
                return_value={"structured_response": {"draft": "done"}}
            )
            result = await AgentRuntime(model=object()).run(
                RunRequest(
                    task="Write a draft",
                    output_schema={
                        "type": "object",
                        "properties": {"draft": {"type": "string"}},
                        "required": ["draft"],
                        "additionalProperties": False,
                    },
                )
            )

        self.assertEqual(set(result.output), {"draft"})
        self.assertEqual(result.output["draft"], "done")
        self.assertEqual(result.mode, "live")

    async def test_selected_skills_are_added_to_system_prompt(self):
        skill = AgentSkill(
            id="skill-1",
            slug="review-output",
            name="Review Output",
            version=1,
            content_hash="a" * 64,
            instructions="Check the result before returning it.",
        )
        with patch("agent.runtime.build_agent") as factory:
            factory.return_value.ainvoke = AsyncMock(
                return_value={
                    "structured_response": {
                        "summary": "done",
                        "steps": [
                            {
                                "node_type": "end",
                                "label": "Done",
                                "instructions": "Return result",
                            }
                        ],
                        "notes": [],
                    }
                }
            )
            await AgentRuntime(model=object()).run(
                RunRequest(task="test", skills=[skill])
            )
        prompt = factory.call_args.kwargs["skill_instructions"]
        self.assertIn("Review Output", prompt)
        self.assertIn(skill.instructions, prompt)

    async def test_shared_runtime_isolates_skills_between_sessions(self):
        first_skill = AgentSkill(
            id="skill-1",
            slug="research",
            name="Research",
            version=1,
            content_hash="a" * 64,
            instructions="Use primary sources.",
        )
        second_skill = AgentSkill(
            id="skill-2",
            slug="writer",
            name="Writer",
            version=1,
            content_hash="b" * 64,
            instructions="Write a concise answer.",
        )
        response = {
            "structured_response": {
                "summary": "done",
                "steps": [
                    {
                        "node_type": "end",
                        "label": "Done",
                        "instructions": "Return result",
                    }
                ],
                "notes": [],
            }
        }

        with patch("agent.runtime.build_agent") as factory:
            factory.side_effect = [
                AsyncMock(ainvoke=AsyncMock(return_value=response)),
                AsyncMock(ainvoke=AsyncMock(return_value=response)),
            ]
            runtime = AgentRuntime(model=object())
            await asyncio.gather(
                runtime.run(RunRequest(task="research", skills=[first_skill])),
                runtime.run(RunRequest(task="write", skills=[second_skill])),
            )

        first_call, second_call = factory.call_args_list
        self.assertIs(first_call.args[0], second_call.args[0])
        first_prompt = first_call.kwargs["skill_instructions"]
        second_prompt = second_call.kwargs["skill_instructions"]
        self.assertIn(first_skill.instructions, first_prompt)
        self.assertNotIn(second_skill.instructions, first_prompt)
        self.assertIn(second_skill.instructions, second_prompt)
        self.assertNotIn(first_skill.instructions, second_prompt)

    async def test_missing_credentials_fail_without_provider_call(self):
        with (
            patch.dict(os.environ, {"AGENT_MODEL": ""}),
            self.assertRaises(AgentRunError) as error,
        ):
            await run_agent(RunRequest(task="test"))
        self.assertEqual(error.exception.code, "CONFIGURATION_ERROR")

    def test_openai_compatible_model_configuration(self):
        environment = {
            "AGENT_PROVIDER": "openai-compatible",
            "AGENT_MODEL": "local-model",
            "OPENAI_COMPATIBLE_BASE_URL": "http://127.0.0.1:9000/v1/",
            "OPENAI_COMPATIBLE_API_KEY": "local-secret",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("agent.graph.ChatOpenAI") as chat_openai,
        ):
            build_configured_model()
        chat_openai.assert_called_once_with(
            model="local-model",
            base_url="http://127.0.0.1:9000/v1",
            api_key="local-secret",
            max_tokens=2048,
            timeout=60.0,
            max_retries=1,
        )

    def test_openai_compatible_allows_server_without_api_key(self):
        environment = {
            "AGENT_PROVIDER": "openai-compatible",
            "AGENT_MODEL": "local-model",
            "OPENAI_COMPATIBLE_BASE_URL": "http://localhost:8000/v1",
            "OPENAI_COMPATIBLE_API_KEY": "",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("agent.graph.ChatOpenAI") as chat_openai,
        ):
            build_configured_model()
        self.assertEqual(chat_openai.call_args.kwargs["api_key"], "not-required")

    def test_openai_compatible_requires_base_url(self):
        environment = {
            "AGENT_PROVIDER": "openai-compatible",
            "AGENT_MODEL": "local-model",
            "OPENAI_COMPATIBLE_BASE_URL": "",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            self.assertRaises(AgentRunError) as error,
        ):
            build_configured_model()
        self.assertEqual(error.exception.code, "CONFIGURATION_ERROR")

    def test_openrouter_accepts_native_environment_variables(self):
        environment = {
            "AGENT_PROVIDER": "openrouter",
            "AGENT_MODEL": "openai/test-model",
            "OPENROUTER_API_KEY": "openrouter-secret",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("agent.graph.ChatOpenAI") as chat_openai,
        ):
            build_configured_model()
        chat_openai.assert_called_once_with(
            model="openai/test-model",
            base_url="https://openrouter.ai/api/v1",
            api_key="openrouter-secret",
            max_tokens=2048,
            timeout=60.0,
            max_retries=1,
        )

    def test_openrouter_requires_api_key(self):
        environment = {
            "AGENT_PROVIDER": "openrouter",
            "AGENT_MODEL": "openai/test-model",
            "OPENROUTER_API_KEY": "",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            self.assertRaises(AgentRunError) as error,
        ):
            build_configured_model()
        self.assertEqual(error.exception.code, "CONFIGURATION_ERROR")

    async def test_deadline_cancels_graph(self):
        cancelled = asyncio.Event()

        async def slow(*args, **kwargs):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

        with patch("agent.runtime.build_agent") as factory:
            factory.return_value.ainvoke = slow
            with self.assertRaises(AgentRunError) as error:
                await AgentRuntime(model=object()).run(
                    RunRequest(task="test"), timeout_seconds=0.01
                )
        self.assertEqual(error.exception.code, "TIMEOUT")
        self.assertTrue(cancelled.is_set())

    async def test_deadline_also_bounds_mcp_tool_discovery(self):
        async def slow_discovery():
            await asyncio.sleep(60)

        with (
            patch("agent.runtime.load_mcp_tools", side_effect=slow_discovery),
            self.assertRaises(AgentRunError) as error,
        ):
            await AgentRuntime(model=object()).run(
                RunRequest(task="test"), timeout_seconds=0.01
            )

        self.assertEqual(error.exception.code, "TIMEOUT")

    async def test_external_cancellation_is_not_reported_as_success_or_failure(self):
        with patch("agent.runtime.build_agent") as factory:
            factory.return_value.ainvoke = AsyncMock(
                side_effect=asyncio.CancelledError()
            )
            with self.assertRaises(asyncio.CancelledError):
                await AgentRuntime(model=object()).run(RunRequest(task="test"))

    async def test_error_mapping_and_provider_message_redaction(self):
        for result, error, code in [
            ({"structured_response": {"summary": "invalid"}}, None, "OUTPUT_INVALID"),
            (None, GraphRecursionError("limit"), "STEP_LIMIT"),
            (
                None,
                ValueError(
                    {
                        "message": "secret-provider-response",
                        "code": 503,
                        "metadata": {"error_type": "provider_overloaded"},
                    }
                ),
                "PROVIDER_OVERLOADED",
            ),
            (None, RuntimeError("secret-provider-response"), "AGENT_FAILED"),
        ]:
            with self.subTest(code=code), patch("agent.runtime.build_agent") as factory:
                factory.return_value.ainvoke = AsyncMock(
                    return_value=result, side_effect=error
                )
                with self.assertRaises(AgentRunError) as caught:
                    await AgentRuntime(model=object()).run(RunRequest(task="test"))
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("secret-provider-response", str(caught.exception))

    async def test_http_roundtrip_and_input_validation(self):
        with patch("agent.runtime.build_agent") as factory:
            factory.return_value.ainvoke = AsyncMock(
                return_value={
                    "structured_response": {
                        "summary": "done",
                        "steps": [
                            {
                                "node_type": "end",
                                "label": "Done",
                                "instructions": "Return result",
                            }
                        ],
                        "notes": [],
                    }
                }
            )
            with patch("agent.runtime.build_configured_model", return_value=object()):
                transport = httpx.ASGITransport(app=create_app())
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://test"
                ) as client:
                    response = await client.post(
                        "/v1/runs", json={"task": "Plan a flow"}
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["mode"], "live")
                    for payload in (
                        {"task": " "},
                        {"task": "x" * 12001},
                        {"task": "ok", "model": "untrusted"},
                    ):
                        self.assertEqual(
                            (await client.post("/v1/runs", json=payload)).status_code,
                            422,
                        )

    async def test_http_configuration_error(self):
        transport = httpx.ASGITransport(app=create_app())
        with patch.dict(os.environ, {"AGENT_MODEL": ""}):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.post("/v1/runs", json={"task": "test"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "CONFIGURATION_ERROR")

    async def test_http_requires_and_verifies_bearer_token(self):
        environment = {
            "AUTH_ENABLED": "true",
            "OIDC_ISSUER": "http://issuer/realms/agentflow",
            "OIDC_AUDIENCE": "agentflow-api",
        }
        with (
            patch.dict(os.environ, environment),
            patch("agent.api.KeycloakTokenVerifier") as verifier_class,
        ):
            verifier_class.return_value.verify.return_value = {"sub": "user-1"}
            with (
                patch("agent.runtime.build_agent") as factory,
                patch("agent.runtime.build_configured_model", return_value=object()),
            ):
                factory.return_value.ainvoke = AsyncMock(
                    return_value={
                        "structured_response": {
                            "summary": "done",
                            "steps": [
                                {
                                    "node_type": "end",
                                    "label": "Done",
                                    "instructions": "Return result",
                                }
                            ],
                            "notes": [],
                        }
                    }
                )
                transport = httpx.ASGITransport(app=create_app())
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://test"
                ) as client:
                    missing = await client.post("/v1/runs", json={"task": "test"})
                    accepted = await client.post(
                        "/v1/runs",
                        json={"task": "test"},
                        headers={"Authorization": "Bearer signed-token"},
                    )
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(accepted.status_code, 200)
        verifier_class.return_value.verify.assert_called_once_with("signed-token")


class McpToolTests(unittest.IsolatedAsyncioTestCase):
    def test_connections_are_loaded_from_invocation_environment(self):
        raw = json.dumps(
            {
                "github-tools": {
                    "transport": "streamable_http",
                    "url": "https://mcp.example.com/mcp",
                    "headers": {"Authorization": "Bearer secret"},
                }
            }
        )
        with patch.dict(os.environ, {"AGENT_MCP_SERVERS": raw}, clear=True):
            self.assertEqual(
                _connections_from_env(),
                {
                    "github-tools": {
                        "transport": "streamable_http",
                        "url": "https://mcp.example.com/mcp",
                        "headers": {"Authorization": "Bearer secret"},
                    }
                },
            )

    async def test_selected_mcp_tools_are_loaded_with_server_prefixes(self):
        raw = json.dumps(
            {
                "docs": {
                    "transport": "sse",
                    "url": "https://mcp.example.com/sse",
                }
            }
        )
        client = Mock()
        client.get_tools = AsyncMock(return_value=[object()])
        with (
            patch.dict(os.environ, {"AGENT_MCP_SERVERS": raw}, clear=True),
            patch("agent.mcp.MultiServerMCPClient", return_value=client) as factory,
        ):
            tools = await load_mcp_tools()

        self.assertEqual(len(tools), 1)
        factory.assert_called_once_with(
            {
                "docs": {
                    "transport": "sse",
                    "url": "https://mcp.example.com/sse",
                }
            },
            tool_name_prefix=True,
            handle_tool_errors=True,
        )

    async def test_mcp_connection_errors_are_public_and_stable(self):
        raw = json.dumps(
            {
                "docs": {
                    "transport": "streamable_http",
                    "url": "https://mcp.example.com/mcp",
                }
            }
        )
        client = Mock()
        client.get_tools = AsyncMock(side_effect=RuntimeError("private failure"))
        with (
            patch.dict(os.environ, {"AGENT_MCP_SERVERS": raw}, clear=True),
            patch("agent.mcp.MultiServerMCPClient", return_value=client),
            self.assertRaises(AgentRunError) as caught,
        ):
            await load_mcp_tools()

        self.assertEqual(caught.exception.code, "MCP_CONNECTION_FAILED")
        self.assertNotIn("private failure", str(caught.exception))


class CliTests(unittest.TestCase):
    def test_demo_flag_is_not_supported(self):
        result = subprocess.run(
            [sys.executable, "-m", "agent.cli", "--demo"],
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments: --demo", result.stderr)

    def test_json_stdin_stdout_and_exit_codes(self):
        for payload, expected_exit, expected_code in [
            (json.dumps({"task": "Ví dụ flow"}), 1, "CONFIGURATION_ERROR"),
            ("{bad", 2, "INVALID_INPUT"),
        ]:
            with self.subTest(payload=payload):
                environment = {**os.environ, "AGENT_MODEL": ""}
                result = subprocess.run(
                    [sys.executable, "-m", "agent.cli"],
                    input=payload,
                    capture_output=True,
                    encoding="utf-8",
                    env=environment,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(result.returncode, expected_exit, result.stderr)
                body = json.loads(result.stdout)
                self.assertEqual(body["error"]["code"], expected_code)


if __name__ == "__main__":
    unittest.main()
