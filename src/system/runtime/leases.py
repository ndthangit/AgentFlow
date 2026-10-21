"""Durable workflow ownership, fencing, and worker-loss recovery."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SessionFactory
from domain.models import FlowRun, RunExecutionLease, RunStep

WORKER_LOST_ERROR = {
    "code": "WORKER_LOST",
    "message": "The workflow worker stopped before the task completed.",
}
WORKFLOW_TIMEOUT_ERROR = {
    "code": "WORKFLOW_TIMEOUT",
    "message": "The workflow exceeded its configured execution timeout.",
}


class LeaseLostError(RuntimeError):
    """Raised when an old worker is no longer allowed to persist results."""


@dataclass(frozen=True)
class RunLeaseClaim:
    run_id: uuid.UUID
    owner_id: str
    generation: int


def _expires_at(now: datetime, ttl_seconds: float) -> datetime:
    return now + timedelta(seconds=ttl_seconds)


async def _fail_run_steps(
    session: AsyncSession,
    run_id: uuid.UUID,
    error: dict[str, str] = WORKER_LOST_ERROR,
) -> None:
    now = datetime.now(UTC)
    running_result = await session.execute(
        update(RunStep)
        .where(RunStep.run_id == run_id, RunStep.status == "running")
        .values(status="failed", error=error, completed_at=now)
    )
    if not running_result.rowcount:
        first_pending = await session.scalar(
            select(RunStep)
            .where(RunStep.run_id == run_id, RunStep.status == "pending")
            .order_by(RunStep.sequence)
            .limit(1)
            .with_for_update()
        )
        if first_pending is not None:
            first_pending.status = "failed"
            first_pending.error = error
            first_pending.completed_at = now
    await session.execute(
        update(RunStep)
        .where(RunStep.run_id == run_id, RunStep.status == "pending")
        .values(
            status="skipped",
            error={
                "code": "RUN_ABORTED",
                "message": "Skipped because the workflow run was terminated.",
            },
            completed_at=now,
        )
    )


async def _fail_running_run(
    session: AsyncSession,
    run_id: uuid.UUID,
    error: dict[str, str] = WORKER_LOST_ERROR,
) -> bool:
    result = await session.execute(
        update(FlowRun)
        .where(FlowRun.id == run_id, FlowRun.status == "running")
        .values(status="failed")
    )
    if not result.rowcount:
        return False
    await _fail_run_steps(session, run_id, error)
    return True


async def claim_run(
    run_id: uuid.UUID,
    *,
    owner_id: str,
    message_id: str,
    reclaimed: bool,
    ttl_seconds: float,
) -> RunLeaseClaim | None:
    """Claim a pending run, or terminalize an expired reclaimed run.

    A reclaimed Redis delivery never grants execution ownership by itself. The
    PostgreSQL lease is authoritative and prevents blind workflow re-execution.
    """

    now = datetime.now(UTC)
    async with SessionFactory() as session:
        # Always lock an existing lease before the run row. Persistence and
        # recovery use the same order, avoiding lease/run deadlocks.
        lease = await session.scalar(
            select(RunExecutionLease)
            .where(RunExecutionLease.run_id == run_id)
            .with_for_update()
        )
        flow_run = await session.scalar(
            select(FlowRun).where(FlowRun.id == run_id).with_for_update()
        )
        if flow_run is None:
            return None
        if flow_run.status == "pending":
            flow_run.status = "running"
            generation = (lease.generation + 1) if lease is not None else 1
            if lease is None:
                lease = RunExecutionLease(
                    run_id=run_id,
                    owner_id=owner_id,
                    generation=generation,
                    message_id=message_id,
                    status="active",
                    heartbeat_at=now,
                    lease_expires_at=_expires_at(now, ttl_seconds),
                    cleanup_status="pending",
                )
                session.add(lease)
            else:
                lease.owner_id = owner_id
                lease.generation = generation
                lease.message_id = message_id
                lease.status = "active"
                lease.heartbeat_at = now
                lease.lease_expires_at = _expires_at(now, ttl_seconds)
                lease.cleanup_status = "pending"
                lease.cleanup_error = None
            await session.commit()
            return RunLeaseClaim(run_id, owner_id, generation)

        if (
            reclaimed
            and flow_run.status == "running"
            and (lease is None or lease.lease_expires_at <= now)
        ):
            await _fail_running_run(session, run_id)
            if lease is not None:
                lease.status = "expired"
                lease.cleanup_status = "pending"
            await session.commit()
        return None


async def renew_run_lease(claim: RunLeaseClaim, ttl_seconds: float) -> bool:
    now = datetime.now(UTC)
    async with SessionFactory() as session:
        result = await session.execute(
            update(RunExecutionLease)
            .where(
                RunExecutionLease.run_id == claim.run_id,
                RunExecutionLease.owner_id == claim.owner_id,
                RunExecutionLease.generation == claim.generation,
                RunExecutionLease.status == "active",
            )
            .values(
                heartbeat_at=now,
                lease_expires_at=_expires_at(now, ttl_seconds),
                cleanup_error=None,
            )
        )
        await session.commit()
        return bool(result.rowcount)


async def lock_owned_lease(
    session: AsyncSession, claim: RunLeaseClaim
) -> RunExecutionLease:
    lease = await session.scalar(
        select(RunExecutionLease)
        .where(RunExecutionLease.run_id == claim.run_id)
        .with_for_update()
    )
    now = datetime.now(UTC)
    if (
        lease is None
        or lease.owner_id != claim.owner_id
        or lease.generation != claim.generation
        or lease.status != "active"
        or lease.lease_expires_at <= now
    ):
        raise LeaseLostError(f"Execution lease for run {claim.run_id} was lost")
    return lease


async def fail_owned_run(
    claim: RunLeaseClaim, *, code: str, message: str
) -> bool:
    async with SessionFactory() as session:
        try:
            lease = await lock_owned_lease(session, claim)
        except LeaseLostError:
            return False
        failed = await _fail_running_run(
            session,
            claim.run_id,
            {"code": code, "message": message},
        )
        lease.status = "released"
        lease.cleanup_status = "pending"
        await session.commit()
        return failed


async def recover_expired_runs(limit: int = 100) -> list[uuid.UUID]:
    """Fence expired/unleased runs and make their visible state terminal."""

    now = datetime.now(UTC)
    recovered: list[uuid.UUID] = []
    async with SessionFactory() as session:
        leases = list(
            await session.scalars(
                select(RunExecutionLease)
                .where(
                    RunExecutionLease.status == "active",
                    RunExecutionLease.lease_expires_at <= now,
                )
                .order_by(RunExecutionLease.lease_expires_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for lease in leases:
            if await _fail_running_run(session, lease.run_id):
                recovered.append(lease.run_id)
            lease.status = "expired"
            lease.cleanup_status = "pending"

        # Handles runs created before the lease migration or a crash between
        # changing the run state and inserting its lease.
        unleased_runs = list(
            await session.scalars(
                select(FlowRun)
                .outerjoin(
                    RunExecutionLease, RunExecutionLease.run_id == FlowRun.id
                )
                .where(
                    FlowRun.status == "running",
                    RunExecutionLease.run_id.is_(None),
                )
                .limit(max(0, limit - len(leases)))
                .with_for_update(skip_locked=True, of=FlowRun)
            )
        )
        for flow_run in unleased_runs:
            if await _fail_running_run(session, flow_run.id):
                recovered.append(flow_run.id)
        await session.commit()
    return recovered


async def recover_timed_out_runs(
    timeout_seconds: float,
    limit: int = 100,
) -> list[uuid.UUID]:
    """Fail runs that exceeded the global wall-clock execution deadline."""

    cutoff = datetime.now(UTC) - timedelta(seconds=timeout_seconds)
    recovered: list[uuid.UUID] = []
    async with SessionFactory() as session:
        leases = list(
            await session.scalars(
                select(RunExecutionLease)
                .join(FlowRun, FlowRun.id == RunExecutionLease.run_id)
                .where(
                    RunExecutionLease.status == "active",
                    FlowRun.status == "running",
                    FlowRun.updated_at <= cutoff,
                )
                .order_by(FlowRun.updated_at)
                .limit(limit)
                .with_for_update(skip_locked=True, of=RunExecutionLease)
            )
        )
        for lease in leases:
            if await _fail_running_run(
                session,
                lease.run_id,
                WORKFLOW_TIMEOUT_ERROR,
            ):
                recovered.append(lease.run_id)
            lease.status = "expired"
            lease.cleanup_status = "pending"

        remaining = max(0, limit - len(recovered))
        if remaining:
            pending_runs = list(
                await session.scalars(
                    select(FlowRun)
                    .where(
                        FlowRun.status == "pending",
                        FlowRun.created_at <= cutoff,
                    )
                    .order_by(FlowRun.created_at)
                    .limit(remaining)
                    .with_for_update(skip_locked=True)
                )
            )
            for flow_run in pending_runs:
                flow_run.status = "failed"
                await _fail_run_steps(
                    session,
                    flow_run.id,
                    WORKFLOW_TIMEOUT_ERROR,
                )
                recovered.append(flow_run.id)

        await session.commit()
    return recovered
