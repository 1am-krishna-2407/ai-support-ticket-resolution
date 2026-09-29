from fastapi import APIRouter

from app.api.routes import documents, ingestion, retrieval, system, tickets

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(retrieval.router)
api_router.include_router(tickets.router)
api_router.include_router(documents.router)
api_router.include_router(ingestion.router)
