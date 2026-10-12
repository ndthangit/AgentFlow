import asyncio
import unittest
import uuid
from unittest.mock import patch

from runtime.leases import RunLeaseClaim
from runtime.worker import _execute_with_heartbeat


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

        run_id = uuid.uuid4()
        claim = RunLeaseClaim(run_id, "worker-1", 1)
        with patch("runtime.worker.load_and_execute", long_execution):
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


if __name__ == "__main__":
    unittest.main()
