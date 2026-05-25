"""favorites: saved events & service providers (polymorphic, no content FK)

Revision ID: 0008_favorites
Revises: 0007_passwordless_auth_sessions
Create Date: 2026-05-25

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008_favorites"
down_revision = "0007_passwordless_auth_sessions"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "favorites",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_type", sa.String(20), nullable=False),  # 'event' | 'service'
        # The event/provider UUID — deliberately NOT a FK (content churns).
        sa.Column("item_id", UUID, nullable=False),
        sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "item_type", "item_id", name="uq_favorites_user_item"),
    )
    op.create_index("ix_favorites_user", "favorites", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_favorites_user", table_name="favorites")
    op.drop_table("favorites")
