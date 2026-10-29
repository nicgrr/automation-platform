"""add persistent eBay bulk-order picking manifests

Revision ID: 20261009_01
Revises: 20261006_01
Create Date: 2026-10-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261009_01"
down_revision: Union[str, None] = "20261006_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ebay_bulk_pick_manifests",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("ebay_order_id", sa.String(128), nullable=False),
        sa.Column("line_item_id", sa.String(256), nullable=False),
        sa.Column("ebay_listing_id", sa.String(128), nullable=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("requested_quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ebay_order_id", "line_item_id"),
    )
    op.create_index("ix_ebay_bulk_pick_manifests_ebay_order_id", "ebay_bulk_pick_manifests", ["ebay_order_id"])
    op.create_index("ix_ebay_bulk_pick_manifests_ebay_listing_id", "ebay_bulk_pick_manifests", ["ebay_listing_id"])
    op.create_index("ix_ebay_bulk_pick_manifests_status", "ebay_bulk_pick_manifests", ["status"])

    op.create_table(
        "ebay_bulk_pick_items",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("manifest_id", sa.String(36), nullable=False),
        sa.Column("inventory_item_id", sa.String(36), nullable=False),
        sa.Column("shop_listing_id", sa.String(36), nullable=False),
        sa.Column("ebay_listing_id", sa.String(128), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("picked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remote_quantity_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["inventory_item_id"], ["inventory_items.id"]),
        sa.ForeignKeyConstraint(["manifest_id"], ["ebay_bulk_pick_manifests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shop_listing_id"], ["shop_listings.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("manifest_id", "inventory_item_id"),
    )
    op.create_index("ix_ebay_bulk_pick_items_manifest_id", "ebay_bulk_pick_items", ["manifest_id"])
    op.create_index("ix_ebay_bulk_pick_items_inventory_item_id", "ebay_bulk_pick_items", ["inventory_item_id"])
    op.create_index("ix_ebay_bulk_pick_items_shop_listing_id", "ebay_bulk_pick_items", ["shop_listing_id"])
    op.create_index("ix_ebay_bulk_pick_items_ebay_listing_id", "ebay_bulk_pick_items", ["ebay_listing_id"])


def downgrade() -> None:
    op.drop_index("ix_ebay_bulk_pick_items_ebay_listing_id", table_name="ebay_bulk_pick_items")
    op.drop_index("ix_ebay_bulk_pick_items_shop_listing_id", table_name="ebay_bulk_pick_items")
    op.drop_index("ix_ebay_bulk_pick_items_inventory_item_id", table_name="ebay_bulk_pick_items")
    op.drop_index("ix_ebay_bulk_pick_items_manifest_id", table_name="ebay_bulk_pick_items")
    op.drop_table("ebay_bulk_pick_items")
    op.drop_index("ix_ebay_bulk_pick_manifests_status", table_name="ebay_bulk_pick_manifests")
    op.drop_index("ix_ebay_bulk_pick_manifests_ebay_listing_id", table_name="ebay_bulk_pick_manifests")
    op.drop_index("ix_ebay_bulk_pick_manifests_ebay_order_id", table_name="ebay_bulk_pick_manifests")
    op.drop_table("ebay_bulk_pick_manifests")
