"""APCI（应用规约控制信息）帧层。

启动字符 0x68 | APDU 长度(≤253) | 控制域 4 字节 | [ASDU]

I 帧（信息传输）: 控制域 = N(S)<<1 (2 字节) + N(R)<<1 (2 字节)，序号 15 位循环
S 帧（监视确认）: 0x01 0x00 + N(R)<<1
U 帧（不编号控制）: STARTDT / STOPDT / TESTFR 的 激活/确认
"""

import asyncio
from dataclasses import dataclass
from enum import Enum, IntEnum

START = 0x68
APDU_MAX_LEN = 253
SEQ_MOD = 1 << 15


class Kind(Enum):
    I = "I"  # noqa: E741
    S = "S"
    U = "U"


class UFunc(IntEnum):
    STARTDT_ACT = 0x07
    STARTDT_CON = 0x0B
    STOPDT_ACT = 0x13
    STOPDT_CON = 0x23
    TESTFR_ACT = 0x43
    TESTFR_CON = 0x83


class FrameError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class Frame:
    kind: Kind
    ns: int = 0
    nr: int = 0
    ufunc: UFunc | None = None
    asdu: bytes = b""


def _seq(n: int) -> bytes:
    n %= SEQ_MOD
    return bytes([(n << 1) & 0xFE, (n >> 7) & 0xFF])


def encode_i(ns: int, nr: int, asdu: bytes) -> bytes:
    if len(asdu) + 4 > APDU_MAX_LEN:
        raise FrameError("ASDU too long")
    return bytes([START, len(asdu) + 4]) + _seq(ns) + _seq(nr) + asdu


def encode_s(nr: int) -> bytes:
    return bytes([START, 4, 0x01, 0x00]) + _seq(nr)


def encode_u(func: UFunc) -> bytes:
    return bytes([START, 4, func, 0, 0, 0])


def decode(apdu: bytes) -> Frame:
    if len(apdu) < 6 or apdu[0] != START or apdu[1] != len(apdu) - 2:
        raise FrameError(f"malformed APDU: {apdu[:8].hex(' ')}")
    c = apdu[2:6]
    if not c[0] & 0x01:
        ns = (c[0] >> 1) | (c[1] << 7)
        nr = (c[2] >> 1) | (c[3] << 7)
        return Frame(Kind.I, ns=ns, nr=nr, asdu=bytes(apdu[6:]))
    if len(apdu) != 6:
        raise FrameError("S/U frame must not carry ASDU")
    if c[0] & 0x03 == 0x01:
        return Frame(Kind.S, nr=(c[2] >> 1) | (c[3] << 7))
    try:
        return Frame(Kind.U, ufunc=UFunc(c[0]))
    except ValueError:
        raise FrameError(f"unknown U function 0x{c[0]:02x}") from None


async def read_apdu(reader: asyncio.StreamReader) -> bytes:
    """从 TCP 流中按长度字段切出一个完整 APDU。"""
    head = await reader.readexactly(2)
    if head[0] != START:
        raise FrameError(f"bad start byte 0x{head[0]:02x}")
    if not 4 <= head[1] <= APDU_MAX_LEN:
        raise FrameError(f"bad APDU length {head[1]}")
    return head + await reader.readexactly(head[1])


def seq_distance(a: int, b: int) -> int:
    """从 a 到 b 的距离（模 2^15）。"""
    return (b - a) % SEQ_MOD
