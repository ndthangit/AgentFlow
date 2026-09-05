import asyncio
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphRecursionError

from agent.api import create_app
from agent.contracts import AgentRunError, RunRequest
from agent.demo import DemoModel
from agent.graph import build_agent, build_configured_model
from agent.runtime import run_agent


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_graph_calls_tool_and_returns_typed_output(self):
        graph = build_agent(DemoModel())
        for task in ("first request", "second independent request"):
            state = await graph.ainvoke(
                {"messages": [{"role": "user", "content": task}]},
                config={"recursion_limit": 40},
            )
            calls = [m for m in state["messages"] if isinstance(m, ToolMessage)]
            self.assertEqual(
                [m.name for m in calls], ["get_node_catalog", "WorkflowPlan"]
            )
            self.assertIn("trigger.manual", calls[0].content)
            self.assertEqual(state["structured_response"].steps[-1].node_type, "end")
            users = [m for m in state["messages"] if m.type == "human"]
            self.assertEqual(len(users), 1)

    async def test_runtime_demo_is_explicit_and_generates_unique_ids(self):
        first, second = await asyncio.gather(
            run_agent(RunRequest(task="first"), demo=True),
            run_agent(RunRequest(task="second"), demo=True),
        )
        self.assertEqual(first.mode, "demo")
        self.assertEqual(first.status, "succeeded")
        self.assertNotEqual(first.run_id, second.run_id)

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
                await run_agent(
                    RunRequest(task="test"), demo=True, timeout_seconds=0.01
                )
        self.assertEqual(error.exception.code, "TIMEOUT")
        self.assertTrue(cancelled.is_set())

    async def test_external_cancellation_is_not_reported_as_success_or_failure(self):
        with patch("agent.runtime.build_agent") as factory:
            factory.return_value.ainvoke = AsyncMock(
                side_effect=asyncio.CancelledError()
            )
            with self.assertRaises(asyncio.CancelledError):
                await run_agent(RunRequest(task="test"), demo=True)

    async def test_error_mapping_and_provider_message_redaction(self):
        for result, error, code in [
            ({"structured_response": {"summary": "invalid"}}, None, "OUTPUT_INVALID"),
            (None, GraphRecursionError("limit"), "STEP_LIMIT"),
            (None, RuntimeError("secret-provider-response"), "AGENT_FAILED"),
        ]:
            with self.subTest(code=code), patch("agent.runtime.build_agent") as factory:
                factory.return_value.ainvoke = AsyncMock(
                    return_value=result, side_effect=error
                )
                with self.assertRaises(AgentRunError) as caught:
                    await run_agent(RunRequest(task="test"), demo=True)
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("secret-provider-response", str(caught.exception))

    async def test_http_roundtrip_and_input_validation(self):
        transport = httpx.ASGITransport(app=create_app(demo=True))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.post("/v1/runs", json={"task": "Plan a flow"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["mode"], "demo")
            self.assertEqual(
                response.json()["output"]["steps"][0]["node_type"], "trigger.manual"
            )
            for payload in (
                {"task": " "},
                {"task": "x" * 12001},
                {"task": "ok", "model": "untrusted"},
            ):
                self.assertEqual(
                    (await client.post("/v1/runs", json=payload)).status_code, 422
                )

    async def test_http_configuration_error(self):
        transport = httpx.ASGITransport(app=create_app(demo=False))
        with patch.dict(os.environ, {"AGENT_MODEL": ""}):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.post("/v1/runs", json={"task": "test"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "CONFIGURATION_ERROR")


class CliTests(unittest.TestCase):
    def test_json_stdin_stdout_and_exit_codes(self):
        for payload, expected_exit in [
            (json.dumps({"task": "Ví dụ flow"}), 0),
            ("{bad", 2),
        ]:
            with self.subTest(payload=payload):
                result = subprocess.run(
                    [sys.executable, "-m", "agent.cli", "--demo"],
                    input=payload,
                    capture_output=True,
                    encoding="utf-8",
                    timeout=30,
                    check=False,
                )
                self.assertEqual(result.returncode, expected_exit, result.stderr)
                body = json.loads(result.stdout)
                if expected_exit == 0:
                    self.assertEqual(body["status"], "succeeded")
                    self.assertEqual(body["mode"], "demo")
                else:
                    self.assertEqual(body["error"]["code"], "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main()
