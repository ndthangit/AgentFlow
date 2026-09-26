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
                "name": "Bắt đầu",
                "note": "Định nghĩa các biến đầu vào ban đầu của workflow.",
                "schema": _object_schema(),
            },
            {
                "id": "agent",
                "type": "agent",
                "name": "Agent",
                "note": "Mô tả ngắn nhiệm vụ của agent.",
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
                "name": "Kết thúc",
                "note": "Định nghĩa các biến workflow trả về.",
                "inputs": {},
                "schema": _object_schema(),
            },
        ],
        "edges": [
            {"from": "start", "to": "agent", "port": "success"},
            {"from": "agent", "to": "end", "port": "success"},
        ],
    }


def sum_workflow_draft() -> dict[str, Any]:
    return {
        "nodes": [
            {
                "id": "input",
                "type": "input.schema",
                "name": "Hai số đầu vào",
                "note": "Nhận num1 và num2 từ dữ liệu chạy.",
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
                "name": "Cộng hai số",
                "note": "Tính num1 + num2 mà không cần gọi LLM.",
                "inputs": {
                    "left": {"from": "$input.num1"},
                    "right": {"from": "$input.num2"},
                },
                "config": {"outputKey": "sum"},
            },
            {
                "id": "output",
                "type": "output.schema",
                "name": "Kết quả",
                "note": "Trả về tổng của hai số.",
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
