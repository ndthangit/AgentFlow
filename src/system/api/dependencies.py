"""Shared authentication and persistence dependencies for API routes."""

import os
import uuid
from asyncio import to_thread
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import KeycloakTokenVerifier, OidcSettings
from core.database import get_session
from domain.models import Workflow

_bearer = HTTPBearer(auto_error=False)
_verifier = (
    KeycloakTokenVerifier(OidcSettings.from_env())
    if os.getenv("AUTH_ENABLED", "false").lower() == "true"
    else None
)


async def authorize(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict:
    """Return verified OIDC claims or a local development identity."""
    if _verifier is None:
        return {"sub": "local-development"}
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return await to_thread(_verifier.verify, credentials.credentials)
    except (jwt.PyJWTError, OSError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


Claims = Annotated[dict, Depends(authorize)]
DatabaseSession = Annotated[AsyncSession, Depends(get_session)]


async def get_owned_workflow(
    workflow_id: uuid.UUID,
    subject: str,
    session: AsyncSession,
) -> Workflow:
    workflow = await session.scalar(
        select(Workflow).where(
            Workflow.id == workflow_id,
            Workflow.owner_subject == subject,
        )
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow
