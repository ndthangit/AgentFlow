import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from api.runs import cancel_flow_run
from domain.models import RunCommandStatus
from domain.schemas import FlowRunCancel
from runtime.cancellation import wait_for_run_cancellation


class RunCancellationApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_run_is_cancelled_immediately(self):
        run_id = uuid.uuid4()
        flow_run = SimpleNamespace(id=run_id, status="pending")
        session = SimpleNamespace(
            scalar=AsyncMock(side_effect=[flow_run, None]),
            add=Mock(),
            execute=AsyncMock(),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )

        result = await cancel_flow_run(
            run_id,
            FlowRunCancel(idempotency_key="stop-pending"),
            {"sub": "owner-1"},
            session,
        )

        self.assertIs(result, flow_run)
        self.assertEqual(flow_run.status, "cancelled")
        command = session.add.call_args.args[0]
        self.assertEqual(command.status, RunCommandStatus.APPLIED)
        self.assertEqual(command.result, {"status": "cancelled"})
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()

    async def test_running_run_records_an_accepted_cancel_command(self):
        run_id = uuid.uuid4()
        flow_run = SimpleNamespace(id=run_id, status="running")
        session = SimpleNamespace(
            scalar=AsyncMock(side_effect=[flow_run, None]),
            add=Mock(),
            execute=AsyncMock(),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )

        publish = AsyncMock(return_value=1)
        with patch("api.runs.publish_run_cancellation", publish):
            result = await cancel_flow_run(
                run_id,
                FlowRunCancel(idempotency_key="stop-running"),
                {"sub": "owner-1"},
                session,
            )

        self.assertIs(result, flow_run)
        self.assertEqual(flow_run.status, "running")
        command = session.add.call_args.args[0]
        self.assertEqual(command.status, RunCommandStatus.ACCEPTED)
        session.execute.assert_not_awaited()
        session.commit.assert_awaited_once()
        publish.assert_awaited_once_with(run_id)


class CancellationEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_waits_for_redis_event_without_database_polling(self):
        class FakePubSub:
            def __init__(self):
                self.subscribe = AsyncMock()
                self.aclose = AsyncMock()

            async def listen(self):
                yield {"type": "subscribe"}
                yield {"type": "message", "data": "stop"}

        pubsub = FakePubSub()
        client = SimpleNamespace(pubsub=Mock(return_value=pubsub), aclose=AsyncMock())
        durable_check = AsyncMock(side_effect=[False, False])
        run_id = uuid.uuid4()

        with (
            patch(
                "runtime.cancellation._cancellation_is_durable",
                durable_check,
            ),
            patch("runtime.cancellation.Redis.from_url", return_value=client),
        ):
            await wait_for_run_cancellation(run_id)

        self.assertEqual(durable_check.await_count, 2)
        pubsub.subscribe.assert_awaited_once()
        pubsub.aclose.assert_awaited_once()
        client.aclose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
