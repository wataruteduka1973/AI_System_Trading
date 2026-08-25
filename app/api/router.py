from fastapi import APIRouter

from app.api.routes import catalog, instruments, market_data

api_router = APIRouter()
api_router.include_router(catalog.router)
api_router.include_router(instruments.router)
api_router.include_router(market_data.router)
