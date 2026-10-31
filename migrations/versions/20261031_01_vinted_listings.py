"""record items posted on Vinted against the stock they reserve

Revision ID: 20261031_01
Revises: 20261030_01
Create Date: 2026-10-31
"""

from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


revision: str = "20261031_01"
down_revision: Union[str, None] = "20261030_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vinted_listings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("inventory_item_id", sa.String(36), sa.ForeignKey("inventory_items.id", ondelete="SET NULL")),
        sa.Column("catalog_item_id", sa.String(64), sa.ForeignKey("catalog_items.id")),
        sa.Column("vinted_item_id", sa.String(32), nullable=False),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.Enum("ACTIVE", "SOLD", "REMOVED", name="vintedlistingstatus"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stock_reserved_at", sa.DateTime(timezone=True)),
        sa.Column("stock_released_at", sa.DateTime(timezone=True)),
        sa.Column("sold_at", sa.DateTime(timezone=True)),
        sa.Column("sale_id", sa.String(36), sa.ForeignKey("sales.id")),
        sa.Column("sale_email_id", sa.String(256), unique=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_vinted_listings_vinted_item_id", "vinted_listings", ["vinted_item_id"], unique=True)
    op.create_index("ix_vinted_listings_inventory_item_id", "vinted_listings", ["inventory_item_id"])
    op.create_index("ix_vinted_listings_catalog_item_id", "vinted_listings", ["catalog_item_id"])
    op.create_index("ix_vinted_listings_status", "vinted_listings", ["status"])
    # Sales recorded from Vinted need their marketplace, as Whatnot has.
    marketplaces = sa.table("marketplaces", sa.column("id", sa.String), sa.column("name", sa.String))
    bind = op.get_bind()
    if not bind.execute(sa.text("select 1 from marketplaces where lower(name) = 'vinted'")).first():
        op.bulk_insert(marketplaces, [{"id": str(uuid.uuid4()), "name": "Vinted"}])


def downgrade() -> None:
    op.drop_index("ix_vinted_listings_status", table_name="vinted_listings")
    op.drop_index("ix_vinted_listings_catalog_item_id", table_name="vinted_listings")
    op.drop_index("ix_vinted_listings_inventory_item_id", table_name="vinted_listings")
    op.drop_index("ix_vinted_listings_vinted_item_id", table_name="vinted_listings")
    op.drop_table("vinted_listings")
    op.execute("delete from marketplaces where lower(name) = 'vinted'")
