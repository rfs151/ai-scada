"""遥测数据校核结果：功率平衡、间隔自洽、当前异常。"""

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/datacheck", tags=["datacheck"])


@router.get("", summary="最近一次数据校核断面")
async def latest(request: Request) -> dict:
    w = request.app.state.workers
    if w is None:
        return {"t": None, "balances": [], "bays": [], "issues": []}
    return {"t": None, "balances": [], "bays": [], "issues": []} | w.alarm.datacheck.snapshot
