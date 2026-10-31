"""remembered collectible brands, series and characters for onboarding

Revision ID: 20261031_02
Revises: 20261031_01
Create Date: 2026-10-31

Seeds the list from every brand/series/character combination already in
collectible_products, with whitespace cleaned so near-duplicates such as
"Vegetable Series" and "Vegetable Series " collapse into one entry.
"""

from typing import Sequence, Union
import re
import uuid

from alembic import op
import sqlalchemy as sa


revision: str = "20261031_02"
down_revision: Union[str, None] = "20261031_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def upgrade() -> None:
    op.create_table(
        "collectible_reference",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("brand", sa.String(128), nullable=False),
        sa.Column("series", sa.String(128), nullable=False),
        sa.Column("character", sa.String(128), nullable=False),
        sa.Column("brand_key", sa.String(128), nullable=False),
        sa.Column("series_key", sa.String(128), nullable=False),
        sa.Column("character_key", sa.String(128), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("brand_key", "series_key", "character_key"),
    )
    op.create_index("ix_collectible_reference_brand_key", "collectible_reference", ["brand_key"])
    rows = op.get_bind().execute(sa.text(
        "select brand, series, character, is_secret from collectible_products where trim(coalesce(brand, '')) <> ''"
    )).all()
    seen, seed = set(), []
    for brand, series, character, secret in rows:
        brand, series, character = _clean(brand), _clean(series), _clean(character)
        for parts in ((brand, "", ""), (brand, series, ""), (brand, series, character)):
            if parts != (brand, "", "") and not parts[1]:
                continue
            k = tuple(p.casefold() for p in parts)
            if k in seen:
                continue
            seen.add(k)
            seed.append({"id": str(uuid.uuid4()), "brand": parts[0], "series": parts[1], "character": parts[2],
                         "brand_key": k[0], "series_key": k[1], "character_key": k[2],
                         "is_secret": bool(secret) and bool(parts[2]), "source": "catalogue"})
    if seed:
        op.get_bind().execute(sa.text(
            "insert into collectible_reference (id, brand, series, character, brand_key, series_key, character_key,"
            " is_secret, source, created_at) values (:id, :brand, :series, :character, :brand_key, :series_key,"
            " :character_key, :is_secret, :source, CURRENT_TIMESTAMP)"), seed)


def downgrade() -> None:
    op.drop_index("ix_collectible_reference_brand_key", table_name="collectible_reference")
    op.drop_table("collectible_reference")
