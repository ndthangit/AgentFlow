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


def simple_agent_workflow_draft() -> dict[str, Any]:
    """Default runnable example that does not grant skills or MCP servers."""
    return {
        "nodes": [
            {
                "id": "input",
                "type": "input.schema",
                "name": "Text Input",
                "note": "Receive the text supplied in the run input.",
                "schema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
            {
                "id": "summary_agent",
                "type": "agent",
                "name": "Summary Agent",
                "note": "Summarize the text with the default model without MCP or skills.",
                "inputs": {"text": {"from": "$input.text"}},
                "config": {
                    "runtime": "default",
                    "instructions": (
                        "Summarize the text in {{input.text}} in two or three clear "
                        "English sentences. Return exactly one JSON object matching "
                        "the output schema with a single summary string."
                    ),
                    "skillIds": [],
                    "mcpServerIds": [],
                    "inputSchema": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                        "additionalProperties": False,
                    },
                    "outputSchema": {
                        "type": "object",
                        "properties": {"summary": {"type": "string"}},
                        "required": ["summary"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "id": "output",
                "type": "output.schema",
                "name": "Summary",
                "note": "Return the summary created by the agent.",
                "inputs": {
                    "summary": {"from": "$nodes.summary_agent.output.summary"}
                },
                "schema": {
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"],
                    "additionalProperties": False,
                },
            },
        ],
        "edges": [
            {"from": "input", "to": "summary_agent", "port": "success"},
            {"from": "summary_agent", "to": "output", "port": "success"},
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
