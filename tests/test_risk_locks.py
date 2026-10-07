"""The losing-streak and peak-drawdown lock halts (app/trading/application/risk_locks.py,
docs/plans/lock-halts.md)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.models.audit import SystemEvent
from app.models.strategy import RiskProfileVersion, TradingBot, TradingHalt
from app.models.trading import TradingAccount
from app.trading.application import risk_gate, risk_locks, trading_halt
from app.trading.application.risk_gate import CONSERVATIVE_V1_RULES

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def _setup(monkeypatch, *, losses=0, peak="1000000", equity="1000000", releases=None):
    """`releases` maps a lock reason to the time it was last released."""
    releases = releases or {}
    bot = TradingBot(id=uuid4(), workspace_id=uuid4(), name="btcusdt-4h")
    account = TradingAccount(id=uuid4(), workspace_id=bot.workspace_id)
    profile = RiskProfileVersion(id=uuid4(), rules=dict(CONSERVATIVE_V1_RULES))
    db = MagicMock()
    db.scalar.return_value = None  # no halt row exists yet
    calls = {}

    def last_release(_db, scope, reason):
        return releases.get(reason)

    def consecutive(_db, _account, since=None):
        calls["consecutive_since"] = since
        return losses

    def peak_equity(_db, _account, since=None):
        calls["peak_since"] = since
        return Decimal(peak) if peak is not None else None

    monkeypatch.setattr(trading_halt, "last_release_time", last_release)
    monkeypatch.setattr(risk_gate, "_consecutive_losses", consecutive)
    monkeypatch.setattr(risk_gate, "_peak_equity", peak_equity)
    monkeypatch.setattr(risk_locks, "value_account", lambda *_a: MagicMock(equity=Decimal(equity)))
    monkeypatch.setattr(trading_halt, "find_active_halt", lambda *_a: None)
    return db, bot, account, profile, calls


def _sync(db, bot, account, profile):
    return risk_locks.sync_lock_halts(db, bot, account, MagicMock(), profile)


def _added(db, kind):
    return [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], kind)]


def test_four_losses_in_a_row_lock_the_account_and_announce_it(monkeypatch) -> None:
    db, bot, account, profile, _ = _setup(monkeypatch, losses=4)

    activated = _sync(db, bot, account, profile)

    assert activated == ["consecutive_loss_limit"]
    (halt,) = _added(db, TradingHalt)
    assert (halt.scope_type, halt.scope_id) == ("account", account.id)
    assert (halt.level, halt.reason_code, halt.auto_releasable) == (
        "entry_halted",
        "consecutive_loss_limit",
        False,
    )
    (event,) = _added(db, SystemEvent)
    assert event.event_type == "trading_halt.consecutive_loss_limit"
    assert "4連敗し、上限(3連敗)を超えた" in event.message
    assert event.payload["consecutive_losses"] == 4 and event.payload["bot_name"] == "btcusdt-4h"


def test_a_streak_at_the_limit_is_not_a_lock(monkeypatch) -> None:
    """The Risk Gate denies from the fourth loss on (more than the limit of 3), so the lock
    starts at the same point."""
    db, bot, account, profile, _ = _setup(monkeypatch, losses=3)

    assert _sync(db, bot, account, profile) == []

    db.add.assert_not_called()


def test_a_drawdown_past_five_percent_locks_the_account(monkeypatch) -> None:
    db, bot, account, profile, _ = _setup(monkeypatch, peak="1000000", equity="940000")

    activated = _sync(db, bot, account, profile)

    assert activated == ["peak_drawdown_limit"]
    (event,) = _added(db, SystemEvent)
    assert "6.0%" in event.message and "上限(5%)" in event.message
    assert event.payload["limit"] == "0.05"


def test_a_drawdown_at_the_limit_or_without_a_peak_is_not_a_lock(monkeypatch) -> None:
    at_limit = _setup(monkeypatch, peak="1000000", equity="950000")
    assert _sync(*at_limit[:4]) == []

    no_peak = _setup(monkeypatch, peak=None, equity="1")
    assert _sync(*no_peak[:4]) == []


def test_both_limits_can_lock_at_once(monkeypatch) -> None:
    db, bot, account, profile, _ = _setup(monkeypatch, losses=5, equity="900000")

    assert _sync(db, bot, account, profile) == ["consecutive_loss_limit", "peak_drawdown_limit"]

    assert len(_added(db, TradingHalt)) == 2


def test_an_active_lock_is_not_announced_again(monkeypatch) -> None:
    db, bot, account, profile, _ = _setup(monkeypatch, losses=6)
    monkeypatch.setattr(trading_halt, "find_active_halt", lambda *_a: MagicMock())

    assert _sync(db, bot, account, profile) == []

    db.add.assert_not_called()


def test_each_measure_counts_from_its_own_locks_last_release(monkeypatch) -> None:
    released_streak = NOW - timedelta(days=3)
    released_peak = NOW - timedelta(days=9)
    db, bot, account, profile, calls = _setup(
        monkeypatch,
        releases={
            "consecutive_loss_limit": released_streak,
            "peak_drawdown_limit": released_peak,
        },
    )

    _sync(db, bot, account, profile)

    assert calls == {"consecutive_since": released_streak, "peak_since": released_peak}


def test_the_lock_is_for_the_account_the_ledger_belongs_to(monkeypatch) -> None:
    db, bot, account, profile, _ = _setup(monkeypatch, losses=4)
    scopes = []
    original = trading_halt.activate_or_escalate
    monkeypatch.setattr(
        trading_halt,
        "activate_or_escalate",
        lambda db_, scope, **kw: scopes.append(scope) or original(db_, scope, **kw),
    )

    _sync(db, bot, account, profile)

    assert scopes == [trading_halt.HaltScope(bot.workspace_id, "account", account.id)]


@pytest.mark.parametrize("reason", sorted(risk_locks.LOCK_REASONS))
def test_a_lock_is_released_in_one_step_and_starts_the_count_over(reason: str) -> None:
    halt = TradingHalt(
        id=uuid4(),
        workspace_id=uuid4(),
        scope_type="account",
        scope_id=uuid4(),
        level="entry_halted",
        reason_code=reason,
        status="active",
    )
    owner = uuid4()
    db = MagicMock()

    released = trading_halt.release_lock(db, halt, released_by=owner, now=NOW)

    assert (released.status, released.released_at, released.released_by) == ("released", NOW, owner)
    assert released.level == "entry_halted"  # not stepped down to `warning` and left active


def test_the_last_release_time_comes_from_the_most_recent_released_halt() -> None:
    scope = trading_halt.HaltScope(uuid4(), "account", uuid4())
    db = MagicMock()
    db.scalar.return_value = TradingHalt(status="released", released_at=NOW)
    assert trading_halt.last_release_time(db, scope, "consecutive_loss_limit") == NOW

    db.scalar.return_value = None
    assert trading_halt.last_release_time(db, scope, "consecutive_loss_limit") is None


def test_the_ledger_and_snapshot_queries_are_bounded_by_the_release() -> None:
    account = TradingAccount(id=uuid4())
    db = MagicMock()
    db.scalars.return_value.all.return_value = []

    risk_gate._consecutive_losses(db, account, since=NOW)
    risk_gate._peak_equity(db, account, since=NOW)
    risk_gate._consecutive_losses(db, account)
    risk_gate._peak_equity(db, account)

    sql = [str(call.args[0]) for call in db.scalars.call_args_list + db.scalar.call_args_list]
    assert "ledger_entry.occurred_at >" in sql[0]
    assert "account_snapshot.captured_at >=" in str(db.scalar.call_args_list[0].args[0])
    assert "occurred_at >" not in sql[1]  # without `since`, the whole history
    assert "captured_at >=" not in str(db.scalar.call_args_list[1].args[0])


def test_a_streak_ignores_trades_before_the_release() -> None:
    """Counting stops at the release: the losses before it are not this bot's streak any more."""
    account = TradingAccount(id=uuid4())
    db = MagicMock()
    db.scalars.return_value.all.return_value = [Decimal(-1), Decimal(-2)]  # after the release

    assert risk_gate._consecutive_losses(db, account, since=NOW) == 2
