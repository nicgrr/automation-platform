"""preserve the applied video-game schema revision in migration history

Revision ID: 20261003_01
Revises: 20261002_09
Create Date: 2026-10-03

The application feature that originally used these tables is not active, but
production was already migrated to this revision. Alembic revisions are
immutable history: retaining the file keeps restores, upgrades and startup
revision checks reproducible without re-enabling any routes or model code.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261003_01"
down_revision: Union[str, None] = "20261002_09"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "game_products" not in tables:
        op.create_table(
            "game_products",
            sa.Column("catalog_item_id", sa.String(length=64), nullable=False),
            sa.Column("title", sa.String(length=256), nullable=False),
            sa.Column("platform", sa.String(length=64), nullable=False),
            sa.Column("region", sa.String(length=32), nullable=True),
            sa.Column("rating", sa.String(length=32), nullable=True),
            sa.Column("edition", sa.String(length=128), nullable=True),
            sa.ForeignKeyConstraint(["catalog_item_id"], ["catalog_items.id"]),
            sa.PrimaryKeyConstraint("catalog_item_id"),
        )
    game_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("game_products")}
    if op.f("ix_game_products_title") not in game_indexes:
        op.create_index(op.f("ix_game_products_title"), "game_products", ["title"], unique=False)
    if op.f("ix_game_products_platform") not in game_indexes:
        op.create_index(op.f("ix_game_products_platform"), "game_products", ["platform"], unique=False)

    if "captured_games" not in tables:
        op.create_table(
            "captured_games",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("image_path", sa.String(length=512), nullable=False),
            sa.Column("title", sa.String(length=256), nullable=True),
            sa.Column("platform", sa.String(length=64), nullable=True),
            sa.Column("region", sa.String(length=32), nullable=True),
            sa.Column("rating", sa.String(length=32), nullable=True),
            sa.Column("edition", sa.String(length=128), nullable=True),
            sa.Column("ai_raw_response", sa.JSON(), nullable=True),
            sa.Column("status", sa.Enum("PENDING_REVIEW", "REVIEWED", "REJECTED", name="cardcapturestatus"), nullable=False),
            sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("catalog_item_id", sa.String(length=64), nullable=True),
            sa.ForeignKeyConstraint(["catalog_item_id"], ["catalog_items.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    captured_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("captured_games")}
    if op.f("ix_captured_games_status") not in captured_indexes:
        op.create_index(op.f("ix_captured_games_status"), "captured_games", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_captured_games_status"), table_name="captured_games")
    op.drop_table("captured_games")
    op.drop_index(op.f("ix_game_products_platform"), table_name="game_products")
    op.drop_index(op.f("ix_game_products_title"), table_name="game_products")
    op.drop_table("game_products")
