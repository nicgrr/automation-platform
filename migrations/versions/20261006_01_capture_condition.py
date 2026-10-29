"""preserve physical condition through card capture handoff

Revision ID: 20261006_01
Revises: 20261003_03
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261006_01"
down_revision: Union[str, None] = "20261003_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("captured_cards", sa.Column("condition", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("captured_cards", "condition")
