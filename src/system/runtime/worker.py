"""Workflow worker consuming Redis Stream jobs and executing persisted versions."""

import asyncio
import contextlib
import logging
import os
import signal
import socket
import time
import uuid

from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.trace import Status, StatusCode
from redis.exceptions import RedisError
from sqlalchemy import select, update

from core.database import SessionFactory
from core.database import engine as database_engine
from core.telemetry import (
    annotate_langfuse_span,
    configure_telemetry,
    langfuse_trace_context,
    record_run_completed,
    record_worker_heartbeat,
)
from domain.models import FlowRun, LlmProvider, McpServer, RunStep, WorkflowVersion
from providers.adapters import validate_provider_endpoint_configuration
from providers.secrets import ProviderSecretStore
from runtime.agent_executor import create_agent_executor, create_llm_executor
from runtime.cancellation import (
    RunCancellationRequested,
    accepted_cancel_command,
    apply_run_cancellation,
    wait_for_run_cancellation,
)
from runtime.engine import ExecutionStep, WorkflowExecution, execute_workflow
from runtime.execution_context import bind_run_execution
from runtime.leases import (
    LeaseLostError,
    RunLeaseClaim,
    claim_run,
    fail_owned_run,
    lock_owned_lease,
    renew_run_lease,
)
from runtime.queue import RedisRunQueue, RunQueueMessage

configure_telemetry(
    "agentflow-workflow-worker", sqlalchemy_engine=database_engine.sync_engine
)
logger = logging.getLogger("agentflow.worker")
tracer = trace.get_tracer("agentflow.worker")


class WorkflowDeadlineExceeded(RuntimeError):
    """Raised only when the configured whole-workflow deadline expires."""


async def load_and_execute(
    run_id: uuid.UUID, claim: RunLeaseClaim
) -> WorkflowExecution:
    async with SessionFactory() as session:
        flow_run = await session.scalar(select(FlowRun).where(FlowRun.id == run_id))
        if flow_run is None:
            raise RuntimeError(f"Run {run_id} no longer exists")
        version = await session.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.id == flow_run.workflow_version_id
            )
        )
        if version is None:
            raise RuntimeError(f"Workflow version for run {run_id} no longer exists")
        provider_result = await session.scalars(
            select(LlmProvider)
            .where(
                LlmProvider.owner_subject == flow_run.owner_subject,
                LlmProvider.enabled.is_(True),
            )
            .order_by(LlmProvider.name)
        )
        providers = list(provider_result)
        mcp_result = await session.scalars(
            select(McpServer)
            .where(
                McpServer.owner_subject == flow_run.owner_subject,
                McpServer.enabled.is_(True),
            )
            .order_by(McpServer.name)
        )
        mcp_servers = list(mcp_result)
        graph = version.graph
        run_input = flow_run.input

    trace_metadata = {
        "run_id": run_id,
        "session_id": str(run_id),
        "workflow_id": version.workflow_id,
        "workflow_version_id": version.id,
        "workflow_version": version.version,
        "trace_name": "workflow.run",
    }
    annotate_langfuse_span(
        trace.get_current_span(),
        "chain",
        input_data=run_input,
        metadata=trace_metadata,
    )

    secret_store = ProviderSecretStore.from_config()
    execute_agent = create_agent_executor(
        graph, providers, secret_store, mcp_servers=mcp_servers
    )
    execute_llm = create_llm_executor(graph, providers, secret_store)

    async def persist_progress(step: ExecutionStep) -> None:
        await persist_step(run_id, step, claim)

    with (
        bind_run_execution(run_id, claim.generation),
        langfuse_trace_context(**trace_metadata),
    ):
        return await execute_workflow(
            graph,
            run_input,
            execute_agent,
            on_step_update=persist_progress,
            execute_llm=execute_llm,
        )


async def persist_step(
    run_id: uuid.UUID, step: ExecutionStep, claim: RunLeaseClaim
) -> None:
    """Commit one node transition so the run viewer can show live progress."""
    async with SessionFactory() as session:
        await lock_owned_lease(session, claim)
        await session.execute(
            update(RunStep)
            .where(RunStep.run_id == run_id, RunStep.sequence == step.sequence)
            .values(
                status=step.status,
                input=step.input,
                output=step.output,
                error=step.error,
                started_at=step.started_at,
                completed_at=step.completed_at,
            )
        )
        await session.commit()


