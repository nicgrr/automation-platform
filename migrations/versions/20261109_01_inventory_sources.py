"""Quantity provenance across ORM and direct SQL sales paths."""
from alembic import op
import sqlalchemy as sa
revision='20261109_01'
down_revision='20261108_01'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('inventory_quantity_events',
        sa.Column('id',sa.Integer(),primary_key=True,autoincrement=True),
        sa.Column('inventory_item_id',sa.String(36),sa.ForeignKey('inventory_items.id',ondelete='CASCADE'),nullable=False),
        sa.Column('delta',sa.Integer(),nullable=False),
        sa.Column('source',sa.String(32),nullable=False),
        sa.Column('pair_id',sa.String(36)),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_inventory_quantity_events_inventory_item_id','inventory_quantity_events',['inventory_item_id'])
    op.execute("INSERT INTO inventory_quantity_events(inventory_item_id,delta,source,created_at) SELECT id,quantity,'baseline',CURRENT_TIMESTAMP FROM inventory_items WHERE quantity>0")
    from automation_control.stock_sources import install
    install(op.get_bind())

def downgrade():
    op.execute('DROP TRIGGER IF EXISTS stock_source_insert')
    op.execute('DROP TRIGGER IF EXISTS stock_source_update')
    op.drop_table('inventory_quantity_events')
