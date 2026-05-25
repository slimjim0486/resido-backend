"""service_providers (Services vertical — Tier A-style durable provider rows)

Revision ID: 0004_service_providers
Revises: 0003_source_content_hash
Create Date: 2026-05-25

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004_service_providers"
down_revision = "0003_source_content_hash"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "service_providers",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("area", sa.String(120), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("reviews_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("whatsapp", sa.String(64), nullable=True),
        sa.Column("website", sa.String(1024), nullable=True),
        sa.Column("maps_url", sa.String(1024), nullable=True),
        sa.Column("place_id", sa.String(255), nullable=False, unique=True),
        sa.Column("price_level", sa.String(16), nullable=True),
        sa.Column("photo_url", sa.String(1024), nullable=True),
        sa.Column("hours", postgresql.JSONB(), nullable=True),
        sa.Column("google_rank", sa.Integer(), nullable=True),
        sa.Column("score", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_sponsored", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("source", sa.String(120), server_default=sa.text("'google_maps'"), nullable=False),
        sa.Column("fetched_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_service_providers_cat_area", "service_providers", ["category", "area"]
    )
    # Default ranking: score desc within a category (Postgres can also scan this
    # backwards for asc, but we declare DESC to match the read path exactly).
    op.create_index(
        "ix_service_providers_cat_score",
        "service_providers",
        ["category", sa.text("score DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_service_providers_cat_score", table_name="service_providers")
    op.drop_index("ix_service_providers_cat_area", table_name="service_providers")
    op.drop_table("service_providers")
