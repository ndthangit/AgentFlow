"""DAG workflow runtime with conditional and parallel branch support."""

import asyncio
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


StepObserver = Callable[[ExecutionStep], Awaitable[None]]

TERMINAL_NODE_STATES = {"succeeded", "failed", "skipped"}
IF_OPERATORS = {
    "equals",
    "notEquals",
    "greaterThan",
    "greaterThanOrEqual",
    "lessThan",
    "lessThanOrEqual",
    "truthy",
    "falsy",
}


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


def _evaluate_condition(value: Any, config: dict[str, Any]) -> bool:
    operator = config.get("operator", "truthy")
    expected = config.get("expected")
    if operator == "truthy":
        return bool(value)
    if operator == "falsy":
        return not bool(value)
    if operator == "equals":
        return value == expected
    if operator == "notEquals":
        return value != expected
    try:
        if operator == "greaterThan":
            return value > expected
        if operator == "greaterThanOrEqual":
            return value >= expected
        if operator == "lessThan":
            return value < expected
        if operator == "lessThanOrEqual":
            return value <= expected
    except TypeError as exc:
        raise WorkflowExecutionError(
            f"if values cannot be compared with operator {operator}"
        ) from exc
    raise WorkflowExecutionError(f"unsupported if operator: {operator}")


def _outgoing_port(node: dict[str, Any], node_output: dict[str, Any]) -> str | None:
    if node.get("type") != "if":
        return None
    return "true" if node_output.get("condition") is True else "false"


