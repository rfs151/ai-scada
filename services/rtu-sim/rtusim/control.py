"""仿真控制通道（仅演示用）：故障注入、通道中断、恢复正常方式。

真实系统中故障来自一次设备，不存在这条通道；这里经 Redis 与 api 通信，
与 IEC 104 数据通道完全独立，不影响规约行为。

    sim:cmd          List   api LPUSH {"id","scenario","target"}，本模块 BLPOP
    sim:res:<id>     List   执行结果 {"ok","detail"}，60s 过期
    sim:state        String 自动装置状态 JSON（备自投充电、重合闸充电、当前故障…），每秒刷新
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis

from .simulator import Simulator

log = logging.getLogger(__name__)

CMD_KEY = "sim:cmd"
STATE_KEY = "sim:state"
OUTAGE_S = 30.0
HIGH_GRID_VM = 1.075


def res_key(req_id: str) -> str:
    return f"sim:res:{req_id}"


class SimControl:
    def __init__(self, sim: Simulator, redis: Redis, outage: Callable[[float], Awaitable[None]]):
        self.sim, self.redis, self._outage = sim, redis, outage
        self.outage_until: float | None = None

    async def run(self) -> None:
        await asyncio.gather(self._cmd_loop(), self._state_loop())

    async def _cmd_loop(self) -> None:
        while True:
            try:
                item = await self.redis.blpop([CMD_KEY], timeout=5)
                if not item:
                    continue
                req = json.loads(item[1])
                try:
                    detail = await self.execute(req["scenario"], req.get("target"))
                    res = {"ok": True, "detail": detail}
                except (ValueError, KeyError) as e:
                    res = {"ok": False, "detail": str(e)}
                log.info("scenario %s %s → %s", req.get("scenario"), req.get("target"), res)
                key = res_key(req["id"])
                await self.redis.rpush(key, json.dumps(res, ensure_ascii=False))
                await self.redis.expire(key, 60)
                await self._publish_state()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("sim control error")
                await asyncio.sleep(2)

    async def execute(self, scenario: str, target: str | None) -> str:
        auto, sim = self.sim.auto, self.sim
        match scenario:
            case "feeder_transient" | "feeder_permanent":
                return auto.inject_feeder_fault(_need(target), "short", scenario == "feeder_permanent")
            case "feeder_ground":
                return auto.inject_feeder_fault(_need(target), "ground", True)
            case "trafo_fault":
                return auto.inject_trafo_fault(_need(target))
            case "line_loss":
                return auto.lose_line(_need(target))
            case "meas_invert" | "meas_freeze" | "meas_scale":
                detail = sim.inject_meas_fault(_need(target), scenario.removeprefix("meas_"))
                auto._log(detail)
                return detail
            case "overvoltage":
                sim.set_grid_voltage(HIGH_GRID_VM)
                auto._log(f"上级 220kV 站 110kV 母线电压升高至 {HIGH_GRID_VM:.3f} pu")
                return "上级电压升高，10kV 母线电压将越上限，可遥调主变降档"
            case "channel_outage":
                if self.outage_until and time.monotonic() < self.outage_until:
                    raise ValueError("通道已处于中断状态")
                self.outage_until = time.monotonic() + OUTAGE_S
                asyncio.get_running_loop().create_task(self._outage(OUTAGE_S))
                return f"104 通道中断 {OUTAGE_S:.0f} 秒"
            case "clear_faults":
                sim.set_grid_voltage(float(sim.st.grid["vm_pu"]))
                sim.meas_faults.clear()
                return auto.clear_faults()
            case "reset":
                sim.reset_normal()
                return "已恢复正常运行方式"
        raise ValueError(f"未知场景 {scenario}")

    async def _state_loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            try:
                await self._publish_state()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.debug("publish sim state failed", exc_info=True)

    async def _publish_state(self) -> None:
        st = self.sim.auto.status()
        st["grid_vm"] = self.sim.grid_vm
        st["meas_faults"] = {pid: kind for pid, (kind, _) in self.sim.meas_faults.items()}
        remaining = (self.outage_until or 0) - time.monotonic()
        st["outage_s"] = round(remaining) if remaining > 0 else 0
        st["t"] = int(time.time() * 1000)
        await self.redis.set(STATE_KEY, json.dumps(st, ensure_ascii=False), ex=10)


def _need(target: str | None) -> str:
    if not target:
        raise ValueError("该场景需要指定对象")
    return target
