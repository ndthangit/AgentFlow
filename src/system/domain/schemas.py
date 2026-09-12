"""Pydantic request and response contracts for workflows and runs."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from domain.examples import default_workflow_draft


class WorkflowCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=160)
    draft: dict[str, Any] = Field(default_factory=default_workflow_draft)


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


class RunStepView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    run_id: uuid.UUID
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
    created_at: datetime
