"""preserve operator-reported bulk-pick variants

Revision ID: 20261011_01
Revises: 20261009_01
Create Date: 2026-10-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261011_01"
down_revision: Union[str, None] = "20261009_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ebay_bulk_pick_items", sa.Column("reported_variant", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("ebay_bulk_pick_items", "reported_variant")
