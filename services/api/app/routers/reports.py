from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from app.auth.deps import optional_user
from app.auth.security import User
from app.reports import daily
from app.reports.excel import to_xlsx

router = APIRouter(prefix="/api/reports", tags=["reports"])
MAX_SPAN = timedelta(days=31)


def _range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    end = end or datetime.now(UTC)
    start = start or end - timedelta(minutes=24)
    if start >= end:
        raise HTTPException(400, "开始时间须早于结束时间")
    if end - start > MAX_SPAN:
        raise HTTPException(400, "统计时段不超过 31 天")
    return start, end


@router.get("/summary", summary="运行报表（默认最近 24 分钟 = 1 个仿真日）")
async def summary(request: Request, start: datetime | None = None, end: datetime | None = None) -> dict:
    s, e = _range(start, end)
    return await daily.build(request.app.state.pool, request.app.state.station, s, e)


@router.get("/export.xlsx", summary="导出 Excel")
async def export(
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
    user: User | None = Depends(optional_user),
) -> Response:
    s, e = _range(start, end)
    st = request.app.state
    rep = await daily.build(st.pool, st.station, s, e)
    data = to_xlsx(rep, st.station.name, user.name if user else None)
    local = s.astimezone(ZoneInfo("Asia/Shanghai"))
    fname = f"运行报表_{local:%Y%m%d_%H%M}.xlsx"
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )
