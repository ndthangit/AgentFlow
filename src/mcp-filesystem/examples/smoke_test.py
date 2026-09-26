"""Call the running demo over Streamable HTTP and verify its three tools."""

import argparse
import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def verify(url: str) -> None:
    async with (
        streamable_http_client(url) as (read_stream, write_stream, _),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        catalog = await session.list_tools()
        tool_names = sorted(tool.name for tool in catalog.tools)
        expected = ["list_files", "read_file", "write_file"]
        if tool_names != expected:
            raise RuntimeError(f"unexpected tool catalog: {tool_names}")

        write_result = await session.call_tool(
            "write_file",
            {
                "path": "demo/hello-agentflow.txt",
                "content": "Xin chào từ MCP filesystem demo!",
            },
        )
        read_result = await session.call_tool(
            "read_file", {"path": "demo/hello-agentflow.txt"}
        )
        list_result = await session.call_tool("list_files", {"directory": "."})
        for result in (write_result, read_result, list_result):
            if result.isError:
                raise RuntimeError("an MCP demo tool returned an error")

        print(
            json.dumps(
                {
                    "tools": tool_names,
                    "write": write_result.structuredContent,
                    "read": read_result.structuredContent,
                    "list": list_result.structuredContent,
                },
                ensure_ascii=True,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8002/mcp")
    arguments = parser.parse_args()
    asyncio.run(verify(arguments.url))


if __name__ == "__main__":
    main()
