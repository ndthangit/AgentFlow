"""FastAPI application factory for the AgentFlow control plane/orchestrator."""

import os
from asyncio import Event, create_task
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.health import router as health_router
from api.mcp_servers import router as mcp_servers_router
from api.node_types import router as node_types_router
from api.providers import router as providers_router
from api.runs import router as runs_router
from api.skills import router as skills_router
from api.workflows import router as workflows_router
from core.database import SessionFactory
from core.database import engine as database_engine
from core.telemetry import configure_telemetry, instrument_fastapi
from providers.adapters import validate_provider_endpoint_configuration
from runtime.orchestrator import orchestrate
from services.skills import sync_builtin_skills
from services.workflows import sync_default_workflows

configure_telemetry("agentflow-system", sqlalchemy_engine=database_engine.sync_engine)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_provider_endpoint_configuration()
    async with SessionFactory() as session:
        await sync_builtin_skills(session)
        await sync_default_workflows(
            session,
            os.getenv("DEFAULT_WORKFLOW_OWNER_SUBJECT", ""),
        )

    stop_event = Event()
    task = create_task(orchestrate(stop_event), name="workflow-orchestrator")
    try:
        yield
    finally:
        stop_event.set()
        await task


def create_app() -> FastAPI:
    application = FastAPI(
        title="AgentFlow System API",
        version="0.1.0",
        lifespan=lifespan,
    )
    origins = os.getenv(
        "WEB_ORIGINS", "http://localhost:3000,http://localhost:5173"
    ).split(",")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in origins if origin.strip()],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    application.include_router(health_router)
    application.include_router(mcp_servers_router)
    application.include_router(node_types_router)
    application.include_router(providers_router)
    application.include_router(skills_router)
    application.include_router(workflows_router)
    application.include_router(runs_router)
    instrument_fastapi(application)
    return application


app = create_app()
