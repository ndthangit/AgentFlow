"""FastAPI control plane backed by PostgreSQL."""

import hashlib
import json
import os
import uuid
from asyncio import to_thread
from typing import Annotated

import jwt
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from system_api.auth import KeycloakTokenVerifier, OidcSettings
from system_api.database import get_session
from system_api.models import FlowRun, Workflow, WorkflowVersion
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
    app = FastAPI(title="AgentFlow System API", version="0.1.0")

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
        canonical = json.dumps(workflow.draft, sort_keys=True, separators=(",", ":"))
        version = WorkflowVersion(
            workflow_id=workflow.id,
            version=(latest or 0) + 1,
            graph=workflow.draft,
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
