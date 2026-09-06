"""Create workflow control-plane tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_control_plane"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS system")
    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_subject", sa.String(255), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("draft", postgresql.JSONB(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
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
        schema="system",
    )
    op.create_index(
        "ix_workflows_owner_subject", "workflows", ["owner_subject"], schema="system"
    )
    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("graph", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["system.workflows.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "version"),
        schema="system",
    )
    op.create_index(
        "ix_workflow_versions_workflow_id",
        "workflow_versions",
        ["workflow_id"],
        schema="system",
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_version_id", sa.Uuid(), nullable=False),
        sa.Column("owner_subject", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input", postgresql.JSONB(), nullable=False),
        sa.Column("output", postgresql.JSONB(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["workflow_version_id"], ["system.workflow_versions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="system",
    )
    op.create_index("ix_runs_owner_subject", "runs", ["owner_subject"], schema="system")
    op.create_index("ix_runs_status", "runs", ["status"], schema="system")
    op.create_index(
        "ix_runs_workflow_version_id",
        "runs",
        ["workflow_version_id"],
        schema="system",
    )


def downgrade() -> None:
    op.drop_table("runs", schema="system")
    op.drop_table("workflow_versions", schema="system")
    op.drop_table("workflows", schema="system")
