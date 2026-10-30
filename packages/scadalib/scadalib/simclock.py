"""仿真时钟：真实时间按固定倍速映射到"仿真钟点"，子站与主站共用。

仿真日固定对齐本地时间：默认 60 倍速时 1 个仿真日 = 1440 秒（24 分钟），
每个整点、24 分、48 分为仿真零点。不依赖进程启动时刻，重启子站后历史数据的
仿真钟点仍然一致，负荷预测可以按"仿真日的同一时刻"对齐历史。
"""

from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_SPEED = 60.0
DEFAULT_TZ = "Asia/Shanghai"


def day_seconds(speed: float = DEFAULT_SPEED) -> float:
    """1 个仿真日对应的真实秒数。"""
    return 86_400 / speed


def _local_seconds(ts: float, tz: str) -> float:
    """本地时间的 Unix 秒（加上 UTC 偏移），使仿真零点对齐本地整点。"""
    off = datetime.fromtimestamp(ts, ZoneInfo(tz)).utcoffset()
    return ts + (off.total_seconds() if off else 0.0)


def sim_hour(ts: float, speed: float = DEFAULT_SPEED, tz: str = DEFAULT_TZ) -> float:
    """真实时刻 ts（Unix 秒）对应的仿真钟点 0~24。"""
    return (_local_seconds(ts, tz) % day_seconds(speed)) / day_seconds(speed) * 24


def day_start(ts: float, speed: float = DEFAULT_SPEED, tz: str = DEFAULT_TZ) -> float:
    """ts 所在仿真日的零点（真实 Unix 秒）。"""
    return ts - _local_seconds(ts, tz) % day_seconds(speed)
