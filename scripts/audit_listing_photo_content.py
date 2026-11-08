"""Read-only pixel-similarity audit; never treats similarity as owner approval."""
import asyncio,json,hashlib
from pathlib import Path
from io import BytesIO
from urllib.parse import urlsplit
from datetime import datetime,timezone
import httpx
from PIL import Image,ImageOps,ImageChops,ImageStat
from sqlalchemy import select
from automation_control.database import SessionLocal
from automation_control.models import ShopListing,ShopListingStatus,CatalogItem,EbayListing
from automation_control.channel_verification import linked_id
from automation_control.listing_quality import listing_photo_sources

async def run(output):
    sem=asyncio.Semaphore(6);cache={}
    async with httpx.AsyncClient(timeout=20,follow_redirects=False) as client:
        async def image(value):
            if value in cache:return cache[value]
            try:
                if value.startswith('https://'):
                    host=urlsplit(value).hostname or ''
                    # Only fetch eBay's public photo CDN. Other hosts need manual review.
                    if not (host=='i.ebayimg.com' or host.endswith('.ebayimg.com')):return None
                    async with sem:
                        async with client.stream('GET',value) as response:
                            response.raise_for_status();data=b''
                            async for chunk in response.aiter_bytes():
                                data+=chunk
                                if len(data)>12_000_000:return None
                elif value.startswith(('http://','//')):return None
                else:data=Path(value).read_bytes()
                with Image.open(BytesIO(data)) as original:
                    pixels=ImageOps.exif_transpose(original).convert('RGB').resize((128,128))
                result=(hashlib.sha256(data).hexdigest(),pixels)
            except Exception:result=None
            cache[value]=result
            return result
        with SessionLocal() as session:
            targets=[]
            for listing in session.scalars(select(ShopListing).where(ShopListing.status.in_([ShopListingStatus.ACTIVE,ShopListingStatus.DRAFT]))):
                item_id=linked_id(session,listing)
                remote=session.scalar(select(EbayListing).where(EbayListing.ebay_listing_id==item_id)) if item_id else None
                if not remote or remote.listing_status!='Active':continue
                item=session.get(CatalogItem,listing.catalog_item_id)
                local=[p.value for p in listing_photo_sources(session,listing,item) if p.is_own_photo] if item else []
                urls=(remote.raw_snapshot or {}).get('image_urls') or []
                targets.append((listing.id,listing.title,item_id,local,urls))
        async def check(target):
            lid,title,eid,local,urls=target
            result={'listing_id':lid,'title':title,'ebay_id':eid,'saved_count':len(local),'ebay_count':len(urls),'photos':[]}
            if not local or len(local)!=len(urls):result['status']='photo_count_or_source_needs_review'
            else:result['status']='visually_similar_not_confirmed'
            for i,(a,b) in enumerate(zip(local,urls)):
                left,right=await asyncio.gather(image(a),image(b))
                if left is None or right is None:
                    result['photos'].append({'position':i+1,'status':'unavailable'});result['status']='needs_review';continue
                score=round(100*(1-sum(ImageStat.Stat(ImageChops.difference(left[1],right[1])).mean)/(3*255)),2)
                state='identical_bytes' if left[0]==right[0] else 'visually_similar' if score>=98 else 'different_pixels'
                result['photos'].append({'position':i+1,'status':state,'similarity_percent':score})
                if state=='different_pixels':result['status']='needs_review'
            return result
        # Bounded batches keep the download queue and memory modest.
        results=[]
        for start in range(0,len(targets),12):results.extend(await asyncio.gather(*(check(t) for t in targets[start:start+12])))
        summary={key:sum(r['status']==key for r in results) for key in sorted({r['status'] for r in results})}
        Path(output).write_text(json.dumps({'checked_at':datetime.now(timezone.utc).isoformat(),'note':'Pixel similarity is triage, not confirmation of exact printing or condition. No listings changed.','summary':summary,'listings':results},indent=2))
        print(json.dumps({'checked':len(results),'summary':summary,'report':output}))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    asyncio.run(run(parser.parse_args().output))
