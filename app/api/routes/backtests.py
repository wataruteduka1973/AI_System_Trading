"""Backtest API (Horizon 4 "backtest + strategy governance" phase --
docs/architecture-alignment-and-long-term-roadmap.md, 2026-09-26). Exposes the
already-implemented, already-tested Horizon4-lite backtest engine
(docs/plans/horizon4-lite-backtest.md Units 1-6:
`app/trading/application/{backtest_replay,backtest_metrics,backtest_walk_forward}.py`)
over HTTP -- the same "fully built, zero HTTP callers" gap Horizon 3's Bot
management API closed for `dummy_pipeline.py`.

Runs synchronously within the POST request (see
`backtest_provisioning.run_backtest_for_workspace`'s own docstring for why): a
large date range can make this endpoint slow. No progress reporting exists;
this is a known, accepted limitation of "minimal" scope, not an oversight.

`StrategyVersion` is not caller-selectable here: this codebase has exactly one
real strategy implementation, so the workspace's shared dummy
`StrategyVersion`/`RiskProfileVersion` (the same ones
`dummy_pipeline.ensure_dummy_bot` uses for live paper bots) is always used. The
`StrategyVersion` Draft/Validated/Approved/Retired governance workflow ADR 0003
deferred remains out of scope here too (explicit user decision, 2026-09-26 --
"公開運用・複数戦略運用が具体化した時点で改めて着手する").
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.instruments import instrument_read
from app.db.session import get_db
from app.market_data.application import use_cases as market_data_application
from app.market_data.application.public_research import RESEARCH_EXCHANGE_CODE
from app.models.backtest import BacktestRun, BacktestTrade
from app.models.connections import Exchange, Market
from app.models.instruments import Instrument
from app.models.workspace import AppUser
from app.schemas.backtests import (
    BacktestCreate,
    BacktestCreateResponse,
    BacktestRunRead,
    BacktestTradeRead,
)
from app.schemas.instruments import WorkspaceInstrumentRead
from app.security.rbac import require_operator_role, require_viewer_role
from app.trading.application.backtest_provisioning import (
    BacktestProvisioningError,
    run_backtest_for_workspace,
)
from app.trading.application.backtest_walk_forward import WalkForwardError

router = APIRouter()
DatabaseSession = Annotated[Session, Depends(get_db)]
Viewer = Annotated[AppUser, Depends(require_viewer_role)]
Operator = Annotated[AppUser, Depends(require_operator_role)]


def _is_research_instrument(db: Session, instrument_id: UUID) -> bool:
    exchange_code = db.scalar(
        select(Exchange.code)
        .join(Instrument, Instrument.exchange_id == Exchange.id)
        .where(Instrument.id == instrument_id)
    )
    return exchange_code == RESEARCH_EXCHANGE_CODE


def _require_instrument_access(db: Session, workspace_id: UUID, instrument_id: UUID) -> None:
    """Mirrors `market_data.py`'s own wrapper around the same application-layer
    checks -- an instrument only counts as usable once the workspace has an
    active, verified connection selected for its exchange (see
    `use_cases._require_instrument_access`) -- **except** a `binance_public`
    research instrument (2026-09-26), which has no `ExchangeConnection`/
    `WorkspaceAccountSelection` at all by design (it is public, read-only
    market data, not a tradeable account): only workspace existence is
    checked for it."""
    try:
        market_data_application._require_workspace(db, workspace_id)
    except market_data_application.MarketDataApplicationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if _is_research_instrument(db, instrument_id):
        return
    try:
        market_data_application._require_instrument_access(db, workspace_id, instrument_id)
    except market_data_application.MarketDataApplicationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get(
    "/workspaces/{workspace_id}/research-instruments",
    response_model=list[WorkspaceInstrumentRead],
    tags=["backtests"],
)
def list_research_instruments(
    workspace_id: UUID, db: DatabaseSession, _viewer: Viewer
) -> list[WorkspaceInstrumentRead]:
    """Research-only instruments (2026-09-26 -- the `binance_public` exchange
    and its `Instrument`s, see `app/market_data/application/public_research.py`)
    for the Backtest form's instrument picker. `list_workspace_instruments`
    (`app/api/routes/instruments.py`) can never return these: it joins on
    `WorkspaceAccountSelection`, which a public, credential-less research
    instrument has none of by design. `workspace_id` here only gates *access*
    (the caller must be a viewer of some workspace, same as every other route
    in this API) -- the returned data itself is identical for every workspace,
    since public market data isn't workspace-scoped."""
    rows = db.execute(
        select(Instrument, Exchange, Market)
        .join(Exchange, Instrument.exchange_id == Exchange.id)
        .join(Market, Instrument.market_id == Market.id)
        .where(Exchange.code == RESEARCH_EXCHANGE_CODE)
        .order_by(Instrument.symbol)
    ).all()
    return [
        instrument_read(instrument, exchange.code, market.code)
        for instrument, exchange, market in rows
    ]


