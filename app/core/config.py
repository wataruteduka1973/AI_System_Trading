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
    # Horizon5 Group A (docs/plans/horizon5-implementation-plan.md Unit 3): OIDC
    # Authorization Code + PKCE against an external IdP the customer provides.
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_redirect_uri: str = "http://localhost:8000/api/v1/auth/callback"
    oidc_scopes: str = "openid email profile"
    session_signing_secret: SecretStr | None = None
    session_ttl_seconds: int = 8 * 60 * 60
    # Horizon5 Group D (docs/plans/horizon5-implementation-plan.md Unit 8): generic
    # SMTP notification delivery. Unconfigured by default -- the Notification
    # Worker refuses to start until smtp_host/smtp_sender_address are set.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_use_tls: bool = True
    smtp_sender_address: str | None = None
    notification_poll_interval_seconds: float = 5.0
    # Horizon 3 execution loop (docs/architecture-alignment-and-long-term-roadmap.md,
    # 2026-09-25 "Bot管理API -> 実行ループ/Worker -> 最低限のUI"順): how often the
    # Bot execution Worker re-evaluates every running/paused bot. Safe to poll faster
    # than any bot's own candle interval -- see dummy_pipeline.run_dummy_pipeline_once's
    # idempotency guard.
    bot_execution_poll_interval_seconds: float = 5.0

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
