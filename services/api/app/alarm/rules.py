"""告警判定（纯逻辑，不含 IO，便于单元测试）。

告警分级（参照调度告警直传规范的五类）：
    1 事故：事故跳闸、保护动作、重合闸动作、事故总
    2 异常：通道中断、母线失压、位置不定、弹簧未储能、轻瓦斯、油温高、接地告警
    3 越限：遥测越上/下限、越上上/下下限
    4 变位：遥控变位、就地（非遥控）变位
    5 告知：复归、越限恢复、通道恢复

遥测越限带回差：越限立即告警，恢复需回到限值以内一个回差量，避免值在限值附近抖动造成告警风暴。
母线失压时闭锁该间隔的频率、零序电压越限告警（失压后这些量为 0 没有意义）。
"""

from __future__ import annotations

from dataclasses import dataclass

from scadalib.iec104 import DPI, Quality
from scadalib.model import Cat, Point, Station

LEVEL_CN = {1: "事故", 2: "异常", 3: "越限", 4: "变位", 5: "告知"}
LIMIT_CN = {"hihi": "越上上限", "hi": "越上限", "lo": "越下限", "lolo": "越下下限"}
SEVERITY = {None: 0, "hi": 1, "lo": 1, "hihi": 2, "lolo": 2}

ACCIDENT_SIGNALS = {"PROT", "DIFF", "GAS_H", "RECL", "ACC"}
PROTECTION_SIGNALS = {"PROT", "DIFF", "GAS_H"}
# 母线三相电压以 Uab 为代表告警，避免一次失压产生三条告警
SKIP_LIMIT = {"UBC", "UCA"}
VOLTAGE_MEAS = {"UAB"}
DEAD_RATIO = 0.1  # 低于额定 10% 视为失压
PROT_WINDOW_MS = 5000  # 保护动作与开关分闸关联的时间窗

BAD = Quality.IV | Quality.NT


@dataclass
class AlarmDraft:
    ts: int  # ms
    level: int
    kind: str
    text: str
    point: str | None = None
    bay: str | None = None
    device: str | None = None
    value: float | None = None


def _side(state: str | None) -> str | None:
    return None if state is None else ("h" if state.startswith("hi") else "l")


def limit_state(p: Point, v: float) -> str | None:
    lim = p.limits
    if lim is None:
        return None
    if lim.hihi is not None and v >= lim.hihi:
        return "hihi"
    if lim.hi is not None and v >= lim.hi:
        return "hi"
    if lim.lolo is not None and v <= lim.lolo:
        return "lolo"
    if lim.lo is not None and v <= lim.lo:
        return "lo"
    return None


def _limit_value(p: Point, state: str) -> float:
    return getattr(p.limits, state)


def _hysteresis(p: Point, state: str) -> float:
    return max(p.deadband, abs(_limit_value(p, state)) * 0.01)


