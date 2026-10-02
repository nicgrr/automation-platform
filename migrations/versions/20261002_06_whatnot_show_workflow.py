"""add inventory-backed Whatnot show lifecycle

Revision ID: 20261002_06
Revises: 20261002_05
Create Date: 2026-10-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261002_06"
down_revision: Union[str, None] = "20261002_05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


show_status = sa.Enum("DRAFT", "LIVE", "COMPLETED", "CANCELLED", name="whatnotshowstatus")


def upgrade() -> None:
    op.add_column("whatnot_shows", sa.Column("category", sa.String(length=64), nullable=False, server_default="TCG"))
    op.add_column("whatnot_shows", sa.Column("status", show_status, nullable=False, server_default="DRAFT"))
    op.add_column("whatnot_shows", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("whatnot_shows", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("whatnot_shows", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_whatnot_shows_status", "whatnot_shows", ["status"])

    op.add_column("whatnot_show_items", sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("whatnot_show_items", sa.Column("fees_amount", sa.Numeric(10, 2), nullable=True))
    op.add_column("whatnot_show_items", sa.Column("buyer_shipping", sa.Numeric(10, 2), nullable=False, server_default="0"))
    op.add_column("whatnot_show_items", sa.Column("buyer_tax", sa.Numeric(10, 2), nullable=False, server_default="0"))
    op.add_column("whatnot_show_items", sa.Column("buyer_handle", sa.String(length=128), nullable=True))
    op.add_column("whatnot_show_items", sa.Column("whatnot_order_id", sa.String(length=128), nullable=True))
    op.add_column("whatnot_show_items", sa.Column("stock_reserved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("whatnot_show_items", sa.Column("stock_released_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_whatnot_show_items_buyer_handle", "whatnot_show_items", ["buyer_handle"])
    op.create_index("ix_whatnot_show_items_whatnot_order_id", "whatnot_show_items", ["whatnot_order_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_whatnot_show_items_whatnot_order_id", table_name="whatnot_show_items")
    op.drop_index("ix_whatnot_show_items_buyer_handle", table_name="whatnot_show_items")
    op.drop_column("whatnot_show_items", "stock_released_at")
    op.drop_column("whatnot_show_items", "stock_reserved_at")
    op.drop_column("whatnot_show_items", "whatnot_order_id")
    op.drop_column("whatnot_show_items", "buyer_handle")
    op.drop_column("whatnot_show_items", "buyer_tax")
    op.drop_column("whatnot_show_items", "buyer_shipping")
    op.drop_column("whatnot_show_items", "fees_amount")
    op.drop_column("whatnot_show_items", "quantity")
    op.drop_index("ix_whatnot_shows_status", table_name="whatnot_shows")
    op.drop_column("whatnot_shows", "cancelled_at")
    op.drop_column("whatnot_shows", "completed_at")
    op.drop_column("whatnot_shows", "started_at")
    op.drop_column("whatnot_shows", "status")
    op.drop_column("whatnot_shows", "category")
