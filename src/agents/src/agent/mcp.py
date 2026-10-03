"""Load remote MCP tools from the invocation-scoped environment."""

import json
import os
import re
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.contracts import AgentRunError

SERVER_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_MCP_SERVERS = 20
MAX_MCP_CONFIG_BYTES = 128 * 1024


def _connections_from_env() -> dict[str, dict[str, Any]]:
    raw = os.getenv("AGENT_MCP_SERVERS", "").strip()
    if not raw:
        return {}
    if len(raw.encode("utf-8")) > MAX_MCP_CONFIG_BYTES:
        raise AgentRunError(
            "CONFIGURATION_ERROR", "MCP server configuration exceeds 128 KiB."
        )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise AgentRunError(
            "CONFIGURATION_ERROR", "MCP server configuration is invalid JSON."
        ) from None
    if not isinstance(value, dict) or len(value) > MAX_MCP_SERVERS:
        raise AgentRunError(
            "CONFIGURATION_ERROR", "MCP server configuration is invalid."
        )
    connections: dict[str, dict[str, Any]] = {}
    for name, connection in value.items():
        if (
            not isinstance(name, str)
            or not SERVER_NAME_PATTERN.fullmatch(name)
            or not isinstance(connection, dict)
            or connection.get("transport") not in {"streamable_http", "sse"}
            or not isinstance(connection.get("url"), str)
        ):
            raise AgentRunError(
                "CONFIGURATION_ERROR", "MCP server configuration is invalid."
            )
        headers = connection.get("headers", {})
        if not isinstance(headers, dict) or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in headers.items()
        ):
            raise AgentRunError(
                "CONFIGURATION_ERROR", "MCP server headers are invalid."
            )
        connections[name] = {
            "transport": connection["transport"],
            "url": connection["url"],
            **({"headers": headers} if headers else {}),
        }
    return connections


async def load_mcp_tools() -> list[BaseTool]:
    connections = _connections_from_env()
    if not connections:
        return []
    try:
        client = MultiServerMCPClient(
            connections,
            tool_name_prefix=True,
            handle_tool_errors=True,
        )
        return await client.get_tools()
    except AgentRunError:
        raise
    except Exception:  # noqa: BLE001 -- remote MCP details are not public
        raise AgentRunError(
            "MCP_CONNECTION_FAILED",
            "Could not connect to one or more selected MCP servers.",
        ) from None
