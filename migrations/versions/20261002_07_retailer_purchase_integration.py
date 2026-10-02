"""link retailer purchases to purchase lots and inventory

Revision ID: 20261002_07
Revises: 20261002_06
Create Date: 2026-10-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261002_07"
down_revision: Union[str, None] = "20261002_06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("purchase_lots") as batch:
        batch.add_column(sa.Column("retailer_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("external_order_number", sa.String(length=256), nullable=True))
        batch.create_foreign_key("fk_purchase_lots_retailer_id", "retailers", ["retailer_id"], ["id"])
        batch.create_index("ix_purchase_lots_retailer_id", ["retailer_id"])
        batch.create_index("ix_purchase_lots_external_order_number", ["external_order_number"])
        batch.create_unique_constraint(
            "uq_purchase_lot_retailer_order", ["retailer_id", "external_order_number"]
        )

    with op.batch_alter_table("retailer_purchases") as batch:
        batch.add_column(sa.Column("submission_key", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("purchase_lot_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_retailer_purchases_purchase_lot_id", "purchase_lots", ["purchase_lot_id"], ["id"]
        )
        batch.create_index("ix_retailer_purchases_purchase_lot_id", ["purchase_lot_id"])
        batch.create_index("ix_retailer_purchases_submission_key", ["submission_key"], unique=True)

    with op.batch_alter_table("purchase_lot_items") as batch:
        batch.add_column(sa.Column("retailer_purchase_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("inventory_item_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_purchase_lot_items_retailer_purchase_id", "retailer_purchases",
            ["retailer_purchase_id"], ["id"],
        )
        batch.create_foreign_key(
            "fk_purchase_lot_items_inventory_item_id", "inventory_items",
            ["inventory_item_id"], ["id"],
        )
        batch.create_index(
            "ix_purchase_lot_items_retailer_purchase_id", ["retailer_purchase_id"], unique=True
        )
        batch.create_index("ix_purchase_lot_items_inventory_item_id", ["inventory_item_id"])


def downgrade() -> None:
    with op.batch_alter_table("purchase_lot_items") as batch:
        batch.drop_index("ix_purchase_lot_items_inventory_item_id")
        batch.drop_index("ix_purchase_lot_items_retailer_purchase_id")
        batch.drop_constraint("fk_purchase_lot_items_inventory_item_id", type_="foreignkey")
        batch.drop_constraint("fk_purchase_lot_items_retailer_purchase_id", type_="foreignkey")
        batch.drop_column("inventory_item_id")
        batch.drop_column("retailer_purchase_id")

    with op.batch_alter_table("retailer_purchases") as batch:
        batch.drop_index("ix_retailer_purchases_submission_key")
        batch.drop_index("ix_retailer_purchases_purchase_lot_id")
        batch.drop_constraint("fk_retailer_purchases_purchase_lot_id", type_="foreignkey")
        batch.drop_column("purchase_lot_id")
        batch.drop_column("submission_key")

    with op.batch_alter_table("purchase_lots") as batch:
        batch.drop_constraint("uq_purchase_lot_retailer_order", type_="unique")
        batch.drop_index("ix_purchase_lots_external_order_number")
        batch.drop_index("ix_purchase_lots_retailer_id")
        batch.drop_constraint("fk_purchase_lots_retailer_id", type_="foreignkey")
        batch.drop_column("external_order_number")
        batch.drop_column("retailer_id")
