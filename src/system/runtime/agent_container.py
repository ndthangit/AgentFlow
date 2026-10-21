"""Run the configured src/agents/<runtime-name> image in a disposable container."""

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from opentelemetry.propagate import inject

from core.telemetry import annotate_langfuse_span
from domain.errors import WorkflowExecutionError
from runtime.container_process import (
    ContainerRunner,
    run_container_process,
)
from runtime.execution_context import current_execution_context

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("agentflow.agent-container")

MODEL_PASSTHROUGH_ENV = (
    "AGENT_PROVIDER",
    "AGENT_MODEL",
    "AGENT_MCP_SERVERS",
    "AGENT_PROVIDER_BASE_URL",
    "AGENT_PROVIDER_API_KEY",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_API_KEY",
)

TELEMETRY_ENV = (
    "TRACEPARENT",
    "TRACESTATE",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_SERVICE_NAME",
    "DEPLOYMENT_ENVIRONMENT",
    "LANGFUSE_ENABLED",
    "LANGFUSE_MAX_IO_BYTES",
    "OPENINFERENCE_HIDE_INPUTS",
    "OPENINFERENCE_HIDE_OUTPUTS",
    "AGENTFLOW_RUN_ID",
    "AGENTFLOW_NODE_ID",
    "AGENTFLOW_AGENT_JOB_ID",
)

RUNTIME_CONTROL_ENV = (
    "AGENT_RUNTIME_TIMEOUT_SECONDS",
    "AGENT_RUNTIME_MAX_STEPS",
)

PASSTHROUGH_ENV = MODEL_PASSTHROUGH_ENV + TELEMETRY_ENV + RUNTIME_CONTROL_ENV

MODEL_ENV = MODEL_PASSTHROUGH_ENV + (
    "OPENAI_COMPATIBLE_BASE_URL",
    "OPENAI_COMPATIBLE_API_KEY",
    "ANTHROPIC_API_KEY",
)

PUBLIC_AGENT_ERROR_CODES = {
    "INVALID_INPUT",
    "CONFIGURATION_ERROR",
    "TIMEOUT",
    "STEP_LIMIT",
    "OUTPUT_INVALID",
    "PROVIDER_OVERLOADED",
    "RATE_LIMITED",
    "PROVIDER_AUTH_ERROR",
    "MODEL_NOT_FOUND",
    "MODEL_REQUEST_REJECTED",
    "PROVIDER_UNAVAILABLE",
    "MCP_CONNECTION_FAILED",
    "CAPABILITY_UNSUPPORTED",
    "RUNTIME_START_FAILED",
    "AGENT_FAILED",
}

RUNTIME_DIAGNOSTIC_TYPE = "agentflow.runtime.log"
MAX_RUNTIME_DIAGNOSTIC_BYTES = 64 * 1024
MAX_RUNTIME_DIAGNOSTICS = 100
RUNTIME_DIAGNOSTIC_FIELDS = {
    "runtime",
    "event",
    "run_id",
    "node_id",
    "agent_job_id",
    "session_id",
    "model",
    "status",
    "error_code",
    "attempt",
    "timeout_ms",
    "elapsed_ms",
}


def _public_agent_error(
    stdout: bytes, max_output_bytes: int
) -> tuple[str, str, str | None] | None:
    """Read only the Agent runtime's bounded, explicitly public error contract."""
    if not stdout or len(stdout) > max_output_bytes:
        return None
    try:
        document = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict) or not isinstance(document.get("error"), dict):
        return None
    error = document["error"]
    code = error.get("code")
    message = error.get("message")
    detail = error.get("detail")
    if (
        not isinstance(code, str)
        or code not in PUBLIC_AGENT_ERROR_CODES
        or not isinstance(message, str)
    ):
        return None
    message = message.strip()
    if not message or len(message) > 500 or any(ord(char) < 32 for char in message):
        return None
    if detail is not None:
        if not isinstance(detail, str):
            return None
        detail = detail.strip()
        if (
            not detail
            or len(detail) > 500
            or any(ord(char) < 32 for char in detail)
        ):
            return None
    return code, message, detail


