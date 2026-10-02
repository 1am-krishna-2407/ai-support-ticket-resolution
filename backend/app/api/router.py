from fastapi import APIRouter, Depends

from app.api.routes import (
    auth,
    documents,
    evaluations,
    ingestion,
    retrieval,
    system,
    tickets,
    users,
)
from app.core.auth import reject_cross_site, require_user

# Every route needs a signed-in user except /health, /version, /ready (system: liveness,
# build info, and dependency status, which matters most when the database is down) and
# /auth/login.
# Ingestion and evaluation runs are admin-only (require_admin also accepts the admin token
# for automation); their read routes require a user individually.
SIGNED_IN = [Depends(require_user)]

api_router = APIRouter(dependencies=[Depends(reject_cross_site)])
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(retrieval.router, dependencies=SIGNED_IN)
api_router.include_router(tickets.router, dependencies=SIGNED_IN)
api_router.include_router(documents.router, dependencies=SIGNED_IN)
api_router.include_router(ingestion.router)
api_router.include_router(evaluations.router)
