"""Add the skill catalog and workflow selections."""

import sqlalchemy as sa
from alembic import op

revision = "0002_skills"
down_revision = "0001_control_plane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_subject", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
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
        "ix_skills_owner_subject", "skills", ["owner_subject"], schema="system"
    )
    op.create_table(
        "workflow_skills",
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["system.skills.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["system.workflows.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("workflow_id", "skill_id"),
        schema="system",
    )


def downgrade() -> None:
    op.drop_table("workflow_skills", schema="system")
    op.drop_table("skills", schema="system")
