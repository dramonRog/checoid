"""add shop_name column to receipts

Revision ID: a1b2c3d4e5f6
Revises: 8f0dc8c066ce
Create Date: 2026-08-06 16:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "8f0dc8c066ce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("receipts", sa.Column("shop_name", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("receipts", "shop_name")
