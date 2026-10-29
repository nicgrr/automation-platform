from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    automation_database_url: str = "sqlite:///./automation.db"
    grocery_base_url: str = "http://grocery-web:8000"
    app_bind_host: str = "127.0.0.1"
    app_bind_port: int = 8080
    app_public_base_url: str | None = None
    dashboard_username: str | None = None
    dashboard_password_hash: str | None = None
    session_signing_key: str | None = None
    ebay_token_encryption_key: str | None = None
    # "production" unlocks the api.ebay.com / auth.ebay.com endpoints and the
    # matching credential validators in ebay_oauth.py. A real write adapter
    # now exists (adapters/ebay_write.py), but every write call still goes
    # through automation_control/ebay_publish.py's Approval gate -- flipping
    # this alone never causes a listing to be created or ended on its own.
    # Defaults to sandbox; this app never assumes production on your behalf.
    ebay_env: Literal["sandbox", "production"] = "sandbox"
    # Both the reviewed CSV/File Exchange workflow and the explicitly
    # confirmed single-listing Inventory API workflow are supported. This
    # switch is an emergency/operator control that can force all direct-
    # publish links back to Bulk Upload without removing listing sync.
    ebay_bulk_upload_only: bool = False
    ebay_client_id: str | None = None
    ebay_client_secret: str | None = None
    ebay_runame: str | None = None
    # eBay listing-write prerequisites (see automation_control.ebay_oauth
    # .configured_for_writes) -- every createOffer call requires these
    # business-policy IDs and a merchant location, none of which this app
    # can create end-to-end on its own: business policies need the
    # seller's eBay account opted into "Business Policies" first, set up
    # once in Seller Hub (or via eBay's Account API) rather than assumed.
    ebay_marketplace_id: str = "EBAY_AU"
    # Cross-market collectible watches. Secrets stay in the environment;
    # the dashboard only reports whether each destination is configured.
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    discord_webhook_url: str | None = None
    market_alert_max_landed_aud: float | None = None
    # Seller/item locations admitted by market search and market watch.
    # Kept as comma-separated ISO 3166-1 alpha-2 codes so deployment can
    # change scope without a code or schema migration.
    market_allowed_countries: str = "AU,JP"
    # Optional public-web discovery. Brave's ordinary Search plan does not
    # automatically grant the right to persist results, so storage remains
    # explicitly off until the account's chosen plan permits it.
    brave_search_api_key: str | None = None
    brave_search_storage_permitted: bool = False
    market_public_search_domains: str = (
        "gumtree.com.au,depop.com,buyee.jp,zenmarket.jp,fromjapan.co.jp"
    )
    # Optional dedicated mailbox for Gumtree/Buyee/other saved-search mail.
    # An uploaded .eml works without these settings.
    market_alert_imap_host: str | None = None
    market_alert_imap_port: int = 993
    market_alert_imap_username: str | None = None
    market_alert_imap_password: str | None = None
    market_alert_imap_mailbox: str = "INBOX"
    market_alert_imap_use_ssl: bool = True
    ebay_payment_policy_id: str | None = None
    ebay_fulfillment_policy_id: str | None = None
    ebay_return_policy_id: str | None = None
    ebay_merchant_location_key: str | None = None
    # The File Exchange bulk-upload path (scripts/export_ebay_bulk_listing.py
    # + scripts/convert_to_ebay_template.py) needs a different shape of the
    # same information: human-readable Business Policy *names*, the ones
    # printed in Seller Hub, not the numeric policy *ids* the Sell API takes
    # above -- eBay gives no API to translate one into the other, so these
    # are a second, separate set of values rather than derived from the ids.
    ebay_ship_from_location: str | None = None
    ebay_dispatch_days: int | None = None
    ebay_listing_payment_policy_name: str | None = None
    ebay_listing_return_policy_name: str | None = None
    ebay_letter_shipping_policy_name: str | None = None
    ebay_parcel_shipping_policy_name: str | None = None
    listing_pipeline_output_dir: str = "listing_pipeline_data/output"
    listing_pipeline_config_path: str = "listing_pipeline_data/config.toml"
    # Stripe Checkout for the NL Vault storefront. Absent by default: the
    # shop builds and runs without it, and simply tells shoppers payments
    # aren't live rather than half-failing at the till.
    stripe_secret_key: str | None = None
    stripe_publishable_key: str | None = None
    # Verifies POSTs to /webhooks/stripe actually came from Stripe (HMAC
    # over the raw body, see storefront/checkout.py:verify_stripe_signature).
    # Created in the Stripe dashboard once the webhook endpoint has a real
    # public URL to register -- unset, the endpoint just 503s.
    stripe_webhook_secret: str | None = None
    # Public-shop identity and reviewed legal wording. These stay empty on
    # purpose: inventing an ABN, entity name or tax status would be worse
    # than visibly blocking launch. `storefront_legal_approved` is the
    # owner's explicit sign-off after the completed text has been reviewed.
    storefront_business_name: str | None = None
    storefront_business_abn: str | None = None
    storefront_contact_email: str | None = None
    storefront_gst_statement: str | None = None
    storefront_retention_period: str | None = None
    storefront_legal_approved: bool = False
    # "shop" runs the cart and checkout; "catalogue" is display-only and sends
    # buyers to eBay/Vinted/Whatnot instead (see storefront/catalogue.py).
    storefront_mode: str = "shop"
    # Comma-separated department slugs shown as "coming soon": the tile and
    # page stay, but their items are hidden everywhere (see
    # storefront/departments.py coming_soon_slugs). e.g. "collectibles".
    storefront_coming_soon: str = ""
    anthropic_api_key: str | None = None
    # Reverse image search for a captured collectible photo (see
    # reverse_image_search.py) -- a real Google Lens actor run on Apify's
    # cloud, not a local tool. Unset, that button just 503s with a clear
    # message rather than pretending the feature doesn't exist.
    apify_token: str | None = None
    apify_google_lens_actor_id: str = "borderline~google-lens"
    captured_cards_dir: str = "captured_cards"
    # Cards enter as a batch (phone photo batch or flatbed sheet), so every
    # card follows the same review/pricing/listing handoff.  The legacy
    # one-photo endpoint remains behind this switch for recovery only.
    card_bulk_upload_only: bool = True
    pokemontcg_api_key: str | None = None
    # pokemonpricetracker.com -- real TCGPlayer market + eBay sold-comp
    # pricing per specific printing, not pokemontcg.io's snapshot data (see
    # scan_ingest/pricing.py's docstring: flagged unreliable, twice).
    pokemonpricetracker_api_key: str | None = None
    # Multi-source pricing is deliberately free-by-default. A provider call
    # carrying a non-zero estimated cost is rejected unless this explicit
    # kill switch is enabled; configuring an API key alone cannot spend.
    allow_paid_pricing: bool = False
    pricing_ebay_daily_budget: int = 300
    pricing_active_cache_hours: int = 12
    pricing_high_value_cache_hours: int = 24
    pricing_standard_cache_days: int = 7
    pricing_bulk_cache_days: int = 30
    # Bulk scan ingest (automation_control/scan_ingest/). watch_dir is the
    # folder scanned sheets land in -- shared to the scanning PC over Samba
    # -- and the rest are local working directories the CLI manages itself.
    scan_watch_dir: str = "scan_ingest_data/watch"
    scan_archive_dir: str = "scan_ingest_data/archive"
    scan_media_dir: str = "scan_ingest_data/media"
    scan_cache_dir: str = "scan_ingest_data/cache"
    # Maximum pHash Hamming distance still counted as a match. 0 is
    # identical; a scan of the same card typically lands in the single
    # digits, while a different card in the same set is usually well above
    # 20. Anything above this goes to the manual-confirmation queue rather
    # than being guessed at.
    scan_phash_max_distance: int = 12
    # A count above this can only be over-fragmentation -- the physical bed
    # never holds more than this many cards at once -- so it's checked
    # unconditionally on every sheet regardless of layout, unlike
    # --expected-count (an exact match, which every smaller or
    # partly-filled sheet would otherwise fail).
    scan_max_cards_per_sheet: int = 9
    # Where the background scan-ingest service (scan-ingest.service) writes
    # its stdout/stderr, per the unit's StandardOutput=append: -- the status
    # page tails this rather than shelling out to journalctl.
    scan_log_file: str = "scan_ingest_data/logs/scan-ingest.log"
    r2_endpoint_url: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket_name: str | None = None

    @property
    def ebay_callback_url(self) -> str | None:
        if not self.app_public_base_url:
            return None
        return f"{self.app_public_base_url.rstrip('/')}/auth/ebay/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()
