"""`trading_halt.level`/`status` state machine (docs/plans/trading-halt-mvp.md,
ADR 0004). Implements the `level` transitions from
`docs/concept/FXtrading_rebuild/05_アーキテクチャと移行計画.md`'s "停止レベルの
状態遷移表" (220-229行目): activation, escalation, the two-step de-escalation
safety valve, and `emergency_stopped`'s Owner-only direct-release exception.

**`status` uses only `active`/`released`, never `release_pending`** (ADR 0004): the
design doc itself flagged `release_pending` as undecided
(05番236-259行目); this MVP resolves that by not using it at all. `released_at` is
therefore only ever set at the exact moment `status` becomes `released`, matching
the DB's `ck_trading_halt_release` CHECK constraint trivially (no intermediate
`release_pending` window to reason about).

**"1つの原因につき`trading_halt`行は1つ"** (05番 決定2): every function here looks
up the existing *active* row for `(workspace_id, scope_type, scope_id, reason_code)`
before creating a new one, and escalation/de-escalation always rewrites that same
row's `level` rather than adding another.

**Which causes call this module at all** is deliberately narrow per ADR 0004: only
`risk_gate.evaluate_signal` (data delay, daily/weekly loss, peak drawdown) does, for
now. The other 8 causes in 05番's "取引停止マトリクス" have no detection code yet and
are out of scope here.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.strategy import TradingHalt

logger = structlog.get_logger(__name__)

_LEVEL_SEVERITY = {
    "warning": 1,
    "entry_halted": 2,
    "all_trading_halted": 3,
    "emergency_stopped": 4,
}

_ONE_STEP_DOWN: dict[str, str | None] = {
    "emergency_stopped": None,  # never stepped down by this function -- see release_emergency_stop
    "all_trading_halted": "entry_halted",
    "entry_halted": "warning",
    "warning": None,  # one step below warning is "released", not another level
}


class TradingHaltError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class HaltScope:
    workspace_id: UUID
    scope_type: str
    scope_id: UUID


def _find_active_halt(db: Session, scope: HaltScope, reason_code: str) -> TradingHalt | None:
    return db.scalar(
        select(TradingHalt).where(
            TradingHalt.workspace_id == scope.workspace_id,
            TradingHalt.scope_type == scope.scope_type,
            TradingHalt.scope_id == scope.scope_id,
            TradingHalt.reason_code == reason_code,
            TradingHalt.status == "active",
        )
    )


def activate_or_escalate(
    db: Session,
    scope: HaltScope,
    *,
    reason_code: str,
    level: str,
    auto_releasable: bool = True,
) -> TradingHalt:
    """Create a new active halt at `level`, or -- if an active halt already exists
    for `(scope, reason_code)` -- escalate it to `level` only if `level` is *more*
    severe than its current one (05番 決定2: rewrite the same row, never add a
    second one for the same cause). A `level` no more severe than the existing one
    is a no-op; use `deescalate_one_step` to relax a halt, never this function.

    `existing is None` is also, necessarily, the exact moment a *reactivation*
    would happen: the only way `(scope, reason_code)` can have no active row is
    that it never had one, or its last one was released. /code-review finding:
    if that release was a manual `/release` call (`released_by is not None` --
    see `deescalate_one_step`), creating a fresh halt here silently overrides an
    Owner's judgment call on the very next signal, with no record of it having
    happened. This does not suppress the reactivation (the breach is still real
    and conservative-v1's numbers still apply) -- it logs it, so the override is
    at least visible after the fact."""
    existing = _find_active_halt(db, scope, reason_code)
    if existing is None:
        last_release = most_recently_released_halt(db, scope, reason_code)
        if last_release is not None and last_release.released_by is not None:
            logger.warning(
                "trading_halt_reactivated_after_manual_release",
                workspace_id=str(scope.workspace_id),
                scope_type=scope.scope_type,
                scope_id=str(scope.scope_id),
                reason_code=reason_code,
                level=level,
                released_by=str(last_release.released_by),
                released_at=(
                    last_release.released_at.isoformat() if last_release.released_at else None
                ),
            )
        halt = TradingHalt(
            workspace_id=scope.workspace_id,
            scope_type=scope.scope_type,
            scope_id=scope.scope_id,
            level=level,
            reason_code=reason_code,
            auto_releasable=auto_releasable,
            status="active",
        )
        db.add(halt)
        db.flush()
        db.refresh(halt)
        return halt

    if _LEVEL_SEVERITY[level] > _LEVEL_SEVERITY[existing.level]:
        existing.level = level
        db.flush()
        db.refresh(existing)
    return existing


def deescalate_one_step(
    db: Session,
    scope: HaltScope,
    *,
    reason_code: str,
    now: datetime,
    released_by: UUID | None = None,
) -> TradingHalt | None:
    """Relax an active `(scope, reason_code)` halt by exactly one level (05番 決定
    3/4's two-step safety valve: `all_trading_halted` -> `entry_halted` ->
    `warning` -> released, one call per step). The caller is responsible for having
    already re-verified that this step is warranted (this function does not
    re-check any threshold itself -- see module docstring). A no-op (returns the
    untouched row) if the halt is `emergency_stopped`: that level is only ever
    released via `release_emergency_stop`, never stepped down here. Returns `None`
    if there was no active halt for this `(scope, reason_code)` at all.

    `released_by` (/code-review finding): only relevant when this step happens to
    be the last one (the halt becomes `released`). Pass the calling Owner's id from
    `app/api/routes/trading_halts.py`'s manual `/release` endpoint; leave it `None`
    for `risk_gate._sync_trading_halts`'s automatic recovery calls. This is what
    lets `most_recently_released_halt` below tell a human override apart from an
    automatic one."""
    existing = _find_active_halt(db, scope, reason_code)
    if existing is None:
        return None
    if existing.level == "emergency_stopped":
        return existing

    next_level = _ONE_STEP_DOWN[existing.level]
    if next_level is None:
        existing.status = "released"
        existing.released_at = now
        existing.released_by = released_by
    else:
        existing.level = next_level
    db.flush()
    db.refresh(existing)
    return existing


def most_recently_released_halt(
    db: Session, scope: HaltScope, reason_code: str
) -> TradingHalt | None:
    """The most recent `released` row for `(scope, reason_code)`, if any (/code-review
    finding: `_sync_trading_halts` used to re-activate a halt the moment its breach
    condition was still true on the next signal, even seconds after an Owner had just
    manually released it via `/release` -- silently undoing the override with no
    record. Callers use this to at least log that a reactivation follows a manual
    release; see `risk_gate._sync_trading_halts`). `released_by is not None` on the
    result means that release was a manual `/release` call, not an automatic
    `deescalate_one_step` recovery (which passes `released_by=None`)."""
    return db.scalar(
        select(TradingHalt)
        .where(
            TradingHalt.workspace_id == scope.workspace_id,
            TradingHalt.scope_type == scope.scope_type,
            TradingHalt.scope_id == scope.scope_id,
            TradingHalt.reason_code == reason_code,
            TradingHalt.status == "released",
        )
        .order_by(TradingHalt.released_at.desc())
        .limit(1)
    )


def release_emergency_stop(
    db: Session, halt: TradingHalt, *, released_by: UUID, now: datetime
) -> TradingHalt:
    """Owner-only direct release of an `emergency_stopped` halt straight to
    `released`, bypassing the step-down ladder entirely (05番 決定5, an
    intentional exception: emergency stop is a human final judgment, not subject
    to the automatic gradual-relaxation principle the other levels follow)."""
    if halt.level != "emergency_stopped":
        raise TradingHaltError(
            "not_emergency_stopped", "Only an emergency_stopped halt uses this release path"
        )
    halt.status = "released"
    halt.released_at = now
    halt.released_by = released_by
    db.flush()
    db.refresh(halt)
    return halt


def has_active_halt_at_or_above(db: Session, scope: HaltScope, *, min_level: str) -> bool:
    """Is there an active halt for this exact `(workspace_id, scope_type,
    scope_id)` at `min_level` or more severe, regardless of `reason_code`? (05番:
    "複数のtrading_haltが同一スコープに同時に有効な場合、実効的な制限は最も重い
    レベルを優先する".) Does **not** check a broader scope (e.g. a workspace- or
    system-level halt covering this bot/account) -- ADR 0004 only wires up
    bot/account-scoped causes, so broader scopes have no way to become active yet;
    a caller adding a new cause at `workspace`/`system` scope will need to extend
    this."""
    rows = db.scalars(
        select(TradingHalt).where(
            TradingHalt.workspace_id == scope.workspace_id,
            TradingHalt.scope_type == scope.scope_type,
            TradingHalt.scope_id == scope.scope_id,
            TradingHalt.status == "active",
        )
    ).all()
    return any(_LEVEL_SEVERITY[row.level] >= _LEVEL_SEVERITY[min_level] for row in rows)
