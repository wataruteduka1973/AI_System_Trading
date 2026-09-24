"""Unit A (docs/plans/trading-halt-mvp.md): the level/status state machine,
exercised against a MagicMock Session (matching this codebase's existing
convention -- see e.g. tests/test_bot_lifecycle.py).
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import structlog.testing
from app.models.strategy import TradingHalt
from app.trading.application import trading_halt as halt


def _scope(**overrides: object) -> halt.HaltScope:
    defaults: dict[str, object] = dict(workspace_id=uuid4(), scope_type="bot", scope_id=uuid4())
    defaults.update(overrides)
    return halt.HaltScope(**defaults)


def _existing_halt(scope: halt.HaltScope, *, level: str, reason_code: str) -> TradingHalt:
    return TradingHalt(
        id=uuid4(),
        workspace_id=scope.workspace_id,
        scope_type=scope.scope_type,
        scope_id=scope.scope_id,
        level=level,
        reason_code=reason_code,
        status="active",
        auto_releasable=True,
    )


# ---- activate_or_escalate ----


def test_activate_creates_a_new_row_when_none_exists() -> None:
    db = MagicMock()
    db.scalar.return_value = None
    scope = _scope()

    result = halt.activate_or_escalate(db, scope, reason_code="data_delay", level="entry_halted")

    db.add.assert_called_once()
    added = db.add.call_args[0][0]
    assert isinstance(added, TradingHalt)
    assert added.level == "entry_halted"
    assert added.status == "active"
    assert result is added


def test_activate_escalates_an_existing_less_severe_row() -> None:
    db = MagicMock()
    existing = _existing_halt(_scope(), level="warning", reason_code="data_delay")
    db.scalar.return_value = existing

    result = halt.activate_or_escalate(db, _scope(), reason_code="data_delay", level="entry_halted")

    assert result is existing
    assert existing.level == "entry_halted"
    db.add.assert_not_called()  # rewrites the same row, never adds a second one


def test_activate_does_not_downgrade_a_more_severe_existing_row() -> None:
    db = MagicMock()
    existing = _existing_halt(_scope(), level="all_trading_halted", reason_code="data_delay")
    db.scalar.return_value = existing

    result = halt.activate_or_escalate(db, _scope(), reason_code="data_delay", level="entry_halted")

    assert result is existing
    assert existing.level == "all_trading_halted"  # unchanged


def _released_halt(
    scope: halt.HaltScope, *, level: str, reason_code: str, released_by: object
) -> TradingHalt:
    return TradingHalt(
        id=uuid4(),
        workspace_id=scope.workspace_id,
        scope_type=scope.scope_type,
        scope_id=scope.scope_id,
        level=level,
        reason_code=reason_code,
        status="released",
        auto_releasable=True,
        released_at=datetime.now(UTC),
        released_by=released_by,
    )


def test_activate_warns_when_reactivating_after_a_manual_release() -> None:
    """Regression test (/code-review finding): the state machine did not
    distinguish a fresh activation following a manual `/release` from any
    other fresh activation -- an Owner's override could be silently undone on
    the very next signal with no record of it. `db.scalar`'s two calls here
    are `_find_active_halt` (none -- this is a genuinely new activation) then
    `most_recently_released_halt` (the manually-released row)."""
    scope = _scope()
    released_by = uuid4()
    released = _released_halt(
        scope, level="warning", reason_code="data_delay", released_by=released_by
    )
    db = MagicMock()
    db.scalar.side_effect = [None, released]

    with structlog.testing.capture_logs() as logs:
        halt.activate_or_escalate(db, scope, reason_code="data_delay", level="entry_halted")

    warnings = [
        log for log in logs if log.get("event") == "trading_halt_reactivated_after_manual_release"
    ]
    assert len(warnings) == 1
    assert warnings[0]["released_by"] == str(released_by)


def test_activate_does_not_warn_when_reactivating_after_an_automatic_release() -> None:
    """The counterpart to the test above: `released_by=None` means the prior
    release was `_sync_trading_halts`'s own automatic recovery, not a human
    override -- routine re-breaching does not need a warning."""
    scope = _scope()
    released = _released_halt(scope, level="warning", reason_code="data_delay", released_by=None)
    db = MagicMock()
    db.scalar.side_effect = [None, released]

    with structlog.testing.capture_logs() as logs:
        halt.activate_or_escalate(db, scope, reason_code="data_delay", level="entry_halted")

    warnings = [
        log for log in logs if log.get("event") == "trading_halt_reactivated_after_manual_release"
    ]
    assert warnings == []


def test_activate_does_not_warn_when_escalating_an_already_active_halt() -> None:
    """No false positive: escalating a halt that never stopped being active
    (the common case) must not consult `most_recently_released_halt` at all --
    only `existing is None` (a genuinely fresh activation) does."""
    db = MagicMock()
    existing = _existing_halt(_scope(), level="warning", reason_code="data_delay")
    db.scalar.return_value = existing  # _find_active_halt finds it immediately

    with structlog.testing.capture_logs() as logs:
        halt.activate_or_escalate(db, _scope(), reason_code="data_delay", level="entry_halted")

    assert logs == []


# ---- most_recently_released_halt ----


def test_most_recently_released_halt_returns_none_when_none_exists() -> None:
    db = MagicMock()
    db.scalar.return_value = None
    result = halt.most_recently_released_halt(db, _scope(), "data_delay")
    assert result is None


# ---- deescalate_one_step ----


def test_deescalate_returns_none_when_no_active_halt_exists() -> None:
    db = MagicMock()
    db.scalar.return_value = None
    result = halt.deescalate_one_step(db, _scope(), reason_code="data_delay", now=datetime.now(UTC))
    assert result is None


def test_deescalate_steps_entry_halted_down_to_warning() -> None:
    db = MagicMock()
    existing = _existing_halt(_scope(), level="entry_halted", reason_code="data_delay")
    db.scalar.return_value = existing

    result = halt.deescalate_one_step(db, _scope(), reason_code="data_delay", now=datetime.now(UTC))

    assert result is existing
    assert existing.level == "warning"
    assert existing.status == "active"  # still active, not released yet


def test_deescalate_steps_all_trading_halted_down_to_entry_halted() -> None:
    db = MagicMock()
    existing = _existing_halt(_scope(), level="all_trading_halted", reason_code="data_delay")
    db.scalar.return_value = existing

    halt.deescalate_one_step(db, _scope(), reason_code="data_delay", now=datetime.now(UTC))

    assert existing.level == "entry_halted"


def test_deescalate_releases_a_warning_level_halt() -> None:
    db = MagicMock()
    existing = _existing_halt(_scope(), level="warning", reason_code="data_delay")
    db.scalar.return_value = existing
    now = datetime.now(UTC)

    result = halt.deescalate_one_step(db, _scope(), reason_code="data_delay", now=now)

    assert result is existing
    assert existing.status == "released"
    assert existing.released_at == now
    assert existing.level == "warning"  # level itself is not touched on release
    assert existing.released_by is None  # no released_by passed -> automatic recovery


def test_deescalate_records_released_by_when_provided() -> None:
    """`app/api/routes/trading_halts.py`'s manual `/release` endpoint passes the
    calling Owner's id here -- confirms it actually lands on the row, since
    `most_recently_released_halt`'s manual-vs-automatic distinction depends on
    it (/code-review finding)."""
    db = MagicMock()
    existing = _existing_halt(_scope(), level="warning", reason_code="data_delay")
    db.scalar.return_value = existing
    owner_id = uuid4()

    result = halt.deescalate_one_step(
        db, _scope(), reason_code="data_delay", now=datetime.now(UTC), released_by=owner_id
    )

    assert result is existing
    assert existing.released_by == owner_id


def test_deescalate_does_not_touch_emergency_stopped() -> None:
    db = MagicMock()
    existing = _existing_halt(_scope(), level="emergency_stopped", reason_code="manual")
    db.scalar.return_value = existing

    result = halt.deescalate_one_step(db, _scope(), reason_code="manual", now=datetime.now(UTC))

    assert result is existing
    assert existing.level == "emergency_stopped"
    assert existing.status == "active"


# ---- release_emergency_stop ----


def test_release_emergency_stop_releases_directly() -> None:
    db = MagicMock()
    existing = _existing_halt(_scope(), level="emergency_stopped", reason_code="manual")
    owner_id = uuid4()
    now = datetime.now(UTC)

    result = halt.release_emergency_stop(db, existing, released_by=owner_id, now=now)

    assert result is existing
    assert existing.status == "released"
    assert existing.released_at == now
    assert existing.released_by == owner_id


def test_release_emergency_stop_rejects_a_non_emergency_halt() -> None:
    db = MagicMock()
    existing = _existing_halt(_scope(), level="entry_halted", reason_code="data_delay")
    with pytest.raises(halt.TradingHaltError) as exc:
        halt.release_emergency_stop(db, existing, released_by=uuid4(), now=datetime.now(UTC))
    assert exc.value.code == "not_emergency_stopped"


# ---- has_active_halt_at_or_above ----


def test_has_active_halt_at_or_above_true_when_severity_meets_threshold() -> None:
    db = MagicMock()
    scope = _scope()
    db.scalars.return_value.all.return_value = [
        _existing_halt(scope, level="entry_halted", reason_code="data_delay")
    ]
    assert halt.has_active_halt_at_or_above(db, scope, min_level="entry_halted") is True
    assert halt.has_active_halt_at_or_above(db, scope, min_level="all_trading_halted") is False


def test_has_active_halt_at_or_above_false_when_no_active_rows() -> None:
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    assert halt.has_active_halt_at_or_above(db, _scope(), min_level="warning") is False
