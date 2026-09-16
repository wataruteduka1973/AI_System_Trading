from fastapi import APIRouter

from app.api.routes import connections, instruments, market_data, workspaces

api_router = APIRouter()
api_router.include_router(workspaces.router)
api_router.include_router(connections.router)
api_router.include_router(instruments.router)
api_router.include_router(market_data.router)
