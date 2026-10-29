import enum
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Index, Integer, JSON, LargeBinary, Numeric, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from .database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RecommendationStatus(str, enum.Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ToolExecutionStatus(str, enum.Enum):
    REQUESTED = "requested"
    DENIED = "denied"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ScheduleKind(str, enum.Enum):
    INTERVAL = "interval"
    CRON = "cron"


class CardCaptureStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"
    REVIEWED = "reviewed"
    REJECTED = "rejected"


class PricingStatus(str, enum.Enum):
    NOT_PRICED = "not_priced"
    PENDING_PRICE_REVIEW = "pending_price_review"
    PRICE_APPROVED = "price_approved"
    PRICE_REJECTED = "price_rejected"


class PricingSourceKind(str, enum.Enum):
    """What a normalized pricing observation actually represents."""

    BULK_MARKET = "bulk_market"
    ACTIVE_LISTING = "active_listing"
    SOLD_LISTING = "sold_listing"
    RETAIL_ASK = "retail_ask"
    BUYLIST = "buylist"
    MANUAL = "manual"


class PricingQueueStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PricingSyncStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class PricingProviderHealth(str, enum.Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class CapturePromotionStatus(str, enum.Enum):
    """Result of handing a priced photo capture to the normal sell pipeline."""

    PROMOTED = "promoted"
    NEEDS_REVIEW = "needs_review"
    # The physical copy is safely recorded in Inventory and its local
    # ShopListing quantity is correct, but an already-live eBay listing must
    # still be revised.  This is deliberately not NEEDS_REVIEW: retrying the
    # capture must never count the same physical copy twice.
    REVISION_REQUIRED = "revision_required"


class CaptureBatchStatus(str, enum.Enum):
    RUNNING = "running"
    DONE = "done"


class ListingPhotoBatchStatus(str, enum.Enum):
    RUNNING = "running"
    DONE = "done"


class ListingPhotoPairStatus(str, enum.Enum):
    PROCESSING = "processing"
    READY = "ready"
    NEEDS_REVIEW = "needs_review"
    APPLIED = "applied"
    PUBLISHED = "published"
    FAILED = "failed"


class ListingBuildStatus(str, enum.Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"
    FAILED = "failed"


class CardVariant(str, enum.Enum):
    """Which printing of a card this copy is. Not reliably detectable from a
    flatbed scan (holo foil mostly reads as glare), so the operator sets it
    per batch and can override it per card at confirmation time.

    POKEBALL_PATTERN / MASTERBALL_PATTERN are a distinct chase-variant
    treatment in Scarlet & Violet-era sets (a literal Poke Ball / Master
    Ball graphic pattern over the card, not just reverse-holo foil) --
    real, separately valuable printings, not a sub-case of reverse holo.
    TCGPlayer's pricing API has no price key for either (they're generally
    listed as their own product, not a sub-variant of the base card), so
    they never get auto-suggested by _VARIANT_BY_TCG_KEY -- only ever set
    by a human who can see the pattern, same as the rest of this comment's
    first sentence already implies for every variant here.
    """

    NORMAL = "normal"
    REVERSE_HOLO = "reverse_holo"
    HOLO = "holo"
    POKEBALL_PATTERN = "pokeball_pattern"
    MASTERBALL_PATTERN = "masterball_pattern"


class CatalogItemType(str, enum.Enum):
    """What kind of thing a CatalogItem is -- decides which detail table
    (TcgCard / SealedProduct / CollectibleProduct) has its other row."""

    TCG_CARD = "tcg_card"
    SEALED_PRODUCT = "sealed_product"
    COLLECTIBLE = "collectible"
    GRADED_CARD = "graded_card"
    ACCESSORY = "accessory"
    APPAREL = "apparel"
    OTHER = "other"


class InventoryStatus(str, enum.Enum):
    """Where one owned item sits in the sell pipeline. Distinct from
    CardVariant (which printing) and from having a quantity at all --
    an item can be AVAILABLE with quantity 3."""

    PERSONAL_COLLECTION = "personal_collection"
    AVAILABLE = "available"
    LISTED = "listed"
    RESERVED = "reserved"
    SOLD = "sold"
    TRADE = "trade"
    GIVEAWAY = "giveaway"
    DAMAGED = "damaged"
    RETURNED = "returned"


class PurchaseLotStatus(str, enum.Enum):
    EVALUATING = "evaluating"
    OFFER_SENT = "offer_sent"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    PURCHASED = "purchased"
    REFUNDED = "refunded"


class PotentialPurchaseStatus(str, enum.Enum):
    WATCHING = "watching"
    CONTACT_SELLER = "contact_seller"
    OFFER_SENT = "offer_sent"
    COUNTER_OFFER = "counter_offer"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    PURCHASED = "purchased"
    EXPIRED = "expired"
    SKIPPED = "skipped"


class SupplierStatus(str, enum.Enum):
    RESEARCHING = "researching"
    APPLY = "apply"
    APPLICATION_SENT = "application_sent"
    APPROVED = "approved"
    DECLINED = "declined"
    ACTIVE = "active"
    PAUSED = "paused"


class GoalKind(str, enum.Enum):
    """MILESTONE: hit target_value once (e.g. "first $1,000 month").
    CUMULATIVE: current_value keeps accruing toward target_value (e.g.
    "100 Whatnot followers")."""

    MILESTONE = "milestone"
    CUMULATIVE = "cumulative"


class WhatnotShowItemOutcome(str, enum.Enum):
    PENDING = "pending"
    SOLD = "sold"
    UNSOLD = "unsold"
    GIVEAWAY = "giveaway"


class WhatnotShowStatus(str, enum.Enum):
    DRAFT = "draft"
    LIVE = "live"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class SaleStatus(str, enum.Enum):
    COMPLETED = "completed"
    VOIDED = "voided"


class ScanSessionStatus(str, enum.Enum):
    RUNNING = "running"
    DONE = "done"


class RetailerTrustStatus(str, enum.Enum):
    TRUSTED = "trusted"
    UNVERIFIED = "unverified"
    BLOCKED = "blocked"


class RetailerProductStatus(str, enum.Enum):
    WATCH = "watch"
    BUY = "buy"
    HIGH_PRIORITY = "high_priority"
    PREORDER = "preorder"
    PURCHASED = "purchased"
    SOLD_OUT = "sold_out"
    SKIP = "skip"
    PRICE_TOO_HIGH = "price_too_high"
    WAIT_FOR_RESTOCK = "wait_for_restock"


class RetailerStockStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    IN_STOCK = "in_stock"
    PREORDER = "preorder"
    OUT_OF_STOCK = "out_of_stock"


class RetailerPurchaseStatus(str, enum.Enum):
    RECORDED = "recorded"
    REFUNDED = "refunded"
    VOID = "void"


class RetailerFundEntryKind(str, enum.Enum):
    OPENING_BALANCE = "opening_balance"
    CONTRIBUTION = "contribution"
    ADJUSTMENT = "adjustment"
    PURCHASE = "purchase"
    REFUND = "refund"


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_type: Mapped[str] = mapped_column(String(32), index=True)
    actor_id: Mapped[str] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(128), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(32), index=True)
    correlation_id: Mapped[str] = mapped_column(String(36), index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlatformConfig(Base):
    __tablename__ = "platform_config"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    requested_by: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(128), index=True)
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[ApprovalStatus] = mapped_column(Enum(ApprovalStatus), default=ApprovalStatus.PENDING)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(Text)


class ToolPermission(Base):
    __tablename__ = "tool_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    principal: Mapped[str] = mapped_column(String(128), index=True)
    tool_name: Mapped[str] = mapped_column(String(128), index=True)
    allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(128), unique=True)
    job_type: Mapped[str] = mapped_column(String(128))
    kind: Mapped[ScheduleKind] = mapped_column(Enum(ScheduleKind))
    expression: Mapped[str] = mapped_column(String(128))
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    schedule_id: Mapped[str | None] = mapped_column(ForeignKey("schedules.id"))
    job_type: Mapped[str] = mapped_column(String(128), index=True)
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING, index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_summary: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)

    schedule: Mapped[Schedule | None] = relationship()


class EbayListing(Base):
    __tablename__ = "ebay_listings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ebay_listing_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    sku: Mapped[str | None] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(512))
    currency: Mapped[str] = mapped_column(String(3))
    current_price_minor: Mapped[int] = mapped_column(Integer)
    listing_status: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[int | None] = mapped_column(Integer)
    marketplace: Mapped[str | None] = mapped_column(String(32), index=True)
    raw_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class OAuthState(Base):
    __tablename__ = "oauth_states"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EbayCredential(Base):
    __tablename__ = "ebay_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    environment: Mapped[str] = mapped_column(String(16), default="sandbox")
    encrypted_access_token: Mapped[bytes] = mapped_column(LargeBinary)
    encrypted_refresh_token: Mapped[bytes] = mapped_column(LargeBinary)
    access_token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Separate cursor from last_successful_sync_at above, which belongs to
    # the unrelated read-mirror sync (GET /api/ebay/listings). This one
    # tracks scripts/import_ebay_orders.py's own "orders modified since"
    # position -- one field per purpose, not a shared one two jobs fight
    # over.
    last_order_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EbayListingLinkStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHING = "publishing"
    ACTIVE = "active"
    ENDING = "ending"
    ENDED = "ended"
    FAILED = "failed"


