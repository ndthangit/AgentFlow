"""Deterministic validation for workflow graph drafts."""

from typing import Any


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
    for edge in edges:
        if (
            not isinstance(edge, dict)
            or edge.get("from") not in adjacency
            or edge.get("to") not in adjacency
        ):
            errors.append("every edge must reference existing nodes")
            continue
        adjacency[edge["from"]].append(edge["to"])

    if _contains_cycle(adjacency):
        errors.append("workflow graph must be acyclic")
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
    elif node_type == "code.python":
        _validate_configured_node(node, errors, require_language=True)
    elif node_type == "math.add":
        _validate_math_node(node, errors)


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
