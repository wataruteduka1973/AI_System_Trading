"""The two Risk Gate limits that can lock a bot out for good: consecutive losses and the
peak drawdown (docs/plans/lock-halts.md).

Both are measured against history that a bot which has stopped trading never changes: the
losing streak is the latest run of losing trades in the ledger, the peak is the best equity ever
recorded. Once either passes its limit the Risk Gate denies every entry, no entry means no new
trade or equity high, and nothing ever clears it -- silently, with only `risk_decision` rows to
show for it. (The daily and weekly loss limits reset with the calendar and are not part of this.)

So each is a `trading_halt`, like the other stopping causes: visible, announced to the workspace's
Owners and Operators, and released by an Owner. Release is **direct** (`trading_halt.release_lock`,
not the one-level-at-a-time step down) and it **starts the count over**: the streak counts only
trades closed after the release, the peak only equity recorded after it. A halt never relaxes by
itself -- recovering on paper would not make the strategy trustworthy again; a person decides.

`sync_lock_halts` runs once per new bar for each active bot (`bot_evaluation`), so the halt
appears when the limit is crossed -- usually as the losing trade closes -- not at the next entry
signal, which may be weeks away.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.instruments import Instrument
from app.models.strategy import RiskProfileVersion, TradingBot
from app.models.trading import TradingAccount
from app.trading.application import risk_gate, trading_halt
from app.trading.application.account_valuation import value_account

CONSECUTIVE_LOSS_REASON = "consecutive_loss_limit"
PEAK_DRAWDOWN_REASON = "peak_drawdown_limit"
LOCK_REASONS = frozenset({CONSECUTIVE_LOSS_REASON, PEAK_DRAWDOWN_REASON})


@dataclass(frozen=True)
class LockReading:
    """What the two measures say right now, counted from each lock's last release."""

    consecutive_losses: int
    peak_drawdown_pct: Decimal | None
    """`None` when there is no equity high to measure from (no snapshot yet)."""


def account_scope(workspace_id: UUID, account_id: UUID) -> trading_halt.HaltScope:
    return trading_halt.HaltScope(workspace_id, "account", account_id)


def read_locks(
    db: Session,
    account: TradingAccount,
    *,
    consecutive_loss_since: datetime | None,
    peak_since: datetime | None,
    equity: Decimal,
) -> LockReading:
    peak = risk_gate._peak_equity(db, account, since=peak_since)
    drawdown = max(Decimal(0), (peak - equity) / peak) if peak is not None and peak > 0 else None
    return LockReading(
        consecutive_losses=risk_gate._consecutive_losses(db, account, since=consecutive_loss_since),
        peak_drawdown_pct=drawdown,
    )


def sync_lock_halts(
    db: Session,
    bot: TradingBot,
    account: TradingAccount,
    instrument: Instrument,
    risk_profile_version: RiskProfileVersion,
) -> list[str]:
    """Activates a lock halt for each limit that has been crossed, in the caller's transaction.
    Returns the reasons activated now (already-active ones are left alone). The limits are the
    risk profile's *initial* thresholds -- where the Risk Gate starts denying entries."""
    rules = risk_profile_version.rules
    scope = account_scope(bot.workspace_id, account.id)
    consecutive_loss_since = trading_halt.last_release_time(db, scope, CONSECUTIVE_LOSS_REASON)
    peak_since = trading_halt.last_release_time(db, scope, PEAK_DRAWDOWN_REASON)
    equity = value_account(db, account, instrument).equity
    reading = read_locks(
        db,
        account,
        consecutive_loss_since=consecutive_loss_since,
        peak_since=peak_since,
        equity=equity,
    )

    activated: list[str] = []
    loss_limit = int(rules["consecutive_loss_limit"])
    if reading.consecutive_losses > loss_limit and _activate(
        db,
        scope,
        CONSECUTIVE_LOSS_REASON,
        message=(
            f"{reading.consecutive_losses}連敗し、上限({loss_limit}連敗)を超えたため、"
            "新規の取引を止めました。Ownerが解除するまで再開しません"
        ),
        payload={
            "consecutive_losses": reading.consecutive_losses,
            "limit": loss_limit,
            "bot_id": str(bot.id),
            "bot_name": bot.name,
        },
    ):
        activated.append(CONSECUTIVE_LOSS_REASON)
    drawdown_limit = risk_gate._decimal(rules, "peak_drawdown_limit")
    if (
        reading.peak_drawdown_pct is not None
        and reading.peak_drawdown_pct > drawdown_limit
        and _activate(
            db,
            scope,
            PEAK_DRAWDOWN_REASON,
            message=(
                f"資産が最高値から{reading.peak_drawdown_pct * 100:.1f}%下がり、"
                f"上限({drawdown_limit * 100:.0f}%)を超えたため、新規の取引を止めました。"
                "Ownerが解除するまで再開しません"
            ),
            payload={
                "peak_drawdown_pct": str(reading.peak_drawdown_pct),
                "limit": str(drawdown_limit),
                "bot_id": str(bot.id),
                "bot_name": bot.name,
            },
        )
    ):
        activated.append(PEAK_DRAWDOWN_REASON)
    return activated


def _activate(
    db: Session,
    scope: trading_halt.HaltScope,
    reason_code: str,
    *,
    message: str,
    payload: dict[str, object],
) -> bool:
    """True when this call created the halt (it is announced then, once)."""
    if trading_halt.find_active_halt(db, scope, reason_code) is not None:
        return False
    trading_halt.activate_or_escalate(
        db,
        scope,
        reason_code=reason_code,
        level="entry_halted",
        auto_releasable=False,
        event=trading_halt.HaltEvent(
            event_type=f"trading_halt.{reason_code}", message=message, payload=payload
        ),
    )
    return True
