"""Context propagated from a leased workflow run into Agent containers."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class RuntimeExecutionContext:
    run_id: uuid.UUID
    lease_generation: int
    node_id: str | None = None


_current_execution: ContextVar[RuntimeExecutionContext | None] = ContextVar(
    "agentflow_runtime_execution", default=None
)


def current_execution_context() -> RuntimeExecutionContext | None:
    return _current_execution.get()


@contextmanager
def bind_run_execution(
    run_id: uuid.UUID, lease_generation: int
) -> Iterator[RuntimeExecutionContext]:
    context = RuntimeExecutionContext(run_id, lease_generation)
    token = _current_execution.set(context)
    try:
        yield context
    finally:
        _current_execution.reset(token)


@contextmanager
def bind_node_execution(node_id: str) -> Iterator[RuntimeExecutionContext | None]:
    current = _current_execution.get()
    if current is None:
        yield None
        return
    context = replace(current, node_id=node_id)
    token = _current_execution.set(context)
    try:
        yield context
    finally:
        _current_execution.reset(token)
