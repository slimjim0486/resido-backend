"""passwordless auth sessions

Revision ID: 0007_passwordless_auth_sessions
Revises: 0006_document_renewals
Create Date: 2026-05-25

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007_passwordless_auth_sessions"
down_revision = "0006_document_renewals"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.alter_column("users", "email", existing_type=sa.String(length=255), nullable=True)
    op.alter_column("users", "hashed_password", existing_type=sa.String(length=255), nullable=True)
    op.add_column(
        "users",
        sa.Column("is_anonymous", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("users", sa.Column("email_verified_at", TS, nullable=True))
    op.add_column("users", sa.Column("last_login_at", TS, nullable=True))

    op.create_table(
        "auth_identities",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("provider_subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "provider", "provider_subject", name="uq_auth_identities_provider_subject"
        ),
    )
    op.create_index("ix_auth_identities_user_id", "auth_identities", ["user_id"])
    op.create_index("ix_auth_identities_email", "auth_identities", ["email"])

    op.create_table(
        "auth_sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("refresh_token_family", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("ip_address", sa.String(length=80), nullable=True),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("replaced_by_session_id", UUID, nullable=True),
        sa.Column("last_seen_at", TS, nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_refresh_token_family", "auth_sessions", ["refresh_token_family"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_index("ix_auth_sessions_revoked_at", "auth_sessions", ["revoked_at"])

    op.create_table(
        "email_otp_challenges",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("purpose", sa.String(length=40), server_default="sign_in", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("consumed_at", TS, nullable=True),
        sa.Column("ip_address", sa.String(length=80), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("delivery_target", sa.Text(), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_email_otp_challenges_email", "email_otp_challenges", ["email"])
    op.create_index("ix_email_otp_challenges_expires_at", "email_otp_challenges", ["expires_at"])
    op.create_index("ix_email_otp_challenges_consumed_at", "email_otp_challenges", ["consumed_at"])


def downgrade() -> None:
    op.drop_index("ix_email_otp_challenges_consumed_at", table_name="email_otp_challenges")
    op.drop_index("ix_email_otp_challenges_expires_at", table_name="email_otp_challenges")
    op.drop_index("ix_email_otp_challenges_email", table_name="email_otp_challenges")
    op.drop_table("email_otp_challenges")

    op.drop_index("ix_auth_sessions_revoked_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_refresh_token_family", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")

    op.drop_index("ix_auth_identities_email", table_name="auth_identities")
    op.drop_index("ix_auth_identities_user_id", table_name="auth_identities")
    op.drop_table("auth_identities")

    op.drop_column("users", "last_login_at")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "is_anonymous")
    op.alter_column("users", "hashed_password", existing_type=sa.String(length=255), nullable=False)
    op.alter_column("users", "email", existing_type=sa.String(length=255), nullable=False)
