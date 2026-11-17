"""Persistent, bounded workspaces for Agent container output."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

NODE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class WorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkspaceArchive:
    archive_key: str | None
    file_count: int
    total_bytes: int
    sha256: str | None
    expires_at: datetime | None


@dataclass(frozen=True)
class WorkspaceSettings:
    root: Path
    volume: str
    max_total_bytes: int
    max_file_bytes: int
    max_files: int
    retention_days: int
    max_depth: int = 20

    @classmethod
    def from_env(cls) -> WorkspaceSettings:
        return cls(
            root=Path(os.getenv("WORKSPACE_ROOT", "/var/lib/agentflow/workspaces")),
            volume=os.getenv("AGENT_WORKSPACE_VOLUME", "agentflow-workspaces"),
            max_total_bytes=int(os.getenv("WORKSPACE_MAX_TOTAL_BYTES", "104857600")),
            max_file_bytes=int(os.getenv("WORKSPACE_MAX_FILE_BYTES", "26214400")),
            max_files=int(os.getenv("WORKSPACE_MAX_FILES", "1000")),
            retention_days=int(os.getenv("WORKSPACE_RETENTION_DAYS", "7")),
        )

    def run_path(self, run_id: uuid.UUID) -> Path:
        return self.root / "runs" / str(run_id)

    def node_path(self, run_id: uuid.UUID, node_id: str) -> Path:
        if not NODE_ID_PATTERN.fullmatch(node_id):
            raise WorkspaceError("Invalid Agent node id")
        return self.run_path(run_id) / "nodes" / node_id

    def archive_key(self, run_id: uuid.UUID) -> str:
        return f"archives/{run_id}/workspace.zip"

    def resolve_archive(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        archive_root = (self.root / "archives").resolve()
        if not candidate.is_relative_to(archive_root):
            raise WorkspaceError("Invalid workspace archive key")
        return candidate


def prepare_run(run_id: uuid.UUID, settings: WorkspaceSettings) -> Path:
    run_path = settings.run_path(run_id)
    (run_path / "nodes").mkdir(parents=True, exist_ok=True)
    shared_path = run_path / "shared"
    shared_path.mkdir(exist_ok=True)
    try:
        os.chown(shared_path, 10001, 10001)
    except (AttributeError, PermissionError):
        pass
    return run_path


def prepare_node(run_id: uuid.UUID, node_id: str, settings: WorkspaceSettings) -> Path:
    prepare_run(run_id, settings)
    node_path = settings.node_path(run_id, node_id)
    if node_path.exists():
        shutil.rmtree(node_path)
    node_path.mkdir()
    try:
        os.chown(node_path, 10001, 10001)
    except (AttributeError, PermissionError):
        pass
    return node_path


def _workspace_files(
    run_path: Path, settings: WorkspaceSettings
) -> list[tuple[Path, str, int, str]]:
    files: list[tuple[Path, str, int, str]] = []
    total_bytes = 0
    if not run_path.exists():
        return files
    for root, directories, names in os.walk(run_path, followlinks=False):
        root_path = Path(root)
        depth = len(root_path.relative_to(run_path).parts)
        if depth > settings.max_depth:
            raise WorkspaceError("Workspace path depth limit exceeded")
        for directory in directories:
            mode = (root_path / directory).lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise WorkspaceError(
                    "Workspace contains an unsupported directory entry"
                )
        for name in names:
            path = root_path / name
            if path == run_path / "manifest.json":
                metadata = path.lstat()
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise WorkspaceError(
                        "Workspace contains an unsupported manifest entry"
                    )
                continue
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise WorkspaceError("Workspace contains an unsupported file entry")
            if metadata.st_size > settings.max_file_bytes:
                raise WorkspaceError("Workspace file size limit exceeded")
            total_bytes += metadata.st_size
            if total_bytes > settings.max_total_bytes:
                raise WorkspaceError("Workspace total size limit exceeded")
            if len(files) >= settings.max_files:
                raise WorkspaceError("Workspace file count limit exceeded")
            digest = hashlib.sha256()
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(path, flags), "rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            files.append(
                (
                    path,
                    path.relative_to(run_path).as_posix(),
                    metadata.st_size,
                    digest.hexdigest(),
                )
            )
    return files


def finalize_workspace(
    run_id: uuid.UUID, settings: WorkspaceSettings
) -> WorkspaceArchive:
    run_path = settings.run_path(run_id)
    files = _workspace_files(run_path, settings)
    if not files:
        shutil.rmtree(run_path, ignore_errors=True)
        return WorkspaceArchive(None, 0, 0, None, None)

    manifest = {
        "run_id": str(run_id),
        "file_count": len(files),
        "total_bytes": sum(item[2] for item in files),
        "files": [
            {"path": relative, "size": size, "sha256": digest}
            for _path, relative, size, digest in files
        ],
    }
    manifest_path = run_path / "manifest.json"
    manifest_path.unlink(missing_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    archive_key = settings.archive_key(run_id)
    archive_path = settings.resolve_archive(archive_key)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive_path.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for path, relative, _size, _digest in files:
                archive.write(path, relative)
            archive.write(manifest_path, "manifest.json")
        os.replace(temporary, archive_path)
    finally:
        temporary.unlink(missing_ok=True)

    digest = hashlib.sha256()
    with archive_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return WorkspaceArchive(
        archive_key,
        len(files),
        manifest["total_bytes"],
        digest.hexdigest(),
        datetime.now(UTC) + timedelta(days=settings.retention_days),
    )


def delete_workspace(run_id: uuid.UUID, settings: WorkspaceSettings) -> None:
    shutil.rmtree(settings.run_path(run_id), ignore_errors=True)
    shutil.rmtree(
        settings.resolve_archive(settings.archive_key(run_id)).parent,
        ignore_errors=True,
    )
