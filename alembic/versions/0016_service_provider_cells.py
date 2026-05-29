"""service provider refresh cells

Revision ID: 0016_service_cells
Revises: 0015_service_subcats
Create Date: 2026-05-29

"""

import sqlalchemy as sa

from alembic import op

revision = "0016_service_cells"
down_revision = "0015_service_subcats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_provider_cells",
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("subcategory", sa.String(length=80), server_default="general", nullable=False),
        sa.Column("area", sa.String(length=120), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("fetched_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("kept_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("inserted_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("source", sa.String(length=120), server_default="google_maps", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("category", "subcategory", "area", name="pk_service_provider_cells"),
    )
    op.create_index(
        "ix_service_provider_cells_fetched_at",
        "service_provider_cells",
        ["fetched_at"],
        unique=False,
    )
    op.execute(
        """
        INSERT INTO service_provider_cells (
            category,
            subcategory,
            area,
            fetched_at,
            fetched_count,
            kept_count,
            inserted_count,
            source,
            created_at,
            updated_at
        )
        SELECT
            category,
            COALESCE(subcategory, 'general') AS subcategory,
            COALESCE(area, '') AS area,
            MAX(fetched_at) AS fetched_at,
            COUNT(*) AS fetched_count,
            COUNT(*) AS kept_count,
            0 AS inserted_count,
            'provider_row_backfill' AS source,
            NOW() AS created_at,
            NOW() AS updated_at
        FROM service_providers
        GROUP BY category, COALESCE(subcategory, 'general'), COALESCE(area, '')
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_service_provider_cells_fetched_at", table_name="service_provider_cells")
    op.drop_table("service_provider_cells")
