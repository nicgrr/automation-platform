"""Owner-approved live description-only revision. Default dry-run; --apply --limit 1 first."""
import argparse,asyncio,copy,json,uuid
from pathlib import Path
from dataclasses import asdict
from automation_control.database import SessionLocal
from automation_control.config import get_settings
from automation_control.models import ShopListing,EbayListingLink,EbayListingLinkStatus,EbayListing,AuditEvent
from automation_control.ebay_oauth import ebay_services,valid_user_access_token
from automation_control.adapters.ebay_write import EbayWriteAdapter
from automation_control.adapters.ebay_trading import EbayTradingReadAdapter
from automation_control.representative_backs import is_representative,with_notice,NOTICE
# eBay's official sell_inventory_v1_oas3.json / EbayOfferDetailsWithId.
FIELDS={'availableQuantity','categoryId','charity','extendedProducerResponsibility','hideBuyerDetails','includeCatalogProductDetails','listingDescription','listingDuration','listingPolicies','listingStartDate','lotSize','merchantLocationKey','pricingSummary','quantityLimitPerBuyer','regulatory','secondaryCategoryId','storeCategoryNames','tax'}
def revise_text(value,paths):
 return with_notice(value or '',paths).replace('Photos show the actual card you will receive','The front photo shows the actual card you will receive')
def without_description(offer):
 return {k:v for k,v in offer.items() if k!='listingDescription'}
async def run(apply=False,limit=0):
 cfg=get_settings();done=0;checked=0
 with SessionLocal() as s:
  cipher,oauth=ebay_services(cfg);token=await valid_user_access_token(s,cipher,oauth)
  a=EbayWriteAdapter(token,environment=cfg.ebay_env)
  r=EbayTradingReadAdapter(token,cfg.ebay_client_id,environment=cfg.ebay_env,marketplace_id=cfg.ebay_marketplace_id)
  try:
   ids=[l.id for l in s.query(ShopListing).order_by(ShopListing.created_at) if any(is_representative(p) for p in l.ebay_photo_paths or [])]
   for lid in ids:
    s.expire_all()
    l=s.get(ShopListing,lid)
    link=s.query(EbayListingLink).filter_by(shop_listing_id=lid,status=EbayListingLinkStatus.ACTIVE).first()
    if not link or not link.ebay_offer_id:continue
    offer=await a.get_offer(link.ebay_offer_id)
    assert offer['sku']==link.sku==lid
    assert offer['listing']['listingId']==link.ebay_listing_id
    if offer.get('status')!='PUBLISHED' or offer['listing'].get('listingStatus')!='ACTIVE':continue
    checked+=1
    desc=revise_text(offer.get('listingDescription'),l.ebay_photo_paths)
    if desc==offer.get('listingDescription'):
     print('Already current',link.ebay_listing_id,flush=True);continue
    live=await r.get_item_details(link.ebay_listing_id)
    assert live.description==offer.get('listingDescription'), 'Live description differs; inspect before overwrite'
    payload={k:copy.deepcopy(v) for k,v in offer.items() if k in FIELDS}
    payload['listingDescription']=desc
    assert {k:v for k,v in payload.items() if k!='listingDescription'}=={k:v for k,v in offer.items() if k in FIELDS and k!='listingDescription'}
    record={'listing_id':lid,'ebay_listing_id':link.ebay_listing_id,'before_offer':offer,'before_live':asdict(live),'before_local':l.ebay_description,'proposed_description':desc}
    if apply:
     path=Path(f'backups/shared-back-{link.ebay_listing_id}-20261106.json')
     with path.open('x') as f:json.dump(record,f,indent=2)
     # Refuse if another edit arrived between reading and writing.
     assert await a.get_offer(link.ebay_offer_id)==offer
     await a.update_offer(link.ebay_offer_id,payload)
     after=await a.get_offer(link.ebay_offer_id)
     after_live=await r.get_item_details(link.ebay_listing_id)
     record.update(after_offer=after,after_live=asdict(after_live))
     path.write_text(json.dumps(record,indent=2))
     assert without_description(after)==without_description(offer), 'Unexpected non-description change; halt'
     assert after['listingDescription']==desc and after_live.description==desc
     assert after_live.picture_urls==live.picture_urls
     s.refresh(l)
     l.ebay_description=revise_text(l.ebay_description,l.ebay_photo_paths)
     cached=s.query(EbayListing).filter_by(ebay_listing_id=link.ebay_listing_id).first()
     if cached:
      snap=dict(cached.raw_snapshot or {});snap['description']=desc;cached.raw_snapshot=snap
     s.add(AuditEvent(actor_type='user',actor_id='Codex (approved shared-back descriptions)',action='ebay.revise_shared_back_notice',resource_type='shop_listing',resource_id=lid,outcome='success',correlation_id=str(uuid.uuid4()),details={'ebay_listing_id':link.ebay_listing_id,'snapshot':str(path),'verified_live':True,'fields_changed':['listingDescription']}))
     s.commit()
    done+=1;print('Verified' if apply else 'Would revise',link.ebay_listing_id,l.title,flush=True)
    if limit and done>=limit:break
   print('Revisions',done,'checked',checked,flush=True)
  finally:
   await a.aclose();await r.client.aclose()
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');p.add_argument('--limit',type=int,default=0);args=p.parse_args();asyncio.run(run(args.apply,args.limit))
