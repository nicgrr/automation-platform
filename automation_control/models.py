import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, LargeBinary, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

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


class CaptureBatchStatus(str, enum.Enum):
    RUNNING = "running"
    DONE = "done"


class ListingBuildStatus(str, enum.Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"
    FAILED = "failed"


class CardVariant(str, enum.Enum):
    """Which printing of a card this copy is. Not reliably detectable from a
    flatbed scan (holo foil mostly reads as glare), so the operator sets it
    per batch and can override it per card at confirmation time."""

    NORMAL = "normal"
    REVERSE_HOLO = "reverse_holo"
    HOLO = "holo"


class CatalogItemType(str, enum.Enum):
    """What kind of thing a CatalogItem is -- decides which detail table
    (TcgCard / SealedProduct / CollectibleProduct) has its other row."""

    TCG_CARD = "tcg_card"
    SEALED_PRODUCT = "sealed_product"
    COLLECTIBLE = "collectible"
    GRADED_CARD = "graded_card"
    ACCESSORY = "accessory"
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


class PurchaseLotStatus(str, enum.Enum):
    EVALUATING = "evaluating"
    OFFER_SENT = "offer_sent"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    PURCHASED = "purchased"


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


class ScanSessionStatus(str, enum.Enum):
    RUNNING = "running"
    DONE = "done"


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
    bundle_id: Mapped[str | None] = mapped_column(String(36), index=True)
    pricing_status: Mapped[PricingStatus] = mapped_column(Enum(PricingStatus), default=PricingStatus.NOT_PRICED, index=True)


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
    without being rewritten. See scripts/migrate_add_catalog_items.py.
    """

    __tablename__ = "catalog_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    item_type: Mapped[CatalogItemType] = mapped_column(Enum(CatalogItemType), index=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    image_url: Mapped[str | None] = mapped_column(String(512))
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

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source: Mapped[str] = mapped_column(String(256))
    seller: Mapped[str | None] = mapped_column(String(256))
    asking_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    target_buy_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("55"))
    offered_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    status: Mapped[PurchaseLotStatus] = mapped_column(Enum(PurchaseLotStatus), default=PurchaseLotStatus.EVALUATING, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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

    marketplace: Mapped[Marketplace | None] = relationship()
    customer: Mapped[Customer | None] = relationship()


class SaleItem(Base):
    __tablename__ = "sale_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id"), index=True)
    inventory_item_id: Mapped[str | None] = mapped_column(ForeignKey("inventory_items.id"), index=True)
    description: Mapped[str] = mapped_column(String(256))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    cost_basis: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    sale: Mapped[Sale] = relationship()


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(256))
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
    viewer_count: Mapped[int | None] = mapped_column(Integer)
    follower_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WhatnotShowItem(Base):
    """One item queued for (or sold in) a show. `sale_id` is set the moment
    an item is marked SOLD -- see whatnot.py's mark_outcome, which creates
    the Sale/SaleItem automatically using the active Whatnot fee rule so a
    sold item shows up in /sales and /analytics without re-entering it."""

    __tablename__ = "whatnot_show_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    whatnot_show_id: Mapped[str] = mapped_column(ForeignKey("whatnot_shows.id"), index=True)
    inventory_item_id: Mapped[str | None] = mapped_column(ForeignKey("inventory_items.id"), index=True)
    description: Mapped[str] = mapped_column(String(256))
    starting_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    final_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    outcome: Mapped[WhatnotShowItemOutcome] = mapped_column(Enum(WhatnotShowItemOutcome), default=WhatnotShowItemOutcome.PENDING, index=True)
    sale_id: Mapped[str | None] = mapped_column(ForeignKey("sales.id"))

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

    That makes (card_id, variant, condition) the natural identity, enforced
    by a unique constraint so a concurrent double-commit can't split one
    holding across two rows.

    `catalog_item_id` / `storage_location_id` / `status` / `allocated_cost_basis`
    / `grading_company` / `certification_number` were added by
    scripts/migrate_add_catalog_items.py, backfilled from the existing
    `card_id` -- nullable and additive, so nothing reading only the
    original columns breaks. `card_id` stays the source of truth for the
    live scan-ingest pipeline until every reader is moved over to
    `catalog_item_id` in a later, separate change.
    """

    __tablename__ = "inventory_items"
    __table_args__ = (UniqueConstraint("card_id", "variant", "condition", name="uq_inventory_card_variant_condition"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    card_id: Mapped[str] = mapped_column(ForeignKey("catalog_cards.id"), index=True)
    variant: Mapped[CardVariant] = mapped_column(Enum(CardVariant), default=CardVariant.NORMAL, index=True)
    condition: Mapped[str] = mapped_column(String(64), default="Near Mint")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    scan_image_path: Mapped[str | None] = mapped_column(String(512))
    session_id: Mapped[str | None] = mapped_column(ForeignKey("scan_sessions.id"), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    catalog_item_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_items.id"), index=True)
    storage_location_id: Mapped[str | None] = mapped_column(ForeignKey("storage_locations.id"), index=True)
    status: Mapped[InventoryStatus] = mapped_column(Enum(InventoryStatus), default=InventoryStatus.AVAILABLE, index=True)
    allocated_cost_basis: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    grading_company: Mapped[str | None] = mapped_column(String(64))
    certification_number: Mapped[str | None] = mapped_column(String(64))

    card: Mapped[CatalogCard] = relationship()
    catalog_item: Mapped[CatalogItem | None] = relationship()
    storage_location: Mapped[StorageLocation | None] = relationship()


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
    inventory_item_id: Mapped[str] = mapped_column(ForeignKey("inventory_items.id"), index=True)
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
    """A price observation for a catalog card, kept append-only and
    source-tagged rather than as a single mutable column on InventoryItem.

    Deliberately decoupled from ingest: pokemontcg.io's pricing proved
    unreliable in practice, so swapping in a different source (eBay sold
    comps) is a new `source` value written by a different adapter, with no
    schema change and no change to scanning code.
    """

    __tablename__ = "card_prices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    card_id: Mapped[str] = mapped_column(ForeignKey("catalog_cards.id"), index=True)
    variant: Mapped[CardVariant | None] = mapped_column(Enum(CardVariant))
    source: Mapped[str] = mapped_column(String(64), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="AUD")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    card: Mapped[CatalogCard] = relationship()
