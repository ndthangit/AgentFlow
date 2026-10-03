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
from agent.runtime import AgentRuntime


def create_app() -> FastAPI:
    auth_enabled = os.getenv("AUTH_ENABLED", "false").lower() == "true"
    verifier = KeycloakTokenVerifier(OidcSettings.from_env()) if auth_enabled else None
    bearer = HTTPBearer(auto_error=False)
    app = FastAPI(title="AgentFlow Agent Integration", version="0.1.0")
    runtime = AgentRuntime()

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
            "mode": "live",
        }

    @app.post("/v1/runs", response_model=RunResult)
    async def create_run(
        request: RunRequest, _claims: Annotated[dict, Depends(authorize)]
    ):
        try:
            return await runtime.run(request)
        except AgentRunError as exc:
            response_status = {
                "CONFIGURATION_ERROR": 503,
                "TIMEOUT": 504,
                "STEP_LIMIT": 502,
                "OUTPUT_INVALID": 502,
                "PROVIDER_OVERLOADED": 503,
                "RATE_LIMITED": 429,
                "PROVIDER_AUTH_ERROR": 502,
                "MODEL_NOT_FOUND": 502,
                "MODEL_REQUEST_REJECTED": 502,
                "PROVIDER_UNAVAILABLE": 503,
                "MCP_CONNECTION_FAILED": 502,
                "AGENT_FAILED": 502,
            }.get(exc.code, 502)
            return JSONResponse(
                status_code=response_status,
                content={"error": {"code": exc.code, "message": exc.message}},
            )

    return app


app = create_app()
