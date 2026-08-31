"""Conservative SDK classification; never persist exception text or response bodies."""

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from aiohttp import ClientConnectionError
from binance.exceptions import BinanceAPIException
from oandapyV20.exceptions import V20Error
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeout

from app.exchanges.binance import BinanceAuthenticationError
from app.exchanges.oanda import OandaAuthenticationError
from app.services.market_data import MarketDataAccessError


@dataclass(frozen=True)
class PageFailure:
    code: str
    retryable: bool = False
    retry_after: float = 0


def classify_failure(exc: Exception) -> PageFailure:
    if isinstance(exc, (BinanceAuthenticationError, OandaAuthenticationError)):
        return PageFailure("authentication_failed")
    if isinstance(exc, MarketDataAccessError):
        allowed = {
            "access_unavailable",
            "credentials_missing",
            "credentials_unreadable",
            "invalid_candles",
            "access_changed",
            "invalid_checkpoint",
        }
        return PageFailure(exc.code if exc.code in allowed else "configuration_error")
    current: BaseException | None = exc
    for _ in range(5):
        if isinstance(
            current, (TimeoutError, RequestsTimeout, RequestsConnectionError, ClientConnectionError)
        ):
            return PageFailure("communication_failed", True)
        status = None
        if isinstance(current, BinanceAPIException):
            status = current.status_code
        elif isinstance(current, V20Error):
            status = current.code
        if status in (401, 403):
            return PageFailure("authentication_failed")
        if status == 429:
            response = getattr(current, "response", None)
            headers = getattr(response, "headers", {}) or {}
            raw = headers.get("Retry-After", "")
            try:
                delay = float(raw)
                if not 0 <= delay < float("inf"):
                    delay = 0
            except (TypeError, ValueError):
                try:
                    delay = max(0, (parsedate_to_datetime(raw) - datetime.now(UTC)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    delay = 0
            # An unreasonable server delay must not overflow checkpoint persistence.
            if delay > 365 * 86400:
                return PageFailure("rate_limited")
            return PageFailure("rate_limited", True, delay)
        if isinstance(status, int) and 500 <= status <= 599:
            return PageFailure("communication_failed", True)
        current = current.__cause__ if current else None
    return PageFailure("internal_error")
