"""Workflow worker consuming Redis Stream jobs and executing persisted versions."""

import asyncio
import logging
import os
import signal
import socket
import uuid

from sqlalchemy import select, update

from core.database import SessionFactory
from domain.models import FlowRun, LlmProvider, RunStep, WorkflowVersion
from providers.secrets import ProviderSecretStore
from runtime.agent_executor import create_agent_executor
from runtime.engine import WorkflowExecution, execute_workflow
from runtime.queue import RedisRunQueue, RunQueueMessage

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("agentflow.worker")


async def claim_run(run_id: uuid.UUID, *, reclaimed: bool) -> bool:
    async with SessionFactory() as session:
        claimed = await session.scalar(
            update(FlowRun)
            .where(FlowRun.id == run_id, FlowRun.status == "pending")
            .values(status="running")
            .returning(FlowRun.id)
        )
        if claimed is not None:
            await session.commit()
            return True
        status = await session.scalar(
            select(FlowRun.status).where(FlowRun.id == run_id)
        )
        return reclaimed and status == "running"


async def load_and_execute(run_id: uuid.UUID) -> WorkflowExecution:
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
        graph = version.graph
        run_input = flow_run.input

    execute_agent = create_agent_executor(
        graph, providers, ProviderSecretStore.from_config()
    )
    return await execute_workflow(graph, run_input, execute_agent)


async def persist_execution(run_id: uuid.UUID, execution: WorkflowExecution) -> None:
    async with SessionFactory() as session:
        flow_run = await session.scalar(select(FlowRun).where(FlowRun.id == run_id))
        if flow_run is None:
            return
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
        await session.commit()


async def process_message(queue: RedisRunQueue, message: RunQueueMessage) -> None:
    try:
        run_id = uuid.UUID(message.run_id)
    except ValueError:
        logger.error("Discarding queue message with invalid run_id: %s", message.run_id)
        await queue.acknowledge(message.message_id)
        return

    try:
        should_execute = await claim_run(run_id, reclaimed=message.reclaimed)
        if not should_execute:
            logger.info("Acknowledging duplicate or terminal run %s", run_id)
            await queue.acknowledge(message.message_id)
            return
        logger.info("Executing workflow run %s", run_id)
        execution = await load_and_execute(run_id)
        await persist_execution(run_id, execution)
        await queue.acknowledge(message.message_id)
        logger.info("Workflow run %s finished with %s", run_id, execution.status)
    except Exception:  # Leave the message pending for XAUTOCLAIM.
        logger.exception("Workflow run %s crashed; message will be reclaimed", run_id)


async def run_worker() -> None:
    queue = RedisRunQueue.from_env()
    consumer = os.getenv(
        "WORKFLOW_WORKER_NAME", f"{socket.gethostname()}-{os.getpid()}"
    )
    reclaim_idle_ms = int(os.getenv("WORKFLOW_RECLAIM_IDLE_MS", "300000"))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stop.set)
    await queue.ensure_group()
    logger.info("Workflow worker %s started", consumer)
    next_reclaim = 0.0
    try:
        while not stop.is_set():
            now = loop.time()
            messages: list[RunQueueMessage] = []
            if now >= next_reclaim:
                messages = await queue.reclaim(consumer, min_idle_ms=reclaim_idle_ms)
                next_reclaim = now + 60
            if not messages:
                messages = await queue.read(consumer)
            for message in messages:
                await process_message(queue, message)
    finally:
        await queue.remove_consumer(consumer)
        await queue.close()
        logger.info("Workflow worker %s stopped", consumer)


if __name__ == "__main__":
    asyncio.run(run_worker())
