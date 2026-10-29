"""store recipient phone number for postage labels

Revision ID: 20261015_01
Revises: 20261011_01
Create Date: 2026-10-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261015_01"
down_revision: Union[str, None] = "20261011_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("shop_orders", sa.Column("ship_to_phone", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("shop_orders", "ship_to_phone")
