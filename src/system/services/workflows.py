"""Idempotent synchronization for workflows bundled with AgentFlow."""

import uuid

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from domain.examples import simple_agent_workflow_draft
from domain.models import Workflow

DEFAULT_WORKFLOW_KEY = "simple-agent-summary-v1"
DEFAULT_WORKFLOW_NAME = "Demo Agent Summary without MCP"
DEFAULT_WORKFLOW_NAMESPACE = uuid.UUID("663d8d5f-8d26-4d9e-9a51-78fe4bd810ec")


def default_workflow_id(owner_subject: str) -> uuid.UUID:
    return uuid.uuid5(
        DEFAULT_WORKFLOW_NAMESPACE,
        f"{owner_subject}:{DEFAULT_WORKFLOW_KEY}",
    )


async def sync_default_workflows(
    session: AsyncSession,
    owner_subject: str,
) -> None:
    """Insert bundled workflows once without overwriting user edits."""
    subject = owner_subject.strip()
    if not subject:
        return
    workflow_id = default_workflow_id(subject)
    existing = await session.scalar(
        select(Workflow.id).where(
            Workflow.owner_subject == subject,
            or_(
                Workflow.id == workflow_id,
                Workflow.name == DEFAULT_WORKFLOW_NAME,
            ),
        )
    )
    if existing is not None:
        return
    statement = (
        insert(Workflow)
        .values(
            id=workflow_id,
            owner_subject=subject,
            name=DEFAULT_WORKFLOW_NAME,
            draft=simple_agent_workflow_draft(),
        )
        .on_conflict_do_nothing(index_elements=[Workflow.id])
    )
    await session.execute(statement)
    await session.commit()
