"""Explicit, source-recorded stock intake; caller owns the transaction."""
import uuid
from decimal import Decimal
from sqlalchemy import select
from fastapi import HTTPException
from .models import (ListingPhotoBatch, ListingPhotoPairStatus, StockIntakeReceipt, InventoryItem,
    InventoryStatus, ShopListing, ShopListingStatus, CatalogItem, TcgCard, CardSet, CardVariant, AuditEvent)
from .listing_finishes import FINISHES

MODES = {'new_stock':'New stock','additional_copies':'Additional copies','photo_update':'Photo update'}

def is_studio(session, pair):
    batch = session.get(ListingPhotoBatch, pair.batch_id)
    return bool(batch and batch.intake_mode and batch.intake_mode != 'legacy')

def guard_legacy(session, pair):
    if is_studio(session, pair):
        raise HTTPException(409, f'Confirm this intake in Scan Studio: /scan-studio/cards/{pair.id}')

def confirm(session, pair, *, mode, catalog_id, finish, quantity, listing_id, user):
    from .product_photo_review import is_graded
    from .ebay_photo_batch import _link_pair_inventory, _refresh_batch_counts
    from .listing_quality import generated_ebay_title, generated_ebay_description
    from .representative_backs import attach_to_pair, with_notice
    receipt = session.get(StockIntakeReceipt, pair.id)
    if receipt:
        return session.get(ShopListing, receipt.shop_listing_id)
    if not is_studio(session, pair) or mode not in MODES:
        raise HTTPException(400, 'Choose a valid Scan Studio intake action.')
    if pair.status in (ListingPhotoPairStatus.PROCESSING, ListingPhotoPairStatus.APPLIED, ListingPhotoPairStatus.PUBLISHED):
        raise HTTPException(409, 'This photo is processing or has already been applied.')
    if is_graded(pair.extracted) or (pair.extracted or {}).get('item_kind') == 'collectible':
        raise HTTPException(409, 'This stock-intake workflow is for raw cards. Keep this item pending for collectible or slab review.')
    item, card = session.get(CatalogItem,catalog_id), session.get(TcgCard,catalog_id)
    if not item or not card or finish not in FINISHES:
        raise HTTPException(400, 'Confirm a catalogue card and its exact finish.')
    variant = CardVariant.HOLO if finish == 'cosmos_holo' else CardVariant(finish)
    listing = session.get(ShopListing,listing_id) if listing_id else None
    if listing_id and (not listing or listing.catalog_item_id != catalog_id):
        raise HTTPException(400, 'Choose a listing for this exact card.')
    if listing and listing.status not in (ShopListingStatus.ACTIVE,ShopListingStatus.DRAFT):
        raise HTTPException(409, 'Choose a current draft or active listing.')
    inventory = session.get(InventoryItem,listing.inventory_item_id) if listing and listing.inventory_item_id else None
    if mode == 'photo_update':
        if not listing or not inventory:
            raise HTTPException(400, 'Photo updates require an existing listing with stock.')
    else:
        if not 1 <= quantity <= 999:
            raise HTTPException(400, 'Enter the number of new physical copies, from 1 to 999.')
        if inventory is None:
            inventory = session.scalar(select(InventoryItem).where(InventoryItem.catalog_item_id==catalog_id,
                InventoryItem.variant==variant, InventoryItem.condition=='Near Mint'))
        current = list(session.scalars(select(ShopListing).where(ShopListing.catalog_item_id==catalog_id,
            ShopListing.status.in_([ShopListingStatus.ACTIVE,ShopListingStatus.DRAFT]), ShopListing.quantity>0)))
        matches = [l for l in current if (inventory and l.inventory_item_id==inventory.id) or l.finish==finish or not l.finish]
        if mode == 'new_stock' and ((inventory and inventory.quantity>0) or matches):
            raise HTTPException(409, 'This printing already has stock. Choose Additional copies or Photo update; nothing was added.')
        if mode == 'additional_copies' and inventory is None:
            raise HTTPException(409, 'No existing holding matches this printing. Choose New stock.')
        if listing is None and matches:
            raise HTTPException(409, 'Select the existing listing before adding copies; no duplicate listing was created.')
        if inventory and len([l for l in matches if l.inventory_item_id==inventory.id])>1:
            raise HTTPException(409, 'Multiple listings share this stock. Reconcile them before adding copies.')
    if inventory and (inventory.catalog_item_id!=catalog_id or inventory.variant!=variant):
        raise HTTPException(409, 'The chosen stock has a different printing. Review its identity first.')
    if listing and listing.finish and listing.finish!=finish:
        raise HTTPException(409, 'The chosen listing has a different finish.')
    if finish=='cosmos_holo' and inventory and (not listing or listing.finish!='cosmos_holo'):
        raise HTTPException(409, 'Confirm the existing Cosmos printing separately before combining stock.')
    previous = inventory.quantity if inventory else 0
    added = 0 if mode=='photo_update' else quantity
    if inventory is None:
        inventory=InventoryItem(catalog_item_id=catalog_id,variant=variant,condition='Near Mint',quantity=quantity,
            status=InventoryStatus.AVAILABLE,scan_image_path=pair.front_image_path)
        session.add(inventory);session.flush()
    elif added:
        inventory.quantity += added
        inventory.status = InventoryStatus.AVAILABLE
    if listing is None:
        listing=ShopListing(catalog_item_id=catalog_id,inventory_item_id=inventory.id,title=item.name,
            quantity=added,price=Decimal('0'),currency='AUD',status=ShopListingStatus.DRAFT,condition='Near Mint',finish=finish)
        session.add(listing);session.flush()
    elif added:
        listing.quantity += added
    attach_to_pair(pair, card.game, card.language, card.rarity)
    # Additional copies retain the existing offer's photos, copy and price.
    if mode!='additional_copies' or not listing.ebay_photo_paths:
        listing.ebay_photo_paths=[pair.front_image_path]+([pair.back_image_path] if pair.back_image_path else [])
    if not listing.ebay_title:
        card_set=session.get(CardSet,card.set_id) if card.set_id else None
        listing.ebay_title=generated_ebay_title(listing,item,card,card_set,inventory)
    if not listing.ebay_description:
        card_set=session.get(CardSet,card.set_id) if card.set_id else None
        listing.ebay_description=generated_ebay_description(listing,item,card,card_set,inventory,has_own_photo=True)
    listing.ebay_description=with_notice(listing.ebay_description,listing.ebay_photo_paths)
    pair.proposed_catalog_item_id=catalog_id;pair.matched_shop_listing_id=listing.id
    pair.status=ListingPhotoPairStatus.APPLIED;pair.issue=None
    _link_pair_inventory(session,pair,inventory)
    from .models import InventoryPlacement
    placement=session.scalar(select(InventoryPlacement).where(InventoryPlacement.listing_photo_pair_id==pair.id))
    if placement and added:
        placement.quantity=added
    elif placement and mode=="photo_update":
        session.delete(placement)  # A replacement photo is not a newly stored copy.
    session.add(StockIntakeReceipt(pair_id=pair.id,inventory_item_id=inventory.id,shop_listing_id=listing.id,
        mode=mode,quantity_added=added,previous_quantity=previous,confirmed_by=user))
    session.add(AuditEvent(actor_type='user',actor_id=user,action='stock_intake.confirm',resource_type='listing_photo_pair',resource_id=pair.id,
        outcome='success',correlation_id=str(uuid.uuid4()),details={'mode':mode,'quantity_added':added,'previous_quantity':previous,'listing_id':listing.id}))
    session.flush();_refresh_batch_counts(session,session.get(ListingPhotoBatch,pair.batch_id))
    return listing
