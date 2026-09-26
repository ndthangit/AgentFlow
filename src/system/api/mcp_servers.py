"""Remote MCP server registry endpoints."""

import json
import uuid
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from api.dependencies import Claims, DatabaseSession
from domain.models import McpServer
from providers.secrets import ProviderSecretError, ProviderSecretStore
from services.mcp_servers import McpServerCreate, McpServerUpdate, McpServerView

router = APIRouter(prefix="/v1/mcp-servers", tags=["mcp-servers"])


@lru_cache(maxsize=1)
def _secret_store() -> ProviderSecretStore:
    try:
        return ProviderSecretStore.from_config()
    except ProviderSecretError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


def _encrypted_headers(headers: dict[str, str]) -> str | None:
    if not headers:
        return None
    try:
        return _secret_store().encrypt(
            json.dumps(headers, ensure_ascii=True, separators=(",", ":"))
        )
    except ProviderSecretError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


@router.get("", response_model=list[McpServerView])
async def list_mcp_servers(claims: Claims, session: DatabaseSession):
    result = await session.scalars(
        select(McpServer)
        .where(McpServer.owner_subject == claims["sub"])
        .order_by(McpServer.name)
    )
    return list(result)


@router.post("", response_model=McpServerView, status_code=201)
async def create_mcp_server(
    request: McpServerCreate,
    claims: Claims,
    session: DatabaseSession,
):
    server = McpServer(
        owner_subject=claims["sub"],
        slug=request.slug,
        name=request.name,
        description=request.description,
        transport=request.transport,
        url=request.url,
        headers_encrypted=_encrypted_headers(request.headers),
        enabled=request.enabled,
    )
    session.add(server)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="MCP slug already exists") from None
    await session.refresh(server)
    return server


@router.put("/{server_id}", response_model=McpServerView)
async def update_mcp_server(
    server_id: uuid.UUID,
    request: McpServerUpdate,
    claims: Claims,
    session: DatabaseSession,
):
    current = await session.scalar(
        select(McpServer).where(
            McpServer.id == server_id,
            McpServer.owner_subject == claims["sub"],
        )
    )
    if current is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    if current.revision != request.expected_revision:
        raise HTTPException(status_code=409, detail="MCP server revision conflict")
    encrypted_headers = (
        current.headers_encrypted
        if request.headers is None
        else _encrypted_headers(request.headers)
    )
    result = await session.execute(
        update(McpServer)
        .where(
            McpServer.id == server_id,
            McpServer.owner_subject == claims["sub"],
            McpServer.revision == request.expected_revision,
        )
        .values(
            name=request.name,
            description=request.description,
            transport=request.transport,
            url=request.url,
            headers_encrypted=encrypted_headers,
            enabled=request.enabled,
            revision=McpServer.revision + 1,
        )
        .returning(McpServer)
    )
    server = result.scalar_one_or_none()
    if server is None:
        raise HTTPException(status_code=409, detail="MCP server revision conflict")
    await session.commit()
    return server


@router.delete("/{server_id}", status_code=204)
async def delete_mcp_server(
    server_id: uuid.UUID,
    claims: Claims,
    session: DatabaseSession,
) -> Response:
    result = await session.execute(
        delete(McpServer)
        .where(
            McpServer.id == server_id,
            McpServer.owner_subject == claims["sub"],
        )
        .returning(McpServer.id)
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        raise HTTPException(status_code=404, detail="MCP server not found")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
