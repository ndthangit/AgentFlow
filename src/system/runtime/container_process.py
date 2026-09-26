"""Shared subprocess boundary for disposable Docker runtimes."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from domain.errors import WorkflowExecutionError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContainerResult:
    returncode: int
    stdout: bytes
    stderr: bytes


ContainerRunner = Callable[
    [list[str], str, bytes, dict[str, str], float], Awaitable[ContainerResult]
]


async def _force_remove(docker_binary: str, container_name: str) -> None:
    try:
        process = await asyncio.create_subprocess_exec(
            docker_binary,
            "rm",
            "--force",
            container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(process.wait(), timeout=15)
    except (OSError, TimeoutError):
        logger.warning("Could not confirm cleanup for container %s", container_name)


async def run_container_process(
    command: list[str],
    container_name: str,
    payload: bytes,
    environment: dict[str, str],
    timeout_seconds: float,
) -> ContainerResult:
    """Run a container, then force cleanup even after timeout or cancellation."""
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(payload), timeout=timeout_seconds
        )
        return ContainerResult(process.returncode or 0, stdout, stderr)
    except TimeoutError:
        raise WorkflowExecutionError("Agent container timed out") from None
    except FileNotFoundError:
        raise WorkflowExecutionError(
            "Docker CLI is not available to the worker"
        ) from None
    except OSError:
        raise WorkflowExecutionError("Agent container could not be started") from None
    finally:
        await _force_remove(command[0], container_name)
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
