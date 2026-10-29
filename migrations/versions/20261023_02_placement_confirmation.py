"""track whether reserved tray slots were physically put away

Revision ID: 20261023_02
Revises: 20261023_01
Create Date: 2026-10-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261023_02"
down_revision: Union[str, None] = "20261023_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("inventory_placements", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("inventory_placements", sa.Column("confirmed_by", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("inventory_placements", "confirmed_by")
    op.drop_column("inventory_placements", "confirmed_at")
