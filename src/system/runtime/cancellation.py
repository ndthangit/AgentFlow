"""Durable cancellation handling for workflow executions."""

import asyncio
import contextlib
import logging
import os
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SessionFactory
from domain.models import (
    FlowRun,
    NodeAttempt,
    NodeAttemptStatus,
    RunCommand,
    RunCommandStatus,
    RunCommandType,
    RunStep,
)
from runtime.leases import LeaseLostError, RunLeaseClaim, lock_owned_lease

logger = logging.getLogger(__name__)
DEFAULT_CANCEL_CHANNEL_PREFIX = "agentflow:workflow-cancel"

CANCELLED_ERROR = {
    "code": "RUN_CANCELLED",
    "message": "The workflow run was stopped by the user.",
}


class RunCancellationRequested(RuntimeError):
    """Raised inside the worker when a durable cancel command is observed."""


def cancellation_channel(run_id: uuid.UUID) -> str:
    prefix = os.getenv(
        "WORKFLOW_CANCEL_CHANNEL_PREFIX", DEFAULT_CANCEL_CHANNEL_PREFIX
    ).rstrip(":")
    return f"{prefix}:{run_id}"


async def accepted_cancel_command(
    session: AsyncSession,
    run_id: uuid.UUID,
) -> RunCommand | None:
    return await session.scalar(
        select(RunCommand)
        .where(
            RunCommand.run_id == run_id,
            RunCommand.type == RunCommandType.CANCEL,
            RunCommand.status == RunCommandStatus.ACCEPTED,
        )
        .order_by(RunCommand.created_at, RunCommand.id)
        .limit(1)
    )


async def _cancellation_is_durable(run_id: uuid.UUID) -> bool:
    try:
        async with SessionFactory() as session:
            return await accepted_cancel_command(session, run_id) is not None
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Could not read cancellation state for run %s", run_id)
        return False


async def wait_for_run_cancellation(
    run_id: uuid.UUID,
) -> None:
    """Block on a Redis event, using the durable command only for race recovery."""

    if await _cancellation_is_durable(run_id):
        return

    reconnect_delay = 1.0
    while True:
        client = Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
            socket_connect_timeout=2,
            health_check_interval=30,
        )
        pubsub = client.pubsub()
        try:
            channel = cancellation_channel(run_id)
            await pubsub.subscribe(channel)

            # A command may have committed between the first DB check and
            # SUBSCRIBE. This second one closes that one-time race window.
            if await _cancellation_is_durable(run_id):
                return

            async for message in pubsub.listen():
                if message.get("type") == "message":
                    return
        except asyncio.CancelledError:
            raise
        except (RedisError, OSError):
            logger.exception(
                "Cancellation event connection failed for run %s; reconnecting",
                run_id,
            )
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 10.0)
        finally:
            with contextlib.suppress(Exception):
                await pubsub.aclose()
            with contextlib.suppress(Exception):
                await client.aclose()


async def publish_run_cancellation(run_id: uuid.UUID) -> int:
    """Wake the worker executing a run after its durable command commits."""

    client = Redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
        socket_connect_timeout=2,
    )
    try:
        return int(await client.publish(cancellation_channel(run_id), str(run_id)))
    finally:
        await client.aclose()


async def apply_run_cancellation(claim: RunLeaseClaim) -> bool:
    """Persist cancellation after the owned runtime has stopped."""

    now = datetime.now(UTC)
    async with SessionFactory() as session:
        try:
            lease = await lock_owned_lease(session, claim)
        except LeaseLostError:
            return False

        flow_run = await session.scalar(
            select(FlowRun).where(FlowRun.id == claim.run_id).with_for_update()
        )
        if flow_run is None or flow_run.status != "running":
            return False

        commands = list(
            await session.scalars(
                select(RunCommand)
                .where(
                    RunCommand.run_id == claim.run_id,
                    RunCommand.type == RunCommandType.CANCEL,
                    RunCommand.status == RunCommandStatus.ACCEPTED,
                )
                .with_for_update()
            )
        )
        if not commands:
            return False

        flow_run.status = "cancelled"
        flow_run.output = None
        await session.execute(
            update(RunStep)
            .where(
                RunStep.run_id == claim.run_id,
                RunStep.status.in_(("pending", "running")),
            )
            .values(status="cancelled", error=CANCELLED_ERROR, completed_at=now)
        )
        run_step_ids = select(RunStep.id).where(RunStep.run_id == claim.run_id)
        await session.execute(
            update(NodeAttempt)
            .where(
                NodeAttempt.run_step_id.in_(run_step_ids),
                NodeAttempt.status.in_(
                    (NodeAttemptStatus.QUEUED, NodeAttemptStatus.RUNNING)
                ),
            )
            .values(
                status=NodeAttemptStatus.CANCELLED,
                error_code=CANCELLED_ERROR["code"],
                error=CANCELLED_ERROR,
                completed_at=now,
            )
        )
        for command in commands:
            command.status = RunCommandStatus.APPLIED
            command.result = {"status": "cancelled"}
            command.applied_at = now
        lease.status = "released"
        lease.cleanup_status = "pending"
        await session.commit()
        return True
