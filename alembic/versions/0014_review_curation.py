"""service_providers review curation

Revision ID: 0014_review_curation
Revises: 0013_event_tags
Create Date: 2026-05-27

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0014_review_curation"
down_revision = "0013_event_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_providers",
        sa.Column("review_curation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute(
        """
        UPDATE service_providers
        SET review_curation = jsonb_build_object(
            'summary',
            'No review text was available in the scrape; use the public rating and review volume as the trust signal.',
            'positives', '[]'::jsonb,
            'watchouts', '[]'::jsonb,
            'sample_size', 0,
            'sample_average_rating', NULL,
            'rating_basis',
            CONCAT(ROUND(rating::numeric, 1)::text, ' from ', reviews_count::text, ' Google reviews')
        )
        WHERE rating IS NOT NULL
          AND reviews_count > 0
          AND review_curation IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("service_providers", "review_curation")
