"""add has_warranty_items column to receipts

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-07 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "receipts",
        sa.Column(
            "has_warranty_items",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Backfill from existing warranty items
    op.execute(
        """
        UPDATE receipts
        SET has_warranty_items = true
        WHERE id IN (
            SELECT DISTINCT receipt_id
            FROM receipt_items
            WHERE is_under_warranty = true
        )
        """
    )


def downgrade() -> None:
    op.drop_column("receipts", "has_warranty_items")
