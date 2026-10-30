"""站内自动装置仿真：线路保护、重合闸、主变保护、备自投、小电流接地。

真实变电站里这些功能由保护装置 / 备自投装置就地完成，不经过调度主站；
SCADA 只看到结果：保护动作信号、开关变位（SOE 毫秒时标）、遥测变化。
这里按装置逻辑在仿真子站内实现，时序参数取常见整定值：

    出线过流 II 段     0.3s 跳闸
    一次重合闸         跳闸后 1.0s 重合，动作后 15s 充电完成才能再次重合
    后加速             重合或手合于故障，0.04s 无延时跳闸，不再重合
    主变差动 / 重瓦斯  0.04s 跳主变各侧断路器，闭锁 110kV 桥备自投
    110kV 桥备自投     进线失压、对侧有压，1.5s 跳失压进线、合桥开关
    10kV 分段备自投    母线失压、对侧有压，3.0s 跳主变低压侧、合分段开关
    备自投充电         正常运行方式持续 10s 才"充电"完成；手动分合相关开关立即放电

两级备自投时间级差配合：进线失压时 110kV 桥备自投先动作恢复供电，
10kV 母线在 3s 内已恢复电压，分段备自投不动作——避免两级同时动作。
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from scadalib.iec104 import DPI

if TYPE_CHECKING:
    from .simulator import Simulator

log = logging.getLogger(__name__)

OVERCURRENT_S = 0.3
ACCEL_S = 0.04
ACCEL_WINDOW_S = 3.0  # 合闸后该时间内保护按后加速动作
RECLOSE_S = 1.0
RECLOSE_CHARGE_S = 15.0
DIFF_S = 0.04
BZT_CHARGE_S = 10.0
SIGNAL_PULSE_S = 0.8  # 保护/自动装置动作信号返回时间
ACC_HOLD_S = 10.0  # 事故总保持时间
LIVE_PU, DEAD_PU = 0.8, 0.3
MONITOR_S = 0.1
GROUND_3U0_V = 92.0  # 金属性单相接地时开口三角电压接近 100V


@dataclass
class Fault:
    kind: str  # short 相间短路 | ground 单相接地
    permanent: bool
    fault_ka: float = 0.0


@dataclass
class Bzt:
    """备自投：tie 为母联/桥开关，sides 为 (检测节点, 该侧进线开关)。"""

    bay: str
    name: str
    tie: str
    sides: list[tuple[str, str]]
    delay_s: float
    block_on_trafo_fault: bool = False
    charged_since: float | None = None
    acting: bool = False
    acted_at: float | None = None

    def charged(self, now: float) -> bool:
        return self.charged_since is not None and now - self.charged_since >= BZT_CHARGE_S

    def discharge(self) -> None:
        self.charged_since = None


@dataclass
class TrafoProt:
    trafo: str
    bay: str
    nodes: tuple[str, str]
    trips: list[str]
    faulted: bool = False
    busy: bool = False


@dataclass
class FeederProt:
    bay: str
    cb: str
    out_node: str
    bus_node: str
    reclose_ready_at: float = 0.0
    closed_at: float = -1e9
    busy: bool = False
    has_reclose: bool = True


class Automation:
    def __init__(self, sim: Simulator):
        self.sim = sim
        st = sim.st
        self.faults: dict[str, Fault] = {}
        self.line_lost: set[str] = set()
        self.events: list[dict] = []  # 最近的装置动作记录（供场景面板展示）
        self._acc_handle: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task] = set()

        self.feeders: dict[str, FeederProt] = {}
        for b in st.bays:
            if b.kind in ("feeder", "pv"):
                cb = next(e for e in b.equipment if e.type == "breaker")
                self.feeders[b.id] = FeederProt(
                    b.id,
                    cb.id,
                    out_node=cb.nodes[1],
                    bus_node=cb.nodes[0],
                    has_reclose=f"{b.id}.RECL" in sim.signals,
                )
        self.trafos: dict[str, TrafoProt] = {}
        for b in st.bays:
            if b.kind == "transformer":
                tr = next(e for e in b.equipment if e.type == "transformer")
                hv, lv = tr.attrs["hv"], tr.attrs["lv"]
                self.trafos[tr.id] = TrafoProt(tr.id, b.id, (hv, lv), trips=self._trafo_trips(tr.id, hv))
        self.bzts: dict[str, Bzt] = {}
        if self._has("100", "101", "102"):
            self.bzts["B100"] = Bzt("B100", "110kV 内桥备自投", "100", [("HB1", "101"), ("HB2", "102")], 1.5,
                                    block_on_trafo_fault=True)  # fmt: skip
        if self._has("900", "901", "902"):
            self.bzts["B900"] = Bzt("B900", "10kV 分段备自投", "900", [("BUS1", "901"), ("BUS2", "902")], 3.0)

    def _has(self, *ids: str) -> bool:
        return all(i in self.sim.switch for i in ids)

    def _trafo_trips(self, trafo: str, hv_node: str) -> list[str]:
        """主变保护跳闸矩阵：跳主变低压侧断路器，以及与高压侧母线相连的所有 110kV 断路器（内桥接线高压侧无断路器）。"""
        st = self.sim.st
        bay = next(b for b in st.bays if any(e.id == trafo for e in b.equipment))
        trips = [e.id for e in bay.equipment if e.type == "breaker"]
        # 高压侧：经刀闸连到 HBx 节点，再找挂在 HBx 上的断路器（进线、桥）
        hb = {n for e in bay.equipment if e.type == "disconnector" for n in e.nodes if n != hv_node}
        for e in st.equipment:
            if e.type == "disconnector" and hb & set(e.nodes):
                far = next(n for n in e.nodes if n not in hb)
                trips += [b.id for b in st.equipment if b.type == "breaker" and far in b.nodes]
        return sorted(set(trips))

    # ================================================================ 工具

    def _vm(self, node: str) -> float:
        try:
            v = float(self.sim.net.res_bus.at[self.sim.idx.bus[node], "vm_pu"])
        except (KeyError, ValueError):
            return 0.0
        return 0.0 if math.isnan(v) else v

    def _spawn(self, coro) -> None:
        t = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def _pulse(self, pid: str, hold_s: float = SIGNAL_PULSE_S) -> None:
        if pid not in self.sim.signals:
            return
        self.sim.set_signal(pid, True)
        asyncio.get_running_loop().call_later(hold_s, self.sim.set_signal, pid, False)

    def _accident(self) -> None:
        """事故总：任一保护动作即置位，保持 ACC_HOLD_S 后自动复归（实际站内由运行人员复归）。"""
        self.sim.set_signal("STN.ACC", True)
        if self._acc_handle:
            self._acc_handle.cancel()
        self._acc_handle = asyncio.get_running_loop().call_later(
            ACC_HOLD_S, self.sim.set_signal, "STN.ACC", False
        )

    def _log(self, text: str) -> None:
        log.info("automation: %s", text)
        self.events = ([{"t": time.time(), "text": text}] + self.events)[:30]

    # ================================================================ 故障注入

    def inject_feeder_fault(self, bay: str, kind: str, permanent: bool) -> str:
        fp = self.feeders.get(bay)
        if fp is None:
            raise ValueError(f"{bay} 不是出线间隔")
        name = self.sim.st.bay(bay).name
        if kind == "short":
            self.faults[bay] = Fault("short", permanent, fault_ka=3.8 if permanent else 3.2)
            self._log(f"{name} {'永久性' if permanent else '瞬时性'}相间短路")
        else:
            self.faults[bay] = Fault("ground", True)
            self._log(f"{name} 单相接地")
        self.sim.changed.set()
        return f"{name} 已注入{'单相接地' if kind == 'ground' else ('永久' if permanent else '瞬时') + '短路'}故障"

    def inject_trafo_fault(self, trafo: str) -> str:
        tp = self.trafos.get(trafo)
        if tp is None:
            raise ValueError(f"{trafo} 不是主变")
        tp.faulted = True
        self._log(f"{self.sim.st.bay(tp.bay).name} 内部故障")
        return f"{self.sim.st.bay(tp.bay).name} 已注入内部故障"

    def lose_line(self, line: str) -> str:
        idx = self.sim.idx.line.get(line)
        if idx is None:
            raise ValueError(f"{line} 不是线路")
        self.sim.net.line.at[idx, "in_service"] = False
        self.line_lost.add(line)
        self._log(f"{line} 对侧 220kV 站跳闸，线路失电")
        self.sim.changed.set()
        return f"{line} 线路失电（对侧跳闸）"

    def clear_faults(self) -> str:
        """抢修完成：消除所有故障、线路恢复送电，开关保持当前位置（由运行人员遥控恢复）。"""
        self.faults.clear()
        for tp in self.trafos.values():
            tp.faulted = False
        for line in self.line_lost:
            self.sim.net.line.at[self.sim.idx.line[line], "in_service"] = True
        self.line_lost.clear()
        self._log("故障消除，线路恢复送电")
        self.sim.changed.set()
        return "故障已消除，开关保持当前位置，可遥控恢复送电"

    def reset(self) -> None:
        self.clear_faults()
        for b in self.bzts.values():
            b.discharge()
            b.acting = False
        for f in self.feeders.values():
            f.reclose_ready_at = 0.0

    # ================================================================ 开关变位钩子

    def on_switch(self, eid: str, closed: bool, source: str) -> None:
        now = time.monotonic()
        for f in self.feeders.values():
            if f.cb == eid and closed:
                f.closed_at = now
        # 手动（遥控）操作备自投相关开关：备自投放电，避免手分进线后备自投误合母联
        if source == "remote":
            for b in self.bzts.values():
                if eid == b.tie or eid in (cb for _, cb in b.sides):
                    if b.charged_since is not None:
                        self._log(f"{b.name} 放电（手动操作 {eid}）")
                    b.discharge()

    # ================================================================ 监视循环

    async def run(self) -> None:
        while True:
            await asyncio.sleep(MONITOR_S)
            try:
                self._tick(time.monotonic())
            except Exception:  # noqa: BLE001
                log.exception("automation tick failed")

    def _tick(self, now: float) -> None:
        sw = self.sim.switch
        # ---- 出线保护
        for bay, fault in list(self.faults.items()):
            fp = self.feeders[bay]
            if (
                fault.kind == "short"
                and sw[fp.cb] == DPI.ON
                and not fp.busy
                and self._vm(fp.bus_node) > DEAD_PU
            ):
                fp.busy = True
                accel = now - fp.closed_at < ACCEL_WINDOW_S
                self._spawn(self._feeder_trip(fp, accel))
        # ---- 主变保护：故障主变任一侧带电即动作
        for tp in self.trafos.values():
            if tp.faulted and not tp.busy and max(self._vm(n) for n in tp.nodes) > DEAD_PU:
                tp.busy = True
                self._spawn(self._trafo_trip(tp))
        # ---- 接地告警：故障出线开关合位且母线带电，则该母线（及经分段相连的母线）3U0 升高
        grounded = self.ground_buses()
        for b in self.sim.st.bays:
            if b.kind == "bus":
                pid = f"{b.id}.GND"
                if pid in self.sim.signals and self.sim.signals[pid] != (b.attrs["node"] in grounded):
                    self.sim.set_signal(pid, b.attrs["node"] in grounded)
        # ---- 备自投
        trafo_fault = any(t.faulted for t in self.trafos.values())
        for bz in self.bzts.values():
            if bz.acting:
                continue
            blocked = bz.block_on_trafo_fault and trafo_fault
            normal = (
                sw[bz.tie] == DPI.OFF
                and all(sw[cb] == DPI.ON and self._vm(n) > LIVE_PU for n, cb in bz.sides)
                and not blocked
            )
            if normal:
                if bz.charged_since is None:
                    bz.charged_since = now
                continue
            if not bz.charged(now):
                bz.discharge()
                continue
            if blocked:
                self._log(f"{bz.name} 被主变保护闭锁")
                bz.discharge()
                continue
            dead = self._bzt_dead_side(bz)
            if dead is not None and sw[bz.tie] == DPI.OFF:
                bz.acting = True
                self._spawn(self._bzt_act(bz, dead))
            elif sw[bz.tie] != DPI.OFF:
                bz.discharge()

    def ground_buses(self) -> set[str]:
        sw = self.sim.switch
        out: set[str] = set()
        for bay, fault in self.faults.items():
            fp = self.feeders[bay]
            if fault.kind == "ground" and sw[fp.cb] == DPI.ON and self._vm(fp.bus_node) > DEAD_PU:
                out.add(fp.bus_node)
        if out and "900" in sw and sw["900"] == DPI.ON:
            out |= {"BUS1", "BUS2"}
        return out

    def _bzt_dead_side(self, bz: Bzt) -> int | None:
        (n1, _), (n2, _) = bz.sides
        v1, v2 = self._vm(n1), self._vm(n2)
        if v1 < DEAD_PU and v2 > LIVE_PU:
            return 0
        if v2 < DEAD_PU and v1 > LIVE_PU:
            return 1
        return None

    # ================================================================ 动作过程

    async def _feeder_trip(self, fp: FeederProt, accel: bool) -> None:
        sim, name = self.sim, self.sim.st.bay(fp.bay).name
        try:
            await asyncio.sleep(ACCEL_S if accel else OVERCURRENT_S)
            fault = self.faults.get(fp.bay)
            if fault is None or sim.switch[fp.cb] != DPI.ON:
                return
            self._pulse(f"{fp.bay}.PROT")
            self._accident()
            self._log(f"{name} {'后加速' if accel else '过流II段'}保护动作，跳 {fp.cb}")
            await sim.operate_switch(fp.cb, False, source="prot")
            if not fault.permanent:
                self.faults.pop(fp.bay, None)  # 瞬时故障：断路器跳开后电弧熄灭
            sim.changed.set()
            now = time.monotonic()
            if accel or not fp.has_reclose or now < fp.reclose_ready_at:
                if not accel and fp.has_reclose:
                    self._log(f"{name} 重合闸未充电，不重合")
                return
            await asyncio.sleep(RECLOSE_S)
            if sim.switch[fp.cb] != DPI.OFF:
                return
            fp.reclose_ready_at = time.monotonic() + RECLOSE_CHARGE_S
            self._pulse(f"{fp.bay}.RECL")
            self._log(f"{name} 重合闸动作，合 {fp.cb}")
            await sim.operate_switch(fp.cb, True, source="recl")
            if fp.bay not in self.faults:
                self._log(f"{name} 重合成功")
        finally:
            fp.busy = False

    async def _trafo_trip(self, tp: TrafoProt) -> None:
        sim, name = self.sim, self.sim.st.bay(tp.bay).name
        try:
            await asyncio.sleep(DIFF_S)
            if not tp.faulted:
                return
            self._pulse(f"{tp.bay}.DIFF", 1.5)
            self._accident()
            await asyncio.sleep(0.015)
            self._pulse(f"{tp.bay}.GAS_H", 1.5)
            to_trip = [cb for cb in tp.trips if sim.switch[cb] == DPI.ON]
            self._log(f"{name} 差动保护、重瓦斯动作，跳 {'、'.join(to_trip) or '（已无合位开关）'}")
            await asyncio.gather(*(sim.operate_switch(cb, False, source="prot") for cb in to_trip))
            sim.changed.set()
            await asyncio.sleep(0.5)  # 等潮流更新后再允许再次判断（防止重复动作）
        finally:
            tp.busy = False

    async def _bzt_act(self, bz: Bzt, dead: int) -> None:
        sim = self.sim
        node, incoming = bz.sides[dead]
        try:
            t0 = time.monotonic()
            while time.monotonic() - t0 < bz.delay_s:
                await asyncio.sleep(MONITOR_S)
                if self._bzt_dead_side(bz) != dead or sim.switch[bz.tie] != DPI.OFF:
                    self._log(f"{bz.name} 启动后返回（{node} 电压已恢复），不动作")
                    return
            self._pulse(f"{bz.bay}.BZT", 2.0)
            self._log(f"{bz.name} 动作：{node} 失压，跳 {incoming}、合 {bz.tie}")
            if sim.switch[incoming] == DPI.ON:
                await sim.operate_switch(incoming, False, source="bzt")
            await asyncio.sleep(0.1)
            await sim.operate_switch(bz.tie, True, source="bzt")
            bz.acted_at = time.monotonic()
        finally:
            bz.discharge()
            bz.acting = False

    # ================================================================ 状态（供场景面板）

    def status(self) -> dict:
        now = time.monotonic()
        return {
            "faults": [
                {"bay": b, "name": self.sim.st.bay(b).name, "kind": f.kind, "permanent": f.permanent}
                for b, f in self.faults.items()
            ]
            + [
                {"bay": t.bay, "name": self.sim.st.bay(t.bay).name, "kind": "trafo", "permanent": True}
                for t in self.trafos.values()
                if t.faulted
            ],
            "line_lost": sorted(self.line_lost),
            "bzt": {
                b.bay: {
                    "name": b.name,
                    "charged": b.charged(now),
                    "charging_s": None
                    if b.charged_since is None or b.charged(now)
                    else round(BZT_CHARGE_S - (now - b.charged_since), 1),
                    "acting": b.acting,
                }
                for b in self.bzts.values()
            },
            "reclose": {
                f.bay: max(0.0, round(f.reclose_ready_at - now, 1))
                for f in self.feeders.values()
                if f.has_reclose
            },
            "events": self.events[:12],
        }
