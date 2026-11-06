"""Refresh verified unpublished photo-batch titles; dry-run by default."""
import json,sys,uuid
from pathlib import Path
from sqlalchemy import select
from automation_control.database import SessionLocal
from automation_control.models import ShopListing,InventoryItem,CatalogItem,TcgCard,CardSet,ListingPhotoPair,AuditEvent
from automation_control.shop_listings_admin import _ebay_state_clauses
from automation_control.listing_quality import generated_ebay_title,generated_ebay_description
from automation_control.representative_backs import with_notice
from scripts.correct_printings_20261106 import snapshot
BATCHES={2:'3fd7a5b0-4f32-4e58-ad47-1bf5082c49f9',3:'d51f20d2-2e82-44fc-8681-c87033d1eca8'}
ROWS=[(2, 1, "N's Zoroark ex", '117/100', 'sv9'), (2, 2, "Team Rocket's Crobat ex", '131/098', 'sv10'), (2, 3, 'Slither Wing', '074/066', 'sv4K'), (2, 4, 'Maractus', '101/100', 'sv9'), (2, 5, 'Iron Thorns', '077/071', 'sv5M'), (2, 6, 'Cramorant', '072/066', 'sv5a'), (2, 7, 'Larvesta', '108/106', 'sv8'), (2, 8, 'Dragonair', '148/165', 'sv2a'), (2, 9, 'Magby', '068/066', 'sv4K'), (2, 10, 'Vaporeon', '030/187', 'sv8a'), (2, 11, 'Charizard ex', '006/165', 'sv2a'), (2, 12, "Team Rocket's Ariana", '119/098', 'sv10'), (2, 13, 'Umbreon V', '100/184', 's8b'), (2, 14, 'Flareon', '021/187', 'sv8a'), (2, 15, 'Furret', '110/100', 'sv9'), (2, 16, 'Mega Gardevoir ex', '042/063', 'M1S'), (2, 17, 'Ampharos', '088/083', 'm4'), (2, 18, 'Blastoise ex', '009/165', 'sv2a'), (2, 19, 'Snorlax', '181/165', 'sv2a'), (2, 20, "N's Reshiram", '109/100', 'sv9'), (2, 21, 'Charmander', '168/165', 'sv2a'), (2, 22, 'Charizard VSTAR', '014/172', 's12a'), (2, 23, 'Charizard V', '013/172', 's12a'), (2, 24, 'Articuno', '102/100', 'sv9'), (2, 25, 'Noctowl', '114/102', 'sv7'), (2, 26, 'Radiant Charizard', '003/021', 'svJL'), (2, 27, 'Zeraora', '109/102', 'sv7'), (2, 28, 'Minior', '070/062', 'sv3a'), (2, 29, 'Poliwhirl', '176/165', 'sv2a'), (2, 30, 'Scolipede', '140/086', 'sv11B'), (2, 31, 'Iron Jugulis', '078/066', 'sv4M'), (2, 32, 'Electivire', '180/172', 's12a'), (2, 33, 'Machamp VMAX', '072/172', 's12a'), (2, 34, 'Greavard', '087/078', 'sv1V'), (2, 35, 'Leafeon', '002/187', 'sv8a'), (2, 36, 'Zeraora', '109/102', 'sv7'), (2, 37, 'Dunsparce', '198/172', 's12a'), (2, 38, 'Ting-Lu ex', '359/190', 'sv4a'), (2, 39, 'Powerglass', '094/064', 'sv6a'), (2, 40, 'Alolan Dugtrio', '116/106', 'sv8'), (3, 4, 'Wattrel', '105/101', 'sv6'), (3, 5, 'Umbreon', '0603', 'CBB2C')]
def run(apply=False):
 with SessionLocal() as s:
  changes=[];seen=set()
  for batch,pos,name,number,code in ROWS:
   p=s.query(ListingPhotoPair).filter_by(batch_id=BATCHES[batch],position=pos).one()
   if not p.matched_shop_listing_id:continue
   l=s.get(ShopListing,p.matched_shop_listing_id)
   if l.id in seen:continue
   seen.add(l.id)
   if not s.scalar(select(ShopListing.id).where(ShopListing.id==l.id,_ebay_state_clauses()['not_listed'])):continue
   # Exact corrections were handled separately and must retain their reviewed titles.
   if l.id in ('70a1a1c7-af1f-41f0-bb0a-c834df194a14','e67f025d-ae81-486a-9528-8bada0914de4'):continue
   c=s.get(TcgCard,l.catalog_item_id);item=s.get(CatalogItem,l.catalog_item_id);inv=s.get(InventoryItem,l.inventory_item_id)
   assert c.number==number or c.number==number.split('/')[0], (l.id,c.number,number)
   before={'listing':snapshot(l),'card':snapshot(c),'item':snapshot(item)}
   protected=(l.price,l.quantity,list(l.ebay_photo_paths),inv.quantity,l.catalog_item_id)
   if l.catalog_item_id.startswith('local-'):
    # Do not change catalogue text used by another live or pending listing.
    total=s.query(ShopListing).filter_by(catalog_item_id=l.catalog_item_id).count()
    editable=s.query(ShopListing).filter(ShopListing.catalog_item_id==l.catalog_item_id,_ebay_state_clauses()['not_listed']).count()
    assert total==editable, 'Shared live catalogue row: review required'
    item.name=name;c.set_code=code
   l.title=f'{name} #{number}'
   cs=s.get(CardSet,c.set_id)
   if batch==3 and pos==5 and cs.name=="CBD2C":
    assert s.query(TcgCard).filter_by(set_id=cs.id).count()==1
    before["set"]=snapshot(cs)
    cs.name="CBB2C";cs.code="CBB2C"
   l.ebay_title=generated_ebay_title(l,item,c,cs,inv)
   l.ebay_description=with_notice(generated_ebay_description(l,item,c,cs,inv,has_own_photo=True),l.ebay_photo_paths)
   # Correct the recognizer's CBD2C typo without guessing a set name/denominator.
   if batch==3 and pos==5:
    l.ebay_description=l.ebay_description.replace('CBD2C','CBB2C')
   for pair in s.query(ListingPhotoPair).filter_by(matched_shop_listing_id=l.id):
    pair.proposed_title=l.ebay_title;pair.proposed_description=l.ebay_description
   assert protected==(l.price,l.quantity,list(l.ebay_photo_paths),inv.quantity,l.catalog_item_id)
   change={'before':before,'after':{'listing':snapshot(l),'card':snapshot(c),'item':snapshot(item)}}
   changes.append(change)
   s.add(AuditEvent(actor_type='user',actor_id='Codex (owner-approved continuation)',action='draft.correct_display_text',resource_type='shop_listing',resource_id=l.id,outcome='success',correlation_id=str(uuid.uuid4()),details=json.loads(json.dumps(change,default=str))))
   print(l.ebay_title)
  if apply:
   with Path('backups/multilingual-drafts-20261106.json').open('x') as f:json.dump(changes,f,default=str,indent=2)
   s.commit()
  else:s.rollback()
  print('Applied' if apply else 'Dry run',len(changes))
if __name__=='__main__':run('--apply' in sys.argv)
