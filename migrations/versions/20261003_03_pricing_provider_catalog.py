"""add local provider catalogue and bulk price history

Revision ID: 20261003_03
Revises: 20261003_02
Create Date: 2026-10-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261003_03"
down_revision: Union[str, None] = "20261003_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _indexes(table: str, columns: list[str]) -> None:
    for column in columns:
        op.create_index(op.f(f"ix_{table}_{column}"), table, [column], unique=False)


def upgrade() -> None:
    op.create_table(
        "pricing_provider_categories",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("provider_category_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["provider_key"], ["pricing_providers.key"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_key", "provider_category_id", name="uq_pricing_provider_category"),
    )
    _indexes("pricing_provider_categories", ["provider_key", "provider_category_id", "name", "imported_at"])

    op.create_table(
        "pricing_provider_groups",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("category_id", sa.String(36), nullable=False),
        sa.Column("provider_group_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("abbreviation", sa.String(64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["provider_key"], ["pricing_providers.key"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["pricing_provider_categories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_key", "provider_group_id", name="uq_pricing_provider_group"),
    )
    _indexes("pricing_provider_groups", [
        "provider_key", "category_id", "provider_group_id", "name", "abbreviation", "imported_at",
    ])

    op.create_table(
        "pricing_provider_products",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("provider_product_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("clean_name", sa.String(512), nullable=True),
        sa.Column("product_type", sa.String(64), nullable=False),
        sa.Column("card_number", sa.String(64), nullable=True),
        sa.Column("rarity", sa.String(128), nullable=True),
        sa.Column("printing", sa.String(128), nullable=True),
        sa.Column("image_url", sa.String(1024), nullable=True),
        sa.Column("item_url", sa.String(1024), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["provider_key"], ["pricing_providers.key"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["group_id"], ["pricing_provider_groups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_key", "provider_product_id", name="uq_pricing_provider_product"),
    )
    _indexes("pricing_provider_products", [
        "provider_key", "group_id", "provider_product_id", "name", "product_type", "card_number",
        "rarity", "printing", "imported_at",
    ])

    op.create_table(
        "pricing_provider_quotes",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("product_id", sa.String(36), nullable=False),
        sa.Column("provider_product_id", sa.String(128), nullable=False),
        sa.Column("subtype", sa.String(128), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("low_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("mid_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("high_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("market_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("direct_low_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["provider_key"], ["pricing_providers.key"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["pricing_provider_products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_key", "provider_product_id", "subtype", "source_updated_at",
            name="uq_pricing_provider_quote_build",
        ),
    )
    _indexes("pricing_provider_quotes", [
        "provider_key", "product_id", "provider_product_id", "subtype", "source_updated_at", "observed_at",
    ])
    op.create_index(
        "ix_pricing_provider_quote_latest", "pricing_provider_quotes",
        ["provider_key", "provider_product_id", "observed_at"], unique=False,
    )


def downgrade() -> None:
    op.drop_table("pricing_provider_quotes")
    op.drop_table("pricing_provider_products")
    op.drop_table("pricing_provider_groups")
    op.drop_table("pricing_provider_categories")
