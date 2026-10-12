import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from runtime.recovery import ManagedContainer, reap_orphan_containers


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


if __name__ == "__main__":
    unittest.main()
