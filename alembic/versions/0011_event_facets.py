"""event facets: price_min + family_friendly (budget / kid-friendly filtering)

The events feed stored price only as a display string and had no kid-friendly
signal, so the agent couldn't answer "a family event under AED 500 on Friday".
These two derived columns make budget + family filtering a DB query. Both
nullable: NULL price_min = no price listed (not free), NULL family_friendly =
unknown. Backfilled from existing rows by scripts/backfill_events.py and set on
every subsequent ingest.

Revision ID: 0011_event_facets
Revises: 0010_service_provider_pricing
Create Date: 2026-05-26

"""

import sqlalchemy as sa

from alembic import op

revision = "0011_event_facets"
down_revision = "0010_service_provider_pricing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("price_min", sa.Numeric(10, 2), nullable=True))
    op.add_column("events", sa.Column("family_friendly", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "family_friendly")
    op.drop_column("events", "price_min")