def _runtime_diagnostics(stderr: bytes) -> list[dict[str, str | int | bool]]:
    """Accept only the bounded, structured diagnostic contract from a runtime."""
    if not stderr:
        return []
    try:
        text = stderr[:MAX_RUNTIME_DIAGNOSTIC_BYTES].decode("utf-8")
    except UnicodeDecodeError:
        return []
    diagnostics: list[dict[str, str | int | bool]] = []
    for line in text.splitlines()[:MAX_RUNTIME_DIAGNOSTICS]:
        try:
            document = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(document, dict) or document.get("type") != RUNTIME_DIAGNOSTIC_TYPE:
            continue
        diagnostic: dict[str, str | int | bool] = {}
        for key in RUNTIME_DIAGNOSTIC_FIELDS:
            value = document.get(key)
            if isinstance(value, (bool, int)) or (
                isinstance(value, str) and len(value) <= 300
            ):
                diagnostic[key] = value
        if diagnostic.get("runtime") and diagnostic.get("event"):
            diagnostics.append(diagnostic)
    return diagnostics


def _log_runtime_diagnostics(
    stderr: bytes,
    execution: Any,
) -> None:
    for diagnostic in _runtime_diagnostics(stderr):
        run_id = str(execution.run_id) if execution is not None else diagnostic.get("run_id")
        node_id = execution.node_id if execution is not None else diagnostic.get("node_id")
        runtime_event = str(diagnostic["event"])
        logger.info(
            "Agent runtime diagnostic runtime=%s event=%s elapsed_ms=%s",
            diagnostic["runtime"],
            runtime_event,
            diagnostic.get("elapsed_ms", "unknown"),
            extra={
                "event": "agent.runtime.diagnostic",
                "run_id": run_id,
                "node_id": node_id,
                "status": diagnostic.get("status"),
                "error_code": diagnostic.get("error_code"),
                "runtime": diagnostic.get("runtime"),
                "runtime_event": runtime_event,
                "agent_job_id": diagnostic.get("agent_job_id"),
                "session_id": diagnostic.get("session_id"),
                "model": diagnostic.get("model"),
                "elapsed_ms": diagnostic.get("elapsed_ms"),
            },
        )


@dataclass(frozen=True)
class AgentContainerSettings:
    image: str
    docker_binary: str
    timeout_seconds: float
    cpus: str
    memory: str
    pids_limit: int
    network: str
    max_output_bytes: int
    pull_policy: str

    @classmethod
    def from_env(cls) -> "AgentContainerSettings":
        return cls(
            image=os.getenv("AGENT_RUNTIME_IMAGE", "agentflow-agent-default:0.1.0"),
            docker_binary=os.getenv("DOCKER_BINARY", "docker"),
            timeout_seconds=float(os.getenv("AGENT_RUNTIME_TIMEOUT_SECONDS", "180")),
            cpus=os.getenv("AGENT_RUNTIME_CPUS", "1"),
            memory=os.getenv("AGENT_RUNTIME_MEMORY", "1g"),
            pids_limit=int(os.getenv("AGENT_RUNTIME_PIDS_LIMIT", "128")),
            network=os.getenv("AGENT_RUNTIME_NETWORK", "bridge"),
            max_output_bytes=int(
                os.getenv("AGENT_RUNTIME_MAX_OUTPUT_BYTES", str(1024 * 1024))
            ),
            pull_policy=os.getenv("AGENT_RUNTIME_PULL_POLICY", "never"),
        )


_global_semaphore: asyncio.Semaphore | None = None


def _shared_semaphore() -> asyncio.Semaphore:
    global _global_semaphore
    if _global_semaphore is None:
        _global_semaphore = asyncio.Semaphore(
            max(1, int(os.getenv("AGENT_RUNTIME_MAX_CONTAINERS", "2")))
        )
    return _global_semaphore


def build_agent_docker_command(
    settings: AgentContainerSettings,
    container_name: str,
    execution_labels: dict[str, str] | None = None,
) -> list[str]:
    """Build argv without embedding credentials or user-controlled input."""
    command = [
        settings.docker_binary,
        "run",
        "--rm",
        "--interactive",
        "--name",
        container_name,
        "--user",
        "10001:10001",
        "--label",
        "io.agentflow.managed=true",
        "--label",
        f"io.agentflow.job-id={container_name.removeprefix('agentflow-agent-')}",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(settings.pids_limit),
        "--cpus",
        settings.cpus,
        "--memory",
        settings.memory,
        "--network",
        settings.network,
        "--pull",
        settings.pull_policy,
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=128m",
    ]
    for name, value in sorted((execution_labels or {}).items()):
        command.extend(("--label", f"io.agentflow.{name}={value}"))
    command.extend(("--add-host", "host.docker.internal:host-gateway"))
    for name in PASSTHROUGH_ENV:
        command.extend(("--env", name))
    command.extend(("--entrypoint", "agent", settings.image))
    return command


