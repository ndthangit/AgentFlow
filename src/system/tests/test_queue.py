import unittest
import uuid
from unittest.mock import AsyncMock

from redis.exceptions import ResponseError

from domain.models import RunDispatch
from runtime.agent_executor import parse_agent_output
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


if __name__ == "__main__":
    unittest.main()
