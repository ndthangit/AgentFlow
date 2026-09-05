"""Small, provider-independent input/output contract for a flow node."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NodeType = Literal["trigger.manual", "transform", "http.request", "approval", "end"]


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    task: str = Field(min_length=1, max_length=12000)
    context: str = Field(default="", max_length=20000)


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_type: NodeType
    label: str = Field(min_length=1, max_length=120)
    instructions: str = Field(min_length=1, max_length=2000)


class WorkflowPlan(BaseModel):
    """A suggested sequential plan, not an executable workflow DSL."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2000)
    steps: list[PlanStep] = Field(min_length=1, max_length=12)
    notes: list[str] = Field(default_factory=list, max_length=10)


class RunResult(BaseModel):
    run_id: str
    status: Literal["succeeded"] = "succeeded"
    mode: Literal["live", "demo"]
    output: WorkflowPlan


class AgentRunError(Exception):
    """Public error text must not contain provider responses or credentials."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
