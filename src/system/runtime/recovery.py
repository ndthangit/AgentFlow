"""Recover lost workflow workers and reap their disposable containers."""

import asyncio
import json
import logging
import os
import signal
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from core.database import SessionFactory
from domain.models import RunExecutionLease
from runtime.leases import recover_expired_runs, recover_timed_out_runs

logger = logging.getLogger("agentflow.recovery")


@dataclass(frozen=True)
class ManagedContainer:
    container_id: str
    run_id: uuid.UUID | None
    lease_generation: int | None


@dataclass(frozen=True)
class CleanupFailure:
    run_id: uuid.UUID | None
    detail: str


async def _docker(
    *arguments: str, timeout: float = 30
) -> tuple[int, bytes, bytes]:
    binary = os.getenv("DOCKER_BINARY", "docker")
    process = await asyncio.create_subprocess_exec(
        binary,
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(f"Docker command timed out: {arguments[0]}") from None
    return process.returncode or 0, stdout, stderr


async def list_managed_containers() -> list[ManagedContainer]:
    code, stdout, stderr = await _docker(
        "ps", "-aq", "--filter", "label=io.agentflow.managed=true"
    )
    if code != 0:
        raise RuntimeError(
            f"Could not list managed containers (docker exit {code}, "
            f"stderr_bytes={len(stderr)})"
        )
    containers: list[ManagedContainer] = []
    for raw_id in stdout.decode("utf-8", errors="replace").splitlines():
        container_id = raw_id.strip()
        if not container_id:
            continue
        inspect_code, labels_json, inspect_stderr = await _docker(
            "inspect", "--format", "{{json .Config.Labels}}", container_id
        )
        if inspect_code != 0:
            raise RuntimeError(
                f"Could not inspect managed container {container_id} "
                f"(stderr_bytes={len(inspect_stderr)})"
            )
        try:
            labels = json.loads(labels_json)
            run_id = uuid.UUID(labels.get("io.agentflow.run-id", ""))
            generation = int(labels.get("io.agentflow.lease-generation", ""))
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            run_id = None
            generation = None
        containers.append(ManagedContainer(container_id, run_id, generation))
    return containers


async def _active_leases() -> dict[uuid.UUID, tuple[int, datetime]]:
    async with SessionFactory() as session:
        leases = list(
            await session.scalars(
                select(RunExecutionLease).where(
                    RunExecutionLease.status == "active"
                )
            )
        )
    return {
        lease.run_id: (lease.generation, lease.lease_expires_at) for lease in leases
    }


async def reap_orphan_containers() -> tuple[int, list[CleanupFailure]]:
    """Remove every managed container not protected by a live DB lease."""

    containers = await list_managed_containers()
    active = await _active_leases()
    now = datetime.now(UTC)
    removed = 0
    errors: list[CleanupFailure] = []
    for container in containers:
        lease = active.get(container.run_id) if container.run_id else None
        if (
            lease is not None
            and container.lease_generation == lease[0]
            and lease[1] > now
        ):
            continue
        code, _stdout, stderr = await _docker(
            "rm", "--force", container.container_id
        )
        if code == 0:
            removed += 1
        else:
            errors.append(
                CleanupFailure(
                    container.run_id,
                    f"{container.container_id}: docker exit {code}, "
                    f"stderr_bytes={len(stderr)}",
                )
            )
    return removed, errors


async def mark_cleanup_results(errors: list[CleanupFailure]) -> None:
    """Persist whether all currently pending cleanup work was completed."""

    async with SessionFactory() as session:
        leases = list(
            await session.scalars(
                select(RunExecutionLease)
                .where(
                    RunExecutionLease.cleanup_status == "pending",
                    RunExecutionLease.status != "active",
                )
                .with_for_update(skip_locked=True)
            )
        )
        for lease in leases:
            matching_errors = [error for error in errors if error.run_id == lease.run_id]
            if matching_errors:
                lease.cleanup_error = matching_errors[0].detail[:500]
            else:
                lease.cleanup_status = "cleaned"
                lease.cleanup_error = None
        await session.commit()


async def recovery_iteration() -> tuple[int, int]:
    timeout_seconds = max(
        1.0,
        float(os.getenv("WORKFLOW_TIMEOUT_SECONDS", "900")),
    )
    timed_out = await recover_timed_out_runs(timeout_seconds)
    recovered = await recover_expired_runs()
    removed, errors = await reap_orphan_containers()
    await mark_cleanup_results(errors)
    if errors:
        logger.warning("Container cleanup had %s error(s)", len(errors))
    recovered_count = len(timed_out) + len(recovered)
    if recovered_count or removed:
        logger.warning(
            "Recovery reconciled %s failed/expired run(s) and removed %s container(s)",
            recovered_count,
            removed,
        )
    return recovered_count, removed


async def run_recovery_controller() -> None:
    interval = max(1.0, float(os.getenv("WORKFLOW_RECOVERY_INTERVAL_SECONDS", "10")))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stop.set)
    logger.info("Workflow recovery controller started")
    while not stop.is_set():
        try:
            await recovery_iteration()
        except Exception:
            logger.exception("Workflow recovery iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
    logger.info("Workflow recovery controller stopped")


if __name__ == "__main__":
    asyncio.run(run_recovery_controller())
