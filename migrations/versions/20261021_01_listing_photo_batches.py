"""add ordered front/back listing photo batches

Revision ID: 20261021_01
Revises: 20261019_01
Create Date: 2026-10-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261021_01"
down_revision: Union[str, None] = "20261019_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "listing_photo_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("total_pairs", sa.Integer(), nullable=False),
        sa.Column("processed_pairs", sa.Integer(), nullable=False),
        sa.Column("ready_pairs", sa.Integer(), nullable=False),
        sa.Column("review_pairs", sa.Integer(), nullable=False),
        sa.Column("failed_pairs", sa.Integer(), nullable=False),
        sa.Column("status", sa.Enum("RUNNING", "DONE", name="listingphotobatchstatus"), nullable=False),
        sa.Column("game_hint", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_by", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_listing_photo_batches_status"), "listing_photo_batches", ["status"], unique=False
    )
    op.create_table(
        "listing_photo_pairs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("front_image_path", sa.String(length=512), nullable=False),
        sa.Column("back_image_path", sa.String(length=512), nullable=True),
        sa.Column("extracted", sa.JSON(), nullable=False),
        sa.Column("matched_shop_listing_id", sa.String(length=36), nullable=True),
        sa.Column("proposed_catalog_item_id", sa.String(length=64), nullable=True),
        sa.Column("proposed_title", sa.String(length=80), nullable=True),
        sa.Column("proposed_description", sa.Text(), nullable=True),
        sa.Column("proposed_price", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("price_source", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum("PROCESSING", "READY", "NEEDS_REVIEW", "APPLIED", "FAILED", name="listingphotopairstatus"),
            nullable=False,
        ),
        sa.Column("issue", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["listing_photo_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matched_shop_listing_id"], ["shop_listings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["proposed_catalog_item_id"], ["catalog_items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "position", name="uq_listing_photo_pair_position"),
    )
    op.create_index(op.f("ix_listing_photo_pairs_batch_id"), "listing_photo_pairs", ["batch_id"], unique=False)
    op.create_index(
        op.f("ix_listing_photo_pairs_matched_shop_listing_id"),
        "listing_photo_pairs", ["matched_shop_listing_id"], unique=False,
    )
    op.create_index(
        op.f("ix_listing_photo_pairs_proposed_catalog_item_id"),
        "listing_photo_pairs", ["proposed_catalog_item_id"], unique=False,
    )
    op.create_index(op.f("ix_listing_photo_pairs_status"), "listing_photo_pairs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_listing_photo_pairs_status"), table_name="listing_photo_pairs")
    op.drop_index(op.f("ix_listing_photo_pairs_proposed_catalog_item_id"), table_name="listing_photo_pairs")
    op.drop_index(op.f("ix_listing_photo_pairs_matched_shop_listing_id"), table_name="listing_photo_pairs")
    op.drop_index(op.f("ix_listing_photo_pairs_batch_id"), table_name="listing_photo_pairs")
    op.drop_table("listing_photo_pairs")
    op.drop_index(op.f("ix_listing_photo_batches_status"), table_name="listing_photo_batches")
    op.drop_table("listing_photo_batches")
