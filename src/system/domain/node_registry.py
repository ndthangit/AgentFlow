"""Authoritative metadata for workflow node types supported by the engine."""

from dataclasses import dataclass
from typing import Any, Literal

SideEffectClass = Literal["pure", "read", "workspace-write", "external-write"]


@dataclass(frozen=True)
class NodeTypeDefinition:
    type: str
    type_version: int
    label: str
    description: str
    executor: str
    control_ports: tuple[str, ...]
    side_effect_class: SideEffectClass
    config_schema: dict[str, Any]
    required_capabilities: tuple[str, ...] = ()

    def public_view(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "typeVersion": self.type_version,
            "label": self.label,
            "description": self.description,
            "control_ports": list(self.control_ports),
            "side_effect_class": self.side_effect_class,
            "config_schema": self.config_schema,
            "required_capabilities": list(self.required_capabilities),
        }


OBJECT_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
}

CONFIGURED_NODE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "inputSchema": {"type": "object"},
        "outputSchema": {"type": "object"},
    },
    "required": ["inputSchema", "outputSchema"],
    "additionalProperties": True,
}


NODE_TYPES: dict[str, NodeTypeDefinition] = {
    definition.type: definition
    for definition in (
        NodeTypeDefinition(
            type="input.schema",
            type_version=1,
            label="Start",
            description="Validate and expose the workflow input.",
            executor="input.schema",
            control_ports=("success",),
            side_effect_class="pure",
            config_schema=OBJECT_CONFIG_SCHEMA,
        ),
        NodeTypeDefinition(
            type="math.add",
            type_version=1,
            label="Math Add",
            description="Add two numeric inputs.",
            executor="math.add",
            control_ports=("success",),
            side_effect_class="pure",
            config_schema={
                "type": "object",
                "properties": {"outputKey": {"type": "string", "minLength": 1}},
                "additionalProperties": False,
            },
        ),
        NodeTypeDefinition(
            type="llm.call",
            type_version=1,
            label="LLM Call",
            description="Make one structured model completion without Agent tools.",
            executor="llm.call",
            control_ports=("success",),
            side_effect_class="read",
            config_schema=CONFIGURED_NODE_SCHEMA,
            required_capabilities=("structured-output",),
        ),
        NodeTypeDefinition(
            type="agent",
            type_version=1,
            label="Agent",
            description="Run the selected Agent runtime with optional skills and MCP tools.",
            executor="agent",
            control_ports=("success",),
            side_effect_class="external-write",
            config_schema=CONFIGURED_NODE_SCHEMA,
            required_capabilities=("structured-output",),
        ),
        NodeTypeDefinition(
            type="code.python",
            type_version=1,
            label="Python",
            description="Evaluate the restricted deterministic Python subset.",
            executor="code.python",
            control_ports=("success",),
            side_effect_class="pure",
            config_schema={
                **CONFIGURED_NODE_SCHEMA,
                "properties": {
                    **CONFIGURED_NODE_SCHEMA["properties"],
                    "language": {"const": "python"},
                    "code": {"type": "string", "minLength": 1},
                },
                "required": ["language", "code", "inputSchema", "outputSchema"],
            },
        ),
        NodeTypeDefinition(
            type="if",
            type_version=1,
            label="If / Else",
            description="Select exactly one true or false branch.",
            executor="if",
            control_ports=("true", "false"),
            side_effect_class="pure",
            config_schema=OBJECT_CONFIG_SCHEMA,
        ),
        NodeTypeDefinition(
            type="parallel",
            type_version=1,
            label="Parallel",
            description="Activate two or more downstream branches.",
            executor="parallel",
            control_ports=("parallel",),
            side_effect_class="pure",
            config_schema=OBJECT_CONFIG_SCHEMA,
        ),
        NodeTypeDefinition(
            type="output.schema",
            type_version=1,
            label="End",
            description="Validate and return a workflow output.",
            executor="output.schema",
            control_ports=(),
            side_effect_class="pure",
            config_schema=OBJECT_CONFIG_SCHEMA,
        ),
    )
}


def get_node_type(node_type: str) -> NodeTypeDefinition | None:
    return NODE_TYPES.get(node_type)


def node_type_catalog() -> list[dict[str, Any]]:
    return [definition.public_view() for definition in NODE_TYPES.values()]
