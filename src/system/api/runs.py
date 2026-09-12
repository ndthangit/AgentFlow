"""Workflow run dispatch and execution-history endpoints."""

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from api.dependencies import Claims, DatabaseSession, get_owned_workflow
from domain.models import FlowRun, RunDispatch, RunStep, WorkflowVersion
from domain.schemas import FlowRunCreate, FlowRunView, RunStepView
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
    session.add(RunDispatch(run_id=flow_run.id))
    await session.commit()
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
