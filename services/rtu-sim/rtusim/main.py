"""仿真子站（RTU）入口：潮流仿真 + IEC 104 子站。

数据上送策略（与实际 RTU 一致）：
    - 遥信变位：立即以带 CP56Time2a 时标的类型突发上送（SOE），时标取 RTU 本地时钟
    - 遥测：每个仿真步与上次上送值比较，超过死区才突发上送
    - 电度：只在主站电度召唤时上送
"""

import asyncio
import contextlib
import logging
import os
import signal

from scadalib.iec104 import COT, DPI, RCS, CmdCheck, Iec104Server, InfoObject, TypeID
from scadalib.iec104.describe import describe
from scadalib.model import Cat, Point, Station, default_config_path, load_station

from .control import SimControl
from .simulator import Simulator, Value

log = logging.getLogger("rtusim")


class RtuHandler:
    """实现 Iec104Server 的 ServerHandler 协议。"""

    def __init__(self, st: Station, sim: Simulator):
        self.st, self.sim = st, sim

    # ---- 召唤
    def interrogation(self) -> dict[TypeID, list[InfoObject]]:
        out: dict[TypeID, list[InfoObject]] = {}
        for p in self.st.points:
            if p.cat in (Cat.YX, Cat.YC):
                v = self.sim.values[p.id]
                out.setdefault(p.gi_type, []).append(InfoObject(p.ioa, v.v, v.q))
        return out

    def counters(self) -> list[InfoObject]:
        return [InfoObject(p.ioa, int(self.sim.values[p.id].v)) for p in self.st.points_of(Cat.DD)]

    def clock_sync(self, master_time) -> None:
        self.sim.clock_sync(master_time)

    # ---- 命令
    def _cmd_point(self, type_id: TypeID, ioa: int) -> Point | None:
        p = self.st.by_ioa(ioa)
        return p if p and p.cmd_type == type_id else None

    def check_command(self, type_id: TypeID, obj: InfoObject) -> CmdCheck:
        p = self._cmd_point(type_id, obj.ioa)
        if p is None:
            return CmdCheck.UNKNOWN_IOA
        reason: str | None = None
        match p.meas:
            case "YK":
                if obj.value not in (DPI.ON, DPI.OFF):
                    reason = "非法遥控值"
                else:
                    reason = self.sim.interlock_reason(p.ref, obj.value == DPI.ON)
            case "TAPCMD":
                reason = self.sim.tap_reason(p.ref, obj.value == RCS.HIGHER)
            case "PSET":
                cap = self.st.bay(p.bay).attrs["capacity_mw"]
                if not 0 <= obj.value <= cap:
                    reason = f"设定值超出 0~{cap} MW"
        if reason:
            log.info("command rejected %s %s: %s", p.id, obj.value, reason)
            return CmdCheck.REJECT
        return CmdCheck.OK

    def requires_select(self, type_id: TypeID, ioa: int) -> bool:
        return type_id == TypeID.C_DC_NA_1  # 遥控必须选择-执行；调档、设点直接执行

    async def execute_command(self, type_id: TypeID, obj: InfoObject) -> bool:
        p = self._cmd_point(type_id, obj.ioa)
        assert p is not None
        log.info("execute %s → %s", p.id, obj.value)
        match p.meas:
            case "YK":
                return await self.sim.operate_switch(p.ref, obj.value == DPI.ON, source="remote")
            case "TAPCMD":
                return await self.sim.change_tap(p.ref, obj.value == RCS.HIGHER)
            case "PSET":
                self.sim.set_pv_limit(p.bay, float(obj.value))
                return True
        return False


class Rtu:
    def __init__(self, st: Station, sim: Simulator, server: Iec104Server, step_s: float):
        self.st, self.sim, self.server, self.step_s = st, sim, server, step_s
        self.last_sent: dict[str, Value] = {}
        self._yx_queue: asyncio.Queue[tuple[Point, Value, object]] = asyncio.Queue()
        sim.on_yx = lambda p, v, ts: self._yx_queue.put_nowait((p, v, ts))

    async def run(self, *extra) -> None:
        await self.server.start()
        await asyncio.gather(self._sim_loop(), self._yx_loop(), self.sim.auto.run(), *extra)

    async def outage(self, seconds: float) -> None:
        """模拟通道中断：断开全部链路并停止监听，到时恢复（主站将重连、总召）。"""
        log.warning("simulated channel outage for %.0fs", seconds)
        await self.server.stop()
        await asyncio.sleep(seconds)
        await self.server.start()
        log.warning("channel restored")

    async def _sim_loop(self) -> None:
        loop = asyncio.get_running_loop()
        last = loop.time()
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.sim.changed.wait(), timeout=self.step_s)
            self.sim.changed.clear()
            now = loop.time()
            # 潮流计算约 30~50ms，放到线程中避免阻塞规约收发
            await asyncio.to_thread(self.sim.step, now - last)
            last = now
            await self._publish_analogs()

    async def _publish_analogs(self) -> None:
        changed: list[InfoObject] = []
        for p in self.st.points_of(Cat.YC):
            v = self.sim.values[p.id]
            old = self.last_sent.get(p.id)
            if old is None or old.q != v.q or abs(float(v.v) - float(old.v)) >= max(p.deadband, 1e-9):
                changed.append(InfoObject(p.ioa, float(v.v), v.q))
                self.last_sent[p.id] = v
        await self.server.publish(TypeID.M_ME_NC_1, changed, COT.SPONTANEOUS)

    async def _yx_loop(self) -> None:
        while True:
            p, v, ts = await self._yx_queue.get()
            await self.server.publish(p.spont_type, [InfoObject(p.ioa, v.v, v.q, time=ts)], COT.SPONTANEOUS)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    st = load_station(default_config_path())
    sim = Simulator(st, speed=float(os.environ.get("SIM_SPEED", "60")))
    frame_log = logging.getLogger("rtusim.frames")

    def on_frame(direction: str, raw: bytes) -> None:
        if frame_log.isEnabledFor(logging.DEBUG):
            frame_log.debug("%s %s | %s", direction, raw.hex(" "), describe(raw))

    server = Iec104Server(
        RtuHandler(st, sim),
        ca=st.ca,
        port=int(os.environ.get("IEC104_PORT", "2404")),
        on_frame=on_frame,
        on_connection=lambda peer, up: log.info("master %s %s", peer, "connected" if up else "disconnected"),
    )
    log.info("RTU %s: %d points, sim speed x%s, clock offset %.2fs",
             st.name, len(st.points), sim.speed, sim.clock_offset_s)  # fmt: skip
    rtu = Rtu(st, sim, server, step_s=float(os.environ.get("SIM_STEP_S", "1.0")))

    async def runner():
        loop = asyncio.get_running_loop()
        extra = []
        if url := os.environ.get("REDIS_URL"):
            from redis.asyncio import Redis

            extra.append(SimControl(sim, Redis.from_url(url, decode_responses=True), rtu.outage).run())
        else:
            log.info("REDIS_URL not set: simulation control channel disabled")
        task = asyncio.create_task(rtu.run(*extra))
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
        try:
            await task
        except asyncio.CancelledError:
            await server.stop()

    asyncio.run(runner())


if __name__ == "__main__":
    main()
