"""events feed (Tier B lifestyle 'what's on')

Revision ID: 0002_events
Revises: 0001_initial
Create Date: 2026-05-25

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_events"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("venue", sa.String(255), nullable=True),
        sa.Column("area", sa.String(120), nullable=True),
        sa.Column("url", sa.String(1024), nullable=False, unique=True),
        sa.Column("image_url", sa.String(1024), nullable=True),
        sa.Column("price_from", sa.String(80), nullable=True),
        sa.Column("starts_at", TS, nullable=True),
        sa.Column("ends_at", TS, nullable=True),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("expires_at", TS, nullable=True),
        sa.Column("is_published", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("fetched_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_events_live", "events", ["is_published", "expires_at"])
    op.create_index("ix_events_category", "events", ["category"])


def downgrade() -> None:
    op.drop_index("ix_events_category", table_name="events")
    op.drop_index("ix_events_live", table_name="events")
    op.drop_table("events")
