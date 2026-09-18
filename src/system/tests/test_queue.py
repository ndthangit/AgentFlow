import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from redis.exceptions import ResponseError

from domain.models import RunDispatch
from providers.adapters import ChatCompletionResult, ChatMessage
from runtime.agent_executor import (
    create_llm_executor,
    parse_agent_output,
    render_prompt,
)
from runtime.orchestrator import dispatch_batch
from runtime.queue import RedisRunQueue


class RunQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_consumer_group_creation_is_idempotent(self):
        client = AsyncMock()
        client.xgroup_create.side_effect = ResponseError(
            "BUSYGROUP Consumer Group name already exists"
        )
        queue = RedisRunQueue(client)

        await queue.ensure_group()

        client.xgroup_create.assert_awaited_once()

    async def test_reads_new_and_reclaimed_stream_messages(self):
        client = AsyncMock()
        client.xreadgroup.return_value = [
            ["agentflow:workflow-runs", [["1-0", {"run_id": "run-new"}]]]
        ]
        client.xautoclaim.return_value = [
            "0-0",
            [["2-0", {"run_id": "run-reclaimed"}]],
            [],
        ]
        queue = RedisRunQueue(client)

        new_messages = await queue.read("worker-1")
        reclaimed = await queue.reclaim("worker-1")

        self.assertEqual(new_messages[0].run_id, "run-new")
        self.assertFalse(new_messages[0].reclaimed)
        self.assertEqual(reclaimed[0].run_id, "run-reclaimed")
        self.assertTrue(reclaimed[0].reclaimed)


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatches_outbox_row_and_marks_it_complete(self):
        dispatch = RunDispatch(run_id=uuid.uuid4())
        session = AsyncMock()
        session.scalars.return_value = [dispatch]
        queue = AsyncMock()
        queue.enqueue.return_value = "1-0"

        count = await dispatch_batch(session, queue)

        self.assertEqual(count, 1)
        self.assertEqual(dispatch.attempts, 1)
        self.assertIsNotNone(dispatch.dispatched_at)
        self.assertIsNone(dispatch.last_error)
        queue.enqueue.assert_awaited_once_with(str(dispatch.run_id))
        session.commit.assert_awaited_once()

    async def test_redis_failure_keeps_outbox_row_retryable(self):
        dispatch = RunDispatch(run_id=uuid.uuid4())
        session = AsyncMock()
        session.scalars.return_value = [dispatch]
        queue = AsyncMock()
        queue.enqueue.side_effect = ConnectionError("redis unavailable")

        count = await dispatch_batch(session, queue)

        self.assertEqual(count, 0)
        self.assertIsNone(dispatch.dispatched_at)
        self.assertEqual(dispatch.last_error, "redis unavailable")
        session.commit.assert_awaited_once()


class AgentOutputTests(unittest.TestCase):
    def test_parses_json_inside_markdown_fence(self):
        self.assertEqual(
            parse_agent_output('```json\n{"answer": 1}\n```'), {"answer": 1}
        )

    def test_renders_nested_prompt_input_references(self):
        self.assertEqual(
            render_prompt(
                "Title: {{ input.article.title }}; tags: {{input.tags}}",
                {"article": {"title": "AgentFlow"}, "tags": ["ai", "flow"]},
            ),
            'Title: AgentFlow; tags: ["ai", "flow"]',
        )

    def test_rejects_missing_prompt_input_reference(self):
        with self.assertRaisesRegex(
            ValueError, "Prompt input reference is not available"
        ):
            render_prompt("Summarize {{input.missing}}", {"text": "content"})


class LlmCallExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_performs_one_completion_without_workflow_skills(self):
        provider_id = uuid.uuid4()
        provider = SimpleNamespace(
            id=provider_id,
            name="OpenRouter",
            kind="openrouter",
            settings={
                "default_model": "openai/test-model",
                "selected_models": ["openai/test-model"],
            },
            api_key_encrypted="encrypted",
        )
        adapter = AsyncMock()
        adapter.complete.return_value = ChatCompletionResult(
            id="completion-1",
            model="openai/test-model",
            message=ChatMessage(role="assistant", content='{"summary": "Short"}'),
        )
        secret_store = Mock()
        secret_store.decrypt.return_value = "secret"
        graph = {
            "skills": [
                {"id": "writer", "instructions": "THIS SKILL MUST NOT BE LOADED"}
            ]
        }
        node = {
            "id": "summarize",
            "type": "llm.call",
            "config": {
                "prompt": "Summarize the input: {{input.text}}",
                "providerId": str(provider_id),
                "model": "openai/test-model",
                "outputSchema": {"type": "object"},
            },
        }

        with patch(
            "runtime.agent_executor.create_provider_adapter", return_value=adapter
        ):
            execute_llm = create_llm_executor(graph, [provider], secret_store)
            result = await execute_llm(node, {"text": "Long text"})

        self.assertEqual(result, {"summary": "Short"})
        adapter.complete.assert_awaited_once()
        request = adapter.complete.await_args.args[0]
        self.assertIn("Summarize the input", request.messages[0].content)
        self.assertIn("Long text", request.messages[0].content)
        self.assertNotIn("{{input.text}}", request.messages[0].content)
        self.assertNotIn("THIS SKILL MUST NOT BE LOADED", request.messages[0].content)


if __name__ == "__main__":
    unittest.main()
