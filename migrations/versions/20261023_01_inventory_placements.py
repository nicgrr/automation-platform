"""add stable tray slots for photographed physical cards

Revision ID: 20261023_01
Revises: 20261022_01
Create Date: 2026-10-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261023_01"
down_revision: Union[str, None] = "20261022_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("listing_photo_batches", sa.Column("storage_tray", sa.String(32), nullable=True))
    op.add_column("listing_photo_batches", sa.Column("starting_slot", sa.Integer(), nullable=True))
    op.create_table(
        "inventory_placements",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("inventory_item_id", sa.String(36), nullable=True),
        sa.Column("listing_photo_pair_id", sa.String(36), nullable=False),
        sa.Column("storage_location_id", sa.String(36), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["inventory_item_id"], ["inventory_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["listing_photo_pair_id"], ["listing_photo_pairs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["storage_location_id"], ["storage_locations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("listing_photo_pair_id"),
    )
    op.create_index(
        op.f("ix_inventory_placements_inventory_item_id"),
        "inventory_placements", ["inventory_item_id"], unique=False,
    )
    op.create_index(
        op.f("ix_inventory_placements_listing_photo_pair_id"),
        "inventory_placements", ["listing_photo_pair_id"], unique=True,
    )
    op.create_index(
        op.f("ix_inventory_placements_storage_location_id"),
        "inventory_placements", ["storage_location_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_inventory_placements_storage_location_id"), table_name="inventory_placements")
    op.drop_index(op.f("ix_inventory_placements_listing_photo_pair_id"), table_name="inventory_placements")
    op.drop_index(op.f("ix_inventory_placements_inventory_item_id"), table_name="inventory_placements")
    op.drop_table("inventory_placements")
    op.drop_column("listing_photo_batches", "starting_slot")
    op.drop_column("listing_photo_batches", "storage_tray")
