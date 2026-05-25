"""documents: confidence + last_reminded_on (renewal radar — see DOCUMENTS.md)

Revision ID: 0006_document_renewals
Revises: 0005_service_provider_is_active
Create Date: 2026-05-25

"""

import sqlalchemy as sa

from alembic import op

revision = "0006_document_renewals"
down_revision = "0005_service_provider_is_active"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # "confirmed" = user-stated; "estimated" = derived (visa-anchor cascade).
    op.add_column(
        "documents",
        sa.Column(
            "confidence",
            sa.String(length=20),
            server_default=sa.text("'confirmed'"),
            nullable=False,
        ),
    )
    # Dedupe key for the (future) reminder job; lead-time is derived from the catalog.
    op.add_column("documents", sa.Column("last_reminded_on", sa.Date(), nullable=True))
    # Renewal radar is always read as (user, soonest expiry first).
    op.create_index("ix_documents_user_expiry", "documents", ["user_id", "expiry_date"])


def downgrade() -> None:
    op.drop_index("ix_documents_user_expiry", table_name="documents")
    op.drop_column("documents", "last_reminded_on")
    op.drop_column("documents", "confidence")
