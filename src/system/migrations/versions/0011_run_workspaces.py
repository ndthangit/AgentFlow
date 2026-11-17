"""Add persistent workspace archive metadata to workflow runs.

Revision ID: 0011_run_workspaces
Revises: 0010_run_execution_leases
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_run_workspaces"
down_revision: str | None = "0010_run_execution_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "workspace_status", sa.String(32), nullable=False, server_default="none"
        ),
        schema="system",
    )
    op.add_column(
        "runs", sa.Column("workspace_size_bytes", sa.BigInteger()), schema="system"
    )
    op.add_column(
        "runs", sa.Column("workspace_file_count", sa.Integer()), schema="system"
    )
    op.add_column("runs", sa.Column("workspace_sha256", sa.String(64)), schema="system")
    op.add_column(
        "runs", sa.Column("workspace_archive_key", sa.String(512)), schema="system"
    )
    op.add_column(
        "runs",
        sa.Column("workspace_expires_at", sa.DateTime(timezone=True)),
        schema="system",
    )


def downgrade() -> None:
    for column in (
        "workspace_expires_at",
        "workspace_archive_key",
        "workspace_sha256",
        "workspace_file_count",
        "workspace_size_bytes",
        "workspace_status",
    ):
        op.drop_column("runs", column, schema="system")
