"""catalogue pricing support policy

Revision ID: 20261001_02
Revises: 20261001_01
Create Date: 2026-10-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261001_02"
down_revision: Union[str, None] = "20261001_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MANUAL_ONLY_IDS = (
    "sdbh-abs-12",
    "sdbh-cp-zh-307",
    "dragon-ball-fusion-world-e-10",
)


def upgrade() -> None:
    op.add_column(
        "catalog_items",
        sa.Column("pricing_policy", sa.String(length=16), nullable=False,
                  server_default="automated"),
    )
    op.add_column(
        "catalog_items",
        sa.Column("pricing_policy_reason", sa.String(length=256), nullable=True),
    )
    catalog_items = sa.table(
        "catalog_items",
        sa.column("id", sa.String()),
        sa.column("pricing_policy", sa.String()),
        sa.column("pricing_policy_reason", sa.String()),
    )
    op.execute(
        catalog_items.update()
        .where(catalog_items.c.id.in_(MANUAL_ONLY_IDS))
        .values(
            pricing_policy="manual_only",
            pricing_policy_reason="No trustworthy automated source for this manually catalogued printing",
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("catalog_items") as batch:
        batch.drop_column("pricing_policy_reason")
        batch.drop_column("pricing_policy")
