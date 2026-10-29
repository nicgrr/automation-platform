"""per-listing card finish override, separate from catalogue rarity

Revision ID: 20261030_01
Revises: 20261023_02
Create Date: 2026-10-30

Production already has this column: it was added on 2026-10-29 by a one-off
script before this migration existed. The column is only added when missing,
so upgrading production just records the revision.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261030_01"
down_revision: Union[str, None] = "20261023_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("shop_listings")}
    if "finish" not in columns:
        op.add_column("shop_listings", sa.Column("finish", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("shop_listings", "finish")
