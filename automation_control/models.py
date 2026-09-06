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

    card: Mapped[CatalogCard] = relationship()


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
