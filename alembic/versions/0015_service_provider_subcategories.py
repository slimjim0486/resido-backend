"""service provider subcategories

Revision ID: 0015_service_subcats
Revises: 0014_review_curation
Create Date: 2026-05-27

"""

import sqlalchemy as sa

from alembic import op

revision = "0015_service_subcats"
down_revision = "0014_review_curation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_providers",
        sa.Column("subcategory", sa.String(length=80), server_default="general", nullable=False),
    )
    op.execute(
        """
        DO $$
        DECLARE
            constraint_name text;
        BEGIN
            SELECT c.conname
              INTO constraint_name
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE t.relname = 'service_providers'
              AND n.nspname = current_schema()
              AND c.contype = 'u'
              AND pg_get_constraintdef(c.oid) = 'UNIQUE (place_id)'
            LIMIT 1;

            IF constraint_name IS NOT NULL THEN
                EXECUTE format(
                    'ALTER TABLE service_providers DROP CONSTRAINT %I',
                    constraint_name
                );
            END IF;
        END $$;
        """
    )
    op.create_unique_constraint(
        "uq_service_providers_place_category_subcategory",
        "service_providers",
        ["place_id", "category", "subcategory"],
    )
    op.create_index(
        "ix_service_providers_cat_subcat_area",
        "service_providers",
        ["category", "subcategory", "area"],
        unique=False,
    )


def downgrade() -> None:
    # Keep one row per Google place before restoring the old global uniqueness.
    op.execute(
        """
        DELETE FROM service_providers sp
        USING service_providers newer
        WHERE sp.place_id = newer.place_id
          AND sp.id <> newer.id
          AND (
            sp.fetched_at < newer.fetched_at
            OR (sp.fetched_at = newer.fetched_at AND sp.id::text < newer.id::text)
          )
        """
    )
    op.drop_index("ix_service_providers_cat_subcat_area", table_name="service_providers")
    op.drop_constraint(
        "uq_service_providers_place_category_subcategory",
        "service_providers",
        type_="unique",
    )
    op.create_unique_constraint(
        "service_providers_place_id_key",
        "service_providers",
        ["place_id"],
    )
    op.drop_column("service_providers", "subcategory")
