"""initial schema + pgvector

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-24

"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
TS = sa.DateTime(timezone=True)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "profiles",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("nationality", sa.String(100), nullable=True),
        sa.Column("emirate", sa.String(100), nullable=True),
        sa.Column("visa_type", sa.String(100), nullable=True),
        sa.Column("arrival_date", sa.Date(), nullable=True),
        sa.Column("employer", sa.String(255), nullable=True),
        sa.Column("household", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("notes", sa.String(2000), nullable=True),
        *_timestamps(),
    )

    op.create_table(
        "checklist_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("category", sa.String(80), nullable=True),
        sa.Column("status", sa.String(20), server_default="todo", nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source_url", sa.String(1024), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_checklist_items_user_status", "checklist_items", ["user_id", "status"])

    op.create_table(
        "deadlines",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("category", sa.String(80), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("recurrence", sa.String(40), nullable=True),
        sa.Column("reminder_sent", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("source_url", sa.String(1024), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_deadlines_user_due", "deadlines", ["user_id", "due_date"])

    op.create_table(
        "documents",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("doc_type", sa.String(80), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("file_url", sa.String(1024), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
    )

    op.create_table(
        "sources",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("url", sa.String(1024), nullable=False, unique=True),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("publisher", sa.String(255), nullable=True),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("last_fetched_at", TS, nullable=True),
        sa.Column("refresh_ttl_days", sa.Integer(), server_default="14", nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_sources_category", "sources", ["category"])

    op.create_table(
        "kb_chunks",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("source_id", UUID, sa.ForeignKey("sources.id", ondelete="SET NULL"), nullable=True),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("fetched_at", TS, server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_kb_chunks_category", "kb_chunks", ["category"])

    op.create_table(
        "leads",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vertical", sa.String(80), nullable=False),
        sa.Column("status", sa.String(30), server_default="new", nullable=False),
        sa.Column("payload", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        *_timestamps(),
    )


def downgrade() -> None:
    op.drop_table("leads")
    op.drop_index("ix_kb_chunks_category", table_name="kb_chunks")
    op.drop_table("kb_chunks")
    op.drop_index("ix_sources_category", table_name="sources")
    op.drop_table("sources")
    op.drop_table("documents")
    op.drop_index("ix_deadlines_user_due", table_name="deadlines")
    op.drop_table("deadlines")
    op.drop_index("ix_checklist_items_user_status", table_name="checklist_items")
    op.drop_table("checklist_items")
    op.drop_table("profiles")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS vector")
