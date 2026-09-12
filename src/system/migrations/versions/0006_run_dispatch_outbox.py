"""Add transactional run dispatch outbox.

Revision ID: 0006_run_dispatch_outbox
Revises: 0005_run_steps
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_run_dispatch_outbox"
down_revision: str | None = "0005_run_steps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_dispatches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["system.runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
        schema="system",
    )
    op.create_index(
        "ix_run_dispatches_run_id",
        "run_dispatches",
        ["run_id"],
        schema="system",
    )
    op.create_index(
        "ix_run_dispatches_dispatched_at",
        "run_dispatches",
        ["dispatched_at"],
        schema="system",
    )


def downgrade() -> None:
    op.drop_table("run_dispatches", schema="system")
