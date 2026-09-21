from fastapi import APIRouter

from app.api.routes import (
    auth,
    client_logs,
    connections,
    instruments,
    market_data,
    trading_halts,
    workspaces,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(workspaces.router)
api_router.include_router(connections.router)
api_router.include_router(instruments.router)
api_router.include_router(market_data.router)
api_router.include_router(trading_halts.router)
api_router.include_router(client_logs.router)
