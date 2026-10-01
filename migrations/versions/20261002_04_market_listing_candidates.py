"""add purchase-candidate workflow to market listings

Revision ID: 20261002_04
Revises: 20261002_03
Create Date: 2026-10-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261002_04"
down_revision: Union[str, None] = "20261002_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


candidate_status = sa.Enum(
    "WATCHING", "CONTACT_SELLER", "OFFER_SENT", "COUNTER_OFFER", "ACCEPTED",
    "DECLINED", "PURCHASED", "EXPIRED", "SKIPPED",
    name="potentialpurchasestatus",
)


def upgrade() -> None:
    op.add_column("market_listings", sa.Column("candidate_status", candidate_status, nullable=True))
    op.add_column("market_listings", sa.Column("target_price_aud", sa.Numeric(12, 2), nullable=True))
    op.add_column("market_listings", sa.Column("max_price_aud", sa.Numeric(12, 2), nullable=True))
    op.add_column("market_listings", sa.Column("candidate_notes", sa.Text(), nullable=True))
    op.add_column("market_listings", sa.Column("candidate_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_market_listings_candidate_status", "market_listings", ["candidate_status"])


def downgrade() -> None:
    op.drop_index("ix_market_listings_candidate_status", table_name="market_listings")
    op.drop_column("market_listings", "candidate_updated_at")
    op.drop_column("market_listings", "candidate_notes")
    op.drop_column("market_listings", "max_price_aud")
    op.drop_column("market_listings", "target_price_aud")
    op.drop_column("market_listings", "candidate_status")
