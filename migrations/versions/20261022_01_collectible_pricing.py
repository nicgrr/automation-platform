"""add category-routed collectible pricing and explicit purchase shipping

Revision ID: 20261022_01
Revises: 20261021_01
Create Date: 2026-10-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261022_01"
down_revision: Union[str, None] = "20261021_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("purchase_lots", sa.Column("shipping_cost", sa.Numeric(10, 2), nullable=True))
    op.add_column("inventory_items", sa.Column("purchase_cost_basis", sa.Numeric(10, 2), nullable=True))
    op.add_column("inventory_items", sa.Column("allocated_shipping_cost", sa.Numeric(10, 2), nullable=True))
    op.create_table(
        "pricing_provider_rules",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("catalog_item_type", sa.String(32), nullable=False),
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("access_mode", sa.String(16), nullable=False),
        sa.Column("allow_active", sa.Boolean(), nullable=False),
        sa.Column("allow_sold", sa.Boolean(), nullable=False),
        sa.Column("refresh_seconds", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("limitation", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["provider_key"], ["pricing_providers.key"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("catalog_item_type", "provider_key", name="uq_pricing_provider_rule"),
    )
    op.create_index("ix_pricing_provider_rules_catalog_item_type", "pricing_provider_rules", ["catalog_item_type"])
    op.create_index("ix_pricing_provider_rules_provider_key", "pricing_provider_rules", ["provider_key"])
    op.create_index("ix_pricing_provider_rules_enabled", "pricing_provider_rules", ["enabled"])

    # Provider metadata contains no credentials and does not activate access.
    op.execute(sa.text("""
        INSERT OR IGNORE INTO pricing_providers
          (key, display_name, enabled, capabilities, configuration,
           commercial_use_status, health_status, created_at, updated_at)
        VALUES
          ('ebay', 'eBay Official', 0, '[\"active_listings\", \"product_lookup\", \"quota\"]', '{}', 'unverified', 'UNKNOWN', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
          ('depop', 'Depop', 0, '[\"active_listings\"]', '{}', 'manual_only', 'UNKNOWN', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
          ('vinted', 'Vinted', 0, '[\"active_listings\"]', '{}', 'manual_only', 'UNKNOWN', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """))
    op.execute(sa.text("""
        INSERT INTO pricing_provider_rules
          (id, catalog_item_type, provider_key, enabled, access_mode,
           allow_active, allow_sold, refresh_seconds, priority, limitation,
           created_at, updated_at)
        VALUES
          ('collectible-ebay', 'COLLECTIBLE', 'ebay', 1, 'automatic', 1, 0, 86400, 10,
           'Official active listings only; verified sold access is not configured.', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
          ('collectible-depop', 'COLLECTIBLE', 'depop', 1, 'manual', 1, 0, 86400, 20,
           'Public competitor-pricing API access is unavailable; save observed listings manually.', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
          ('collectible-vinted', 'COLLECTIBLE', 'vinted', 1, 'manual', 1, 0, 86400, 30,
           'Vinted Pro does not provide general competitor-price search; save observed listings manually.', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """))


def downgrade() -> None:
    op.drop_index("ix_pricing_provider_rules_enabled", table_name="pricing_provider_rules")
    op.drop_index("ix_pricing_provider_rules_provider_key", table_name="pricing_provider_rules")
    op.drop_index("ix_pricing_provider_rules_catalog_item_type", table_name="pricing_provider_rules")
    op.drop_table("pricing_provider_rules")
    op.drop_column("inventory_items", "allocated_shipping_cost")
    op.drop_column("inventory_items", "purchase_cost_basis")
    op.drop_column("purchase_lots", "shipping_cost")
