from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.services import handover as svc

router = APIRouter(prefix="/reconcile", tags=["交班对账"])


class RunBody(BaseModel):
    sample_size: int = Field(default=10, ge=1, le=1000, description="抽查账本行数")


@router.get("")
def get_reports():
    return svc.list_reconcile()


@router.post("/run")
def run(body: RunBody):
    return svc.run_reconcile(body.sample_size)


@router.get("/runs")
def get_runs():
    return svc.list_reconcile_runs()


@router.get("/diffs")
def get_diffs(run_id: int | None = Query(default=None)):
    return svc.list_reconcile_diffs(run_id)
