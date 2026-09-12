"""Sequential workflow runtime for built-in, Agent, and restricted Python nodes."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from domain.errors import WorkflowExecutionError
from runtime.restricted_python import execute_restricted_python

AgentExecutor = Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ExecutionStep:
    sequence: int
    node_id: str
    node_type: str
    node_name: str
    status: str
    input: dict[str, Any] | None
    output: dict[str, Any] | None
    error: dict[str, Any] | None
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True)
class WorkflowExecution:
    status: str
    output: dict[str, Any] | None
    steps: list[ExecutionStep]


def schema_errors(
    value: Any, schema: dict[str, Any], path: str = "$input"
) -> list[str]:
    errors: list[str] = []
    expected = schema.get("type")
    matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }
    if isinstance(expected, str) and expected in matches and not matches[expected]:
        return [f"{path} must be {expected}"]
    if expected == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if isinstance(required, list):
            for field in required:
                if isinstance(field, str) and field not in value:
                    errors.append(f"{path}.{field} is required")
        if isinstance(properties, dict):
            for field, field_schema in properties.items():
                if field in value and isinstance(field_schema, dict):
                    errors.extend(
                        schema_errors(value[field], field_schema, f"{path}.{field}")
                    )
            if schema.get("additionalProperties") is False:
                for field in value.keys() - properties.keys():
                    errors.append(f"{path}.{field} is not allowed")
    return errors


def ordered_nodes(
    graph: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if not isinstance(nodes, list) or not nodes:
        raise WorkflowExecutionError("workflow has no nodes")
    by_id = {node["id"]: node for node in nodes}
    indegree = {node_id: 0 for node_id in by_id}
    parents: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    children: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    for edge in edges:
        source, target = edge["from"], edge["to"]
        children[source].append(target)
        parents[target].append(source)
        indegree[target] += 1
    ready = [node["id"] for node in nodes if indegree[node["id"]] == 0]
    ordered: list[dict[str, Any]] = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(by_id[node_id])
        for child in children[node_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if len(ordered) != len(nodes):
        raise WorkflowExecutionError("workflow graph must be acyclic")
    return ordered, parents


def _resolve_reference(
    binding: Any, run_input: dict[str, Any], outputs: dict[str, dict[str, Any]]
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


def _node_input(
    node: dict[str, Any],
    parents: list[str],
    run_input: dict[str, Any],
    outputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    bindings = node.get("inputs")
    if isinstance(bindings, dict) and bindings:
        return {
            field: _resolve_reference(binding, run_input, outputs)
            for field, binding in bindings.items()
        }
    if len(parents) == 1 and parents[0] in outputs:
        return dict(outputs[parents[0]])
    return dict(run_input)


async def execute_workflow(
    graph: dict[str, Any],
    run_input: dict[str, Any],
    execute_agent: AgentExecutor,
) -> WorkflowExecution:
    """Execute every node in topological order and retain success/failure details."""
    ordered, parents = ordered_nodes(graph)
    outputs: dict[str, dict[str, Any]] = {}
    steps: list[ExecutionStep] = []
    workflow_output: dict[str, Any] | None = None

    for sequence, node in enumerate(ordered, start=1):
        node_id = node["id"]
        node_type = node.get("type", "unknown")
        started_at = datetime.now(UTC)
        node_input: dict[str, Any] | None = None
        try:
            if node_type == "input.schema":
                node_input = dict(run_input)
                errors = schema_errors(run_input, node.get("schema", {}))
                if errors:
                    raise WorkflowExecutionError("; ".join(errors))
                node_output = dict(run_input)
            else:
                node_input = _node_input(node, parents[node_id], run_input, outputs)
                if node_type == "math.add":
                    left, right = node_input.get("left"), node_input.get("right")
                    if (
                        not isinstance(left, (int, float))
                        or isinstance(left, bool)
                        or not isinstance(right, (int, float))
                        or isinstance(right, bool)
                    ):
                        raise WorkflowExecutionError("math.add inputs must be numbers")
                    config = node.get("config", {})
                    output_key = config.get("outputKey", "sum")
                    node_output = {output_key: left + right}
                elif node_type == "agent":
                    config = node.get("config", {})
                    errors = schema_errors(node_input, config.get("inputSchema", {}))
                    if errors:
                        raise WorkflowExecutionError("; ".join(errors))
                    node_output = await execute_agent(node, node_input)
                    errors = schema_errors(
                        node_output, config.get("outputSchema", {}), "$output"
                    )
                    if errors:
                        raise WorkflowExecutionError("; ".join(errors))
                elif node_type == "code.python":
                    config = node.get("config", {})
                    errors = schema_errors(node_input, config.get("inputSchema", {}))
                    if errors:
                        raise WorkflowExecutionError("; ".join(errors))
                    node_output = execute_restricted_python(
                        config.get("code", ""), node_input
                    )
                    errors = schema_errors(
                        node_output, config.get("outputSchema", {}), "$output"
                    )
                    if errors:
                        raise WorkflowExecutionError("; ".join(errors))
                elif node_type == "output.schema":
                    node_output = dict(node_input)
                    errors = schema_errors(
                        node_output, node.get("schema", {}), "$output"
                    )
                    if errors:
                        raise WorkflowExecutionError("; ".join(errors))
                else:
                    raise WorkflowExecutionError(f"unsupported node type: {node_type}")

            outputs[node_id] = node_output
            workflow_output = node_output
            completed_at = datetime.now(UTC)
            steps.append(
                ExecutionStep(
                    sequence=sequence,
                    node_id=node_id,
                    node_type=node_type,
                    node_name=node.get("name", node_id),
                    status="succeeded",
                    input=node_input,
                    output=node_output,
                    error=None,
                    started_at=started_at,
                    completed_at=completed_at,
                )
            )
        except (RuntimeError, ValueError, TypeError, KeyError, IndexError) as exc:
            # Provider adapters and parsers surface ordinary Exceptions. Persist the
            # concise message on the failed step so a run never remains silently pending.
            completed_at = datetime.now(UTC)
            steps.append(
                ExecutionStep(
                    sequence=sequence,
                    node_id=node_id,
                    node_type=node_type,
                    node_name=node.get("name", node_id),
                    status="failed",
                    input=node_input,
                    output=None,
                    error={"code": "STEP_EXECUTION_FAILED", "message": str(exc)},
                    started_at=started_at,
                    completed_at=completed_at,
                )
            )
            for skipped_sequence, skipped in enumerate(
                ordered[sequence:], start=sequence + 1
            ):
                steps.append(
                    ExecutionStep(
                        sequence=skipped_sequence,
                        node_id=skipped["id"],
                        node_type=skipped.get("type", "unknown"),
                        node_name=skipped.get("name", skipped["id"]),
                        status="skipped",
                        input=None,
                        output=None,
                        error={
                            "code": "UPSTREAM_STEP_FAILED",
                            "message": f"Skipped because node {node_id} failed",
                        },
                        started_at=None,
                        completed_at=None,
                    )
                )
            return WorkflowExecution(status="failed", output=None, steps=steps)

    return WorkflowExecution(status="succeeded", output=workflow_output, steps=steps)
