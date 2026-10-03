"""Add node attempts, idempotent run commands and run events.

Revision ID: 0008_durable_execution_ledger
Revises: 0007_mcp_servers
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_durable_execution_ledger"
down_revision: str | None = "0007_mcp_servers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "node_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_step_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("operation_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_ref", sa.String(2048), nullable=True),
        sa.Column("usage", postgresql.JSONB(), nullable=True),
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
            "status IN ('queued', 'running', 'succeeded', 'failed', "
            "'timed_out', 'cancelled', 'unknown')",
            name="ck_node_attempts_status",
        ),
        sa.ForeignKeyConstraint(
            ["run_step_id"], ["system.run_steps.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_key"),
        sa.UniqueConstraint("run_step_id", "attempt"),
        schema="system",
    )
    op.create_index(
        "ix_node_attempts_run_step_id",
        "node_attempts",
        ["run_step_id"],
        schema="system",
    )
    op.create_index(
        "ix_node_attempts_status",
        "node_attempts",
        ["status"],
        schema="system",
    )

    op.create_table(
        "run_commands",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("owner_subject", sa.String(255), nullable=False),
        sa.Column("scope", sa.String(255), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(32), nullable=False, server_default="accepted"),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "type IN ('start', 'cancel', 'approval_decision')",
            name="ck_run_commands_type",
        ),
        sa.CheckConstraint(
            "status IN ('accepted', 'applied', 'rejected')",
            name="ck_run_commands_status",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["system.runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_subject", "scope", "idempotency_key"),
        schema="system",
    )
    op.create_index(
        "ix_run_commands_run_id", "run_commands", ["run_id"], schema="system"
    )
    op.create_index(
        "ix_run_commands_owner_subject",
        "run_commands",
        ["owner_subject"],
        schema="system",
    )
    op.create_index(
        "ix_run_commands_status", "run_commands", ["status"], schema="system"
    )

    op.create_table(
        "run_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["system.runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence"),
        schema="system",
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"], schema="system")
    op.create_index("ix_run_events_type", "run_events", ["type"], schema="system")
    op.create_index(
        "ix_run_events_created_at", "run_events", ["created_at"], schema="system"
    )


def downgrade() -> None:
    op.drop_table("run_events", schema="system")
    op.drop_table("run_commands", schema="system")
    op.drop_table("node_attempts", schema="system")
