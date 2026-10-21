"""Workflow run dispatch, cancellation, and execution-history endpoints."""

import hashlib
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from opentelemetry import trace
from opentelemetry.propagate import inject
from redis.exceptions import RedisError
from sqlalchemy import select, update

from api.dependencies import Claims, DatabaseSession, get_owned_workflow
from core.telemetry import annotate_langfuse_span, record_run_started
from domain.models import (
    FlowRun,
    RunCommand,
    RunCommandStatus,
    RunCommandType,
    RunDispatch,
    RunStep,
    WorkflowVersion,
)
from domain.schemas import FlowRunCancel, FlowRunCreate, FlowRunView, RunStepView
from runtime.cancellation import CANCELLED_ERROR, publish_run_cancellation
from runtime.engine import ordered_nodes

router = APIRouter(tags=["runs"])


@router.post(
    "/v1/workflows/{workflow_id}/runs",
    response_model=FlowRunView,
    status_code=202,
)
async def enqueue_flow_run(
    workflow_id: uuid.UUID,
    request: FlowRunCreate,
    claims: Claims,
    session: DatabaseSession,
):
    await get_owned_workflow(workflow_id, claims["sub"], session)
    version = await session.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.id == request.version_id,
            WorkflowVersion.workflow_id == workflow_id,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Workflow version not found")

    ordered, _parents = ordered_nodes(version.graph)
    flow_run = FlowRun(
        workflow_version_id=version.id,
        owner_subject=claims["sub"],
        input=request.input,
        output=None,
        status="pending",
    )
    session.add(flow_run)
    await session.flush()
    session.add_all(
        RunStep(
            run_id=flow_run.id,
            sequence=sequence,
            node_id=node["id"],
            node_type=node.get("type", "unknown"),
            node_name=node.get("name", node["id"]),
            status="pending",
        )
        for sequence, node in enumerate(ordered, start=1)
    )
    annotate_langfuse_span(
        trace.get_current_span(),
        "chain",
        input_data=request.input,
        metadata={
            "run_id": flow_run.id,
            "session_id": flow_run.session_id,
            "workflow_id": workflow_id,
            "workflow_version_id": version.id,
            "workflow_version": version.version,
            "trace_name": "workflow.run",
            "trigger_type": "api",
        },
    )
    trace_context: dict[str, str] = {}
    inject(trace_context)
    session.add(
        RunDispatch(
            run_id=flow_run.id,
            traceparent=trace_context.get("traceparent"),
            tracestate=trace_context.get("tracestate"),
        )
    )
    await session.commit()
    record_run_started()
    await session.refresh(flow_run)
    return flow_run


@router.get("/v1/runs/{run_id}", response_model=FlowRunView)
async def get_flow_run(
    run_id: uuid.UUID,
    claims: Claims,
    session: DatabaseSession,
):
    flow_run = await session.scalar(
        select(FlowRun).where(
            FlowRun.id == run_id,
            FlowRun.owner_subject == claims["sub"],
        )
    )
    if flow_run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return flow_run


@router.post(
    "/v1/runs/{run_id}/cancel",
    response_model=FlowRunView,
    status_code=202,
)
async def cancel_flow_run(
    run_id: uuid.UUID,
    request: FlowRunCancel,
    claims: Claims,
    session: DatabaseSession,
):
    """Record a durable stop request; pending runs are cancelled immediately."""

    flow_run = await session.scalar(
        select(FlowRun)
        .where(
            FlowRun.id == run_id,
            FlowRun.owner_subject == claims["sub"],
        )
        .with_for_update()
    )
    if flow_run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    scope = f"run:{run_id}"
    payload_hash = hashlib.sha256(b"{}").hexdigest()
    existing = await session.scalar(
        select(RunCommand).where(
            RunCommand.owner_subject == claims["sub"],
            RunCommand.scope == scope,
            RunCommand.idempotency_key == request.idempotency_key,
        )
    )
    if existing is not None:
        if (
            existing.type != RunCommandType.CANCEL
            or existing.payload_hash != payload_hash
        ):
            raise HTTPException(
                status_code=409,
                detail="Idempotency key was already used for another command",
            )
        should_publish = (
            existing.status == RunCommandStatus.ACCEPTED
            and flow_run.status == "running"
        )
        await session.commit()
        if should_publish:
            try:
                await publish_run_cancellation(run_id)
            except (RedisError, OSError):
                raise HTTPException(
                    status_code=503,
                    detail="Stop command was saved but the worker signal failed; retry Stop",
                ) from None
        return flow_run

    if flow_run.status not in {"pending", "running"}:
        return flow_run

    now = datetime.now(UTC)
    command = RunCommand(
        run_id=flow_run.id,
        owner_subject=claims["sub"],
        scope=scope,
        type=RunCommandType.CANCEL,
        idempotency_key=request.idempotency_key,
        payload_hash=payload_hash,
        payload={},
        status=RunCommandStatus.ACCEPTED,
    )
    session.add(command)
    should_publish = flow_run.status == "running"
    if flow_run.status == "pending":
        flow_run.status = "cancelled"
        command.status = RunCommandStatus.APPLIED
        command.result = {"status": "cancelled"}
        command.applied_at = now
        await session.execute(
            update(RunStep)
            .where(RunStep.run_id == flow_run.id, RunStep.status == "pending")
            .values(status="cancelled", error=CANCELLED_ERROR, completed_at=now)
        )
    await session.commit()
    await session.refresh(flow_run)
    if should_publish:
        try:
            await publish_run_cancellation(run_id)
        except (RedisError, OSError):
            raise HTTPException(
                status_code=503,
                detail="Stop command was saved but the worker signal failed; retry Stop",
            ) from None
    return flow_run


@router.get("/v1/runs/{run_id}/steps", response_model=list[RunStepView])
async def list_flow_run_steps(
    run_id: uuid.UUID,
    claims: Claims,
    session: DatabaseSession,
):
    exists = await session.scalar(
        select(FlowRun.id).where(
            FlowRun.id == run_id,
            FlowRun.owner_subject == claims["sub"],
        )
    )
    if exists is None:
        raise HTTPException(status_code=404, detail="Run not found")
    result = await session.scalars(
        select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.sequence)
    )
    return list(result)


@router.get(
    "/v1/workflows/{workflow_id}/runs",
    response_model=list[FlowRunView],
)
async def list_flow_runs(
    workflow_id: uuid.UUID,
    claims: Claims,
    session: DatabaseSession,
):
    await get_owned_workflow(workflow_id, claims["sub"], session)
    result = await session.scalars(
        select(FlowRun)
        .join(WorkflowVersion, WorkflowVersion.id == FlowRun.workflow_version_id)
        .where(
            WorkflowVersion.workflow_id == workflow_id,
            FlowRun.owner_subject == claims["sub"],
        )
        .order_by(FlowRun.created_at.desc(), FlowRun.id.desc())
        .limit(100)
    )
    return list(result)
