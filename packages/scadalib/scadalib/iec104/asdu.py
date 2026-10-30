"""ASDU（应用服务数据单元）编解码。

类型标识(1) | 可变结构限定词 VSQ(1) | 传送原因 COT(2) | 公共地址 CA(2) | 信息体...
COT 第一字节: bit0-5 原因, bit6 P/N(否定确认), bit7 T(试验);  第二字节: 源发地址
信息体: IOA(3, 小端) + 信息元素 [+ 时标]
VSQ bit7=SQ: SQ=1 时只有第一个信息体带 IOA，后续地址连续递增
"""

import struct
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import Any

from . import cp56
from .constants import COT, DPI, RCS, Quality, TypeID

ASDU_MAX_LEN = 249  # APDU 最大 253 字节 - 4 字节控制域
HEADER_LEN = 6


class AsduError(ValueError):
    pass


class UnknownTypeError(AsduError):
    def __init__(self, type_id: int):
        super().__init__(f"unsupported type id {type_id}")
        self.type_id = type_id


@dataclass(slots=True)
class InfoObject:
    ioa: int
    # 遥测: float；遥信: bool / DPI；电度: int；遥控: bool / DPI / RCS；设点: float；
    # 总召/电度召唤: 限定词 int；对时: datetime；初始化结束: COI int
    value: Any = None
    quality: Quality = Quality.GOOD
    time: datetime | None = None
    time_invalid: bool = False
    select: bool = False  # 命令 S/E 位：True=选择，False=执行
    qualifier: int = 0  # 命令 QU / 设点 QL / 电度顺序号


@dataclass(slots=True)
class Asdu:
    type_id: TypeID
    cot: COT
    ca: int
    objects: list[InfoObject] = field(default_factory=list)
    negative: bool = False
    test: bool = False
    oa: int = 0
    sq: bool = False

    def reply(self, cot: COT, negative: bool = False) -> "Asdu":
        """镜像回复（激活确认/终止、否定确认等），信息体原样返回。"""
        return Asdu(self.type_id, cot, self.ca, list(self.objects), negative, self.test, self.oa)


# ---------------------------------------------------------------- 信息元素


def _elem_size(t: TypeID) -> int:
    return _SIZES[t]


_SIZES: dict[TypeID, int] = {
    TypeID.M_SP_NA_1: 1,
    TypeID.M_DP_NA_1: 1,
    TypeID.M_ME_NC_1: 5,
    TypeID.M_IT_NA_1: 5,
    TypeID.M_SP_TB_1: 8,
    TypeID.M_DP_TB_1: 8,
    TypeID.M_ME_TF_1: 12,
    TypeID.M_IT_TB_1: 12,
    TypeID.M_EI_NA_1: 1,
    TypeID.C_SC_NA_1: 1,
    TypeID.C_DC_NA_1: 1,
    TypeID.C_RC_NA_1: 1,
    TypeID.C_SE_NC_1: 5,
    TypeID.C_IC_NA_1: 1,
    TypeID.C_CI_NA_1: 1,
    TypeID.C_CS_NA_1: 7,
}


def _q_status(q: Quality) -> int:
    """SIQ/DIQ 的品质位（不含 OV）。"""
    return int(q) & 0xF0


def _encode_elem(t: TypeID, o: InfoObject, tz: tzinfo) -> bytes:
    se = 0x80 if o.select else 0
    match t:
        case TypeID.M_SP_NA_1 | TypeID.M_SP_TB_1:
            b = bytes([(1 if o.value else 0) | _q_status(o.quality)])
        case TypeID.M_DP_NA_1 | TypeID.M_DP_TB_1:
            b = bytes([(int(o.value) & 0x03) | _q_status(o.quality)])
        case TypeID.M_ME_NC_1 | TypeID.M_ME_TF_1:
            b = struct.pack("<fB", float(o.value), int(o.quality) & 0xF1)
        case TypeID.M_IT_NA_1 | TypeID.M_IT_TB_1:
            iv = 0x80 if o.quality & Quality.IV else 0
            b = struct.pack("<iB", int(o.value), (o.qualifier & 0x1F) | iv)
        case TypeID.M_EI_NA_1 | TypeID.C_IC_NA_1 | TypeID.C_CI_NA_1:
            b = bytes([int(o.value) & 0xFF])
        case TypeID.C_SC_NA_1:
            b = bytes([(1 if o.value else 0) | ((o.qualifier & 0x1F) << 2) | se])
        case TypeID.C_DC_NA_1 | TypeID.C_RC_NA_1:
            b = bytes([(int(o.value) & 0x03) | ((o.qualifier & 0x1F) << 2) | se])
        case TypeID.C_SE_NC_1:
            b = struct.pack("<fB", float(o.value), (o.qualifier & 0x7F) | se)
        case TypeID.C_CS_NA_1:
            return cp56.encode(o.value, tz)
        case _:
            raise UnknownTypeError(t)
    if t in (TypeID.M_SP_TB_1, TypeID.M_DP_TB_1, TypeID.M_ME_TF_1, TypeID.M_IT_TB_1):
        if o.time is None:
            raise AsduError(f"{t.name} requires a timestamp (ioa={o.ioa})")
        b += cp56.encode(o.time, tz, o.time_invalid)
    return b


