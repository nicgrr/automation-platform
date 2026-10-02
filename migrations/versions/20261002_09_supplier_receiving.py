"""integrate suppliers, purchase lots and inventory receiving

Revision ID: 20261002_09
Revises: 20261002_08
Create Date: 2026-10-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261002_09"
down_revision: Union[str, None] = "20261002_08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("suppliers") as batch:
        batch.create_index("ix_suppliers_name", ["name"], unique=True)

    with op.batch_alter_table("supplier_products") as batch:
        batch.create_unique_constraint(
            "uq_supplier_product_catalog", ["supplier_id", "catalog_item_id"]
        )

    with op.batch_alter_table("purchase_lots") as batch:
        batch.add_column(sa.Column("supplier_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("inventory_received_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key("fk_purchase_lots_supplier_id", "suppliers", ["supplier_id"], ["id"])
        batch.create_index("ix_purchase_lots_supplier_id", ["supplier_id"])

    with op.batch_alter_table("purchase_lot_items") as batch:
        batch.add_column(sa.Column("supplier_product_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("condition", sa.String(length=64), nullable=False, server_default="Sealed"))
        batch.add_column(sa.Column("quoted_unit_cost", sa.Numeric(10, 2), nullable=True))
        batch.add_column(sa.Column("allocated_total_cost", sa.Numeric(12, 2), nullable=True))
        batch.add_column(sa.Column("allocated_unit_cost", sa.Numeric(10, 2), nullable=True))
        batch.create_foreign_key(
            "fk_purchase_lot_items_supplier_product_id", "supplier_products",
            ["supplier_product_id"], ["id"],
        )
        batch.create_index("ix_purchase_lot_items_supplier_product_id", ["supplier_product_id"])


def downgrade() -> None:
    with op.batch_alter_table("purchase_lot_items") as batch:
        batch.drop_index("ix_purchase_lot_items_supplier_product_id")
        batch.drop_constraint("fk_purchase_lot_items_supplier_product_id", type_="foreignkey")
        batch.drop_column("allocated_unit_cost")
        batch.drop_column("allocated_total_cost")
        batch.drop_column("quoted_unit_cost")
        batch.drop_column("condition")
        batch.drop_column("supplier_product_id")

    with op.batch_alter_table("purchase_lots") as batch:
        batch.drop_index("ix_purchase_lots_supplier_id")
        batch.drop_constraint("fk_purchase_lots_supplier_id", type_="foreignkey")
        batch.drop_column("inventory_received_at")
        batch.drop_column("supplier_id")

    with op.batch_alter_table("supplier_products") as batch:
        batch.drop_constraint("uq_supplier_product_catalog", type_="unique")

    with op.batch_alter_table("suppliers") as batch:
        batch.drop_index("ix_suppliers_name")
