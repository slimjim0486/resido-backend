"""source refresh attempt backoff

Revision ID: 0017_source_attempts
Revises: 0016_service_cells
Create Date: 2026-05-29

"""

import sqlalchemy as sa

from alembic import op

revision = "0017_source_attempts"
down_revision = "0016_service_cells"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("sources", sa.Column("last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "last_error")
    op.drop_column("sources", "failure_count")
    op.drop_column("sources", "last_attempted_at")
