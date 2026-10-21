import asyncio
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from runtime.cancellation import RunCancellationRequested
from runtime.leases import RunLeaseClaim
from runtime.worker import (
    WorkflowDeadlineExceeded,
    _execute_with_heartbeat,
    _execute_with_timeout,
)


class WorkerLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_worker_execution_cancels_the_child_runtime(self):
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def long_execution(*_args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async def never_cancel(*_args):
            await asyncio.Event().wait()

        run_id = uuid.uuid4()
        claim = RunLeaseClaim(run_id, "worker-1", 1)
        with (
            patch("runtime.worker.load_and_execute", long_execution),
            patch(
                "runtime.worker.wait_for_run_cancellation",
                never_cancel,
            ),
        ):
            task = asyncio.create_task(
                _execute_with_heartbeat(
                    run_id,
                    claim,
                    ttl_seconds=60,
                    heartbeat_seconds=30,
                )
            )
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        await asyncio.wait_for(stopped.wait(), timeout=1)

    async def test_workflow_timeout_cancels_the_child_runtime(self):
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def long_execution(*_args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async def never_cancel(*_args):
            await asyncio.Event().wait()

        run_id = uuid.uuid4()
        claim = RunLeaseClaim(run_id, "worker-1", 1)
        with (
            patch("runtime.worker.load_and_execute", long_execution),
            patch(
                "runtime.worker.wait_for_run_cancellation",
                never_cancel,
            ),
        ):
            with self.assertRaises(WorkflowDeadlineExceeded):
                await _execute_with_timeout(
                    run_id,
                    claim,
                    ttl_seconds=60,
                    heartbeat_seconds=30,
                    timeout_seconds=0.01,
                )

        await asyncio.wait_for(stopped.wait(), timeout=1)

    async def test_inner_timeout_is_not_reported_as_workflow_deadline(self):
        run_id = uuid.uuid4()
        claim = RunLeaseClaim(run_id, "worker-1", 1)
        with patch(
            "runtime.worker._execute_with_heartbeat",
            AsyncMock(side_effect=TimeoutError("provider timeout")),
        ):
            with self.assertRaisesRegex(TimeoutError, "provider timeout"):
                await _execute_with_timeout(
                    run_id,
                    claim,
                    ttl_seconds=60,
                    heartbeat_seconds=30,
                    timeout_seconds=1,
                )

    async def test_durable_cancel_command_stops_the_child_runtime(self):
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def long_execution(*_args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async def cancellation_requested(*_args, **_kwargs):
            await started.wait()

        run_id = uuid.uuid4()
        claim = RunLeaseClaim(run_id, "worker-1", 1)
        with (
            patch("runtime.worker.load_and_execute", long_execution),
            patch(
                "runtime.worker.wait_for_run_cancellation",
                cancellation_requested,
            ),
        ):
            with self.assertRaises(RunCancellationRequested):
                await _execute_with_heartbeat(
                    run_id,
                    claim,
                    ttl_seconds=60,
                    heartbeat_seconds=30,
                )

        await asyncio.wait_for(stopped.wait(), timeout=1)


if __name__ == "__main__":
    unittest.main()
