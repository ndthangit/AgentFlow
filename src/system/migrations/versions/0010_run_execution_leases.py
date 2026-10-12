"""Add durable workflow worker leases and cleanup state.

Revision ID: 0010_run_execution_leases
Revises: 0009_dispatch_trace_context
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_run_execution_leases"
down_revision: str | None = "0009_dispatch_trace_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_execution_leases",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("message_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "cleanup_status", sa.String(32), nullable=False, server_default="pending"
        ),
        sa.Column("cleanup_error", sa.String(500), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('active', 'released', 'expired')",
            name="ck_run_execution_leases_status",
        ),
        sa.CheckConstraint(
            "cleanup_status IN ('pending', 'cleaned')",
            name="ck_run_execution_leases_cleanup_status",
        ),
        sa.CheckConstraint(
            "generation > 0", name="ck_run_execution_leases_generation"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["system.runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("run_id"),
        schema="system",
    )
    op.create_index(
        "ix_run_execution_leases_owner_id",
        "run_execution_leases",
        ["owner_id"],
        schema="system",
    )
    op.create_index(
        "ix_run_execution_leases_status",
        "run_execution_leases",
        ["status"],
        schema="system",
    )
    op.create_index(
        "ix_run_execution_leases_lease_expires_at",
        "run_execution_leases",
        ["lease_expires_at"],
        schema="system",
    )
    op.create_index(
        "ix_run_execution_leases_cleanup_status",
        "run_execution_leases",
        ["cleanup_status"],
        schema="system",
    )


def downgrade() -> None:
    op.drop_table("run_execution_leases", schema="system")
