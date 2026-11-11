"""Run code.python nodes in isolated, disposable Docker containers."""

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

from domain.errors import WorkflowExecutionError
from runtime.container_process import ContainerRunner, run_container_process
from runtime.execution_context import current_execution_context


@dataclass(frozen=True)
class PythonSandboxSettings:
    image: str
    docker_binary: str
    timeout_seconds: float
    cpus: str
    memory: str
    pids_limit: int
    max_input_bytes: int
    max_output_bytes: int

    @classmethod
    def from_env(cls) -> "PythonSandboxSettings":
        return cls(
            image=os.getenv("PYTHON_SANDBOX_IMAGE", "agentflow-system"),
            docker_binary=os.getenv("DOCKER_BINARY", "docker"),
            timeout_seconds=float(os.getenv("PYTHON_SANDBOX_TIMEOUT_SECONDS", "10")),
            cpus=os.getenv("PYTHON_SANDBOX_CPUS", "0.5"),
            memory=os.getenv("PYTHON_SANDBOX_MEMORY", "256m"),
            pids_limit=int(os.getenv("PYTHON_SANDBOX_PIDS_LIMIT", "32")),
            max_input_bytes=int(
                os.getenv("PYTHON_SANDBOX_MAX_INPUT_BYTES", str(1024 * 1024))
            ),
            max_output_bytes=int(
                os.getenv("PYTHON_SANDBOX_MAX_OUTPUT_BYTES", str(1024 * 1024))
            ),
        )


_global_semaphore: asyncio.Semaphore | None = None


def _shared_semaphore() -> asyncio.Semaphore:
    global _global_semaphore
    if _global_semaphore is None:
        _global_semaphore = asyncio.Semaphore(
            max(1, int(os.getenv("PYTHON_SANDBOX_MAX_CONTAINERS", "4")))
        )
    return _global_semaphore


def build_python_sandbox_command(
    settings: PythonSandboxSettings,
    container_name: str,
    execution_labels: dict[str, str] | None = None,
) -> list[str]:
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
        f"io.agentflow.job-id={container_name.removeprefix('agentflow-python-')}",
        "--label",
        "io.agentflow.runtime=python-sandbox",
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
        "none",
        "--pull",
        "never",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=64m",
    ]
    for name, value in sorted((execution_labels or {}).items()):
        command.extend(("--label", f"io.agentflow.{name}={value}"))
    command.extend(
        (
            "--entrypoint",
            "python",
            settings.image,
            "-m",
            "runtime.python_sandbox_runner",
        )
    )
    return command


class PythonSandboxExecutor:
    def __init__(
        self,
        settings: PythonSandboxSettings | None = None,
        *,
        runner: ContainerRunner = run_container_process,
        semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        self.settings = settings or PythonSandboxSettings.from_env()
        self._runner = runner
        self._semaphore = semaphore or _shared_semaphore()

    async def execute(self, code: str, inputs: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = json.dumps(
                {"code": code, "inputs": inputs},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise WorkflowExecutionError(
                "Python sandbox request is not JSON serializable"
            ) from exc
        if len(payload) > self.settings.max_input_bytes:
            raise WorkflowExecutionError("Python sandbox request is too large")

        job_id = uuid.uuid4().hex
        container_name = f"agentflow-python-{job_id}"
        execution = current_execution_context()
        labels = None
        if execution is not None:
            labels = {
                "run-id": str(execution.run_id),
                "lease-generation": str(execution.lease_generation),
            }
            if execution.node_id:
                labels["node-id"] = execution.node_id
        command = build_python_sandbox_command(
            self.settings, container_name, labels
        )
        async with self._semaphore:
            result = await self._runner(
                command,
                container_name,
                payload,
                dict(os.environ),
                self.settings.timeout_seconds,
            )
        if len(result.stdout) > self.settings.max_output_bytes:
            raise WorkflowExecutionError("Python sandbox output is too large")
        try:
            document = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            message = (
                "Python sandbox failed"
                if result.returncode != 0
                else "Python sandbox returned invalid JSON"
            )
            raise WorkflowExecutionError(message) from exc
        if not isinstance(document, dict):
            raise WorkflowExecutionError("Python sandbox returned invalid JSON")
        if result.returncode != 0:
            error = document.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            raise WorkflowExecutionError(
                message if isinstance(message, str) else "Python sandbox failed"
            )
        if not isinstance(document.get("output"), dict):
            raise WorkflowExecutionError("Python sandbox must return a JSON object")
        return document["output"]
