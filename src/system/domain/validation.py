"""Deterministic validation for workflow graph drafts."""

import re
from collections.abc import Iterable
from typing import Any

from domain.node_registry import get_node_type

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

NODE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
INPUT_REFERENCE_PATTERN = re.compile(r"^\$input(?:\.[A-Za-z0-9_-]+)*$")
NODE_REFERENCE_PATTERN = re.compile(
    r"^\$nodes\.([A-Za-z0-9_-]+)\.output\.([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)$"
)


def validate_graph(graph: dict[str, Any]) -> list[str]:
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return ["draft must contain nodes and edges arrays"]
    if not nodes:
        return ["workflow must contain at least one node"]

    errors: list[str] = []
    ids = [node.get("id") for node in nodes if isinstance(node, dict)]
    if len(ids) != len(nodes) or any(
        not isinstance(item, str) or not item for item in ids
    ):
        return ["every node must have a non-empty string id"]
    if len(set(ids)) != len(ids):
        errors.append("node ids must be unique")
    for node_id in ids:
        if not NODE_ID_PATTERN.fullmatch(node_id):
            errors.append(
                f"node id {node_id} may contain only letters, numbers, underscores and hyphens"
            )

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
        elif nodes_by_id[source].get("type") == "parallel" and port != "parallel":
            errors.append(f"parallel node {source} edges must use parallel ports")
        else:
            definition = get_node_type(str(nodes_by_id[source].get("type", "")))
            if definition is not None and port not in definition.control_ports:
                errors.append(f"node {source} does not support port {port}")

    if _contains_cycle(adjacency):
        errors.append("workflow graph must be acyclic")
    for node_id, node in nodes_by_id.items():
        ports = outgoing_ports[node_id]
        if node.get("type") == "if" and set(ports) != {"true", "false"}:
            errors.append(f"if node {node_id} must have true and false branches")
        if node.get("type") == "parallel" and len(ports) < 2:
            errors.append(f"parallel node {node_id} must have at least two branches")
        _validate_input_bindings(node, nodes_by_id, adjacency, errors)
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
    if not isinstance(node_type, str) or not node_type.strip():
        errors.append(f"node {node_id} must define a supported type")
        return
    definition = get_node_type(node_type)
    if definition is None:
        errors.append(f"node {node_id} has unsupported type: {node_type}")
        return
    type_version = node.get("typeVersion", definition.type_version)
    if (
        not isinstance(type_version, int)
        or isinstance(type_version, bool)
        or type_version != definition.type_version
    ):
        errors.append(
            f"node {node_id} typeVersion must be {definition.type_version} for {node_type}"
        )
    if "name" in node and not isinstance(node["name"], str):
        errors.append(f"node {node_id} name must be a string")
    if "note" in node and not isinstance(node["note"], str):
        errors.append(f"node {node_id} note must be a string")
    if node_type in {"input.schema", "output.schema"} and not isinstance(
        node.get("schema"), dict
    ):
        errors.append(f"node {node_id} must define schema as an object")
    if node_type in {"agent", "llm.call"}:
        _validate_configured_node(node, errors, require_language=False)
        config = node.get("config")
        runtime = (
            config.get("runtime", "agent") if isinstance(config, dict) else "agent"
        )
        if node_type == "agent" and runtime not in {"direct", "agent"}:
            errors.append(f"agent node {node_id} runtime must be direct or agent")
        if (
            node_type == "llm.call"
            and isinstance(config, dict)
            and (
                not isinstance(config.get("prompt"), str)
                or not config["prompt"].strip()
            )
        ):
            errors.append(f"llm.call node {node_id} must define a non-empty prompt")
        if (
            node_type == "llm.call"
            and isinstance(config, dict)
            and "skillIds" in config
        ):
            errors.append(f"llm.call node {node_id} cannot define skillIds")
        if (
            node_type == "llm.call"
            and isinstance(config, dict)
            and "mcpServerIds" in config
        ):
            errors.append(f"llm.call node {node_id} cannot define mcpServerIds")
        if isinstance(config, dict):
            has_provider = "providerId" in config
            has_model = "model" in config
            if has_provider or has_model:
                if (
                    not isinstance(config.get("providerId"), str)
                    or not config["providerId"].strip()
                ):
                    errors.append(
                        f"{node_type} node {node_id} must define a non-empty providerId"
                    )
                if (
                    not isinstance(config.get("model"), str)
                    or not config["model"].strip()
                ):
                    errors.append(
                        f"{node_type} node {node_id} must define a non-empty model"
                    )
        if node_type == "agent" and isinstance(config, dict) and "skillIds" in config:
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
        if node_type == "agent" and isinstance(config, dict) and "mcpServerIds" in config:
            server_ids = config["mcpServerIds"]
            if (
                not isinstance(server_ids, list)
                or any(
                    not isinstance(server_id, str) or not server_id
                    for server_id in server_ids
                )
                or len(set(server_ids)) != len(server_ids)
            ):
                errors.append(
                    f"agent node {node_id} mcpServerIds must be unique non-empty strings"
                )
            if runtime == "direct" and server_ids:
                errors.append(
                    f"agent node {node_id} must use agent runtime when MCP servers are selected"
                )
    elif node_type == "code.python":
        _validate_configured_node(node, errors, require_language=True)
    elif node_type == "math.add":
        _validate_math_node(node, errors)
    elif node_type == "if":
        _validate_if_node(node, errors)


