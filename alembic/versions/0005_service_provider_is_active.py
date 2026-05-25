"""service_providers.is_active (retirement soft-hide of stale/closed listings)

Revision ID: 0005_service_provider_is_active
Revises: 0004_service_providers
Create Date: 2026-05-25

"""

import sqlalchemy as sa

from alembic import op

revision = "0005_service_provider_is_active"
down_revision = "0004_service_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_providers",
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    # Read path is always (category, score desc) over *active* rows, so a partial
    # index on is_active keeps it lean as retired rows accumulate.
    op.create_index(
        "ix_service_providers_active",
        "service_providers",
        ["category", sa.text("score DESC")],
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.drop_index("ix_service_providers_active", table_name="service_providers")
    op.drop_column("service_providers", "is_active")
