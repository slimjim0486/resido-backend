"""event tags: free-form taste tags for personalisation (not filtering)

Cuisine/vibe/genre tags ("brunch", "jazz", "rooftop", "vegetarian-friendly")
extracted by Claude at ingestion. They feed the preference tag_weights so the feed
can rank up "your kind of thing" beyond the coarse category/area facets (see
PERSONALIZATION.md Phase 3). Nullable: older rows carry no tags until re-ingested,
which simply means no tag signal (graceful).

Revision ID: 0013_event_tags
Revises: 0012_preference_memory
Create Date: 2026-05-27

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0013_event_tags"
down_revision = "0012_preference_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("tags", postgresql.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("events", "tags")
