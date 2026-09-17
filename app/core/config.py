from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "AI System Trading API"
    app_env: str = "local"
    api_v1_prefix: str = "/api/v1"
    database_url: str = (
        "postgresql+psycopg://trade_bot_user:change-me@localhost:5432/general_system_db"
    )
    database_schema: str = "fx"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    dev_owner_token: SecretStr | None = None
    secret_encryption_key: SecretStr | None = None
    secret_store_path: Path = Path(".secrets")
    trading_env: str = "paper"
    live_trading_allowed: bool = False
    worker_scan_interval_seconds: float = 2
    worker_lease_seconds: int = 90
    worker_heartbeat_interval_seconds: float = 15
    worker_fetch_timeout_seconds: float = 30
    worker_candidate_limit: int = 500
    worker_recover_limit: int = 100
    market_stream_ticket_secret: SecretStr | None = None
    market_stream_ticket_ttl_seconds: int = 60
    market_stream_grace_period_seconds: float = 30.0
    market_stream_heartbeat_interval_seconds: float = 30.0
    log_dir: Path = Path("logs")
    log_level: str = "INFO"
    log_rotation_max_bytes: int = 10 * 1024 * 1024
    log_rotation_backup_count: int = 5

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
