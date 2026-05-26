"""service_providers.highlights (feature chips from Google's additionalInfo)

Revision ID: 0009_service_provider_highlights
Revises: 0008_favorites
Create Date: 2026-05-26

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0009_service_provider_highlights"
down_revision = "0008_favorites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable JSONB list of short feature strings; backfilled on the next scrape.
    op.add_column(
        "service_providers",
        sa.Column("highlights", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("service_providers", "highlights")
