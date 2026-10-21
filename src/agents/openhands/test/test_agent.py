import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace

AGENT_PATH = Path(__file__).resolve().parents[1] / "agent.py"
SPEC = importlib.util.spec_from_file_location("agentflow_openhands_agent", AGENT_PATH)
assert SPEC is not None and SPEC.loader is not None
agent = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = agent
SPEC.loader.exec_module(agent)


ENVIRONMENT = {
    "AGENT_PROVIDER": "openrouter",
    "AGENT_MODEL": "openai/gpt-5.1",
    "AGENT_PROVIDER_BASE_URL": "https://openrouter.ai/api/v1",
    "AGENT_PROVIDER_API_KEY": "managed-secret",
    "AGENT_RUNTIME_TIMEOUT_SECONDS": "180",
    "AGENT_RUNTIME_MAX_STEPS": "6",
}

REQUEST = {
    "task": "Create a plan",
    "context": '{"topic":"runtime"}',
    "skills": [{"name": "Clear writing", "instructions": "Be concise"}],
    "output_schema": {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": False,
    },
}


class OpenHandsAgentTests(unittest.TestCase):
    def test_prompt_contains_task_context_skill_and_finish_instruction(self):
        prompt = agent.build_prompt(REQUEST)
        self.assertIn("Create a plan", prompt)
        self.assertIn("runtime", prompt)
        self.assertIn("Be concise", prompt)
        self.assertIn("call the finish tool exactly once", prompt)
        self.assertIn("If tool calling is unavailable", prompt)

    def test_json_message_fallback_accepts_bare_or_fenced_object(self):
        expected = {"summary": "ready"}
        self.assertEqual(
            agent.parse_json_object_response('{"summary":"ready"}'), expected
        )
        self.assertEqual(
            agent.parse_json_object_response('```json\n{"summary":"ready"}\n```'),
            expected,
        )

    def test_json_message_fallback_rejects_prose_and_arrays(self):
        self.assertIsNone(agent.parse_json_object_response('Result: {"summary":"x"}'))
        self.assertIsNone(agent.parse_json_object_response('[{"summary":"x"}]'))

    def test_final_message_fallback_reads_sdk_text_content(self):
        from openhands.sdk.event import MessageEvent
        from openhands.sdk.llm import Message, TextContent

        event = MessageEvent(
            source="agent",
            llm_message=Message(
                role="assistant",
                content=[TextContent(text='{"result":{"summary":"ready"}}')],
            ),
        )
        self.assertEqual(
            agent.final_message_result([event]), {"summary": "ready"}
        )

    def test_schema_is_nested_away_from_finish_tool_reserved_names(self):
        schema = agent.wrapped_response_schema(
            {
                "type": "object",
                "properties": {"kind": {"type": "string"}},
                "required": ["kind"],
            }
        )
        self.assertEqual(schema["required"], ["result"])
        self.assertIn("kind", schema["properties"]["result"]["properties"])

    def test_sdk_agent_initializes_with_one_schema_aware_finish_tool(self):
        from openhands.sdk import Conversation

        config = agent.build_runtime_config(ENVIRONMENT)
        openhands_agent = agent.build_openhands_agent(REQUEST, config)
        with tempfile.TemporaryDirectory() as workspace:
            conversation = Conversation(
                agent=openhands_agent,
                workspace=workspace,
                visualizer=None,
            )
            try:
                conversation.send_message("Initialize the agent without running it")
                self.assertEqual(set(conversation.agent.tools_map), {"finish"})
                finish_tool = agent.conversation_finish_tool(conversation)
                self.assertTrue(hasattr(finish_tool, "parse_last_response"))
            finally:
                conversation.close()

    def test_finish_tool_is_read_from_the_conversation_owned_agent(self):
        marker = object()
        conversation = SimpleNamespace(
            agent=SimpleNamespace(tools_map={"finish": marker})
        )
        self.assertIs(agent.conversation_finish_tool(conversation), marker)

    def test_mcp_configuration_accepts_streamable_http(self):
        raw = json.dumps(
            {
                "filesystem-demo": {
                    "transport": "streamable_http",
                    "url": "http://filesystem-mcp:8002/mcp",
                    "headers": {"Authorization": "Bearer secret"},
                }
            }
        )
        config = agent.build_mcp_configuration(raw)
        self.assertEqual(
            config["filesystem-demo"]["url"],
            "http://filesystem-mcp:8002/mcp",
        )
        self.assertEqual(
            config["filesystem-demo"]["headers"]["Authorization"],
            "Bearer secret",
        )

    def test_mcp_configuration_rejects_sse(self):
        raw = json.dumps(
            {"legacy": {"transport": "sse", "url": "https://example.test/sse"}}
        )
        with self.assertRaisesRegex(agent.PublicAgentError, "Streamable HTTP"):
            agent.build_mcp_configuration(raw)

    def test_execute_returns_agentflow_success_envelope(self):
        def fake_runner(request, config):
            self.assertEqual(request["task"], "Create a plan")
            self.assertEqual(config.model, "openrouter/openai/gpt-5.1")
            self.assertEqual(config.max_steps, 6)
            self.assertEqual(config.deadline_seconds, 160)
            self.assertEqual(config.api_key, "managed-secret")
            return {"summary": "ready"}

        result = agent.execute_request(REQUEST, ENVIRONMENT, fake_runner)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["mode"], "live")
        self.assertEqual(result["output"], {"summary": "ready"})

    def test_execute_rejects_output_that_misses_schema(self):
        with self.assertRaisesRegex(agent.PublicAgentError, "required schema") as caught:
            agent.execute_request(REQUEST, ENVIRONMENT, lambda _request, _config: {})
        self.assertEqual(caught.exception.code, "OUTPUT_INVALID")

    def test_missing_managed_key_fails_before_runner(self):
        environment = {**ENVIRONMENT, "AGENT_PROVIDER_API_KEY": ""}
        with self.assertRaises(agent.PublicAgentError) as caught:
            agent.execute_request(REQUEST, environment, lambda _request, _config: {})
        self.assertEqual(caught.exception.code, "CONFIGURATION_ERROR")

    def test_step_limit_is_bounded(self):
        environment = {**ENVIRONMENT, "AGENT_RUNTIME_MAX_STEPS": "100"}
        with self.assertRaises(agent.PublicAgentError) as caught:
            agent.build_runtime_config(environment)
        self.assertEqual(caught.exception.code, "CONFIGURATION_ERROR")

    def test_maps_provider_auth_without_leaking_details(self):
        mapped = agent.map_openhands_failure(RuntimeError("401 secret upstream body"))
        self.assertEqual(mapped.code, "PROVIDER_AUTH_ERROR")
        self.assertNotIn("secret", str(mapped))

    def test_maps_nested_provider_failure(self):
        try:
            try:
                raise RuntimeError("429 provider detail")
            except RuntimeError as exc:
                raise ValueError("conversation failed") from exc
        except ValueError as exc:
            mapped = agent.map_openhands_failure(exc)
        self.assertEqual(mapped.code, "RATE_LIMITED")

    def test_preserves_sanitized_provider_detail(self):
        mapped = agent.map_openhands_failure(
            RuntimeError(
                "[Nvidia] Function function-id not found for account account-id "
                "using managed-secret"
            ),
            ENVIRONMENT,
        )

        self.assertEqual(mapped.code, "AGENT_FAILED")
        self.assertIn("[Nvidia] Function function-id not found", mapped.detail)
        self.assertIn("account [REDACTED]", mapped.detail)
        self.assertNotIn("account-id", mapped.detail)
        self.assertNotIn("managed-secret", mapped.detail)

    def test_failure_diagnostic_contains_types_but_not_messages(self):
        try:
            try:
                raise RuntimeError("secret provider body")
            except RuntimeError as exc:
                raise ValueError("outer secret") from exc
        except ValueError as exc:
            stream = io.StringIO()
            with redirect_stderr(stream):
                agent.emit_failure_diagnostic(
                    exc,
                    agent.PublicAgentError("AGENT_FAILED", "Safe message"),
                )
        diagnostic = json.loads(stream.getvalue())
        self.assertEqual(diagnostic["status"], "ValueError>RuntimeError")
        self.assertEqual(diagnostic["error_code"], "AGENT_FAILED")
        self.assertNotIn("secret", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
