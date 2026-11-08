"""Transactional quantity history, including direct SQL writes from sales channels."""
from sqlalchemy import text

TRIGGERS = [
"""CREATE TRIGGER IF NOT EXISTS stock_source_insert AFTER INSERT ON inventory_items BEGIN
 INSERT INTO inventory_quantity_events(inventory_item_id,delta,source,created_at)
 VALUES(NEW.id,NEW.quantity,'unassigned',CURRENT_TIMESTAMP); END""",
"""CREATE TRIGGER IF NOT EXISTS stock_source_update AFTER UPDATE OF quantity ON inventory_items
 WHEN NEW.quantity != OLD.quantity BEGIN
 INSERT INTO inventory_quantity_events(inventory_item_id,delta,source,created_at)
 VALUES(NEW.id,NEW.quantity-OLD.quantity,'unassigned',CURRENT_TIMESTAMP); END"""
]

def install(connection):
    if connection.dialect.name == 'sqlite':
        for statement in TRIGGERS:connection.execute(text(statement))


def tag_intake(session, inventory_id, pair_id, added):
    if not added:return
    row=session.execute(text('SELECT id,delta FROM inventory_quantity_events WHERE inventory_item_id=:i ORDER BY id DESC LIMIT 1'),{'i':inventory_id}).first()
    if not row or row.delta!=added:raise ValueError('Intake source did not match the quantity change; transaction cancelled.')
    session.execute(text("UPDATE inventory_quantity_events SET source='photo_batch',pair_id=:p WHERE id=:id"),{'p':pair_id,'id':row.id})


def balances(session,inventory_id):
    events=session.execute(text('SELECT id,delta,source,pair_id FROM inventory_quantity_events WHERE inventory_item_id=:i ORDER BY id'),{'i':inventory_id}).all()
    lots=[]
    for event in events:
        if event.delta>0:
            lots.append({'event':event.id,'source':event.source,'pair_id':event.pair_id,'added':event.delta,'remaining':event.delta})
        elif event.delta<0:
            needed=-event.delta
            # New photographed batches first, then other additions newest first.
            ordered=sorted(lots,key=lambda lot:(lot['source']=='photo_batch',lot['event']),reverse=True)
            for lot in ordered:
                used=min(lot['remaining'],needed);lot['remaining']-=used;needed-=used
                if not needed:break
    return lots


def panel(session,inventory_id):
    if not inventory_id:return ''
    from html import escape
    lots=balances(session,inventory_id)
    body="<details><summary>Stock by source</summary><p>Allocation rule: newest photographed copies first when stock reduces. Other additions/returns stay unassigned. This is an accounting allocation; it cannot identify which identical physical copy was picked.</p><ul>"
    for lot in lots:
        label={'baseline':'Existing stock at tracking start','unassigned':'Other additions / returns','photo_batch':'Photographed intake'}.get(lot['source'],lot['source'])
        if lot['pair_id']:label+=f" · {lot['pair_id'][:8]}"
        body+=f"<li>{escape(label)}: {lot['remaining']} remaining of {lot['added']}</li>"
    return body+'</ul></details>'
