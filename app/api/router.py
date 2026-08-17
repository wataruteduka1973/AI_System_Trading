from fastapi import APIRouter

from app.api.routes import catalog

api_router = APIRouter()
api_router.include_router(catalog.router)
