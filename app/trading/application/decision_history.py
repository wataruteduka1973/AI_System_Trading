"""Why a bot did or did not trade (docs/plans/decision-history.md): one entry per bar the bot
evaluated -- its signal, the risk decision behind an entry, what came of it -- and the halts that
stopped it from entering.

Everything is read off what `bot_evaluation` already records: a `signal` per evaluated bar (buy,
sell or hold, with the strategy's `rationale`), a `risk_decision` for an entry that reached the Risk
Gate (its `rule_results` name each check and whether it passed), an `order_intent` and `trade_order`
for an entry that was placed, an audit row for a signal that closed an opposite position, and
`trading_halt` rows. Nothing is stored for this.

What happened to a signal is derived, in this order:

- `hold`: the strategy asked for nothing;
- `ordered`: an order intent exists -- the entry was placed;
- `closed`: a signal against an open position closed it (no risk decision: closing is not an entry);
- `denied`: the Risk Gate refused (`risk_decision.outcome = 'deny'`), with the checks that failed;
- `halted`: the Risk Gate allowed it but an active halt stopped the entry, so nothing was placed;
- `paused`: nothing at all was recorded for a buy/sell -- the bot was paused, which allows closing
  but not entering.

The bot's halts are those on the bot itself, on its account (the lock halts, a ledger mismatch) and
on the whole workspace (an emergency stop).
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.market_data import Candle
from app.models.strategy import BotRun, RiskDecision, Signal, TradingBot, TradingHalt
from app.models.trading import Fill, OrderIntent, TradeOrder

MAX_PAGE_SIZE = 200
OUTCOMES = ("hold", "ordered", "closed", "denied", "halted", "paused")


@dataclass(frozen=True)
class FailedCheck:
    name: str
    detail: dict[str, object]
    """The check's own figures (`pct`, `count`, `bars` ...), without the `passed` flag."""


@dataclass(frozen=True)
class DecisionEntry:
    signal: Signal
    bar_open_time: datetime | None
    bar_close_time: datetime | None
    outcome: str
    risk_outcome: str | None
    risk_reason_code: str | None
    failed_checks: list[FailedCheck] = field(default_factory=list)
    approved_quantity: Decimal | None = None
    order_id: UUID | None = None
    order_side: str | None = None
    fill_price: Decimal | None = None
    fill_quantity: Decimal | None = None


@dataclass(frozen=True)
class DecisionSummary:
    evaluations: int
    counts: dict[str, int]
    """Per outcome, over every evaluation the filters match."""


@dataclass(frozen=True)
class DecisionPage:
    items: list[DecisionEntry]
    next_before: tuple[datetime, UUID] | None
    summary: DecisionSummary
    halts: list[TradingHalt]
    """The bot's halts overlapping the time this page covers."""


def failed_checks(rule_results: dict[str, object]) -> list[FailedCheck]:
    """The checks of a risk decision that did not pass, with their figures."""
    failed = []
    for name, result in rule_results.items():
        if isinstance(result, dict) and result.get("passed") is False:
            failed.append(FailedCheck(name, {k: v for k, v in result.items() if k != "passed"}))
    return failed


def derive_outcome(
    action: str, *, has_intent: bool, closed_position: bool, risk_outcome: str | None
) -> str:
    if action == "hold":
        return "hold"
    if has_intent:
        return "ordered"
    if closed_position:
        return "closed"
    if risk_outcome == "deny":
        return "denied"
    if risk_outcome is not None:
        return "halted"
    return "paused"


def _closed_signal_ids(db: Session, workspace_id: UUID) -> set[str]:
    """Signals that closed an opposite position (`bot_evaluation` records an audit row for each)."""
    return {
        str(value)
        for value in db.scalars(
            select(AuditLog.after_data["signal_id"].as_string()).where(
                AuditLog.workspace_id == workspace_id, AuditLog.action == "dote_gate.close_only"
            )
        ).all()
        if value
    }


def _range(statement, bot_id: UUID, from_time: datetime | None, to_time: datetime | None):  # noqa: ANN001, ANN202
    statement = statement.join(BotRun, BotRun.id == Signal.bot_run_id).where(
        BotRun.bot_id == bot_id
    )
    if from_time is not None:
        statement = statement.where(Signal.created_at >= from_time)
    if to_time is not None:
        statement = statement.where(Signal.created_at < to_time)
    return statement


