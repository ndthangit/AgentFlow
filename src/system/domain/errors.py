"""Workflow domain exceptions."""


class WorkflowExecutionError(ValueError):
    """Raised when a workflow graph or node cannot be executed safely."""
