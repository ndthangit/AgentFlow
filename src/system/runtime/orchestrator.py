"""Transactional-outbox orchestrator that publishes workflow runs to Redis."""

import asyncio
import logging
import os
import signal
from datetime import UTC, datetime, timedelta

from opentelemetry import trace
from opentelemetry.propagate import extract
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SessionFactory
from core.telemetry import annotate_langfuse_span, record_outbox_dispatch
from domain.models import FlowRun, RunDispatch
from runtime.queue import RedisRunQueue

logger = logging.getLogger("agentflow.orchestrator")
tracer = trace.get_tracer("agentflow.orchestrator")


async def dispatch_batch(
    session: AsyncSession, queue: RedisRunQueue, batch_size: int = 50
) -> int:
    redispatch_before = datetime.now(UTC) - timedelta(
        seconds=max(
            10.0, float(os.getenv("WORKFLOW_OUTBOX_REDISPATCH_SECONDS", "60"))
        )
    )
    result = await session.scalars(
        select(RunDispatch)
        .join(FlowRun, FlowRun.id == RunDispatch.run_id)
        .where(
            or_(
                RunDispatch.dispatched_at.is_(None),
                (
                    (FlowRun.status == "pending")
                    & (RunDispatch.dispatched_at < redispatch_before)
                ),
            )
        )
        .order_by(RunDispatch.created_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    dispatches = list(result)
    dispatched_count = 0
    for dispatch in dispatches:
        dispatch.attempts = (dispatch.attempts or 0) + 1
        carrier = {
            key: value
            for key, value in {
                "traceparent": dispatch.traceparent,
                "tracestate": dispatch.tracestate,
            }.items()
            if value
        }
        parent_context = extract(carrier)
        with tracer.start_as_current_span(
            "workflow.dispatch",
            context=parent_context,
            attributes={"agentflow.run.id": str(dispatch.run_id)},
        ) as span:
            annotate_langfuse_span(
                span,
                "span",
                metadata={
                    "run_id": dispatch.run_id,
                    "trace_name": "workflow.run",
                    "stage": "dispatch",
                },
            )
            try:
                # Inject the current dispatch span into Redis so the worker becomes
                # its child while the original API context remains the trace root.
                await queue.enqueue(str(dispatch.run_id))
                dispatch.dispatched_at = datetime.now(UTC)
                dispatch.last_error = None
                dispatched_count += 1
                record_outbox_dispatch("succeeded")
            except Exception as exc:  # noqa: BLE001 - outbox stays retryable.
                dispatch.last_error = str(exc)[:500]
                record_outbox_dispatch("failed")
                logger.warning(
                    "Could not dispatch run %s: %s",
                    dispatch.run_id,
                    exc,
                    extra={
                        "event": "workflow.dispatch.failed",
                        "run_id": dispatch.run_id,
                    },
                )
    await session.commit()
    return dispatched_count


async def orchestrate(stop: asyncio.Event) -> None:
    """Continuously dispatch the outbox; safe to run inside FastAPI lifespan."""
    queue = RedisRunQueue.from_env()
    logger.info("Workflow orchestrator loop started")
    try:
        while not stop.is_set():
            try:
                async with SessionFactory() as session:
                    dispatched = await dispatch_batch(session, queue)
                delay = 0 if dispatched else 0.5
            except Exception:  # Keep the embedded orchestrator alive after outages.
                logger.exception("Workflow orchestrator iteration failed")
                delay = 1
            if delay:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except TimeoutError:
                    pass
    finally:
        await queue.close()
        logger.info("Workflow orchestrator loop stopped")


async def run_orchestrator() -> None:
    """Standalone entry point retained for local diagnostics."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stop.set)
    await orchestrate(stop)


if __name__ == "__main__":
    asyncio.run(run_orchestrator())
