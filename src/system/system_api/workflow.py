"""Workflow API schemas and deterministic graph validation."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkflowCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=160)
    draft: dict[str, Any] = Field(default_factory=lambda: {"nodes": [], "edges": []})


class DraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    draft: dict[str, Any]


class WorkflowView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    draft: dict[str, Any]
    revision: int
    created_at: datetime
    updated_at: datetime


class VersionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    workflow_id: uuid.UUID
    version: int
    graph: dict[str, Any]
    content_hash: str
    created_at: datetime


class ValidationResult(BaseModel):
    valid: bool
    errors: list[str]


class FlowRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: uuid.UUID
    input: dict[str, Any] = Field(default_factory=dict)


class FlowRunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    workflow_version_id: uuid.UUID
    status: str
    input: dict[str, Any]
    output: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


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
        errors.append("every node must have a non-empty string id")
        return errors
    if len(set(ids)) != len(ids):
        errors.append("node ids must be unique")

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

    if any(visit(node_id) for node_id in ids if node_id not in visited):
        errors.append("workflow graph must be acyclic")
    return errors