def _validate_input_bindings(
    node: dict[str, Any],
    nodes_by_id: dict[str, dict[str, Any]],
    adjacency: dict[str, list[str]],
    errors: list[str],
) -> None:
    inputs = node.get("inputs")
    if inputs is None:
        return
    node_id = node["id"]
    node_type = node.get("type", "unknown")
    if not isinstance(inputs, dict):
        errors.append(f"{node_type} node {node_id} inputs must be an object")
        return
    for field, binding in inputs.items():
        if not isinstance(field, str) or not field:
            errors.append(f"node {node_id} input names must be non-empty strings")
            continue
        if not isinstance(binding, dict) or not isinstance(binding.get("from"), str):
            errors.append(f"node {node_id} input {field} must bind with from")
            continue
        reference = binding["from"]
        if INPUT_REFERENCE_PATTERN.fullmatch(reference):
            continue
        match = NODE_REFERENCE_PATTERN.fullmatch(reference)
        if match is None:
            errors.append(f"node {node_id} input {field} has invalid reference {reference}")
            continue
        source = match.group(1)
        if source not in nodes_by_id:
            errors.append(
                f"node {node_id} input {field} references unknown node {source}"
            )
            continue
        if source == node_id or not _has_path(source, node_id, adjacency):
            errors.append(
                f"node {node_id} input {field} must reference an upstream node"
            )


def _has_path(source: str, target: str, adjacency: dict[str, list[str]]) -> bool:
    pending = list(adjacency.get(source, []))
    visited: set[str] = set()
    while pending:
        node_id = pending.pop()
        if node_id == target:
            return True
        if node_id in visited:
            continue
        visited.add(node_id)
        pending.extend(adjacency.get(node_id, []))
    return False


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


def validate_agent_model_selections(
    graph: dict[str, Any], providers: Iterable[Any]
) -> list[str]:
    """Validate explicit Agent and LLM Call model selections."""
    providers_by_id = {str(provider.id): provider for provider in providers}
    errors: list[str] = []
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return errors

    for node in nodes:
        if not isinstance(node, dict) or node.get("type") not in {
            "agent",
            "llm.call",
        }:
            continue
        config = node.get("config")
        if not isinstance(config, dict):
            continue
        provider_id = config.get("providerId")
        model = config.get("model")
        if not isinstance(provider_id, str) or not provider_id.strip():
            continue
        if not isinstance(model, str) or not model.strip():
            continue
        provider = providers_by_id.get(provider_id)
        node_id = node.get("id", "unknown")
        node_type = node.get("type", "agent")
        if provider is None:
            errors.append(
                f"{node_type} node {node_id} references an unavailable LLM provider"
            )
            continue
        settings = provider.settings if isinstance(provider.settings, dict) else {}
        selected_models = settings.get("selected_models")
        if not isinstance(selected_models, list) or model not in selected_models:
            errors.append(
                f"{node_type} node {node_id} model {model} is not enabled for provider {provider.name}"
            )
    return errors


def agent_skill_ids(graph: dict[str, Any]) -> list[str]:
    """Return unique Agent skill IDs in stable graph/node selection order."""
    selected: list[str] = []
    seen: set[str] = set()
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return selected
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "agent":
            continue
        config = node.get("config")
        skill_ids = config.get("skillIds") if isinstance(config, dict) else None
        if not isinstance(skill_ids, list):
            continue
        for skill_id in skill_ids:
            if isinstance(skill_id, str) and skill_id and skill_id not in seen:
                selected.append(skill_id)
                seen.add(skill_id)
    return selected


def validate_agent_skill_selections(
    graph: dict[str, Any], skills: Iterable[Any]
) -> list[str]:
    """Validate Agent selections against the owner's active, visible skill catalog."""
    available_ids = {str(skill.id) for skill in skills}
    errors: list[str] = []
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return errors
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "agent":
            continue
        config = node.get("config")
        skill_ids = config.get("skillIds") if isinstance(config, dict) else None
        if not isinstance(skill_ids, list):
            continue
        missing = [
            skill_id
            for skill_id in skill_ids
            if isinstance(skill_id, str) and skill_id not in available_ids
        ]
        if missing:
            errors.append(
                f"agent node {node.get('id', 'unknown')} references unavailable skills: "
                + ", ".join(missing)
            )
    return errors


def agent_mcp_server_ids(graph: dict[str, Any]) -> list[str]:
    """Return unique MCP server IDs selected by Agent nodes."""
    selected: list[str] = []
    seen: set[str] = set()
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return selected
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "agent":
            continue
        config = node.get("config")
        server_ids = config.get("mcpServerIds") if isinstance(config, dict) else None
        if not isinstance(server_ids, list):
            continue
        for server_id in server_ids:
            if isinstance(server_id, str) and server_id and server_id not in seen:
                selected.append(server_id)
                seen.add(server_id)
    return selected


def validate_agent_mcp_selections(
    graph: dict[str, Any], servers: Iterable[Any]
) -> list[str]:
    """Validate Agent MCP selections against the owner's enabled registry."""
    available_ids = {str(server.id) for server in servers}
    errors: list[str] = []
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return errors
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "agent":
            continue
        config = node.get("config")
        server_ids = config.get("mcpServerIds") if isinstance(config, dict) else None
        if not isinstance(server_ids, list):
            continue
        missing = [
            server_id
            for server_id in server_ids
            if isinstance(server_id, str) and server_id not in available_ids
        ]
        if missing:
            errors.append(
                f"agent node {node.get('id', 'unknown')} references unavailable MCP servers: "
                + ", ".join(missing)
            )
    return errors