async def persist_execution(
    run_id: uuid.UUID, execution: WorkflowExecution, claim: RunLeaseClaim
) -> None:
    async with SessionFactory() as session:
        lease = await lock_owned_lease(session, claim)
        flow_run = await session.scalar(
            select(FlowRun).where(FlowRun.id == run_id).with_for_update()
        )
        if flow_run is None or flow_run.status != "running":
            raise LeaseLostError(f"Run {run_id} is no longer running")
        if await accepted_cancel_command(session, run_id) is not None:
            raise RunCancellationRequested(f"Cancellation requested for run {run_id}")
        flow_run.status = execution.status
        flow_run.output = execution.output
        for step in execution.steps:
            await session.execute(
                update(RunStep)
                .where(RunStep.run_id == run_id, RunStep.sequence == step.sequence)
                .values(
                    status=step.status,
                    input=step.input,
                    output=step.output,
                    error=step.error,
                    started_at=step.started_at,
                    completed_at=step.completed_at,
                )
            )
        lease.status = "released"
        lease.cleanup_status = "pending"
        await session.commit()


async def _heartbeat_lease(
    claim: RunLeaseClaim,
    *,
    ttl_seconds: float,
    interval_seconds: float,
    lease_lost: asyncio.Event,
) -> None:
    loop = asyncio.get_running_loop()
    last_success = loop.time()
    while not lease_lost.is_set():
        await asyncio.sleep(interval_seconds)
        try:
            renewed = await renew_run_lease(claim, ttl_seconds)
        except Exception:
            logger.exception("Could not renew execution lease for run %s", claim.run_id)
            if loop.time() - last_success >= ttl_seconds:
                lease_lost.set()
            continue
        if not renewed:
            lease_lost.set()
            return
        last_success = loop.time()