class AgentContainerExecutor:
    def __init__(
        self,
        settings: AgentContainerSettings | None = None,
        *,
        runner: ContainerRunner = run_container_process,
        semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        self.settings = settings or AgentContainerSettings.from_env()
        self._runner = runner
        self._semaphore = semaphore or _shared_semaphore()

    @tracer.start_as_current_span("agent.container.run")
    async def execute(
        self,
        request: dict[str, Any],
        *,
        environment_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            payload = json.dumps(
                request, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise WorkflowExecutionError(
                "Agent runtime request is not JSON serializable"
            ) from exc
        if len(payload) > 1024 * 1024:
            raise WorkflowExecutionError(
                "Agent runtime request exceeds the 1 MiB limit"
            )

        job_id = uuid.uuid4().hex
        container_name = f"agentflow-agent-{job_id}"
        span = trace.get_current_span()
        span.set_attributes(
            {
                "agentflow.agent.job_id": job_id,
                "agentflow.agent.runtime": self.settings.image,
            }
        )
        annotate_langfuse_span(
            span,
            "span",
            input_data=request,
            metadata={
                "agent_job_id": job_id,
                "agent_runtime": self.settings.image,
            },
        )
        execution = current_execution_context()
        execution_labels = None
        if execution is not None:
            execution_labels = {
                "run-id": str(execution.run_id),
                "lease-generation": str(execution.lease_generation),
            }
            if execution.node_id:
                execution_labels["node-id"] = execution.node_id
        command = build_agent_docker_command(
            self.settings, container_name, execution_labels
        )
        environment = dict(os.environ)
        # Model credentials must come from the workflow/provider selection for
        # this invocation, never from stale defaults on the worker process.
        for name in MODEL_ENV:
            environment.pop(name, None)
        if environment_overrides:
            environment.update(environment_overrides)
        environment["OTEL_SERVICE_NAME"] = "agentflow-agent-runtime"
        environment["AGENTFLOW_AGENT_JOB_ID"] = job_id
        if execution is not None:
            environment["AGENTFLOW_RUN_ID"] = str(execution.run_id)
            if execution.node_id:
                environment["AGENTFLOW_NODE_ID"] = execution.node_id
        carrier: dict[str, str] = {}
        inject(carrier)
        if traceparent := carrier.get("traceparent"):
            environment["TRACEPARENT"] = traceparent
        if tracestate := carrier.get("tracestate"):
            environment["TRACESTATE"] = tracestate
        async with self._semaphore:
            result = await self._runner(
                command,
                container_name,
                payload,
                environment,
                self.settings.timeout_seconds,
            )
        _log_runtime_diagnostics(result.stderr, execution)
        if result.returncode != 0:
            public_error = _public_agent_error(
                result.stdout, self.settings.max_output_bytes
            )
            logger.warning(
                "Agent runtime container %s exited unsuccessfully "
                "(code=%s, agent_error=%s, stderr_bytes=%s)",
                container_name,
                result.returncode,
                public_error[0] if public_error else "unavailable",
                len(result.stderr),
            )
            if public_error:
                code, message, detail = public_error
                raise WorkflowExecutionError(message, code=code, detail=detail)
            raise WorkflowExecutionError("Agent runtime container failed")
        if len(result.stdout) > self.settings.max_output_bytes:
            raise WorkflowExecutionError(
                "Agent runtime output exceeds the configured limit"
            )
        try:
            result_document = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkflowExecutionError("Agent runtime returned invalid JSON") from exc
        if not isinstance(result_document, dict) or not isinstance(
            result_document.get("output"), dict
        ):
            raise WorkflowExecutionError(
                "Agent runtime must return a RunResult JSON object"
            )
        output = result_document["output"]
        annotate_langfuse_span(span, "span", output_data=output)
        return output
