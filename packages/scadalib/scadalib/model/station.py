"""厂站模型加载与点表生成。

点表按"间隔类型 → 典型信息配置"规则生成，IOA 按国内 104 实施细则分段、按出现顺序确定性分配：
    遥信 YX 0x0001~   遥测 YC 0x4001~   遥控 YK 0x6001~   遥调 YT 0x6201~   电度 DD 0x6401~
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from scadalib.iec104.constants import IOA, TypeID


class Cat(StrEnum):
    YC = "YC"  # 遥测
    YX = "YX"  # 遥信
    YK = "YK"  # 遥控
    YT = "YT"  # 遥调
    DD = "DD"  # 电度


IOA_BASE = {Cat.YX: IOA.YX_START, Cat.YC: IOA.YC_START, Cat.YK: IOA.YK_START, Cat.YT: IOA.YT_START,
            Cat.DD: IOA.DD_START}  # fmt: skip


@dataclass(frozen=True, slots=True)
class Limits:
    """四级限值：越下下限 < 越下限 < 正常 < 越上限 < 越上上限。"""

    hihi: float | None = None
    hi: float | None = None
    lo: float | None = None
    lolo: float | None = None

    def as_dict(self) -> dict[str, float]:
        return {k: v for k, v in (("hihi", self.hihi), ("hi", self.hi), ("lo", self.lo), ("lolo", self.lolo))
                if v is not None}  # fmt: skip


@dataclass(frozen=True, slots=True)
class Point:
    id: str  # 唯一标识，如 "F01.P"、"911.POS"
    name: str  # 中文名称
    cat: Cat
    ioa: int
    bay: str
    ref: str  # 关联设备
    meas: str  # 量测角色：P/Q/I/COS/UAB/POS/PROT/...
    unit: str = ""
    deadband: float = 0.0
    limits: Limits | None = None
    double: bool = False  # 双点遥信
    target: str | None = None  # 遥控/遥调 对应的返校遥信/遥测点
    decimals: int = 2

    @property
    def gi_type(self) -> TypeID | None:
        """总召唤时上送的类型。"""
        return {Cat.YC: TypeID.M_ME_NC_1, Cat.DD: TypeID.M_IT_NA_1}.get(self.cat) or (
            (TypeID.M_DP_NA_1 if self.double else TypeID.M_SP_NA_1) if self.cat == Cat.YX else None
        )

    @property
    def spont_type(self) -> TypeID | None:
        """突发上送类型：遥信带 CP56Time2a 时标（SOE），遥测为短浮点。"""
        if self.cat == Cat.YX:
            return TypeID.M_DP_TB_1 if self.double else TypeID.M_SP_TB_1
        return self.gi_type

    @property
    def cmd_type(self) -> TypeID | None:
        if self.cat == Cat.YK:
            return TypeID.C_DC_NA_1
        if self.cat == Cat.YT:
            return TypeID.C_RC_NA_1 if self.meas == "TAPCMD" else TypeID.C_SE_NC_1
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "cat": str(self.cat),
            "ioa": self.ioa,
            "bay": self.bay,
            "ref": self.ref,
            "meas": self.meas,
            "unit": self.unit,
            "deadband": self.deadband,
            "limits": self.limits.as_dict() if self.limits else None,
            "double": self.double,
            "target": self.target,
            "decimals": self.decimals,
        }


@dataclass(slots=True)
class Equipment:
    id: str
    type: str  # line / breaker / disconnector / earth_switch / transformer
    bay: str
    vl: str
    name: str
    attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def nodes(self) -> list[str]:
        if self.type in ("breaker", "disconnector"):
            return list(self.attrs["nodes"])
        if self.type == "earth_switch":
            return [self.attrs["node"]]
        if self.type == "line":
            return [self.attrs["from"], self.attrs["to"]]
        if self.type == "transformer":
            return [self.attrs["hv"], self.attrs["lv"]]
        return []

    @property
    def is_switch(self) -> bool:
        return self.type in ("breaker", "disconnector", "earth_switch")

    @property
    def normally_closed(self) -> bool:
        return self.attrs.get("normal", "closed") == "closed"


@dataclass(slots=True)
class Bay:
    id: str
    name: str
    kind: str
    vl: str
    equipment: list[Equipment]
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Station:
    id: str
    name: str
    ca: int
    voltage_levels: dict[str, dict[str, Any]]
    grid: dict[str, Any]
    bays: list[Bay]
    std_types: dict[str, dict[str, dict[str, Any]]]
    points: list[Point] = field(default_factory=list)

    def __post_init__(self):
        self.reindex()

    def reindex(self) -> None:
        self._by_id = {p.id: p for p in self.points}
        self._by_ioa = {p.ioa: p for p in self.points}

    # ---- 查询
    def point(self, pid: str) -> Point:
        return self._by_id[pid]

    def by_ioa(self, ioa: int) -> Point | None:
        return self._by_ioa.get(ioa)

    def points_of(self, cat: Cat) -> list[Point]:
        return [p for p in self.points if p.cat == cat]

    @property
    def equipment(self) -> list[Equipment]:
        return [e for b in self.bays for e in b.equipment]

    def equip(self, eid: str) -> Equipment:
        return next(e for e in self.equipment if e.id == eid)

    def bay(self, bid: str) -> Bay:
        return next(b for b in self.bays if b.id == bid)

    def node_vl(self) -> dict[str, str]:
        """连接节点 → 电压等级。"""
        out: dict[str, str] = {self.grid["node"]: "HV"}
        for b in self.bays:
            if b.attrs.get("node"):
                out[b.attrs["node"]] = b.vl
            for e in b.equipment:
                if e.type == "transformer":
                    out[e.attrs["hv"]], out[e.attrs["lv"]] = "HV", "LV"
                else:
                    for n in e.nodes:
                        out.setdefault(n, e.vl)
        return out

    def point_table_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["类别", "IOA(十进制)", "IOA(十六进制)", "点号", "名称", "单位", "死区", "限值", "关联"])
        order = [Cat.YX, Cat.YC, Cat.YK, Cat.YT, Cat.DD]
        for p in sorted(self.points, key=lambda p: (order.index(p.cat), p.ioa)):
            lim = " ".join(f"{k}={v}" for k, v in p.limits.as_dict().items()) if p.limits else ""
            w.writerow([p.cat, p.ioa, f"0x{p.ioa:04X}", p.id, p.name, p.unit, p.deadband or "", lim,
                        p.target or ""])  # fmt: skip
        return buf.getvalue()


# ============================================================== 加载


def load_station(path: str | Path) -> Station:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    st = raw["station"]
    bays: list[Bay] = []
    for b in raw["bays"]:
        equipment = [
            Equipment(
                id=str(e["id"]),
                type=e["type"],
                bay=b["id"],
                vl=e.get("vl", b["vl"]),
                name=e.get("name", f"{b['name']}{_TYPE_CN.get(e['type'], '')}"),
                attrs={k: v for k, v in e.items() if k not in ("id", "type", "name", "vl")},
            )
            for e in b.get("equipment", [])
        ]
        attrs = {k: v for k, v in b.items() if k not in ("id", "name", "kind", "vl", "equipment")}
        bays.append(Bay(b["id"], b["name"], b["kind"], b["vl"], equipment, attrs))

    station = Station(
        id=st["id"],
        name=st["name"],
        ca=int(st["ca"]),
        voltage_levels=raw["voltage_levels"],
        grid=raw["grid"],
        bays=bays,
        std_types=raw.get("std_types", {}),
    )
    station.points = _generate_points(station)
    station.reindex()
    return station


_TYPE_CN = {"line": "线路", "breaker": "断路器", "disconnector": "刀闸", "earth_switch": "接地刀",
            "transformer": ""}  # fmt: skip


# ============================================================== 点表生成


class _Alloc:
    def __init__(self):
        self.next = dict(IOA_BASE)
        self.points: list[Point] = []

    def add(self, cat: Cat, **kw) -> None:
        self.points.append(Point(cat=cat, ioa=self.next[cat], **kw))
        self.next[cat] += 1


# 电压限值依据：110kV 母线 -3%~+7%；10kV 母线考核范围 10.0~10.7kV（GB/T 12325 允许 ±7%）
LIM_U110 = Limits(hihi=121.0, hi=117.7, lo=106.7, lolo=104.5)
LIM_U10 = Limits(hihi=11.0, hi=10.7, lo=10.0, lolo=9.3)
LIM_F = Limits(hihi=50.5, hi=50.2, lo=49.8, lolo=49.5)


def _generate_points(s: Station) -> list[Point]:
    a = _Alloc()

    def yc(bay: Bay, meas: str, name: str, unit: str, db: float, ref: str | None = None, lim=None, dec=2):
        a.add(Cat.YC, id=f"{bay.id}.{meas}", name=f"{bay.name}{name}", bay=bay.id, ref=ref or bay.id,
              meas=meas, unit=unit, deadband=db, limits=lim, decimals=dec)  # fmt: skip

    def sp(bay_id: str, bay_name: str, meas: str, name: str, ref: str):
        a.add(Cat.YX, id=f"{bay_id}.{meas}", name=f"{bay_name}{name}", bay=bay_id, ref=ref, meas=meas)

    # ---- 全站信号
    sp("STN", "全站", "ACC", "事故总", s.id)
    sp("STN", "全站", "WARN", "预告总", s.id)

    for bay in s.bays:
        breakers = [e for e in bay.equipment if e.type == "breaker"]
        cb = breakers[0].id if breakers else bay.id

        # ---- 开关位置（双点遥信）及遥控
        for e in bay.equipment:
            if not e.is_switch:
                continue
            a.add(
                Cat.YX, id=f"{e.id}.POS", name=f"{e.name}位置", bay=bay.id, ref=e.id, meas="POS", double=True
            )
            if e.type == "breaker" or (e.type == "disconnector" and e.vl == "HV"):
                a.add(Cat.YK, id=f"{e.id}.YK", name=f"{e.name}遥控", bay=bay.id, ref=e.id, meas="YK",
                      target=f"{e.id}.POS")  # fmt: skip

        # ---- 保护及告警信号（单点遥信）
        match bay.kind:
            case "line_in" | "feeder" | "pv":
                sp(bay.id, bay.name, "PROT", "保护动作", cb)
                sp(bay.id, bay.name, "RECL", "重合闸动作", cb)
                sp(bay.id, bay.name, "SPRING", "弹簧未储能", cb)
            case "capacitor":
                sp(bay.id, bay.name, "PROT", "保护动作", cb)
            case "transformer":
                sp(bay.id, bay.name, "DIFF", "差动保护动作", bay.id)
                sp(bay.id, bay.name, "GAS_H", "本体重瓦斯", bay.id)
                sp(bay.id, bay.name, "GAS_L", "本体轻瓦斯", bay.id)
                sp(bay.id, bay.name, "TOIL_H", "油温高告警", bay.id)
            case "bus":
                sp(bay.id, bay.name, "GND", "接地告警", bay.id)

        # ---- 遥测 / 遥调 / 电度
        match bay.kind:
            case "line_in":
                line = next(e for e in bay.equipment if e.type == "line")
                lim_i = Limits(hi=line_max_a(s, line) * 0.9, hihi=line_max_a(s, line))
                yc(bay, "P", "有功", "MW", 0.2, line.id)
                yc(bay, "Q", "无功", "Mvar", 0.2, line.id)
                yc(bay, "I", "电流", "A", 2.0, line.id, lim_i, dec=1)
                yc(bay, "COS", "功率因数", "", 0.005, line.id, dec=3)
                yc(bay, "UAB", "线电压Uab", "kV", 0.2, line.id, LIM_U110)
            case "transformer":
                tr = next(e for e in bay.equipment if e.type == "transformer")
                yc(bay, "HP", "高压侧有功", "MW", 0.2, tr.id)
                yc(bay, "HQ", "高压侧无功", "Mvar", 0.2, tr.id)
                yc(bay, "HI", "高压侧电流", "A", 1.0, tr.id, dec=1)
                yc(bay, "LP", "低压侧有功", "MW", 0.2, tr.id)
                yc(bay, "LQ", "低压侧无功", "Mvar", 0.2, tr.id)
                yc(bay, "LI", "低压侧电流", "A", 5.0, tr.id, dec=0)
                yc(bay, "TAP", "档位", "档", 0.5, tr.id, dec=0)
                yc(bay, "TOIL", "顶层油温", "℃", 0.5, tr.id, Limits(hi=85, hihi=95), dec=1)
                yc(bay, "LOAD", "负载率", "%", 0.5, tr.id, Limits(hi=80, hihi=100), dec=1)
                a.add(Cat.YT, id=f"{bay.id}.TAPCMD", name=f"{bay.name}调档", bay=bay.id, ref=tr.id,
                      meas="TAPCMD", target=f"{bay.id}.TAP")  # fmt: skip
                a.add(Cat.DD, id=f"{bay.id}.E_IMP", name=f"{bay.name}低压侧正向有功电量", bay=bay.id,
                      ref=tr.id, meas="E_IMP", unit="kWh")  # fmt: skip
            case "bus":
                for ph in ("UAB", "UBC", "UCA"):
                    yc(bay, ph, f"线电压{ph[0]}{ph[1:].lower()}", "kV", 0.02, lim=LIM_U10, dec=3)
                yc(bay, "U0", "零序电压3U0", "V", 1.0, lim=Limits(hi=30), dec=1)
                yc(bay, "F", "频率", "Hz", 0.005, lim=LIM_F, dec=3)
            case "feeder":
                line = next(e for e in bay.equipment if e.type == "line")
                lim_i = Limits(hi=round(line_max_a(s, line) * 0.8), hihi=round(line_max_a(s, line)))
                yc(bay, "P", "有功", "MW", 0.05, line.id)
                yc(bay, "Q", "无功", "Mvar", 0.05, line.id)
                yc(bay, "I", "电流", "A", 2.0, line.id, lim_i, dec=1)
                yc(bay, "COS", "功率因数", "", 0.005, line.id, dec=3)
                a.add(Cat.DD, id=f"{bay.id}.E_IMP", name=f"{bay.name}正向有功电量", bay=bay.id, ref=line.id,
                      meas="E_IMP", unit="kWh")  # fmt: skip
            case "capacitor":
                yc(bay, "Q", "无功", "Mvar", 0.05)
                yc(bay, "I", "电流", "A", 2.0, dec=1)
            case "pv":
                line = next(e for e in bay.equipment if e.type == "line")
                yc(bay, "P", "有功", "MW", 0.05, line.id)
                yc(bay, "Q", "无功", "Mvar", 0.05, line.id)
                yc(bay, "I", "电流", "A", 2.0, line.id, dec=1)
                yc(bay, "PLIM", "有功限值", "MW", 0.01, line.id)
                a.add(Cat.YT, id=f"{bay.id}.PSET", name=f"{bay.name}有功设定", bay=bay.id, ref=line.id,
                      meas="PSET", unit="MW", target=f"{bay.id}.PLIM")  # fmt: skip
                a.add(Cat.DD, id=f"{bay.id}.E_EXP", name=f"{bay.name}上网电量", bay=bay.id, ref=line.id,
                      meas="E_EXP", unit="kWh")  # fmt: skip

    return a.points


def line_max_a(s: Station, line: Equipment) -> float:
    return s.std_types["line"][line.attrs["std"]]["max_i_ka"] * 1000


def default_config_path() -> Path:
    """查找 config/station.yaml：环境变量 STATION_CONFIG 优先，其次逐级向上查找仓库根目录。"""
    import os

    if env := os.environ.get("STATION_CONFIG"):
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "config" / "station.yaml"
        if cand.exists():
            return cand
    return Path("/config/station.yaml")
