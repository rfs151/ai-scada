"""仿真场景注入（仅演示系统）：经 Redis 仿真控制通道下发到仿真子站，与 104 数据通道无关。"""

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import audit, sim_control
from app.auth.deps import client_ip, require
from app.auth.security import User

router = APIRouter(prefix="/api/sim", tags=["sim"])

COOLDOWN_S = 2.0

SCENARIOS = [
    {"id": "feeder_transient", "name": "出线瞬时故障", "target": "feeder",
     "desc": "过流保护 0.3s 跳闸 → 1s 后重合闸 → 重合成功，线路恢复供电"},
    {"id": "feeder_permanent", "name": "出线永久故障", "target": "feeder",
     "desc": "保护跳闸 → 重合闸 → 合于故障后加速 0.04s 再次跳闸，线路停电；消除故障后可遥控送电"},
    {"id": "feeder_ground", "name": "出线单相接地", "target": "feeder",
     "desc": "10kV 小电流接地系统不跳闸：母线 3U0 升高、接地告警；遥控逐条拉开出线找出接地线路（拉路选线）"},
    {"id": "line_loss", "name": "110kV 进线失电", "target": "line",
     "desc": "对侧站跳闸 → 1.5s 桥备自投跳进线、合 100；10kV 分段备自投（3s）因电压已恢复不动作——时间级差配合"},
    {"id": "trafo_fault", "name": "主变内部故障", "target": "trafo",
     "desc": "差动、重瓦斯动作跳主变各侧 → 闭锁桥备自投 → 3s 后 10kV 分段备自投合 900，负荷转由另一台主变供电"},
    {"id": "overvoltage", "name": "上级电压偏高", "target": None,
     "desc": "110kV 电压升高，10kV 母线越上限（持续 10s 告警）→ 遥调主变降档使电压回到合格范围"},
    {"id": "channel_outage", "name": "通道中断", "target": None,
     "desc": "104 链路中断 30s：画面数据品质置 NT、告警；恢复后主站重连、对时、总召"},
    {"id": "clear_faults", "name": "消除故障", "target": None, "restore": True,
     "desc": "抢修完成：故障消除、线路恢复送电、电压复原；开关保持当前位置，由值班员遥控恢复"},
    {"id": "reset", "name": "恢复正常方式", "target": None, "restore": True,
     "desc": "一键恢复：消除故障、全部开关回到正常位置、档位与限值复原"},
]  # fmt: skip
_BY_ID = {s["id"]: s for s in SCENARIOS}


def _targets(st) -> dict[str, list[dict]]:
    return {
        "feeder": [{"id": b.id, "name": b.name} for b in st.bays if b.kind == "feeder"],
        "line": [{"id": b.id, "name": b.name} for b in st.bays if b.kind == "line_in"],
        "trafo": [
            {"id": e.id, "name": b.name} for b in st.bays for e in b.equipment if e.type == "transformer"
        ],
    }


@router.get("", summary="场景列表与自动装置状态")
async def info(request: Request) -> dict:
    st = request.app.state
    auto = st.auto_reset.info() if getattr(st, "auto_reset", None) else {"enabled": False}
    return {"scenarios": SCENARIOS, "targets": _targets(st.station), "state": await sim_control.state(st.redis),
            "auto_reset": auto}  # fmt: skip


@router.get("/state", summary="自动装置状态（备自投充电、重合闸充电、当前故障）")
async def state(request: Request) -> dict | None:
    return await sim_control.state(request.app.state.redis)


class InjectReq(BaseModel):
    scenario: str
    target: str | None = None


_last_inject = 0.0
_lock = asyncio.Lock()


@router.post("/inject", summary="注入场景")
async def inject(req: InjectReq, request: Request, user: User = Depends(require("sim"))) -> dict:
    global _last_inject
    st = request.app.state
    sc = _BY_ID.get(req.scenario)
    if sc is None:
        raise HTTPException(400, "未知场景")
    if sc["target"]:
        valid = {t["id"] for t in _targets(st.station)[sc["target"]]}
        if req.target not in valid:
            raise HTTPException(400, f"请选择对象：{'、'.join(sorted(valid))}")
    if await sim_control.state(st.redis) is None:
        raise HTTPException(503, "仿真子站未连接仿真控制通道")
    async with _lock:
        wait = COOLDOWN_S - (time.monotonic() - _last_inject)
        if wait > 0:
            raise HTTPException(429, f"操作过于频繁，请 {wait:.0f} 秒后再试")
        _last_inject = time.monotonic()
    res = await sim_control.send(st.redis, req.scenario, req.target)
    await audit.write(st.pool, "sim.inject", user=user, target=f"{sc['name']} {req.target or ''}".strip(),
                      ok=res["ok"], detail={"scenario": req.scenario, "detail": res["detail"]},
                      ip=client_ip(request))  # fmt: skip
    if not res["ok"]:
        raise HTTPException(409, res["detail"])
    return res