async def execute_workflow(
    graph: dict[str, Any],
    run_input: dict[str, Any],
    execute_agent: AgentExecutor,
    on_step_update: StepObserver | None = None,
) -> WorkflowExecution:
    """Execute a DAG, scheduling every simultaneously-ready node concurrently."""
    ordered, parents = ordered_nodes(graph)
    by_id = {node["id"]: node for node in ordered}
    sequence_by_id = {
        node["id"]: sequence for sequence, node in enumerate(ordered, start=1)
    }
    incoming: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in by_id}
    outgoing: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in by_id}
    for edge in graph.get("edges", []):
        incoming[edge["to"]].append(edge)
        outgoing[edge["from"]].append(edge)

    outputs: dict[str, dict[str, Any]] = {}
    selected_ports: dict[str, str | None] = {}
    states = {node_id: "pending" for node_id in by_id}
    steps_by_id: dict[str, ExecutionStep] = {}
    max_parallel = graph.get("settings", {}).get("maxParallelNodes", 4)
    if not isinstance(max_parallel, int) or isinstance(max_parallel, bool):
        max_parallel = 4
    semaphore = asyncio.Semaphore(max(1, min(max_parallel, 32)))

    def edge_is_active(edge: dict[str, Any]) -> bool:
        source = edge["from"]
        if states[source] != "succeeded":
            return False
        if by_id[source].get("type") == "if":
            return edge.get("port") == selected_ports.get(source)
        return True

    async def observe(step: ExecutionStep) -> None:
        if on_step_update is not None:
            await on_step_update(step)

    async def skip_node(node_id: str, code: str, message: str) -> None:
        node = by_id[node_id]
        step = ExecutionStep(
            sequence=sequence_by_id[node_id],
            node_id=node_id,
            node_type=node.get("type", "unknown"),
            node_name=node.get("name", node_id),
            status="skipped",
            input=None,
            output=None,
            error={"code": code, "message": message},
            started_at=None,
            completed_at=None,
        )
        states[node_id] = "skipped"
        steps_by_id[node_id] = step
        await observe(step)

    async def execute_node(node_id: str) -> tuple[str, dict[str, Any] | None]:
        node = by_id[node_id]
        sequence = sequence_by_id[node_id]
        node_id = node["id"]
        node_type = node.get("type", "unknown")
        async with semaphore:
            started_at = datetime.now(UTC)
            node_input: dict[str, Any] | None = None
            states[node_id] = "running"
            await observe(
                ExecutionStep(
                    sequence=sequence,
                    node_id=node_id,
                    node_type=node_type,
                    node_name=node.get("name", node_id),
                    status="running",
                    input=None,
                    output=None,
                    error=None,
                    started_at=started_at,
                    completed_at=None,
                )
            )
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
                            raise WorkflowExecutionError(
                                "math.add inputs must be numbers"
                            )
                        config = node.get("config", {})
                        node_output = {config.get("outputKey", "sum"): left + right}
                    elif node_type == "agent":
                        config = node.get("config", {})
                        errors = schema_errors(
                            node_input, config.get("inputSchema", {})
                        )
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
                        errors = schema_errors(
                            node_input, config.get("inputSchema", {})
                        )
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
                    elif node_type == "if":
                        condition = _evaluate_condition(
                            node_input.get("value"), node.get("config", {})
                        )
                        node_output = {**node_input, "condition": condition}
                    elif node_type == "parallel":
                        node_output = dict(node_input)
                    elif node_type == "output.schema":
                        node_output = dict(node_input)
                        errors = schema_errors(
                            node_output, node.get("schema", {}), "$output"
                        )
                        if errors:
                            raise WorkflowExecutionError("; ".join(errors))
                    else:
                        raise WorkflowExecutionError(
                            f"unsupported node type: {node_type}"
                        )

                outputs[node_id] = node_output
                selected_ports[node_id] = _outgoing_port(node, node_output)
                states[node_id] = "succeeded"
                completed_step = ExecutionStep(
                    sequence=sequence,
                    node_id=node_id,
                    node_type=node_type,
                    node_name=node.get("name", node_id),
                    status="succeeded",
                    input=node_input,
                    output=node_output,
                    error=None,
                    started_at=started_at,
                    completed_at=datetime.now(UTC),
                )
                steps_by_id[node_id] = completed_step
                await observe(completed_step)
                return node_id, node_output
            except (RuntimeError, ValueError, TypeError, KeyError, IndexError) as exc:
                states[node_id] = "failed"
                failed_step = ExecutionStep(
                    sequence=sequence,
                    node_id=node_id,
                    node_type=node_type,
                    node_name=node.get("name", node_id),
                    status="failed",
                    input=node_input,
                    output=None,
                    error={"code": "STEP_EXECUTION_FAILED", "message": str(exc)},
                    started_at=started_at,
                    completed_at=datetime.now(UTC),
                )
                steps_by_id[node_id] = failed_step
                await observe(failed_step)
                return node_id, None

    while any(state == "pending" for state in states.values()):
        progressed = False
        for node_id in sequence_by_id:
            if states[node_id] != "pending" or not incoming[node_id]:
                continue
            if not all(
                states[edge["from"]] in TERMINAL_NODE_STATES
                for edge in incoming[node_id]
            ):
                continue
            if not any(edge_is_active(edge) for edge in incoming[node_id]):
                await skip_node(
                    node_id,
                    "BRANCH_NOT_SELECTED",
                    "Skipped because no incoming branch was selected",
                )
                progressed = True

        ready = [
            node_id
            for node_id in sequence_by_id
            if states[node_id] == "pending"
            and (
                not incoming[node_id]
                or (
                    all(
                        states[edge["from"]] in TERMINAL_NODE_STATES
                        for edge in incoming[node_id]
                    )
                    and any(edge_is_active(edge) for edge in incoming[node_id])
                )
            )
        ]
        if ready:
            await asyncio.gather(*(execute_node(node_id) for node_id in ready))
            progressed = True

        failed_ids = [node_id for node_id, state in states.items() if state == "failed"]
        if failed_ids:
            failed_id = failed_ids[0]
            for node_id in sequence_by_id:
                if states[node_id] == "pending":
                    await skip_node(
                        node_id,
                        "UPSTREAM_STEP_FAILED",
                        f"Skipped because node {failed_id} failed",
                    )
            return WorkflowExecution(
                status="failed",
                output=None,
                steps=sorted(steps_by_id.values(), key=lambda step: step.sequence),
            )
        if not progressed:
            raise WorkflowExecutionError("workflow scheduler reached a deadlock")

    active_terminals = [
        node_id
        for node_id in sequence_by_id
        if states[node_id] == "succeeded"
        and not any(edge_is_active(edge) for edge in outgoing[node_id])
    ]
    if len(active_terminals) == 1:
        workflow_output = outputs[active_terminals[0]]
    else:
        workflow_output = {
            "branches": {node_id: outputs[node_id] for node_id in active_terminals}
        }
    return WorkflowExecution(
        status="succeeded",
        output=workflow_output,
        steps=sorted(steps_by_id.values(), key=lambda step: step.sequence),
    )
