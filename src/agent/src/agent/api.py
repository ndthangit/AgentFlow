"""Local HTTP boundary. Runs are synchronous and are not persisted."""

import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from agent.contracts import AgentRunError, RunRequest, RunResult
from agent.runtime import run_agent


def create_app(*, demo: bool | None = None) -> FastAPI:
    use_demo = os.getenv("AGENT_DEMO", "").lower() == "true" if demo is None else demo
    app = FastAPI(title="AgentFlow Sample Agent", version="0.1.0")

    @app.get("/health")
    async def health():
        return {"status": "ok", "mode": "demo" if use_demo else "live"}

    @app.post("/v1/runs", response_model=RunResult)
    async def create_run(request: RunRequest):
        try:
            return await run_agent(request, demo=use_demo)
        except AgentRunError as exc:
            status = {
                "CONFIGURATION_ERROR": 503,
                "TIMEOUT": 504,
                "STEP_LIMIT": 502,
                "OUTPUT_INVALID": 502,
                "AGENT_FAILED": 502,
            }[exc.code]
            return JSONResponse(
                status_code=status,
                content={"error": {"code": exc.code, "message": exc.message}},
            )

    return app


app = create_app()
