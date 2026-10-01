"""make inventory catalogue identity canonical

Revision ID: 20261002_03
Revises: 20261001_02
Create Date: 2026-10-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261002_03"
down_revision: Union[str, None] = "20261001_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("inventory_items", recreate="always") as batch:
        batch.drop_constraint("uq_inventory_card_variant_condition", type_="unique")
        batch.alter_column(
            "catalog_item_id", existing_type=sa.String(length=64), nullable=False
        )
        batch.create_unique_constraint(
            "uq_inventory_catalog_item_variant_condition",
            ["catalog_item_id", "variant", "condition"],
        )


def downgrade() -> None:
    with op.batch_alter_table("inventory_items", recreate="always") as batch:
        batch.drop_constraint("uq_inventory_catalog_item_variant_condition", type_="unique")
        batch.alter_column(
            "catalog_item_id", existing_type=sa.String(length=64), nullable=True
        )
        batch.create_unique_constraint(
            "uq_inventory_card_variant_condition", ["card_id", "variant", "condition"]
        )
