"""api_tokens + audit_logs

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-30

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("token_hash", sa.String(), primary_key=True),
        sa.Column("prefix", sa.String(), nullable=False),
        sa.Column(
            "user_email",
            sa.String(),
            sa.ForeignKey("users.email"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=True),  # JSON array; null = full
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_ip", sa.String(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("idx_api_tokens_user", "api_tokens", ["user_email"])
    op.create_index("idx_api_tokens_prefix", "api_tokens", ["prefix"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_email", sa.String(), nullable=True),
        sa.Column("actor_type", sa.String(), nullable=False),  # session | token | mcp | system
        sa.Column("token_prefix", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("resource", sa.String(), nullable=True),
        sa.Column("ip", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),  # JSON
        sa.Column(
            "timestamp",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "idx_audit_user_time", "audit_logs", ["user_email", "timestamp"]
    )
    op.create_index(
        "idx_audit_action_time", "audit_logs", ["action", "timestamp"]
    )


def downgrade() -> None:
    op.drop_index("idx_audit_action_time", table_name="audit_logs")
    op.drop_index("idx_audit_user_time", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("idx_api_tokens_prefix", table_name="api_tokens")
    op.drop_index("idx_api_tokens_user", table_name="api_tokens")
    op.drop_table("api_tokens")
