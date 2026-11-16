from fastapi import APIRouter, Query

from app.services import handover as svc

router = APIRouter(prefix="/ledger", tags=["流转账本"])


@router.get("")
def get_ledger(
    handover_id: int | None = Query(default=None),
    biz_key: str | None = Query(default=None),
    day: str | None = Query(default=None),
):
    return svc.list_ledger(handover_id, biz_key, day)


@router.get("/flows")
def get_flows(
    start: str | None = Query(default=None, description="起始（含），缺省今日"),
    end: str | None = Query(default=None, description="截止（不含），缺省明日"),
):
    return svc.flow_summary(start, end)
