from fastapi import APIRouter

from app.routers import batches, dashboard, handovers, ledger, reconcile, shifts

api = APIRouter(prefix="/api")
api.include_router(dashboard.router)
api.include_router(shifts.router)
api.include_router(batches.router)
api.include_router(handovers.router)
api.include_router(ledger.router)
api.include_router(reconcile.router)
