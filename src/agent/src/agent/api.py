"""HTTP adapter exposing the standalone Deep Agent runtime."""

import os
from asyncio import to_thread
from typing import Annotated

import jwt
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agent.auth import KeycloakTokenVerifier, OidcSettings
from agent.contracts import AgentRunError, RunRequest, RunResult
from agent.runtime import run_agent


def create_app(*, demo: bool | None = None) -> FastAPI:
    use_demo = os.getenv("AGENT_DEMO", "").lower() == "true" if demo is None else demo
    auth_enabled = os.getenv("AUTH_ENABLED", "false").lower() == "true"
    verifier = KeycloakTokenVerifier(OidcSettings.from_env()) if auth_enabled else None
    bearer = HTTPBearer(auto_error=False)
    app = FastAPI(title="AgentFlow Agent Integration", version="0.1.0")

    async def authorize(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> dict:
        if verifier is None:
            return {}
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
        return {
            "status": "ok",
            "service": "agent-integration",
            "mode": "demo" if use_demo else "live",
        }

    @app.post("/v1/runs", response_model=RunResult)
    async def create_run(
        request: RunRequest, _claims: Annotated[dict, Depends(authorize)]
    ):
        try:
            return await run_agent(request, demo=use_demo)
        except AgentRunError as exc:
            response_status = {
                "CONFIGURATION_ERROR": 503,
                "TIMEOUT": 504,
                "STEP_LIMIT": 502,
                "OUTPUT_INVALID": 502,
                "AGENT_FAILED": 502,
            }[exc.code]
            return JSONResponse(
                status_code=response_status,
                content={"error": {"code": exc.code, "message": exc.message}},
            )

    return app


app = create_app()
