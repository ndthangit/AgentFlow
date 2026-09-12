"""Workflow draft, skill selection, validation, and publishing endpoints."""

import hashlib
import json
import uuid
from copy import deepcopy

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import delete, func, select, update

from api.dependencies import Claims, DatabaseSession, get_owned_workflow
from api.skills import visible_skill_filter
from domain.models import (
    FlowRun,
    Skill,
    Workflow,
    WorkflowSkill,
    WorkflowVersion,
)
from domain.schemas import (
    DraftUpdate,
    ValidationResult,
    VersionView,
    WorkflowCreate,
    WorkflowView,
)
from domain.validation import validate_graph
from services.skills import SkillView, WorkflowSkillSelection

router = APIRouter(prefix="/v1/workflows", tags=["workflows"])


@router.post("", response_model=WorkflowView, status_code=201)
async def create_workflow(
    request: WorkflowCreate,
    claims: Claims,
    session: DatabaseSession,
):
    workflow = Workflow(
        owner_subject=claims["sub"],
        name=request.name,
        draft=request.draft,
    )
    session.add(workflow)
    await session.commit()
    await session.refresh(workflow)
    return workflow


@router.get("", response_model=list[WorkflowView])
async def list_workflows(claims: Claims, session: DatabaseSession):
    result = await session.scalars(
        select(Workflow)
        .where(Workflow.owner_subject == claims["sub"])
        .order_by(Workflow.updated_at.desc())
    )
    return list(result)


@router.get("/{workflow_id}", response_model=WorkflowView)
async def get_workflow(
    workflow_id: uuid.UUID,
    claims: Claims,
    session: DatabaseSession,
):
    return await get_owned_workflow(workflow_id, claims["sub"], session)


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(
    workflow_id: uuid.UUID,
    claims: Claims,
    session: DatabaseSession,
) -> Response:
    version_ids = select(WorkflowVersion.id).where(
        WorkflowVersion.workflow_id == workflow_id
    )
    await session.execute(
        delete(FlowRun).where(FlowRun.workflow_version_id.in_(version_ids))
    )
    result = await session.execute(
        delete(Workflow)
        .where(
            Workflow.id == workflow_id,
            Workflow.owner_subject == claims["sub"],
        )
        .returning(Workflow.id)
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        raise HTTPException(status_code=404, detail="Workflow not found")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{workflow_id}/skills", response_model=list[SkillView])
async def list_workflow_skills(
    workflow_id: uuid.UUID,
    claims: Claims,
    session: DatabaseSession,
):
    await get_owned_workflow(workflow_id, claims["sub"], session)
    result = await session.scalars(
        select(Skill)
        .join(WorkflowSkill, WorkflowSkill.skill_id == Skill.id)
        .where(WorkflowSkill.workflow_id == workflow_id)
        .order_by(WorkflowSkill.position)
    )
    return list(result)


@router.put("/{workflow_id}/skills", response_model=list[SkillView])
async def select_workflow_skills(
    workflow_id: uuid.UUID,
    request: WorkflowSkillSelection,
    claims: Claims,
    session: DatabaseSession,
):
    await get_owned_workflow(workflow_id, claims["sub"], session)
    skills: list[Skill] = []
    if request.skill_ids:
        result = await session.scalars(
            select(Skill).where(
                Skill.id.in_(request.skill_ids),
                visible_skill_filter(claims["sub"]),
                Skill.enabled.is_(True),
            )
        )
        by_id = {skill.id: skill for skill in result}
        if len(by_id) != len(request.skill_ids):
            raise HTTPException(
                status_code=422, detail="One or more skills are unavailable"
            )
        skills = [by_id[skill_id] for skill_id in request.skill_ids]

    await session.execute(
        delete(WorkflowSkill).where(WorkflowSkill.workflow_id == workflow_id)
    )
    session.add_all(
        WorkflowSkill(
            workflow_id=workflow_id,
            skill_id=skill.id,
            position=position,
        )
        for position, skill in enumerate(skills)
    )
    await session.commit()
    return skills


@router.put("/{workflow_id}/draft", response_model=WorkflowView)
async def update_draft(
    workflow_id: uuid.UUID,
    request: DraftUpdate,
    claims: Claims,
    session: DatabaseSession,
):
    result = await session.execute(
        update(Workflow)
        .where(
            Workflow.id == workflow_id,
            Workflow.owner_subject == claims["sub"],
            Workflow.revision == request.expected_revision,
        )
        .values(draft=request.draft, revision=Workflow.revision + 1)
        .returning(Workflow)
    )
    workflow = result.scalar_one_or_none()
    if workflow is None:
        exists = await session.scalar(
            select(Workflow.id).where(
                Workflow.id == workflow_id,
                Workflow.owner_subject == claims["sub"],
            )
        )
        if exists is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        raise HTTPException(status_code=409, detail="Draft revision conflict")
    await session.commit()
    return workflow


@router.post("/{workflow_id}/validate", response_model=ValidationResult)
async def validate_workflow(
    workflow_id: uuid.UUID,
    claims: Claims,
    session: DatabaseSession,
):
    workflow = await get_owned_workflow(workflow_id, claims["sub"], session)
    errors = validate_graph(workflow.draft)
    return ValidationResult(valid=not errors, errors=errors)


@router.post("/{workflow_id}/versions", response_model=VersionView, status_code=201)
async def publish_workflow(
    workflow_id: uuid.UUID,
    claims: Claims,
    session: DatabaseSession,
):
    workflow = await get_owned_workflow(workflow_id, claims["sub"], session)
    errors = validate_graph(workflow.draft)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"code": "WORKFLOW_INVALID", "errors": errors},
        )
    latest = await session.scalar(
        select(func.max(WorkflowVersion.version)).where(
            WorkflowVersion.workflow_id == workflow.id
        )
    )
    selected_skills = await session.scalars(
        select(Skill)
        .join(WorkflowSkill, WorkflowSkill.skill_id == Skill.id)
        .where(WorkflowSkill.workflow_id == workflow.id)
        .order_by(WorkflowSkill.position)
    )
    graph = deepcopy(workflow.draft)
    graph["skills"] = [
        {
            "id": str(skill.id),
            "slug": skill.slug,
            "name": skill.name,
            "version": skill.version,
            "content_hash": skill.content_hash,
            "instructions": skill.instructions,
        }
        for skill in selected_skills
    ]
    canonical = json.dumps(graph, sort_keys=True, separators=(",", ":"))
    version = WorkflowVersion(
        workflow_id=workflow.id,
        version=(latest or 0) + 1,
        graph=graph,
        content_hash=hashlib.sha256(canonical.encode()).hexdigest(),
    )
    session.add(version)
    await session.commit()
    await session.refresh(version)
    return version