async def _execute_with_heartbeat(
    run_id: uuid.UUID,
    claim: RunLeaseClaim,
    *,
    ttl_seconds: float,
    heartbeat_seconds: float,
) -> WorkflowExecution:
    lease_lost = asyncio.Event()
    heartbeat = asyncio.create_task(
        _heartbeat_lease(
            claim,
            ttl_seconds=ttl_seconds,
            interval_seconds=heartbeat_seconds,
            lease_lost=lease_lost,
        )
    )
    execution = asyncio.create_task(load_and_execute(run_id, claim))
    lost_waiter = asyncio.create_task(lease_lost.wait())
    cancel_waiter = asyncio.create_task(wait_for_run_cancellation(run_id))
    try:
        done, _pending = await asyncio.wait(
            {execution, lost_waiter, cancel_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if lost_waiter in done and lease_lost.is_set():
            execution.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await execution
            raise LeaseLostError(f"Execution lease for run {run_id} was lost")
        if cancel_waiter in done:
            execution.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await execution
            raise RunCancellationRequested(f"Cancellation requested for run {run_id}")
        return await execution
    finally:
        if not execution.done():
            execution.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await execution
        heartbeat.cancel()
        lost_waiter.cancel()
        cancel_waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        with contextlib.suppress(asyncio.CancelledError):
            await lost_waiter
        with contextlib.suppress(asyncio.CancelledError):
            await cancel_waiter


async def _execute_with_timeout(
    run_id: uuid.UUID,
    claim: RunLeaseClaim,
    *,
    ttl_seconds: float,
    heartbeat_seconds: float,
    timeout_seconds: float,
) -> WorkflowExecution:
    """Enforce the workflow deadline without masking inner TimeoutError values."""

    execution = asyncio.create_task(
        _execute_with_heartbeat(
            run_id,
            claim,
            ttl_seconds=ttl_seconds,
            heartbeat_seconds=heartbeat_seconds,
        )
    )
    try:
        done, _pending = await asyncio.wait({execution}, timeout=timeout_seconds)
        if execution not in done:
            execution.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await execution
            raise WorkflowDeadlineExceeded(
                f"Workflow {run_id} exceeded {timeout_seconds:g} seconds"
            )
        return await execution
    finally:
        if not execution.done():
            execution.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await execution


async def process_message(
    queue: RedisRunQueue,
    message: RunQueueMessage,
    *,
    owner_id: str,
    lease_ttl_seconds: float,
    heartbeat_seconds: float,
    workflow_timeout_seconds: float,
) -> None:
    try:
        run_id = uuid.UUID(message.run_id)
    except ValueError:
        logger.error("Discarding queue message with invalid run_id: %s", message.run_id)
        await queue.acknowledge(message.message_id)
        return

    parent_context = extract(message.trace_context or {})
    with tracer.start_as_current_span(
        "workflow.run",
        context=parent_context,
        attributes={
            "agentflow.run.id": str(run_id),
            "messaging.system": "redis",
            "messaging.operation": "process",
        },
    ) as span:
        annotate_langfuse_span(
            span,
            "chain",
            metadata={
                "run_id": run_id,
                "session_id": str(run_id),
                "trace_name": "workflow.run",
                "stage": "execution",
            },
        )
        started = time.perf_counter()
        claim: RunLeaseClaim | None = None
        try:
            claim = await claim_run(
                run_id,
                owner_id=owner_id,
                message_id=message.message_id,
                reclaimed=message.reclaimed,
                ttl_seconds=lease_ttl_seconds,
            )
            if claim is None:
                logger.info(
                    "Acknowledging duplicate or terminal run %s",
                    run_id,
                    extra={"event": "workflow.run.duplicate", "run_id": run_id},
                )
                await queue.acknowledge(message.message_id)
                return
            logger.info(
                "Executing workflow run %s",
                run_id,
                extra={"event": "workflow.run.started", "run_id": run_id},
            )
            execution = await _execute_with_timeout(
                run_id,
                claim,
                ttl_seconds=lease_ttl_seconds,
                heartbeat_seconds=heartbeat_seconds,
                timeout_seconds=workflow_timeout_seconds,
            )
            await persist_execution(run_id, execution, claim)
            duration = time.perf_counter() - started
            span.set_attribute("agentflow.run.status", execution.status)
            annotate_langfuse_span(
                span,
                "chain",
                output_data=execution.output,
                metadata={"status": execution.status},
                level="ERROR" if execution.status == "failed" else "DEFAULT",
            )
            record_run_completed(execution.status, duration)
            logger.info(
                "Workflow run %s finished with %s",
                run_id,
                execution.status,
                extra={
                    "event": "workflow.run.completed",
                    "run_id": run_id,
                    "status": execution.status,
                },
            )
        except WorkflowDeadlineExceeded as exc:
            duration = time.perf_counter() - started
            span.record_exception(exc)
            span.set_attribute("agentflow.run.status", "failed")
            span.set_status(Status(StatusCode.ERROR, "WORKFLOW_TIMEOUT"))
            annotate_langfuse_span(
                span,
                "chain",
                metadata={"status": "failed", "error_code": "WORKFLOW_TIMEOUT"},
                level="ERROR",
                status_message="Workflow execution timed out",
            )
            failed = await fail_owned_run(
                claim,
                code="WORKFLOW_TIMEOUT",
                message=(
                    "The workflow exceeded its configured timeout of "
                    f"{workflow_timeout_seconds:g} seconds."
                ),
            )
            record_run_completed("failed", duration)
            logger.warning(
                "Workflow run %s timed out after %.1f seconds; persisted=%s",
                run_id,
                workflow_timeout_seconds,
                failed,
                extra={"event": "workflow.run.timed_out", "run_id": run_id},
            )
            if not failed:
                return
        except RunCancellationRequested:
            cancelled = await apply_run_cancellation(claim)
            duration = time.perf_counter() - started
            span.set_attribute("agentflow.run.status", "cancelled")
            annotate_langfuse_span(
                span,
                "chain",
                metadata={"status": "cancelled"},
            )
            record_run_completed("cancelled", duration)
            logger.info(
                "Workflow run %s was cancelled; terminal state persisted=%s",
                run_id,
                cancelled,
                extra={"event": "workflow.run.cancelled", "run_id": run_id},
            )
            if not cancelled:
                return
        except asyncio.CancelledError:
            if claim is not None:
                await asyncio.shield(
                    fail_owned_run(
                        claim,
                        code="WORKER_SHUTDOWN",
                        message="The workflow worker shut down before completion.",
                    )
                )
            raise
        except LeaseLostError as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, "LEASE_LOST"))
            logger.error(
                "Workflow run %s lost its execution lease; recovery will reconcile it",
                run_id,
                extra={"event": "workflow.run.lease_lost", "run_id": run_id},
            )
            return
        except Exception as exc:  # Persist a terminal state before ACK when possible.
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            annotate_langfuse_span(
                span,
                "chain",
                metadata={"status": "crashed"},
                level="ERROR",
                status_message=type(exc).__name__,
            )
            failed = False
            if claim is not None:
                try:
                    failed = await fail_owned_run(
                        claim,
                        code="WORKER_EXECUTION_FAILED",
                        message="The workflow worker could not complete the task.",
                    )
                except Exception:
                    logger.exception("Could not persist failure for run %s", run_id)
            logger.exception(
                "Workflow run %s crashed; terminal failure persisted=%s",
                run_id,
                failed,
                extra={"event": "workflow.run.crashed", "run_id": run_id},
            )
            if not failed:
                return
        await queue.acknowledge(message.message_id)


