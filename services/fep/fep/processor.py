"""规约数据 → 实时库更新。与 Redis 解耦，便于单元测试。

处理规则：
    - IOA 按点表映射为测点；点表中不存在或类型不匹配的 IOA 计入统计并丢弃
    - 实时值每次都刷新（总召唤刷新时间戳），只有值或品质变化才产生变化记录（供历史库与推送）
    - 遥信：突发上送一律记为变位事件（带设备时标即 SOE）；总召唤中发现状态不一致也记为变位（无设备时标）
    - 通道中断：全部测点品质置 NT（非当前值），保留最后值
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from scadalib.iec104 import COT, DPI, RCS, Asdu, Quality, TypeID
from scadalib.model import Cat, Station


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


@dataclass(slots=True)
class RtValue:
    v: float | int
    q: int
    t: int  # 数据时标（毫秒，UTC）：带时标的取设备时标，否则取接收时间

    def to_dict(self) -> dict:
        return {"v": self.v, "q": self.q, "t": self.t}


@dataclass(slots=True)
class YxEvent:
    point: str
    value: int
    old: int | None
    dev_t: int | None  # SOE 设备时标
    recv_t: int
    cot: int

    def to_dict(self) -> dict:
        return {"p": self.point, "v": self.value, "old": self.old, "dev_t": self.dev_t, "recv_t": self.recv_t,
                "cot": self.cot}  # fmt: skip


@dataclass(slots=True)
class Batch:
    values: dict[str, RtValue] = field(default_factory=dict)  # 全部刷新
    changes: dict[str, RtValue] = field(default_factory=dict)  # 仅变化
    yx_events: list[YxEvent] = field(default_factory=list)
    unknown: int = 0


MONITOR_TYPES = {
    TypeID.M_SP_NA_1, TypeID.M_DP_NA_1, TypeID.M_ME_NC_1, TypeID.M_IT_NA_1,
    TypeID.M_SP_TB_1, TypeID.M_DP_TB_1, TypeID.M_ME_TF_1, TypeID.M_IT_TB_1,
}  # fmt: skip

_CAT_OF_TYPE = {
    TypeID.M_SP_NA_1: Cat.YX, TypeID.M_DP_NA_1: Cat.YX, TypeID.M_SP_TB_1: Cat.YX, TypeID.M_DP_TB_1: Cat.YX,
    TypeID.M_ME_NC_1: Cat.YC, TypeID.M_ME_TF_1: Cat.YC,
    TypeID.M_IT_NA_1: Cat.DD, TypeID.M_IT_TB_1: Cat.DD,
}  # fmt: skip


class Processor:
    def __init__(self, st: Station):
        self.st = st
        self.cache: dict[str, RtValue] = {}

    def process(self, a: Asdu, recv_t: int | None = None) -> Batch:
        b = Batch()
        if a.type_id not in MONITOR_TYPES:
            return b
        recv = recv_t or now_ms()
        cat = _CAT_OF_TYPE[a.type_id]
        for o in a.objects:
            p = self.st.by_ioa(o.ioa)
            if p is None or p.cat != cat:
                b.unknown += 1
                continue
            # 遥测保留 6 位小数；单点 0/1，双点 0~3，电度整数
            v: float | int = round(float(o.value), 6) if cat == Cat.YC else int(o.value)
            dev_t = int(o.time.timestamp() * 1000) if o.time else None
            new = RtValue(v, int(o.quality), dev_t or recv)
            old = self.cache.get(p.id)
            self.cache[p.id] = new
            b.values[p.id] = new
            if old is None or old.v != new.v or old.q != new.q:
                b.changes[p.id] = new
            if cat == Cat.YX and old is not None and (a.cot == COT.SPONTANEOUS or old.v != new.v):
                b.yx_events.append(YxEvent(p.id, new.v, old.v, dev_t, recv, int(a.cot)))
        return b

    def mark_not_topical(self) -> Batch:
        """通道中断：所有测点置 NT。"""
        b = Batch()
        for pid, old in self.cache.items():
            if not old.q & Quality.NT:
                new = RtValue(old.v, old.q | Quality.NT, now_ms())
                self.cache[pid] = new
                b.values[pid] = b.changes[pid] = new
        return b


def command_value(type_id: TypeID, raw: object) -> DPI | RCS | float:
    """api 下发的控制值 → 规约值。遥控 close/open，调档 raise/lower，设点为数值。"""
    match type_id:
        case TypeID.C_DC_NA_1:
            if raw in ("close", "on"):
                return DPI.ON
            if raw in ("open", "off"):
                return DPI.OFF
        case TypeID.C_RC_NA_1:
            if raw in ("raise", "higher"):
                return RCS.HIGHER
            if raw in ("lower",):
                return RCS.LOWER
        case TypeID.C_SE_NC_1:
            if isinstance(raw, int | float) and not isinstance(raw, bool):
                return float(raw)
    raise ValueError(f"invalid value {raw!r} for {type_id.name}")
