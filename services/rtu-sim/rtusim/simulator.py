"""变电站仿真：设备状态 + 负荷曲线 + 潮流计算 → 全站遥测/遥信/电度。

时间：仿真时钟按 speed 倍速推进（默认 60 倍，1 分钟 = 1 小时，演示时可以看到完整日负荷曲线）；
     SOE 等时标使用"RTU 本地时钟" = 真实时间 + 时钟偏差（启动时随机偏差，主站对时后校正）。
"""

import asyncio
import logging
import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from scadalib import simclock
from scadalib.iec104 import DPI, Quality
from scadalib.model import Cat, Point, Station

from . import grid
from .automation import GROUND_3U0_V, Automation
from .profiles import OU, profile_value, pv_clear_sky

log = logging.getLogger(__name__)

BREAKER_OP_S = 0.06  # 断路器分合闸时间
DISCONNECTOR_TRAVEL_S = 3.0  # 电动刀闸行程时间（期间为双点"中间态"）
OLTC_OP_S = 5.0  # 有载调压一档动作时间
TAP_MIN, TAP_MAX = -8, 8
AMBIENT_MEAN_C = 25.0


@dataclass(slots=True)
class Value:
    v: float | int | bool | DPI
    q: Quality = Quality.GOOD


# (测点 id, 新值, RTU 时标) —— 遥信变位回调，用于立即突发上送 SOE
YxCallback = Callable[[Point, Value, datetime], None]