async def run_worker() -> None:
    validate_provider_endpoint_configuration()
    queue = RedisRunQueue.from_env()
    consumer = os.getenv(
        "WORKFLOW_WORKER_NAME", f"{socket.gethostname()}-{os.getpid()}"
    )
    reclaim_idle_ms = int(os.getenv("WORKFLOW_RECLAIM_IDLE_MS", "300000"))
    lease_ttl_seconds = max(10.0, float(os.getenv("WORKFLOW_LEASE_TTL_SECONDS", "60")))
    heartbeat_seconds = max(
        1.0,
        min(
            float(os.getenv("WORKFLOW_HEARTBEAT_SECONDS", "10")),
            lease_ttl_seconds / 2,
        ),
    )
    workflow_timeout_seconds = max(
        1.0,
        float(os.getenv("WORKFLOW_TIMEOUT_SECONDS", "900")),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stop.set)
    logger.info("Workflow worker %s started", consumer)
    next_reclaim = 0.0
    reconnect_delay = 1.0
    try:
        while not stop.is_set():
            try:
                await queue.ensure_group()
                record_worker_heartbeat()
                now = loop.time()
                messages: list[RunQueueMessage] = []
                if now >= next_reclaim:
                    messages = await queue.reclaim(
                        consumer, min_idle_ms=reclaim_idle_ms
                    )
                    next_reclaim = now + 60
                if not messages:
                    messages = await queue.read(consumer)
                reconnect_delay = 1.0
                for message in messages:
                    processing = asyncio.create_task(
                        process_message(
                            queue,
                            message,
                            owner_id=consumer,
                            lease_ttl_seconds=lease_ttl_seconds,
                            heartbeat_seconds=heartbeat_seconds,
                            workflow_timeout_seconds=workflow_timeout_seconds,
                        )
                    )
                    stop_waiter = asyncio.create_task(stop.wait())
                    done, _pending = await asyncio.wait(
                        {processing, stop_waiter},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if stop_waiter in done and stop.is_set():
                        processing.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await processing
                    stop_waiter.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await stop_waiter
                    if stop.is_set():
                        break
            except (RedisError, OSError):
                logger.exception(
                    "Redis unavailable; worker will reconnect in %.1f seconds",
                    reconnect_delay,
                )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=reconnect_delay)
                except TimeoutError:
                    pass
                reconnect_delay = min(reconnect_delay * 2, 30.0)
    finally:
        with contextlib.suppress(Exception):
            await queue.remove_consumer(consumer)
        with contextlib.suppress(Exception):
            await queue.close()
        logger.info("Workflow worker %s stopped", consumer)


if __name__ == "__main__":
    asyncio.run(run_worker())