def list_decisions(
    db: Session,
    workspace_id: UUID,
    bot: TradingBot,
    *,
    outcomes: list[str],
    from_time: datetime | None,
    to_time: datetime | None,
    limit: int,
    before: tuple[datetime, UUID] | None = None,
) -> DecisionPage:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    closed_ids = _closed_signal_ids(db, workspace_id)

    # Light rows for every evaluation in the range: the outcome of each, the summary and the page
    # are decided from these; only the page's own rows are read in full (the rule results are big).
    light = db.execute(
        _range(
            select(
                Signal.id, Signal.created_at, Signal.action, RiskDecision.outcome, OrderIntent.id
            )
            .outerjoin(RiskDecision, RiskDecision.signal_id == Signal.id)
            .outerjoin(OrderIntent, OrderIntent.signal_id == Signal.id),
            bot.id,
            from_time,
            to_time,
        ).order_by(Signal.created_at.desc(), Signal.id.desc())
    ).all()
    outcome_of = {
        signal_id: derive_outcome(
            action,
            has_intent=intent_id is not None,
            closed_position=str(signal_id) in closed_ids,
            risk_outcome=risk_outcome,
        )
        for signal_id, _, action, risk_outcome, intent_id in light
    }
    counts = Counter(outcome_of.values())
    summary = DecisionSummary(
        evaluations=len(light), counts={outcome: counts.get(outcome, 0) for outcome in OUTCOMES}
    )

    matching = [
        (signal_id, created_at)
        for signal_id, created_at, *_ in light
        if (not outcomes or outcome_of[signal_id] in outcomes)
        and (before is None or (created_at, signal_id) < before)
    ]
    chosen = matching[:limit]
    more = len(matching) > limit
    entries = _read_entries(db, [signal_id for signal_id, _ in chosen], outcome_of)
    entries = _with_orders(db, entries)
    return DecisionPage(
        items=entries,
        next_before=(chosen[-1][1], chosen[-1][0]) if more and chosen else None,
        summary=summary,
        halts=_halts_over(db, workspace_id, bot, entries, from_time, to_time),
    )


def _read_entries(
    db: Session, signal_ids: list[UUID], outcome_of: dict[UUID, str]
) -> list[DecisionEntry]:
    if not signal_ids:
        return []
    rows = {
        row[0].id: row
        for row in db.execute(
            select(Signal, RiskDecision, Candle.open_time, Candle.close_time)
            .join(Candle, Candle.id == Signal.candle_id)
            .outerjoin(RiskDecision, RiskDecision.signal_id == Signal.id)
            .where(Signal.id.in_(signal_ids))
        ).all()
    }
    entries = []
    for signal_id in signal_ids:  # the order of the page
        signal, decision, bar_open, bar_close = rows[signal_id]
        entries.append(
            DecisionEntry(
                signal=signal,
                bar_open_time=bar_open,
                bar_close_time=bar_close,
                outcome=outcome_of[signal_id],
                risk_outcome=decision.outcome if decision else None,
                risk_reason_code=decision.reason_code if decision else None,
                failed_checks=failed_checks(decision.rule_results) if decision else [],
                approved_quantity=decision.adjusted_quantity if decision else None,
            )
        )
    return entries


def _with_orders(db: Session, entries: list[DecisionEntry]) -> list[DecisionEntry]:
    """The order (and its fill) behind each entry that was placed."""
    ordered = [entry for entry in entries if entry.outcome == "ordered"]
    if not ordered:
        return entries
    rows = db.execute(
        select(OrderIntent.signal_id, TradeOrder.id, TradeOrder.side, Fill.price, Fill.quantity)
        .join(TradeOrder, TradeOrder.order_intent_id == OrderIntent.id)
        .outerjoin(Fill, Fill.order_id == TradeOrder.id)
        .where(OrderIntent.signal_id.in_([entry.signal.id for entry in ordered]))
    ).all()
    by_signal = {row[0]: row for row in rows}
    result = []
    for entry in entries:
        found = by_signal.get(entry.signal.id)
        if found is None:
            result.append(entry)
            continue
        result.append(
            DecisionEntry(
                signal=entry.signal,
                bar_open_time=entry.bar_open_time,
                bar_close_time=entry.bar_close_time,
                outcome=entry.outcome,
                risk_outcome=entry.risk_outcome,
                risk_reason_code=entry.risk_reason_code,
                failed_checks=entry.failed_checks,
                approved_quantity=entry.approved_quantity,
                order_id=found[1],
                order_side=found[2],
                fill_price=found[3],
                fill_quantity=found[4],
            )
        )
    return result


def _halts_over(
    db: Session,
    workspace_id: UUID,
    bot: TradingBot,
    page: list[DecisionEntry],
    from_time: datetime | None,
    to_time: datetime | None,
) -> list[TradingHalt]:
    """The halts on the bot, its account or the workspace that overlap the time the page covers:
    from its oldest evaluation to the end of the range (or now)."""
    now = datetime.now(UTC)
    span_to = min(to_time, now) if to_time else now
    # Back to the oldest evaluation shown: a halt that ended before it explains none of them.
    span_from = page[-1].signal.created_at if page else (from_time or now)
    scopes = or_(
        (TradingHalt.scope_type == "bot") & (TradingHalt.scope_id == bot.id),
        (TradingHalt.scope_type == "account") & (TradingHalt.scope_id == bot.account_id),
        TradingHalt.scope_type == "workspace",
    )
    return list(
        db.scalars(
            select(TradingHalt)
            .where(
                TradingHalt.workspace_id == workspace_id,
                scopes,
                TradingHalt.halted_at < span_to,
                or_(TradingHalt.released_at.is_(None), TradingHalt.released_at > span_from),
            )
            .order_by(TradingHalt.halted_at.desc())
        ).all()
    )
