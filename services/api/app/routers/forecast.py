"""负荷预测接口。"""

import time

from fastapi import APIRouter, Request

from app.forecast import service

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


@router.get("/load", summary="10kV 负荷预测：最近 1 仿真日实测 + 未来 1 仿真日预测 + 昨日回测准确率")
async def load(request: Request) -> dict:
    st = request.app.state
    return await service.build(st.pool, st.station, st.settings.sim_speed, time.time())
