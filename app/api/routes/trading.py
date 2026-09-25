"""Bot management API (Horizon5, "trade bot core functionality" phase --
docs/architecture-alignment-and-long-term-roadmap.md, 2026-09-25 "その順で
進めて": Bot management API -> execution loop/Worker -> minimal UI).

Two resources: trading-accounts (paper only -- see app/schemas/trading.py's
module docstring) and bots. `POST .../bots` deliberately does not start the
bot it creates -- see `dummy_pipeline.ensure_dummy_bot`'s module docstring
for why provisioning and starting were split into separate actions, matching
how `bot_lifecycle.py`'s four commands (start/pause/resume/stop) are already
independent of each other.

`docs/concept/FXtrading_rebuild/04_API再設計.md` sketches a similar Bot/口座
API shape (single `POST /bots/{id}/commands` instead of four verb endpoints,
no account-creation endpoint at all -- accounts are assumed synced from the
exchange). That doc lives under `docs/concept/` which, per AGENTS.md's own
directory convention, is explicitly "not the current scope" -- it is used
here only as a design reference, not as a binding contract. This module
instead follows the convention this codebase already established in
`trading_halts.py` (one endpoint per verb, e.g. `/release`), since paper
accounts here have no exchange-side counterpart to sync from.

`bot_lifecycle.py`'s own module docstring notes an earlier task deliberately
left it with "no HTTP layer... explicitly out of scope for this task too" --
that was a scope decision for that task, not a permanent architectural rule;
exposing it is exactly this task's job.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.connections import ExchangeConnection
from app.models.instruments import Instrument
from app.models.strategy import BotRun, Signal, TradingBot
from app.models.trading import LedgerTransaction, TradingAccount
from app.models.workspace import AppUser
from app.schemas.trading import (
    BotRunRead,
    BotRunSummaryRead,
    LatestSignalRead,
    TradingAccountCreate,
    TradingAccountDepositCreate,
    TradingAccountDepositRead,
    TradingAccountRead,
    TradingBotCreate,
    TradingBotRead,
)
from app.security.rbac import require_operator_role, require_viewer_role
from app.trading.application import account_funding, bot_lifecycle
from app.trading.application.bot_lifecycle import BotLifecycleError, BotStateConflictError
from app.trading.application.dummy_pipeline import ensure_dummy_bot

router = APIRouter()
DatabaseSession = Annotated[Session, Depends(get_db)]
Viewer = Annotated[AppUser, Depends(require_viewer_role)]
Operator = Annotated[AppUser, Depends(require_operator_role)]


def _get_account(db: Session, workspace_id: UUID, account_id: UUID) -> TradingAccount:
    account = db.scalar(
        select(TradingAccount).where(
            TradingAccount.id == account_id, TradingAccount.workspace_id == workspace_id
        )
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trading account not found"
        )
    return account


def _get_bot(db: Session, workspace_id: UUID, bot_id: UUID) -> TradingBot:
    bot = db.scalar(
        select(TradingBot).where(TradingBot.id == bot_id, TradingBot.workspace_id == workspace_id)
    )
    if bot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trading bot not found")
    return bot


def _lifecycle_error_response(exc: BotLifecycleError) -> HTTPException:
    if isinstance(exc, BotStateConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


@router.post(
    "/workspaces/{workspace_id}/trading-accounts",
    response_model=TradingAccountRead,
    status_code=status.HTTP_201_CREATED,
    tags=["trading-accounts"],
)
def create_trading_account(
    workspace_id: UUID, payload: TradingAccountCreate, db: DatabaseSession, _operator: Operator
) -> TradingAccount:
    connection = db.scalar(
        select(ExchangeConnection).where(
            ExchangeConnection.id == payload.connection_id,
            ExchangeConnection.workspace_id == workspace_id,
        )
    )
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Exchange connection not found"
        )
    account = TradingAccount(
        workspace_id=workspace_id,
        connection_id=connection.id,
        external_account_id=None,
        mode="paper",
        base_currency=payload.base_currency,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.get(
    "/workspaces/{workspace_id}/trading-accounts",
    response_model=list[TradingAccountRead],
    tags=["trading-accounts"],
)
def list_trading_accounts(
    workspace_id: UUID, db: DatabaseSession, _viewer: Viewer
) -> list[TradingAccount]:
    statement = (
        select(TradingAccount)
        .where(TradingAccount.workspace_id == workspace_id)
        .order_by(TradingAccount.created_at)
    )
    return list(db.scalars(statement).all())


@router.get(
    "/workspaces/{workspace_id}/trading-accounts/{account_id}",
    response_model=TradingAccountRead,
    tags=["trading-accounts"],
)
def get_trading_account(
    workspace_id: UUID, account_id: UUID, db: DatabaseSession, _viewer: Viewer
) -> TradingAccount:
    return _get_account(db, workspace_id, account_id)


@router.post(
    "/workspaces/{workspace_id}/trading-accounts/{account_id}/deposits",
    response_model=TradingAccountDepositRead,
    status_code=status.HTTP_201_CREATED,
    tags=["trading-accounts"],
)
def create_trading_account_deposit(
    workspace_id: UUID,
    account_id: UUID,
    payload: TradingAccountDepositCreate,
    db: DatabaseSession,
    _operator: Operator,
) -> LedgerTransaction:
    account = _get_account(db, workspace_id, account_id)
    if payload.note is not None:
        transaction = account_funding.seed_paper_deposit(
            db, account, amount=payload.amount, asset=payload.asset, note=payload.note
        )
    else:
        transaction = account_funding.seed_paper_deposit(
            db, account, amount=payload.amount, asset=payload.asset
        )
    db.commit()
    db.refresh(transaction)
    return transaction


@router.post(
    "/workspaces/{workspace_id}/bots",
    response_model=TradingBotRead,
    status_code=status.HTTP_201_CREATED,
    tags=["bots"],
)
def create_trading_bot(
    workspace_id: UUID, payload: TradingBotCreate, db: DatabaseSession, _operator: Operator
) -> TradingBot:
    existing = db.scalar(
        select(TradingBot).where(
            TradingBot.workspace_id == workspace_id, TradingBot.name == payload.name
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A bot with this name already exists in this workspace",
        )
    account = _get_account(db, workspace_id, payload.account_id)
    if account.connection_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Trading account has no exchange connection",
        )
    instrument = db.get(Instrument, payload.instrument_id)
    if instrument is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instrument not found")
    return ensure_dummy_bot(
        db, workspace_id, account, instrument, bot_name=payload.name, timeframe=payload.timeframe
    )


@router.get("/workspaces/{workspace_id}/bots", response_model=list[TradingBotRead], tags=["bots"])
def list_trading_bots(workspace_id: UUID, db: DatabaseSession, _viewer: Viewer) -> list[TradingBot]:
    statement = (
        select(TradingBot)
        .where(TradingBot.workspace_id == workspace_id)
        .order_by(TradingBot.created_at)
    )
    return list(db.scalars(statement).all())


@router.get(
    "/workspaces/{workspace_id}/bots/{bot_id}", response_model=TradingBotRead, tags=["bots"]
)
def get_trading_bot(
    workspace_id: UUID, bot_id: UUID, db: DatabaseSession, _viewer: Viewer
) -> TradingBot:
    return _get_bot(db, workspace_id, bot_id)


@router.get(
    "/workspaces/{workspace_id}/bots/{bot_id}/latest-run",
    response_model=BotRunSummaryRead,
    tags=["bots"],
)
def get_latest_bot_run(
    workspace_id: UUID, bot_id: UUID, db: DatabaseSession, _viewer: Viewer
) -> BotRunSummaryRead:
    bot = _get_bot(db, workspace_id, bot_id)
    bot_run = db.scalar(
        select(BotRun).where(BotRun.bot_id == bot.id).order_by(BotRun.started_at.desc()).limit(1)
    )
    if bot_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="This bot has never been started"
        )
    latest_signal = db.scalar(
        select(Signal)
        .where(Signal.bot_run_id == bot_run.id)
        .order_by(Signal.created_at.desc())
        .limit(1)
    )
    return BotRunSummaryRead(
        id=bot_run.id,
        bot_id=bot_run.bot_id,
        status=bot_run.status,
        code_version=bot_run.code_version,
        started_at=bot_run.started_at,
        stopped_at=bot_run.stopped_at,
        stop_reason=bot_run.stop_reason,
        heartbeat_at=bot_run.heartbeat_at,
        latest_signal=(
            LatestSignalRead(
                id=latest_signal.id,
                action=latest_signal.action,
                created_at=latest_signal.created_at,
            )
            if latest_signal is not None
            else None
        ),
    )


@router.post(
    "/workspaces/{workspace_id}/bots/{bot_id}/start", response_model=BotRunRead, tags=["bots"]
)
def start_trading_bot(
    workspace_id: UUID, bot_id: UUID, db: DatabaseSession, _operator: Operator
) -> BotRun:
    bot = _get_bot(db, workspace_id, bot_id)
    try:
        return bot_lifecycle.start_bot(db, bot)
    except BotLifecycleError as exc:
        raise _lifecycle_error_response(exc) from exc


@router.post(
    "/workspaces/{workspace_id}/bots/{bot_id}/pause", response_model=BotRunRead, tags=["bots"]
)
def pause_trading_bot(
    workspace_id: UUID, bot_id: UUID, db: DatabaseSession, _operator: Operator
) -> BotRun:
    bot = _get_bot(db, workspace_id, bot_id)
    try:
        return bot_lifecycle.pause_bot(db, bot)
    except BotLifecycleError as exc:
        raise _lifecycle_error_response(exc) from exc


@router.post(
    "/workspaces/{workspace_id}/bots/{bot_id}/resume", response_model=BotRunRead, tags=["bots"]
)
def resume_trading_bot(
    workspace_id: UUID, bot_id: UUID, db: DatabaseSession, _operator: Operator
) -> BotRun:
    bot = _get_bot(db, workspace_id, bot_id)
    try:
        return bot_lifecycle.resume_bot(db, bot)
    except BotLifecycleError as exc:
        raise _lifecycle_error_response(exc) from exc


@router.post(
    "/workspaces/{workspace_id}/bots/{bot_id}/stop", response_model=BotRunRead, tags=["bots"]
)
def stop_trading_bot(
    workspace_id: UUID, bot_id: UUID, db: DatabaseSession, _operator: Operator
) -> BotRun:
    bot = _get_bot(db, workspace_id, bot_id)
    try:
        return bot_lifecycle.stop_bot(db, bot)
    except BotLifecycleError as exc:
        raise _lifecycle_error_response(exc) from exc
