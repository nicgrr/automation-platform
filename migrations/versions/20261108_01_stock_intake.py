"""Explicit photo intake intent and replay-safe source receipts."""
from alembic import op
import sqlalchemy as sa
revision = '20261108_01'
down_revision = '20261106_01'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('listing_photo_batches', sa.Column('intake_mode',sa.String(32),nullable=False,server_default='legacy'))
    op.create_table('stock_intake_receipts',
        sa.Column('pair_id',sa.String(36),sa.ForeignKey('listing_photo_pairs.id'),primary_key=True),
        sa.Column('inventory_item_id',sa.String(36),sa.ForeignKey('inventory_items.id'),nullable=False),
        sa.Column('shop_listing_id',sa.String(36),sa.ForeignKey('shop_listings.id'),nullable=False),
        sa.Column('mode',sa.String(32),nullable=False),
        sa.Column('quantity_added',sa.Integer(),nullable=False),
        sa.Column('previous_quantity',sa.Integer(),nullable=False),
        sa.Column('confirmed_by',sa.String(128),nullable=False),
        sa.Column('confirmed_at',sa.DateTime(timezone=True),nullable=False))

def downgrade():
    op.drop_table('stock_intake_receipts')
    op.drop_column('listing_photo_batches','intake_mode')
