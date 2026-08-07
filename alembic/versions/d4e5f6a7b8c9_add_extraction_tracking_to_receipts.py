"""add extraction tracking columns to receipts

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-07 11:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("receipts", sa.Column("extraction_started_at", sa.DateTime(), nullable=True))
    op.add_column("receipts", sa.Column("extraction_error", sa.String(length=512), nullable=True))
    op.add_column(
        "receipts",
        sa.Column("extraction_attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("receipts", "extraction_attempts")
    op.drop_column("receipts", "extraction_error")
    op.drop_column("receipts", "extraction_started_at")
