"""FastAPI application factory for the AgentFlow control plane/orchestrator."""

import os
from asyncio import Event, create_task
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.health import router as health_router
from api.providers import router as providers_router
from api.runs import router as runs_router
from api.skills import router as skills_router
from api.workflows import router as workflows_router
from core.database import SessionFactory
from runtime.orchestrator import orchestrate
from services.skills import sync_builtin_skills


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with SessionFactory() as session:
        await sync_builtin_skills(session)

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
    application.include_router(providers_router)
    application.include_router(skills_router)
    application.include_router(workflows_router)
    application.include_router(runs_router)
    return application


app = create_app()
