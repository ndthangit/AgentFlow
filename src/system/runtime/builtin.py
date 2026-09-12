"""Synchronous compatibility runtime for side-effect-free built-in nodes."""

from dataclasses import dataclass
from typing import Any

from domain.errors import WorkflowExecutionError
from runtime.engine import ordered_nodes, schema_errors


@dataclass(frozen=True)
class WorkflowStepResult:
    sequence: int
    node_id: str
    node_type: str
    node_name: str
    input: dict[str, Any]
    output: dict[str, Any]


@dataclass(frozen=True)
class BuiltinWorkflowResult:
    output: dict[str, Any]
    steps: list[WorkflowStepResult]


def _resolve_reference(
    binding: Any,
    run_input: dict[str, Any],
    outputs: dict[str, dict[str, Any]],
) -> Any:
    if not isinstance(binding, dict) or not isinstance(binding.get("from"), str):
        raise WorkflowExecutionError("node input binding must contain a string from")
    reference = binding["from"]
    if reference == "$input":
        return run_input
    if reference.startswith("$input."):
        value: Any = run_input
        fields = reference.removeprefix("$input.").split(".")
    elif reference.startswith("$nodes."):
        parts = reference.split(".")
        if len(parts) < 4 or parts[2] != "output":
            raise WorkflowExecutionError(f"invalid node reference {reference}")
        if parts[1] not in outputs:
            raise WorkflowExecutionError(f"node output is not available: {reference}")
        value = outputs[parts[1]]
        fields = parts[3:]
    else:
        raise WorkflowExecutionError(f"unsupported input reference {reference}")
    for field in fields:
        if not isinstance(value, dict) or field not in value:
            raise WorkflowExecutionError(f"missing input reference {reference}")
        value = value[field]
    return value


def execute_builtin_workflow_detailed(
    graph: dict[str, Any], run_input: dict[str, Any]
) -> BuiltinWorkflowResult | None:
    nodes = graph.get("nodes", [])
    supported = {"input.schema", "math.add", "output.schema"}
    if not nodes or any(node.get("type") not in supported for node in nodes):
        return None

    ordered, _parents = ordered_nodes(graph)
    outputs: dict[str, dict[str, Any]] = {}
    workflow_output: dict[str, Any] = {}
    steps: list[WorkflowStepResult] = []
    for sequence, node in enumerate(ordered, start=1):
        node_id = node["id"]
        node_type = node["type"]
        if node_type == "input.schema":
            node_input = dict(run_input)
            errors = schema_errors(run_input, node["schema"])
            node_output = dict(run_input)
        elif node_type == "math.add":
            bindings = node.get("inputs", {})
            left = _resolve_reference(bindings.get("left"), run_input, outputs)
            right = _resolve_reference(bindings.get("right"), run_input, outputs)
            node_input = {"left": left, "right": right}
            errors = []
            if (
                not isinstance(left, (int, float))
                or isinstance(left, bool)
                or not isinstance(right, (int, float))
                or isinstance(right, bool)
            ):
                raise WorkflowExecutionError("math.add inputs must be numbers")
            output_key = node.get("config", {}).get("outputKey", "sum")
            node_output = {output_key: left + right}
        else:
            bindings = node.get("inputs", {})
            if not isinstance(bindings, dict):
                raise WorkflowExecutionError("output.schema inputs must be an object")
            workflow_output = {
                field: _resolve_reference(binding, run_input, outputs)
                for field, binding in bindings.items()
            }
            node_input = dict(workflow_output)
            errors = schema_errors(workflow_output, node["schema"], "$output")
            node_output = dict(workflow_output)
        if errors:
            raise WorkflowExecutionError("; ".join(errors))
        outputs[node_id] = node_output
        steps.append(
            WorkflowStepResult(
                sequence=sequence,
                node_id=node_id,
                node_type=node_type,
                node_name=node.get("name", node_id),
                input=node_input,
                output=node_output,
            )
        )
    return BuiltinWorkflowResult(output=workflow_output, steps=steps)


def execute_builtin_workflow(
    graph: dict[str, Any], run_input: dict[str, Any]
) -> dict[str, Any] | None:
    result = execute_builtin_workflow_detailed(graph, run_input)
    return result.output if result is not None else None
