"""service_providers pricing (advertised prices extracted from provider websites)

Google Maps never populates `price_level` for home-services businesses, so we
extract real advertised prices from each provider's own website via Claude
(Haiku). These columns hold those values; all null until a pricing pass runs.

Revision ID: 0010_service_provider_pricing
Revises: 0009_service_provider_highlights
Create Date: 2026-05-26

"""

import sqlalchemy as sa

from alembic import op

revision = "0010_service_provider_pricing"
down_revision = "0009_service_provider_highlights"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # All nullable; backfilled by scripts/backfill_pricing.py and on the next scrape.
    op.add_column("service_providers", sa.Column("price_from", sa.String(length=32), nullable=True))
    op.add_column("service_providers", sa.Column("price_to", sa.String(length=32), nullable=True))
    op.add_column("service_providers", sa.Column("price_unit", sa.String(length=64), nullable=True))
    op.add_column("service_providers", sa.Column("price_notes", sa.String(length=300), nullable=True))
    op.add_column(
        "service_providers",
        sa.Column("price_fetched_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("service_providers", "price_fetched_at")
    op.drop_column("service_providers", "price_notes")
    op.drop_column("service_providers", "price_unit")
    op.drop_column("service_providers", "price_to")
    op.drop_column("service_providers", "price_from")
