import json
import tempfile
import unittest
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.runs import download_flow_run_workspace
from runtime.workspace import (
    WorkspaceError,
    WorkspaceSettings,
    delete_workspace,
    finalize_workspace,
    prepare_node,
)


def settings(root: Path) -> WorkspaceSettings:
    return WorkspaceSettings(
        root=root,
        volume="test-workspaces",
        max_total_bytes=1024,
        max_file_bytes=512,
        max_files=10,
        retention_days=7,
    )


class WorkspaceTests(unittest.TestCase):
    def test_archives_regular_files_with_manifest_and_checksum(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace_settings = settings(Path(temporary))
            run_id = uuid.uuid4()
            node = prepare_node(run_id, "writer", workspace_settings)
            (node / "report.md").write_text("ready", encoding="utf-8")

            result = finalize_workspace(run_id, workspace_settings)

            self.assertEqual(result.file_count, 1)
            self.assertEqual(result.total_bytes, 5)
            self.assertIsNotNone(result.sha256)
            archive_path = workspace_settings.resolve_archive(result.archive_key or "")
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {"nodes/writer/report.md", "manifest.json"},
                )
                manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["files"][0]["path"], "nodes/writer/report.md")
            self.assertEqual(len(manifest["files"][0]["sha256"]), 64)
            self.assertFalse(archive_path.with_suffix(".zip.tmp").exists())

    def test_rejects_paths_outside_the_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace_settings = settings(Path(temporary))
            with self.assertRaises(WorkspaceError):
                workspace_settings.node_path(uuid.uuid4(), "../outside")
            with self.assertRaises(WorkspaceError):
                workspace_settings.resolve_archive("../outside.zip")

    def test_retry_clears_only_the_current_node(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace_settings = settings(Path(temporary))
            run_id = uuid.uuid4()
            writer = prepare_node(run_id, "writer", workspace_settings)
            reviewer = prepare_node(run_id, "reviewer", workspace_settings)
            (writer / "partial.txt").write_text("partial", encoding="utf-8")
            (reviewer / "review.txt").write_text("keep", encoding="utf-8")

            prepare_node(run_id, "writer", workspace_settings)

            self.assertFalse((writer / "partial.txt").exists())
            self.assertEqual((reviewer / "review.txt").read_text(), "keep")

    def test_delete_removes_only_the_selected_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace_settings = settings(Path(temporary))
            selected = uuid.uuid4()
            retained = uuid.uuid4()
            (prepare_node(selected, "writer", workspace_settings) / "a.txt").write_text(
                "a"
            )
            (prepare_node(retained, "writer", workspace_settings) / "b.txt").write_text(
                "b"
            )
            finalize_workspace(selected, workspace_settings)

            delete_workspace(selected, workspace_settings)

            self.assertFalse(workspace_settings.run_path(selected).exists())
            self.assertFalse(
                workspace_settings.resolve_archive(
                    workspace_settings.archive_key(selected)
                ).exists()
            )
            self.assertTrue(workspace_settings.run_path(retained).exists())


class WorkspaceDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_can_download_ready_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace_settings = settings(root)
            run_id = uuid.uuid4()
            archive_key = workspace_settings.archive_key(run_id)
            archive_path = workspace_settings.resolve_archive(archive_key)
            archive_path.parent.mkdir(parents=True)
            archive_path.write_bytes(b"zip")
            flow_run = SimpleNamespace(
                workspace_status="ready",
                workspace_archive_key=archive_key,
                workspace_expires_at=datetime.now(UTC) + timedelta(days=1),
            )
            session = SimpleNamespace(scalar=AsyncMock(return_value=flow_run))

            with patch.dict(
                "os.environ",
                {
                    "WORKSPACE_ROOT": str(root),
                    "AGENT_WORKSPACE_VOLUME": "test-workspaces",
                },
                clear=False,
            ):
                response = await download_flow_run_workspace(
                    run_id, {"sub": "owner"}, session
                )

            self.assertEqual(response.media_type, "application/zip")
            self.assertIn(str(run_id)[:8], response.headers["content-disposition"])

    async def test_missing_or_foreign_workspace_returns_not_found(self):
        session = SimpleNamespace(scalar=AsyncMock(return_value=None))
        with self.assertRaises(HTTPException) as raised:
            await download_flow_run_workspace(
                uuid.uuid4(), {"sub": "other-user"}, session
            )
        self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
