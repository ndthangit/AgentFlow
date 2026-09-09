"""Store encrypted LLM provider API keys."""

import sqlalchemy as sa
from alembic import op

revision = "0004_provider_api_keys"
down_revision = "0003_llm_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_providers",
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        schema="system",
    )
    op.execute("UPDATE system.llm_providers SET settings = settings - 'api_key_env'")


def downgrade() -> None:
    op.drop_column("llm_providers", "api_key_encrypted", schema="system")
