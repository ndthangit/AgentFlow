import asyncio
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from runtime.recovery import (
    ManagedContainer,
    reap_orphan_containers,
    recovery_iteration,
    run_recovery_controller,
)


class ContainerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_reaper_preserves_only_containers_with_a_live_matching_lease(self):
        live_run = uuid.uuid4()
        stale_run = uuid.uuid4()
        containers = [
            ManagedContainer("live", live_run, 3),
            ManagedContainer("old-generation", live_run, 2),
            ManagedContainer("expired", stale_run, 1),
            ManagedContainer("legacy", None, None),
        ]
        docker = AsyncMock(return_value=(0, b"", b""))
        with (
            patch(
                "runtime.recovery.list_managed_containers",
                AsyncMock(return_value=containers),
            ),
            patch(
                "runtime.recovery._active_leases",
                AsyncMock(
                    return_value={
                        live_run: (3, datetime.now(UTC) + timedelta(minutes=1)),
                        stale_run: (1, datetime.now(UTC) - timedelta(seconds=1)),
                    }
                ),
            ),
            patch("runtime.recovery._docker", docker),
        ):
            removed, errors = await reap_orphan_containers()

        self.assertEqual(removed, 3)
        self.assertEqual(errors, [])
        removed_ids = [call.args[-1] for call in docker.await_args_list]
        self.assertEqual(removed_ids, ["old-generation", "expired", "legacy"])

    async def test_iteration_recovers_timeout_and_expired_runs(self):
        timed_out = uuid.uuid4()
        expired = uuid.uuid4()
        with (
            patch(
                "runtime.recovery.recover_timed_out_runs",
                AsyncMock(return_value=[timed_out]),
            ) as recover_timeouts,
            patch(
                "runtime.recovery.recover_expired_runs",
                AsyncMock(return_value=[expired]),
            ),
            patch(
                "runtime.recovery.reap_orphan_containers",
                AsyncMock(return_value=(0, [])),
            ),
            patch(
                "runtime.recovery.mark_cleanup_results",
                AsyncMock(),
            ),
            patch.dict("os.environ", {"WORKFLOW_TIMEOUT_SECONDS": "42"}),
        ):
            recovered, removed = await recovery_iteration()

        self.assertEqual((recovered, removed), (2, 0))
        recover_timeouts.assert_awaited_once_with(42.0)

    async def test_embedded_controller_uses_host_lifecycle_event(self):
        stop = asyncio.Event()

        async def run_once():
            stop.set()
            return 0, 0

        iteration = AsyncMock(side_effect=run_once)
        with patch("runtime.recovery.recovery_iteration", iteration):
            await run_recovery_controller(stop)

        iteration.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