def _decode_elem(t: TypeID, ioa: int, d: bytes, tz: tzinfo) -> InfoObject:
    o = InfoObject(ioa)
    match t:
        case TypeID.M_SP_NA_1 | TypeID.M_SP_TB_1:
            o.value = bool(d[0] & 0x01)
            o.quality = Quality(d[0] & 0xF0)
        case TypeID.M_DP_NA_1 | TypeID.M_DP_TB_1:
            o.value = DPI(d[0] & 0x03)
            o.quality = Quality(d[0] & 0xF0)
        case TypeID.M_ME_NC_1 | TypeID.M_ME_TF_1:
            o.value, q = struct.unpack_from("<fB", d)
            o.quality = Quality(q & 0xF1)
        case TypeID.M_IT_NA_1 | TypeID.M_IT_TB_1:
            o.value, s = struct.unpack_from("<iB", d)
            o.qualifier = s & 0x1F
            o.quality = Quality.IV if s & 0x80 else Quality.GOOD
        case TypeID.M_EI_NA_1 | TypeID.C_IC_NA_1 | TypeID.C_CI_NA_1:
            o.value = d[0]
        case TypeID.C_SC_NA_1:
            o.value, o.qualifier, o.select = (
                bool(d[0] & 1),
                (d[0] >> 2) & 0x1F,
                bool(d[0] & 0x80),
            )
        case TypeID.C_DC_NA_1:
            o.value, o.qualifier, o.select = (
                DPI(d[0] & 3),
                (d[0] >> 2) & 0x1F,
                bool(d[0] & 0x80),
            )
        case TypeID.C_RC_NA_1:
            rcs = d[0] & 3
            if rcs not in (1, 2):
                raise AsduError(f"invalid RCS {rcs}")
            o.value, o.qualifier, o.select = (
                RCS(rcs),
                (d[0] >> 2) & 0x1F,
                bool(d[0] & 0x80),
            )
        case TypeID.C_SE_NC_1:
            o.value, qos = struct.unpack_from("<fB", d)
            o.qualifier, o.select = qos & 0x7F, bool(qos & 0x80)
        case TypeID.C_CS_NA_1:
            o.value, _ = cp56.decode(d, tz)
            return o
    if t in (TypeID.M_SP_TB_1, TypeID.M_DP_TB_1, TypeID.M_ME_TF_1, TypeID.M_IT_TB_1):
        o.time, o.time_invalid = cp56.decode(d[_SIZES[t] - 7 :], tz)
    return o


# ---------------------------------------------------------------- ASDU


def encode(a: Asdu, tz: tzinfo = cp56.DEFAULT_TZ) -> bytes:
    n = len(a.objects)
    if not 0 < n <= 127:
        raise AsduError(f"object count {n} out of range")
    cot = int(a.cot) | (0x40 if a.negative else 0) | (0x80 if a.test else 0)
    vsq = n | (0x80 if a.sq else 0)
    out = bytearray(struct.pack("<BBBBH", a.type_id, vsq, cot, a.oa, a.ca))
    for i, o in enumerate(a.objects):
        if not a.sq or i == 0:
            out += o.ioa.to_bytes(3, "little")
        out += _encode_elem(a.type_id, o, tz)
    if len(out) > ASDU_MAX_LEN:
        raise AsduError(f"ASDU too long ({len(out)} > {ASDU_MAX_LEN})")
    return bytes(out)


def decode(data: bytes, tz: tzinfo = cp56.DEFAULT_TZ) -> Asdu:
    if len(data) < HEADER_LEN:
        raise AsduError("ASDU too short")
    tid, vsq, cot, oa, ca = struct.unpack_from("<BBBBH", data)
    try:
        type_id = TypeID(tid)
    except ValueError:
        raise UnknownTypeError(tid) from None
    try:
        cause = COT(cot & 0x3F)
    except ValueError:
        raise AsduError(f"unknown COT {cot & 0x3F}") from None
    a = Asdu(type_id, cause, ca, [], bool(cot & 0x40), bool(cot & 0x80), oa, bool(vsq & 0x80))
    n, size, pos = vsq & 0x7F, _elem_size(type_id), HEADER_LEN
    ioa = 0
    for i in range(n):
        if not a.sq or i == 0:
            ioa = int.from_bytes(data[pos : pos + 3], "little")
            pos += 3
        else:
            ioa += 1
        if pos + size > len(data):
            raise AsduError("ASDU truncated")
        a.objects.append(_decode_elem(type_id, ioa, data[pos : pos + size], tz))
        pos += size
    if pos != len(data):
        raise AsduError(f"ASDU length mismatch ({len(data) - pos} trailing bytes)")
    return a


def chunk(type_id: TypeID, objects: list[InfoObject]) -> list[list[InfoObject]]:
    """按 ASDU 最大长度把信息体分批（SQ=0，每个信息体带 IOA）。"""
    per = min(127, (ASDU_MAX_LEN - HEADER_LEN) // (3 + _elem_size(type_id)))
    return [objects[i : i + per] for i in range(0, len(objects), per)]
