"""preference memory: behavioural signals + distilled preference profile

The co-pilot's 'taste graph' for personalising the events feed and services
directory (see backend/PERSONALIZATION.md). Two user-scoped tables, separate from
`profiles` so taste can be reset/paused without touching identity:

- preference_signals: append-only behavioural log; the item's preference-relevant
  attributes are denormalised into `attrs` (no content FK — events/providers churn).
- preference_profile: 1:1 distilled view the ranker reads (weight maps + summary).

Revision ID: 0012_preference_memory
Revises: 0011_event_facets
Create Date: 2026-05-26

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0012_preference_memory"
down_revision = "0011_event_facets"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "preference_signals",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(20), nullable=False),  # favorite|lead|view|dismiss|…
        sa.Column("domain", sa.String(20), nullable=False),  # 'event' | 'service'
        # The event/provider UUID — deliberately NOT a FK (content churns).
        sa.Column("item_id", UUID, nullable=True),
        sa.Column("attrs", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("weight", sa.Float, server_default=sa.text("1.0"), nullable=False),
        sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_preference_signals_user_created", "preference_signals", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_preference_signals_user_domain", "preference_signals", ["user_id", "domain"]
    )

    op.create_table(
        "preference_profile",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("area_weights", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column(
            "event_category_weights", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "service_category_affinity", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("tag_weights", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("weekday_weights", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("typical_budget_aed", sa.Float, nullable=True),
        sa.Column("family_bias", sa.Boolean, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("notes", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("signal_count", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column(
            "personalization_paused", sa.Boolean, server_default=sa.text("false"), nullable=False
        ),
        sa.Column("distilled_at", TS, nullable=True),
        sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_preference_profile_user"),
    )


def downgrade() -> None:
    op.drop_table("preference_profile")
    op.drop_index("ix_preference_signals_user_domain", table_name="preference_signals")
    op.drop_index("ix_preference_signals_user_created", table_name="preference_signals")
    op.drop_table("preference_signals")
