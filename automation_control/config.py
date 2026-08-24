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
    ebay_env: Literal["sandbox"] = "sandbox"
    ebay_client_id: str | None = None
    ebay_client_secret: str | None = None
    ebay_runame: str | None = None
    listing_pipeline_output_dir: str = "listing_pipeline_data/output"
    anthropic_api_key: str | None = None
    captured_cards_dir: str = "captured_cards"
    pokemontcg_api_key: str | None = None

    @property
    def ebay_callback_url(self) -> str | None:
        if not self.app_public_base_url:
            return None
        return f"{self.app_public_base_url.rstrip('/')}/auth/ebay/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()
