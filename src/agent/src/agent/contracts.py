"""Small, provider-independent input/output contract for a flow node."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NodeType = Literal["trigger.manual", "transform", "http.request", "approval", "end"]


class AgentSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    version: int = Field(ge=1)
    content_hash: str = Field(min_length=64, max_length=64)
    instructions: str = Field(min_length=1, max_length=20_000)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    task: str = Field(min_length=1, max_length=12000)
    context: str = Field(default="", max_length=20000)
    skills: list[AgentSkill] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def skills_fit_prompt_budget(self):
        if sum(len(skill.instructions) for skill in self.skills) > 50_000:
            raise ValueError("combined skill instructions exceed 50000 characters")
        return self


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
