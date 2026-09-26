"""A small Streamable HTTP MCP server confined to one data directory."""

import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

MAX_FILE_BYTES = 1024 * 1024
MAX_LIST_ENTRIES = 200
FILE_ROOT = Path(os.getenv("MCP_FILE_ROOT", "/data")).resolve()

mcp = FastMCP(
    "AgentFlow Filesystem Demo",
    instructions=(
        "Read, write and list UTF-8 files inside the isolated demo data directory. "
        "All paths must be relative."
    ),
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8002")),
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    max_request_body_size=2 * 1024 * 1024,
)


def _safe_path(relative_path: str, *, allow_root: bool = False) -> Path:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("path must be a non-empty relative path")
    requested = Path(relative_path.strip())
    if requested.is_absolute():
        raise ValueError("absolute paths are not allowed")
    target = (FILE_ROOT / requested).resolve()
    try:
        target.relative_to(FILE_ROOT)
    except ValueError:
        raise ValueError("path must stay inside the MCP data directory") from None
    if target == FILE_ROOT and not allow_root:
        raise ValueError("path must identify a file")
    return target


@mcp.tool()
def write_file(path: str, content: str, overwrite: bool = True) -> dict[str, Any]:
    """Write UTF-8 text to a relative path in the isolated demo directory."""
    if not isinstance(content, str):
        raise TypeError("content must be text")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise ValueError("content exceeds the 1 MiB demo limit")
    target = _safe_path(path)
    if target.exists() and not overwrite:
        raise FileExistsError(f"file already exists: {path}")
    if target.exists() and not target.is_file():
        raise ValueError("path does not identify a regular file")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(encoded)
    return {"path": target.relative_to(FILE_ROOT).as_posix(), "bytes": len(encoded)}


@mcp.tool()
def read_file(path: str) -> dict[str, Any]:
    """Read a UTF-8 text file from a relative path in the demo directory."""
    target = _safe_path(path)
    if not target.is_file():
        raise FileNotFoundError(f"file not found: {path}")
    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError("file exceeds the 1 MiB demo limit")
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError("file is not valid UTF-8 text") from None
    return {"path": target.relative_to(FILE_ROOT).as_posix(), "content": content}


@mcp.tool()
def list_files(directory: str = ".") -> dict[str, Any]:
    """List up to 200 files below a relative directory in the demo data root."""
    target = _safe_path(directory, allow_root=True)
    if not target.is_dir():
        raise NotADirectoryError(f"directory not found: {directory}")
    files = sorted(
        path.relative_to(FILE_ROOT).as_posix()
        for path in target.rglob("*")
        if path.is_file()
    )
    truncated = len(files) > MAX_LIST_ENTRIES
    return {"files": files[:MAX_LIST_ENTRIES], "truncated": truncated}


def main() -> None:
    FILE_ROOT.mkdir(parents=True, exist_ok=True)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
