"""add eBay listing-studio content and ordered photos

Revision ID: 20261019_01
Revises: 20261015_01
Create Date: 2026-10-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261019_01"
down_revision: Union[str, None] = "20261015_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("shop_listings", sa.Column("ebay_title", sa.String(length=80), nullable=True))
    op.add_column("shop_listings", sa.Column("ebay_description", sa.Text(), nullable=True))
    op.add_column("shop_listings", sa.Column("ebay_photo_paths", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("shop_listings", "ebay_photo_paths")
    op.drop_column("shop_listings", "ebay_description")
    op.drop_column("shop_listings", "ebay_title")
