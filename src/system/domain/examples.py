"""Reusable workflow graph templates."""

from typing import Any


def _object_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "additionalProperties": False}


def default_workflow_draft() -> dict[str, Any]:
    return {
        "nodes": [
            {
                "id": "start",
                "type": "input.schema",
                "name": "Start",
                "note": "Define the initial workflow input fields.",
                "schema": _object_schema(),
            },
            {
                "id": "agent",
                "type": "agent",
                "name": "Agent",
                "note": "Describe the task this agent should complete.",
                "config": {
                    "runtime": "agent",
                    "instructions": "",
                    "skillIds": [],
                    "inputSchema": _object_schema(),
                    "outputSchema": _object_schema(),
                },
            },
            {
                "id": "end",
                "type": "output.schema",
                "name": "End",
                "note": "Define the fields returned by the workflow.",
                "inputs": {},
                "schema": _object_schema(),
            },
        ],
        "edges": [
            {"from": "start", "to": "agent", "port": "success"},
            {"from": "agent", "to": "end", "port": "success"},
        ],
    }


def simple_agent_workflow_draft(
    skill_id: str,
    mcp_server_id: str,
) -> dict[str, Any]:
    """Small starter workflow demonstrating a skill, MCP, and parallel branches."""
    return {
        "settings": {"maxParallelNodes": 2},
        "nodes": [
            {
                "id": "input",
                "type": "input.schema",
                "name": "Request",
                "note": "Receive a request to explore with the starter workflow.",
                "schema": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}},
                    "required": ["request"],
                    "additionalProperties": False,
                },
            },
            {
                "id": "parallel",
                "type": "parallel",
                "name": "Run in Parallel",
                "note": "Start the MCP and skill examples at the same time.",
            },
            {
                "id": "filesystem_agent",
                "type": "agent",
                "name": "Filesystem MCP Agent",
                "note": "Use the connected Filesystem MCP server without changing files.",
                "inputs": {"request": {"from": "$nodes.parallel.output.request"}},
                "config": {
                    "runtime": "default",
                    "instructions": (
                        "Use the Filesystem MCP tools to inspect the available files, "
                        "then answer {{input.request}}. Do not modify files. Return "
                        "exactly one JSON object matching the output schema."
                    ),
                    "skillIds": [],
                    "mcpServerIds": [mcp_server_id],
                    "inputSchema": {
                        "type": "object",
                        "properties": {"request": {"type": "string"}},
                        "required": ["request"],
                        "additionalProperties": False,
                    },
                    "outputSchema": {
                        "type": "object",
                        "properties": {"filesystem_result": {"type": "string"}},
                        "required": ["filesystem_result"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "id": "workflow_agent",
                "type": "agent",
                "name": "Skilled Workflow Agent",
                "note": "Use the built-in Workflow Designer skill.",
                "inputs": {"request": {"from": "$nodes.parallel.output.request"}},
                "config": {
                    "runtime": "default",
                    "instructions": (
                        "Use the assigned skill to propose a small workflow for "
                        "{{input.request}}. Return exactly one JSON object matching "
                        "the output schema."
                    ),
                    "skillIds": [skill_id],
                    "mcpServerIds": [],
                    "inputSchema": {
                        "type": "object",
                        "properties": {"request": {"type": "string"}},
                        "required": ["request"],
                        "additionalProperties": False,
                    },
                    "outputSchema": {
                        "type": "object",
                        "properties": {"workflow_plan": {"type": "string"}},
                        "required": ["workflow_plan"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "id": "output",
                "type": "output.schema",
                "name": "Results",
                "note": "Return the results after both parallel branches finish.",
                "inputs": {
                    "filesystem_result": {
                        "from": "$nodes.filesystem_agent.output.filesystem_result"
                    },
                    "workflow_plan": {
                        "from": "$nodes.workflow_agent.output.workflow_plan"
                    },
                },
                "schema": {
                    "type": "object",
                    "properties": {
                        "filesystem_result": {"type": "string"},
                        "workflow_plan": {"type": "string"},
                    },
                    "required": ["filesystem_result", "workflow_plan"],
                    "additionalProperties": False,
                },
            },
        ],
        "edges": [
            {"from": "input", "to": "parallel", "port": "success"},
            {"from": "parallel", "to": "filesystem_agent", "port": "parallel"},
            {"from": "parallel", "to": "workflow_agent", "port": "parallel"},
            {"from": "filesystem_agent", "to": "output", "port": "success"},
            {"from": "workflow_agent", "to": "output", "port": "success"},
        ],
    }


def sum_workflow_draft() -> dict[str, Any]:
    return {
        "nodes": [
            {
                "id": "input",
                "type": "input.schema",
                "name": "Number Input",
                "note": "Receive num1 and num2 from the run input.",
                "schema": {
                    "type": "object",
                    "properties": {
                        "num1": {"type": "number"},
                        "num2": {"type": "number"},
                    },
                    "required": ["num1", "num2"],
                    "additionalProperties": False,
                },
            },
            {
                "id": "sum",
                "type": "math.add",
                "name": "Add Numbers",
                "note": "Calculate num1 + num2 without calling an LLM.",
                "inputs": {
                    "left": {"from": "$input.num1"},
                    "right": {"from": "$input.num2"},
                },
                "config": {"outputKey": "sum"},
            },
            {
                "id": "output",
                "type": "output.schema",
                "name": "Result",
                "note": "Return the calculated sum.",
                "inputs": {"sum": {"from": "$nodes.sum.output.sum"}},
                "schema": {
                    "type": "object",
                    "properties": {"sum": {"type": "number"}},
                    "required": ["sum"],
                    "additionalProperties": False,
                },
            },
        ],
        "edges": [
            {"from": "input", "to": "sum", "port": "success"},
            {"from": "sum", "to": "output", "port": "success"},
        ],
    }
