"""Add user-owned LLM provider configurations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_llm_providers"
down_revision = "0002_skills"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_providers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_subject", sa.String(255), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("settings", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
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
        sa.UniqueConstraint("owner_subject", "name"),
        schema="system",
    )
    op.create_index(
        "ix_llm_providers_owner_subject",
        "llm_providers",
        ["owner_subject"],
        schema="system",
    )
    op.create_index("ix_llm_providers_kind", "llm_providers", ["kind"], schema="system")


def downgrade() -> None:
    op.drop_table("llm_providers", schema="system")
