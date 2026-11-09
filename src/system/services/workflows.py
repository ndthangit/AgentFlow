"""Idempotent synchronization for workflows bundled with AgentFlow."""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from domain.examples import simple_agent_workflow_draft
from domain.models import McpServer, Skill, Workflow
from services.skills import BUILTIN_OWNER

DEFAULT_WORKFLOW_KEY = "mcp-skill-parallel-starter-v1"
DEFAULT_WORKFLOW_NAME = "Starter: MCP + Skill in Parallel"
DEFAULT_WORKFLOW_NAMESPACE = uuid.UUID("663d8d5f-8d26-4d9e-9a51-78fe4bd810ec")
DEFAULT_MCP_SLUG = "filesystem-demo"


def default_workflow_id(owner_subject: str) -> uuid.UUID:
    return uuid.uuid5(
        DEFAULT_WORKFLOW_NAMESPACE,
        f"{owner_subject}:{DEFAULT_WORKFLOW_KEY}",
    )


def default_mcp_server_id(owner_subject: str) -> uuid.UUID:
    return uuid.uuid5(
        DEFAULT_WORKFLOW_NAMESPACE,
        f"{owner_subject}:{DEFAULT_MCP_SLUG}",
    )


async def sync_default_workflows(
    session: AsyncSession,
    owner_subject: str,
) -> None:
    """Insert bundled workflows once without overwriting user edits."""
    subject = owner_subject.strip()
    if not subject:
        return
    existing = await session.scalar(
        select(Workflow.id).where(Workflow.owner_subject == subject).limit(1)
    )
    if existing is not None:
        return
    skill_id = await session.scalar(
        select(Skill.id).where(
            Skill.owner_subject == BUILTIN_OWNER,
            Skill.slug == "workflow-designer",
            Skill.enabled.is_(True),
        )
    )
    if skill_id is None:
        raise RuntimeError("The built-in workflow-designer skill is missing")
    mcp_server_id = await session.scalar(
        select(McpServer.id).where(
            McpServer.owner_subject == subject,
            McpServer.slug == DEFAULT_MCP_SLUG,
        )
    )
    if mcp_server_id is None:
        mcp_server_id = default_mcp_server_id(subject)
        await session.execute(
            insert(McpServer)
            .values(
                id=mcp_server_id,
                owner_subject=subject,
                slug=DEFAULT_MCP_SLUG,
                name="Filesystem MCP demo",
                description=(
                    "Read, write, and list files in the isolated MCP container volume."
                ),
                transport="streamable_http",
                url="http://filesystem-mcp:8002/mcp",
                enabled=True,
            )
            .on_conflict_do_nothing(index_elements=[McpServer.id])
        )
    workflow_id = default_workflow_id(subject)
    statement = (
        insert(Workflow)
        .values(
            id=workflow_id,
            owner_subject=subject,
            name=DEFAULT_WORKFLOW_NAME,
            draft=simple_agent_workflow_draft(str(skill_id), str(mcp_server_id)),
        )
        .on_conflict_do_nothing(index_elements=[Workflow.id])
    )
    await session.execute(statement)
    await session.commit()
