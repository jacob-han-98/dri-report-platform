"""users

Revision ID: 0001
Revises:
Create Date: 2026-04-30

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("email", sa.String(), primary_key=True),
        sa.Column("google_sub", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=False, server_default="user"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("google_sub", name="uq_users_google_sub"),
    )


def downgrade() -> None:
    op.drop_table("users")
