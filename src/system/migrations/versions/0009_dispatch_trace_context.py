"""Persist W3C trace context across the transactional outbox.

Revision ID: 0009_dispatch_trace_context
Revises: 0008_durable_execution_ledger
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_dispatch_trace_context"
down_revision: str | None = "0008_durable_execution_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_dispatches",
        sa.Column("traceparent", sa.String(128), nullable=True),
        schema="system",
    )
    op.add_column(
        "run_dispatches",
        sa.Column("tracestate", sa.String(512), nullable=True),
        schema="system",
    )


def downgrade() -> None:
    op.drop_column("run_dispatches", "tracestate", schema="system")
    op.drop_column("run_dispatches", "traceparent", schema="system")