def _get_backtest_run(db: Session, workspace_id: UUID, backtest_run_id: UUID) -> BacktestRun:
    run = db.scalar(
        select(BacktestRun).where(
            BacktestRun.id == backtest_run_id, BacktestRun.workspace_id == workspace_id
        )
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backtest run not found")
    return run


@router.post(
    "/workspaces/{workspace_id}/backtests",
    response_model=BacktestCreateResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["backtests"],
)
def create_backtest(
    workspace_id: UUID, payload: BacktestCreate, db: DatabaseSession, _operator: Operator
) -> BacktestCreateResponse:
    _require_instrument_access(db, workspace_id, payload.instrument_id)
    instrument = db.get(Instrument, payload.instrument_id)
    if instrument is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instrument not found")
    try:
        runs = run_backtest_for_workspace(
            db,
            workspace_id,
            instrument,
            timeframe=payload.timeframe,
            from_time=payload.from_time,
            to_time=payload.to_time,
            initial_equity=payload.initial_equity,
            spread=payload.spread,
            walk_forward=payload.mode == "walk_forward",
            train_ratio=payload.train_ratio,
        )
    except (BacktestProvisioningError, WalkForwardError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return BacktestCreateResponse(runs=[BacktestRunRead.model_validate(run) for run in runs])


@router.get(
    "/workspaces/{workspace_id}/backtests",
    response_model=list[BacktestRunRead],
    tags=["backtests"],
)
def list_backtests(workspace_id: UUID, db: DatabaseSession, _viewer: Viewer) -> list[BacktestRun]:
    statement = (
        select(BacktestRun)
        .where(BacktestRun.workspace_id == workspace_id)
        .order_by(BacktestRun.created_at.desc())
    )
    return list(db.scalars(statement).all())


@router.get(
    "/workspaces/{workspace_id}/backtests/{backtest_run_id}",
    response_model=BacktestRunRead,
    tags=["backtests"],
)
def get_backtest(
    workspace_id: UUID, backtest_run_id: UUID, db: DatabaseSession, _viewer: Viewer
) -> BacktestRun:
    return _get_backtest_run(db, workspace_id, backtest_run_id)


@router.get(
    "/workspaces/{workspace_id}/backtests/{backtest_run_id}/trades",
    response_model=list[BacktestTradeRead],
    tags=["backtests"],
)
def list_backtest_trades(
    workspace_id: UUID, backtest_run_id: UUID, db: DatabaseSession, _viewer: Viewer
) -> list[BacktestTrade]:
    run = _get_backtest_run(db, workspace_id, backtest_run_id)
    statement = (
        select(BacktestTrade)
        .where(BacktestTrade.backtest_run_id == run.id)
        .order_by(BacktestTrade.sequence_no)
    )
    return list(db.scalars(statement).all())