class Simulator:
    def __init__(self, st: Station, *, speed: float = 60.0, seed: int | None = None,
                 start_hour: float | None = None, tz: str = "Asia/Shanghai"):  # fmt: skip
        self.st, self.speed = st, speed
        self.rng = random.Random(seed)
        self.net, self.idx = grid.build_net(st)
        self.on_yx: YxCallback | None = None
        self.changed = asyncio.Event()  # 设备状态变化，主循环应立即重算潮流

        # ---- 时钟
        # 默认按 scadalib.simclock 对齐本地时间（主站据此换算仿真钟点）；start_hour 仅供测试固定钟点
        self.tz = tz
        self._t0_real = time.time()
        self._t0_hour = start_hour
        self.clock_offset_s = self.rng.choice([-1, 1]) * self.rng.uniform(0.8, 2.5)  # 未对时的 RTU 时钟偏差
        self.clock_synced_at: datetime | None = None

        # ---- 设备状态
        self.switch: dict[str, DPI] = {
            e.id: DPI.ON if e.normally_closed else DPI.OFF for e in st.equipment if e.is_switch
        }
        self.moving: set[str] = set()
        self.tap: dict[str, int] = {e.id: int(e.attrs.get("tap", 0)) for e in st.equipment
                                    if e.type == "transformer"}  # fmt: skip
        self.tap_busy: set[str] = set()
        self.signals: dict[str, bool] = {p.id: False for p in st.points if p.cat == Cat.YX and not p.double}
        self.pv_limit: dict[str, float] = {
            b.id: float(b.attrs["capacity_mw"]) for b in st.bays if b.kind == "pv"
        }
        self.oil_c: dict[str, float] = {t: 45.0 for t in self.tap}
        self.energy: dict[str, float] = {
            p.id: 1_000_000.0 + self.rng.randrange(0, 9_000_000) for p in st.points if p.cat == Cat.DD
        }

        # ---- 随机过程
        self.freq = OU(50.0, theta=0.08, sigma=0.004, rng=self.rng)
        self.cloud = OU(0.9, theta=0.02, sigma=0.03, lo=0.25, hi=1.0, rng=self.rng)
        self.load_noise = {b.id: OU(1.0, theta=0.1, sigma=0.012, rng=self.rng) for b in st.bays
                           if b.kind == "feeder"}  # fmt: skip
        self.u0_noise = {b.id: OU(1.2, theta=0.2, sigma=0.3, lo=0.2, rng=self.rng) for b in st.bays
                         if b.kind == "bus"}  # fmt: skip

        self.grid_vm = float(st.grid["vm_pu"])
        self.auto = Automation(self)
        # 量测故障（测量回路问题，一次设备正常）：点号 → (类型, 参数)
        #   invert CT 极性接反（有功、无功符号相反）/ freeze 变送器卡死（值不刷新）/ scale 量程设置错误
        self.meas_faults: dict[str, tuple[str, float]] = {}

        self.values: dict[str, Value] = {}
        self.pf_ok = True
        self.last_step = time.monotonic()
        self._apply_switches()
        self.step(0.0)

    # ================================================================ 时钟

    def sim_hour(self) -> float:
        if self._t0_hour is None:
            return simclock.sim_hour(time.time(), self.speed, self.tz)
        return (self._t0_hour + (time.time() - self._t0_real) * self.speed / 3600) % 24

    def rtu_now(self) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=self.clock_offset_s)

    def clock_sync(self, master_time: datetime) -> None:
        # 报文传输时延忽略（局域网 < 1ms）；校正后保留极小漂移
        before = self.clock_offset_s
        self.clock_offset_s = (master_time - datetime.now(UTC)).total_seconds()
        self.clock_synced_at = master_time
        log.info("clock sync: offset %.3fs → %.3fs", before, self.clock_offset_s)

    # ================================================================ 仿真步

    def step(self, dt_real: float) -> None:
        dt_sim_h = dt_real * self.speed / 3600
        hour = self.sim_hour()
        self.freq.step(dt_real)
        self.cloud.step(dt_real)

        # ---- 负荷 / 光伏注入
        for bay in self.st.bays:
            if bay.kind == "feeder":
                p = (
                    bay.attrs["peak_mw"]
                    * profile_value(bay.attrs["profile"], hour)
                    * self.load_noise[bay.id].step(dt_real)
                )
                q = p * math.tan(math.acos(bay.attrs["pf"]))
                self.net.load.loc[self.idx.load[bay.id], ["p_mw", "q_mvar"]] = [p, q]
            elif bay.kind == "pv":
                avail = bay.attrs["capacity_mw"] * pv_clear_sky(hour) * self.cloud.x
                self.net.sgen.loc[self.idx.sgen[bay.id], ["p_mw", "q_mvar"]] = [
                    min(avail, self.pv_limit[bay.id]),
                    0.0,
                ]
        for t, pos in self.tap.items():
            self.net.trafo.loc[self.idx.trafo[t], "tap_pos"] = pos
        self.net.ext_grid.loc[:, "vm_pu"] = self.grid_vm

        self.pf_ok = grid.run_pf(self.net)
        self._thermal(dt_sim_h, hour)
        self._compute_values(dt_sim_h)

    def _thermal(self, dt_sim_h: float, hour: float) -> None:
        """IEC 60076-7 顶层油温指数模型：θ' = (θ_ss − θ)/τ。"""
        ambient = AMBIENT_MEAN_C + 5 * math.sin(math.pi * (hour - 9) / 12)
        r, d_or, x, tau_h = 4.3, 52.0, 0.8, 3.0  # 负载损耗/空载损耗比、额定油温升、油指数、时间常数
        for t in self.tap:
            k = grid.nz(self.net.res_trafo.at[self.idx.trafo[t], "loading_percent"]) / 100
            ss = ambient + d_or * ((1 + r * k * k) / (1 + r)) ** x
            self.oil_c[t] += (ss - self.oil_c[t]) * (1 - math.exp(-dt_sim_h / tau_h))

    def _noise(self, v: float, rel: float = 0.001) -> float:
        return v * (1 + self.rng.gauss(0, rel))

    def _compute_values(self, dt_sim_h: float) -> None:
        net, idx, n = self.net, self.idx, grid.nz
        q = Quality.GOOD if self.pf_ok else Quality.IV
        vals: dict[str, Value] = {}
        # 故障期间的电气量：短路电流、故障母线电压跌落、接地母线 3U0
        fault_i: dict[str, float] = {}
        sag_buses: set[str] = set()
        for bay, f in self.auto.faults.items():
            fp = self.auto.feeders[bay]
            if f.kind == "short" and self.switch[fp.cb] == DPI.ON:
                fault_i[bay] = f.fault_ka * 1000
                sag_buses.add(fp.bus_node)
        grounded = self.auto.ground_buses()

        for p in self.st.points:
            if p.cat == Cat.YX:
                vals[p.id] = Value(self.switch[p.ref] if p.double else self.signals[p.id])
                continue
            if p.cat not in (Cat.YC, Cat.DD):
                continue
            bay = self.st.bay(p.bay)
            v: float = 0.0
            match bay.kind, p.meas:
                # -------- 110kV 进线：线路"to"端为站内侧，受入功率为正
                case "line_in", "P":
                    v = -n(net.res_line.at[idx.line[p.ref], "p_to_mw"])
                case "line_in", "Q":
                    v = -n(net.res_line.at[idx.line[p.ref], "q_to_mvar"])
                case "line_in", "I":
                    v = n(net.res_line.at[idx.line[p.ref], "i_to_ka"]) * 1000
                case "line_in", "COS":
                    r = net.res_line.loc[idx.line[p.ref]]
                    v = grid.cos_phi(n(r.p_to_mw), n(r.q_to_mvar))
                case "line_in", "UAB":
                    v = n(net.res_bus.at[idx.bus[self.st.equip(p.ref).attrs["to"]], "vm_pu"]) * 110
                # -------- 主变
                case "transformer", meas:
                    r = net.res_trafo.loc[idx.trafo[p.ref]]
                    v = {
                        "HP": n(r.p_hv_mw), "HQ": n(r.q_hv_mvar), "HI": n(r.i_hv_ka) * 1000,
                        "LP": -n(r.p_lv_mw), "LQ": -n(r.q_lv_mvar), "LI": n(r.i_lv_ka) * 1000,
                        "LOAD": n(r.loading_percent), "TOIL": self.oil_c[p.ref],
                        "TAP": grid.tap_to_position(self.tap[p.ref]),
                        "E_IMP": 0.0,
                    }[meas]  # fmt: skip
                    if meas == "E_IMP":
                        self.energy[p.id] += max(0.0, -n(r.p_lv_mw)) * 1000 * dt_sim_h
                        v = self.energy[p.id]
                # -------- 10kV 母线
                case "bus", ("UAB" | "UBC" | "UCA") as ph:
                    u = n(net.res_bus.at[idx.bus[bay.attrs["node"]], "vm_pu"]) * 10
                    unbalance = {"UAB": 1.0, "UBC": 1.0018, "UCA": 0.9986}[ph]  # 轻微三相不平衡
                    v = u * unbalance * (0.55 if bay.attrs["node"] in sag_buses else 1.0)
                case "bus", "U0":
                    energized = n(net.res_bus.at[idx.bus[bay.attrs["node"]], "vm_pu"]) > 0.1
                    v = self.u0_noise[bay.id].step() if energized else 0.0
                    if energized and bay.attrs["node"] in grounded:
                        v += GROUND_3U0_V  # 小电流接地系统单相接地：线电压不变，零序电压升高
                case "bus", "F":
                    energized = n(net.res_bus.at[idx.bus[bay.attrs["node"]], "vm_pu"]) > 0.1
                    v = self.freq.x if energized else 0.0
                # -------- 10kV 出线 / 光伏
                case (("feeder" | "pv"), meas):
                    r = net.res_line.loc[idx.line[p.ref]]
                    sign = -1 if bay.kind == "pv" else 1  # 光伏线路以送出为正
                    match meas:
                        case "P":
                            v = sign * n(r.p_from_mw)
                        case "Q":
                            v = sign * n(r.q_from_mvar)
                        case "I":
                            v = fault_i.get(bay.id, n(r.i_from_ka) * 1000)
                        case "COS":
                            v = grid.cos_phi(n(r.p_from_mw), n(r.q_from_mvar))
                        case "PLIM":
                            v = self.pv_limit[bay.id]
                        case "E_IMP" | "E_EXP":
                            self.energy[p.id] += max(0.0, sign * n(r.p_from_mw)) * 1000 * dt_sim_h
                            v = self.energy[p.id]
                # -------- 电容器
                case "capacitor", meas:
                    sh = net.res_shunt.loc[idx.shunt[bay.id]]
                    qc = -n(sh.q_mvar)
                    u_kv = n(sh.vm_pu) * 10
                    v = qc if meas == "Q" else (qc * 1000 / (math.sqrt(3) * u_kv) if u_kv > 0.1 else 0.0)

            if p.cat == Cat.YC and p.meas not in ("TAP", "TOIL", "LOAD", "PLIM", "COS", "F", "U0"):
                v = self._noise(v)
            if (mf := self.meas_faults.get(p.id)) is not None:
                kind, arg = mf
                v = -v if kind == "invert" else (arg if kind == "freeze" else v * arg)
            vals[p.id] = Value(round(v, 6) + 0.0 if p.cat == Cat.YC else int(v), q)  # +0.0 消除 -0.0
        self.values = vals

    def inject_meas_fault(self, bay: str, kind: str) -> str:
        """量测回路故障：CT 极性接反 → P、Q 反号；有功变送器卡死 → P 冻结；电流量程错误 → I × 1.25。"""
        if bay not in {b.id for b in self.st.bays if b.kind in ("feeder", "pv")}:
            raise ValueError(f"{bay} 不是出线间隔")
        name = self.st.bay(bay).name
        match kind:
            case "invert":
                self.meas_faults[f"{bay}.P"] = ("invert", 0.0)
                self.meas_faults[f"{bay}.Q"] = ("invert", 0.0)
                return f"{name} 测量 CT 极性接反（有功、无功符号相反）"
            case "freeze":
                self.meas_faults[f"{bay}.P"] = ("freeze", float(self.values[f"{bay}.P"].v))
                return f"{name} 有功变送器卡死，数值不再刷新"
            case "scale":
                self.meas_faults[f"{bay}.I"] = ("scale", 1.25)
                return f"{name} 电流量程设置错误（测量值偏大 25%）"
        raise ValueError(f"未知量测故障 {kind}")

    # ================================================================ 设备操作

    def _apply_switches(self) -> None:
        for eid, i in self.idx.switch.items():
            self.net.switch.at[i, "closed"] = self.switch[eid] == DPI.ON

    def _set_switch(self, eid: str, state: DPI) -> None:
        self.switch[eid] = state
        self._apply_switches()
        point = self.st.point(f"{eid}.POS")
        if self.on_yx:
            self.on_yx(point, Value(state), self.rtu_now())
        self.changed.set()

    def set_signal(self, pid: str, on: bool) -> None:
        """保护 / 告警单点信号变位，立即以 SOE 上送。"""
        if self.signals.get(pid) == on:
            return
        self.signals[pid] = on
        if self.on_yx:
            self.on_yx(self.st.point(pid), Value(on), self.rtu_now())

    def interlock_reason(self, eid: str, close: bool) -> str | None:
        """间隔层联锁（测控装置内置）：断路器合位时禁止操作同间隔刀闸（防止带负荷拉合刀闸）。"""
        e = self.st.equip(eid)
        if eid in self.moving:
            return "设备正在动作"
        if e.type == "disconnector":
            # 只受同电压等级断路器约束：内桥接线主变高压侧无断路器，110kV 刀闸不受 10kV 侧 901 约束
            for other in self.st.bay(e.bay).equipment:
                if other.type == "breaker" and other.vl == e.vl and self.switch[other.id] == DPI.ON:
                    return f"同间隔断路器 {other.id} 在合位"
        return None

    async def operate_switch(self, eid: str, close: bool, source: str = "remote") -> bool:
        """source：remote 遥控 / prot 保护跳闸 / recl 重合闸 / bzt 备自投 —— 自动装置据此判断是否放电。"""
        target = DPI.ON if close else DPI.OFF
        if self.switch[eid] == target:
            return True
        e = self.st.equip(eid)
        self.moving.add(eid)
        try:
            if e.type == "disconnector":
                self._set_switch(eid, DPI.INDETERMINATE)  # 刀闸行程中：双点 00
                await asyncio.sleep(DISCONNECTOR_TRAVEL_S)
            else:
                await asyncio.sleep(BREAKER_OP_S)
            self._set_switch(eid, target)
            self.auto.on_switch(eid, close, source)
            return True
        finally:
            self.moving.discard(eid)

    def tap_reason(self, trafo: str, higher: bool) -> str | None:
        if trafo in self.tap_busy:
            return "调压机构动作中"
        new = self.tap[trafo] + (-1 if higher else 1)  # 升档 = 档位 +1 = tap_pos −1
        if not TAP_MIN <= new <= TAP_MAX:
            return "已到极限档位"
        return None

    async def change_tap(self, trafo: str, higher: bool) -> bool:
        self.tap_busy.add(trafo)
        try:
            await asyncio.sleep(OLTC_OP_S)
            self.tap[trafo] += -1 if higher else 1
            self.changed.set()
            return True
        finally:
            self.tap_busy.discard(trafo)

    def set_pv_limit(self, bay: str, mw: float) -> None:
        self.pv_limit[bay] = mw
        self.changed.set()

    # ================================================================ 场景

    def set_grid_voltage(self, vm_pu: float) -> None:
        self.grid_vm = vm_pu
        self.changed.set()

    def reset_normal(self) -> None:
        """恢复正常运行方式：消除故障、开关回到正常位置、档位/光伏限值/上级电压复原、信号复归。"""
        self.auto.reset()
        self.meas_faults.clear()
        for e in self.st.equipment:
            if e.is_switch:
                normal = DPI.ON if e.normally_closed else DPI.OFF
                if self.switch[e.id] != normal:
                    self._set_switch(e.id, normal)
        for pid, on in self.signals.items():
            if on:
                self.set_signal(pid, False)
        for e in self.st.equipment:
            if e.type == "transformer":
                self.tap[e.id] = int(e.attrs.get("tap", 0))
        for b in self.st.bays:
            if b.kind == "pv":
                self.pv_limit[b.id] = float(b.attrs["capacity_mw"])
        self.grid_vm = float(self.st.grid["vm_pu"])
        self.changed.set()
