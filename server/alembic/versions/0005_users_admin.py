"""users.disabled_at + invited_at + invited_by

Revision ID: 0005
Revises: 0003
Create Date: 2026-04-30

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("disabled_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("invited_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("invited_by", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.drop_column("invited_by")
        b.drop_column("invited_at")
        b.drop_column("disabled_at")
