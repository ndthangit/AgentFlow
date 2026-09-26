"""Run the Agent implementation from src/agent in a disposable container."""

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any

from domain.errors import WorkflowExecutionError
from runtime.container_process import (
    ContainerRunner,
    run_container_process,
)

logger = logging.getLogger(__name__)

PASSTHROUGH_ENV = (
    "AGENT_PROVIDER",
    "AGENT_MODEL",
    "AGENT_MCP_SERVERS",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_API_KEY",
)

MODEL_ENV = PASSTHROUGH_ENV + (
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
    "AGENT_FAILED",
}


def _public_agent_error(
    stdout: bytes, max_output_bytes: int
) -> tuple[str, str] | None:
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
    if (
        not isinstance(code, str)
        or code not in PUBLIC_AGENT_ERROR_CODES
        or not isinstance(message, str)
    ):
        return None
    message = message.strip()
    if not message or len(message) > 500 or any(ord(char) < 32 for char in message):
        return None
    return code, message


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
            image=os.getenv("AGENT_RUNTIME_IMAGE", "agentflow-agent-runtime:0.1.0"),
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
    settings: AgentContainerSettings, container_name: str
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
        command = build_agent_docker_command(self.settings, container_name)
        environment = dict(os.environ)
        # Model credentials must come from the workflow/provider selection for
        # this invocation, never from stale defaults on the worker process.
        for name in MODEL_ENV:
            environment.pop(name, None)
        if environment_overrides:
            environment.update(environment_overrides)
        async with self._semaphore:
            result = await self._runner(
                command,
                container_name,
                payload,
                environment,
                self.settings.timeout_seconds,
            )
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
                code, message = public_error
                raise WorkflowExecutionError(message, code=code)
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
        return result_document["output"]
