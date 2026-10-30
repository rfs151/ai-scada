"""CP56Time2a 七字节时标。

    字节 0-1  毫秒（含秒，0..59999），小端
    字节 2    分 (bit0-5) | IV (bit7)
    字节 3    时 (bit0-4) | SU 夏令时 (bit7)
    字节 4    日 (bit0-4) | 星期 (bit5-7, 1=周一)
    字节 5    月 (bit0-3)
    字节 6    年 (bit0-6, 0..99)

规约里时标是"站内本地时间"，没有时区字段。国内厂站一般用北京时间，
因此编解码时需要显式指定时区，系统内部统一用带时区的 UTC datetime。
"""

import struct
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo

DEFAULT_TZ: tzinfo = ZoneInfo("Asia/Shanghai")


def encode(dt: datetime, tz: tzinfo = DEFAULT_TZ, invalid: bool = False) -> bytes:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    local = dt.astimezone(tz)
    ms = local.second * 1000 + local.microsecond // 1000
    return struct.pack(
        "<HBBBBB",
        ms,
        local.minute | (0x80 if invalid else 0),
        local.hour,
        local.day | (local.isoweekday() << 5),
        local.month,
        local.year % 100,
    )


def decode(data: bytes, tz: tzinfo = DEFAULT_TZ) -> tuple[datetime, bool]:
    """返回 (UTC 时间, IV 无效标志)。"""
    ms, minute, hour, day, month, year = struct.unpack("<HBBBBB", data[:7])
    invalid = bool(minute & 0x80)
    local = datetime(
        2000 + (year & 0x7F),
        month & 0x0F,
        day & 0x1F,
        hour & 0x1F,
        minute & 0x3F,
        ms // 1000,
        (ms % 1000) * 1000,
        tzinfo=tz,
    )
    return local.astimezone(UTC), invalid