class AlarmRules:
    def __init__(self, st: Station, limit_delay_ms: int = 10_000):
        self.st = st
        self.limit_delay_ms = limit_delay_ms
        self.limit: dict[str, str | None] = {}
        self.pending: dict[
            str, tuple[str | None, int, float]
        ] = {}  # 待确认的越限状态 (目标, 起始时标, 最新值)
        self.dead_bays: set[str] = set()
        self.prot_at: dict[str, int] = {}  # 间隔 → 最近一次保护动作时标
        self.channels: dict[str, str] = {}
        self.vn = {b.id: float(st.voltage_levels[b.vl]["vn_kv"]) for b in st.bays}

    # ------------------------------------------------------------ 遥测

    def _fmt(self, p: Point, v: float) -> str:
        return f"{v:.{p.decimals}f}{p.unit}"

    def init_values(self, values: dict[str, tuple[float, int]]) -> None:
        """启动时用当前断面初始化越限状态，不产生告警（避免重启时告警风暴）。"""
        for pid, (v, q) in values.items():
            p = self._yc(pid)
            if p is None or q & BAD:
                continue
            if p.meas in VOLTAGE_MEAS and self._is_dead(p, v):
                self.dead_bays.add(p.bay)
            self.limit[pid] = limit_state(p, v)

    def _yc(self, pid: str) -> Point | None:
        try:
            p = self.st.point(pid)
        except KeyError:
            return None
        return p if p.cat == Cat.YC and p.limits is not None and p.meas not in SKIP_LIMIT else None

    def _is_dead(self, p: Point, v: float) -> bool:
        return self.st.bay(p.bay).kind == "bus" and v < self.vn[p.bay] * DEAD_RATIO

    def on_analog(self, pid: str, v: float, q: int, t: int) -> list[AlarmDraft]:
        p = self._yc(pid)
        if p is None or q & BAD:
            return []
        out: list[AlarmDraft] = []
        base = {"point": pid, "bay": p.bay, "device": p.ref, "value": v}

        # 母线失压 / 恢复
        if p.meas in VOLTAGE_MEAS and self.st.bay(p.bay).kind == "bus":
            dead = self._is_dead(p, v)
            if dead and p.bay not in self.dead_bays:
                self.dead_bays.add(p.bay)
                self.limit[pid] = limit_state(p, v)
                return [
                    AlarmDraft(
                        t, 2, "失压", f"{self.st.bay(p.bay).name} 失压（Uab {self._fmt(p, v)}）", **base
                    )
                ]
            if not dead and p.bay in self.dead_bays:
                self.dead_bays.discard(p.bay)
                self.limit[pid] = limit_state(p, v)
                out.append(
                    AlarmDraft(
                        t, 5, "失压恢复", f"{self.st.bay(p.bay).name} 电压恢复 {self._fmt(p, v)}", **base
                    )
                )
                return out
        if p.bay in self.dead_bays:
            self.pending.pop(pid, None)
            return []

        return self._limit_step(p, pid, v, t, base)

    def _target(self, p: Point, pid: str, v: float) -> str | None:
        """按当前值应进入的越限状态：加重方向直接判，恢复方向需越过回差。"""
        cur = self.limit.get(pid)
        new = limit_state(p, v)
        if new == cur:
            return cur
        if new is not None and (SEVERITY[new] > SEVERITY[cur] or _side(new) != _side(cur)):
            return new
        assert cur is not None
        h = _hysteresis(p, cur)
        relaxed = limit_state(p, v + h if _side(cur) == "h" else v - h)
        return cur if relaxed == cur or SEVERITY[relaxed] >= SEVERITY[cur] else relaxed

    def _limit_step(self, p: Point, pid: str, v: float, t: int, base: dict) -> list[AlarmDraft]:
        """
        越限延时：新状态须持续 limit_delay_ms 才确认（越限与恢复都适用），
        避免负荷在限值附近波动时反复告警；越上上限/下下限加重时立即告警。
        """
        cur = self.limit.get(pid)
        target = self._target(p, pid, v)
        if target == cur:
            self.pending.pop(pid, None)
            return []
        urgent = target is not None and SEVERITY[target] == 2 and SEVERITY[target] > SEVERITY[cur]
        if urgent or self.limit_delay_ms <= 0:
            self.pending.pop(pid, None)
            return self._commit(p, pid, target, v, t, base)
        pend = self.pending.get(pid)
        if pend is None or pend[0] != target:
            self.pending[pid] = (target, t, v)
            return []
        self.pending[pid] = (target, pend[1], v)
        if t - pend[1] >= self.limit_delay_ms:
            del self.pending[pid]
            return self._commit(p, pid, target, v, pend[1], base)
        return []

    def tick(self, now: int) -> list[AlarmDraft]:
        """定时检查：值保持不变（无新变化上送）时也能按延时确认。"""
        out: list[AlarmDraft] = []
        for pid, (target, since, v) in list(self.pending.items()):
            if now - since >= self.limit_delay_ms:
                del self.pending[pid]
                p = self.st.point(pid)
                if p.bay in self.dead_bays:
                    continue
                out += self._commit(
                    p, pid, target, v, since, {"point": pid, "bay": p.bay, "device": p.ref, "value": v}
                )
        return out

    def _commit(self, p: Point, pid: str, new: str | None, v: float, t: int, base: dict) -> list[AlarmDraft]:
        cur = self.limit.get(pid)
        self.limit[pid] = new
        if new is not None and (SEVERITY[new] > SEVERITY[cur] or _side(new) != _side(cur)):
            lim = _limit_value(p, new)
            return [
                AlarmDraft(
                    t, 3, "越限", f"{p.name} {LIMIT_CN[new]} {self._fmt(p, v)}（限值 {lim:g}）", **base
                )
            ]
        if new is None:
            return [AlarmDraft(t, 5, "越限恢复", f"{p.name} 越限恢复 {self._fmt(p, v)}", **base)]
        return [AlarmDraft(t, 5, "越限恢复", f"{p.name} 由{LIMIT_CN[cur]}回落至{LIMIT_CN[new]} {self._fmt(p, v)}",
                           **base)]  # fmt: skip

    # ------------------------------------------------------------ 遥信

    def on_yx(self, e: dict, commanded: dict | None = None) -> list[AlarmDraft]:
        """
        :param e: 前置机变位事件 {p, v, old, dev_t, recv_t, cot}
        :param commanded: 该设备最近的遥控记录（由遥控服务写入），None 表示非遥控
        """
        try:
            p = self.st.point(e["p"])
        except KeyError:
            return []
        t = e.get("dev_t") or e["recv_t"]
        v = int(e["v"])
        gi = " （总召发现）" if e.get("cot") == 20 else ""
        base = {"point": p.id, "bay": p.bay, "device": p.ref, "value": float(v)}

        if p.double:
            eq = self.st.equip(p.ref)
            if v == DPI.INDETERMINATE:
                return []  # 刀闸行程中，不告警；超时未到位由不定态/位置不一致反映
            if v == DPI.INVALID:
                return [AlarmDraft(t, 2, "位置不定", f"{eq.name} 位置不定（辅助接点异常）{gi}", **base)]
            verb = "合闸" if v == DPI.ON else "分闸"
            if commanded and commanded.get("action") == ("close" if v == DPI.ON else "open"):
                who = f"操作人 {commanded.get('operator')}"
                if commanded.get("supervisor"):
                    who += f"，监护人 {commanded['supervisor']}"
                return [AlarmDraft(t, 4, "遥控变位", f"{eq.name} 遥控{verb}（{who}）", **base)]
            if eq.type == "breaker" and v == DPI.OFF and self._prot_recent(p.bay, t):
                return [AlarmDraft(t, 1, "事故跳闸", f"{eq.name} 事故跳闸{gi}", **base)]
            return [AlarmDraft(t, 4, "变位", f"{eq.name} {verb}（非遥控）{gi}", **base)]

        # 单点信号
        if v:
            if p.meas in PROTECTION_SIGNALS:
                self.prot_at[p.bay] = t
            level = 1 if p.meas in ACCIDENT_SIGNALS else 2
            return [AlarmDraft(t, level, "保护动作" if level == 1 else "告警信号", f"{p.name}{gi}", **base)]
        return [AlarmDraft(t, 5, "复归", f"{p.name} 复归{gi}", **base)]

    def _prot_recent(self, bay: str, t: int) -> bool:
        at = self.prot_at.get(bay)
        return at is not None and abs(t - at) <= PROT_WINDOW_MS

    # ------------------------------------------------------------ 通道

    def on_channel(self, ch: str, state: str, t: int, detail: str = "") -> list[AlarmDraft]:
        old = self.channels.get(ch)
        self.channels[ch] = state
        if old is None or old == state:
            return []  # 首次观测只记录状态
        if state != "up" and old == "up":
            return [AlarmDraft(t, 2, "通道中断", f"104 通道 {ch} 中断 {detail}".strip(), device=ch)]
        if state == "up":
            return [AlarmDraft(t, 5, "通道恢复", f"104 通道 {ch} 恢复", device=ch)]
        return []
