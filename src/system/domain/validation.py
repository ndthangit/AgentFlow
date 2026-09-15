"""Deterministic validation for workflow graph drafts."""

from typing import Any

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


def validate_graph(graph: dict[str, Any]) -> list[str]:
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return ["draft must contain nodes and edges arrays"]

    errors: list[str] = []
    ids = [node.get("id") for node in nodes if isinstance(node, dict)]
    if len(ids) != len(nodes) or any(
        not isinstance(item, str) or not item for item in ids
    ):
        return ["every node must have a non-empty string id"]
    if len(set(ids)) != len(ids):
        errors.append("node ids must be unique")

    for node in nodes:
        _validate_node(node, errors)

    adjacency = {node_id: [] for node_id in ids}
    nodes_by_id = {node["id"]: node for node in nodes}
    outgoing_ports: dict[str, list[str]] = {node_id: [] for node_id in ids}
    seen_edges: set[tuple[str, str, str]] = set()
    for edge in edges:
        if (
            not isinstance(edge, dict)
            or edge.get("from") not in adjacency
            or edge.get("to") not in adjacency
        ):
            errors.append("every edge must reference existing nodes")
            continue
        source = edge["from"]
        target = edge["to"]
        port = edge.get("port", "success")
        if not isinstance(port, str) or not port:
            errors.append(f"edge {source} -> {target} must define a non-empty port")
            continue
        edge_key = (source, target, port)
        if edge_key in seen_edges:
            errors.append(f"duplicate edge {source} ({port}) -> {target}")
            continue
        seen_edges.add(edge_key)
        adjacency[source].append(target)
        outgoing_ports[source].append(port)
        if nodes_by_id[source].get("type") == "if" and port not in {"true", "false"}:
            errors.append(f"if node {source} edges must use true or false ports")
        if nodes_by_id[source].get("type") == "parallel" and port != "parallel":
            errors.append(f"parallel node {source} edges must use parallel ports")

    if _contains_cycle(adjacency):
        errors.append("workflow graph must be acyclic")
    for node_id, node in nodes_by_id.items():
        ports = outgoing_ports[node_id]
        if node.get("type") == "if" and set(ports) != {"true", "false"}:
            errors.append(f"if node {node_id} must have true and false branches")
        if node.get("type") == "parallel" and len(ports) < 2:
            errors.append(f"parallel node {node_id} must have at least two branches")
    settings = graph.get("settings")
    if settings is not None and not isinstance(settings, dict):
        errors.append("settings must be an object")
    elif isinstance(settings, dict) and "maxParallelNodes" in settings:
        max_parallel = settings["maxParallelNodes"]
        if (
            not isinstance(max_parallel, int)
            or isinstance(max_parallel, bool)
            or not 1 <= max_parallel <= 32
        ):
            errors.append("settings.maxParallelNodes must be an integer from 1 to 32")
    return errors


def _validate_node(node: dict[str, Any], errors: list[str]) -> None:
    node_id = node["id"]
    node_type = node.get("type")
    if node_type is not None and not isinstance(node_type, str):
        errors.append(f"node {node_id} type must be a string")
    if "name" in node and not isinstance(node["name"], str):
        errors.append(f"node {node_id} name must be a string")
    if "note" in node and not isinstance(node["note"], str):
        errors.append(f"node {node_id} note must be a string")
    if node_type in {"input.schema", "output.schema"} and not isinstance(
        node.get("schema"), dict
    ):
        errors.append(f"node {node_id} must define schema as an object")
    if node_type == "agent":
        _validate_configured_node(node, errors, require_language=False)
        config = node.get("config")
        if isinstance(config, dict) and "skillIds" in config:
            skill_ids = config["skillIds"]
            if (
                not isinstance(skill_ids, list)
                or any(
                    not isinstance(skill_id, str) or not skill_id
                    for skill_id in skill_ids
                )
                or len(set(skill_ids)) != len(skill_ids)
            ):
                errors.append(
                    f"agent node {node_id} skillIds must be unique non-empty strings"
                )
    elif node_type == "code.python":
        _validate_configured_node(node, errors, require_language=True)
    elif node_type == "math.add":
        _validate_math_node(node, errors)
    elif node_type == "if":
        _validate_if_node(node, errors)


def _validate_configured_node(
    node: dict[str, Any], errors: list[str], *, require_language: bool
) -> None:
    node_id = node["id"]
    node_type = node["type"]
    config = node.get("config")
    if not isinstance(config, dict):
        errors.append(f"{node_type} node {node_id} must define config as an object")
        return
    if require_language:
        if config.get("language") != "python":
            errors.append(f"code.python node {node_id} language must be python")
        if not isinstance(config.get("code"), str) or not config["code"].strip():
            errors.append(f"code.python node {node_id} must define non-empty code")
    for field in ("inputSchema", "outputSchema"):
        if not isinstance(config.get(field), dict):
            errors.append(
                f"{node_type} node {node_id} must define {field} as an object"
            )


def _validate_math_node(node: dict[str, Any], errors: list[str]) -> None:
    node_id = node["id"]
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        errors.append(f"math.add node {node_id} must define inputs")
        return
    for field in ("left", "right"):
        binding = inputs.get(field)
        if not isinstance(binding, dict) or not isinstance(binding.get("from"), str):
            errors.append(f"math.add node {node_id} must bind {field} with from")


def _validate_if_node(node: dict[str, Any], errors: list[str]) -> None:
    node_id = node["id"]
    inputs = node.get("inputs")
    value_binding = inputs.get("value") if isinstance(inputs, dict) else None
    if not isinstance(value_binding, dict) or not isinstance(
        value_binding.get("from"), str
    ):
        errors.append(f"if node {node_id} must bind value with from")
    config = node.get("config")
    operator = config.get("operator") if isinstance(config, dict) else None
    if operator not in IF_OPERATORS:
        errors.append(f"if node {node_id} has an unsupported operator")
    if (
        operator not in {"truthy", "falsy", None}
        and isinstance(config, dict)
        and "expected" not in config
    ):
        errors.append(f"if node {node_id} must define expected")


def _contains_cycle(adjacency: dict[str, list[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        cyclic = any(visit(child) for child in adjacency[node_id])
        visiting.remove(node_id)
        visited.add(node_id)
        return cyclic

    return any(visit(node_id) for node_id in adjacency if node_id not in visited)
