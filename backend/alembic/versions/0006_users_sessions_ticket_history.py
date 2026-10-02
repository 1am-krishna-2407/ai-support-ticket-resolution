"""Users, sessions, saved ticket analyses; request_logs.user_id for the audit trail.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("password_changed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("role IN ('admin', 'agent')", name="role_valid"),
        sa.CheckConstraint("email = lower(email)", name="email_lowercase"),
    )
    op.create_table(
        "user_sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])
    op.create_table(
        "ticket_analyses",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("environment", postgresql.JSONB),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("category", sa.String(64)),
        sa.Column("priority", sa.String(16)),
        sa.Column("grounded", sa.Boolean),
        sa.Column("latency_ms", sa.Float),
        sa.Column("result", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("ix_ticket_analyses_user_created", "ticket_analyses", ["user_id", "created_at"])
    op.add_column(
        "request_logs",
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.create_index("ix_request_logs_user_id", "request_logs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_request_logs_user_id", table_name="request_logs")
    op.drop_column("request_logs", "user_id")
    op.drop_table("ticket_analyses")
    op.drop_table("user_sessions")
    op.drop_table("users")
