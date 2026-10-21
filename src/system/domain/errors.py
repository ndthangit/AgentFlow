"""Workflow domain exceptions."""


class WorkflowExecutionError(ValueError):
    """Raised when a workflow graph or node cannot be executed safely."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "STEP_EXECUTION_FAILED",
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail
