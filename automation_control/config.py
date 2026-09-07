from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    automation_database_url: str = "sqlite:///./automation.db"
    grocery_base_url: str = "http://grocery-web:8000"
    docker_broker_url: str = "http://docker-broker:8081"
    app_bind_host: str = "127.0.0.1"
    app_bind_port: int = 8080
    app_public_base_url: str | None = None
    dashboard_username: str | None = None
    dashboard_password_hash: str | None = None
    session_signing_key: str | None = None
    ebay_token_encryption_key: str | None = None
    # "production" unlocks the api.ebay.com / auth.ebay.com endpoints and the
    # matching credential validators in ebay_oauth.py -- it does NOT unlock
    # any write capability (listing_pipeline/publish.py is still a documented
    # placeholder, unimplemented and unwired). Defaults to sandbox; this app
    # never assumes production on your behalf.
    ebay_env: Literal["sandbox", "production"] = "sandbox"
    ebay_client_id: str | None = None
    ebay_client_secret: str | None = None
    ebay_runame: str | None = None
    listing_pipeline_output_dir: str = "listing_pipeline_data/output"
    listing_pipeline_config_path: str = "listing_pipeline_data/config.toml"
    anthropic_api_key: str | None = None
    captured_cards_dir: str = "captured_cards"
    pokemontcg_api_key: str | None = None
    # pokemonpricetracker.com -- real TCGPlayer market + eBay sold-comp
    # pricing per specific printing, not pokemontcg.io's snapshot data (see
    # scan_ingest/pricing.py's docstring: flagged unreliable, twice).
    pokemonpricetracker_api_key: str | None = None
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
