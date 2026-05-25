"""source.content_hash (skip re-embed on unchanged refresh)

Revision ID: 0003_source_content_hash
Revises: 0002_events
Create Date: 2026-05-25

"""

import sqlalchemy as sa

from alembic import op

revision = "0003_source_content_hash"
down_revision = "0002_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("content_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "content_hash")
