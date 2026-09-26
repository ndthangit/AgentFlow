"""Add the remote MCP server registry.

Revision ID: 0007_mcp_servers
Revises: 0006_run_dispatch_outbox
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_mcp_servers"
down_revision: str | None = "0006_run_dispatch_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_subject", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("transport", sa.String(32), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("headers_encrypted", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_subject", "slug"),
        schema="system",
    )
    op.create_index(
        "ix_mcp_servers_owner_subject",
        "mcp_servers",
        ["owner_subject"],
        schema="system",
    )


def downgrade() -> None:
    op.drop_table("mcp_servers", schema="system")
