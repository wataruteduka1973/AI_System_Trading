from fastapi import APIRouter

from app.api.routes import (
    auth,
    backtests,
    client_logs,
    connections,
    events,
    instruments,
    market_data,
    notifications,
    system_status,
    trading,
    trading_halts,
    workspaces,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(workspaces.router)
api_router.include_router(connections.router)
api_router.include_router(instruments.router)
api_router.include_router(market_data.router)
api_router.include_router(trading.router)
api_router.include_router(backtests.router)
api_router.include_router(trading_halts.router)
api_router.include_router(notifications.router)
api_router.include_router(events.router)
api_router.include_router(system_status.router)
api_router.include_router(client_logs.router)
