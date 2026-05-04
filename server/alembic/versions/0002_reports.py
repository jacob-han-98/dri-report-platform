"""reports + report_viewers

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-30

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("slug", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "owner_email",
            sa.String(),
            sa.ForeignKey("users.email"),
            nullable=False,
        ),
        sa.Column("visibility", sa.String(), nullable=False, server_default="restricted"),
        sa.Column("type", sa.String(), nullable=False, server_default="static"),
        sa.Column("storage_path", sa.String(), nullable=False),
        sa.Column("entry_point", sa.String(), nullable=False, server_default="index.html"),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("file_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("last_viewed_at", sa.DateTime(), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("idx_reports_owner", "reports", ["owner_email"])
    op.create_index("idx_reports_updated", "reports", ["updated_at"])

    op.create_table(
        "report_viewers",
        sa.Column(
            "slug",
            sa.String(),
            sa.ForeignKey("reports.slug", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_email",
            sa.String(),
            sa.ForeignKey("users.email"),
            primary_key=True,
        ),
        sa.Column("granted_by", sa.String(), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    op.drop_table("report_viewers")
    op.drop_index("idx_reports_updated", table_name="reports")
    op.drop_index("idx_reports_owner", table_name="reports")
    op.drop_table("reports")
