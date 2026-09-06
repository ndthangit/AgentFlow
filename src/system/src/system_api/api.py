"""FastAPI control plane backed by PostgreSQL."""

import hashlib
import json
import os
import uuid
from asyncio import to_thread
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Annotated

import jwt
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from system_api.auth import KeycloakTokenVerifier, OidcSettings
from system_api.database import SessionFactory, get_session
from system_api.models import FlowRun, Skill, Workflow, WorkflowSkill, WorkflowVersion
from system_api.skills import (
    BUILTIN_OWNER,
    SkillCreate,
    SkillUpdate,
    SkillView,
    WorkflowSkillSelection,
    content_hash,
    sync_builtin_skills,
)
from system_api.workflow import (
    DraftUpdate,
    FlowRunCreate,
    FlowRunView,
    ValidationResult,
    VersionView,
    WorkflowCreate,
    WorkflowView,
    validate_graph,
)


def create_app() -> FastAPI:
    auth_enabled = os.getenv("AUTH_ENABLED", "false").lower() == "true"
    verifier = KeycloakTokenVerifier(OidcSettings.from_env()) if auth_enabled else None
    bearer = HTTPBearer(auto_error=False)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with SessionFactory() as session:
            await sync_builtin_skills(session)
        yield

    app = FastAPI(title="AgentFlow System API", version="0.1.0", lifespan=lifespan)
    web_origins = os.getenv(
        "WEB_ORIGINS", "http://localhost:3000,http://localhost:5173"
    ).split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in web_origins if origin.strip()],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    async def authorize(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> dict:
        if verifier is None:
            return {"sub": "local-development"}
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bearer token required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            return await to_thread(verifier.verify, credentials.credentials)
        except (jwt.PyJWTError, OSError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid access token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from None

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "system"}

    @app.get("/ready")
    async def ready(session: Annotated[AsyncSession, Depends(get_session)]):
        await session.execute(text("SELECT 1"))
        return {"status": "ready", "database": "postgresql"}

    async def owned_workflow(
        workflow_id: uuid.UUID, subject: str, session: AsyncSession
    ) -> Workflow:
        workflow = await session.scalar(
            select(Workflow).where(
                Workflow.id == workflow_id, Workflow.owner_subject == subject
            )
        )
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return workflow

    def visible_skill_filter(subject: str):
        return or_(Skill.owner_subject == subject, Skill.owner_subject == BUILTIN_OWNER)

    @app.get("/v1/skills", response_model=list[SkillView])
    async def list_skills(
        claims: Annotated[dict, Depends(authorize)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ):
        result = await session.scalars(
            select(Skill)
            .where(visible_skill_filter(claims["sub"]), Skill.enabled.is_(True))
            .order_by(Skill.source, Skill.name)
        )
        return list(result)

    @app.post("/v1/skills", response_model=SkillView, status_code=201)
    async def create_skill(
        request: SkillCreate,
        claims: Annotated[dict, Depends(authorize)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ):
        skill = Skill(
            owner_subject=claims["sub"],
            slug=request.slug,
            name=request.name,
            description=request.description,
            instructions=request.instructions,
            content_hash=content_hash(request.instructions),
            source="user",
        )
        session.add(skill)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status_code=409, detail="Skill slug already exists"
            ) from None
        await session.refresh(skill)
        return skill

    @app.get("/v1/skills/{skill_id}", response_model=SkillView)
    async def get_skill(
        skill_id: uuid.UUID,
        claims: Annotated[dict, Depends(authorize)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ):
        skill = await session.scalar(
            select(Skill).where(
                Skill.id == skill_id, visible_skill_filter(claims["sub"])
            )
        )
        if skill is None:
            raise HTTPException(status_code=404, detail="Skill not found")
        return skill

    @app.put("/v1/skills/{skill_id}", response_model=SkillView)
    async def update_skill(
        skill_id: uuid.UUID,
        request: SkillUpdate,
        claims: Annotated[dict, Depends(authorize)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ):
        result = await session.execute(
            update(Skill)
            .where(
                Skill.id == skill_id,
                Skill.owner_subject == claims["sub"],
                Skill.source == "user",
                Skill.version == request.expected_version,
            )
            .values(
                name=request.name,
                description=request.description,
                instructions=request.instructions,
                content_hash=content_hash(request.instructions),
                enabled=request.enabled,
                version=Skill.version + 1,
            )
            .returning(Skill)
        )
        skill = result.scalar_one_or_none()
        if skill is None:
            exists = await session.scalar(
                select(Skill.id).where(
                    Skill.id == skill_id,
                    Skill.owner_subject == claims["sub"],
                    Skill.source == "user",
                )
            )
            if exists is None:
                raise HTTPException(status_code=404, detail="Editable skill not found")
            raise HTTPException(status_code=409, detail="Skill version conflict")
        await session.commit()
        return skill

    @app.post("/v1/workflows", response_model=WorkflowView, status_code=201)
    async def create_workflow(
        request: WorkflowCreate,
        claims: Annotated[dict, Depends(authorize)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ):
        workflow = Workflow(
            owner_subject=claims["sub"], name=request.name, draft=request.draft
        )
        session.add(workflow)
        await session.commit()
        await session.refresh(workflow)
        return workflow

    @app.get("/v1/workflows", response_model=list[WorkflowView])
    async def list_workflows(
        claims: Annotated[dict, Depends(authorize)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ):
        result = await session.scalars(
            select(Workflow)
            .where(Workflow.owner_subject == claims["sub"])
            .order_by(Workflow.updated_at.desc())
        )
        return list(result)

    @app.get("/v1/workflows/{workflow_id}", response_model=WorkflowView)
    async def get_workflow(
        workflow_id: uuid.UUID,
        claims: Annotated[dict, Depends(authorize)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ):
        return await owned_workflow(workflow_id, claims["sub"], session)

    @app.get("/v1/workflows/{workflow_id}/skills", response_model=list[SkillView])
    async def list_workflow_skills(
        workflow_id: uuid.UUID,
        claims: Annotated[dict, Depends(authorize)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ):
        await owned_workflow(workflow_id, claims["sub"], session)
        result = await session.scalars(
            select(Skill)
            .join(WorkflowSkill, WorkflowSkill.skill_id == Skill.id)
            .where(WorkflowSkill.workflow_id == workflow_id)
            .order_by(WorkflowSkill.position)
        )
        return list(result)

    @app.put("/v1/workflows/{workflow_id}/skills", response_model=list[SkillView])
    async def select_workflow_skills(
        workflow_id: uuid.UUID,
        request: WorkflowSkillSelection,
        claims: Annotated[dict, Depends(authorize)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ):
        await owned_workflow(workflow_id, claims["sub"], session)
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
            WorkflowSkill(workflow_id=workflow_id, skill_id=skill.id, position=index)
            for index, skill in enumerate(skills)
        )
        await session.commit()
        return skills

    @app.put("/v1/workflows/{workflow_id}/draft", response_model=WorkflowView)
    async def update_draft(
        workflow_id: uuid.UUID,
        request: DraftUpdate,
        claims: Annotated[dict, Depends(authorize)],
        session: Annotated[AsyncSession, Depends(get_session)],
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

    @app.post("/v1/workflows/{workflow_id}/validate", response_model=ValidationResult)
    async def validate_workflow(
        workflow_id: uuid.UUID,
        claims: Annotated[dict, Depends(authorize)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ):
        workflow = await owned_workflow(workflow_id, claims["sub"], session)
        errors = validate_graph(workflow.draft)
        return ValidationResult(valid=not errors, errors=errors)

    @app.post(
        "/v1/workflows/{workflow_id}/versions",
        response_model=VersionView,
        status_code=201,
    )
    async def publish_workflow(
        workflow_id: uuid.UUID,
        claims: Annotated[dict, Depends(authorize)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ):
        workflow = await owned_workflow(workflow_id, claims["sub"], session)
        errors = validate_graph(workflow.draft)
        if errors:
            raise HTTPException(
                status_code=422, detail={"code": "WORKFLOW_INVALID", "errors": errors}
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

    @app.post(
        "/v1/workflows/{workflow_id}/runs",
        response_model=FlowRunView,
        status_code=202,
    )
    async def enqueue_flow_run(
        workflow_id: uuid.UUID,
        request: FlowRunCreate,
        claims: Annotated[dict, Depends(authorize)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ):
        await owned_workflow(workflow_id, claims["sub"], session)
        version = await session.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.id == request.version_id,
                WorkflowVersion.workflow_id == workflow_id,
            )
        )
        if version is None:
            raise HTTPException(status_code=404, detail="Workflow version not found")
        flow_run = FlowRun(
            workflow_version_id=version.id,
            owner_subject=claims["sub"],
            input=request.input,
            status="pending",
        )
        session.add(flow_run)
        await session.commit()
        await session.refresh(flow_run)
        return flow_run

    @app.get("/v1/runs/{run_id}", response_model=FlowRunView)
    async def get_flow_run(
        run_id: uuid.UUID,
        claims: Annotated[dict, Depends(authorize)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ):
        flow_run = await session.scalar(
            select(FlowRun).where(
                FlowRun.id == run_id, FlowRun.owner_subject == claims["sub"]
            )
        )
        if flow_run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return flow_run

    return app


app = create_app()
