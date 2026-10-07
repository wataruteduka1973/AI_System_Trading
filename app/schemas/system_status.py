from datetime import datetime
from uuid import UUID

from app.schemas.base import OrmModel


class TradingWorkerStatusRead(OrmModel):
    status: str
    active_bots: int
    last_heartbeat_age_seconds: int | None
    stalled_bots: list[str]
    stale_after_seconds: int


class MarketDataWorkerStatusRead(OrmModel):
    status: str
    enabled_subscriptions: int
    blocked_subscriptions: int
    overdue_subscriptions: int
    most_overdue_seconds: int | None
    overdue_after_seconds: int


class NotificationStatusRead(OrmModel):
    pending_events: int
    oldest_pending_age_seconds: int | None
    failed_events_last_7_days: int


class ConnectionStatusRead(OrmModel):
    id: UUID
    label: str
    environment: str
    status: str
    verification_outcome: str
    last_verified_at: datetime | None


class SystemStatusRead(OrmModel):
    checked_at: datetime
    overall: str
    """`ok`, or `attention` when `problems` is not empty."""
    problems: list[str]
    trading_worker: TradingWorkerStatusRead
    market_data_worker: MarketDataWorkerStatusRead
    notifications: NotificationStatusRead
    halts: dict[str, int]
    """Active halts by level."""
    bots: dict[str, int]
    """Bots by `actual_state`."""
    connections: list[ConnectionStatusRead]
