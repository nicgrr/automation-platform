"""add durable shop order reservation and reversal fields

Revision ID: 20261002_05
Revises: 20261002_04
Create Date: 2026-10-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261002_05"
down_revision: Union[str, None] = "20261002_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("shop_orders", sa.Column("stock_reserved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("shop_orders", sa.Column("stock_restored_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("shop_orders", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("shop_orders", sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("shop_order_items") as batch:
        batch.add_column(sa.Column("bulk_lot_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_shop_order_items_bulk_lot_id", "bulk_lots", ["bulk_lot_id"], ["id"]
        )
        batch.create_index("ix_shop_order_items_bulk_lot_id", ["bulk_lot_id"])


def downgrade() -> None:
    with op.batch_alter_table("shop_order_items") as batch:
        batch.drop_index("ix_shop_order_items_bulk_lot_id")
        batch.drop_constraint("fk_shop_order_items_bulk_lot_id", type_="foreignkey")
        batch.drop_column("bulk_lot_id")
    op.drop_column("shop_orders", "refunded_at")
    op.drop_column("shop_orders", "cancelled_at")
    op.drop_column("shop_orders", "stock_restored_at")
    op.drop_column("shop_orders", "stock_reserved_at")
