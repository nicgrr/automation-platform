"""Owner-approved Vaporeon/Charizard correction; dry-run unless --apply."""
import sys, uuid, json
from pathlib import Path
from sqlalchemy import select
from automation_control.database import SessionLocal
from automation_control.models import ShopListing, InventoryItem, CatalogItem, TcgCard, CardSet, ListingPhotoPair, AuditEvent
from automation_control.shop_listings_admin import _ebay_state_clauses
from automation_control.listing_quality import generated_ebay_title, generated_ebay_description
from automation_control.representative_backs import with_notice

def snapshot(obj):
    return {col.name: getattr(obj,col.name) for col in obj.__table__.columns}

def run(apply=False):
    changes=[]
    with SessionLocal() as s:
        for lid,expected in [('70a1a1c7-af1f-41f0-bb0a-c834df194a14','tcgp-226405'),('e67f025d-ae81-486a-9528-8bada0914de4','local-f9c57898be9384972ec48627')]:
            l=s.get(ShopListing,lid)
            assert s.scalar(select(ShopListing.id).where(ShopListing.id==lid,_ebay_state_clauses()['not_listed']))==lid, 'Listing no longer unpublished'
            assert l.catalog_item_id==expected, 'Identity changed; inspect before repeating'
            inv=s.get(InventoryItem,l.inventory_item_id)
            assert inv.catalog_item_id==expected
            c=s.get(TcgCard,expected); item=s.get(CatalogItem,expected)
            pairs=s.query(ListingPhotoPair).filter_by(matched_shop_listing_id=lid).all()
            before={'listing':snapshot(l),'inventory':snapshot(inv),'card':snapshot(c),'item':snapshot(item),'pairs':[snapshot(p) for p in pairs]}
            protected=(l.quantity,l.price,list(l.ebay_photo_paths),inv.quantity)
            if expected=='tcgp-226405':
                target='jp-sv8a-030'
                c=s.get(TcgCard,target);item=s.get(CatalogItem,target)
                assert c and c.language=='Japanese' and c.set_id=='jp-sv8a'
                l.catalog_item_id=inv.catalog_item_id=target
                inv.card_id=None
                name='Vaporeon'
            else:
                assert s.query(ShopListing).filter_by(catalog_item_id=expected).count()==1
                assert s.query(InventoryItem).filter_by(catalog_item_id=expected).count()==1
                setid='jp-svjl'
                if not s.get(CardSet,setid):
                    s.add(CardSet(id=setid,name='Battle Master Deck Terastal Charizard ex',code='svJL',game='pokemon',printed_total=21,total=21))
                    s.flush()
                c.set_id=setid;c.set_code='svJL';c.number='003/021'
                item.name=name='Radiant Charizard'
            cs=s.get(CardSet,c.set_id)
            l.title=f'{name} #{"030/187" if name=="Vaporeon" else c.number}'
            l.ebay_title=generated_ebay_title(l,item,c,cs,inv)
            if name=="Radiant Charizard":
                l.ebay_title="Radiant Charizard svJL 003/021 Battle Master Deck Holo Pokemon Japanese"
            l.ebay_description=with_notice(generated_ebay_description(l,item,c,cs,inv,has_own_photo=True),l.ebay_photo_paths)
            for p in pairs:
                p.proposed_catalog_item_id=l.catalog_item_id
                p.proposed_title=l.ebay_title;p.proposed_description=l.ebay_description
            assert protected==(l.quantity,l.price,list(l.ebay_photo_paths),inv.quantity)
            change={'before':before,'after':{'listing':snapshot(l),'inventory':snapshot(inv),'card':snapshot(c)},'evidence':'Owner approved printed photos; Charizard official reference https://www.pokemon-card.com/card-search/details.php/card/45803'}
            changes.append(change)
            s.add(AuditEvent(actor_type='user',actor_id='Codex (owner-approved 2026-11-06)',action='photo_confirm.correct_printing',resource_type='shop_listing',resource_id=lid,outcome='success',correlation_id=str(uuid.uuid4()),details=json.loads(json.dumps(change,default=str))))
            print(l.id,l.ebay_title)
        if apply:
            path=Path('backups/printing-corrections-20261106.json')
            with path.open('x') as f:json.dump(changes,f,default=str,indent=2)
            s.commit();print('Applied both corrections atomically')
        else:
            s.rollback();print('Dry run: no writes')
if __name__=='__main__':run('--apply' in sys.argv)
