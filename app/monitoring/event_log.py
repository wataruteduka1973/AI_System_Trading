"""Searching the workspace's `system_event` rows: the event log (FR-UI-09, docs/plans/event-log.md).

`system_event` is what the system announced about the workspace -- halts, worker stops, bot
failures, undeliverable notifications -- one row per announcement, each with the `correlation_id` it
shares with its outbox row and, for an emergency stop, its audit rows. The log is newest first and
pages by a keyset (`occurred_at`, `id`): a page does not shift when a new event arrives, which an
offset would.

The bot filter matches an event that is *about* a bot (`target_type='bot'`), an event whose payload
names the bot by id (a lock halt is on the account, with `bot_id` in its payload) and one that lists
the bot's name (`bot_names`, as the stalled-worker alert does).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, literal, or_, select, tuple_
from sqlalchemy.orm import Session

from app.models.audit import SystemEvent
from app.models.strategy import TradingBot

MAX_PAGE_SIZE = 200
MAX_TEXT_LENGTH = 100


@dataclass(frozen=True)
class EventFilters:
    severities: list[str] = field(default_factory=list)
    category: str | None = None
    event_type: str | None = None
    reason_code: str | None = None
    bot_id: UUID | None = None
    correlation_id: UUID | None = None
    from_time: datetime | None = None
    to_time: datetime | None = None
    text: str | None = None
    """A word in the message."""


@dataclass(frozen=True)
class EventPage:
    items: list[SystemEvent]
    next_before: tuple[datetime, UUID] | None
    """The `(occurred_at, id)` to continue from, or `None` at the end."""


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def query_events(
    db: Session,
    workspace_id: UUID,
    filters: EventFilters,
    *,
    limit: int,
    before: tuple[datetime, UUID] | None = None,
) -> EventPage:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    statement = select(SystemEvent).where(SystemEvent.workspace_id == workspace_id)
    if filters.severities:
        statement = statement.where(SystemEvent.severity.in_(filters.severities))
    if filters.category:
        statement = statement.where(SystemEvent.category == filters.category)
    if filters.event_type:
        statement = statement.where(SystemEvent.event_type == filters.event_type)
    if filters.reason_code:
        statement = statement.where(SystemEvent.reason_code == filters.reason_code)
    if filters.correlation_id:
        statement = statement.where(SystemEvent.correlation_id == filters.correlation_id)
    if filters.bot_id:
        conditions = [
            and_(SystemEvent.target_type == "bot", SystemEvent.target_id == filters.bot_id),
            SystemEvent.payload["bot_id"].as_string() == str(filters.bot_id),
        ]
        bot_name = db.scalar(
            select(TradingBot.name).where(
                TradingBot.id == filters.bot_id, TradingBot.workspace_id == workspace_id
            )
        )
        if bot_name is not None:
            # A worker-stall event names the bots it is about, not their ids.
            conditions.append(SystemEvent.payload["bot_names"].contains([bot_name]))
        statement = statement.where(or_(*conditions))
    if filters.from_time:
        statement = statement.where(SystemEvent.occurred_at >= filters.from_time)
    if filters.to_time:
        statement = statement.where(SystemEvent.occurred_at < filters.to_time)
    if filters.text:
        needle = _escape_like(filters.text[:MAX_TEXT_LENGTH])
        statement = statement.where(SystemEvent.message.ilike(f"%{needle}%", escape="\\"))
    if before is not None:
        statement = statement.where(
            tuple_(SystemEvent.occurred_at, SystemEvent.id)
            < tuple_(literal(before[0]), literal(before[1]))
        )
    rows = list(
        db.scalars(
            statement.order_by(SystemEvent.occurred_at.desc(), SystemEvent.id.desc()).limit(
                limit + 1
            )
        ).all()
    )
    page = rows[:limit]
    more = len(rows) > limit
    return EventPage(
        items=page,
        next_before=(page[-1].occurred_at, page[-1].id) if more and page else None,
    )


@dataclass(frozen=True)
class EventFacets:
    severities: list[str]
    categories: list[str]
    event_types: list[str]
    reason_codes: list[str]


def event_facets(db: Session, workspace_id: UUID) -> EventFacets:
    """The values that occur in this workspace, for the filter choices."""

    def distinct(column: Any) -> list[str]:
        return [
            str(value)
            for value in db.scalars(
                select(column)
                .where(SystemEvent.workspace_id == workspace_id, column.is_not(None))
                .distinct()
                .order_by(column)
            ).all()
        ]

    return EventFacets(
        severities=distinct(SystemEvent.severity),
        categories=distinct(SystemEvent.category),
        event_types=distinct(SystemEvent.event_type),
        reason_codes=distinct(SystemEvent.reason_code),
    )