class EbayExportItemStatus(str, enum.Enum):
    """Where one SKU is in the File Exchange hand-off.

    Downloading a CSV is not the same thing as eBay accepting it, so the
    ledger deliberately keeps those as two separate states.  A failed or
    cancelled row is eligible for a later export; pending and confirmed
    rows are not.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EbayExportBatch(Base):
    """One CSV downloaded from the eBay bulk-export review page."""

    __tablename__ = "ebay_export_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    filename: Mapped[str] = mapped_column(String(256))
    row_count: Mapped[int] = mapped_column(Integer)
    selected_count: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    result_filename: Mapped[str | None] = mapped_column(String(256))
    result_imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EbayExportItem(Base):
    """Per-SKU history for an :class:`EbayExportBatch`.

    This is intentionally not a flag on ShopListing: variation-parent SKUs
    are synthetic and have no ShopListing row, and preserving batch history
    makes a partial eBay upload result diagnosable instead of collapsing it
    into one mutable boolean.
    """

    __tablename__ = "ebay_export_items"
    __table_args__ = (UniqueConstraint("batch_id", "sku"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    batch_id: Mapped[str] = mapped_column(ForeignKey("ebay_export_batches.id"), index=True)
    sku: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str | None] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(32), default="single")
    status: Mapped[EbayExportItemStatus] = mapped_column(
        Enum(EbayExportItemStatus), default=EbayExportItemStatus.PENDING, index=True
    )
    ebay_listing_id: Mapped[str | None] = mapped_column(String(128), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    exported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    batch: Mapped[EbayExportBatch] = relationship()


class EbayListingLink(Base):
    """The write-side source of truth for "is this ShopListing also live on
    eBay, and as what" -- deliberately separate from EbayListing (below),
    which is a read-only mirror wholesale-upserted by polling eBay's own
    listing data and has no concept of which local ShopListing a row
    corresponds to. Conflating the two would mean a failed poll cycle
    could blank fields the sync logic depends on.

    sku is always == shop_listing_id, stored explicitly (rather than only
    implied by the FK) so eBay API payloads/responses can be matched
    without a join back to shop_listings.

    status moves draft -> publishing (row created, eBay calls in flight)
    -> active (published) or failed, then active -> ending -> ended when
    the listing sells out or is withdrawn. Persisted as PUBLISHING before
    the first eBay call, not after, so a crash mid-sequence (see
    ebay_publish.py) leaves a diagnosable partial record instead of
    nothing.
    """

    __tablename__ = "ebay_listing_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_listing_id: Mapped[str] = mapped_column(ForeignKey("shop_listings.id"), unique=True, index=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(32))
    ebay_offer_id: Mapped[str | None] = mapped_column(String(128))
    ebay_listing_id: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[EbayListingLinkStatus] = mapped_column(Enum(EbayListingLinkStatus), default=EbayListingLinkStatus.DRAFT, index=True)
    last_pushed_quantity: Mapped[int | None] = mapped_column(Integer)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("approvals.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class EbayImportedOrder(Base):
    """One row per eBay order line scripts/import_ebay_orders.py has ever
    seen, kept as an append-only idempotency ledger -- eBay's getOrders
    returns a rolling window rather than "new since last call" cleanly,
    so without this a re-run could decrement the matching ShopListing's
    stock twice for the same real-world sale.

    applied_at is None the moment a row is written (seen, not yet acted
    on) and set only once the matching ShopListing has actually been
    decremented -- the poller's idempotency check is "does a row with
    this (ebay_order_id, sku) pair already have applied_at set", not
    merely "does a row exist", so a row written just before a crash gets
    retried rather than silently treated as done.

    The natural key is the (ebay_order_id, sku) *pair*, not ebay_order_id
    alone -- a multi-item order has one row per line. This was originally
    `unique=True` on ebay_order_id by itself, which made every line after
    the first on the same order raise an IntegrityError; see
    scripts/migrations/migrate_fix_ebay_imported_orders_unique.py for the
    real production incident this caused and the index-only fix.
    """

    __tablename__ = "ebay_imported_orders"
    # Production uses a unique index (created by the incident-repair
    # migration), not a table-level UNIQUE constraint. They enforce the
    # same business rule in SQLite, but declaring the real shape prevents
    # every Alembic check from proposing a pointless table rebuild.
    __table_args__ = (Index("uq_ebay_imported_orders_order_sku", "ebay_order_id", "sku", unique=True),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ebay_order_id: Mapped[str] = mapped_column(String(128), index=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    marketplace_id: Mapped[str] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shop_listing_id: Mapped[str | None] = mapped_column(ForeignKey("shop_listings.id"))
    raw_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    # The operator's own "done with this" marker for /ebay-sales -- set by
    # marking an order complete or discarding it from the active view.
    # Never touched by the sync poller: that idempotency check is
    # applied_at alone, so this can't affect whether stock gets decremented.
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EbayBulkPickManifest(Base):
    """A fixed, resumable packing list for a legacy eBay bulk-card line.

    Older bulk listings have no per-card SKU, so the order importer cannot
    decrement a specific InventoryItem.  A manifest records the exact cards
    selected to fulfil that line and keeps the physical-picking step separate
    from the later stock/eBay quantity update.
    """

    __tablename__ = "ebay_bulk_pick_manifests"
    __table_args__ = (UniqueConstraint("ebay_order_id", "line_item_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ebay_order_id: Mapped[str] = mapped_column(String(128), index=True)
    line_item_id: Mapped[str] = mapped_column(String(256))
    ebay_listing_id: Mapped[str | None] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(512))
    requested_quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="picking", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EbayBulkPickItem(Base):
    """One physical card reserved for an EbayBulkPickManifest."""

    __tablename__ = "ebay_bulk_pick_items"
    __table_args__ = (UniqueConstraint("manifest_id", "inventory_item_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    manifest_id: Mapped[str] = mapped_column(
        ForeignKey("ebay_bulk_pick_manifests.id", ondelete="CASCADE"), index=True
    )
    inventory_item_id: Mapped[str] = mapped_column(ForeignKey("inventory_items.id"), index=True)
    shop_listing_id: Mapped[str] = mapped_column(ForeignKey("shop_listings.id"), index=True)
    # Variation parent ItemID.  ReviseInventoryStatus identifies the child
    # using this parent id plus shop_listing_id (the variation SKU).
    ebay_listing_id: Mapped[str] = mapped_column(String(128), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    # What the person physically handling the card reported. Usually this
    # matches InventoryItem.variant; kept separately so a packing list can
    # preserve a spoken correction without silently rewriting an aggregated
    # inventory row or the live eBay variation before final confirmation.
    reported_variant: Mapped[str | None] = mapped_column(String(64))
    picked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remote_quantity_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    manifest: Mapped[EbayBulkPickManifest] = relationship()


class EbayCategoryMapping(Base):
    """Which eBay category a card belongs to, keyed loosely enough to cover
    most of the catalogue from a handful of seeded rows rather than one
    row per card: (marketplace, item_type, game). ebay_publish.py falls
    back from the full key to (marketplace, item_type, None) when no
    game-specific row exists, so a single "tcg_card, game=None" row can
    cover a brand-new game with zero config. Seeded once by
    scripts/seed_ebay_categories.py, not hardcoded, matching this
    project's existing rates/rules-as-rows-not-constants convention
    (see ShippingRate, MarketplaceFeeRule)."""

    __tablename__ = "ebay_category_mappings"
    __table_args__ = (UniqueConstraint("marketplace_id", "catalog_item_type", "game"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    marketplace_id: Mapped[str] = mapped_column(String(32))
    catalog_item_type: Mapped[str] = mapped_column(String(32))
    game: Mapped[str | None] = mapped_column(String(32))
    ebay_category_id: Mapped[str] = mapped_column(String(32))


class MarketObservation(Base):
    __tablename__ = "market_observations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    listing_id: Mapped[str | None] = mapped_column(ForeignKey("ebay_listings.id"), index=True)
    query: Mapped[str] = mapped_column(String(512), index=True)
    source_item_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(512))
    currency: Mapped[str] = mapped_column(String(3))
    price_minor: Mapped[int] = mapped_column(Integer)
    shipping_minor: Mapped[int | None] = mapped_column(Integer)
    condition: Mapped[str | None] = mapped_column(String(128))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    raw_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


class PriceRecommendation(Base):
    __tablename__ = "price_recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    listing_id: Mapped[str] = mapped_column(ForeignKey("ebay_listings.id"), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    current_price_minor: Mapped[int] = mapped_column(Integer)
    recommended_price_minor: Mapped[int] = mapped_column(Integer)
    rationale: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[RecommendationStatus] = mapped_column(
        Enum(RecommendationStatus), default=RecommendationStatus.PROPOSED, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ToolExecution(Base):
    __tablename__ = "tool_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    correlation_id: Mapped[str] = mapped_column(String(36), index=True)
    principal: Mapped[str] = mapped_column(String(128), index=True)
    tool_name: Mapped[str] = mapped_column(String(128), index=True)
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[ToolExecutionStatus] = mapped_column(
        Enum(ToolExecutionStatus), default=ToolExecutionStatus.REQUESTED, index=True
    )
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("approvals.id"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_summary: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)


class CapturedCard(Base):
    __tablename__ = "captured_cards"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    front_image_path: Mapped[str] = mapped_column(String(512))
    back_image_path: Mapped[str | None] = mapped_column(String(512))
    character: Mapped[str | None] = mapped_column(String(256))
    set_name: Mapped[str | None] = mapped_column(String(256))
    card_number: Mapped[str | None] = mapped_column(String(64))
    rarity: Mapped[str | None] = mapped_column(String(128))
    language: Mapped[str | None] = mapped_column(String(64))
    graded: Mapped[str | None] = mapped_column(String(128))
    # Condition belongs to the photographed physical copy, not the catalogue
    # card.  Keeping it on the capture lets the reviewer decide it once and
    # carry the same value through Inventory and the listing handoff instead
    # of silently labelling every card Near Mint.
    condition: Mapped[str | None] = mapped_column(String(64))
    ai_raw_response: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[CardCaptureStatus] = mapped_column(Enum(CardCaptureStatus), default=CardCaptureStatus.PENDING_REVIEW, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(128))

    suggested_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    price_source: Mapped[str | None] = mapped_column(String(64))
    price_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    price_lookup_error: Mapped[str | None] = mapped_column(String(512))
    approved_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    # The physical printing is required before Inventory handoff. It cannot
    # be inferred safely from an ex/VMAX name or a flat photo: some catalogue
    # cards have normal, holo and reverse-holo copies with different stock.
    variant: Mapped[CardVariant | None] = mapped_column(Enum(CardVariant))
    bundle_id: Mapped[str | None] = mapped_column(String(36), index=True)
    pricing_status: Mapped[PricingStatus] = mapped_column(Enum(PricingStatus), default=PricingStatus.NOT_PRICED, index=True)


class CapturePromotion(Base):
    """Idempotency and diagnostics for capture -> Inventory -> ShopListing."""

    __tablename__ = "capture_promotions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    captured_card_id: Mapped[str] = mapped_column(
        ForeignKey("captured_cards.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[CapturePromotionStatus] = mapped_column(Enum(CapturePromotionStatus), index=True)
    catalog_item_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_items.id"), index=True)
    inventory_item_id: Mapped[str | None] = mapped_column(ForeignKey("inventory_items.id"), index=True)
    shop_listing_id: Mapped[str | None] = mapped_column(ForeignKey("shop_listings.id"), index=True)
    issue: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CaptureBatch(Base):
    """Tracks a bulk-capture upload that's processed in the background --
    lets the browser get an immediate response and check back on progress
    later instead of holding the request open for the whole batch."""

    __tablename__ = "capture_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    total_photos: Mapped[int] = mapped_column(Integer, default=0)
    processed_photos: Mapped[int] = mapped_column(Integer, default=0)
    cards_created: Mapped[int] = mapped_column(Integer, default=0)
    failed_photos: Mapped[int] = mapped_column(Integer, default=0)
    failure_details: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[CaptureBatchStatus] = mapped_column(Enum(CaptureBatchStatus), default=CaptureBatchStatus.RUNNING, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_by: Mapped[str | None] = mapped_column(String(128))


class ListingPhotoBatch(Base):
    """One ordered front/back upload used to repair existing listings.

    Recognition runs in the background and only prepares CRM draft changes;
    no row in this workflow is itself permission to revise eBay.
    """

    __tablename__ = "listing_photo_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    total_pairs: Mapped[int] = mapped_column(Integer, default=0)
    processed_pairs: Mapped[int] = mapped_column(Integer, default=0)
    ready_pairs: Mapped[int] = mapped_column(Integer, default=0)
    review_pairs: Mapped[int] = mapped_column(Integer, default=0)
    failed_pairs: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[ListingPhotoBatchStatus] = mapped_column(
        Enum(ListingPhotoBatchStatus), default=ListingPhotoBatchStatus.RUNNING, index=True
    )
    game_hint: Mapped[str | None] = mapped_column(String(64))
    storage_tray: Mapped[str | None] = mapped_column(String(32))
    starting_slot: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_by: Mapped[str | None] = mapped_column(String(128))


class ListingPhotoPair(Base):
    """A staged pair and its reviewable listing-repair proposal."""

    __tablename__ = "listing_photo_pairs"
    __table_args__ = (UniqueConstraint("batch_id", "position", name="uq_listing_photo_pair_position"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("listing_photo_batches.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    front_image_path: Mapped[str] = mapped_column(String(512))
    back_image_path: Mapped[str | None] = mapped_column(String(512))
    extracted: Mapped[dict] = mapped_column(JSON, default=dict)
    matched_shop_listing_id: Mapped[str | None] = mapped_column(
        ForeignKey("shop_listings.id", ondelete="SET NULL"), index=True
    )
    proposed_catalog_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("catalog_items.id", ondelete="SET NULL"), index=True
    )
    proposed_title: Mapped[str | None] = mapped_column(String(80))
    proposed_description: Mapped[str | None] = mapped_column(Text)
    proposed_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    price_source: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[ListingPhotoPairStatus] = mapped_column(
        Enum(ListingPhotoPairStatus), default=ListingPhotoPairStatus.PROCESSING, index=True
    )
    issue: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class InventoryPlacement(Base):
    """One photographed physical card in one stable tray slot.

    InventoryItem intentionally aggregates identical copies, so its legacy
    storage_location_id cannot say where two separate copies live. A placement
    keeps the physical photo pair and slot distinct while optionally linking
    back to the aggregate inventory row after recognition succeeds.
    """

    __tablename__ = "inventory_placements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    inventory_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="SET NULL"), index=True
    )
    listing_photo_pair_id: Mapped[str] = mapped_column(
        ForeignKey("listing_photo_pairs.id", ondelete="CASCADE"), unique=True, index=True
    )
    storage_location_id: Mapped[str] = mapped_column(
        ForeignKey("storage_locations.id"), index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[str | None] = mapped_column(String(128))


class PendingListing(Base):
    """A listing built from priced, approved CapturedCard rows (see
    listing_pipeline/ingest_db.py + render.py), staged here for human review
    before it's ever sent to eBay. status moves draft -> approved (a human
    clicked Approve on /listings/review, which also creates/decides the
    Approval row referenced by approval_id) -> published (the Sell API
    write in listing_pipeline/publish.py succeeded) or failed."""

    __tablename__ = "pending_listings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    custom_label: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(256))
    description_html: Mapped[str] = mapped_column(Text)
    list_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    floor_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    category_id: Mapped[str] = mapped_column(String(32))
    condition_id: Mapped[str] = mapped_column(String(32))
    image_urls: Mapped[list] = mapped_column(JSON, default=list)
    card_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[ListingBuildStatus] = mapped_column(Enum(ListingBuildStatus), default=ListingBuildStatus.DRAFT, index=True)
    ebay_listing_id: Mapped[str | None] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("approvals.id"))


class CardSet(Base):
    """A Pokemon TCG set, cached from pokemontcg.io. Reference data for the
    scan-ingest pipeline (automation_control/scan_ingest/), which asks the
    operator which set they're scanning and then only has to resolve each
    card's number *within* that set.

    The upstream set id ("xy11") is a stable natural key, so it's the PK
    directly rather than carrying a separate UUID.
    """

    __tablename__ = "card_sets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    code: Mapped[str | None] = mapped_column(String(32), index=True)
    series: Mapped[str | None] = mapped_column(String(128))
    release_date: Mapped[str | None] = mapped_column(String(32))
    printed_total: Mapped[int | None] = mapped_column(Integer)
    total: Mapped[int | None] = mapped_column(Integer)
    cached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set artwork from pokemontcg.io, stored as upstream URLs rather than
    # downloaded: they're small, the browser caches them, and the set grid
    # is the one view where a recognisable logo does more for scanning a
    # list than any amount of text.
    logo_url: Mapped[str | None] = mapped_column(String(512))
    symbol_url: Mapped[str | None] = mapped_column(String(512))
    # Same free-text keys as TcgCard.game. Every set was Pokemon until
    # One Piece and Dragon Ball were cached from TCGplayer (see
    # scan_ingest/tcgplayer_catalog.py) -- it's what lets the scan service
    # match those games across their whole catalogue rather than one set,
    # and what /inventory splits its sections by.
    game: Mapped[str] = mapped_column(String(32), default="pokemon", server_default="pokemon", index=True)


class CatalogCard(Base):
    """One card's reference data within a cached set -- what the physical
    scan gets matched *against*. Named CatalogCard rather than Card to stay
    unambiguous next to CapturedCard (the AI-vision capture flow's rows,
    which are actual owned photographs rather than catalog reference data).

    `phash` is precomputed at cache time from the downloaded reference
    image, so identification (scan_ingest/identify.py) is a pure in-memory
    Hamming-distance comparison against only this set's ~100-250 cards.

    `raw_prices` holds whatever pricing blob (tcgplayer/cardmarket) came
    bundled in the same set-fetch response -- captured at cache time
    because it costs no extra API call, not because it's trusted. See
    scan_ingest/pricing.py for the caveats and the swappable interface this
    feeds.
    """

    __tablename__ = "catalog_cards"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    set_id: Mapped[str] = mapped_column(ForeignKey("card_sets.id"), index=True)
    number: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(256))
    rarity: Mapped[str | None] = mapped_column(String(128))
    artist: Mapped[str | None] = mapped_column(String(256))
    supertype: Mapped[str | None] = mapped_column(String(64))
    image_url: Mapped[str | None] = mapped_column(String(512))
    local_image_path: Mapped[str | None] = mapped_column(String(512))
    phash: Mapped[str | None] = mapped_column(String(64), index=True)
    # Hash of just the artwork window (see scan_ingest/identify.ART_REGION).
    # A reverse holo's foil covers everything but that window, so this is the
    # signal that finds those printings; the whole-card phash above still
    # covers layouts the window doesn't fit, like full-art cards.
    art_phash: Mapped[str | None] = mapped_column(String(64), index=True)
    raw_prices: Mapped[dict | None] = mapped_column(JSON)

    card_set: Mapped[CardSet] = relationship()


class CatalogItem(Base):
    """The generic "what is it" record -- Module 1 of the collectibles
    platform (see ARCHITECTURE.md). Every inventory item, purchase-lot
    line, and potential purchase eventually points at one of these,
    regardless of whether it's a TCG card, a sealed booster box, or a
    Sonny Angel. Detail lives in exactly one matching type-specific table
    (TcgCard / SealedProduct / CollectibleProduct), keyed by this row's id.

    A CatalogCard row is migrated into this as item_type=TCG_CARD reusing
    the *same* id -- so inventory_items.card_id, foil_labels.card_id, and
    everything else already pointing at a CatalogCard.id keeps resolving
    without being rewritten. See scripts/migrations/migrate_add_catalog_items.py.
    """

    __tablename__ = "catalog_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    item_type: Mapped[CatalogItemType] = mapped_column(Enum(CatalogItemType), index=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    image_url: Mapped[str | None] = mapped_column(String(512))
    # "automated" means a configured/provider source is expected to price
    # this exact product. "manual_only" means no trustworthy automated
    # source covers this printing; health checks must not report that as a
    # broken/stale job, while the UI can still invite an attributed manual
    # price. Never infer an English equivalent for a foreign printing.
    pricing_policy: Mapped[str] = mapped_column(
        String(16), default="automated", server_default="automated"
    )
    pricing_policy_reason: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TcgCard(Base):
    """TCG-card-specific detail for a CatalogItem with item_type=TCG_CARD.
    `game` is what makes this generic across Pokémon/One Piece/future TCGs
    rather than Pokémon-specific like CatalogCard is."""

    __tablename__ = "tcg_cards"

    catalog_item_id: Mapped[str] = mapped_column(ForeignKey("catalog_items.id"), primary_key=True)
    game: Mapped[str] = mapped_column(String(32), index=True)
    set_id: Mapped[str | None] = mapped_column(String(32), index=True)
    set_code: Mapped[str | None] = mapped_column(String(32))
    number: Mapped[str | None] = mapped_column(String(32))
    character: Mapped[str | None] = mapped_column(String(256))
    rarity: Mapped[str | None] = mapped_column(String(128))
    variant: Mapped[str | None] = mapped_column(String(64))
    parallel: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(32), default="English")
    release_date: Mapped[str | None] = mapped_column(String(32))
    artist: Mapped[str | None] = mapped_column(String(256))

    catalog_item: Mapped[CatalogItem] = relationship()


class SealedProduct(Base):
    """Sealed-product detail for a CatalogItem -- booster packs/boxes,
    displays, cases, ETBs. See Module 11 (sealed case economics) for why
    units_per_display/displays_per_case matter: they're what a case-vs-box
    breakeven calculation is built from."""

    __tablename__ = "sealed_products"

    catalog_item_id: Mapped[str] = mapped_column(ForeignKey("catalog_items.id"), primary_key=True)
    brand: Mapped[str | None] = mapped_column(String(128))
    game: Mapped[str | None] = mapped_column(String(32))
    set_id: Mapped[str | None] = mapped_column(String(32))
    product_type: Mapped[str | None] = mapped_column(String(64))
    units_per_display: Mapped[int | None] = mapped_column(Integer)
    displays_per_case: Mapped[int | None] = mapped_column(Integer)
    release_date: Mapped[str | None] = mapped_column(String(32))
    rrp: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    catalog_item: Mapped[CatalogItem] = relationship()


class CollectibleProduct(Base):
    """Non-TCG collectible detail for a CatalogItem -- Sonny Angel, Smiski,
    blind boxes, figures. `is_secret` is its own column rather than folded
    into `variant` because "is this the chase figure" is a distinct
    question from "which colourway" and drives its own pricing."""

    __tablename__ = "collectible_products"

    catalog_item_id: Mapped[str] = mapped_column(ForeignKey("catalog_items.id"), primary_key=True)
    brand: Mapped[str | None] = mapped_column(String(128))
    series: Mapped[str | None] = mapped_column(String(128))
    character: Mapped[str | None] = mapped_column(String(128))
    variant: Mapped[str | None] = mapped_column(String(128))
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    blind_box_series: Mapped[str | None] = mapped_column(String(128))
    retail_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    catalog_item: Mapped[CatalogItem] = relationship()


class AccessoryProduct(Base):
    """Card-supply detail for a CatalogItem -- sleeves, toploaders, deck
    boxes, binders, playmats. Its own table rather than reusing
    CollectibleProduct: a supply item has no character/series/is_secret
    (there's no chase-figure equivalent for a box of sleeves), and
    pack_size (e.g. "100ct", "9-Pocket") is a supply-specific concept
    CollectibleProduct has no column for."""

    __tablename__ = "accessory_products"

    catalog_item_id: Mapped[str] = mapped_column(ForeignKey("catalog_items.id"), primary_key=True)
    brand: Mapped[str | None] = mapped_column(String(128))
    product_type: Mapped[str | None] = mapped_column(String(128))
    pack_size: Mapped[str | None] = mapped_column(String(64))
    color: Mapped[str | None] = mapped_column(String(64))
    retail_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    catalog_item: Mapped[CatalogItem] = relationship()


class CapturedAccessory(Base):
    """A photo-captured card-supply item (sleeves, toploaders, deck boxes,
    ...) awaiting human confirmation before it becomes a real CatalogItem +
    AccessoryProduct. Mirrors CapturedCollectible's PENDING_REVIEW pattern
    exactly -- see that class's docstring for why."""

    __tablename__ = "captured_accessories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    image_path: Mapped[str] = mapped_column(String(512))
    brand: Mapped[str | None] = mapped_column(String(128))
    product_type: Mapped[str | None] = mapped_column(String(128))
    pack_size: Mapped[str | None] = mapped_column(String(64))
    color: Mapped[str | None] = mapped_column(String(64))
    ai_raw_response: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[CardCaptureStatus] = mapped_column(Enum(CardCaptureStatus), default=CardCaptureStatus.PENDING_REVIEW, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    catalog_item_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_items.id"))


class CapturedCollectible(Base):
    """A photo-captured collectible (Sonny Angel/Smiski/blind box) awaiting
    human confirmation before it becomes a real CatalogItem +
    CollectibleProduct -- Module 19. Mirrors CapturedCard's PENDING_REVIEW
    pattern exactly: AI extraction (collectible_recognition.py) never
    writes to the catalogue directly, only here, and confirming is what
    actually creates the catalogue rows. Reuses CardCaptureStatus rather
    than a duplicate enum -- the three states mean the same thing."""

    __tablename__ = "captured_collectibles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    image_path: Mapped[str] = mapped_column(String(512))
    brand: Mapped[str | None] = mapped_column(String(128))
    series: Mapped[str | None] = mapped_column(String(128))
    character: Mapped[str | None] = mapped_column(String(128))
    variant: Mapped[str | None] = mapped_column(String(128))
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    blind_box_series: Mapped[str | None] = mapped_column(String(128))
    ai_raw_response: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[CardCaptureStatus] = mapped_column(Enum(CardCaptureStatus), default=CardCaptureStatus.PENDING_REVIEW, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    catalog_item_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_items.id"))


class StorageLocation(Base):
    """Where a physical item actually sits -- Binder A, Bulk Box 1, Display
    Cabinet. Its own table rather than a free-text column so it can be
    renamed in one place and eventually carry a QR/barcode label."""

    __tablename__ = "storage_locations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[str | None] = mapped_column(String(64))


class BuyThresholdConfig(Base):
    """Configurable green/yellow/red acquisition-percentage bands (Module 3)
    -- deliberately a labelled row, not a single hardcoded triple, since
    different categories reasonably want different bands (bulk commons vs.
    graded singles). `is_default` marks which one the buying calculator and
    purchase-lot pages fall back to when nothing more specific is picked.
    """

    __tablename__ = "buy_threshold_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    label: Mapped[str] = mapped_column(String(128))
    green_max_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    yellow_max_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PurchaseLot(Base):
    """One negotiation/purchase of a group of items from one seller (Module
    3) -- "Facebook Marketplace Collection, seller asking $1,000". Lines
    live in PurchaseLotItem; this row holds the deal-level numbers."""

    __tablename__ = "purchase_lots"
    __table_args__ = (
        UniqueConstraint("retailer_id", "external_order_number", name="uq_purchase_lot_retailer_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source: Mapped[str] = mapped_column(String(256))
    seller: Mapped[str | None] = mapped_column(String(256))
    asking_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    target_buy_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("55"))
    offered_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    # NULL means genuinely unknown. It must never be silently treated as
    # free shipping when inventory cost is allocated.
    shipping_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    status: Mapped[PurchaseLotStatus] = mapped_column(Enum(PurchaseLotStatus), default=PurchaseLotStatus.EVALUATING, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    retailer_id: Mapped[str | None] = mapped_column(ForeignKey("retailers.id"), index=True)
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id"), index=True)
    external_order_number: Mapped[str | None] = mapped_column(String(256), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    inventory_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PurchaseLotItem(Base):
    """One line within a PurchaseLot. `market_value` is a snapshot at
    evaluation time, kept even if the live market price moves later --
    "what we thought it was worth when we made the offer" is the number
    the offer decision was actually based on."""

    __tablename__ = "purchase_lot_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    purchase_lot_id: Mapped[str] = mapped_column(ForeignKey("purchase_lots.id"), index=True)
    catalog_item_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_items.id"), index=True)
    description: Mapped[str] = mapped_column(String(256))
    market_value: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    supplier_product_id: Mapped[str | None] = mapped_column(ForeignKey("supplier_products.id"), index=True)
    condition: Mapped[str] = mapped_column(String(64), default="Sealed")
    quoted_unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    allocated_total_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    allocated_unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    retailer_purchase_id: Mapped[str | None] = mapped_column(
        ForeignKey("retailer_purchases.id"), unique=True, index=True
    )
    inventory_item_id: Mapped[str | None] = mapped_column(ForeignKey("inventory_items.id"), index=True)

    purchase_lot: Mapped[PurchaseLot] = relationship()


class PotentialPurchase(Base):
    """An item or lot being evaluated but not yet owned (Module 4) -- the
    buy watchlist/pipeline. Distinct from PurchaseLot: this is
    pre-negotiation ("watching", "contacted"); a PurchaseLot is one already
    being formally offered on. Tracked loosely via `purchase_lot_id` rather
    than a hard state-machine transition once an offer actually goes out.
    """

    __tablename__ = "potential_purchases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    description: Mapped[str] = mapped_column(String(256))
    seller: Mapped[str | None] = mapped_column(String(256))
    source: Mapped[str | None] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(String(512))
    asking_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    market_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    max_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    confidence: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[PotentialPurchaseStatus] = mapped_column(Enum(PotentialPurchaseStatus), default=PotentialPurchaseStatus.WATCHING, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    purchase_lot_id: Mapped[str | None] = mapped_column(ForeignKey("purchase_lots.id"))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Marketplace(Base):
    __tablename__ = "marketplaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(64), unique=True)


class MarketplaceFeeRule(Base):
    """A time-boxed fee rule, not a single mutable number -- marketplace fee
    structures change, and a "0% commission weekend" promotion is a new row
    with its own effective dates, not a code change. The rule in effect for
    a sale is whichever row's [effective_from, effective_to) window
    contains the sale date; `category=None` applies to every category.
    """

    __tablename__ = "marketplace_fee_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    marketplace_id: Mapped[str] = mapped_column(ForeignKey("marketplaces.id"), index=True)
    category: Mapped[str | None] = mapped_column(String(64))
    commission_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    processing_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    fixed_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    gst_treatment: Mapped[str | None] = mapped_column(String(32))
    promotion_label: Mapped[str | None] = mapped_column(String(128))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    marketplace: Mapped[Marketplace] = relationship()


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    display_name: Mapped[str] = mapped_column(String(256))
    email: Mapped[str | None] = mapped_column(String(256), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(64))
    platform_handles: Mapped[dict] = mapped_column(JSON, default=dict)
    segment: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Sale(Base):
    """One sale event/order, possibly bundling several SaleItems. Fee and
    shipping numbers are captured here at sale time even though
    MarketplaceFeeRule could reconstruct them, because a rule can be
    superseded later and the sale record should keep saying what was
    actually true when the sale happened."""

    __tablename__ = "sales"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    submission_key: Mapped[str | None] = mapped_column(String(36), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(32), default="manual", index=True)
    status: Mapped[SaleStatus] = mapped_column(Enum(SaleStatus), default=SaleStatus.COMPLETED, index=True)
    marketplace_id: Mapped[str | None] = mapped_column(ForeignKey("marketplaces.id"), index=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id"), index=True)
    sold_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    fees_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    shipping_revenue: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    shipping_cost: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    packaging_cost: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    notes: Mapped[str | None] = mapped_column(Text)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    marketplace: Mapped[Marketplace | None] = relationship()
    customer: Mapped[Customer | None] = relationship()


class SaleItem(Base):
    __tablename__ = "sale_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id"), index=True)
    # A sale is permanent business history even if its source inventory row
    # is later cleaned up.  Keep the line and only detach the optional link.
    inventory_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="SET NULL"), index=True
    )
    description: Mapped[str] = mapped_column(String(256))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    cost_basis: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    sale: Mapped[Sale] = relationship()


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    contact: Mapped[str | None] = mapped_column(String(256))
    website: Mapped[str | None] = mapped_column(String(512))
    account_status: Mapped[SupplierStatus] = mapped_column(Enum(SupplierStatus), default=SupplierStatus.RESEARCHING, index=True)
    categories: Mapped[str | None] = mapped_column(String(256))
    minimum_order: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    wholesale_discount_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SupplierProduct(Base):
    __tablename__ = "supplier_products"
    __table_args__ = (
        UniqueConstraint("supplier_id", "catalog_item_id", name="uq_supplier_product_catalog"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), index=True)
    catalog_item_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_items.id"), index=True)
    description: Mapped[str] = mapped_column(String(256))
    cost: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    min_order_qty: Mapped[int | None] = mapped_column(Integer)

    supplier: Mapped[Supplier] = relationship()


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    label: Mapped[str] = mapped_column(String(256))
    kind: Mapped[GoalKind] = mapped_column(Enum(GoalKind), default=GoalKind.MILESTONE)
    target_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("1"))
    current_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    achieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReleaseCalendarEntry(Base):
    __tablename__ = "release_calendar"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    catalog_item_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_items.id"), index=True)
    product_name: Mapped[str] = mapped_column(String(256))
    announcement_date: Mapped[str | None] = mapped_column(String(32))
    preorder_date: Mapped[str | None] = mapped_column(String(32))
    supplier_deadline: Mapped[str | None] = mapped_column(String(32))
    release_date: Mapped[str | None] = mapped_column(String(32), index=True)
    ordered_quantity: Mapped[int | None] = mapped_column(Integer)
    wholesale_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    retail_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    notes: Mapped[str | None] = mapped_column(Text)


class WhatnotShow(Base):
    """One Whatnot stream -- Module 7's actual show tracking, distinct from
    MarketplaceFeeRule which just holds Whatnot's fee structure. Rollup
    numbers (revenue, profit, sell-through) are computed from
    WhatnotShowItem at read time rather than stored here, so they're never
    stale relative to the items."""

    __tablename__ = "whatnot_shows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(256))
    show_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    category: Mapped[str] = mapped_column(String(64), default="TCG")
    status: Mapped[WhatnotShowStatus] = mapped_column(
        Enum(WhatnotShowStatus), default=WhatnotShowStatus.DRAFT, index=True
    )
    viewer_count: Mapped[int | None] = mapped_column(Integer)
    follower_count: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WhatnotShowItem(Base):
    """One item queued for (or sold in) a show. `sale_id` is set the moment
    an item is marked SOLD -- see whatnot.py's mark_outcome, which creates
    the Sale/SaleItem automatically using the active Whatnot fee rule so a
    sold item shows up in /sales and /analytics without re-entering it."""

    __tablename__ = "whatnot_show_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    whatnot_show_id: Mapped[str] = mapped_column(ForeignKey("whatnot_shows.id"), index=True)
    inventory_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="SET NULL"), index=True
    )
    description: Mapped[str] = mapped_column(String(256))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    starting_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    final_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    fees_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    buyer_shipping: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    buyer_tax: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    buyer_handle: Mapped[str | None] = mapped_column(String(128), index=True)
    whatnot_order_id: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    outcome: Mapped[WhatnotShowItemOutcome] = mapped_column(Enum(WhatnotShowItemOutcome), default=WhatnotShowItemOutcome.PENDING, index=True)
    sale_id: Mapped[str | None] = mapped_column(ForeignKey("sales.id"))
    stock_reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stock_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    whatnot_show: Mapped[WhatnotShow] = relationship()


class ScanSession(Base):
    """One sitting of scanning a single set. Survives being interrupted:
    a session left RUNNING is offered for resume next time that set is
    picked, so a pile spread over several days keeps one running count
    instead of fragmenting into a session per sitting.
    """

    __tablename__ = "scan_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    set_id: Mapped[str] = mapped_column(ForeignKey("card_sets.id"), index=True)
    status: Mapped[ScanSessionStatus] = mapped_column(Enum(ScanSessionStatus), default=ScanSessionStatus.RUNNING, index=True)
    batch_variant: Mapped[CardVariant] = mapped_column(Enum(CardVariant), default=CardVariant.NORMAL)
    sheets_scanned: Mapped[int] = mapped_column(Integer, default=0)
    cards_committed: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_by: Mapped[str | None] = mapped_column(String(128))

    card_set: Mapped[CardSet] = relationship()


class InventoryItem(Base):
    """A physical card actually owned, as opposed to CatalogCard (what
    exists in the world). Quantity-based rather than row-per-copy: scanning
    a third copy of the same card in the same variant and condition
    increments an existing row, since inventory questions are "how many do
    I have" rather than "tell me about copy #2".

    That makes (catalog_item_id, variant, condition) the natural identity,
    enforced by a unique constraint so a concurrent double-commit can't
    split one holding across two rows.

    `catalog_item_id` is the canonical identity for every stock type.
    `card_id` is a temporary compatibility link to the older Pokemon-style
    CatalogCard reference table. When present it must agree during the 60-day
    observation window; sealed products and collectibles leave it NULL.

    `card_id` was made nullable by scripts/migrations/migrate_relax_inventory_card_id.py
    (a full table rebuild -- SQLite can't ALTER a NOT NULL away) so a
    non-TCG collectible (Sonny Angel, Smiski -- no catalog_cards row at all)
    can have real ownership: an InventoryItem with card_id=None and
    catalog_item_id pointing at its CollectibleProduct. Every existing
    TCG-facing query joins InventoryItem to CatalogCard on card_id, which
    naturally still excludes these rows (NULL never matches a join), so no
    other reader needed to change.
    """

    __tablename__ = "inventory_items"
    __table_args__ = (UniqueConstraint(
        "catalog_item_id", "variant", "condition",
        name="uq_inventory_catalog_item_variant_condition",
    ),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    card_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_cards.id"), index=True)
    variant: Mapped[CardVariant] = mapped_column(Enum(CardVariant), default=CardVariant.NORMAL, index=True)
    condition: Mapped[str] = mapped_column(String(64), default="Near Mint")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    scan_image_path: Mapped[str | None] = mapped_column(String(512))
    session_id: Mapped[str | None] = mapped_column(ForeignKey("scan_sessions.id"), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    catalog_item_id: Mapped[str] = mapped_column(ForeignKey("catalog_items.id"), nullable=False, index=True)
    storage_location_id: Mapped[str | None] = mapped_column(ForeignKey("storage_locations.id"), index=True)
    status: Mapped[InventoryStatus] = mapped_column(Enum(InventoryStatus), default=InventoryStatus.AVAILABLE, index=True)
    allocated_cost_basis: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    purchase_cost_basis: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    allocated_shipping_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    grading_company: Mapped[str | None] = mapped_column(String(64))
    # Kept separate from condition: "Near Mint" and "PSA 10" are not two
    # spellings of one fact and must never share pricing comparisons.
    grade: Mapped[str | None] = mapped_column(String(32), index=True)
    certification_number: Mapped[str | None] = mapped_column(String(64))

    card: Mapped[CatalogCard | None] = relationship()
    catalog_item: Mapped[CatalogItem | None] = relationship()
    storage_location: Mapped[StorageLocation | None] = relationship()


@event.listens_for(InventoryItem, "before_insert")
@event.listens_for(InventoryItem, "before_update")
def _enforce_inventory_catalog_identity(_mapper, _connection, item: InventoryItem) -> None:
    """Keep legacy TCG writes safe while catalogue identity becomes canonical."""
    if item.catalog_item_id is None and item.card_id is not None:
        item.catalog_item_id = item.card_id
    if item.catalog_item_id is None:
        raise ValueError("InventoryItem.catalog_item_id is required")
    if item.card_id is not None and item.card_id != item.catalog_item_id:
        raise ValueError("InventoryItem.card_id must match catalog_item_id during compatibility")


class FoilLabel(Base):
    """A human's yes/no verdict on one `assess_foil` suggestion.

    `assess_foil` runs in shadow mode -- it logs a guess but never picks the
    committed variant, because there's no confirmed sample to say its ±4
    body-bias threshold means anything (see that module's docstring). This
    is where confirmed samples come from: every verdict recorded at
    /foil-review is a labelled (metrics, was it actually foil) pair, so the
    threshold can eventually be set from real data instead of a guess,
    across every set rather than the handful checked by hand so far.
    """

    __tablename__ = "foil_labels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Labels remain useful training data after a holding is sold, merged or
    # removed.  Preserve the sample but detach it from the vanished copy.
    inventory_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    card_id: Mapped[str] = mapped_column(ForeignKey("catalog_cards.id"), index=True)
    filed_variant: Mapped[CardVariant] = mapped_column(Enum(CardVariant))
    suggested_variant: Mapped[CardVariant] = mapped_column(Enum(CardVariant))
    # What it actually is, per the human -- not always `suggested_variant`:
    # a card can genuinely be foil while the weak signal guessed the wrong
    # *kind* of foil (confirmed live: an Articuno the signal called
    # reverse_holo that was actually a plain holo). `confirmed` alone can't
    # represent that third case.
    actual_variant: Mapped[CardVariant] = mapped_column(Enum(CardVariant))
    confirmed: Mapped[bool] = mapped_column(Boolean)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    labeled_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CardPrice(Base):
    """A price observation for a catalogue item, kept append-only and
    source-tagged rather than as a single mutable column on InventoryItem.

    Deliberately decoupled from ingest: pokemontcg.io's pricing proved
    unreliable in practice, so swapping in a different source (eBay sold
    comps) is a new `source` value written by a different adapter, with no
    schema change and no change to scanning code.
    """

    __tablename__ = "card_prices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # The database column remains named ``card_id`` for a no-downtime
    # compatibility window, but the application attribute says what the id
    # has represented since the generic catalogue shipped. The synonym keeps
    # old one-off scripts readable during the agreed retirement period.
    catalog_item_id: Mapped[str] = mapped_column(
        "card_id", ForeignKey("catalog_items.id", ondelete="CASCADE"), index=True
    )
    card_id = synonym("catalog_item_id")
    variant: Mapped[CardVariant | None] = mapped_column(Enum(CardVariant))
    source: Mapped[str] = mapped_column(String(64), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="AUD")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    catalog_item: Mapped[CatalogItem] = relationship()


class PricingProvider(Base):
    """Non-secret provider configuration and current operational health."""

    __tablename__ = "pricing_providers"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    commercial_use_status: Mapped[str] = mapped_column(String(32), default="unverified")
    health_status: Mapped[PricingProviderHealth] = mapped_column(
        Enum(PricingProviderHealth), default=PricingProviderHealth.UNKNOWN, index=True,
    )
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PricingProviderRule(Base):
    """Operator-configurable provider routing for one catalogue category.

    TCG pricing continues through its existing game-specific pipelines. These
    rows govern the generic pricing service only, making the collectible
    provider boundary explicit rather than a set of brand-name exceptions.
    """

    __tablename__ = "pricing_provider_rules"
    __table_args__ = (
        UniqueConstraint("catalog_item_type", "provider_key", name="uq_pricing_provider_rule"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    catalog_item_type: Mapped[CatalogItemType] = mapped_column(Enum(CatalogItemType), index=True)
    provider_key: Mapped[str] = mapped_column(
        ForeignKey("pricing_providers.key", ondelete="CASCADE"), index=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    access_mode: Mapped[str] = mapped_column(String(16), default="disabled")
    allow_active: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_sold: Mapped[bool] = mapped_column(Boolean, default=False)
    refresh_seconds: Mapped[int] = mapped_column(Integer, default=86_400)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    limitation: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PricingExternalId(Base):
    """Resolve an EzBay product once and reuse stable provider identifiers."""

    __tablename__ = "pricing_external_ids"
    __table_args__ = (
        UniqueConstraint(
            "catalog_item_id", "provider_key", "provider_product_id", "provider_variant_id",
            name="uq_pricing_external_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    catalog_item_id: Mapped[str] = mapped_column(
        ForeignKey("catalog_items.id", ondelete="CASCADE"), index=True,
    )
    provider_key: Mapped[str] = mapped_column(
        ForeignKey("pricing_providers.key", ondelete="CASCADE"), index=True,
    )
    provider_product_id: Mapped[str] = mapped_column(String(128), index=True)
    provider_group_id: Mapped[str | None] = mapped_column(String(128), index=True)
    provider_variant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    external_ids: Mapped[dict] = mapped_column(JSON, default=dict)
    match_confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("1"))
    match_method: Mapped[str] = mapped_column(String(32), default="exact")
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PricingObservation(Base):
    """One normalized provider fact, retained independently of valuations."""

    __tablename__ = "pricing_observations"
    __table_args__ = (
        Index("ix_pricing_observation_identity_time", "catalog_item_id", "observed_at"),
        Index("ix_pricing_observation_provider_item", "provider_key", "source_item_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    catalog_item_id: Mapped[str] = mapped_column(
        ForeignKey("catalog_items.id", ondelete="CASCADE"), index=True,
    )
    provider_key: Mapped[str] = mapped_column(
        ForeignKey("pricing_providers.key", ondelete="CASCADE"), index=True,
    )
    external_id_id: Mapped[str | None] = mapped_column(
        ForeignKey("pricing_external_ids.id", ondelete="SET NULL"), index=True,
    )
    provider_product_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_item_id: Mapped[str | None] = mapped_column(String(256))
    source_kind: Mapped[PricingSourceKind] = mapped_column(Enum(PricingSourceKind), index=True)
    marketplace: Mapped[str | None] = mapped_column(String(32), index=True)
    query: Mapped[str | None] = mapped_column(String(512))
    product_type: Mapped[str | None] = mapped_column(String(64), index=True)
    listing_type: Mapped[str | None] = mapped_column(String(64))
    card_name: Mapped[str | None] = mapped_column(String(256))
    set_name: Mapped[str | None] = mapped_column(String(256))
    set_code: Mapped[str | None] = mapped_column(String(64))
    card_number: Mapped[str | None] = mapped_column(String(64))
    variant: Mapped[str | None] = mapped_column(String(64), index=True)
    language: Mapped[str | None] = mapped_column(String(32), index=True)
    condition: Mapped[str | None] = mapped_column(String(64), index=True)
    grading_company: Mapped[str | None] = mapped_column(String(64), index=True)
    grade: Mapped[str | None] = mapped_column(String(32), index=True)
    title: Mapped[str | None] = mapped_column(String(512))
    original_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    original_currency: Mapped[str] = mapped_column(String(3))
    aud_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    shipping_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    total_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    listed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    item_url: Mapped[str | None] = mapped_column(String(1024))
    image_url: Mapped[str | None] = mapped_column(String(1024))
    match_confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("1"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    ignored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    ignored_by: Mapped[str | None] = mapped_column(String(128))
    ignore_reason: Mapped[str | None] = mapped_column(String(512))
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class PricingValuation(Base):
    """A derived valuation snapshot for one exact product identity."""

    __tablename__ = "pricing_valuations"
    __table_args__ = (Index("ix_pricing_valuation_identity_time", "catalog_item_id", "valued_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    catalog_item_id: Mapped[str] = mapped_column(
        ForeignKey("catalog_items.id", ondelete="CASCADE"), index=True,
    )
    identity_key: Mapped[str] = mapped_column(String(64), index=True)
    variant: Mapped[str | None] = mapped_column(String(64), index=True)
    language: Mapped[str | None] = mapped_column(String(32), index=True)
    condition: Mapped[str | None] = mapped_column(String(64), index=True)
    grading_company: Mapped[str | None] = mapped_column(String(64), index=True)
    grade: Mapped[str | None] = mapped_column(String(32), index=True)
    market_value_aud: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    global_market_value_aud: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    australian_market_value_aud: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    sold_median_aud: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    active_median_aud: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    expected_private_sale_aud: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    expected_ebay_sale_aud: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    quick_sale_value_aud: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    store_buylist_value_aud: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    low_aud: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    high_aud: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    comp_count: Mapped[int] = mapped_column(Integer, default=0)
    provider_count: Mapped[int] = mapped_column(Integer, default=0)
    confidence_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    confidence_label: Mapped[str] = mapped_column(String(16), default="low", index=True)
    confidence_components: Mapped[dict] = mapped_column(JSON, default=dict)
    calculation_version: Mapped[str] = mapped_column(String(32), default="v1")
    valued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PricingProviderUsage(Base):
    __tablename__ = "pricing_provider_usage"
    __table_args__ = (
        UniqueConstraint("provider_key", "quota_type", "period_start", name="uq_pricing_provider_usage_period"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider_key: Mapped[str] = mapped_column(
        ForeignKey("pricing_providers.key", ondelete="CASCADE"), index=True,
    )
    quota_type: Mapped[str] = mapped_column(String(32), default="requests")
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    quota_limit: Mapped[int | None] = mapped_column(Integer)
    used: Mapped[int] = mapped_column(Integer, default=0)
    results: Mapped[int] = mapped_column(Integer, default=0)
    cache_hits: Mapped[int] = mapped_column(Integer, default=0)
    cache_misses: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    actual_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="AUD")
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PricingCacheEntry(Base):
    __tablename__ = "pricing_cache_entries"
    __table_args__ = (UniqueConstraint("provider_key", "cache_key", name="uq_pricing_cache_provider_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider_key: Mapped[str] = mapped_column(
        ForeignKey("pricing_providers.key", ondelete="CASCADE"), index=True,
    )
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    endpoint: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    negative: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)


class PricingRefreshQueue(Base):
    __tablename__ = "pricing_refresh_queue"
    __table_args__ = (
        UniqueConstraint("catalog_item_id", "identity_key", name="uq_pricing_refresh_identity"),
        Index("ix_pricing_refresh_ready", "status", "available_at", "priority"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    catalog_item_id: Mapped[str] = mapped_column(
        ForeignKey("catalog_items.id", ondelete="CASCADE"), index=True,
    )
    identity_key: Mapped[str] = mapped_column(String(64))
    identity: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(String(64), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    status: Mapped[PricingQueueStatus] = mapped_column(
        Enum(PricingQueueStatus), default=PricingQueueStatus.PENDING, index=True,
    )
    requested_manually: Mapped[bool] = mapped_column(Boolean, default=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PricingSyncRun(Base):
    __tablename__ = "pricing_sync_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider_key: Mapped[str] = mapped_column(
        ForeignKey("pricing_providers.key", ondelete="CASCADE"), index=True,
    )
    job_kind: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[PricingSyncStatus] = mapped_column(Enum(PricingSyncStatus), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    products_seen: Mapped[int] = mapped_column(Integer, default=0)
    observations_written: Mapped[int] = mapped_column(Integer, default=0)
    cache_hits: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    actual_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    error: Mapped[str | None] = mapped_column(String(1024))
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class PricingFxRate(Base):
    __tablename__ = "pricing_fx_rates"
    __table_args__ = (
        UniqueConstraint("base_currency", "quote_currency", "observed_at", name="uq_pricing_fx_observation"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    base_currency: Mapped[str] = mapped_column(String(3), index=True)
    quote_currency: Mapped[str] = mapped_column(String(3), index=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    source: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PricingProviderCategory(Base):
    """A provider's game/category, retained locally for bulk discovery."""

    __tablename__ = "pricing_provider_categories"
    __table_args__ = (
        UniqueConstraint("provider_key", "provider_category_id", name="uq_pricing_provider_category"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider_key: Mapped[str] = mapped_column(
        ForeignKey("pricing_providers.key", ondelete="CASCADE"), index=True,
    )
    provider_category_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    display_name: Mapped[str | None] = mapped_column(String(256))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class PricingProviderGroup(Base):
    """A provider set/group; products belong to exactly one imported group."""

    __tablename__ = "pricing_provider_groups"
    __table_args__ = (
        UniqueConstraint("provider_key", "provider_group_id", name="uq_pricing_provider_group"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider_key: Mapped[str] = mapped_column(
        ForeignKey("pricing_providers.key", ondelete="CASCADE"), index=True,
    )
    category_id: Mapped[str] = mapped_column(
        ForeignKey("pricing_provider_categories.id", ondelete="CASCADE"), index=True,
    )
    provider_group_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    abbreviation: Mapped[str | None] = mapped_column(String(64), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class PricingProviderProduct(Base):
    """Provider catalogue product, including singles and sealed products."""

    __tablename__ = "pricing_provider_products"
    __table_args__ = (
        UniqueConstraint("provider_key", "provider_product_id", name="uq_pricing_provider_product"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider_key: Mapped[str] = mapped_column(
        ForeignKey("pricing_providers.key", ondelete="CASCADE"), index=True,
    )
    group_id: Mapped[str] = mapped_column(
        ForeignKey("pricing_provider_groups.id", ondelete="CASCADE"), index=True,
    )
    provider_product_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(512), index=True)
    clean_name: Mapped[str | None] = mapped_column(String(512))
    product_type: Mapped[str] = mapped_column(String(64), default="unknown", index=True)
    card_number: Mapped[str | None] = mapped_column(String(64), index=True)
    rarity: Mapped[str | None] = mapped_column(String(128), index=True)
    printing: Mapped[str | None] = mapped_column(String(128), index=True)
    image_url: Mapped[str | None] = mapped_column(String(1024))
    item_url: Mapped[str | None] = mapped_column(String(1024))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class PricingProviderQuote(Base):
    """Append-only bulk price row for a provider product/printing."""

    __tablename__ = "pricing_provider_quotes"
    __table_args__ = (
        UniqueConstraint(
            "provider_key", "provider_product_id", "subtype", "source_updated_at",
            name="uq_pricing_provider_quote_build",
        ),
        Index("ix_pricing_provider_quote_latest", "provider_key", "provider_product_id", "observed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider_key: Mapped[str] = mapped_column(
        ForeignKey("pricing_providers.key", ondelete="CASCADE"), index=True,
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("pricing_provider_products.id", ondelete="CASCADE"), index=True,
    )
    provider_product_id: Mapped[str] = mapped_column(String(128), index=True)
    subtype: Mapped[str] = mapped_column(String(128), index=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    low_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    mid_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    high_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    market_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    direct_low_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class MarketWatchTerm(Base):
    """A user-managed collectible search shared by every market source.

    ``sources`` names discovery mechanisms (currently eBay and public-web
    search), while individual normalized listings retain the marketplace
    they came from.  Countries live on the term as JSON because a watch can
    deliberately cover AU, JP, or both without duplicating the query.
    """

    __tablename__ = "market_watch_terms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(128), index=True)
    query: Mapped[str] = mapped_column(String(256))
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    countries: Mapped[list] = mapped_column(JSON, default=lambda: ["AU", "JP"])
    sources: Mapped[list] = mapped_column(JSON, default=lambda: ["ebay"])
    max_landed_aud: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_scan_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MarketListing(Base):
    """A normalized, current listing discovered on any marketplace.

    Prices retain their source currency while ``landed_aud`` is the number
    used for deal comparisons: item + delivery + estimated import tax, all
    converted to AUD.  Keeping both makes exchange-rate changes auditable.
    """

    __tablename__ = "market_listings"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_market_listing_source_external"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(256))
    catalog_item_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_items.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(1024))
    image_url: Mapped[str | None] = mapped_column(String(1024))
    seller: Mapped[str | None] = mapped_column(String(256))
    item_kind: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    condition: Mapped[str | None] = mapped_column(String(64))
    country: Mapped[str | None] = mapped_column(String(8), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    shipping: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3))
    exchange_rate_aud: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal("1"))
    estimated_tax_aud: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    landed_aud: Mapped[Decimal] = mapped_column(Numeric(12, 2), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)

    # Purchase-candidate workflow. NULL means this is only a discovered
    # market result; setting a status promotes it into the owner's buying
    # pipeline without copying the listing into a second table.
    candidate_status: Mapped[PotentialPurchaseStatus | None] = mapped_column(
        Enum(PotentialPurchaseStatus), index=True,
    )
    target_price_aud: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    max_price_aud: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    candidate_notes: Mapped[str | None] = mapped_column(Text)
    candidate_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    catalog_item: Mapped[CatalogItem | None] = relationship()


class MarketListingMatch(Base):
    """Many-to-many link between a listing and the watches it satisfied."""

    __tablename__ = "market_listing_matches"

    watch_term_id: Mapped[str] = mapped_column(
        ForeignKey("market_watch_terms.id", ondelete="CASCADE"), primary_key=True,
    )
    market_listing_id: Mapped[str] = mapped_column(
        ForeignKey("market_listings.id", ondelete="CASCADE"), primary_key=True,
    )
    matched_by: Mapped[str] = mapped_column(String(32), default="query")
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MarketAlertImport(Base):
    """Deduplication ledger for uploaded or IMAP-fetched marketplace mail."""

    __tablename__ = "market_alert_imports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(32), default="email")
    subject: Mapped[str | None] = mapped_column(String(512))
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="imported", index=True)
    detail: Mapped[str | None] = mapped_column(String(1024))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class MarketAlertDelivery(Base):
    """Deduplication/audit ledger for Telegram and Discord alerts."""

    __tablename__ = "market_alert_deliveries"
    __table_args__ = (UniqueConstraint("market_listing_id", "channel", name="uq_market_alert_listing_channel"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    market_listing_id: Mapped[str] = mapped_column(ForeignKey("market_listings.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="sent")
    detail: Mapped[str | None] = mapped_column(String(512))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- NL Vault storefront (Module 24) ----------------------------------------
# The public shop is a *separate application* (storefront/) reading this same
# database -- deliberately not extra routes on the admin app, so an
# auth misconfiguration there can never expose inventory management,
# scan control, or eBay credentials to the public internet. These tables
# are the contract between the two.


class ShopListingStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    SOLD_OUT = "sold_out"
    WITHDRAWN = "withdrawn"


class ShopOrderStatus(str, enum.Enum):
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    PACKED = "packed"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class ShippingZone(str, enum.Enum):
    AU = "au"
    INTERNATIONAL = "international"


class ShopListing(Base):
    """One thing offered for sale on the storefront.

    Deliberately separate from InventoryItem rather than a flag on it:
    owning something and offering it publicly at a chosen price are
    different decisions, and the shop must never implicitly list the whole
    collection because a scan happened. `price` is set per listing rather
    than read live from card_prices -- a shopper's price shouldn't move
    because a market feed twitched mid-session.
    """

    __tablename__ = "shop_listings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    catalog_item_id: Mapped[str] = mapped_column(ForeignKey("catalog_items.id"), index=True)
    # A listing backed by physical stock must not survive deletion of that
    # stock as an active, unfulfillable storefront item.  Manually-created
    # listings can still leave this nullable field empty.
    inventory_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    # The storefront/admin title has a different job from eBay's search
    # title.  Keeping an explicit channel override prevents the edit screen
    # from claiming it changed eBay while ebay_publish silently regenerates
    # a different title from catalogue metadata.
    ebay_title: Mapped[str | None] = mapped_column(String(80))
    ebay_description: Mapped[str | None] = mapped_column(Text)
    # Ordered, operator-owned photos for the eBay listing. Values may be a
    # local processed-photo path (uploaded to R2 only after final publish
    # confirmation) or an existing public HTTPS URL. Originals are kept on
    # disk beside processed uploads and are never overwritten.
    ebay_photo_paths: Mapped[list[str] | None] = mapped_column(JSON)
    condition: Mapped[str | None] = mapped_column(String(64))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="AUD")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[ShopListingStatus] = mapped_column(Enum(ShopListingStatus), default=ShopListingStatus.DRAFT, index=True)
    image_url: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    catalog_item: Mapped[CatalogItem] = relationship()


class ShippingRate(Base):
    """Flat shipping per zone, with an optional free-over threshold.
    Rates as rows rather than constants so postage price rises don't need
    a code change -- same reasoning as MarketplaceFeeRule."""

    __tablename__ = "shipping_rates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    zone: Mapped[ShippingZone] = mapped_column(Enum(ShippingZone), index=True)
    label: Mapped[str] = mapped_column(String(128))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    free_over: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class ShopOrder(Base):
    """A customer order. Payment is handled by Stripe Checkout, so no card
    data ever reaches this server -- only the session/payment id needed to
    reconcile. Totals are stored as charged rather than recomputed, so a
    later price or postage change can't rewrite history."""

    __tablename__ = "shop_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    reference: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(256), index=True)
    ship_to_name: Mapped[str] = mapped_column(String(256))
    ship_to_address: Mapped[str] = mapped_column(Text)
    ship_to_phone: Mapped[str | None] = mapped_column(String(64))
    ship_to_country: Mapped[str] = mapped_column(String(64), default="Australia")
    zone: Mapped[ShippingZone] = mapped_column(Enum(ShippingZone), default=ShippingZone.AU)
    items_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    shipping_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    grand_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="AUD")
    status: Mapped[ShopOrderStatus] = mapped_column(Enum(ShopOrderStatus), default=ShopOrderStatus.PENDING_PAYMENT, index=True)
    stripe_session_id: Mapped[str | None] = mapped_column(String(256), index=True)
    stripe_payment_intent: Mapped[str | None] = mapped_column(String(256))
    tracking_number: Mapped[str | None] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    stock_reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stock_restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ShopOrderItem(Base):
    """A line on an order. Title and price are copied rather than
    referenced, so an order still reads correctly after the listing behind
    it is edited, sold, or withdrawn."""

    __tablename__ = "shop_order_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id: Mapped[str] = mapped_column(ForeignKey("shop_orders.id"), index=True)
    listing_id: Mapped[str | None] = mapped_column(ForeignKey("shop_listings.id"), index=True)
    bulk_lot_id: Mapped[str | None] = mapped_column(ForeignKey("bulk_lots.id"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    order: Mapped[ShopOrder] = relationship()


class BulkLot(Base):
    """A set-based lot of low-value cards -- the "Bulk Master" section.

    Its own table rather than a flavour of ShopListing: a lot has no
    single catalog_item behind it, and forcing one would mean making
    ShopListing.catalog_item_id nullable (a SQLite table rebuild) to model
    something that is genuinely a different product. Most of a scanned
    collection is commons worth under a dollar or two -- individually
    unsellable once postage is $9.95, but a viable product by the
    hundred, sorted by set.
    """

    __tablename__ = "bulk_lots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    set_id: Mapped[str | None] = mapped_column(ForeignKey("card_sets.id"), index=True)
    game: Mapped[str] = mapped_column(String(32), default="pokemon", index=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    card_count: Mapped[int] = mapped_column(Integer, default=0)
    market_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="AUD")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[ShopListingStatus] = mapped_column(Enum(ShopListingStatus), default=ShopListingStatus.DRAFT, index=True)
    image_url: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    card_set: Mapped[CardSet] = relationship()


class ShopCostConfig(Base):
    """What it actually costs to fulfil an order -- the inputs to a real
    break-even price.

    The bulk-lot builder previously used a hardcoded $12 floor, which was a
    guess dressed up as a rule: it couldn't say whether $12 was profitable,
    and at these order values the fixed payment fee alone is a material
    share of the take. These are the numbers that decide it, stored rather
    than constants so postage rises and fee changes aren't code edits.

    Card cost basis is deliberately not in here -- it's per item
    (`inventory_items.allocated_cost_basis`) and mostly unknown for a
    scanned collection, so the break-even below treats unknown stock cost
    as zero and says so, rather than inventing a number.
    """

    __tablename__ = "shop_cost_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    label: Mapped[str] = mapped_column(String(128), default="Default")
    postage_cost_au: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("9.95"))
    postage_cost_intl: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("29.95"))
    packaging_cost: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("1.20"))
    payment_pct: Mapped[Decimal] = mapped_column(Numeric(5, 3), default=Decimal("1.650"))
    payment_fixed: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.30"))
    target_margin_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("25.00"))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SetDiscountTier(Base):
    """A quantity threshold that earns a discount when a cart holds that
    many items from the same TCG set or collectible series -- "buy more
    of one set, pay less per card". Rows rather than a code constant, same
    reasoning as ShippingRate: the numbers should be tunable without a
    redeploy, and there's no reason the right threshold for a slow set is
    the right one for a hot one.

    Bulk lots never participate: a lot already IS a whole set in one
    line, so discounting it against itself has no meaning.
    """

    __tablename__ = "set_discount_tiers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    min_quantity: Mapped[int] = mapped_column(Integer)
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    label: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class SocialLink(Base):
    """A social/marketplace link shown in the shop's footer -- Instagram,
    the eBay store, Whatnot. Rows rather than constants so the operator
    can add, retire or reorder one without a redeploy; is_active rather
    than deleting means a paused channel's history isn't lost.

    A blank url is treated as "not ready to show" by the renderer, not an
    error -- a platform can exist here as a placeholder while the real
    link is pending, without it ever rendering as a broken link.
    """

    __tablename__ = "social_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    platform: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(String(512))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PhotoAuditDismissal(Base):
    """A human's "this is fine" verdict on a flagged photo/card mismatch.

    scripts/audit_inventory_photos.py's hash comparison is triage, not a
    verdict (see its own docstring) -- a crop catching the edge of the next
    card, or a foil, can hash badly against its own reference without being
    the wrong card. Without this, every /inventory-audit run would re-show
    the same false positive forever. Recorded per item rather than by
    silently raising the threshold, since the fix is "a human looked and
    confirmed" for that one photo, not a global tolerance change.
    """

    __tablename__ = "photo_audit_dismissals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    inventory_item_id: Mapped[str] = mapped_column(ForeignKey("inventory_items.id"), unique=True, index=True)
    own_distance_at_dismissal: Mapped[int] = mapped_column(Integer)
    dismissed_by: Mapped[str] = mapped_column(String(128))
    dismissed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContactMessage(Base):
    """A message from the shop's Contact page.

    No outbound email exists anywhere in this app, so a message can't be
    forwarded to an inbox -- it lands here instead, and the operator reads
    it in the CRM the same way they'd check shop orders. `is_read` rather
    than a delete keeps a record rather than losing it once handled.
    """

    __tablename__ = "contact_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(256))
    email: Mapped[str] = mapped_column(String(256), index=True)
    message: Mapped[str] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- Retailer loyalty goals -------------------------------------------------
#
# These tables deliberately use retailer/program language rather than
# Gameology-specific names. Gameology Guild is the first seeded program, but
# the same ledger can later represent an EB Games tier, a distributor spend
# target, or a supplier minimum-order program without another parallel system.


class Retailer(Base):
    __tablename__ = "retailers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    website: Mapped[str | None] = mapped_column(String(512))
    domain: Mapped[str | None] = mapped_column(String(256), unique=True)
    trust_status: Mapped[RetailerTrustStatus] = mapped_column(
        Enum(RetailerTrustStatus), default=RetailerTrustStatus.UNVERIFIED, index=True
    )
    best_price_eligible: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    last_reviewed: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RetailerProgram(Base):
    __tablename__ = "retailer_programs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    retailer_id: Mapped[str] = mapped_column(ForeignKey("retailers.id"), index=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256))
    currency: Mapped[str] = mapped_column(String(3), default="AUD")
    historical_eligible_spend: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    eligible_spend_notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    retailer: Mapped[Retailer] = relationship()


class RetailerMilestone(Base):
    __tablename__ = "retailer_milestones"
    __table_args__ = (UniqueConstraint("program_id", "target_amount", name="uq_retailer_milestone_target"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    program_id: Mapped[str] = mapped_column(ForeignKey("retailer_programs.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(256))
    target_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    achieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RetailerBudget(Base):
    __tablename__ = "retailer_budgets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    program_id: Mapped[str] = mapped_column(
        ForeignKey("retailer_programs.id", ondelete="CASCADE"), unique=True, index=True
    )
    weekly_contribution: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("100"))
    monthly_maximum: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    per_product_maximum: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    contribution_weekday: Mapped[int] = mapped_column(Integer, default=4)  # Monday=0, Friday=4
    next_contribution_date: Mapped[date] = mapped_column(Date)
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RetailerPremiumConfig(Base):
    __tablename__ = "retailer_premium_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    program_id: Mapped[str] = mapped_column(
        ForeignKey("retailer_programs.id", ondelete="CASCADE"), unique=True, index=True
    )
    excellent_max_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0"))
    excellent_guild_max_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("5"))
    acceptable_max_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("10"))
    borderline_max_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("15"))


class RetailerProduct(Base):
    __tablename__ = "retailer_products"
    __table_args__ = (UniqueConstraint("program_id", "name", name="uq_retailer_product_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    program_id: Mapped[str] = mapped_column(ForeignKey("retailer_programs.id", ondelete="CASCADE"), index=True)
    catalog_item_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_items.id"), index=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    tcg: Mapped[str] = mapped_column(String(32), default="other", index=True)
    product_type: Mapped[str] = mapped_column(String(64), default="other", index=True)
    set_name: Mapped[str | None] = mapped_column(String(256))
    retailer_url: Mapped[str | None] = mapped_column(String(1024))
    quantity_wanted: Mapped[int] = mapped_column(Integer, default=1)
    maximum_acceptable_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    release_date: Mapped[date | None] = mapped_column(Date, index=True)
    preorder_date: Mapped[date | None] = mapped_column(Date)
    purchase_limit: Mapped[int | None] = mapped_column(Integer)
    stock_status: Mapped[RetailerStockStatus] = mapped_column(
        Enum(RetailerStockStatus), default=RetailerStockStatus.UNKNOWN, index=True
    )
    status: Mapped[RetailerProductStatus] = mapped_column(
        Enum(RetailerProductStatus), default=RetailerProductStatus.WATCH, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=3, index=True)
    image_url: Mapped[str | None] = mapped_column(String(1024))
    notes: Mapped[str | None] = mapped_column(Text)
    date_added: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    catalog_item: Mapped[CatalogItem | None] = relationship()


class RetailerPriceSnapshot(Base):
    __tablename__ = "retailer_price_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id: Mapped[str] = mapped_column(ForeignKey("retailer_products.id", ondelete="CASCADE"), index=True)
    retailer_id: Mapped[str] = mapped_column(ForeignKey("retailers.id"), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="AUD")
    product_url: Mapped[str | None] = mapped_column(String(1024))
    stock_status: Mapped[RetailerStockStatus] = mapped_column(
        Enum(RetailerStockStatus), default=RetailerStockStatus.UNKNOWN, index=True
    )
    source: Mapped[str] = mapped_column(String(64), default="manual")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    retailer: Mapped[Retailer] = relationship()


class RetailerPurchase(Base):
    __tablename__ = "retailer_purchases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    submission_key: Mapped[str | None] = mapped_column(String(36), unique=True, index=True)
    program_id: Mapped[str] = mapped_column(ForeignKey("retailer_programs.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("retailer_products.id"), index=True)
    retailer_id: Mapped[str] = mapped_column(ForeignKey("retailers.id"), index=True)
    purchase_unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    eligible_spend: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    shipping: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    market_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    guild_premium_paid: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    purchase_date: Mapped[date] = mapped_column(Date, index=True)
    order_number: Mapped[str | None] = mapped_column(String(256))
    notes: Mapped[str | None] = mapped_column(Text)
    inventory_added: Mapped[bool] = mapped_column(Boolean, default=False)
    inventory_item_id: Mapped[str | None] = mapped_column(ForeignKey("inventory_items.id"), index=True)
    purchase_lot_id: Mapped[str | None] = mapped_column(ForeignKey("purchase_lots.id"), index=True)
    status: Mapped[RetailerPurchaseStatus] = mapped_column(
        Enum(RetailerPurchaseStatus), default=RetailerPurchaseStatus.RECORDED, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RetailerFundEntry(Base):
    __tablename__ = "retailer_fund_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    budget_id: Mapped[str] = mapped_column(ForeignKey("retailer_budgets.id", ondelete="CASCADE"), index=True)
    purchase_id: Mapped[str | None] = mapped_column(ForeignKey("retailer_purchases.id"), index=True)
    kind: Mapped[RetailerFundEntryKind] = mapped_column(Enum(RetailerFundEntryKind), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))  # signed: spending is negative
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RetailerNotificationPreference(Base):
    __tablename__ = "retailer_notification_preferences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    program_id: Mapped[str] = mapped_column(
        ForeignKey("retailer_programs.id", ondelete="CASCADE"), unique=True, index=True
    )
    preorder_open: Mapped[bool] = mapped_column(Boolean, default=True)
    restock: Mapped[bool] = mapped_column(Boolean, default=True)
    price_drop: Mapped[bool] = mapped_column(Boolean, default=True)
    premium_at_or_below_5: Mapped[bool] = mapped_column(Boolean, default=True)
    premium_at_or_below_10: Mapped[bool] = mapped_column(Boolean, default=True)
    becomes_affordable: Mapped[bool] = mapped_column(Boolean, default=True)
    high_priority_status_change: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
