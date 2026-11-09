"""Explicit price approval tied to the printing, condition and proposed price."""
from decimal import Decimal, ROUND_CEILING
from html import escape as e
import hashlib,json,uuid
from sqlalchemy import select
from .models import AuditEvent,TcgCard,StockIntakeReceipt


def fingerprint(session, listing):
    card=session.get(TcgCard,listing.catalog_item_id)
    identity={k:getattr(card,k,None) for k in ('set_id','set_code','number','language','rarity','variant','parallel')}
    return hashlib.sha256(json.dumps({'catalog':listing.catalog_item_id,'identity':identity,'price':str(Decimal(listing.price).quantize(Decimal('.01'))),
        'currency':listing.currency,'finish':listing.finish,'condition':listing.condition},sort_keys=True).encode()).hexdigest()


def current_review(session,listing):
    row=session.scalar(select(AuditEvent).where(AuditEvent.action=='listing.price_reviewed',AuditEvent.resource_id==listing.id).order_by(AuditEvent.occurred_at.desc()).limit(1))
    return row if row and row.details.get('fingerprint')==fingerprint(session,listing) else None


def required(session,listing):
    return session.scalar(select(StockIntakeReceipt.pair_id).where(StockIntakeReceipt.shop_listing_id==listing.id).limit(1)) is not None


def blockers(session,listing):
    if not required(session,listing):return []
    review=current_review(session,listing)
    if not review:return ['Review and approve the current price and printing before publishing this Scan Studio listing.']
    if review.details.get('disposition')!='single':return ['This card is marked for bulk consideration. Approve it as a single before publishing individually.']
    return []


def approve(session,listing,price,disposition,user,expected,round99=False):
    if expected!=fingerprint(session,listing):raise ValueError('Listing changed since you opened it. Reload and review again.')
    try:value=Decimal(price)
    except Exception:raise ValueError('Enter a valid AUD price.')
    if not value.is_finite() or value<1 or value>999999 or value!=value.quantize(Decimal('.01')):raise ValueError('Enter a price from $1.00 to $999,999.00 with at most two decimal places.')
    if listing.currency!='AUD' or disposition not in ('single','bulk'):raise ValueError('Choose single or bulk for an AUD listing.')
    if round99:value=(value-Decimal('.99')).to_integral_value(rounding=ROUND_CEILING)+Decimal('.99')
    if value>999999:raise ValueError('Rounded price exceeds the maximum. Enter a lower price.')
    old=str(listing.price);listing.price=value
    session.add(AuditEvent(actor_type='user',actor_id=user,action='listing.price_reviewed',resource_type='shop_listing',resource_id=listing.id,
        outcome='success',correlation_id=str(uuid.uuid4()),details={'fingerprint':fingerprint(session,listing),'disposition':disposition,'before':old,'price':str(value),'rule':'owner-reviewed'}))
    return value


def panel(session,listing):
    review=current_review(session,listing)
    status=f"Reviewed: {'single card' if review.details.get('disposition')=='single' else 'bulk consideration'}" if review else 'Price needs your review'
    suggestion='Single-card candidate' if listing.price>=5 else 'Bulk candidate' if listing.price>0 else 'Set a price to see the suggestion'
    body=f"<section class='panel price-review'><h2>Price approval</h2><p><strong>{status}</strong></p><p>{suggestion}. Your $5 threshold is a sorting guide, not a market valuation or profit calculation. Compare sold results and postage before deciding.</p>"
    body+=f"<form method='post' action='/shop-listings/{e(listing.id)}/review-price'><input type='hidden' name='expected' value='{fingerprint(session,listing)}'><label>Price (AUD)<input name='price' type='number' min='1' max='999999' step='0.01' value='{listing.price}' required></label><label>Sell as<select name='disposition'><option value='single'>Single card</option><option value='bulk'>Consider for bulk</option></select></label><label><input type='checkbox' name='round99' value='yes'>Round up to the next .99</label><label><input type='checkbox' name='confirmed' value='yes' required>I checked this printing and price.</label><button>Save reviewed price</button></form><p>This updates the saved proposal only. Bulk consideration does not create a bundle or withdraw an existing eBay listing.</p></section>"
    return body
